# Storm tracks

Every storm track STORM uses or saves lives in this folder, one folder per workspace:

```
data/storm_tracks/<workspace>/storm_YYYYMMDD_HHMM_track.csv
                             /manifest.json
```

Track files are named from the track's first point (UTC). Two tracks starting in the same minute get `_2`, `_3`, …

- **Pardun_Tracks/** — the shared tracks that come with STORM (109 tracks from MESO-VIEW, 78 dates). **STORM never changes these files.** `manifest.json` records each track's session date. To update them from MESO-VIEW, run `python scripts/import_mesoview_tracks.py <MESO-VIEW data/storm_tracks folder>`.
- **My work/** (or any other workspace name) — tracks users create or edit in STORM. The first edit to a shared track saves the whole track here as a copy with the same name. From then on your copy is the one STORM opens and lists, in place of the shared one. These folders are not committed (see `.gitignore`). To share a track, add it to `Pardun_Tracks/` or send a case package.

**Opening:** when you click TRACK, STORM opens the session date's saved track automatically: your own copy if you have one, otherwise the shared one. If the date has several tracks, STORM opens the one nearest the current time. The others are in the list next to OPEN. If the date has none, TRACK starts empty for a new track.

**Session date:** a track belongs to the archive session it was drawn in, as recorded in the manifest. For a file the manifest doesn't list, STORM uses the file's first point, and a track starting before 12Z counts toward the previous day's session (the evening before, in the US). For example, `storm_20220610_0114_track.csv` belongs to the session of 2022-06-09.

Files are plain CSV holding only what STORM uses: `point_id, time, lat, lon, source` (how the point got there: placed, moved, or from the original file) and the radar, product and tilt on screen when the point was placed. STORM can load any CSV or Excel table with time and lat/lon columns; it ignores other columns and doesn't write them back.
