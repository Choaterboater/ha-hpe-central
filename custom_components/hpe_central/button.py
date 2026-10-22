"""Restart button per AP / switch / gateway, disabled by default.

Central cannot tell in advance whether the API client may restart devices, so the
buttons are always created (disabled); a refusal is shown when one is pressed.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeCentralConfigEntry
from .coordinator import HpeCentralCoordinator
from .entity import CentralEntity, add_entities_as_seen, device_uid, network_device_info
from .errors import SourceError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeCentralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    if not c.can_write:
        return

    def candidates():
        for mac, dev in list(c.net.devices.items()):
            if dev.get("id") and dev.get("site_id"):
                yield f"{device_uid(entry, mac)}_restart", lambda m=mac: RestartButton(c, entry, m)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class RestartButton(CentralEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, c: HpeCentralCoordinator, entry, mac: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_restart"
        self._attr_device_info = network_device_info(c.hass, entry, c.net.devices[mac])

    async def async_press(self) -> None:
        dev = self.coordinator.net.devices[self._mac]
        try:
            await self.coordinator.source.restart_device(dev)
        except SourceError as err:
            raise HomeAssistantError(f"Central did not restart {dev['name']}: {err}") from err
