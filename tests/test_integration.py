"""Config flow, options flow, sweeps, presence, devices and events, with Home Assistant's test helpers and a fake Central."""

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hpe_central.const import AWAY_AFTER_SECONDS, DOMAIN, POLL_SECONDS, TOKEN_URL

BASE = "https://us1.api.central.arubanetworks.com"
TENANT = "0000000000000000000000000000aaaa"
CLIENT_ID = "test-client-id"
SECRET = "test-secret-not-real"
SITE = "1000000001"
EMPTY_SITE = "1000000002"
PHONE = "02:00:00:00:00:76"
AP_SERIAL = "AP0000001"
SW_SERIAL = "SW0000001"
GW_SERIAL = "GW0000001"
ENTITY = "device_tracker.test_phone"


def jwt(claims):
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{part}.sig"


DEVICES = [
    {"serialNumber": AP_SERIAL, "deviceName": "AP 1", "deviceType": "ACCESS_POINT", "macAddress": "02:00:00:00:00:01",
     "model": "AP-EXAMPLE", "status": "ONLINE", "ipv4": "192.0.2.2", "firmwareVersion": "10.7.0.0", "siteId": SITE,
     "uptimeInMillis": 3_600_000},
    {"serialNumber": SW_SERIAL, "deviceName": "Switch 1", "deviceType": "SWITCH", "macAddress": "02:00:00:00:00:05",
     "model": "SW-EXAMPLE", "status": "ONLINE", "siteId": SITE, "uptimeInMillis": 7_200_000},
    {"serialNumber": GW_SERIAL, "deviceName": "Gateway 1", "deviceType": "GATEWAY", "macAddress": "",
     "model": "GW-EXAMPLE", "status": "OFFLINE", "siteId": SITE},
]
APS = [{"serialNumber": AP_SERIAL, "clientCount": 1, "cpuUtilization": 7, "memoryUtilization": 40}]
GATEWAYS = [{"serialNumber": GW_SERIAL, "cpuUtilization": 3, "memoryUtilization": 50}]
PHONE_A = {"macAddress": PHONE, "hostName": "phone-a", "ipv4": "192.0.2.10", "wlanName": "Example",
           "clientConnectionType": "Wireless", "connectedDeviceSerial": AP_SERIAL, "status": "Connected",
           "wirelessBand": "5GHZ", "vlanId": "21"}
PRINTER = {"macAddress": "02:00:00:a3:c7:c2", "clientManufacturer": "ExampleVendor", "ipv4": "192.0.2.12",
           "clientConnectionType": "Wired", "connectedDeviceSerial": SW_SERIAL, "status": "Connected", "port": "1/1/2"}

