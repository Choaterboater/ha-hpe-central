"""Small async client for the HPE (New) Central REST API (no Home Assistant imports, so it is testable alone).

Authentication is the OAuth client-credentials grant against GreenLake; the
token is cached, refreshed a minute before it expires and once more on a 401.
Requests are spaced so the integration stays well under Central's 10 per second.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import aiohttp

from .const import MIN_REQUEST_GAP_SECONDS, REQUEST_TIMEOUT_SECONDS, STREAM_HEARTBEAT_SECONDS, TOKEN_URL
from .errors import SourceAuthError, SourceBusyError, SourceError


class CentralError(SourceError):
    """Central could not be reached or answered with an error."""


class CentralAuthError(CentralError, SourceAuthError):
    """The client id / secret or the token was refused."""


class CentralBusyError(CentralError, SourceBusyError):
    """Central asked us to slow down (429) or had a server error (5xx)."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"Central answered {status}")
        self.status = status
        self.retry_after = retry_after


def clean_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.startswith(("https://", "http://")) else f"https://{url}"


class CentralClient:
    """Read calls (and the few write calls used by controls) to New Central."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        client_id: str,
        client_secret: str,
        token_url: str = TOKEN_URL,
    ) -> None:
        self._session = session
        self._base = clean_base_url(base_url)
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._token_url = token_url
        self._token: str | None = None
        self._token_exp = 0.0
        self._token_lock = asyncio.Lock()
        self._pace_lock = asyncio.Lock()
        self._last_request = 0.0
        self.claims: dict[str, Any] = {}

    # --- token ---------------------------------------------------------------

    async def token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            try:
                async with self._session.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    # Never echo the body: it may contain secrets.
                    if resp.status in (400, 401, 403):
                        raise CentralAuthError(f"GreenLake refused the client id / secret ({resp.status})")
                    if resp.status == 429 or resp.status >= 500:
                        raise CentralBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status != 200:
                        raise CentralError(f"GreenLake token endpoint answered {resp.status}")
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise CentralError(f"Cannot reach GreenLake: {type(err).__name__}") from err
            if not isinstance(body, dict) or not body.get("access_token"):
                raise CentralError("GreenLake token endpoint gave no token")
            self._token = body["access_token"]
            self._token_exp = time.time() + float(body.get("expires_in") or 7200)
            self.claims = _jwt_claims(self._token)
            return self._token

    @property
    def tenant_id(self) -> str | None:
        """The GreenLake workspace the credentials belong to (from the token), if present."""
        return self.claims.get("platform_customer_id") or None

    # --- stream --------------------------------------------------------------

    async def stream(self, path: str, on_message, on_connected) -> None:
        """One websocket connection to a streaming endpoint, until it closes (Central closes it when the token expires)."""
        token = await self.token()
        url = "wss" + self._base[len("https"):] if self._base.startswith("https") else self._base
        try:
            async with self._session.ws_connect(
                url + path, headers={"Authorization": f"Bearer {token}"}, heartbeat=STREAM_HEARTBEAT_SECONDS,
            ) as ws:
                on_connected()
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        on_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise CentralAuthError(f"Central refused the client stream ({err.status}): it needs an Advanced subscription") from err
            raise CentralError(f"Central client stream answered {err.status}") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CentralError(f"Central client stream: {type(err).__name__}") from err

    # --- requests ------------------------------------------------------------

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._last_request + MIN_REQUEST_GAP_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def request(self, method: str, path: str, params: dict[str, str] | None = None, body: Any = None) -> Any:
        """One API call; retried once with a fresh token on 401. Secrets never appear in errors."""
        for attempt in (0, 1):
            token = await self.token(force=attempt == 1)
            await self._pace()
            try:
                async with self._session.request(
                    method,
                    self._base + path,
                    params=params,
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    if resp.status in (401, 403):
                        raise CentralAuthError(f"Central refused the request ({resp.status} for {method} {path})")
                    if resp.status == 429 or resp.status >= 500:
                        raise CentralBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status >= 400:
                        raise CentralError(f"Central answered {resp.status} for {method} {path}")
                    text = await resp.text()
                    return json.loads(text) if text.strip() else None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise CentralError(f"Cannot reach Central: {type(err).__name__}") from err
        raise CentralAuthError("Central refused the token")

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self.request("GET", path, params)

    async def items(self, path: str, params: dict[str, str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every item of a list endpoint, following the "next" cursor (or offset when there is none)."""
        query = {"limit": str(limit), **(params or {})}
        out: list[dict[str, Any]] = []
        for _ in range(200):
            page = await self.get(path, query) or {}
            rows = page.get("items") or page.get("sites") or []
            out.extend(r for r in rows if isinstance(r, dict))
            total = page.get("total") or 0
            if not rows or (total and len(out) >= total):
                break
            if page.get("next"):
                query["next"] = str(page["next"])
            elif "offset" in page:
                query["offset"] = str(len(out))
            else:
                break
        return out

    # --- endpoints -------------------------------------------------------------

    async def sites(self) -> list[dict[str, Any]]:
        return await self.items("/network-config/v1/sites")

    async def devices(self) -> list[dict[str, Any]]:
        return await self.items("/network-monitoring/v1/devices")

    async def aps(self) -> list[dict[str, Any]]:
        return await self.items("/network-monitoring/v1/aps")

    async def gateways(self) -> list[dict[str, Any]]:
        return await self.items("/network-monitoring/v1/gateways")

    async def switches(self) -> list[dict[str, Any]]:
        return await self.items("/network-monitoring/v1/switches")

    async def clients(self, site_id: str) -> list[dict[str, Any]]:
        return await self.items("/network-monitoring/v1/clients", {"site-id": site_id}, limit=1000)

    # Per-device detail calls (one request each; the source caches them between sweeps).

    async def ap(self, serial: str) -> dict[str, Any]:
        return await self.get(f"/network-monitoring/v1/aps/{serial}") or {}

    async def ap_radios(self, serial: str) -> list[dict[str, Any]]:
        page = await self.get(f"/network-monitoring/v1/aps/{serial}/radios") or {}
        return [r for r in page.get("items") or [] if isinstance(r, dict)]

    async def switch_interfaces(self, serial: str) -> list[dict[str, Any]]:
        return await self.items(f"/network-monitoring/v1/switches/{serial}/interfaces", limit=1000)

    async def switch_poe(self, serial: str) -> list[dict[str, Any]]:
        return await self.items(f"/network-monitoring/v1/switches/{serial}/interface-poe", limit=1000)

    async def switch_vlans(self, serial: str) -> list[dict[str, Any]]:
        return await self.items(f"/network-monitoring/v1/switches/{serial}/vlans", limit=1000)

    async def alerts(self) -> list[dict[str, Any]]:
        return await self.items("/network-notifications/v1/alerts")

    async def reboot(self, kind: str, serial: str) -> None:
        """kind: "aps", "gateways", "cx" or "aos-s" (Central answers 202 Accepted)."""
        await self.request("POST", f"/network-troubleshooting/v1/{kind}/{serial}/reboot")


def _jwt_claims(token: str) -> dict[str, Any]:
    """Claims of a JWT access token, without verifying it (only used to name the tenant)."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(headers: Any) -> float | None:
    for key in ("Retry-After", "X-RateLimit-Reset"):
        try:
            if value := headers.get(key):
                return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return None
