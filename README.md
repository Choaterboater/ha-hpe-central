# HPE Central for Home Assistant (unofficial)

> Not affiliated with or endorsed by Hewlett Packard Enterprise (HPE). "HPE
> Central" is used only to name the service this integration talks to (HPE Aruba
> Networking Central, the "New Central" API). No HPE or Aruba logos are included;
> the icon is original artwork.

Brings an HPE Central account into Home Assistant so anything in it can drive an
automation. It is the sibling of the **HPE Mist** integration and works the same
way. One config entry per Central account (GreenLake workspace) gives you:

- **A device per site**: clients (total, wireless, wired), clients per SSID, APs
  online / offline, active alerts, and an **Events** entity (see below).
- **A device per AP, switch and gateway** (under its site): online, clients, and
  as diagnostics CPU, memory, firmware and last restart where Central reports them.
  An AP's clients come from Central; a switch's are the wired clients on it.
  - **APs**: *Radios* (radios up; attribute `radios`: band, status, channel,
    bandwidth, power, utilization, noise floor, clients), per band (2.4 / 5 / 6 GHz)
    channel, clients, utilization, power and noise floor (diagnostics), *Uplink*
    (`<switch> <port>` when a switch lists the AP as its neighbour, else
    `<speed> Mbps`; attributes device, port, speed, poe_w) and *WLANs* (enabled
    count; attribute `wlans`: ssid, enabled, vlan, clients). The *Clients* sensor
    carries `clients` (name, mac, ip, ssid, band, rssi, vendor, snr), `by_band` and `by_ssid`.
  - **Switches** (online ones): *Ports up* (attributes `ports_total` and `ports`:
    port, up, speed, uplink, poe_w, neighbor, neighbor_port, vlan, description, in
    natural port order), *PoE draw* (W; attribute `budget_w`, which Central does not
    report) and *VLANs* (count; attribute `vlans`), each only once Central reports it.
    Per-port link sensors exist too, disabled by default.
  - List attributes are capped at 500 rows and kept out of the recorder.
- The site's **Clients** sensor carries `clients`: every connected client with
  name, mac, ip, ssid, band, ap, wired, port, switch, rssi, vendor and snr. A
  client's name is the first of: the name you gave it (when picking it, or with the
  rename box / `hpe_central.rename_client`), a rename of its device in Home Assistant,
  Central's name for it (the user name, for example a ClearPass MAC-auth guest
  entry, or the client name), the name another integration gave the device with
  that MAC (a WiZ bulb, a Ring camera), the DHCP hostname, the vendor + last 3 MAC
  bytes, the MAC. A user name or client name that is just the MAC is skipped.
- **A tracker per client**: any client Central lists works, Wi-Fi or wired
  (phone, laptop, car, printer, IoT device). Clients you tick in the options are
  enabled, named, and get their own device and sensors (IP address, SSID, connected
  to). With *Add all other clients* on (the default), every other client gets a
  tracker too, **disabled until you enable it** in one click under Entities.

- **Restart buttons**, one per AP, switch and gateway, **disabled by default**
  (see Controls below).

Nothing is installed on the clients; everything is set up and edited in the
Home Assistant UI.

### Cards

The integration adds three cards to the dashboard card picker (no YAML, no
resources to add):

- **HPE Client list**: every client from HPE Central and HPE Mist. Sortable
  columns (click a header; again to reverse), search across every field, filters
  (site, wired / wireless, network, type) and *Connected now*. Pick the columns in
  the card editor: IP, network, connected to, signal, SNR, type, vendor, OS, site,
  VLAN, role, band, channel, security, auth, user name, hostname, MAC, connected
  for, last seen, health, source. Clicking a client opens everything Central
  gives for it (role, VLAN name, category, OS, channel, BSSID, PHY, capabilities,
  security, key management, auth, tunnel, connected since…) and a rename box.
  *Download CSV* saves the filtered list. Central reports SNR, not RSSI: the
  signal column shows `≈ RSSI`, the SNR plus the noise floor of the AP radio the
  client is on.
- **HPE Access point** and **HPE Switch**: pick a device.

The card reads its rows with the `hpe_central/clients` websocket command while
it is shown, so the long rows stay out of the state machine.

### Actions

| Action | Data | Does |
|---|---|---|
| `hpe_central.rename_client` | `mac`, `name` | Names any client. An empty name goes back to the name the integration found. A picked client's name in the options changes too. Typed names are kept in `.storage/hpe_central.client_names`. |

### Events

Each site's **Events** entity fires, with details as attributes:

| Event type | When | Attributes |
| --- | --- | --- |
| `client_joined` | a sweep lists a client that was away | mac, name, hostname, ip, ssid, ap, tracked |
| `client_left` | a client has not been listed for 10 minutes | same |
| `device_down` | a sweep reports an AP / switch / gateway offline | mac, name, type, model |
| `device_up` | it is back online | same |
| `alarm` | a new active Central alert for the site | id, type, severity, timestamp |

Use them as triggers: *Entity* → the site's Events entity, with a condition on the
`event_type` attribute. The first sweep after a start only takes stock, so a
restart does not fire a flood of events.

### Controls

| Entity | Does | Central call |
| --- | --- | --- |
| `button.<device>_restart` | restarts that AP, switch or gateway (a gateway restart drops the internet connection for a few minutes) | `POST /network-troubleshooting/v1/{aps,cx,aos-s,gateways}/<serial>/reboot` |

