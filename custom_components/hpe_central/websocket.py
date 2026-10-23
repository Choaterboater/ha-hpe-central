"""<domain>/clients: every client with everything the network says about it, for the client list card.

Rows can be long, so they stay out of state attributes and the card asks for them while it is shown.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import CONF_CLIENT_COLUMNS, CONF_CLIENT_LAYOUT, DEFAULT_CLIENT_COLUMNS, DEFAULT_CLIENT_LAYOUT, DOMAIN


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_clients)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/clients"})
@callback
def ws_clients(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rows = []
    settings = None
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        rows.extend(entry.runtime_data.client_rows())
        # The columns and layout chosen in the integration's options, for cards that set none of their own.
        settings = settings or {"columns": entry.options.get(CONF_CLIENT_COLUMNS, DEFAULT_CLIENT_COLUMNS),
                                "layout": entry.options.get(CONF_CLIENT_LAYOUT, DEFAULT_CLIENT_LAYOUT)}
    connection.send_result(msg["id"], {"clients": rows, "settings": settings})