# Detail calls. The AP ones are trimmed from a live tenant; the switch ones are synthetic,
# shaped like New Central's documented /interfaces, /interface-poe and /vlans answers.
AP_RADIOS = {"total": 3, "count": 3, "next": None, "items": [
    {"band": "6 GHz", "channel": "53E", "radioNumber": 2, "bandwidth": "80 MHz", "status": "UP", "clientCount": 0,
     "channelUtilization": "3", "noiseFloor": "-92", "power": "21"},
    {"band": "2.4 GHz", "channel": None, "radioNumber": 1, "bandwidth": None, "status": "DISABLED", "clientCount": 0,
     "channelUtilization": None, "noiseFloor": None, "power": None},
    {"band": "5 GHz", "channel": "56E", "radioNumber": 0, "bandwidth": "80 MHz", "status": "UP", "clientCount": 1,
     "channelUtilization": "12", "noiseFloor": "-90", "power": "18 dBm"},
]}
AP_DETAIL = {
    "serialNumber": AP_SERIAL, "status": "ONLINE", "currentUplinkInUse": "Ethernet",
    "wlans": [{"vlan": "21", "status": "ENABLED", "band": "5 GHz, 6 GHz", "wlanName": "Example"},
              {"vlan": "200", "status": "DISABLED", "band": "-", "wlanName": "Guest"}],
    "radios": [{"status": "DOWN", "band": "2.4 GHz", "channel": None, "bandwidth": None,
                "radioStats": [{"channelUtilization": "0", "noiseFloor": "0"}], "power": None},
               {"status": "UP", "band": "5 GHz", "channel": "56E", "bandwidth": "80 MHz",
                "radioStats": [{"channelUtilization": "0", "noiseFloor": "-92"}], "power": "21 dBm"}],
    "ports": [{"status": "UP", "nativeVlan": "5", "isUplink": True, "speed": "2500", "name": "eth0", "portIndex": 0},
              {"status": "DOWN", "nativeVlan": "5", "isUplink": False, "speed": "-", "name": "eth1", "portIndex": 1}],
}
SW_INTERFACES = [
    {"name": "1/1/10", "id": "10", "index": 10, "type": "TenGigabitEthernet", "operStatus": "Up", "speed": 10000,
     "uplink": True, "neighbour": "core-1", "neighbourPort": "1/1/48", "nativeVlan": 1, "description": "To core"},
    {"name": "1/1/1", "id": "1", "index": 1, "type": "GigabitEthernet", "operStatus": "Up", "speed": 1000,
     "uplink": False, "neighbour": "AP 1", "neighbourPort": "eth0", "neighbourSerial": AP_SERIAL, "nativeVlan": 5},
    {"name": "1/1/2", "id": "2", "index": 2, "type": "GigabitEthernet", "operStatus": "Up", "speed": 1000,
     "uplink": False, "nativeVlan": 10},
    {"name": "1/1/3", "id": "3", "index": 3, "type": "GigabitEthernet", "operStatus": "Down", "speed": 0, "uplink": False},
    {"name": "lag1", "id": "lag1", "type": "LAG", "operStatus": "Up", "speed": 20000},
    {"name": "vlan10", "id": "vlan10", "type": "VLAN", "operStatus": "Up"},
]
SW_POE = [{"name": "1/1/1", "powerDrawnInWatts": 12.5}, {"name": "1/1/2", "powerDrawnInWatts": 0.0}]
SW_VLANS = [{"id": "10", "name": "IOT", "status": "Up"}, {"id": "1", "name": "DEFAULT_VLAN", "status": "Up"}]
DETAIL = ("/radios", f"/aps/{AP_SERIAL}", "/interfaces", "/interface-poe", "/vlans")
# Shape of a real New Central /clients row (an iPhone with a private MAC; identifiers replaced):
# hostName / clientName are the MAC, clientManufacturer is "Private", and the AP's own
# clientCount still said 0 while /clients listed it.
IPHONE_MAC = "02:00:00:96:20:60"
IPHONE = {"macAddress": IPHONE_MAC, "hostName": IPHONE_MAC, "clientName": IPHONE_MAC, "clientVendor": "Apple",
          "clientManufacturer": "Private", "clientOperatingSystem": "Apple iOS Endpoint", "ipv4": "192.0.2.55",
          "wlanName": "outside", "wirelessBand": "5 GHz", "wirelessChannel": "44E (80 MHz)", "snr": 27, "vlanId": "200",
          "vlanName": None, "clientConnectionType": "Wireless", "connectedTo": "AP 1", "connectedDeviceSerial": AP_SERIAL,
          "connectedDeviceType": "AP", "status": "Connected", "connectedAt": "2026-10-22T13:50:52.535Z",
          "lastSeenAt": "0", "port": None, "siteId": SITE, "siteName": "Site 1"}
FAILED = {"macAddress": "02:00:00:00:00:99", "hostName": "failed", "status": "Failed"}


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_central.api.MIN_REQUEST_GAP_SECONDS", 0)


