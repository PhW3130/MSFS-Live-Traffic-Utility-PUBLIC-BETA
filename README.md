# MSFS Live Traffic Utility

**Documentation:** [Deutsch](readme.de.md) · [Complete project documentation (PDF)](Dokumentation/Projekt-Dokumentation.pdf)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green)](https://doc.qt.io/qtforpython-6/)
[![AGPL-3.0-only](https://img.shields.io/badge/License-AGPL--3.0--only-orange)](LICENSE)

A real-time ADS-B map with optional AI-traffic injection into Microsoft Flight Simulator via SimConnect. Ready-to-use clients are designed to use the central Community Proxy and its automatic peer-mesh failover; end users do not need to configure a proxy.

![GUI preview](docs/gui_preview.svg)

## Table of Contents

- [How the Network Works](#how-the-network-works)
- [Features](#features)
- [Planned Features and Roadmap](#planned-features-and-roadmap)
- [Requirements and Installation](#requirements-and-installation)
- [Quick Start and GUI Usage](#quick-start-and-gui-usage)
- [Configuration](#configuration)
- [Self-Hosting Your Own Proxy (Open Source)](#self-hosting-your-own-proxy-open-source)
- [Architecture](#architecture)
- [Testing](#testing)
- [Data Sources, License and Disclaimer](#data-sources-license-and-disclaimer)
- [Contributing](#contributing)

## How the Network Works

By default, every client requests traffic through the central Community Proxy, reducing repeated upstream requests and providing shared one-hour aircraft history and live service status. The [public live-status page](http://dnd.59-seconds.com/ltu/index.php) requires no token and shows current data-source, region, and client availability. If collector data is stale or at least half of fresh client heartbeats report the proxy unavailable or rate-limited (HTTP 429), the website elects an eligible healthy client, when available, as a temporary relay; traffic flows through the website, with no open ports or router configuration. Only if that web relay is unavailable does each client use direct API fallbacks, in order: **adsb.lol → adsb.fi → OpenSky Network**. If all direct sources fail, the client's last cached traffic snapshot may be shown.

## Features

### Map and Visualization

- Dark-themed Qt desktop interface with a tabbed Settings dialog, built with PySide6, QtWebEngine, QtWebChannel, and Leaflet.
- Live aircraft map and table, searchable by callsign, ICAO hex, type, or registration; additional airline and type filters are available. Other filters include minimum altitude, ground-traffic visibility, labels, and follow mode.
- Rotated SVG aircraft silhouettes for airliners and helicopters, altitude coloring, ground dimming, and emergency highlighting for squawk 7500/7600/7700 or ADS-B emergency state.
- Select an aircraft to inspect airline, category, route, navigation data, and other available details. Route lookup uses adsbdb and can show a great-circle path plus origin/destination markers.
- Incremental, length-capped, altitude-gradient trails for the selected aircraft and all visible aircraft. The proxy samples collector/relay pushes and fresh regional responses into one-hour flight history, resolvable by hex or callsign so a selected trail can load recent points asynchronously.
- Search-and-jump, nearest-aircraft selection, Excel-friendly semicolon-delimited CSV export, and emergency toast/sound alerts for visible-map traffic without duplicate alarms.
- OpenStreetMap, dark CARTO, OpenTopoMap, or a custom tile server. Custom tile URLs must include `{z}`, `{x}`, and `{y}` and display the provider's required attribution.

### MSFS Integration and TCAS

- The map starts and remains usable without MSFS or SimConnect. AI injection starts automatically when a simulator is detected by default, can be toggled independently from the traffic display, and runs alongside the map.
- SimConnect reports the player's position for follow and TCAS mode; the own aircraft is shown on the map, which TCAS centers on the player. For selected traffic it shows relative bearing/clock position, distance, altitude difference, and climb/descent trend.
- Experimental AI traffic injection matches live traffic to compatible local aircraft models. FSLTL base models are recommended but are not included; AI injection and model matching are still evolving.
- On-ground parked-aircraft fillers are synthetic local scenery, not live flights. They are best-effort and stop when SimConnect rejects a spawn or the configured safety cap is reached; a rejected request does not prove every stand is occupied.
- The default parked-filler cap is 50 aircraft and the default model is an installed A20N title. Simulator discovery and legacy `aircraft.cfg` title matching are described below.

### Resilience, Mesh, and Usability

- The web-relay peer mesh automatically elects one client when the proxy is down or rate-limited, then elects the next candidate if that relay fails. It needs no inbound ports.
- Status indicators cover the proxy, adsb.lol, adsb.fi, OpenSky, mesh role, and MSFS connection: green means healthy, orange means HTTP 429, red means down, and gray means idle. Public service status is also shown on the web map.
- QSettings remembers window geometry, map position/style, filters, appearance, network preferences, and MSFS options. Settings identify previous defaults and can reload them while preserving saved proxy credentials, mesh name, and local simulator paths.
- The animated startup splash advances through real loading stages, including the `aircraft.cfg` scan. Per-launch timestamped diagnostics in `logs/` cover UI actions, settings, source requests/status changes, and MSFS/mesh events, with a user-data fallback if the application directory is not writable.
- The interface detects German or English from the operating-system locale. The engine in `traffic_core.py` remains usable and testable without Qt.

## Planned Features and Roadmap

- PlaneTrack.ai is planned as an optional fourth data source in a future version; it is not part of the current three-source chain.
- Modular livery resolution for MSFS 2024 SimObjects is planned. Current matching uses legacy `aircraft.cfg` titles; SimConnect EX1 receives the full title with an empty livery field, selecting the default livery.

## Requirements and Installation

**Windows 10/11** is required for the packaged application. Map mode can also run from source on Linux or macOS. Python 3.10+ and an internet connection are required when running from source. AI traffic injection additionally needs MSFS 2020 or 2024, a usable SimConnect DLL/connection, and compatible aircraft models; SimConnect is optional for map-only use.

### Option A: Windows Release (Ready to Use)

1. Download the latest ZIP from [GitHub Releases](https://github.com/PhW3130/Live-Traffic/releases).
2. Extract the archive to a writable folder.
3. Start `MSFS Live Traffic Utility.exe`. The release uses the Community Proxy by default; no network setup is needed.

### Option B: Run from Source

```bash
git clone https://github.com/PhW3130/Live-Traffic.git
cd Live-Traffic
python -m pip install -r requirements.txt
python main.py
```

To build a standalone Windows application for another PC (no Python is needed on the target PC), run `build_exe.bat`; the bundle is created under `dist/MSFS Live Traffic Utility/`. A pushed `v*` tag triggers the Windows release workflow, which attaches `MSFS-Live-Traffic-Utility-win64.zip` to a GitHub Release; pull requests validate the Windows build. The full proxy-stack build and deployment process is in [Self-Hosting Your Own Proxy](docs/SELF_HOSTING_PROXY.md).

## Quick Start and GUI Usage

- The live map opens in all-aircraft mode. Enter a flight number and select **Start** to track it; clear the field or choose **All aircraft** to return to the full view.
- Select an aircraft by clicking its map icon or table row. Press `Esc` to clear the selection; double-click a row to follow and zoom to the aircraft.
- Use `+` and `-` to zoom. Right-click the map to select the nearest aircraft.
- Search filters the map and table as you type; press `Enter` to select and jump to the first match. The toolbar also includes airline/type filters, all-aircraft trails, **TCAS (MSFS)**, emergency alerts, and CSV export.
- When MSFS is detected, toggle **MSFS AI traffic** without stopping the live map. If the app starts before MSFS, the SimConnect connection probe retries automatically.
- Open **Settings** for map style, appearance, filters, network, and simulator options. Changes apply immediately where possible; changing the Community folder or SimConnect DLL requires an app restart.

The app scans simulator `UserCfg.opt` files for MSFS Community folders. If multiple installations are found, it prompts for one; if none is detected, it offers a folder browser for the Community or parent Packages directory. Aircraft titles are indexed from the selected Community folder's `aircraft.cfg` files for AI model matching. The current FSLTL/VMR title matcher uses legacy aircraft titles; MSFS 2024 modular livery selection is not implemented yet.

When the player is on the ground, the filler can use `MSFS_AIRPORT_ICAO`, then SimConnect's current-airport SimVars, then live-traffic inference to identify the airport. SimConnect does not report a dependable free-stand count, so filler spawning is intentionally best-effort.

## Configuration

Per-user `QSettings` stores map center/zoom/style, custom tiles, theme and altitude-gradient colors, icon sizes, traffic filters and refresh interval, proxy/mesh preferences, and MSFS options. `colors.json` supplies developer/distributor defaults for UI, label, emergency and accent colors, altitude gradient, aircraft icons, map, proxy, and peer mesh. The Settings dialog labels previous defaults; **Load defaults** preserves saved proxy credentials, the mesh name, and local MSFS paths.

- Proxy URL priority is `LTU_PROXY_URL` → a saved user setting → `page.txt` → `colors.json`'s `proxy.url`. `page.txt` is gitignored and can be placed beside the source script or packaged executable; its first non-comment line is the URL. `page.example.txt` is a template.
- `LTU_PROXY_TOKEN` overrides the saved proxy token. The Community Proxy is preconfigured in ready-to-use builds; custom proxy setup is covered by the self-hosting guide.
- `LTU_MESH=off` disables the peer mesh. Mesh enablement and the optional display name are also stored in `colors.json` under `peer_net` or in user settings.
- `MSFS_COMMUNITY_PATH` overrides Community-folder discovery; `MSFS_PACKAGES_PATH` can select a Packages directory. `MSFS_SIMCONNECT_DLL` selects a local SimConnect DLL.
- `MSFS_AIRPORT_ICAO` overrides parked-filler airport detection. `MSFS_MAX_PARKED_FILLERS` sets the safety cap (default `50`); `MSFS_PARKED_FILLER_MODEL` overrides the default installed A20N title.
- A custom map tile URL must use `{z}`, `{x}`, and `{y}` and include correct attribution.

## Self-Hosting Your Own Proxy (Open Source)

The complete stack—Qt desktop client, PHP web relay, and C++ collector—is open source and can be built and operated on your own infrastructure for a group or independent deployment. The guide covers the `rea/` distribution, collector and PHP setup, OpenSky credentials, quotas, watchdog, admin console, status page, and web-relay mesh: **[Read the self-hosting guide](docs/SELF_HOSTING_PROXY.md).**

## Architecture

| Module | Purpose |
|---|---|
| `main.py` | PySide6 main window, toolbar, aircraft table, detail panel, QWebEngineView, and Qt bridge. |
| `settings_dialog.py` | Tabbed settings UI; `MainWindow` applies and persists preferences. |
| `traffic_core.py` | UI-independent traffic engine: ADS-B polling, filters, search, selection, trails, routes, and state snapshots. |
| `msfs_ai.py` | SimConnect position updates, aircraft spawning, parked fillers, and the AI traffic loop. |
| `startup_splash.py` | Animated startup screen and loading-stage indicator. |
| `aircraft_config.py` | MSFS Community-folder discovery and `aircraft.cfg` title catalog. |
| `gui_web/` | Leaflet map, aircraft SVGs, and JavaScript frontend loaded inside QtWebEngine. |
| `model_map.py`, `modelmap.txt` | Aircraft-to-model matching rules and title catalog. |
| `routes.py` | adsbdb route lookup and airport-proximity helpers. |
| `peer_net.py` | Client heartbeat, server-elected peer relay, failover, and mesh status. |
| `proxy/` | Optional self-hosted PHP relay (`index.php`, `client_demand.php`, `aircraft_tracks.php`) and C++ collector (`ltu_collector.cpp`). |

Python sends map state to JavaScript through Qt signals (`stateUpdated`, `settingsUpdated`). JavaScript calls Python Qt slots on the `bridge` object (`aircraftClicked`, `mapContextMenu`, `mapMoved`, `uiReady`).

## Testing

The headless tests do not require a display, a live simulator, or Qt libraries. Install `pytest` and `requests` in addition to `requirements.txt` where needed.

```bash
python -m py_compile main.py settings_dialog.py startup_splash.py aircraft_config.py traffic_core.py routes.py msfs_ai.py model_map.py peer_net.py
python tests/test_traffic_core.py
python tests/test_aircraft_history.py
python tests/test_peer_net.py
python tests/test_msfs_ai.py
python tests/mock_qt_test.py
python -m pytest -q tests/test_aircraft_config.py tests/test_model_map.py
```

Compile and run the C++ helper tests with a C++11 compiler:

```bash
for test in opensky_auth client_demand adsb_sources collector_reliability; do
  g++ -std=c++11 -Wall -Wextra -pedantic "tests/test_${test}.cpp" -o "/tmp/test_${test}" && "/tmp/test_${test}"
done
```

Run the PHP syntax checks and proxy tests with PHP 8.2 or newer:

```bash
for file in proxy/index.php proxy/client_demand.php proxy/aircraft_tracks.php; do php -l "$file"; done
php tests/test_client_demand.php
php tests/test_opensky_proxy.php
php tests/test_aircraft_tracks.php
```

GitHub Actions runs the Python, PHP, C++, and Qt-wiring checks on pushes and pull requests. The Windows distribution workflow also validates the collector build.

## Data Sources, License and Disclaimer

The current application uses only these three ADS-B sources:

- **adsb.lol** — free regional requests through `/v2/lat/{lat}/lon/{lon}/dist/{dist}`; the operator documents dynamic, load-dependent limits rather than a fixed quota. The [API documentation](https://github.com/adsblol/api) describes the routes and responses; 4xx responses indicate an invalid request, and an API-key requirement may be introduced in the future.
- **adsb.fi Open Data** — regional fallback via the official v3 route `/api/v3/lat/{lat}/lon/{lon}/dist/{dist}` (distance in nautical miles, capped at 250 NM). Public requests are limited to one per second and the service is for personal, non-commercial use with attribution. See the [API documentation](https://github.com/adsbfi/opendata).
- **OpenSky Network** — OAuth2 credentials are used only by the collector; direct client fallback is anonymous and rate-limited. OpenSky's live API is restricted to research and non-commercial use. See the [REST API documentation](https://openskynetwork.github.io/opensky-api/rest.html).

No paid aviation-data feeds such as ADS-B Exchange or FlightAware are integrated. Provider quotas, terms, and regional availability apply; the application must not be used to bypass them. The collector's OpenSky configuration and persistent quota ledger are documented in the [self-hosting guide](docs/SELF_HOSTING_PROXY.md). The desktop's anonymous OpenSky fallback is limited to a 250 km radius and at most one request every 15 minutes per process (up to 96 requests/384 credits in 24 hours at four credits each); its timer resets on restart and does not coordinate users behind a shared IP.

Leaflet 1.9.4 is distributed under the BSD-2-Clause license; its text is included at [`gui_web/leaflet/LICENSE`](gui_web/leaflet/LICENSE). OpenStreetMap tiles credit © OpenStreetMap contributors ([copyright](https://www.openstreetmap.org/copyright)); CARTO tiles credit © OpenStreetMap contributors and © CARTO ([attribution](https://carto.com/attributions)); OpenTopoMap uses OpenStreetMap/SRTM data and its map style is [CC-BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Follow each tile provider's terms and attribution requirements. Third-party assets, data, and services remain under their own licenses; see [`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt) and the [publishing checklist](docs/PUBLISHING-CHECKLIST.md). FSLTL aircraft models are not included.

Copyright (C) 2026 PhW3130. Project-authored code may be used, modified, and redistributed under the **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**; see [`LICENSE`](LICENSE). If you modify the program and let users interact with your modified version remotely over a network, AGPLv3 section 13 requires you to offer those users the corresponding source code. This does not change the license accompanying earlier copies.

**Disclaimer:** This project is not affiliated with or endorsed by Microsoft, Asobo Studio, Flightsim.to, FSLTL, ADS-B Exchange, adsb.lol, or any other third-party organization. Microsoft Flight Simulator and related trademarks belong to their respective owners. The application is for flight simulation and information only; it is not an air-navigation, dispatch, or air-traffic-control tool. It is provided without warranty, subject to the applicable license and third-party terms.

## Contributing

The project is actively developed and remains experimental, especially its AI-traffic injection and model matching. Contributions, bug reports, aircraft-mapping updates, release testing, and feature suggestions are welcome. Please open an issue or pull request in the [GitHub repository](https://github.com/PhW3130/MSFS-Live-Traffic-Utility-PUBLIC-BETA). See the [publishing checklist](docs/PUBLISHING-CHECKLIST.md) before redistributing the packaged application; rights for all bundled third-party material must be documented.
