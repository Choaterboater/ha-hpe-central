"""Presence rule and Central normalisation, no Home Assistant needed."""

from custom_components.hpe_central.const import AWAY_AFTER_SECONDS
from custom_components.hpe_central.presence import PresenceTracker, client_label, mac_key
from custom_components.hpe_central.source import (
    alarm_from_central,
    ap_uplink_from_central,
    band_label,
    client_from_central,
    client_is_connected,
    device_from_central,
    poe_from_central,
    port_sort_key,
    ports_from_central,
    radios_from_central,
    vlans_from_central,
    wlans_from_central,
)

KEY = "020000000076"


def test_presence_rule():
    t = PresenceTracker({KEY})
    assert t.update([], 0.0) == ([], [])
    assert t.update([{"mac": "02:00:00:00:00:76"}], 10.0) == ([KEY], [])
    assert t.evaluate(10.0 + AWAY_AFTER_SECONDS - 1) == []
    assert t.evaluate(10.0 + AWAY_AFTER_SECONDS) == [KEY]


def test_client_from_central():
    c = client_from_central(
        {"macAddress": "02:00:00:00:00:76", "hostName": "", "clientName": "phone-a", "ipv4": "192.0.2.10",
         "wlanName": "Example", "connectedDeviceSerial": "ap0000001", "clientVendor": "ExampleVendor"},
        "s1", {"AP0000001": "020000000001"})
    assert c.pop("last_seen") > 0  # Central gave none: now
    assert c == {"mac": "02:00:00:00:00:76", "site_id": "s1", "ip": "192.0.2.10", "hostname": "phone-a",
                 "manufacture": "ExampleVendor", "ssid": "Example", "ap_mac": "020000000001", "rssi": None, "snr": None,
                 "band": None, "wired": False, "port": None, "switch_mac": None, "vlan": None}
    w = client_from_central(
        {"macAddress": "02:00:00:00:00:77", "clientConnectionType": "Wired", "connectedDeviceSerial": "SW1",
         "port": "1/1/7", "vlanId": "30", "wlanName": "stale", "lastSeenAt": 1790301396910},
        "s1", {"SW1": "020000000005"})
    assert (w["wired"], w["port"], w["switch_mac"], w["ap_mac"], w["ssid"], w["vlan"]) == (
        True, "1/1/7", "020000000005", None, None, 30)
    assert w["last_seen"] == 1790301396.91
    assert client_from_central({"wirelessBand": "2.4GHZ", "port": "None"}, "s1", {})["band"] == "2.4 GHz"
    assert client_from_central({"clientConnectionType": "Wired", "portNumber": "None"}, "s1", {})["port"] is None
    assert client_label({"mac": "020000a3c7c2", "manufacture": "ExampleVendor"}) == "ExampleVendor a3:c7:c2"
    assert client_label({"mac": "020000a3c7c2"}) == "02:00:00:a3:c7:c2"
    real = client_from_central(
        {"macAddress": "02:00:00:96:20:60", "hostName": "02:00:00:96:20:60", "clientName": "02:00:00:96:20:60",
         "clientVendor": "Apple", "clientManufacturer": "Private", "wirelessBand": "5 GHz", "snr": 27, "vlanId": "200",
         "clientConnectionType": "Wireless", "connectedDeviceSerial": "AP1", "lastSeenAt": "0", "port": None},
        "s1", {"AP1": "020000000001"})
    assert (real["hostname"], real["manufacture"], real["band"], real["snr"], real["vlan"], real["wired"]) == (
        None, "Apple", "5 GHz", 27, 200, False)
    assert client_label(real) == "Apple 96:20:60"
    assert client_from_central({"clientManufacturer": "Private"}, "s1", {})["manufacture"] is None
    # The authenticated name (ClearPass MAC-auth guest entry) beats the DHCP hostname; a MAC as the
    # username (plain MAC auth) does not.
    named = {"macAddress": "02:00:00:00:00:78", "hostName": "wiz_000078", "userName": "Light_Porch"}
    assert client_from_central(named, "s1", {})["hostname"] == "Light_Porch"
    mac_auth = {"macAddress": "02:00:00:00:00:78", "hostName": "wiz_000078", "userName": "020000000078"}
    assert client_from_central(mac_auth, "s1", {})["hostname"] == "wiz_000078"
    assert client_from_central({**mac_auth, "clientName": "Kitchen bulb"}, "s1", {})["hostname"] == "Kitchen bulb"
    assert client_is_connected({"status": "Connected"}) and client_is_connected({})
    assert not client_is_connected({"status": "Failed"})


def test_device_from_central():
    d = device_from_central(
        {"serialNumber": "AP0000001", "deviceName": "AP 1", "deviceType": "ACCESS_POINT", "macAddress": "02:00:00:00:00:01",
         "status": "ONLINE", "firmwareVersion": "10.7", "siteId": "s1", "uptimeInMillis": 60000},
        {"clientCount": 3, "cpuUtilization": "7", "memoryUtilization": 40})
    assert d["mac"] == "020000000001" and d["id"] == "AP0000001" and d["type"] == "ap" and d["online"] is True
    assert d["uptime"] == 60 and d["clients"] == 3 and d["cpu"] == 7.0 and d["memory"] == 40
    g = device_from_central({"serialNumber": "GW0000001", "deviceType": "GATEWAY", "status": "OFFLINE"})
    assert g["mac"] == "gw0000001" and g["online"] is False and mac_key(g["mac"]) == "" and g["name"] == "GW0000001"