def central(aioclient_mock, clients=None, devices=None, alerts=None, status=200, token_status=200,
            radios_status=200, switch_status=200):
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, status=token_status,
                        json={"access_token": jwt({"platform_customer_id": TENANT}), "expires_in": 7199, "token_type": "Bearer"})
    aioclient_mock.get(f"{BASE}/network-config/v1/sites?limit=100", json={"items": [
        {"id": SITE, "scopeName": "Site 1", "deviceCount": 3}, {"id": EMPTY_SITE, "scopeName": "Site 2", "deviceCount": 0}],
        "total": 2, "offset": 0})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/devices?limit=100", status=status,
                       json={"items": devices if devices is not None else DEVICES, "next": None})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/aps?limit=100", json={"items": APS})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/gateways?limit=100", json={"items": GATEWAYS})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/clients?limit=1000&site-id={SITE}",
                       json={"items": clients if clients is not None else [], "total": len(clients or [])})
    aioclient_mock.get(f"{BASE}/network-notifications/v1/alerts?limit=100", json={"items": alerts or []})
    mon = f"{BASE}/network-monitoring/v1"
    aioclient_mock.get(f"{mon}/aps/{AP_SERIAL}/radios", status=radios_status, json=AP_RADIOS)
    aioclient_mock.get(f"{mon}/aps/{AP_SERIAL}", json=AP_DETAIL)
    for path, rows in (("interfaces", SW_INTERFACES), ("interface-poe", SW_POE), ("vlans", SW_VLANS)):
        aioclient_mock.get(f"{mon}/switches/{SW_SERIAL}/{path}?limit=1000", status=switch_status,
                           json={"items": rows, "count": len(rows), "total": len(rows), "offset": "0"})


def detail_calls(aioclient_mock) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if any(str(c[1]).split("?")[0].endswith(d) for d in DETAIL))


def calls(aioclient_mock, fragment: str) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if fragment in str(c[1]))


async def settle(hass):
    for _ in range(3):
        await hass.async_block_till_done()
        pending = [t for t in hass._background_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
    await hass.async_block_till_done()


async def tick(hass, freezer, seconds=POLL_SECONDS + 1):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def entry(options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        title="HPE Central (US-1)",
        unique_id=TENANT,
        data={"base_url": BASE, "client_id": CLIENT_ID, "client_secret": SECRET, "tenant_id": TENANT},
        options=options if options is not None else {"sites": [], "tracked": {PHONE: "Test Phone"}, "add_all_clients": True},
    )


async def setup(hass, e=None):
    e = e or entry()
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    return e


# --- flows ------------------------------------------------------------------------


async def test_config_flow_clusters_credentials_and_sites(hass, aioclient_mock):
    central(aioclient_mock)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    cfg = r["data_schema"].schema["base_url"].config
    assert {"https://us1.api.central.arubanetworks.com", "https://de1.api.central.arubanetworks.com"} <= {o["value"] for o in cfg["options"]}
    assert cfg["custom_value"] is True
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"base_url": "us1.api.central.arubanetworks.com/", "client_id": CLIENT_ID, "client_secret": SECRET})
    assert r["step_id"] == "sites"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"sites": [SITE, EMPTY_SITE]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "HPE Central (US-1)"
    assert r["data"] == {"base_url": BASE, "client_id": CLIENT_ID, "client_secret": SECRET, "tenant_id": TENANT}
    assert r["result"].unique_id == TENANT
    assert r["result"].options == {"sites": [], "tracked": {}, "add_all_clients": True}  # all ticked = all sites
    token_call = next(c for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL)
    assert token_call[2]["grant_type"] == "client_credentials"


async def test_config_flow_bad_credentials_and_duplicate(hass, aioclient_mock):
    central(aioclient_mock, token_status=401)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"base_url": BASE, "client_id": "x", "client_secret": "y"})
    assert r["errors"] == {"base": "invalid_auth"}
    central(aioclient_mock)
    entry(options={}).add_to_hass(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"base_url": BASE, "client_id": CLIENT_ID, "client_secret": SECRET})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_options_flow_labels_sites_and_picking(hass, aioclient_mock):
    central(aioclient_mock, [PRINTER, PHONE_A, FAILED])
    e = await setup(hass, entry(options={"sites": [], "tracked": {}, "add_all_clients": True}))
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("device_tracker.phone_a").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert ent_reg.async_get("device_tracker.failed") is None  # not connected

    r = await hass.config_entries.options.async_init(e.entry_id)
    labels = [o["label"] for o in r["data_schema"].schema["clients"].config["options"]]
    assert labels == ["ExampleVendor a3:c7:c2 · 192.0.2.12 · 02:00:00:a3:c7:c2 · ExampleVendor · connected now",
                      f"phone-a · 192.0.2.10 · {PHONE} · connected now"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [PHONE], "extra_mac": "", "sites": [SITE]})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {PHONE: "Test Phone"})
    await settle(hass)
    assert e.options == {"sites": [SITE], "tracked": {PHONE: "Test Phone"}, "add_all_clients": True}
    s = hass.states.get(ENTITY)
    assert s.state == STATE_HOME and s.attributes["ip"] == "192.0.2.10" and s.attributes["ap_name"] == "AP 1"
    # Site 2 was left out.
    assert hass.states.get("sensor.site_2_clients") is None