Central's token does not say what an API client may do, so the buttons are always
created, disabled; enable the ones you want under Entities. If the API client may
not restart devices, pressing one shows "this API client may not restart devices".
Restarting a switch first reads the switch list (one call) to tell CX from AOS-S.
No confirmation is asked beyond Home Assistant's own. WLAN on / off is not offered
yet.

## How it decides

- **Polling only.** Every **120 seconds** a sweep reads devices, AP and gateway
  stats, alerts, and the clients of every included site that has devices.
  Central's streaming API was checked: its client stream delivers nothing usable
  for presence (and the AP event stream delivered nothing), so it is not used.
- **Device detail** (AP radios and detail, switch interfaces / PoE / VLANs) costs a
  call per device, so it is read at most every 5 minutes (in practice every third
  sweep) and cached in between, only for online APs and switches. A detail call
  that fails (403, 404, 5xx) just leaves that data empty; a 429 stops the detail
  calls until the next detail cycle. Only the sweep's own calls can fail a sweep.
- **Central's client list lags.** `/network-monitoring/v1/clients` was seen to list a
  client 2 to 4 minutes after it joined, so with 120 s sweeps *home* can take up to
  about 6 minutes. Client counts (per AP, per radio, per SSID) are taken from that
  list too, because the AP's and radio's own counts were seen to stay at 0.
  Central gives SNR, not RSSI, so client rows carry `snr` and `rssi` is empty.
- **Home** on the first sweep that lists a client as connected; **away after 10
  minutes** (`AWAY_AFTER_SECONDS` in `const.py`) without being listed. A short drop
  does not count as leaving. Before its first sighting a client counts as away.
- **A Central outage never looks like everyone left.** Every call has a timeout;
  429 and 5xx answers back off (doubling, honouring `Retry-After` /
  `X-RateLimit-Reset`, at most 5 minutes); an expired token is refreshed
  automatically. Trackers keep their last state for 5 minutes, then become
  **unavailable** (not `not_home`). Away is only decided while the data is fresh.

### API budget

One sweep is **4 calls + 1 per site with devices** (`network-monitoring/v1/devices`,
`/aps`, `/gateways`, `network-notifications/v1/alerts`, and
`network-monitoring/v1/clients?site-id=…`), more only when a list runs past one
page: (4 + sites) × 30 calls per hour, 150 per hour for one site.

Device detail runs every third sweep (every 6 minutes, 10 times an hour): **2 calls
per online AP** (`/aps/<serial>/radios`, `/aps/<serial>`) and **3 per online switch**
(`/switches/<serial>/interfaces`, `/interface-poe`, `/vlans`); offline devices and
gateways cost nothing. For one site with 6 APs and 1 switch that is 120 calls per
hour with the switch offline, 150 with it online, so **270 to 300 calls per hour**
in all, plus a `network-config/v1/sites` call after a start or an error and one
GreenLake token about every 2 hours. Requests are spaced at least 0.35 s apart, well under
Central's 10 per second, so other tools on the same API client keep room.

## Install

HACS: add this repository as a custom repository (category *Integration*),
install **HPE Central**, restart Home Assistant.

Manual: copy `custom_components/hpe_central` into your Home Assistant
`config/custom_components/` folder and restart.

## Set up

1. In HPE GreenLake, create (or reuse) an API client for Central and note its
   **client id** and **client secret**. It needs read access to Central monitoring.
2. Settings → Devices & services → Add integration → **HPE Central**.
3. Pick your Central cluster (for example US-1 is
   `https://us1.api.central.arubanetworks.com`; HPE's New Central API docs list the
   API gateway base URL for each cluster), or type another base URL. Enter the client id and
   secret. They are checked by fetching a token and the site list, and are stored
   only in the config entry.
4. Pick the sites to include. Leaving all ticked includes every site, also ones
   added later. Add the integration again for another Central account.
5. Open the entry's **Configure** (*Clients*): change the sites, leave *Add all
   other clients* on (or off to keep only ticked ones), tick clients (shown as
   `name · ip · mac · vendor · connected now` or `… · seen 3 h ago`; one with no
   hostname shows its vendor and the last three bytes of its MAC, like
   `ExampleVendor a3:c7:c2`) or type the MAC of one that is not listed, then name
   each one. The list holds tracked clients first, then clients connected now, then
   clients seen in the last 7 days, most recent first. Central has no client
   history, so the integration remembers every client its sweeps see (for 30 days,
   in `.storage/hpe_central.<entry id>.seen_clients`, removed with the entry).

A phone must keep the same address on that network: on iOS set "Private Wi-Fi
Address" to Fixed; on Android the per-network randomised MAC is fine unless
"non-persistent" randomisation is turned on.

## Icon

The integration ships its own icon in `custom_components/hpe_central/brand/`
(original artwork). Home Assistant 2026.10 and later shows it from there.

## Planned

- WLAN on / off switches.
- Gateway uplink / tunnel sensors.

## Design notes

The coordinator only talks to a `PresenceSource` (`source.py`): `sweep()` returns
a `Snapshot` of sites, clients, devices and alerts as plain normalised dicts. The
presence rules (`presence.py`), coordinator and entities are shared with the HPE
Mist integration; each integration carries its own copy so it installs on its own.

## Tests

```
pip install -r requirements_test.txt
python -m pytest
```
