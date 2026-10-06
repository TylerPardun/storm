# STORM
**Severe Thunderstorm Observation and Reconnaissance Monitor**

A standalone desktop application for storm chasing situational awareness. Runs on a laptop in the field and is designed for **low bandwidth environments** — offline map tiles, compressed radar data, and efficient MQTT messaging. → **[User Guide](STORM_USER_GUIDE.md)** for full installation, features, and usage reference.

---

## Table of Contents

- [Current Features](#current-features)
- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Clone the repo](#1-clone-the-repo)
  - [2. Run the setup script](#2-run-the-setup-script)
  - [3. Download the map tiles](#3-download-the-map-tiles)
  - [4. Run](#4-run)
  - [5. Place your AWS IoT credentials](#5-place-your-aws-iot-credentials)
  - [6. Platform-specific launchers](#6-platform-specific-launchers-optional)
- [Updating](#updating)
- [Project Structure](#project-structure)
- [Architecture Notes](#architecture-notes)
- [Radar Site Coverage](#radar-site-coverage)

---

## Current Features

- **Offline vector map** — OpenStreetMap tiles served entirely in-process via a custom `storm://` URL scheme; no internet required for the base map
- **Optional offline base layers** — user-accessible MAP drawer toggles for NLCD land cover and USGS satellite basemap imagery when the optional MBTiles caches are present
- **NEXRAD radar overlay** — fetches Level 3 super‑res reflectivity (N0B fallback to N0Q/N0R) and velocity from Unidata THREDDS (~50–300 KB per scan); re-projects polar data to lat/lon and renders as a transparent PNG overlay on the map; adjustable playback speed (0.5×–3×)
- **Satellite overlay** — GOES‑East CONUS and MESO imagery with time‑step playback (backfills up to 10 recent frames on selection); adjustable playback speed (0.5×–3×)
- **SPC/NWS hazards** — Day 1 outlook polygons, SPC watches/MDs, and NWS warnings with map tooltips + click‑through discussion text
- **Real-time annotations** — place road closure, construction, flooding, downed lines, debris, and storm motion cones on the map; editable and movable after placement; synced over MQTT
- **Drawing tools** — create fronts, custom-styled polylines, and custom-styled polygons on the map; synced over MQTT via DrawingSync
- **Station plot markers** — MetPy-style station plot PNGs rendered at vehicle positions (temperature, dewpoint, pressure, wind barb); synced over MQTT
- **Surface obs** — live station models from OK Mesonet, West Texas Mesonet, Kansas Mesonet, Colorado Mesonet, Nebraska Mesonet, and user-selected ASOS/AWOS bounding boxes; OK/WTM/KS/CO/NE poll every 5 minutes, ASOS reuses or redraws a selected bbox
- **SFCOA mesoanalysis overlays** — NSSL SFCOA vector-tile contours with grouped thermodynamic, parcel, lapse-rate, shear, helicity, composite, upper-air, and wind products; step through recent valid times and overlay one or more variables on the map
- **Point soundings** — click any map location to fetch a live Skew-T log-P sounding; three sources: HRRR model (open-meteo, F0–F3 scrubber), observed radiosondes (IEM RAOB), and NSSL CLAMPS DL truck data; shows parcel parameters (including 0–3 km CAPE), kinematics table, surface T/Td labels, and hodograph
- **Turn-by-turn routing** — on-map directions via OSRM with Nominatim geocoding; address, lat/lon, or map-click origin/destination; auto-re-routes when off-course
- **Multi-mode launch** — VEHICLE (full obs publish), MONITOR (view-only, no local publish), VIEWER (no MQTT, no obs), or ARCHIVE (replay a past session) — all modes selected exclusively through the launch dialog with passphrase authentication
- **Archive mode** — replay any past session at a chosen UTC date/time; synchronized playback of NEXRAD Level 2/3 radar, GOES satellite, SPC/NWS hazards, soundings, and MQTT vehicle positions; central time controller with play/pause, 1×–300× speed multipliers, ←/→ 30-second step buttons, and a timeline scrubber
- **Vehicle timeseries** — interactive time-series plots of meteorological observations (temperature, dewpoint, wind speed/direction, pressure) for any tracked vehicle; works in both live and archive modes; features scroll-wheel zoom, click-drag selection zoom with visual highlight, double-click to reset zoom, and inline cursor readouts below each subplot with 10-second grid snapping

### What's New in 1.5.0

- Kansas Mesonet surface observations are now available in the SURFACE drawer and launch dialog
- KS Mesonet station models use the NSSL API-hosted mesonet feed with embedded metadata
- Surface obs diagnostics and layer ordering now treat KS and CO Mesonet as independent sources

---

## Requirements

- Python 3.11 via conda (Miniforge, Miniconda, or Anaconda)
- conda environment: `storm`
- macOS or Windows

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
```

> **Important:** You must clone the repo — do not download the ZIP from GitHub. STORM's in-app updater relies on `git pull` and will not work without a `.git` directory.

### 2. Run the setup script

The setup script creates the `storm` conda environment and places a launch shortcut on your Desktop — all in one step. Works on macOS, Linux, and Windows.

**Prerequisites:** Python 3.8+ and (optionally) conda. If you don't have conda, the script will offer to install Miniforge for you, or you can install it yourself from [Miniforge](https://github.com/conda-forge/miniforge#download).

```bash
python setup.py
```

> If you prefer to set up manually, see steps 2a–2b below.

<details>
<summary>Manual setup</summary>

**2a. Create the conda environment**

```bash
conda env create -f envs/storm.yml
conda activate storm
```

**2b. Create the Desktop shortcut (optional)**

macOS:
```bash
bash scripts/create_app.sh
```

Linux:
```bash
bash scripts/create_desktop_entry.sh
```

Windows: double-click `scripts\create_app_windows.bat`

</details>

---

## Updating

> **Note:** In-app updates and `git pull` only work if you installed via `git clone`. If you downloaded a ZIP, re-install using the clone instructions above.

Pull the latest code and sync your conda environment using your normal git/conda workflow for this checkout. The repository does not currently include a dedicated update script.

Typical steps:
1. Run `git pull`
2. Run `conda env update --prune -f envs/storm.yml`
3. Rebuild the app bundle or refresh the shortcut only if you use those packaging flows

> If `git pull` fails, it usually means you have local uncommitted changes that conflict. Run `git status` to see what's changed, resolve any conflicts, and re-run the update steps.

---

### 3. Download the map tiles

The MBTiles file is too large for git and is hosted separately. It is needed only to use the map with no internet connection: without `tiles/storm.mbtiles`, STORM loads the same base map from [OpenFreeMap](https://openfreemap.org) (OpenStreetMap data, no account or key) whenever the computer is online.

**[Download tiles/ folder (Google Drive)](https://drive.google.com/drive/folders/1q4DJ-mg94tpDWHLEkQ_50oQ3uauQ77it?usp=sharing)**

Download the files and place them in the `tiles/` folder so the structure is, for example:
```
tiles/storm.mbtiles
tiles/storm_nlcd.mbtiles     # optional NLCD land-cover overlay
tiles/satellite.mbtiles      # optional USGS satellite basemap
```

### 4. Run

```bash
conda activate storm
python main.py
```

**Optional flags:**
```bash
python main.py --debug                          # enable debug logging
python main.py --truck-replay-file path/to.txt  # replay a truck logger file locally
python main.py --render-grid-size 256           # lower radar resolution for slow hardware
```

### 5. Place your AWS IoT credentials

Obtain the four TLS cert files and place them at:
```
aws/storm.pem
aws/storm.pem.crt
aws/storm-private.pem.key
aws/storm-public.pem.key
```
These are distributed out-of-band and are never committed to the repo. Please contact [Bobby Saba](mailto:robert.saba@noaa.gov) for the files.

### 6. Platform-specific launchers (optional)

**macOS:** Create a double-clickable `STORM.app`:
```bash
bash scripts/create_app.sh
```

The app bundle records your project folder location at build time, so it can be moved or copied anywhere — the Dock, `/Applications`, a Desktop alias — and will always launch from the correct location. If you ever move the project folder itself, just re-run `bash scripts/create_app.sh` to update the path.

**Linux:** Create a desktop entry and application menu launcher:
```bash
bash scripts/create_desktop_entry.sh
```

**Windows:** The setup script automatically creates a desktop shortcut via `scripts\create_app_windows.bat`.

---

## Project Structure

```
storm/
├── main.py                  # Entry point
├── config.py                # Constants — cert paths, MQTT settings, defaults
├── runtime_flags.py         # Runtime configuration flags and debug profiles
├── set_password.py          # CLI tool to update launch passphrases
├── storm.icns               # macOS app icon
├── storm.ico                # Windows app icon
├── storm.png                # Linux app icon
├── setup.py                 # Cross-platform one-step setup (macOS/Linux/Windows)
├── VERSION                  # Current version number
├── CHANGELOG.md             # Version history and release notes
├── STORM_USER_GUIDE.md      # User documentation
│
├── envs/                    # Conda environment specs
│   └── storm.yml            # Unified environment (all platforms)
│
├── scripts/                 # Build, check and debugging tools (scripts/README.md says what each is for)
│   ├── create_app.sh        # Builds STORM.app macOS bundle
│   ├── create_desktop_entry.sh  # Creates Linux desktop entry and launcher
│   ├── create_app_windows.bat  # Creates STORM desktop shortcut (Windows)
│   ├── launch_storm.bat     # Activates conda env and launches STORM (Windows)
│   ├── launch_storm.vbs     # Silent launcher for Windows (no console window)
│   └── make_satellite_mbtiles.py # Builds optional USGS satellite basemap cache
│
├── archive/                 # Archive (replay) mode — session config, clock, fetchers
│   ├── thredds_paths.py     # Every THREDDS host, folder and datastream STORM reads (edit here when THREDDS moves)
│   ├── session.py           # ArchiveSession dataclass and the session's time span
│   ├── time_controller.py   # Central archive clock (play/pause, speed, scrubber, signals)
│   └── fetchers/            # Per-layer archive data fetchers (synchronized to clock)
│       ├── radar_archive_fetcher.py     # Fetches historical NEXRAD Level 3 frames
│       ├── satellite_archive_fetcher.py # Fetches historical GOES satellite frames
│       ├── hazard_archive_fetcher.py    # Fetches historical SPC/NWS hazard products
│       ├── sounding_archive_fetcher.py  # Fetches historical sounding data
│       └── mqtt_reader.py               # Replays historical MQTT vehicle positions
│
├── core/                    # Pure data types (no Qt, no I/O)
│   ├── annotation.py        # Annotation dataclass + type registry
│   ├── drawing.py           # Drawing (front/polyline/polygon) dataclass
│   ├── scan_sector.py       # Scan sector dataclass + geometry helpers
│   ├── observation.py       # Meteorological obs record
│   ├── radar_scan.py        # RadarScan dataclass + product metadata (Level 3)
│   ├── level2_radar_scan.py # Level2RadarScan dataclass (extends RadarScan for Level 2)
│   ├── sounding.py          # Sounding + SoundingSet dataclasses; pressure levels
│   ├── storm_cone.py        # StormCone dataclass + GeoJSON builder
│   ├── vad.py               # Velocity-azimuth display data types
│   └── vehicle.py           # Vehicle dataclass
│
├── data/                    # Background I/O and decoding
│   ├── endpoints.py         # Every AWS bucket and web API STORM reads (edit here when one moves)
│   ├── update_checker.py    # Git-based update check at launch
│   ├── asos_stations.json   # ASOS/AWOS station metadata
│   ├── fetchers/            # Network-backed data fetchers
│   │   ├── radar_fetcher.py     # Polls Unidata THREDDS; backfills 12 scans (Level 3)
│   │   ├── satellite_fetcher.py # WMS satellite imagery fetch + cache
│   │   ├── hazard_fetcher.py    # SPC/NWS hazard polygons
│   │   ├── sounding_fetcher.py  # On-demand HRRR point sounding via open-meteo
│   │   ├── obs_sounding_fetcher.py  # Observed radiosonde soundings via IEM RAOB
│   │   ├── clamps_sounding_fetcher.py  # NSSL CLAMPS DL truck soundings via API
│   │   ├── surface_fetcher.py   # OK / WTM / KS / CO / NE Mesonet / ASOS surface obs
│   │   ├── sfcoa_overlay_fetcher.py # SFCOA mesoanalysis vector-tile overlays
│   │   ├── routing_fetcher.py   # OSRM turn-by-turn routing + Nominatim geocoding
│   │   └── vad_fetcher.py       # NEXRAD VAD fetch helpers
│   ├── ingest/              # Local field-data ingest
│   │   ├── obs_file_watcher.py  # Watches FOFS instrument logger file (Track A)
│   │   ├── gps_reader.py        # NMEA via pyserial — auto-detects GPS puck (Track B)
│   │   └── truck_replay.py      # Offline CSV replay for testing
│   ├── radar/
│   │   └── radar_decoder.py     # MetPy Level 3 decode → RadarScan
│   └── stations/
│       └── sounding_stations.py # Radiosonde station metadata (lat/lon lookup)
│
├── network/
│   ├── mqtt_client.py       # Paho-MQTT wrapper (TLS, reconnect, signals)
│   ├── vehicle_sync.py      # Bidirectional vehicle obs sync via storm/vehicles/{id}
│   ├── annotation_sync.py   # Bidirectional annotation MQTT sync
│   ├── drawing_sync.py      # Bidirectional drawing MQTT sync (fronts/polylines/polygons)
│   ├── scan_sector_sync.py  # Bidirectional scan sector MQTT sync
│   └── storm_cone_sync.py   # Bidirectional storm cone MQTT sync
│
├── ui/                      # Qt widgets and embedded map UI
│   ├── theme.py             # QSS dark theme + color constants
│   ├── export_tools.py      # Widget image copy/save helpers
│   ├── app/                 # Main application window and mixins
│   │   ├── main_window.py       # Top-level QMainWindow
│   │   ├── main_window_debug.py # Debug helpers for MainWindow
│   │   └── main_window_map_helpers.py # Map helper mixin for MainWindow
│   ├── launch/              # Pre-launch config dialog modules
│   │   ├── dialog.py            # VEHICLE / MONITOR / VIEWER / ARCHIVE launch dialog
│   │   ├── icons.py             # Launch dialog icon helpers
│   │   ├── styles.py            # Launch dialog styling
│   │   └── update_dialogs.py    # Update and log dialogs shown at launch
│   ├── map/                 # MapLibre/QWebEngine integration
│   │   ├── widget.py            # MapLibre GL map + custom storm:// asset/tile scheme
│   │   ├── bridge.py            # QWebChannel bridge object
│   │   ├── html.py              # Embedded map HTML builder
│   │   ├── map_template.html    # MapLibre HTML template
│   │   ├── radar_overlay.py     # RadarScan → PNG → MapLibre raster layer
│   │   └── tile_scheme_handler.py # QWebEngineUrlSchemeHandler for storm://
│   ├── controls/            # Toolbar drawers and layer controls
│   │   ├── archive_controls.py  # Archive playback controls bar
│   │   ├── deploy_locs_controls.py # Deployment location filter drawer
│   │   ├── hazard_controls.py   # SPC/NWS hazard toggle drawer
│   │   ├── landcover_controls.py # NLCD opacity + legend drawer
│   │   ├── map_controls.py      # MAP drawer for route/measure/base layers
│   │   ├── mesoanalysis_controls.py # SPC mesoanalysis controls
│   │   ├── radar_controls.py    # Radar site/product/playback drawer
│   │   ├── routing_controls.py  # Turn-by-turn routing input / directions drawer
│   │   ├── satellite_controls.py # Satellite mode/playback drawer
│   │   ├── sfcoa_controls.py    # SFCOA mesoanalysis product drawer
│   │   └── surface_controls.py  # Surface obs toggle drawer
│   ├── dialogs/             # Modal and floating dialogs
│   │   ├── annotation_dialog.py # Place / edit annotation dialogs
│   │   ├── archive_loading_dialog.py # Archive prefetch progress dialog
│   │   ├── drawing_dialog.py    # Front/polyline/polygon drawing dialogs
│   │   ├── loading_dialog.py    # Generic loading dialog
│   │   ├── scan_sector_dialog.py # Scan sector input dialog
│   │   ├── storm_cone_dialog.py # Storm motion cone input dialog
│   │   ├── vad_dialog.py        # VAD display dialog
│   │   └── vehicle_timeseries_dialog.py # Vehicle observation timeseries plots
│   ├── layers/              # Rendered map overlay helpers
│   │   ├── station_plot_layer.py # MetPy station plot PNG markers at vehicle positions
│   │   └── surface_plot_layer.py # MetPy station model circles for surface obs
│   ├── sounding/            # Skew-T controls, dialog, parameters, and styling
│   │   ├── controls.py          # Sounding source selector drawer
│   │   ├── dialog.py            # Skew-T log-P dialog
│   │   ├── params.py            # Sounding parameter calculations/formatting
│   │   └── theme.py             # Sounding plot colors and styling
│   └── widgets/             # Reusable floating widgets
│       ├── annotation_tools.py  # Annotation type selector drawer
│       ├── debug_pill.py        # Debug information display pill
│       ├── layer_order_pill.py  # Floating pill for reordering map layer draw order
│       ├── nav_pill.py          # Compact navigation summary pill widget
│       └── outlook_panel.py     # Sliding panel for SPC/NWS discussion text
│
├── static/                  # Bundled offline assets (no CDN)
│   ├── maplibre-gl.js
│   ├── maplibre-gl.css
│   ├── indicator_on.svg     # MQTT connection status indicators
│   ├── indicator_off.svg
│   └── fonts/               # Noto Sans glyph PBFs (Latin ranges)
│
├── locs/
│   └── deployment_locations.csv  # Deployment location database
│
├── tests/                   # Unit tests
│   ├── test_annotation.py
│   ├── test_clamps_parser.py
│   ├── test_clamps_sounding.py
│   ├── test_drawing.py
│   ├── test_observation.py
│   ├── test_routing_controls.py
│   ├── test_runtime_flags.py
│   ├── test_scan_sector.py
│   ├── test_sounding.py
│   ├── test_storm_cone.py
│   ├── test_surface_fetcher.py
│   └── test_vad.py
│
├── tiles/
│   ├── storm.mbtiles        # NOT in git — download separately
│   ├── storm_nlcd.mbtiles   # optional NLCD land-cover raster cache
│   └── satellite.mbtiles    # optional USGS satellite raster cache
│
└── aws/                    # AWS IoT TLS credentials — NOT in git
    ├── storm.pem
    ├── storm.pem.crt
    ├── storm-private.pem.key
    └── storm-public.pem.key
```

---

## Architecture Notes

- **Tile/asset serving** — `StormSchemeHandler` (`ui/map/tile_scheme_handler.py`) registers a custom `storm://` URL scheme that serves the map HTML, MapLibre assets, fonts, vector tiles, optional NLCD raster tiles, and optional satellite-basemap tiles entirely in-process — no Flask server, no TCP port required.
- **Radar pipeline** — `RadarFetcher` (`data/fetchers/radar_fetcher.py`) polls Unidata THREDDS every 2 minutes for NEXRAD Level 3 files. On first fetch it backfills the last 6 scans per product (12 total — reflectivity and velocity). Archive mode supports Level 2 radar via `Level2RadarScan` with multiple elevation tilts and dual-pol products. Data flows: `RadarFetcher` → `decode_nexrad_l3()` (`data/radar/radar_decoder.py`) → `RadarScan` → `RadarOverlay` (`ui/map/radar_overlay.py`) → base64 PNG → MapLibre raster source.
- **Map bridge** — `QWebChannel` connects Python and the MapLibre JS context through `MapBridge` (`ui/map/bridge.py`). Mouse moves, clicks, and feature interactions emit Qt signals. Python calls JS functions (`stormAddVehicle`, `stormAddStormCone`, `stormAddAnnotation`, etc.) via `page().runJavaScript()`.
- **Data paths** — Track A: `ObsFileWatcher` (`data/ingest/obs_file_watcher.py`) reads FOFS instrument logger CSV. Track B: `GPSReader` (`data/ingest/gps_reader.py`) reads NMEA from serial port. Both update the live vehicle state and publish via `VehicleSync`.
- **MQTT** — AWS IoT broker over TLS port 8883. Topic layout: `storm/vehicles/{id}`, `storm/annotations/{id}`, `storm/cones/{id}`, `storm/drawings/{id}`. Messages expire at 08:00 UTC the following day.
- **Vehicle locations** — Live vehicle positions come from MQTT subscriptions on `storm/vehicles/{id}`. Local obs sources publish to that topic, and all connected clients subscribe to the same stream for fleet positions.
- **Radar source** — NEXRAD Level 3 via Unidata THREDDS (public, no auth). N0B (super-res reflectivity) with N0Q/N0R fallbacks; N0U (velocity) with N0S fallback. Archive mode supports Level 2 radar with full dual-pol products.
- **Surface obs** — `SurfaceFetcher` (`data/fetchers/surface_fetcher.py`) polls OK Mesonet, West Texas Mesonet (WTM), Kansas Mesonet, Colorado Mesonet, and Nebraska Mesonet from the NSSL API every 5 minutes and fetches ASOS/AWOS observations from IEM for a user-drawn bounding box. Station model PNGs are rendered via MetPy/matplotlib, served through the in-process `storm://` scheme, and displayed as map markers via `SurfacePlotLayer` (`ui/layers/surface_plot_layer.py`).
- **SFCOA overlays** — `SfcoaOverlayFetcher` (`data/fetchers/sfcoa_overlay_fetcher.py`) reads the NSSL API SFCOA run index and per-run metadata, exposes grouped variables through `SfcoaControls`, and renders selected vector-tile contour products through the MapLibre bridge.
- **Soundings** — Three independent sources: HRRR point soundings via open-meteo API (`SoundingFetcher`), observed radiosondes via IEM RAOB (`ObsSoundingFetcher`), and NSSL CLAMPS DL truck soundings via the NSSL API (`ClampsSoundingFetcher`) in `data/fetchers/`. Sounding UI lives in `ui/sounding/`.
- **Testing many dates** — `python scripts/check_archive_days.py --preset` (or dates) opens archive sessions one at a time in separate processes, steps each through its active hours, exercises radar, satellite, lidar, NOXP and the Video Studio, and writes `case_data/day_checks/<run>/report.md` with screenshots. It respects the machine (memory watchdog) and THREDDS (one date at a time, rests between). Don't run it with STORM open.
- **Data sources in two files** — `archive/thredds_paths.py` holds every THREDDS host, folder and datastream STORM reads (FOFS mobile mesonet and recordings, CLAMPS/lidar-truck winds, TROPoe, surface met, sondes, raw lidar, CopterSonde, NOXP, UCAR NEXRAD Level 3); `data/endpoints.py` holds the AWS buckets (NEXRAD Level 2, GOES) and every web API (NSSL API paths, IEM, SPC, NWS map services, damage surveys, open-meteo, mesonets, nowCOAST, routing, tiles). Every fetcher builds its URLs from them, and a test fails if an address is written anywhere else. `python scripts/check_data_sources.py` checks that each one still answers and, for a THREDDS folder that doesn't, lists what its parent now holds. THREDDS is fragile under load: the check asks one folder at a time, 1.5 s apart, and stops if THREDDS goes quiet.
- **Routing** — `RoutingFetcher` (`data/fetchers/routing_fetcher.py`) geocodes addresses with Nominatim and fetches turn-by-turn directions from the public OSRM demo server. Auto-re-routing triggers when the vehicle drifts >100 m off-route for 3+ consecutive GPS fixes.

---

## Radar Site Coverage

The radar site selector covers the central and northern Great Plains:
Oklahoma, Kansas, Nebraska, South Dakota, North Dakota, Texas (panhandle and north), Colorado, Wyoming, Missouri, Iowa, and Arkansas.

The dropdown automatically sorts by distance from your configured home location and shows the 5 nearest sites. Any NEXRAD site can be entered manually via the **OTHER...** option.

---