# --- devices, sites, events -----------------------------------------------------------


async def test_sites_devices_and_client_counts(hass, aioclient_mock):
    central(aioclient_mock, [PHONE_A, PRINTER])
    e = await setup(hass)
    st = lambda eid: hass.states.get(eid).state  # noqa: E731
    assert st("sensor.site_1_clients") == "2"
    assert st("sensor.site_1_wireless_clients") == "1"
    assert st("sensor.site_1_wired_clients") == "1"
    assert st("sensor.site_1_clients_on_example") == "1"
    assert st("sensor.site_1_aps_online") == "1"
    assert st("sensor.site_2_clients") == "0"
    assert st("binary_sensor.ap_1_online") == "on"
    assert st("binary_sensor.gateway_1_online") == "off"
    assert st("sensor.ap_1_clients") == "1"
    assert st("sensor.ap_1_cpu") == "7"
    assert st("sensor.ap_1_memory") == "40"
    assert st("sensor.ap_1_firmware") == "10.7.0.0"
    assert st("sensor.switch_1_clients") == "1"  # wired clients on it
    assert st("sensor.gateway_1_cpu") == STATE_UNAVAILABLE  # offline
    assert st("sensor.test_phone_connected_to") == "AP 1"
    assert hass.states.get("sensor.ap_1_last_restart").state.endswith("+00:00")
    # Only the clients of the site with devices are asked for.
    assert calls(aioclient_mock, f"site-id={EMPTY_SITE}") == 0
    dev_reg = dr.async_get(hass)
    site = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_site_{SITE}"), e.entry_id)
    ap = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_dev_020000000001"), e.entry_id)
    gw = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_dev_{GW_SERIAL.lower()}"), e.entry_id)
    assert ap.via_device_id == site.id and ap.model == "AP-EXAMPLE" and ap.serial_number == AP_SERIAL
    assert gw is not None and not gw.connections  # no MAC from Central: keyed by serial


def events_of(hass):
    return hass.states.get("event.site_1_events")


async def test_events_and_presence(hass, aioclient_mock, freezer):
    central(aioclient_mock, [PRINTER])
    await setup(hass)
    assert events_of(hass).state == "unknown"
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME

    seen = []
    hass.bus.async_listen("state_changed", lambda ev: ev.data["entity_id"] == "event.site_1_events" and seen.append(
        ev.data["new_state"].attributes.get("event_type")))
    gw_up = [*DEVICES[:2], {**DEVICES[2], "status": "ONLINE"}]
    central(aioclient_mock, [PRINTER, PHONE_A], devices=gw_up,
            alerts=[{"id": "al1", "name": "AP down", "severity": "Major", "status": "Active", "siteName": "Site 1"}])
    await tick(hass, freezer)
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert sorted(seen) == ["alarm", "client_joined", "device_up"]
    assert hass.states.get("sensor.site_1_active_alarms").state == "1"

    seen.clear()
    central(aioclient_mock, [PRINTER], devices=DEVICES,
            alerts=[{"id": "al1", "name": "AP down", "severity": "Major", "status": "Cleared", "siteName": "Site 1"}])
    await tick(hass, freezer)
    assert seen == ["device_down"]
    assert hass.states.get("sensor.site_1_active_alarms").state == "0"
    elapsed = POLL_SECONDS + 1
    while elapsed + POLL_SECONDS + 1 < AWAY_AFTER_SECONDS:
        await tick(hass, freezer)
        elapsed += POLL_SECONDS + 1
        assert hass.states.get(ENTITY).state == STATE_HOME, elapsed
    await tick(hass, freezer)
    await tick(hass, freezer)
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME
    assert "client_left" in seen


