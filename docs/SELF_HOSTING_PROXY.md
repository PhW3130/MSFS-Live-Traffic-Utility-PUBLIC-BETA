# Self-Hosting the Live Traffic Proxy Stack

This guide covers an independent deployment of the complete stack: the PySide6 desktop client, PHP web relay, and Windows C++ collector. Use your own host and provider credentials. The examples deliberately use `http://your-server.example`; replace that placeholder with your own deployment and do not run self-hosting tests against the Community Proxy.

The current collector chain uses only **adsb.lol → adsb.fi → OpenSky Network**. OpenSky is optional and requires local OAuth2 credentials on the collector PC. The proxy collects only regions that active clients request.

## Contents

- [Architecture and prerequisites](#architecture-and-prerequisites)
- [Build the complete `rea/` package](#build-the-complete-rea-package)
- [Configure and build the C++ collector](#configure-and-build-the-c-collector)
- [Configure OpenSky OAuth2 and quota tracking](#configure-opensky-oauth2-and-quota-tracking)
- [Deploy the PHP web relay](#deploy-the-php-web-relay)
- [Public status page and admin console](#public-status-page-and-admin-console)
- [Peer-mesh relay and failover](#peer-mesh-relay-and-failover)
- [Point desktop clients to your proxy](#point-desktop-clients-to-your-proxy)
- [Operational notes and troubleshooting](#operational-notes-and-troubleshooting)

## Architecture and Prerequisites

```text
Qt desktop clients  -- traffic / heartbeat -->  PHP web relay
        ^                                         ^   |
        |                                         |   | private demand poll / token-protected push
        +---- web-relay pull from elected peer ---+   C++ collector
                                                       |-- adsb.lol
                                                       |-- adsb.fi
                                                       `-- optional OpenSky Network
```

The PHP relay stores compressed regional snapshots and indexes as files; no database is required. A client traffic request records a coarse, short-lived demand area. The collector polls that demand and fetches only active areas. It pauses all upstream requests after 20 seconds without demand. Client IP addresses and exact client positions are not included in the public demand API.

For a self-hosted deployment you need:

- Windows 10/11 for the collector and packaged desktop client.
- MinGW-w64 `g++` or Visual Studio Build Tools with the C++ workload for the collector; Python 3.10+ and the repository's Python requirements for the client build.
- A PHP-enabled web host that allows the PHP process to write its proxy state files and has a writable system temporary directory.
- An OpenSky OAuth2 client only if you want the optional third source. adsb.lol and adsb.fi do not use a project API key.
- A private shared `LTU_TOKEN` for collector-to-PHP requests and console access. The desktop does not need the collector token for normal public traffic and status reads.

The web host must be reachable by the collector PC and clients. The relay does not need a database or inbound connections to client PCs. For HTTP deployments, use a trusted network or configure HTTPS at your web host; the PHP shared token must not be sent over an untrusted plaintext connection.

## Build the Complete `rea/` Package

### Package contents

Run `build_all.bat` from a Windows developer prompt. It creates a fresh `rea/` directory with:

- `rea/php/` — `index.php`, `client_demand.php`, and `aircraft_tracks.php` for the web host.
- `rea/proxy/` — `ltu_collector.exe`, `ltu_collector.ini`, OpenSky credential template, and watchdog/task scripts for the collector PC.
- `rea/user/MSFS Live Traffic Utility/` — the standalone Qt desktop client and its `page.txt` URL file.
- `rea/LIESMICH.txt` — generated package notes. The CI workflow also creates `rea/TOKEN-SETUP.txt` for its safe package.

`build_all.bat` deletes and recreates `rea/` before building. Back up any local settings or credentials stored there first. Never place real OAuth credentials in a directory that is going to be distributed or committed.

### Set your own deployment URL before building

Before building for self-hosting, replace the `LTU_URL` value in `build_all.bat` and the `url=` value in `proxy/ltu_collector.example.ini` with your own endpoint, for example:

```text
http://your-server.example/index.php
```

After building, verify both `rea/proxy/ltu_collector.ini` and `rea/user/MSFS Live Traffic Utility/page.txt` use your host. Do not start the collector or client until those files have been checked. The `page.example.txt` file is a template for a client-side `page.txt`; its first non-comment line is the proxy URL.

### Shared token

`token.txt` belongs beside `build_all.bat` in the repository root and is gitignored. If it is missing, `build_all.bat` generates a cryptographically random 256-bit token. `generate_token.bat` can create or replace it manually and prompts before replacing an existing file. A valid token is one line of 8–256 characters from `A-Z`, `a-z`, `0-9`, `.`, `_`, `~`, and `-`.

The build applies the same token to `rea/php/index.php` (`LTU_TOKEN`) and `rea/proxy/ltu_collector.ini`. The token is used for authenticated collector pushes, demand polls, source-status reports, history replay, and the admin console. It is not included in desktop client bundles. Keep `token.txt` private, do not commit it, and do not upload it to the web root. After rotating it, rerun `build_all.bat` and redeploy both the generated PHP and collector INI; older copies will no longer authenticate.

The GitHub Actions `Build complete distribution (rea)` workflow validates pull requests. On `main`, it builds the package with a non-secret placeholder token, pushes the generated `rea/` files in size-limited commits to a temporary branch, and updates `main` only after the complete upload succeeds. The package is not ready for production until you follow `TOKEN-SETUP.txt` and install your private token. The workflow does not upload an Actions artifact. A `v*` tag separately triggers the desktop Windows release workflow.

## Configure and Build the C++ Collector

The collector source is `proxy/ltu_collector.cpp`; it uses Windows WinHTTP and no external C++ libraries. From `proxy/`, `build_collector.bat` chooses MinGW or MSVC if available. Equivalent commands are:

```bat
:: MinGW-w64
g++ -O2 -static -pthread ltu_collector.cpp -o ltu_collector.exe -lwinhttp

:: MSVC, from the x64 Native Tools Command Prompt
cl /O2 /EHsc /MT ltu_collector.cpp /Fe:ltu_collector.exe /link winhttp.lib
```

The build script `build_all.bat` can build the collector and desktop client together. It uses MinGW-w64 `g++` or MSVC `cl`; set `LTU_BUILD_CXX=cl` or `LTU_BUILD_CXX=g++` to select a compiler explicitly. If no collector compiler is available, the script can still build the PHP and desktop portions but reports that the collector is missing.

Place `ltu_collector.ini` beside `ltu_collector.exe`. Start the collector by double-clicking the EXE or running it without arguments. It reads built-in defaults, then the adjacent INI, then optional command-line arguments; later values override earlier ones. The shipped source defaults are intended for the project deployment, so replace the URL and token before using a self-hosted collector.

Example self-hosting INI:

```ini
url=http://your-server.example/index.php
token=REPLACE_WITH_YOUR_PRIVATE_TOKEN
interval=5
opensky_interval=120
opensky_credentials=credentials.json
; Optional repeated demand allowlist; this does not start independent polling.
; region=50.03,8.57,250
; region=52.52,13.40,250
```

INI rules and options:

- One `key=value` setting per line. A line beginning with `;` or `#` is a comment; inline comments are not parsed. Section headers are ignored.
- Supported keys are `url`, `token`, `interval`, `opensky_interval`, `opensky_credentials`, and repeatable `region`.
- `interval` is the configured demand-poll interval in seconds (default 5, minimum accepted value 1; effective demand polling is clamped to 2–10 seconds). Upstream regional providers are feed-wide rate-limited to at least one request per second.
- `opensky_interval` has a hard minimum of 120 seconds. OpenSky requests are also subject to the shared persistent daily quota described below.
- `opensky_credentials` defaults to `credentials.json` next to the EXE. An absolute path is accepted; relative paths resolve beside the EXE.
- `region=LAT,LON,RADIUS_KM` entries form an optional allowlist for current client demand. They never initiate their own upstream requests. The maximum public regional coverage is 463 km (250 NM).

Command-line options are optional and override the INI:

```text
ltu_collector.exe --url http://your-server.example/index.php --token REPLACE_WITH_YOUR_PRIVATE_TOKEN --interval 5
ltu_collector.exe --region 50.03,8.57,250 --region 52.52,13.40,250
ltu_collector.exe --lat 50.03 --lon 8.57 --radius 250
```

`--region` can be repeated; command-line regions replace INI regions. The compatibility shortcut `--lat`/`--lon` with optional `--radius` sets a single allowlisted region. Radius values are in kilometres and are clamped to the public maximum. `--help` prints usage.

The collector polls `?action=demand` every five seconds by default. Demand areas expire after 20 seconds without a client traffic request; once none remain, all upstream queries pause. Normal regional order is adsb.lol, then adsb.fi, then optional OpenSky. An explicit worldwide search queues one global OpenSky snapshot through the proxy collector, not an adsb.lol global query; the old global-tile/raster polling path is not used. Regional responses are pushed to the PHP relay using the shared token.

### Watchdog and Windows logon task

For supervised operation, run the PowerShell watchdog from `rea/proxy` (or the directory containing the EXE):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_collector_watchdog.ps1
```

It restarts the collector after failures, performs a daily restart after 24 hours by default, and writes `logs/watchdog.log` plus timestamped `collector-*.stdout.log` and `collector-*.stderr.log` files. It keeps at most 100 recent run logs and removes logs older than 14 days; `watchdog.log` is rotated at 1 MB to a single `watchdog.log.1` backup. Directly double-clicking the collector writes to the console window instead of these watchdog files.

Install a Windows logon task for the current user with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_collector_task.ps1
```

Remove the task and stop its collector process with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\remove_collector_task.ps1
```

The scheduled task starts the watchdog at the current Windows user's logon with limited privileges. The collector and peer mesh use outbound HTTP to the PHP host; no inbound ports or router/firewall forwarding are needed. The removal script stops the task and matching collector process but keeps existing logs. The scripts are `proxy/run_collector_watchdog.ps1`, `proxy/install_collector_task.ps1`, and `proxy/remove_collector_task.ps1` in the source checkout.

## Configure OpenSky OAuth2 and Quota Tracking

OpenSky is the optional third regional source after adsb.lol and adsb.fi. Only the collector needs OAuth2 credentials. Register an OpenSky OAuth2 `client_credentials` client using the [OpenSky REST API documentation](https://openskynetwork.github.io/opensky-api/rest.html), then copy `rea/proxy/credentials.example.json` (or `proxy/credentials.example.json` in a source checkout) to `credentials.json` beside `ltu_collector.exe` and enter `clientId` and `clientSecret`:

```json
{
  "clientId": "REPLACE_WITH_YOUR_OPENSKY_CLIENT_ID",
  "clientSecret": "REPLACE_WITH_YOUR_OPENSKY_CLIENT_SECRET"
}
```

The template is not a credential. Restrict the real file to the collector account, keep it out of Git, releases, client folders, and the PHP web root, and use `opensky_credentials=` only if you need a different local path. These OpenSky credentials are separate from `LTU_TOKEN`, which authenticates collector-to-PHP requests. The collector obtains and refreshes OAuth access tokens automatically; credentials and bearer tokens remain in collector memory and are not sent to clients or written to logs. OpenSky's live API is restricted to research and non-commercial use.

OpenSky regional and explicit global searches share one `opensky_quota.txt` ledger beside the collector EXE. The ledger records attempted `/states/all` requests before they are sent, survives restarts, and should not be deleted if you want to preserve the daily cap. The quota guard enforces at least 120 seconds between attempts and a maximum of 700 attempts in a rolling 24-hour window. It fails closed if it cannot read or update the ledger. HTTP 429 handling honors `X-Rate-Limit-Retry-After-Seconds` and `Retry-After` (delta seconds or HTTP date), capped at one day.

OpenSky charges 1–4 credits per `/states/all` request depending on bounding-box area. The documented anonymous quota is 400 credits per IP per day, a standard account receives 4,000, and an active feeder receives 8,000. The collector's 700-attempt cap stays below 2,800 credits even at four credits per request, and is shared between regional traffic and requested global snapshots.

For each state vector, the collector keeps `baro_altitude` (index 7) and `geo_altitude` (index 13) separate and converts metres to feet; `velocity` (index 9) is converted from metres/second to knots; `vertical_rate` (index 11) is converted to feet/minute; `on_ground` is index 8.

## Deploy the PHP Web Relay

Upload these files together into the document root or a dedicated directory on your PHP host:

```text
proxy/index.php
proxy/client_demand.php
proxy/aircraft_tracks.php
```

For a manual deployment, set `LTU_TOKEN` in `index.php` to the same private value used in the collector INI. `build_all.bat` instead inserts the token into the generated PHP and INI. The web server must be able to write its state files in the relay directory; PHP must also be able to write its system temporary directory. The temporary demand store is outside the web root, file-locked, and set to mode `0600`. No database is needed.

The PHP directory stores `ltu_index.json`, `ltu_source_status.json`, `ltu_clients.json`, `ltu_mesh.json`, `ltu_relay.json`, compressed `ltu_r_<region>.json.gz` region snapshots, and `ltu_h_<region>_<timestamp>.json.gz` replay snapshots; the bounded push log is in the index. Use host access rules to deny direct HTTP downloads of these internal state files and snapshots; clients should use the `index.php` API. Public APIs return sanitized status, not the private token or raw client-demand geometry.

### PHP API and access rules

| Endpoint | Access | Purpose |
|---|---|---|
| `?action=traffic&bbox=N,S,E,W` | Client, public | Fetch only regions intersecting the visible map bounds; records coarse demand and returns `demand_recorded`. Circle queries remain supported as `?action=traffic&lat=...&lon=...&dist=...`. |
| `?action=push&region=<id>` | Collector, `X-LTU-Token` required | Push an ADS-B `ac[]` payload or OpenSky states for the active region; accepted source/status headers identify the source without exposing credentials. The PHP caps each aircraft payload at 30,000 records. |
| `?action=source_status&region=<id>` | Collector, `X-LTU-Token` required | Report fetch failures and bounded source metadata such as HTTP status, mode, provider, and freshness window. |
| `?action=demand` | Collector, `X-LTU-Token` required | Return active coarse demand areas with opaque IDs. The maximum is 64 areas; demand expires after 20 seconds. |
| `?action=global_search` | Client, public; collector polls protected demand | Queue a one-shot global OpenSky snapshot only after an explicit search; requests are centrally limited to one every 120 seconds and fresh snapshots can be reused for 180 seconds. |
| `?action=heartbeat` / `?action=logout` | Client, no token | Register client status and mesh heartbeat, or remove the client on orderly shutdown. |
| `?action=relay_push` / `?action=relay_pull` | Selected relay pushes; clients pull, no shared token | Publish and retrieve the elected peer's filtered traffic through the website. |
| `?action=healthdata` / `?action=statuspage` | Public, no token | Sanitized live status JSON and HTML status page. The default `index.php` action is the status page. |
| `?action=status` | Public, no token | Region age/count and a sanitized push log. |
| `?action=console` | Admin, shared token required | Authenticated management console with region health, traffic, push log, live map, push chart, and replay. |
| `?action=hist&region=<id>&ts=<unix>` | Admin console, shared token required | Retrieve a regional replay snapshot. |
| `?action=track&hex=<icao>` or `?action=track&flight=<callsign>` | Client, public | Retrieve the sampled one-hour flight path by ICAO hex or callsign; implemented in `aircraft_tracks.php`. |

Demand is stored in the server's private temporary directory using a coarse grid and keyed hash. Public health/status responses anonymize demand region IDs and remove exact coordinates. A traffic request may use a bounding box in `north,south,east,west` order or a center/radius query; the server filters aircraft and unpacks only intersecting regional snapshots. If the demand store cannot be written, `action=traffic` still returns `demand_recorded:false`; the Qt client logs this condition once until a successful request restores demand recording.

The collector publishes region snapshots to compressed files plus a small index. The index keeps a bounded push log; a replay snapshot containing reduced position/altitude data is taken every 60 seconds and retained for about 90 minutes. The independent sampled flight-history endpoint keeps paths for one hour. Configure the web directory to permit required state writes while preventing direct access to those files.

## Public Status Page and Admin Console

Open `http://your-server.example/` or `http://your-server.example/index.php` for the public status page; the explicit route is `http://your-server.example/index.php?action=statuspage`. It refreshes every five seconds from `?action=healthdata`, requires no shared token, and does not reveal client demand geometry. It shows proxy/collector health, region and aircraft totals, active demand, source status, clients, and the elected relay with web-relay freshness.

The page includes per-region/provider status with HTTP result and a provider-specific freshness window, plus a **client × component** matrix for the proxy, adsb.lol, adsb.fi, OpenSky, and MSFS. Green means healthy, orange indicates HTTP 429/rate limiting, red means down, and gray means not yet reported. Regions with expired source data are marked stale; when there is no active demand, collection is shown as paused.

Open `http://your-server.example/index.php?action=console` for the admin console and authenticate with the private shared token. It provides region status, current aircraft, push logs, a live map with region bounds and altitude-colored traffic, a push-count chart, and per-region replay controls with a time slider and play button. The dashboard refreshes through `fetch` without a full-page reload. Historical snapshots are reduced data, sampled once per minute and retained for approximately 90 minutes.

The collector source sends status updates for the three configured feeds. A source freshness window is separate from the PHP host's own reachability: a stale source status means the upstream collector has not recently reported that feed, not necessarily that the web host is offline.

## Peer-Mesh Relay and Failover

The Qt clients send a heartbeat to the PHP page; the server, not each client, decides the common mesh role. Normal heartbeats are every 30 seconds and switch to every 10 seconds while the mesh is active. A client entry expires after 600 seconds, the server accepts no more than one heartbeat per client every two seconds, and it retains at most 200 client entries. On orderly exit, the client posts `?action=logout`; the TTL handles abrupt power loss.

The server activates the mesh when the collector has no fresh data or at least half of fresh clients report `proxy_ok=false` (including HTTP 429). It randomly selects an eligible fresh client whose direct network is usable. The heartbeat response gives every client the same elected role and relay ID in `role`, `ersatz`, `mesh_active`, and `hb`. An elected relay polls traffic through the normal fallback chain and pushes it to `?action=relay_push` about every five seconds; only the currently elected UID is accepted. Other clients use `?action=relay_pull`, with bounding-box/radius filtering performed server-side. The relay becomes stale after 25 seconds.

If the elected relay stops heartbeating for 45 seconds or reports `net_ok=false` during a direct-source rate-limit storm, the server elects another candidate. A client that yields due to a direct-source 429 is not eligible for 120 seconds. When the collector resumes fresh service, all clients return to normal proxy mode and the relay stops. The mesh requires the web host to remain reachable; if that host itself is down, each client falls back directly to adsb.lol, adsb.fi, OpenSky, then cached traffic.

Set `peer_net.enabled` and an optional `peer_net.name` in `colors.json` or user settings. `LTU_MESH=off` disables the mesh. The mesh never opens ports or connects directly to another client; all heartbeats and relay traffic use the PHP web host.

## Point Desktop Clients to Your Proxy

Ready-to-use Community Proxy clients are already configured. For an independent instance, make the URL available through one of these options:

1. Put the endpoint on the first non-comment line of `page.txt` beside `main.py` or the packaged EXE:
   ```text
   http://your-server.example/index.php
   ```
2. Set `LTU_PROXY_URL` to the same endpoint; this environment variable has highest priority.
3. Set the proxy URL in the Qt Settings dialog or in `colors.json` under `proxy.url`.

Resolution order is `LTU_PROXY_URL` → saved user setting → `page.txt` → `colors.json` (`proxy.url`). `page.txt` is gitignored and is not part of the source repository. `LTU_PROXY_TOKEN` or the saved proxy token is used for authenticated features; ordinary public traffic pulls do not need a token. Configure the collector and PHP with the same `LTU_TOKEN` for protected endpoints and the console.

## Operational Notes and Troubleshooting

- **HTTP 401 from collector actions:** compare `LTU_TOKEN` in PHP with `token` in `ltu_collector.ini`; they must match exactly. Re-run the token application step after rotating it.
- **No active collection:** the collector intentionally pauses without a fresh `?action=traffic` demand. Check that clients use your URL and that demand polling is succeeding; a demand area expires after 20 seconds.
- **PHP write errors:** ensure the web process can write the proxy state directory and its temp directory. Internal JSON and compressed files should not be exposed as static downloads.
- **OpenSky unavailable or rate-limited:** confirm the local JSON credential file, allow the collector to refresh its OAuth token, and keep `opensky_quota.txt`; 429 backoff may delay the next fetch.
- **No mesh when the website is down:** peer election and relay both run through the PHP host, so a host outage prevents mesh service. Direct client fallback remains available.
- **Collector diagnostics:** a direct double-click prints to its console. The initial and changed demand polls report HTTP/WinHTTP status plus received and allowlisted region counts; errors and state changes are printed immediately. The PowerShell watchdog captures this output in `collector-*.stdout.log` / `collector-*.stderr.log`, writes `logs/watchdog.log`, and restarts the process after a failure.

See the repository [publishing checklist](PUBLISHING-CHECKLIST.md) before publishing a package. The project-owned code is under [AGPL-3.0-only](../LICENSE); bundled third-party components, data, services, and maps retain their own terms.