def test_alarm_from_central():
    a = alarm_from_central({"id": "x", "name": "AP down", "severity": "Major", "status": "Active", "siteName": "Site 1"},
                           {"Site 1": "s1"})
    assert a["site_id"] == "s1" and a["acked"] is False and a["type"] == "AP down"
    assert alarm_from_central({"id": "y", "status": "Cleared"}, {})["acked"] is True


def test_band_label_and_port_order():
    assert [band_label(b) for b in ("2.4 GHz", "5GHZ", "6 GHz", "BAND_24", "2GHz", None, "-")] == [
        "2.4 GHz", "5 GHz", "6 GHz", "2.4 GHz", "2.4 GHz", None, None]
    ports = ["1/1/10", "1/1/2", "ge-0/0/10", "ge-0/0/2", "1/1/1"]
    assert sorted(ports, key=port_sort_key) == ["1/1/1", "1/1/2", "1/1/10", "ge-0/0/2", "ge-0/0/10"]


# Trimmed from a live tenant: GET /network-monitoring/v1/aps/{serial}/radios.
RADIOS = [
    {"txUtilization": "0", "band": "6 GHz", "channel": "53E", "radioNumber": 2, "bandwidth": "80 MHz", "status": "UP",
     "clientCount": 0, "channelUtilization": "0", "noiseFloor": "-92", "power": "21"},
    {"txUtilization": None, "band": "2.4 GHz", "channel": None, "radioNumber": 1, "bandwidth": None, "status": "DISABLED",
     "clientCount": 0, "channelUtilization": None, "noiseFloor": None, "power": None},
]


def test_radios_from_central():
    radios = radios_from_central(RADIOS)
    assert list(radios) == ["band_24", "band_6"]
    assert radios["band_6"] == {"band": "6 GHz", "status": "up", "channel": "53E", "bandwidth": 80, "power": 21.0,
                                "utilization": 0.0, "noise_floor": -92.0, "clients": 0}
    assert radios["band_24"] == {"band": "2.4 GHz", "status": "disabled", "channel": None, "bandwidth": None,
                                 "power": None, "utilization": None, "noise_floor": None, "clients": 0}
    # The AP detail's radio list: stats nested, power with a unit, "0" noise from a radio that is down.
    detail = radios_from_central([
        {"status": "DOWN", "band": "2.4 GHz", "radioStats": [{"channelUtilization": "0", "noiseFloor": "0"}]},
        {"status": "UP", "band": "5 GHz", "channel": "56E", "bandwidth": "80 MHz", "power": "21 dBm",
         "radioStats": [{"channelUtilization": "7", "noiseFloor": "-92"}]},
    ])
    assert detail["band_24"]["status"] == "down" and detail["band_24"]["noise_floor"] is None
    assert (detail["band_5"]["power"], detail["band_5"]["utilization"], detail["band_5"]["clients"]) == (21.0, 7.0, None)


def test_ap_detail_uplink_and_wlans():
    detail = {
        "wlans": [{"vlan": "21", "status": "ENABLED", "band": "5 GHz, 6 GHz", "wlanName": "Example"},
                  {"vlan": "-", "status": "DISABLED", "band": "-", "wlanName": "Guest", "clientCount": 2}],
        "ports": [{"status": "DOWN", "isUplink": False, "speed": "-", "name": "eth1"},
                  {"status": "UP", "isUplink": True, "speed": "2500", "name": "eth0"}],
    }
    assert ap_uplink_from_central(detail) == {"device": None, "port": None, "speed": 2500, "poe_w": None}
    assert ap_uplink_from_central({"ports": [{"isUplink": "true", "speed": "5 Gbps"}]})["speed"] == 5000
    assert ap_uplink_from_central({"ports": []}) is None
    assert wlans_from_central(detail) == [
        {"ssid": "Example", "enabled": True, "vlan": "21", "clients": None},
        {"ssid": "Guest", "enabled": False, "vlan": None, "clients": 2},
    ]
    assert wlans_from_central({}) is None


def test_switch_ports_poe_vlans_parsing():
    interfaces = [
        {"name": "1/1/2", "id": "2", "index": 2, "status": "Down", "speed": 0, "uplink": "false", "accessVlan": "20"},
        {"name": "1/1/1", "id": "7", "index": 1, "operStatus": "Up", "speed": 1000, "uplink": True,
         "neighbour": "core", "neighbourPort": "1/1/48", "nativeVlan": 1, "description": "Uplink"},
        {"name": "lag1", "type": "LAG", "operStatus": "Up"},
        {"name": "1/1/3", "type": "Loopback"},
        {"name": "mgmt", "operStatus": "Up"},
    ]
    poe = [{"name": "7", "powerDrawnInWatts": 4.5}, {"name": "1/1/2", "powerDrawnInWatts": 0}]
    ports = ports_from_central(interfaces, poe)
    assert ports == {
        "1/1/2": {"up": False, "speed": None, "uplink": False, "poe_w": 0, "neighbor": None, "neighbor_port": None,
                  "vlan": 20, "description": None},
        "1/1/1": {"up": True, "speed": 1000, "uplink": True, "poe_w": 4.5, "neighbor": "core",
                  "neighbor_port": "1/1/48", "vlan": 1, "description": "Uplink"},
    }
    assert ports_from_central(interfaces, None)["1/1/1"]["poe_w"] is None
    assert poe_from_central(poe) == {"draw_w": 4.5, "budget_w": None}
    assert poe_from_central([]) is None and poe_from_central(None) is None
    assert vlans_from_central([{"id": "10", "name": "IOT"}, {"id": "1", "name": ""}, {"id": "x"}]) == [
        {"id": 1, "name": None}, {"id": 10, "name": "IOT"}]
    assert vlans_from_central(None) is None and vlans_from_central([]) == []
