# Storm tracks

Every storm track STORM uses or saves lives in this folder:

```
data/storm_tracks/<workspace>/<session date YYYYMMDD>/<track>.csv
                                                     /manifest.json
```

- **MESO-VIEW/** — the shared tracks from MESO-VIEW (52 tornadic `T*`, 57 non-tornadic `N*`), part of STORM. File names and contents are MESO-VIEW's own; each date's `manifest.json` records the case ID, tornadic/non-tornadic category and where the file came from. Update with `python scripts/import_mesoview_tracks.py <MESO-VIEW data/storm_tracks folder>`.
- **My work/** (or any other workspace name) — tracks users create or edit in STORM. Opening a MESO-VIEW track from your own workspace edits a copy here, so the shared originals stay unchanged. These folders are not committed (see `.gitignore`); share a track by adding it to `MESO-VIEW/` or sending a case package.

**Session date:** a track belongs to the date of the archive session it was drawn in. Tracks starting before 12Z count toward the previous day's session (the evening before, in the US), e.g. `N11_20220610_0114…` is filed under `20220609`.

Files are plain CSV (`point_id, time, lat, lon, source, case_id, …`), readable by STORM, MESO-VIEW, Excel or pandas.