async def test_central_outage_keeps_state_then_unavailable(hass, aioclient_mock, freezer):
    central(aioclient_mock, [PHONE_A])
    await setup(hass)
    central(aioclient_mock, [PHONE_A], status=503)
    seen = set()
    for _ in range(15):
        await tick(hass, freezer, 121)
        seen.add(hass.states.get(ENTITY).state)
    assert STATE_NOT_HOME not in seen
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    central(aioclient_mock, [PHONE_A])
    await tick(hass, freezer, 301)
    assert hass.states.get(ENTITY).state == STATE_HOME


async def test_secret_never_logged(hass, aioclient_mock, caplog, freezer):
    central(aioclient_mock, [PHONE_A], token_status=401)
    e = entry()
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    assert e.state is config_entries.ConfigEntryState.SETUP_RETRY
    assert SECRET not in caplog.text and CLIENT_ID not in caplog.text


# --- controls ------------------------------------------------------------------------


async def test_restart_buttons_disabled_by_default_and_work(hass, aioclient_mock):
    from homeassistant.exceptions import HomeAssistantError

    central(aioclient_mock, [])
    aioclient_mock.post(f"{BASE}/network-troubleshooting/v1/aps/{AP_SERIAL}/reboot", status=202, text="")
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/switches?limit=100",
                       json={"items": [{"serialNumber": SW_SERIAL, "switchType": "AOS-S"}]})
    aioclient_mock.post(f"{BASE}/network-troubleshooting/v1/aos-s/{SW_SERIAL}/reboot", status=202, text="")
    aioclient_mock.post(f"{BASE}/network-troubleshooting/v1/gateways/{GW_SERIAL}/reboot", status=403)
    e = await setup(hass)
    ent_reg = er.async_get(hass)
    ids = ("button.ap_1_restart", "button.switch_1_restart", "button.gateway_1_restart")
    for eid in ids:
        assert ent_reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION, eid
        ent_reg.async_update_entity(eid, disabled_by=None)
    assert await hass.config_entries.async_reload(e.entry_id)
    await settle(hass)

    await hass.services.async_call("button", "press", {"entity_id": "button.ap_1_restart"}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.switch_1_restart"}, blocking=True)
    posts = [str(c[1]) for c in aioclient_mock.mock_calls if c[0] == "POST" and "reboot" in str(c[1])]
    assert posts == [f"{BASE}/network-troubleshooting/v1/aps/{AP_SERIAL}/reboot",
                     f"{BASE}/network-troubleshooting/v1/aos-s/{SW_SERIAL}/reboot"]
    with pytest.raises(HomeAssistantError, match="may not restart devices"):
        await hass.services.async_call("button", "press", {"entity_id": "button.gateway_1_restart"}, blocking=True)


# --- detail calls: radios, uplink, WLANs, switch ports / PoE / VLANs ---------------------


async def test_ap_radios_uplink_wlans_and_clients(hass, aioclient_mock):
    central(aioclient_mock, [PHONE_A, PRINTER])
    await setup(hass)
    radios = hass.states.get("sensor.ap_1_radios")
    assert radios.state == "2"
    assert [r["band"] for r in radios.attributes["radios"]] == ["2.4 GHz", "5 GHz", "6 GHz"]
    assert radios.attributes["radios"][1] == {"band": "5 GHz", "status": "up", "channel": "56E", "bandwidth": 80,
                                              "power": 18.0, "utilization": 12.0, "noise_floor": -90.0, "clients": 1}
    assert radios.attributes["radios"][0]["status"] == "disabled"
    st = lambda eid: hass.states.get(eid).state  # noqa: E731
    assert st("sensor.ap_1_5_ghz_channel") == "56E"
    assert st("sensor.ap_1_5_ghz_clients") == "1"
    assert st("sensor.ap_1_5_ghz_utilization") == "12.0"
    assert st("sensor.ap_1_5_ghz_power") == "18.0"
    assert st("sensor.ap_1_6_ghz_noise_floor") == "-92.0"
    assert hass.states.get("sensor.ap_1_2_4_ghz_channel") is not None
    assert hass.states.get("sensor.ap_1_2_4_ghz_power") is None  # never reported
    # The switch lists the AP as the neighbour on 1/1/1, so the uplink names it.
    up = hass.states.get("sensor.ap_1_uplink")
    assert up.state == "Switch 1 1/1/1"
    assert {k: up.attributes[k] for k in ("device", "port", "speed", "poe_w")} == {
        "device": "Switch 1", "port": "1/1/1", "speed": 2500, "poe_w": 12.5}
    wlans = hass.states.get("sensor.ap_1_wlans")
    assert wlans.state == "1"
    assert wlans.attributes["wlans"] == [{"ssid": "Example", "enabled": True, "vlan": "21", "clients": 1},
                                         {"ssid": "Guest", "enabled": False, "vlan": "200", "clients": 0}]
    clients = hass.states.get("sensor.ap_1_clients").attributes
    assert clients["clients"] == [{"name": "Test Phone", "mac": PHONE, "ip": "192.0.2.10", "ssid": "Example",
                                   "band": "5 GHz", "rssi": None, "vendor": None, "snr": None}]
    assert clients["by_band"] == {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 0}
    assert clients["by_ssid"] == {"Example": 1}
    site = hass.states.get("sensor.site_1_clients").attributes["clients"]
    assert site == [
        {"name": "ExampleVendor a3:c7:c2", "mac": "02:00:00:a3:c7:c2", "ip": "192.0.2.12", "ssid": None, "band": None,
         "ap": None, "wired": True, "port": "1/1/2", "switch": "Switch 1", "rssi": None, "vendor": "ExampleVendor",
         "snr": None},
        {"name": "Test Phone", "mac": PHONE, "ip": "192.0.2.10", "ssid": "Example", "band": "5 GHz", "ap": "AP 1",
         "wired": False, "port": None, "switch": None, "rssi": None, "vendor": None, "snr": None},
    ]
    assert st("sensor.test_phone_connected_to") == "AP 1"
    # List attributes stay out of the recorder.
    from custom_components.hpe_central.sensor import DeviceSensor, SiteSensor

    assert {"clients", "radios", "ports", "wlans", "vlans"} <= DeviceSensor._unrecorded_attributes
    assert "clients" in SiteSensor._unrecorded_attributes


async def test_switch_ports_poe_vlans(hass, aioclient_mock):
    central(aioclient_mock, [PRINTER])
    await setup(hass)
    ports = hass.states.get("sensor.switch_1_ports_up")
    assert ports.state == "3"
    assert ports.attributes["ports_total"] == 4  # LAG and VLAN interfaces left out
    rows = ports.attributes["ports"]
    assert [p["port"] for p in rows] == ["1/1/1", "1/1/2", "1/1/3", "1/1/10"]
    assert rows[0] == {"port": "1/1/1", "up": True, "speed": 1000, "uplink": False, "poe_w": 12.5, "neighbor": "AP 1",
                       "neighbor_port": "eth0", "vlan": 5, "description": None}
    assert rows[1]["neighbor"] == "02:00:00:a3:c7:c2"  # no LLDP: the wired client on it
    assert rows[2]["up"] is False and rows[2]["speed"] is None
    assert rows[3]["uplink"] is True and rows[3]["description"] == "To core"
    poe = hass.states.get("sensor.switch_1_poe_draw")
    assert poe.state == "12.5" and poe.attributes["budget_w"] is None and poe.attributes["unit_of_measurement"] == "W"
    vlans = hass.states.get("sensor.switch_1_vlans")
    assert vlans.state == "2"
    assert vlans.attributes["vlans"] == [{"id": 1, "name": "DEFAULT_VLAN"}, {"id": 10, "name": "IOT"}]
    # Port link sensors exist, disabled by default, and read the new port shape.
    ent = er.async_get(hass).async_get("binary_sensor.switch_1_port_1_1_1")
    assert ent is not None and ent.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert ent.unique_id == f"{TENANT}_dev_020000000005_port_1/1/1"


async def test_offline_switch_gets_no_detail_calls(hass, aioclient_mock):
    offline = [DEVICES[0], {**DEVICES[1], "status": "OFFLINE"}, DEVICES[2]]
    central(aioclient_mock, [PHONE_A], devices=offline)
    await setup(hass)
    assert calls(aioclient_mock, "/switches/") == 0
    assert hass.states.get("sensor.switch_1_ports_up") is None
    assert hass.states.get("sensor.ap_1_radios").state == "2"


async def test_detail_calls_cached_between_sweeps(hass, aioclient_mock, freezer):
    central(aioclient_mock, [PHONE_A])
    await setup(hass)
    first = detail_calls(aioclient_mock)
    assert first == 5  # AP radios + detail, switch interfaces + PoE + VLANs
    await tick(hass, freezer)  # 121 s
    await tick(hass, freezer)  # 242 s
    assert calls(aioclient_mock, "/network-monitoring/v1/devices") == 3
    assert detail_calls(aioclient_mock) == first
    await tick(hass, freezer)  # 363 s: a detail cycle is due
    assert detail_calls(aioclient_mock) == 2 * first
    assert hass.states.get("sensor.ap_1_radios").state == "2"


async def test_detail_errors_do_not_fail_the_sweep(hass, aioclient_mock, freezer):
    central(aioclient_mock, [PHONE_A], radios_status=404, switch_status=404)
    e = await setup(hass)
    assert e.state is config_entries.ConfigEntryState.LOADED
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert hass.states.get("sensor.switch_1_ports_up") is None
    assert hass.states.get("sensor.switch_1_vlans") is None
    # The radios call failed: the AP detail's own radio list is used instead.
    radios = hass.states.get("sensor.ap_1_radios")
    assert radios.state == "1"
    assert radios.attributes["radios"][0]["noise_floor"] is None  # "0" from a radio that is down
    assert hass.states.get("sensor.ap_1_5_ghz_power").state == "21.0"
    # A 429 on a detail call stops the detail calls, not the sweep.
    central(aioclient_mock, [PHONE_A], radios_status=429)
    await tick(hass, freezer, 361)
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert calls(aioclient_mock, "/network-monitoring/v1/devices") == 1
    assert calls(aioclient_mock, "/switches/") == 0
    assert hass.states.get("sensor.site_1_clients").state == "1"


# --- remembered clients ------------------------------------------------------------------------

LAPTOP = "02:00:00:00:00:40"


def seen_key(e) -> str:
    return f"{DOMAIN}.{e.entry_id}.seen_clients"


async def test_seen_clients_store(hass, aioclient_mock, freezer, hass_storage):
    import time

    e = entry()
    now = time.time()
    hass_storage[seen_key(e)] = {"version": 1, "key": seen_key(e), "data": {"clients": {
        "020000000040": {"mac": "020000000040", "hostname": "laptop", "last_seen": now - 3600},
        "020000000041": {"mac": "020000000041", "hostname": "ancient", "last_seen": now - 31 * 86400},
    }}}
    central(aioclient_mock, [PHONE_A, PRINTER])
    await setup(hass, e)
    seen = e.runtime_data.seen.rows
    assert set(seen) == {"020000000040", "020000000076", "020000a3c7c2"}  # older than 30 days dropped
    assert seen["020000000076"]["hostname"] == "phone-a" and seen["020000000076"]["band"] == "5 GHz"
    assert seen["020000a3c7c2"]["wired"] is True
    await tick(hass, freezer, 61)  # the delayed save
    saved = hass_storage[seen_key(e)]["data"]["clients"]
    assert set(saved) == set(seen)
    assert await hass.config_entries.async_remove(e.entry_id)
    await settle(hass)
    assert seen_key(e) not in hass_storage


async def test_options_picker_lists_remembered_clients(hass, aioclient_mock, hass_storage):
    import time

    e = entry(options={"sites": [], "tracked": {"02:00:00:00:00:42": "Old Tag"}, "add_all_clients": True})
    now = time.time()
    hass_storage[seen_key(e)] = {"version": 1, "key": seen_key(e), "data": {"clients": {
        "020000000040": {"mac": "020000000040", "hostname": "laptop", "ip": "192.0.2.20", "manufacture": "ExampleCo",
                         "last_seen": now - 2 * 3600 - 60},
        "020000000043": {"mac": "020000000043", "hostname": "tablet", "last_seen": now - 600},
        "020000000044": {"mac": "020000000044", "hostname": "last-week", "last_seen": now - 8 * 86400},
    }}}
    central(aioclient_mock, [PHONE_A])
    await setup(hass, e)
    r = await hass.config_entries.options.async_init(e.entry_id)
    labels = [o["label"] for o in r["data_schema"].schema["clients"].config["options"]]
    assert labels == [
        "Old Tag · 02:00:00:00:00:42 · not seen in 30 days",
        f"phone-a · 192.0.2.10 · {PHONE} · connected now",
        "tablet · no ip · 02:00:00:00:00:43 · seen 10 min ago",
        "laptop · 192.0.2.20 · 02:00:00:00:00:40 · ExampleCo · seen 2 h ago",
    ]
    # An offline client can be picked and keeps its remembered hostname as the default name.
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [LAPTOP], "extra_mac": "", "sites": []})
    assert r["step_id"] == "names"
    assert r["data_schema"]({})[LAPTOP] == "laptop"


