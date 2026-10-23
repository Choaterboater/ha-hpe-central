"""Constants for the HPE Central integration."""

DOMAIN = "hpe_central"

CONF_BASE_URL = "base_url"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_TENANT_ID = "tenant_id"
CONF_SITES = "sites"  # options: site ids to include; empty = all
CONF_TRACKED = "tracked"  # options: {"02:00:00:00:00:01": "Name"}
CONF_SCAN = "scan_seconds"  # options: seconds between sweeps
CONF_ADD_ALL = "add_all_clients"  # options: also add every other client, disabled until enabled
DEFAULT_ADD_ALL = True

MANUFACTURER = "HPE Central"

# New Central API gateways (base URL -> cluster name), from HPE's published list.
CENTRAL_CLUSTERS = {
    "https://us1.api.central.arubanetworks.com": "US-1",
    "https://us2.api.central.arubanetworks.com": "US-2",
    "https://us4.api.central.arubanetworks.com": "US-WEST-4",
    "https://us5.api.central.arubanetworks.com": "US-WEST-5",
    "https://us6.api.central.arubanetworks.com": "US-East1",
    "https://ca1.api.central.arubanetworks.com": "Canada-1",
    "https://de1.api.central.arubanetworks.com": "EU-1",
    "https://de2.api.central.arubanetworks.com": "EU-Central2",
    "https://de3.api.central.arubanetworks.com": "EU-Central3",
    "https://gb1.api.central.arubanetworks.com": "UK",
    "https://in1.api.central.arubanetworks.com": "APAC-1",
    "https://jp1.api.central.arubanetworks.com": "APAC-EAST1",
    "https://au1.api.central.arubanetworks.com": "APAC-SOUTH1",
    "https://ae1.api.central.arubanetworks.com": "UAE",
    "https://cn1.api.central.arubanetworks.com.cn": "China",
    "https://internal.api.central.arubanetworks.com": "Internal",
}
DEFAULT_BASE_URL = "https://us1.api.central.arubanetworks.com"
# GreenLake issues the OAuth tokens for every cluster (client credentials grant).
TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"

# Site event entity: what it can fire.
EVENT_CLIENT_JOINED = "client_joined"
EVENT_CLIENT_LEFT = "client_left"
EVENT_DEVICE_DOWN = "device_down"
EVENT_DEVICE_UP = "device_up"
EVENT_ALARM = "alarm"
EVENT_TYPES = [EVENT_CLIENT_JOINED, EVENT_CLIENT_LEFT, EVENT_DEVICE_DOWN, EVENT_DEVICE_UP, EVENT_ALARM]

# REST sweep: devices, APs, gateways and alerts per tenant, clients per site with
# devices: every POLL_SECONDS by default, changeable in the options (MIN..MAX).
POLL_SECONDS = 30
MIN_POLL_SECONDS = 15
MAX_POLL_SECONDS = 600
# Per-device detail calls (AP radios and detail, switch interfaces / PoE / VLANs)
# cost one request each, so they run at most this often and are cached between.
SAFETY_SWEEP_SECONDS = 300
DETAIL_SECONDS = SAFETY_SWEEP_SECONDS
# List attributes (clients, ports, ...) are capped at this many rows.
MAX_LIST_ROWS = 500
# Clients seen are remembered for the picker (Central has no client history):
# kept this long, saved at most this often, offered in the picker for PICKER_SECONDS.
SEEN_KEEP_SECONDS = 30 * 86400
SEEN_SAVE_DELAY_SECONDS = 60
PICKER_SECONDS = 7 * 86400
# A client counts as away once neither the stream nor a sweep has seen it for
# this long, so a short Wi-Fi drop or a sleeping radio does not look like leaving.
AWAY_AFTER_SECONDS = 600
# How often away is re-checked between sweeps.
AWAY_CHECK_SECONDS = 30
# Short Central errors keep the last known state; only after this long without a
# good sweep (and no live stream) do the trackers become unavailable, never not_home.
UNAVAILABLE_AFTER_SECONDS = 300
# Backoff ceiling after 429 / 5xx answers, and between stream reconnects.
MAX_BACKOFF_SECONDS = 300
STREAM_RETRY_SECONDS = 5
# Every Central request gives up after this long; the stream pings this often.
REQUEST_TIMEOUT_SECONDS = 20
STREAM_HEARTBEAT_SECONDS = 30
# Central allows 10 requests per second per account; stay well under it so other
# tools using the same API client keep room.
MIN_REQUEST_GAP_SECONDS = 0.35

# Central's client stream (Advanced subscription): client joins in seconds. Sweeps still run.
CLIENT_STREAM_PATH = "/network-monitoring/v1/clients-events"
