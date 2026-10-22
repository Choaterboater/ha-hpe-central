"""Set up HPE Central in the UI: cluster and API client, sites, then which clients to track."""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import client_for
from .api import CentralAuthError, CentralError, clean_base_url
from .const import (
    CENTRAL_CLUSTERS,
    CONF_ADD_ALL,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SITES,
    CONF_TENANT_ID,
    CONF_TRACKED,
    DEFAULT_ADD_ALL,
    DEFAULT_BASE_URL,
    DOMAIN,
    PICKER_SECONDS,
)
from .coordinator import SeenClients, known_name
from .errors import SourceAuthError, SourceError
from .presence import client_label, mac_key, mac_pretty
from .source import CentralSource

_LOGGER = logging.getLogger(__name__)

CONF_CLIENTS = "clients"
CONF_EXTRA_MAC = "extra_mac"


def _sites_selector(sites: dict[str, str]) -> SelectSelector:
    options = [SelectOptionDict(value=k, label=v) for k, v in sorted(sites.items(), key=lambda kv: kv[1].lower())]
    return SelectSelector(SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST))


def _site_choice(picked: list[str], sites: dict[str, str]) -> list[str]:
    """Store an empty list for "all sites", so sites added later are included too."""
    return [] if not picked or set(picked) >= set(sites) else sorted(picked)


class HpeCentralConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._sites: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HpeCentralOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_BASE_URL: clean_base_url(user_input[CONF_BASE_URL]),
                CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
                CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET].strip(),
            }
            client = client_for(self.hass, data)
            try:
                self._sites = await CentralSource(client).sites()
            except CentralAuthError:
                errors["base"] = "invalid_auth"
            except CentralError:
                errors["base"] = "cannot_connect"
            else:
                tenant = client.tenant_id or data[CONF_CLIENT_ID]
                await self.async_set_unique_id(tenant)
                self._abort_if_unique_id_configured()
                self._data = {**data, CONF_TENANT_ID: tenant}
                return await self.async_step_sites()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=u, label=f"{n} ({u.split('//')[1]})") for u, n in CENTRAL_CLUSTERS.items()
                            ],
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
            description_placeholders={"example_url": "https://us1.api.central.arubanetworks.com"},
        )

    async def async_step_sites(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None or not self._sites:
            cluster = CENTRAL_CLUSTERS.get(self._data[CONF_BASE_URL], self._data[CONF_BASE_URL].split("//")[-1])
            return self.async_create_entry(
                title=f"HPE Central ({cluster})",
                data=self._data,
                options={
                    CONF_SITES: _site_choice((user_input or {}).get(CONF_SITES, []), self._sites),
                    CONF_TRACKED: {},
                    CONF_ADD_ALL: DEFAULT_ADD_ALL,
                },
            )
        return self.async_show_form(
            step_id="sites",
            data_schema=vol.Schema({vol.Optional(CONF_SITES, default=list(self._sites)): _sites_selector(self._sites)}),
        )


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def picker_label(row: dict[str, Any], connected: bool, now: float, name: str | None = None) -> str:
    """name · ip · mac · vendor · (connected now | seen 3 h ago)."""
    parts = [name or client_label(row), row.get("ip") or "no ip", mac_pretty(row.get("mac"))]
    if row.get("manufacture"):
        parts.append(row["manufacture"])
    parts.append("connected now" if connected else f"seen {_ago(now - float(row.get('last_seen') or 0))}")
    return " · ".join(parts)


class HpeCentralOptionsFlow(OptionsFlow):
    """Pick clients connected now or seen in the last 7 days (or type a MAC), then name them."""

    def __init__(self) -> None:
        self._picked: list[str] = []
        self._hostnames: dict[str, str] = {}
        self._add_all = DEFAULT_ADD_ALL
        self._site_ids: list[str] = []
        self._all_sites: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current: dict[str, str] = {mac_key(m): n for m, n in self.config_entry.options.get(CONF_TRACKED, {}).items()}
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = [mac_key(m) for m in user_input.get(CONF_CLIENTS, [])]
            extra = [x for x in user_input.get(CONF_EXTRA_MAC, "").replace(";", ",").split(",") if x.strip()]
            for raw in extra:
                if not mac_key(raw):
                    errors[CONF_EXTRA_MAC] = "invalid_mac"
                picked.append(mac_key(raw))
            if not errors:
                self._picked = list(dict.fromkeys(m for m in picked if m))
                self._add_all = user_input.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
                self._site_ids = _site_choice(user_input.get(CONF_SITES, []), self._all_sites)
                if not self._picked:
                    return self.async_create_entry(data=self._options({}))
                return await self.async_step_names()

        options, load_error = await self._client_options(current)
        if load_error:
            errors["base"] = load_error
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CLIENTS, default=[mac_pretty(m) for m in current]): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                    vol.Optional(CONF_EXTRA_MAC, default=""): str,
                    vol.Optional(
                        CONF_ADD_ALL, default=self.config_entry.options.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_SITES, default=self.config_entry.options.get(CONF_SITES) or list(self._all_sites)
                    ): _sites_selector(self._all_sites),
                }
            ),
            errors=errors,
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {mac_key(m): n for m, n in self.config_entry.options.get(CONF_TRACKED, {}).items()}
        if user_input is not None:
            names = {
                mac_pretty(m): (user_input.get(mac_pretty(m)) or "").strip() or self._hostnames.get(m) or mac_pretty(m)
                for m in self._picked
            }
            return self.async_create_entry(data=self._options(names))
        schema = {
            vol.Required(mac_pretty(m), default=current.get(m) or self._hostnames.get(m) or mac_pretty(m)): str
            for m in self._picked
        }
        listing = "\n".join(
            f"- `{mac_pretty(m)}`: {self._hostnames.get(m) or 'not connected now'}" for m in self._picked
        )
        return self.async_show_form(
            step_id="names",
            data_schema=vol.Schema(schema),
            description_placeholders={"clients": listing},
        )

    def _options(self, names: dict[str, str]) -> dict[str, Any]:
        return {CONF_SITES: self._site_ids, CONF_TRACKED: names, CONF_ADD_ALL: self._add_all}

    async def _seen(self) -> dict[str, dict[str, Any]]:
        """Clients remembered by the running entry (or read from its store when it is not loaded)."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if isinstance(getattr(coordinator, "seen", None), SeenClients):
            return coordinator.seen.rows
        seen = SeenClients(self.hass, self.config_entry.entry_id)
        await seen.async_load()
        return seen.rows

    async def _client_options(self, current: dict[str, str]) -> tuple[list[SelectOptionDict], str | None]:
        """Connected clients across the chosen sites, clients seen in the last 7 days, and clients already tracked.

        Tracked first, then connected now, then the rest by most recently seen.
        """
        entry = self.config_entry
        source = CentralSource(client_for(self.hass, entry.data), entry.options.get(CONF_SITES))
        now = time.time()
        rows: dict[str, tuple[tuple[Any, ...], str]] = {}
        error = None
        try:
            self._all_sites = await CentralSource(source.client).sites()
            for c in await source.clients():
                if not (m := mac_key(c.get("mac"))):
                    continue
                label = known_name(self.hass, m, c.get("user_name")) or client_label(c)
                self._hostnames[m] = label
                rows[m] = ((1, label.lower()), picker_label({**c, "mac": m}, True, now, label))
        except SourceAuthError:
            error = "invalid_auth"
        except SourceError as err:
            _LOGGER.warning("Could not list Central clients: %s", err)
            error = "cannot_connect"
        for m, row in (await self._seen()).items():
            last = float(row.get("last_seen") or 0)
            # Tracked clients are always listed; others only when seen in the last 7 days.
            if m in rows or (m not in current and now - last > PICKER_SECONDS):
                continue
            label = known_name(self.hass, m, row.get("user_name")) or client_label({**row, "mac": m})
            self._hostnames.setdefault(m, label)
            rows[m] = ((2, -last), picker_label({**row, "mac": m}, False, now, label))
        for m, name in current.items():  # the store keeps 30 days
            rows.setdefault(m, ((0,), f"{name} · {mac_pretty(m)} · not seen in 30 days"))
        ordered = sorted(rows.items(), key=lambda kv: ((0, kv[1][1].lower()) if kv[0] in current else kv[1][0]))
        return [SelectOptionDict(value=mac_pretty(m), label=label) for m, (_, label) in ordered], error
