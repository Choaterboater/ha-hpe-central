"""Central's client stream: protobuf CloudEvents decoded into clients, and a streamed join marks a client home."""

import asyncio

from custom_components.hpe_central.source import CentralSource, client_from_stream

MAC = "02:00:00:00:00:77"
AP_SERIAL = "AP0000001"
BY_SERIAL = {AP_SERIAL: "020000000001"}


# Sample data, not from a real network: a tiny protobuf writer for the test messages.
def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def f(num: int, value) -> bytes:
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    data = value.encode() if isinstance(value, str) else value
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def event(status=2, kind=1, site="1000000001", etype="com.hpe.greenlake.network-monitoring.v1.clients.state", mac=MAC):
    details = (f(1, mac) + f(3, site) + f(5, status) + f(6, AP_SERIAL) + f(7, "phone-z") + f(10, "192.0.2.77")
               + f(14, 21) + f(17, f(2, "Apple")))
    state = f(1, details) + f(2, kind) + f(4, "Example") + f(5, "5 GHz") + f(10, 31)
    payload = f(1, state)  # StreamClientMessage.client_state
    return f(4, etype) + f(8, f(1, "type.googleapis.com/x") + f(2, payload))


def test_a_wifi_join_is_decoded():
    c = client_from_stream(event(), BY_SERIAL, set())
    assert {k: c[k] for k in ("mac", "ip", "hostname", "ssid", "band", "snr", "ap_mac", "wired", "vlan", "manufacture")} == {
        "mac": MAC, "ip": "192.0.2.77", "hostname": "phone-z", "ssid": "Example", "band": "5 GHz", "snr": 31,
        "ap_mac": "020000000001", "wired": False, "vlan": 21, "manufacture": "Apple"}


def test_a_wired_join_has_no_wifi_fields():
    c = client_from_stream(event(kind=2), BY_SERIAL, set())
    assert c["wired"] is True and c["ssid"] is None and c["snr"] is None and c["switch_mac"] == "020000000001"


def test_disconnects_stats_other_sites_and_junk_are_ignored():
    assert client_from_stream(event(status=3), BY_SERIAL, set()) is None  # disconnected
    assert client_from_stream(event(etype="com.hpe.greenlake.network-monitoring.v1.clients.stats"), BY_SERIAL, set()) is None
    assert client_from_stream(event(site="other"), BY_SERIAL, {"1000000001"}) is None
    assert client_from_stream(b"\xff\xff\xff", BY_SERIAL, set()) is None


def test_source_stream_hands_joins_to_the_coordinator():
    class FakeClient:
        async def stream(self, path, on_message, on_connected):
            assert path == "/network-monitoring/v1/clients-events"
            on_connected()
            on_message(event())
            on_message(event(status=3))

    source = CentralSource(FakeClient(), None)
    seen, up = [], []
    asyncio.run(source.stream(seen.append, lambda d: None, lambda: up.append(True)))
    assert up == [True] and [c["mac"] for c in seen] == [MAC]
    assert source.has_stream is True
