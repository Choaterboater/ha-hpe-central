"""Online state of each AP / switch / gateway, and link state of each switch port (disabled by default)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeCentralConfigEntry
from .coordinator import HpeCentralCoordinator
from .entity import CentralEntity, add_entities_as_seen, device_uid, network_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeCentralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        for mac, dev in list(c.net.devices.items()):
            yield f"{device_uid(entry, mac)}_online", lambda m=mac: DeviceOnline(c, entry, m)
            for port in dev.get("ports") or {}:
                yield f"{device_uid(entry, mac)}_port_{port}", lambda m=mac, p=port: PortUp(c, entry, m, p)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class DeviceOnline(CentralEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, c: HpeCentralCoordinator, entry, mac: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_online"
        self._attr_device_info = network_device_info(c.hass, entry, c.net.devices[mac])

    @property
    def is_on(self) -> bool:
        dev = self.coordinator.net.devices.get(self._mac)
        return bool(dev and dev.get("online"))


class PortUp(CentralEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "port"

    def __init__(self, c: HpeCentralCoordinator, entry, mac: str, port: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._port = port
        self._attr_unique_id = f"{device_uid(entry, mac)}_port_{port}"
        self._attr_translation_placeholders = {"port": port}
        self._attr_device_info = network_device_info(c.hass, entry, c.net.devices[mac])

    @property
    def available(self) -> bool:
        dev = self.coordinator.net.devices.get(self._mac)
        return super().available and dev is not None and bool(dev.get("online"))

    @property
    def is_on(self) -> bool:
        dev = self.coordinator.net.devices.get(self._mac) or {}
        return bool(((dev.get("ports") or {}).get(self._port) or {}).get("up"))
