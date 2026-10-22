"""Where sightings, device stats and alarms come from.

The coordinator only talks to a PresenceSource (the same design as the HPE Mist
integration). Everything a source returns is already normalised into the plain
dicts described below.

Client: mac, site_id, ip, user_name (Central's name for it), hostname, manufacture (vendor), ssid, ap_mac, rssi, snr (dB),
        band ("2.4 GHz" | "5 GHz" | "6 GHz"), wired (bool), port (switch port
        when wired), switch_mac (wired), vlan (int), last_seen (epoch s),
        details {DETAIL_FIELDS key: text, connected_since (epoch s)} for the client list card.
Device: mac, site_id, type ("ap" | "switch" | "gateway"), name, model, serial,
        ip, version, online, uptime (s), clients, cpu (%), memory (%),
        temperature (C),
        radios {band_24 | band_5 | band_6: {band, status ("up" | "down" |
            "disabled"), channel (str, "56E" ok), bandwidth (MHz), power (dBm),
            utilization (%), noise_floor (dBm), clients}},
        ports {port_id: {up, speed (Mbps), uplink, poe_w, neighbor (LLDP name,
            or the wired client's hostname / mac), neighbor_port, vlan,
            description}} (physical switch interfaces only),
        uplink {device, port, speed (Mbps), poe_w} (APs),
        poe {draw_w, budget_w} (switches), vlans [{id, name}] (switches),
        wlans [{ssid, enabled, vlan, clients}] (APs).
Alarm:  id, site_id, type, severity, count, acked, timestamp.
Wlan:   id, ssid, enabled, site_id (None for an org-wide WLAN).
Devices also carry "id" (the controller's own id; the serial number for
Central) for controls. A device's "mac" is its MAC, or its serial when Central
gives no MAC.
Any value may be None when the controller does not report it. Radios, ports,
uplink, PoE, VLANs and WLANs come from per-device detail calls, made at most
every DETAIL_SECONDS (only for online APs and switches) and cached between.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .api import CentralBusyError, CentralClient
from .const import DETAIL_SECONDS
from .errors import SourceAuthError, SourceError
from .presence import mac_key

_LOGGER = logging.getLogger(__name__)

Client = dict[str, Any]
Device = dict[str, Any]
Alarm = dict[str, Any]
Wlan = dict[str, Any]
CLIENT_KEYS = ("mac", "site_id", "ip", "user_name", "hostname", "manufacture", "ssid", "ap_mac", "rssi", "snr",
               "band", "wired", "port", "switch_mac", "vlan", "last_seen")


@dataclass
class Snapshot:
    """One full sweep."""

    sites: dict[str, str] = field(default_factory=dict)  # site_id -> name
    clients: list[Client] = field(default_factory=list)  # connected now
    devices: list[Device] = field(default_factory=list)
    alarms: list[Alarm] = field(default_factory=list)


class PresenceSource(Protocol):
    """A controller that can sweep everything and, optionally, stream changes."""

    @property
    def has_stream(self) -> bool: ...

    async def sweep(self) -> Snapshot:
        """Everything now. Raises SourceError (or a subclass) on failure."""

    async def clients(self) -> list[Client]:
        """Only the clients connected now (for the options picker)."""

    async def stream(
        self,
        on_client: Callable[[Client], None],
        on_device: Callable[[Device], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Run one live connection until it ends. Device updates may be partial."""

    def forget_sites(self) -> None:
        """Drop cached site ids so the next call asks again."""

    # Controls: only used when can_write() is True.

    async def can_write(self) -> bool:
        """True if the credentials may change things."""

    async def wlans(self) -> list[Wlan]:
        """Every WLAN, org-wide and per site."""

    async def restart_device(self, device: Device) -> None: ...

    async def set_wlan_enabled(self, wlan: Wlan, enabled: bool) -> None: ...


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_LEADING_NUMBER = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z]*)")


def _lead(v: Any) -> float | None:
    """Number at the start of a string: "21 dBm" -> 21.0, "80 MHz" -> 80.0, "-" -> None."""
    if (n := _num(v)) is not None:
        return n
    m = _LEADING_NUMBER.match(str(v or ""))
    return float(m.group(1)) if m else None


def _int(v: Any) -> int | None:
    n = _lead(v)
    return int(n) if n is not None else None


