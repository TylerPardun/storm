# scripts/

Tools for building, checking and debugging STORM. None of them run as part of
the app. Run them from the `storm/` folder with the `storm` environment's
Python, with STORM closed (a STORM session and a check together don't fit in
8 GB).

THREDDS (data.nssl.noaa.gov) stalls under bursts of requests, so the tools
that read it ask one thing at a time, with pauses, and stop if it goes quiet.
Don't run them in a loop or several at once.

## Checking that archive mode works

| Script | What it does |
|---|---|
| `check_archive_days.py` | Opens archive sessions for many dates (one process at a time, with a memory watchdog), steps each through its active hours, exercises radar, satellite, lidar, NOXP and the Video Studio, and writes `case_data/day_checks/<run>/report.md` with screenshots. `--preset` runs a spread of 11 dates, 2009–2026. |
| `check_data_sources.py` | Asks every THREDDS folder (`archive/thredds_paths.py`) and every AWS bucket and web API (`data/endpoints.py`) whether it still answers; for a THREDDS folder that's gone, lists what its parent now holds. |
| `verify_offline_case.py` | Builds a case package from a live session, then reopens it with the network blocked and reports what loaded. |
| `stress_archive_sessions.py` | Opens and closes archive windows quickly, to catch crashes during startup and teardown. |

## Debugging one data source

| Script | What it does |
|---|---|
| `verify_archive_sources.py` | Runs STORM's own fetchers for a set of campaign dates and records file identities, record counts, time bounds and location sanity per source (mobile mesonet, recorded vehicles, CLAMPS winds/surface/TROPoe/sondes, CopterSonde). Uses `archive_probe_evidence.py`. |
| `verify_noxp_archive.py` | NOXP: a bounded inventory of a date's volumes, and explicit sample downloads. |
| `verify_raw_lidar_archive.py` | Raw lidar: the inventory for a source and date; `--load` downloads and parses its files. |
| `audit_fofs_vehicle_days.py` | Mobile mesonet: audits each vehicle's one-second files exactly as STORM loads them (dating, gaps, known corrections). |
| `report_missing_truck_heading.py` | Lists the LiDAR Truck files with no recorded truck heading, with STORM's estimate, so the data can be fixed at the source. |

## Building and installing

| Script | What it does |
|---|---|
| `make_satellite_mbtiles.py` | Downloads USGS National Map imagery and packages it as `tiles/` MBTiles for the offline satellite basemap. |
| `import_mesoview_tracks.py` | Brings MESO-VIEW's storm tracks into STORM's shared track folder. |
| `create_app.sh`, `create_app_windows.bat`, `create_desktop_entry.sh` | Make a STORM app / shortcut on macOS, Windows and Linux (the Linux one writes `launch_storm_linux.sh` for this machine). |
| `launch_storm.bat`, `launch_storm.vbs` | The Windows launchers those shortcuts run. |
