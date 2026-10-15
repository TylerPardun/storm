# scripts/

Tools for building, checking and debugging STORM. None of them run as part of
the app. Run them from the `storm/` folder with the `storm` environment's
Python, with STORM closed (a STORM session and a check together don't fit in
8 GB).

THREDDS (data.nssl.noaa.gov) stalls under bursts of requests, so the tools
that read it ask one thing at a time, with pauses, and stop if it goes quiet.
Don't run them in a loop or several at once.

## Checking that STORM works

| Script | What it does |
|---|---|
| `check_archive_days.py` | Opens archive sessions for many dates (one process at a time, with a memory watchdog), steps each through its active hours, exercises radar, satellite, lidar, NOXP and the Video Studio, and writes `case_data/day_checks/<run>/report.md` with screenshots. `--preset` runs a spread of 11 dates, 2009–2026. |
| `check_live_mode.py` | Opens STORM in viewer, monitor or vehicle mode, turns on radar, hazards and satellite as a user would, and reports what each live source delivered (`case_data/live_checks/`). Monitor and vehicle run with MQTT off so a check never appears on the team's live map; viewer (read-only) connects. |
| `check_data_sources.py` | Asks every THREDDS folder (`archive/thredds_paths.py`) and every AWS bucket and web API (`data/endpoints.py`) whether it still answers; for a THREDDS folder that's gone, lists what its parent now holds. |
| `verify_offline_case.py` | Builds a case package from a live session, then reopens it with the network blocked and reports what loaded. |
| `stress_archive_sessions.py` | Opens and closes archive windows quickly, to catch crashes during startup and teardown. |
| `benchmark_hardware.py` | Times STORM's heavy steps (radar decode, dealiasing, radar and lidar images) on fixed inputs and prints them next to the development Mac's times. 1–3 minutes, about 1 GB of memory, one 10.6 MB download from AWS. |
| `summarize_perf_log.py` | Turns a `--perf-log` session log into a readable report (computer, step timings, memory, CPU, window freezes, map smoothness, warnings). |
| `collect_perf_logs.py` | Zips the performance logs, benchmark results, STORM's error logs and the latest day-check reports into one file on the Desktop, for upload. |

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
| `launch_storm.bat`, `launch_storm.vbs` | The Windows launchers those shortcuts run. `launch_storm.bat` passes any options on to `main.py`. |
| `launch_storm_perf_log.bat` | Windows: start STORM with the performance log on (`--perf-log`). |

## Testing on a field laptop (Toughbook, older Intel, Windows)

The goal is a log of how STORM actually runs there, to compare with the
development Mac (see `planning/performance-on-older-hardware.md`).

**1. Install this branch.** In Miniforge Prompt (or any terminal with conda):

```
git clone -b feature/mesoview-merge https://github.com/TylerPardun/storm.git
cd storm
python setup.py
```

`setup.py` creates the `storm` environment and a desktop shortcut. The
offline satellite basemap (`tiles/*.mbtiles`) isn't in git; without it the
map uses the online basemap, which is fine for this test.

**2. Benchmark (once).** Close STORM, then in the `storm` folder:

```
conda activate storm
python scripts/benchmark_hardware.py
```

It prints each step next to the Mac's time (e.g. `2.4×` = 2.4 times slower).

**3. Use STORM with the performance log on**, the way you normally would:
double-click `scripts\launch_storm_perf_log.bat`, or `python main.py
--perf-log`. Open archive dates you know, play at different speeds, scrub,
open radar velocity with dealiasing, lidar, mesonet trails, the Video
Studio. Each session writes `perf_logs\perf-<computer>-<time>.jsonl`.
Write down anything that felt slow, and roughly when.

**4. Optional: the automated day check** (about an hour for 11 dates, rests
between dates so THREDDS isn't hammered):

```
python scripts/check_archive_days.py --preset
```

**5. Collect and upload:**

```
python scripts/collect_perf_logs.py
```

It saves `storm-perf-<computer>-<date>.zip` on the Desktop: upload that.
To read a log on the laptop itself: `python scripts/summarize_perf_log.py`.

What the log records: the computer (CPU, cores, RAM, Windows version,
screen, the graphics chip the map uses), each radar download / decode /
image and lidar load / image with its time, memory and CPU every 5 s, every
time the window stopped responding for 0.3 s or more, how smoothly the map
drew, playback speed changes, and STORM's own warnings. No passwords, keys
or personal files.
