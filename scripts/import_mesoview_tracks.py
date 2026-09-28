"""Copy MESO-VIEW's storm tracks into STORM's track folder.

MESO-VIEW keeps them as <source>/{tornadic,nontornadic}/<case>_<YYYYMMDD>_<HHMM>_*.csv.
STORM keeps tracks as data/storm_tracks/<workspace>/<session date>/<file>.csv
with a manifest.json per date (core/workspace.py); these go to the
"MESO-VIEW" workspace. File names and contents are kept unchanged. The
session date follows STORM's session rule: a track starting before 12Z
belongs to the previous day's session (e.g. N11_20220610_0114 is the evening
of 2022-06-09). Tornadic / non-tornadic is kept in each manifest entry.

Re-running is safe: files already present with identical contents are
skipped; a changed file is updated and reported.

    python scripts/import_mesoview_tracks.py ~/Desktop/mesonet_data_viewer/data/storm_tracks
"""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKSPACE = "MESO-VIEW"


def session_day(first_time):
    return (first_time - timedelta(days=1)).date() if first_time.hour < 12 else first_time.date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="MESO-VIEW's data/storm_tracks folder")
    args = parser.parse_args()
    from core.storm_track import case_id_for, read_track_file
    from core.workspace import Workspace, atomic_write_text

    workspace = Workspace(WORKSPACE)
    added = updated = unchanged = 0
    for path in sorted(args.source.rglob("*.csv")):
        category = path.parent.name if path.parent != args.source else ""
        try:
            points = read_track_file(path)
        except ValueError as exc:
            print(f"skipped {path.name}: {exc}")
            continue
        if not points:
            print(f"skipped {path.name}: no usable points")
            continue
        day = session_day(min(p.time for p in points))
        target = workspace.case_dir(day) / path.name
        data = path.read_bytes()
        if target.exists() and target.read_bytes() == data:
            unchanged += 1
            continue
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        workspace.record_track(day, target, len(points), origin=f"MESO-VIEW {category}/{path.name}".replace(" /", " "))
        manifest_path = workspace.case_dir(day) / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["tracks"]:
            if entry["file"] == path.name:
                entry.update(case_id=case_id_for(path), category=category)
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
        updated += existed
        added += not existed
    print(f"{WORKSPACE}: {added} added, {updated} updated, {unchanged} already identical "
          f"-> {workspace.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
