"""HPE Central (unofficial): sites, APs / switches / gateways, client presence and events from a New Central tenant."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import CentralClient
from .const import (
    CONF_ADD_ALL,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SITES,
    CONF_TENANT_ID,
    DEFAULT_ADD_ALL,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import HpeCentralCoordinator, seen_store, tracked
from .entity import ensure_site_devices
from .source import CentralSource

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
]

type HpeCentralConfigEntry = ConfigEntry[HpeCentralCoordinator]


def client_for(hass: HomeAssistant, data: dict) -> CentralClient:
    return CentralClient(async_get_clientsession(hass), data[CONF_BASE_URL], data[CONF_CLIENT_ID], data[CONF_CLIENT_SECRET])


async def async_setup_entry(hass: HomeAssistant, entry: HpeCentralConfigEntry) -> bool:
    source = CentralSource(client_for(hass, entry.data), entry.options.get(CONF_SITES))
    coordinator = HpeCentralCoordinator(hass, entry, source)
    coordinator.can_write = await source.can_write()
    await coordinator.seen.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _sync_client_registry(hass, entry)
    _remove_stale_devices(hass, entry, coordinator)
    ensure_site_devices(hass, entry, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(lambda: ensure_site_devices(hass, entry, coordinator)))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_away_check()
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpeCentralConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.seen.async_flush()  # the reload after an options change reads it back
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the remembered clients with the entry."""
    await seen_store(hass, entry.entry_id).async_remove()


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: HpeCentralCoordinator) -> None:
    """Drop sites (and their devices) that are no longer included, and devices Central no longer lists."""
    tenant = entry.data[CONF_TENANT_ID]
    keep = {f"{tenant}_site_{s}" for s in coordinator.net.sites} | {f"{tenant}_dev_{d}" for d in coordinator.net.devices}
    dev_reg = dr.async_get(hass)
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        idents = [i for d, i in dev.identifiers if d == DOMAIN]
        if any(i.startswith((f"{tenant}_site_", f"{tenant}_dev_")) for i in idents) and not keep.intersection(idents):
            dev_reg.async_remove_device(dev.id)


def _sync_client_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Make the registry match the picked clients before the platforms add entities.

    Picked: a named device; its tracker is enabled, moved onto that device and,
    if it was an auto-added tracker, renamed after the device. Not picked: its
    sensors and device go; its tracker stays (disabled) only with "add all" on.
    """
    picked = tracked(entry)
    add_all = entry.options.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
    tenant = entry.data[CONF_TENANT_ID]
    client_id = re.compile(rf"^{re.escape(tenant)}_([0-9a-f]{{12}})(_client_.+)?$")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    devices = {}
    for mac, name in picked.items():
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{tenant}_{mac}")},
            name=name,
            manufacturer=MANUFACTURER,
            model="Client",
        )
        if device.name != name:
            device = dev_reg.async_update_device(device.id, name=name) or device
        devices[mac] = device
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if not (m := client_id.match(ent.unique_id)):
            continue
        mac = m.group(1)
        if ent.domain != "device_tracker":
            if mac not in picked:
                ent_reg.async_remove(ent.entity_id)
        elif mac in picked:
            device = devices[mac]
            if ent.device_id != device.id:
                ent_reg.async_update_entity(
                    ent.entity_id,
                    device_id=device.id,
                    disabled_by=None,
                    new_entity_id=ent_reg.async_get_available_entity_id(
                        "device_tracker", slugify(device.name or mac), current_entity_id=ent.entity_id
                    ),
                )
        elif not add_all:
            ent_reg.async_remove(ent.entity_id)
        elif ent.device_id is not None:
            ent_reg.async_update_entity(
                ent.entity_id, device_id=None, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for d, ident in dev.identifiers:
            if d == DOMAIN and (m := client_id.match(ident)) and m.group(1) not in picked:
                dev_reg.async_remove_device(dev.id)
