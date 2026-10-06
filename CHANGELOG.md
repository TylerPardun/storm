# Changelog

All notable changes to STORM will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## Unreleased
### Added
- **Archive tools for every user.** Archive mode and its tools no longer require admin mode.
- **Choosing a case.** A calendar button and year grid next to the archive date; a *Browse available cases* panel that finds dates with data in the background (newest first) and shades the calendar; *Surprise me* picks a random date with data; *Open case…* opens a case package straight from the launch window.
- **Sessions past 00Z.** An archive session runs from the chosen day until activity ends (at most 06Z the next day), so evening cases play through midnight.
- **Mobile mesonet (FOFS)** one-second observations from the THREDDS catalog, rebuilt around the published files' defects (including corrupted `gps_date` values).
- **CLAMPS**: wind profiles (in the VAD viewer), TROPoe thermodynamic profiles and mobile sondes (in the sounding viewer), and trailer surface observations (met tower first, then the MWR's surface fields).
- **NOXP mobile radar** in the radar picker, and **PERiLS CopterSonde** profiles in the sounding viewer.
- **LIDAR**: the lidar truck and CLAMPS trailers' Doppler lidars, shown like radar. Pick a location on the map like a radar station; its PPI scans (PPI files and CSM sector sweeps) are drawn on the map as the clock reaches them, with scanning periods marked under the time slider and a one-row panel (lidar, field, radar on/off). STORM reads only the PPI and CSM files; vertical stares and RHIs are not supported. Truck scans are rotated by the recorded truck heading (estimated from the truck compass, and flagged with ⚠, when missing).
- **ASOS** observations replaying on the archive clock (draw a box to choose stations), and **damage paths** (DAT/NCEI surveys, from MESO-VIEW) with hover details.
- **Storm tracks (TRACK)**: place, drag and delete points one per radar frame, undo/redo, a reference marker, a table of exact times and positions, storm motion from the track, and MESO-VIEW-compatible CSV/Excel files. Tracks are kept in named local workspaces under `data/storm_tracks/`; MESO-VIEW's 109 tracks ship in *Pardun_Tracks*, and TRACK opens the date's saved track automatically.
- **Radar velocity options**: optional dealiasing and storm-relative velocity from the track's motion.
- **Observation trails (TRAILS)**: each platform's recent path colored by a measured or derived quantity (θ, θv, θe, θw, mixing ratio, u/v, storm-relative, radial and tangential wind), wind barbs (optionally storm-relative) and time-to-space display. The sounding viewer shows the observed storm motion from the track.
- **Case packages** (*File > Export / Open Case Package*): tracks, case settings, the provenance (URL and SHA-256) of every source file and, optionally, the data itself, limited to a chosen UTC time frame and including scans the catalogs list for it, so a case opens offline. Saved in `data/case_packages/<date>/`.
- **Menu bar**: File (cases, map screenshot, change day), Edit (undo/redo), View (error log, debug panel) and Help (user guide, About STORM).
- **Archive day checker** (`scripts/check_archive_days.py`): opens archive sessions for many dates, one process at a time with a memory watchdog and rests for THREDDS, uses each like a user (clock through the active hours, radar product switch, satellite, lidar, NOXP, a short movie) and writes a report with screenshots. An 11-date spread (2009-2026) now passes on every date.
- Fixed, found by the day checker: archive satellite never showed imagery (GOES band 13 kelvins were drawn as reflectance, all white); it now draws infrared directly on the map's projection (+150 MB, 0.5 s a frame instead of +750 MB, 2.5 s through cartopy) and only while SATELLITE is on, with "none" (not a red error) before 2017. Switching to NOXP showed the volume loaded at startup instead of the one at the clock, and the archive bar kept the WSR-88D label. 2017-2020 CLAMPS lidar files (range labeled "km AGL") didn't load. A throttled IEM reply hid mesoscale discussions for the rest of a session.
- Archive memory: radar full-resolution images built in strips (identical result, a fraction of the memory); decoded scans no longer pile up across clock jumps; one radar volume parsed and decoded at a time; the parsed volume on screen (465-840 MB) is let go after 45 s unused; the old station's volumes are freed on a station switch; lidar fields kept in float32, scan-less (wind-profile) lidar files not kept, and the truck's GPS track reused instead of downloaded again; the lidar survey waits for startup to settle. Peak memory over the 11 dates went from 2.5-2.8 GB to 2.0-2.3 GB.
- **Data sources in two files**: `archive/thredds_paths.py` (every THREDDS folder and datastream) and `data/endpoints.py` (AWS buckets and web APIs); all fetchers build their URLs from them (URLs unchanged). `scripts/check_data_sources.py` checks every source still answers, gently for THREDDS, and shows where a missing THREDDS folder went. It found the NSSL API (`api.nssl.noaa.gov`, live mesonets, SFCOA, CLAMPS soundings) answering 404 with an invalid certificate.
- Movie captions show UTC only; the "Case package opened" notice is one short line (tracks imported, the span the package holds); studio and export-dialog wording trimmed.
- **Video Studio speed and radars**: new moves play at 300× (about a radar scan a second; was 60×); ½× / 2× speed and a movie-length box retime the whole movie in proportion; the keyframe box shows how long each radar scan stays on screen. Each keyframe records its radar (station, product, tilt), so a movie can switch radars or products partway; scrubbing, preview and export follow it.
- **Video Studio clock steps**: a movie's case time can change every minute (the default), every 5 minutes or each radar scan, like a radar loop, instead of every frame; repeated frames aren't redrawn, so exports with a still camera are much faster. The keyframe box is simplified: case time, Retake from clock + map, then the speed (presets or typed; Use for all), camera motion and pause for the move to the next keyframe.
- Fixed: a finished video export canceled itself (deleting the movie) and left its progress window stuck on “Canceling…”, and Cancel never closed it; the same affected case-package exports. Export Case Package took minutes to open after a long session (each listed scan re-checked every other one). Movie frames could show the previous radar scan (the archive radar was never waited for). Scrolling over a studio field changed it without clicking in (the clock could jump to another hour).
- Map scale bar restyled so its label sits inside it; the archive bar's timeline has a tick and label every hour.
- **Video Studio** (File > Video Studio, Cmd+Shift+V; Esc or ✕ Close Studio to leave): keyframed movies of a case, laid out like a video editor. A case-clock strip over the whole session (zoomable, scroll for radar scans, type a time); a movie timeline in video seconds with clips per move (case span, speed, easing), holds, draggable keyframes (ripple, Alt for one), a scrubbing playhead and right-click actions; an inspector for case time, view, hold, length / speed and easing; undo / redo; live preview with loop; exact frame-by-frame export to MP4 (H.264, Qt's own encoder), GIF or PNG frames at map size, 720p, 1080p or 4K (rendered natively), with time, data line and legend captions; Save Frame; projects saved and reopened (older project files still open). Archive screenshots capture the map alone once it has finished drawing.
- **Online base map**: without `tiles/storm.mbtiles`, the map loads from OpenFreeMap whenever the computer is online, and says so on the map (retrying) when it isn't.
- **Replay of annotations, storm cones and drawings** as they stood at the playback time; *MAP > ANNOTATIONS* shows or hides them.
- Colorado Mesonet surface observations are now available from the SURFACE drawer and launch dialog alongside OK Mesonet, WTM, KS Mesonet, and ASOS.
- CO Mesonet station plots use the NSSL API-hosted `co_mesonet.json` feed with station positions and names from `co_metadata.json`.
- Nebraska Mesonet surface observations are now available to all users from the SURFACE drawer and launch dialog.
- NE Mesonet station plots use the NSSL API-hosted `ne_mesonet.json` feed with embedded station metadata.

### Changed
- Surface observation diagnostics and layer ordering now include CO and NE Mesonet as independent 5-minute mesonet sources.
- The blocking archive-loading window is replaced by a non-blocking indicator; after a seek, the wanted radar scan loads first.
- Archive time bar: ±10 s steps (±1 min without one-second observations), previous/next radar scan, play; EXIT sits at the map's top right; case packages moved to the File menu.
- Hover readouts share one compact look, and self-explanatory controls no longer show one.
- Trail colors avoid the road colors (violet scale; blue–gray–magenta for signed winds).
- Every archive download goes through one step that uses an opened case package's copy first and records provenance.
- Network requests try IPv4 before IPv6 (an unreachable IPv6 route had added ~6 s to each request); transient NSSL failures are retried with backoff.
- Log lines carry the UTC date and time, a per-run session ID and the open case.
- American spelling throughout the interface and documentation.
- `envs/storm.yml` adds `openpyxl` (Excel storm-track files).

### Fixed
- WSR-88D archive scans occasionally drawn rotated; split-cut velocity volumes; the radar image's time now shows the scan's own acquisition time.
- A native crash when an archive window closed during startup.
- Pre-2020 SPC outlooks (shapefile fallback, reprojection and smoothing).
- CLAMPS surface temperatures stored in kelvin under a °C label (shown as ~290–305 °C); CLAMPS MWR files missed by a fixed filename guess.
- NWS CWA boundaries in archive mode; live mode finishing startup; ASOS and damage-path box drawing losing its crosshair.
- Tooltip panels much larger than their text on macOS.
- `scripts/create_app.sh` failing to compile its launcher with current clang.

---

## [1.5.0] - 2026-05-07
### Added
- Kansas Mesonet surface observations are now available from the SURFACE drawer and launch dialog alongside OK Mesonet, WTM, and ASOS.
- KS Mesonet station plots use the NSSL API-hosted `ks_mesonet.json` feed with embedded station metadata.

### Changed
- Surface observation diagnostics, layer ordering, and station-model rendering now include KS Mesonet as an independent 5-minute mesonet source.

---

## [1.4.0] - 2026-05-07
### Changed
- Migrated NSSL-hosted CLAMPS soundings, surface mesonet feeds, annotation snapshots, archive annotation logs, and SFCOA overlays to authenticated NSSL API endpoints.
- SFCOA now discovers available runs from the API index and product metadata from each run's `metadata.json`.
- NSSL CLAMPS soundings now use the API directly in live and archive modes without THREDDS fallback.

### Removed
- Removed the HRRR map overlay product and its UI controls.
- Removed legacy NSSL THREDDS fallback code for CLAMPS soundings.

## [1.3.0] - 2026-04-28
### Added
- MAP drawer now exposes optional offline NLCD land-cover and USGS satellite-basemap toggles to normal users when `tiles/storm_nlcd.mbtiles` and `tiles/satellite.mbtiles` are present.
- Land-cover controls include an opacity slider and NLCD class legend, with hover labels for land-cover classes on the map.
- Added `scripts/make_satellite_mbtiles.py` to build the optional USGS satellite basemap cache.
- Added high- and low-pressure annotation markers with a pressure selector in the placement dialog.

### Changed
- NLCD land cover and satellite basemap no longer require admin mode.
- Routing and measurement controls now live under the MAP drawer with the optional base-layer controls.
- Road labels and road hover readouts are available at lower zoom levels and prefer route/reference shields when available.

## [1.2.0] - 2026-04-24
### Added
- SFCOA mesoanalysis overlays are now available in normal user sessions, with valid-time stepping, refresh controls, grouped variable buttons, and MapLibre vector-tile rendering.

### Changed
- SFCOA no longer requires admin mode; the SFCOA toolbar drawer appears alongside the other standard map layers.

---

## [1.1.0] - 2026-04-16
### Added
- ASOS/AWOS surface observations — draw a map bounding box to fetch IEM ASOS current observations, then render them as full station plots alongside OK Mesonet and West Texas Mesonet data
- ASOS bbox reuse and redraw workflow — toggling ASOS back on reuses the last domain; the surface status text includes a `new box` link to select a replacement domain
- Surface station freshness coloring — surface station plots color their center dot by observation age, with wider freshness thresholds for hourly ASOS reports
- Custom drawing styling — polylines and polygons can be titled and styled with custom color and line style; saved edits sync through the existing drawing workflow
- ASOS station plot transport optimization — station plot PNGs are served through the in-process `storm://` scheme instead of being embedded as base64 payloads

### Changed
- Surface obs drawer now supports OK Mesonet, WTM, and ASOS controls from one place
- ASOS selection uses the application accent color for the click-drag bounding box
- Surface plot caching now includes observation timestamp so station freshness colors update from the observation valid time rather than stale cached plot images
- OK Mesonet and WTM surface plots render and appear as a single batch; ASOS plots are chunked because user-selected domains may contain many stations

### Fixed
- ASOS plot URL decoding for station IDs served via `storm://app/plots/...`
- ASOS bbox draw mode now exits and restores map interaction state immediately after selection
- Stale ASOS render batches are discarded when a new bbox is requested

---

## [1.0.0] - 2026-04-14
### Added
- Vehicle meteorological timeseries dialog — interactive time-series plots of temperature, dewpoint, wind speed/direction, and pressure for any tracked vehicle; scroll-wheel zoom, click-drag selection zoom, double-click to reset, and inline cursor readouts with 10-second grid snapping; works in both live and archive modes
- VAD wind profile — initial support for VAD-derived wind profiles from NEXRAD data
- Layer ordering pill — floating UI control to reorder map layer draw order (radar, satellite, hazards, annotations, drawings) at runtime
- Screenshot capability — save the current map view as an image from the layer pill
- CWA warning display — NWS warning polygons now include county warning area context and improved text formatting
- Warning filtering — filter active NWS warnings by type; improved relevance for field operations
- Annotation expiration — annotations now carry a configurable expiration time and auto-clear from the map when expired
- Auto environment updating — `conda env update --prune` step integrated into the in-app update flow

### Changed
- NSSL/OBS sounding dialog defaults to the most recent available sounding on open
- NSSL sounding unit handling fixed (temperature/dewpoint consistency)

### Fixed
- Radar data toggle state bug — toggling radar off/on no longer drops the last fetched frame
- Surface station display bug during network interruptions
- Layer pill layout and sizing on various screen resolutions
- Satellite/radar map projection alignment fix
- Various stability patches and minor UI refinements

---

## [0.9.0] - 2026-03-31
### Added
- Archive mode — replay any past session with full data reconstruction; select a date/time at launch to enter archive playback
- Central time controller with play/pause, configurable speed multipliers (1×–300×), and ←/→ step buttons (30-second steps)
- Archive fetchers for NEXRAD radar, satellite, hazards, soundings, and MQTT vehicle position data — all synchronized to the archive clock
- Archive controls bar with timeline scrubber, playback speed selector, and per-layer status indicators (radar, satellite)
- Archive loading dialog — shows fetch progress before playback begins
- Window title and status pill reflect the active archive timestamp (`[ARCHIVE YYYY-MM-DD HH:MMZ]`)

---

## [0.8.0] - 2026-03-25
### Added
- Mesonet surface observation overlay — live station data fetched and displayed on the map
- Observed sounding dialog — fetch and display real-time vertical profiles from surface obs networks
- CLAMPS sounding support — additional sounding data source via CLAMPS fetcher
- Routing and turn-by-turn navigation with off-route recalculation and arrival detection
- On-launch data fetch selection — choose which data products to load at startup

### Changed
- Status pill top row reorganized: version anchor (`STORM vX.X.X`), update indicator, and status message now occupy a dedicated top row above mode/position and connectivity rows
- Launch window and viewer mode patching

---

## [0.7.0] - 2026-03-19
### Added
- HRRR point sounding dialog — click any map location to fetch a live vertical atmospheric profile from the open-meteo HRRR API (free tier, single HTTP request per click)
- Skew-T log-P diagram with temperature, dewpoint, virtual temperature curve, wind barbs, surface-based parcel profile, and CAPE/CIN shading
- SHARPpy-inspired features: dendritic growth zone shading (-10 to -20°C), effective inflow layer bracket on left spine, AGL height reference lines (0.5–9 km) in red
- Hodograph inset (color-coded by height: 0–3 km red, 3–6 km gold, 6–9 km blue) with EIL segment highlight, Bunkers RM/LM dots, and storm motion dir/spd readout
- Parcel table showing CAPE, CIN, LCL, LFC, and EL for surface-based, mixed-layer, and most-unstable parcels
- Kinematics table showing bulk shear, SRH, and mean storm-relative wind for 0–500 m, 0–1 km, 0–3 km, and 0–6 km layers
- Composite indices row: LR 700–500, LR 0–3 km, SFC θe, PW, Convective Temperature, STP, SCP, EHI — with threshold-based color coding
- F0–F3 forecast hour scrubber in the dialog header (cyan accent); header displays both init time and valid time
- Interactive pressure-level cursor readout (hover over SkewT to see T, Td, wind, height)

### Changed
- Point sounding data source: open-meteo HRRR CONUS at 3 km / hourly resolution; rate limit 10,000 calls/day on free tier

## [0.6.0] - 2026-03-13
### Added
- Internet connectivity indicator in status pill (● NET OK / ● NET SLOW / ● NO INTERNET) — TCP check to 1.1.1.1:53 every 30 seconds
- "AWAITING VEHICLES..." placeholder in vehicle panel that auto-hides after first fetch completes

### Changed
- Tile and asset serving migrated from Flask (localhost:8765) to QWebEngineUrlSchemeHandler (storm://app/) — no open TCP port, no firewall exposure, faster startup
- Flask and Werkzeug removed as dependencies from both Mac and Windows env files
- MQTT status indicator renamed: CONNECTED → AWS OK, OFFLINE → AWS OFFLINE
- Monitor mode badge in status pill renamed: OBSERVER → MONITOR
- Update check failure message changed from red error to amber warning with "PROCEED AND TRY AGAIN LATER" guidance
- Update available text simplified from "N updates available" to "UPDATE AVAILABLE"
- Git fetch timeout in launch dialog reduced from 10s to 5s for faster failure on slow connections

### Fixed
- Hazard error clear timer was incorrectly wired to the radar error clear method — each now only clears its own prefix
- Radar error in status bar now clears immediately when a successful scan arrives instead of waiting for the timer
- Vehicle panel placeholder visibility check used `isVisible()` which returned False when panel was closed — now hides unconditionally after first fetch
- Net connectivity indicator used `QTimer.singleShot` from a background thread (unreliable) — replaced with `_NetChecker` QObject using a proper pyqtSignal

---

## [0.5.0] - 2026-03-08
### Added
- Hazard overlay panel (SPC and NWS layers accessible via HAZARDS toolbar button)
- SPC Day 1 convective outlook (MRGL / SLGT / ENH / MDT / HIGH risk tiers)
- SPC tornado, wind, and hail probability overlays
- SPC severe thunderstorm and tornado watches
- SPC Mesoscale Discussions via NOAA MapServer GeoJSON endpoint
- NWS active warnings with per-event color coding
- Click any SPC outlook or MD polygon to read the full discussion text in a sliding panel
- Version number displayed in window title and status overlay

### Fixed
- SPC outlook now correctly renders ENH (Enhanced) and MRGL (Marginal) risk tiers — previously silently dropped
- Hazard and annotation drawers now have transparent backgrounds consistent with the radar drawer
- NWS warning bounding box updates dynamically as the map is panned

---

## [0.4.0] - 2026-03-08
### Added
- Variable radar resolution control
- Front annotations (cold, warm, stationary, occluded, dry line)

### Fixed
- Default window size and position settings on Windows
- Vehicle ID assignment bug

---

## [0.3.0] - 2026-03-06
### Added
- Previous deployment locations overlay
- Windows compatibility (Chromium/ANGLE GPU workarounds, setup scripts)
- macOS and Windows application build and update scripts

### Fixed
- Various Windows setup and startup bugs
- Small road layer visibility adjustments

---

## [0.2.0] - 2026-03-05
### Added
- NEXRAD radar overlay with site selector, product toggle (reflectivity / velocity), and frame playback
- Annotation tools (road conditions, storm motion, point markers)
- Measure tool

---

## [0.1.0] - 2026-02-28
### Added
- Initial build — MapLibre GL map with local MBTiles tile server
- Basic application shell, dark theme, floating toolbar