async def test_real_central_client_row(hass, aioclient_mock):
    """Counts come from /clients even when the AP and radio counts lag at 0; MAC-as-hostname means no name."""
    global APS
    lagging, APS = APS, [{**APS[0], "clientCount": 0}]
    try:
        central(aioclient_mock, [IPHONE])
        await setup(hass)
    finally:
        APS = lagging
    assert hass.states.get("sensor.ap_1_clients").state == "1"
    attrs = hass.states.get("sensor.ap_1_clients").attributes
    assert attrs["clients"] == [{"name": "Apple 96:20:60", "mac": IPHONE_MAC, "ip": "192.0.2.55", "ssid": "outside",
                                 "band": "5 GHz", "rssi": None, "vendor": "Apple", "snr": 27}]
    assert attrs["by_band"] == {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 0} and attrs["by_ssid"] == {"outside": 1}
    assert hass.states.get("sensor.ap_1_5_ghz_clients").state == "1"
    row = hass.states.get("sensor.site_1_clients").attributes["clients"][0]
    assert (row["name"], row["vendor"], row["snr"], row["ap"], row["wired"], row["port"]) == (
        "Apple 96:20:60", "Apple", 27, "AP 1", False, None)
    assert er.async_get(hass).async_get("device_tracker.apple_96_20_60") is not None  # auto-added, disabled