def _speed(v: Any) -> int | None:
    """Link speed in Mbps: 2500, "2500", "5 Gbps", "1G"; 0 / "-" (no link) -> None."""
    n = _lead(v)
    if not n:
        return None
    m = _LEADING_NUMBER.match(str(v))
    if m and m.group(2).lower().startswith("g"):
        n *= 1000
    return int(n)


def _text(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s if s and s.lower() not in ("-", "none", "null") else None


def _true(v: Any) -> bool:
    return v is True or str(v).lower() == "true"


def _epoch(v: Any) -> float | None:
    """Epoch seconds from epoch ms / s or an ISO time; None for 0 or junk."""
    if (n := _num(v)) is not None:
        return n / 1000 if n > 1e11 else (n or None)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


BANDS = {"band_24": "2.4 GHz", "band_5": "5 GHz", "band_6": "6 GHz"}
_BAND_KEYS = {label: key for key, label in BANDS.items()}
_BAND = re.compile(r"2\.?4|2(?=g)|5|6")


def band_label(v: Any) -> str | None:
    """Band label: "5GHZ" -> "5 GHz", "BAND_24" / "2.4" -> "2.4 GHz"; None if unknown."""
    m = _BAND.search(str(v or "").lower().replace(" ", ""))
    if not m:
        return None
    return "5 GHz" if m.group() == "5" else "6 GHz" if m.group() == "6" else "2.4 GHz"


def port_sort_key(port: str) -> list[Any]:
    """Natural order: 1/1/2 before 1/1/10, ge-0/0/2 before ge-0/0/10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.split(r"(\d+)", str(port)) if p]


DEVICE_TYPES = {"ACCESS_POINT": "ap", "SWITCH": "switch", "GATEWAY": "gateway"}


def device_key(d: dict[str, Any]) -> str:
    return mac_key(d.get("macAddress")) or str(d.get("serialNumber") or "").lower()


def _named(c: dict[str, Any], *keys: str) -> str | None:
    """The first of keys that holds a name. Plain MAC auth and Central's own fallbacks fill
    these fields with the MAC, which is no name."""
    for key in keys:
        name = _text(c.get(key))
        if name and not (mac_key(name) and mac_key(name) == mac_key(c.get("macAddress"))):
            return name
    return None


def _vendor(c: dict[str, Any]) -> str | None:
    """clientVendor first; clientManufacturer is "Private" for a randomised MAC, which says nothing."""
    for key in ("clientVendor", "clientManufacturer"):
        vendor = _text(c.get(key))
        if vendor and vendor.lower() != "private":
            return vendor
    return None


def client_from_central(c: dict[str, Any], site_id: str, ap_by_serial: dict[str, str]) -> Client:
    wired = str(c.get("clientConnectionType") or c.get("connectionType") or "").lower() == "wired"
    device = ap_by_serial.get(str(c.get("connectedDeviceSerial") or "").upper())
    return {
        "mac": c.get("macAddress"),
        "site_id": site_id,
        "ip": c.get("ipv4") or None,
        # Central lists a client by its authenticated name (a ClearPass MAC-auth guest entry, an
        # 802.1X login); the hostname is whatever the client sent in its DHCP request.
        "user_name": _named(c, "userName", "clientName"),
        "hostname": _named(c, "hostName"),
        "manufacture": _vendor(c),
        "ssid": None if wired else c.get("wlanName") or None,
        "ap_mac": None if wired else device,
        "rssi": None,  # Central reports SNR, not RSSI
        "snr": None if wired else _int(c.get("snr")),
        "band": None if wired else band_label(c.get("wirelessBand") or c.get("band") or c.get("radioBand")),
        "wired": wired,
        "port": _text(c.get("port") or c.get("portNumber")) if wired else None,
        "switch_mac": device if wired else None,
        "vlan": _int(c.get("vlanId") or c.get("vlan")),
        "last_seen": _epoch(c.get("lastSeenAt")) or time.time(),
        "details": client_details(c),
    }


# Everything else Central says about a client, for the client list card: {our key: Central's keys}.
DETAIL_FIELDS = {
    "ipv6": ("ipv6",),
    "status": ("status",),
    "health": ("health", "experience"),
    "connected_to": ("connectedTo",),
    "connected_device_type": ("connectedDeviceType",),
    "vlan_name": ("vlanName",),
    "role": ("role", "userRole"),
    "os": ("clientOperatingSystem", "osType"),
    "category": ("clientCategory",),
    "function": ("clientFunction",),
    "channel": ("wirelessChannel",),
    "bssid": ("bssid",),
    "security": ("wirelessSecurity",),
    "key_mgmt": ("keyManagement",),
    "phy": ("phyType",),
    "capabilities": ("clientCapabilities",),
    "auth": ("authenticationType",),
    "tunnel": ("tunnelType",),
}


def client_details(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, sources in DETAIL_FIELDS.items():
        for src in sources:
            if value := _text(c.get(src)):
                out[key] = value
                break
    if since := _epoch(c.get("connectedAt")):
        out["connected_since"] = since
    return out


def client_is_connected(c: dict[str, Any]) -> bool:
    return str(c.get("status") or "Connected").lower() == "connected"


def device_from_central(d: dict[str, Any], stats: dict[str, Any] | None = None) -> Device:
    """Normalise one /network-monitoring/v1/devices row, with the AP / gateway stats row if any."""
    stats = stats or {}
    uptime = _num(d.get("uptimeInMillis") or stats.get("uptimeInMillis"))
    return {
        "mac": device_key(d),
        "id": d.get("serialNumber"),
        "site_id": d.get("siteId") or None,
        "type": DEVICE_TYPES.get(str(d.get("deviceType") or "").upper(), str(d.get("deviceType") or "").lower() or None),
        "name": d.get("deviceName") or d.get("serialNumber") or device_key(d),
        "model": d.get("model") or None,
        "serial": d.get("serialNumber") or None,
        "ip": d.get("ipv4") or None,
        "version": d.get("firmwareVersion") or None,
        "online": str(d.get("status") or "").upper() in ("ONLINE", "UP"),
        "uptime": uptime / 1000 if uptime is not None else None,
        "clients": _num(stats.get("clientCount")),
        "cpu": _num(stats.get("cpuUtilization")),
        "memory": _num(stats.get("memoryUtilization")),
        "temperature": None,
        "radios": {},
        "ports": {},
        "uplink": None,
        "poe": None,
        "vlans": None,
        "wlans": None,
    }


# --- detail calls -------------------------------------------------------------


def radio_from_central(r: dict[str, Any]) -> dict[str, Any]:
    """One /aps/{serial}/radios item (or an AP detail "radios" entry, whose stats are nested)."""
    stats = r.get("radioStats")
    if isinstance(stats, list):
        stats = stats[0] if stats and isinstance(stats[0], dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    status = str(r.get("status") or "").lower()
    noise = _lead(r["noiseFloor"] if r.get("noiseFloor") is not None else stats.get("noiseFloor"))
    util = r["channelUtilization"] if r.get("channelUtilization") is not None else stats.get("channelUtilization")
    return {
        "band": band_label(r.get("band")),
        "status": status if status in ("up", "disabled") else "down",
        "channel": _text(r.get("channel")),
        "bandwidth": _int(r.get("bandwidth")),
        "power": _lead(r.get("power")),
        "utilization": _lead(util),
        "noise_floor": noise or None,  # 0 means not measured (a radio that is down)
        "clients": _int(r.get("clientCount")),
    }


def radios_from_central(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{band_key: radio} in 2.4 / 5 / 6 order; with two radios on one band the one up wins."""
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        radio = radio_from_central(row)
        key = _BAND_KEYS.get(radio["band"] or "")
        if key and (key not in found or (found[key]["status"] != "up" and radio["status"] == "up")):
            found[key] = radio
    return {key: found[key] for key in BANDS if key in found}


def ap_uplink_from_central(detail: dict[str, Any]) -> dict[str, Any] | None:
    """The AP detail's uplink port. Central does not name the neighbour; a switch's interfaces may."""
    port = next((p for p in detail.get("ports") or [] if isinstance(p, dict) and _true(p.get("isUplink"))), None)
    if port is None:
        return None
    return {"device": None, "port": None, "speed": _speed(port.get("speed")), "poe_w": None}


def wlans_from_central(detail: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = detail.get("wlans")
    if not isinstance(rows, list):
        return None
    return [
        {
            "ssid": w.get("wlanName") or w.get("ssid"),
            "enabled": str(w.get("status") or "").upper() == "ENABLED",
            "vlan": _text(w.get("vlan")),
            "clients": _int(w.get("clientCount")),
        }
        for w in rows
        if isinstance(w, dict) and (w.get("wlanName") or w.get("ssid"))
    ]


_NOT_PHYSICAL = ("lag", "vlan", "loopback", "tunnel", "mgmt", "management", "port-channel", "trk")


def _physical(i: dict[str, Any]) -> bool:
    name = str(i.get("name") or "").lower()
    kind = str(i.get("type") or "").lower()
    return bool(name) and not name.startswith(_NOT_PHYSICAL) and not kind.startswith(_NOT_PHYSICAL)


def ports_from_central(interfaces: list[dict[str, Any]], poe_rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Physical switch interfaces, with each port's PoE draw from /interface-poe matched by name, id or index."""
    poe = {str(r.get("name")): _num(r.get("powerDrawnInWatts")) for r in poe_rows or [] if r.get("name") is not None}
    ports: dict[str, dict[str, Any]] = {}
    for i in interfaces:
        if not _physical(i):
            continue
        port = str(i["name"])
        status = i.get("operStatus") or i.get("status")
        keys = [port, *(str(i[k]) for k in ("id", "index") if i.get(k) is not None)]
        ports[port] = {
            "up": str(status or "").lower() == "up",
            "speed": _speed(i.get("speed")),
            "uplink": _true(i.get("uplink")),
            "poe_w": next((poe[k] for k in keys if k in poe), None),
            "neighbor": _text(i.get("neighbour") or i.get("neighbor")),
            "neighbor_port": _text(i.get("neighbourPort") or i.get("neighborPort")),
            "vlan": _int(i.get("nativeVlan") if i.get("nativeVlan") not in (None, "", "-") else i.get("accessVlan")),
            "description": _text(i.get("description")),
        }
    return ports


def poe_from_central(poe_rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Total draw over the ports; Central's interface PoE call gives no budget."""
    draws = [w for r in poe_rows or [] if (w := _num(r.get("powerDrawnInWatts"))) is not None]
    return {"draw_w": round(sum(draws), 1), "budget_w": None} if draws else None


def vlans_from_central(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if rows is None:
        return None
    vlans = [{"id": vid, "name": _text(v.get("name"))} for v in rows if (vid := _int(v.get("id"))) is not None]
    return sorted(vlans, key=lambda v: v["id"])


def _neighbor_serials(interfaces: list[dict[str, Any]]) -> dict[str, str]:
    """{neighbour serial: port}, so an AP's uplink can name the switch port it is on."""
    return {
        str(i["neighbourSerial"]).upper(): str(i["name"])
        for i in interfaces
        if _physical(i) and _text(i.get("neighbourSerial"))
    }


class _RateLimited(Exception):
    """A detail call got 429: stop the detail calls until the next detail cycle."""


def alarm_from_central(a: dict[str, Any], site_ids_by_name: dict[str, str]) -> Alarm:
    return {
        "id": a.get("id"),
        "site_id": site_ids_by_name.get(a.get("siteName") or ""),
        "type": a.get("name") or a.get("type"),
        "severity": a.get("severity"),
        "count": None,
        "acked": str(a.get("status") or "").lower() != "active",
        "timestamp": a.get("updatedAt") or a.get("createdAt"),
    }


class CentralSource:
    """HPE (New) Central: REST sweeps only (its client stream delivers nothing usable for presence)."""

    def __init__(self, client: CentralClient, site_filter: list[str] | None = None) -> None:
        self.client = client
        self.site_filter = set(site_filter or [])
        self._sites: dict[str, dict[str, Any]] = {}
        self._details: dict[str, dict[str, Any]] = {}  # serial -> cached detail fields
        self._details_at: float | None = None

    @property
    def has_stream(self) -> bool:
        return False

    async def site_rows(self) -> dict[str, dict[str, Any]]:
        if not self._sites:
            rows = await self.client.sites()
            self._sites = {r["id"]: r for r in rows if r.get("id") and (not self.site_filter or r["id"] in self.site_filter)}
        return self._sites

    async def sites(self) -> dict[str, str]:
        return {sid: r.get("scopeName") or r.get("name") or sid for sid, r in (await self.site_rows()).items()}

    def forget_sites(self) -> None:
        self._sites = {}

    async def _raw_clients(self) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for sid, row in (await self.site_rows()).items():
            if row.get("deviceCount") == 0:
                continue  # nothing can be connected there; saves a call per empty site
            out.extend((sid, c) for c in await self.client.clients(sid) if client_is_connected(c))
        return out

    async def clients(self) -> list[Client]:
        index = _ap_index(await self.client.devices())
        return [client_from_central(c, sid, index) for sid, c in await self._raw_clients()]

    async def sweep(self) -> Snapshot:
        sites = await self.sites()
        devices = await self.client.devices()
        stats = {str(r.get("serialNumber") or "").upper(): r for r in await self.client.aps()}
        stats.update({str(r.get("serialNumber") or "").upper(): r for r in await self.client.gateways()})
        raw = await self._raw_clients()
        index = _ap_index(devices)
        clients = [client_from_central(c, sid, index) for sid, c in raw]
        by_name = {name: sid for sid, name in sites.items()}
        alarms = [alarm_from_central(a, by_name) for a in await self.client.alerts()]
        # Switches report no client count of their own, and an AP's (and its radios')
        # count can lag at 0 while /clients lists the client: count the clients on each.
        on: dict[str, int] = {}
        for _, c in raw:
            serial = str(c.get("connectedDeviceSerial") or "").upper()
            on[serial] = on.get(serial, 0) + 1
        normal = []
        for d in devices:
            serial = str(d.get("serialNumber") or "").upper()
            dev = device_from_central(d, stats.get(serial))
            if dev["type"] in ("ap", "switch"):
                dev["clients"] = on.get(serial, 0)
            if dev["mac"] and (not self.site_filter or dev["site_id"] in sites):
                normal.append(dev)
        # Only the calls above can fail a sweep; detail calls never do.
        await self._refresh_details(normal)
        self._add_details(normal, [c for _, c in raw], clients)
        return Snapshot(sites=sites, clients=clients, devices=normal, alarms=[a for a in alarms if a["id"]])

    # --- per-device detail, cached between sweeps ---------------------------------

    async def _refresh_details(self, devices: list[Device]) -> None:
        """Every DETAIL_SECONDS ask each online AP / switch for its detail; between, only ones not asked yet."""
        now = time.time()
        wanted = {
            str(d["serial"]).upper(): d for d in devices if d.get("serial") and d["online"] and d["type"] in ("ap", "switch")
        }
        self._details = {s: v for s, v in self._details.items() if s in wanted}  # offline: no detail calls
        due = self._details_at is None or now - self._details_at >= DETAIL_SECONDS
        todo = [s for s in wanted if due or s not in self._details]
        if due:
            self._details_at = now
        try:
            for serial in todo:
                dev = wanted[serial]
                fetch = self._ap_detail if dev["type"] == "ap" else self._switch_detail
                self._details[serial] = await fetch(dev["serial"])
        except _RateLimited:
            _LOGGER.debug("Central rate limit on detail calls; keeping cached details until the next detail cycle")
            for serial in todo:
                self._details.setdefault(serial, {})

    async def _detail(self, what: str, serial: str, call: Any) -> Any:
        """One detail call; None (logged at debug) when it fails, so the sweep goes on."""
        try:
            return await call
        except CentralBusyError as err:
            if err.status == 429:
                raise _RateLimited from err
            _LOGGER.debug("Central %s for %s not available: %s", what, serial, err)
        except SourceError as err:
            _LOGGER.debug("Central %s for %s not available: %s", what, serial, err)
        return None

    async def _ap_detail(self, serial: str) -> dict[str, Any]:
        radios = await self._detail("radios", serial, self.client.ap_radios(serial))
        detail = await self._detail("AP detail", serial, self.client.ap(serial))
        rows = radios or (detail or {}).get("radios") or []
        return {
            "radios": radios_from_central([r for r in rows if isinstance(r, dict)]),
            "uplink": ap_uplink_from_central(detail) if detail else None,
            "wlans": wlans_from_central(detail) if detail else None,
        }

    async def _switch_detail(self, serial: str) -> dict[str, Any]:
        interfaces = await self._detail("interfaces", serial, self.client.switch_interfaces(serial))
        poe = await self._detail("interface PoE", serial, self.client.switch_poe(serial))
        vlans = await self._detail("VLANs", serial, self.client.switch_vlans(serial))
        return {
            "ports": ports_from_central(interfaces or [], poe),
            "poe": poe_from_central(poe),
            "vlans": vlans_from_central(vlans),
            "neighbors": _neighbor_serials(interfaces or []),
        }

    def _add_details(self, devices: list[Device], raw: list[dict[str, Any]], clients: list[Client]) -> None:
        """Copy the cached detail onto this sweep's devices, filled in from the clients connected now."""
        by_serial = {str(d["serial"]).upper(): d for d in devices if d.get("serial")}
        clients_by_serial: dict[str, list[Client]] = {}
        for r, c in zip(raw, clients, strict=True):
            clients_by_serial.setdefault(str(r.get("connectedDeviceSerial") or "").upper(), []).append(c)
        for serial, dev in by_serial.items():
            det = self._details.get(serial) or {}
            mine = [c for c in raw if str(c.get("connectedDeviceSerial") or "").upper() == serial]
            if dev["type"] == "ap":
                dev["radios"] = {k: dict(v) for k, v in (det.get("radios") or {}).items()}
                for radio in dev["radios"].values():
                    radio["clients"] = sum(1 for c in clients_by_serial.get(serial, []) if c["band"] == radio["band"])
                dev["uplink"] = dict(det["uplink"]) if det.get("uplink") else None
                if det.get("wlans") is not None:
                    dev["wlans"] = [
                        {**w, "clients": w["clients"] if w["clients"] is not None
                         else sum(1 for c in mine if c.get("wlanName") == w["ssid"])}
                        for w in det["wlans"]
                    ]
            elif dev["type"] == "switch":
                ports = {p: dict(v) for p, v in (det.get("ports") or {}).items()}
                for c in mine:  # a wired client names the port when LLDP does not
                    port = ports.get(_text(c.get("port") or c.get("portNumber")) or "")
                    if port is not None and not port["neighbor"]:
                        port["neighbor"] = c.get("hostName") or c.get("clientName") or c.get("macAddress")
                dev["ports"] = ports
                dev["poe"] = dict(det["poe"]) if det.get("poe") else None
                dev["vlans"] = [dict(v) for v in det["vlans"]] if det.get("vlans") is not None else None
        # An AP's uplink names the switch port that lists the AP as its neighbour.
        for serial, dev in by_serial.items():
            if dev["type"] != "ap":
                continue
            for sw_serial, det in self._details.items():
                port = (det.get("neighbors") or {}).get(serial)
                switch = by_serial.get(sw_serial)
                if port is None or switch is None:
                    continue
                link = switch["ports"].get(port) or {}
                uplink = dev["uplink"] or {"device": None, "port": None, "speed": None, "poe_w": None}
                dev["uplink"] = {**uplink, "device": switch["name"], "port": port,
                                 "speed": uplink["speed"] or link.get("speed"), "poe_w": link.get("poe_w")}
                break

    async def stream(self, on_client, on_device, on_connected) -> None:
        raise SourceError("HPE Central has no client stream")

    # Controls. Central's token says nothing about what the API client may do, so
    # restart buttons are always offered (disabled by default); a refusal is shown
    # when one is pressed.

    async def can_write(self) -> bool:
        return True

    async def wlans(self) -> list[Wlan]:
        return []  # WLAN on / off is not offered for Central yet

    async def restart_device(self, device: Device) -> None:
        serial = device["id"]
        if device["type"] == "ap":
            kind = "aps"
        elif device["type"] == "gateway":
            kind = "gateways"
        else:
            row = next((r for r in await self.client.switches() if r.get("serialNumber") == serial), {})
            kind = "aos-s" if "aos-s" in str(row.get("switchType") or "").lower().replace("_", "-") else "cx"
        try:
            await self.client.reboot(kind, serial)
        except SourceAuthError as err:
            raise SourceAuthError("this API client may not restart devices (Central refused it)") from err


def _ap_index(devices: list[dict[str, Any]]) -> dict[str, str]:
    return {str(d.get("serialNumber") or "").upper(): device_key(d) for d in devices if d.get("serialNumber")}
