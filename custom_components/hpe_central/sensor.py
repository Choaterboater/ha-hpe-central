"""Sensors for each site, each AP / switch / gateway, and each picked client."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeCentralConfigEntry
from .const import MAX_LIST_ROWS
from .coordinator import HpeCentralCoordinator
from .entity import (
    CentralEntity,
    add_entities_as_seen,
    client_device_info,
    client_uid,
    device_uid,
    network_device_info,
    site_device_info,
    site_uid,
)
from .presence import Sighting, mac_pretty
from .source import BANDS, Device, port_sort_key

_DIAG = EntityCategory.DIAGNOSTIC
# List-shaped attributes stay out of the recorder.
_LIST_ATTRS = frozenset({"clients", "radios", "ports", "wlans", "vlans"})


@dataclass(frozen=True, kw_only=True)
class CentralSensorDescription(SensorEntityDescription):
    value_fn: Callable[..., Any]
    exists_fn: Callable[..., bool] = lambda *_: True
    # Same arguments as value_fn, but always with the coordinator first.
    attrs_fn: Callable[..., dict[str, Any] | None] | None = None


def _by_name(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: str(r["name"]).casefold())[:MAX_LIST_ROWS]


def _name(c: HpeCentralCoordinator, x: Sighting) -> str:
    return c.client_name(x)


# --- per site: value_fn(coordinator, site_id) ----------------------------------


def _home(c: HpeCentralCoordinator, site: str):
    return (x for x in c.net.clients.values() if x.home and x.site_id == site)


def _wireless(c: HpeCentralCoordinator, site: str, ssid: str | None = None) -> int:
    """Central lists wired and wireless clients together; wireless ones carry an SSID."""
    return sum(1 for x in _home(c, site) if x.ssid and (ssid is None or x.ssid == ssid))


def _wired(c: HpeCentralCoordinator, site: str) -> int:
    return sum(1 for x in _home(c, site) if not x.ssid)


def _aps(c: HpeCentralCoordinator, site: str, online: bool) -> int:
    return sum(1 for d in c.net.devices.values() if d["site_id"] == site and d["type"] == "ap" and d["online"] == online)


def _site_clients(c: HpeCentralCoordinator, site: str) -> dict[str, Any]:
    return {"clients": _by_name([
        {"name": _name(c, x), "mac": mac_pretty(x.mac), "ip": x.ip, "ssid": x.ssid, "band": x.band,
         "ap": c.device_name(x.ap_mac), "wired": x.wired, "port": x.port, "switch": c.device_name(x.switch_mac),
         "rssi": x.rssi, "snr": x.snr, "vendor": x.manufacture}
        for x in _home(c, site)
    ])}


SITE_SENSORS = (
    CentralSensorDescription(key="clients", translation_key="clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: sum(1 for _ in _home(c, s)), attrs_fn=_site_clients),
    CentralSensorDescription(key="wireless_clients", translation_key="wireless_clients",
                          state_class=SensorStateClass.MEASUREMENT, value_fn=_wireless),
    CentralSensorDescription(key="wired_clients", translation_key="wired_clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=_wired),
    CentralSensorDescription(key="aps_online", translation_key="aps_online", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: _aps(c, s, True)),
    CentralSensorDescription(key="aps_offline", translation_key="aps_offline", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: _aps(c, s, False)),
    CentralSensorDescription(key="active_alarms", translation_key="active_alarms", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: c.net.active_alarms(s)),
)


# --- per AP / switch / gateway: value_fn(device) --------------------------------


def _radio(band: str, field: str) -> Callable[[Device], Any]:
    return lambda d: (d.get("radios") or {}).get(band, {}).get(field)


def _ap_clients(c: HpeCentralCoordinator, d: Device) -> dict[str, Any] | None:
    if d.get("type") == "switch":
        return _switch_clients(c, d)
    if d.get("type") != "ap":
        return None
    rows = [x for x in c.net.clients.values() if x.home and x.ap_mac == d["mac"]]
    by_ssid: dict[str, int] = {}
    for x in rows:
        if x.ssid:
            by_ssid[x.ssid] = by_ssid.get(x.ssid, 0) + 1
    return {
        "clients": _by_name([{"name": _name(c, x), "mac": mac_pretty(x.mac), "ip": x.ip, "ssid": x.ssid,
                              "band": x.band, "rssi": x.rssi, "snr": x.snr, "vendor": x.manufacture}
                             for x in rows]),
        "by_band": {label: sum(1 for x in rows if x.band == label) for label in BANDS.values()},
        "by_ssid": dict(sorted(by_ssid.items())),
    }


def _switch_clients(c: HpeCentralCoordinator, d: Device) -> dict[str, Any]:
    """The wired clients on a switch (every member of a stack), with the port each is on."""
    rows = [x for x in c.net.clients.values() if x.home and x.wired and x.switch_mac == d["mac"]]
    return {"clients": _by_name([{"name": _name(c, x), "mac": mac_pretty(x.mac), "ip": x.ip, "port": x.port,
                                   "vlan": x.vlan, "vendor": x.manufacture} for x in rows])}


def _uplink_state(d: Device) -> str | None:
    up = d.get("uplink") or {}
    if up.get("device"):
        return f"{up['device']} {up.get('port') or ''}".strip()
    return f"{up['speed']} Mbps" if up.get("speed") else None


def _ports(c: HpeCentralCoordinator, d: Device) -> dict[str, Any]:
    ports = d.get("ports") or {}
    return {
        "ports_total": len(ports),
        "ports": [{"port": p, **ports[p]} for p in sorted(ports, key=port_sort_key)][:MAX_LIST_ROWS],
    }


DEVICE_SENSORS: tuple[CentralSensorDescription, ...] = (
    CentralSensorDescription(key="clients", translation_key="device_clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: d.get("clients"), exists_fn=lambda d: d.get("clients") is not None,
                          attrs_fn=_ap_clients),
    CentralSensorDescription(key="cpu", translation_key="cpu", native_unit_of_measurement=PERCENTAGE,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=_DIAG,
                          value_fn=lambda d: d.get("cpu"), exists_fn=lambda d: d.get("cpu") is not None),
    CentralSensorDescription(key="memory", translation_key="memory", native_unit_of_measurement=PERCENTAGE,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=_DIAG,
                          value_fn=lambda d: d.get("memory"), exists_fn=lambda d: d.get("memory") is not None),
    CentralSensorDescription(key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                          native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
                          entity_category=_DIAG, value_fn=lambda d: d.get("temperature"),
                          exists_fn=lambda d: d.get("temperature") is not None),
    CentralSensorDescription(key="firmware", translation_key="firmware", entity_category=_DIAG,
                          value_fn=lambda d: d.get("version"), exists_fn=lambda d: bool(d.get("version"))),
    CentralSensorDescription(key="last_restart", translation_key="last_restart", device_class=SensorDeviceClass.TIMESTAMP,
                          entity_category=_DIAG, value_fn=lambda d: d.get("uptime"),
                          exists_fn=lambda d: d.get("uptime") is not None),
    # APs
    CentralSensorDescription(key="radios", translation_key="radios", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: sum(1 for r in (d.get("radios") or {}).values() if r["status"] == "up"),
                          exists_fn=lambda d: bool(d.get("radios")),
                          attrs_fn=lambda c, d: {"radios": list((d.get("radios") or {}).values())}),
    CentralSensorDescription(key="uplink", translation_key="uplink", value_fn=_uplink_state,
                          exists_fn=lambda d: d.get("uplink") is not None,
                          attrs_fn=lambda c, d: dict(d.get("uplink") or {})),
    CentralSensorDescription(key="wlans", translation_key="wlans", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: None if d.get("wlans") is None else sum(1 for w in d["wlans"] if w["enabled"]),
                          exists_fn=lambda d: d.get("wlans") is not None,
                          attrs_fn=lambda c, d: {"wlans": (d.get("wlans") or [])[:MAX_LIST_ROWS]}),
    # Switches
    CentralSensorDescription(key="ports_up", translation_key="ports_up", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: sum(1 for p in (d.get("ports") or {}).values() if p["up"]),
                          exists_fn=lambda d: bool(d.get("ports")), attrs_fn=_ports),
    CentralSensorDescription(key="poe_draw", translation_key="poe_draw", device_class=SensorDeviceClass.POWER,
                          native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: (d.get("poe") or {}).get("draw_w"),
                          exists_fn=lambda d: (d.get("poe") or {}).get("draw_w") is not None,
                          attrs_fn=lambda c, d: {"budget_w": (d.get("poe") or {}).get("budget_w")}),
    CentralSensorDescription(key="vlans", translation_key="vlans", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: None if d.get("vlans") is None else len(d["vlans"]), exists_fn=lambda d: d.get("vlans") is not None,
                          attrs_fn=lambda c, d: {"vlans": (d.get("vlans") or [])[:MAX_LIST_ROWS]}),
) + tuple(
    CentralSensorDescription(key=f"{band}_{field}", translation_key=f"radio_{field}", entity_category=_DIAG,
                          translation_placeholders={"band": label}, value_fn=_radio(band, field),
                          exists_fn=exists, **extra)
    for band, label in BANDS.items()
    for field, exists, extra in (
        ("clients", lambda d, band=band: band in (d.get("radios") or {}),
         {"state_class": SensorStateClass.MEASUREMENT}),
        # "56E" (channel 56, 80 MHz) is text, so no state class.
        ("channel", lambda d, band=band: band in (d.get("radios") or {}), {}),
        ("utilization", lambda d, band=band: band in (d.get("radios") or {}),
         {"state_class": SensorStateClass.MEASUREMENT, "native_unit_of_measurement": PERCENTAGE}),
        ("power", lambda d, band=band: _radio(band, "power")(d) is not None,
         {"state_class": SensorStateClass.MEASUREMENT, "native_unit_of_measurement": SIGNAL_STRENGTH_DECIBELS_MILLIWATT}),
        ("noise_floor", lambda d, band=band: _radio(band, "noise_floor")(d) is not None,
         {"state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
          "native_unit_of_measurement": SIGNAL_STRENGTH_DECIBELS_MILLIWATT}),
    )
)


# --- per picked client: value_fn(coordinator, sighting) ---------------------------

CLIENT_SENSORS = (
    CentralSensorDescription(key="ip", translation_key="ip", value_fn=lambda c, s: s.ip),
    CentralSensorDescription(key="ssid", translation_key="ssid", value_fn=lambda c, s: s.ssid),
    CentralSensorDescription(key="ap", translation_key="ap", value_fn=lambda c, s: c.device_name(s.ap_mac or s.switch_mac)),
    # Central reports SNR only; Signal is SNR + the AP radio's noise floor (empty for wired clients).
    CentralSensorDescription(key="rssi", translation_key="rssi", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                          native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                          state_class=SensorStateClass.MEASUREMENT, value_fn=lambda c, s: c.rssi_estimate(s)),
    CentralSensorDescription(key="snr", translation_key="snr", native_unit_of_measurement="dB",
                          state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: None if s.wired else s.snr),
    # Text: recorded in history, so a history graph shows when each changed (a roam, a band switch).
    CentralSensorDescription(key="band", translation_key="band", value_fn=lambda c, s: None if s.wired else s.band),
    *(CentralSensorDescription(key=key, translation_key=key, entity_category=category,
                               value_fn=lambda c, s, key=key: None if s.wired else s.details.get(key))
      for key, category in (("channel", None), ("phy", None), ("capabilities", None), ("security", _DIAG),
                            ("key_mgmt", _DIAG), ("auth", _DIAG), ("bssid", _DIAG))),
)


# Every Wi-Fi client gets these (with "add all other clients" on), disabled until enabled under Entities.
SIGNAL_SENSORS = tuple(d for d in CLIENT_SENSORS if d.key in ("rssi", "snr"))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeCentralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        net = c.net
        for site in list(net.sites):
            for desc in SITE_SENSORS:
                if desc.exists_fn(c, site):
                    yield f"{site_uid(entry, site)}_{desc.key}", lambda d=desc, s=site: SiteSensor(c, entry, s, d)
            for ssid in sorted({x.ssid for x in net.clients.values() if x.site_id == site and x.ssid}):
                desc = CentralSensorDescription(
                    key=f"ssid_{_slug(ssid)}", translation_key="ssid_clients", translation_placeholders={"ssid": ssid},
                    state_class=SensorStateClass.MEASUREMENT, value_fn=lambda c_, s_, ssid=ssid: _wireless(c_, s_, ssid),
                )
                yield f"{site_uid(entry, site)}_{desc.key}", lambda d=desc, s=site: SiteSensor(c, entry, s, d)
        for mac, dev in list(net.devices.items()):
            for desc in DEVICE_SENSORS:
                if desc.exists_fn(dev):
                    yield f"{device_uid(entry, mac)}_{desc.key}", lambda d=desc, m=mac: DeviceSensor(c, entry, m, d)
        for mac in c.picked:
            for desc in CLIENT_SENSORS:
                yield f"{client_uid(entry, mac)}_client_{desc.key}", lambda d=desc, m=mac: ClientSensor(c, entry, m, d)
        if c.add_all:
            for mac, s in list(net.clients.items()):
                if mac in c.picked or s.wired:
                    continue
                for desc in SIGNAL_SENSORS:
                    yield (f"{client_uid(entry, mac)}_client_{desc.key}",
                           lambda d=desc, m=mac, label=c.client_name(s): ClientSensor(c, entry, m, d, label))

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class SiteSensor(CentralEntity, SensorEntity):
    entity_description: CentralSensorDescription
    _unrecorded_attributes = _LIST_ATTRS

    def __init__(self, c: HpeCentralCoordinator, entry, site: str, desc: CentralSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._site = site
        self._attr_unique_id = f"{site_uid(entry, site)}_{desc.key}"
        self._attr_device_info = site_device_info(entry, site, c.net.sites.get(site, site))

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self._site)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator, self._site) if fn else None


class DeviceSensor(CentralEntity, SensorEntity):
    entity_description: CentralSensorDescription
    _unrecorded_attributes = _LIST_ATTRS

    def __init__(self, c: HpeCentralCoordinator, entry, mac: str, desc: CentralSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_{desc.key}"
        self._attr_device_info = network_device_info(c.hass, entry, c.net.devices[mac])
        self._boot: datetime | None = None

    @property
    def available(self) -> bool:
        dev = self.coordinator.net.devices.get(self._mac)
        return super().available and dev is not None and bool(dev.get("online"))

    @property
    def native_value(self) -> Any:
        dev = self.coordinator.net.devices.get(self._mac)
        if dev is None:
            return None
        value = self.entity_description.value_fn(dev)
        if self.entity_description.key == "last_restart":
            return self._restart_time(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dev = self.coordinator.net.devices.get(self._mac)
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator, dev) if fn and dev is not None else None

    def _restart_time(self, uptime: float | None) -> datetime | None:
        """Boot time from uptime; kept steady unless it moves by more than 2 minutes (a real restart)."""
        if uptime is None:
            return self._boot
        boot = (datetime.now(UTC) - timedelta(seconds=uptime)).replace(microsecond=0)
        if self._boot is None or abs((boot - self._boot).total_seconds()) > 120:
            self._boot = boot
        return self._boot


class ClientSensor(CentralEntity, SensorEntity):
    entity_description: CentralSensorDescription

    def __init__(self, c: HpeCentralCoordinator, entry, mac: str, desc: CentralSensorDescription,
                 label: str | None = None) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._mac = mac
        self._attr_unique_id = f"{client_uid(entry, mac)}_client_{desc.key}"
        if label is None:
            self._attr_device_info = client_device_info(entry, mac)
        else:
            # A client not picked: no device of its own (like its tracker), disabled until enabled under Entities.
            self._attr_has_entity_name = False
            self._attr_name = f"{label} {'Signal' if desc.key == 'rssi' else 'SNR'}"
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Any:
        s = self.coordinator.net.clients.get(self._mac)
        return self.entity_description.value_fn(self.coordinator, s) if s and s.home else None