async def test_client_named_by_another_integration(hass, aioclient_mock):
    # A camera another integration already knows by MAC is listed by that name, not by vendor + MAC.
    other = MockConfigEntry(domain="example_camera")
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, name="Front door",
                                           connections={(dr.CONNECTION_NETWORK_MAC, "02:00:00:a3:c7:c2")})
    central(aioclient_mock, [PHONE_A, PRINTER])
    await setup(hass)
    site = hass.states.get("sensor.site_1_clients").attributes["clients"]
    assert [c["name"] for c in site] == ["Front door", "Test Phone"]


async def test_central_name_beats_integration_name_but_not_a_rename(hass, aioclient_mock):
    # Central's name (ClearPass) beats the name an integration made up for a bulb; a rename in HA beats both.
    other = MockConfigEntry(domain="example_bulb")
    other.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    bulb = dev_reg.async_get_or_create(config_entry_id=other.entry_id, name="Bulb 00:00:76",
                                       connections={(dr.CONNECTION_NETWORK_MAC, PHONE)})
    central(aioclient_mock, [{**PHONE_A, "userName": "Light_Porch"}, PRINTER])
    await setup(hass, entry(options={"tracked": {}}))
    names = lambda: [c["name"] for c in hass.states.get("sensor.site_1_clients").attributes["clients"]]  # noqa: E731
    assert "Light_Porch" in names() and "Bulb 00:00:76" not in names()

    dev_reg.async_update_device(bulb.id, name_by_user="Porch light")
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "Porch light" in names()
