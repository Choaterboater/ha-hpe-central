"""The Central client against fake GreenLake / Central answers: token reuse and refresh, paging, errors."""

import base64
import json

import pytest

from custom_components.hpe_central.api import (
    CentralAuthError,
    CentralBusyError,
    CentralClient,
    CentralError,
    _jwt_claims,
)
from custom_components.hpe_central.const import TOKEN_URL

BASE = "https://us1.api.central.arubanetworks.com"
SECRET = "test-secret-not-real"


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_central.api.MIN_REQUEST_GAP_SECONDS", 0)


def token_body(n=1):
    claims = base64.urlsafe_b64encode(json.dumps({"platform_customer_id": "tenant-x", "n": n}).encode()).decode().rstrip("=")
    return {"access_token": f"e30.{claims}.sig", "expires_in": 7199}


async def test_token_reused_paging_follows_next(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(TOKEN_URL, json=token_body())
    # The mocker matches the first registered URL whose query is a subset, so the later page goes first.
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/devices?limit=100&next=c2", json={"items": [{"a": 2}], "next": None, "total": 2})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/devices?limit=100", json={"items": [{"a": 1}], "next": "c2", "total": 2})
    client = CentralClient(async_get_clientsession(hass), BASE, "id", SECRET)
    assert await client.devices() == [{"a": 1}, {"a": 2}]
    assert await client.devices() == [{"a": 1}, {"a": 2}]
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL) == 1
    assert client.tenant_id == "tenant-x"
    auth = [c[3]["Authorization"] for c in aioclient_mock.mock_calls if "devices" in str(c[1])]
    assert all(a.startswith("Bearer e30.") for a in auth)


async def test_401_refreshes_token_once_then_fails(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(TOKEN_URL, json=token_body())
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/aps?limit=100", status=401)
    client = CentralClient(async_get_clientsession(hass), BASE, "id", SECRET)
    with pytest.raises(CentralAuthError) as info:
        await client.aps()
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL) == 2
    assert SECRET not in str(info.value)


async def test_rate_limited(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(TOKEN_URL, json=token_body())
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/aps?limit=100", status=429, headers={"X-RateLimit-Reset": "1"})
    client = CentralClient(async_get_clientsession(hass), BASE, "id", SECRET)
    with pytest.raises(CentralBusyError) as info:
        await client.aps()
    assert info.value.retry_after == 1.0


async def test_bad_credentials(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(TOKEN_URL, status=400, text='{"error":"invalid_client","client_secret":"echo"}')
    client = CentralClient(async_get_clientsession(hass), BASE, "id", SECRET)
    with pytest.raises(CentralAuthError) as info:
        await client.sites()
    assert "echo" not in str(info.value) and SECRET not in str(info.value)


def test_jwt_claims_tolerates_opaque_tokens():
    assert _jwt_claims("opaque") == {}
    assert _jwt_claims("a.!!!.c") == {}


async def test_detail_endpoints(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(TOKEN_URL, json=token_body())
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/aps/AP1/radios", json={"items": [{"band": "5 GHz"}], "next": None})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/aps/AP1", json={"ports": []})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/switches/SW1/interfaces?limit=1000&offset=1",
                       json={"items": [{"name": "1/1/2"}], "total": 2, "offset": "1"})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/switches/SW1/interfaces?limit=1000",
                       json={"items": [{"name": "1/1/1"}], "total": 2, "offset": "0"})
    aioclient_mock.get(f"{BASE}/network-monitoring/v1/switches/SW1/vlans?limit=1000", status=404)
    client = CentralClient(async_get_clientsession(hass), BASE, "id", SECRET)
    assert await client.ap_radios("AP1") == [{"band": "5 GHz"}]
    assert await client.ap("AP1") == {"ports": []}
    assert await client.switch_interfaces("SW1") == [{"name": "1/1/1"}, {"name": "1/1/2"}]
    with pytest.raises(CentralError, match="404"):
        await client.switch_vlans("SW1")
