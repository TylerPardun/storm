"""Copy MESO-VIEW's storm tracks into STORM's shared track folder.

MESO-VIEW keeps them as <source>/{tornadic,nontornadic}/<case>_<YYYYMMDD>_<HHMM>_*.csv.
STORM keeps them flat in data/storm_tracks/Pardun_Tracks/ (core/workspace.py),
named the way STORM names every track -- storm_YYYYMMDD_HHMM_track.csv from
the track's first point -- with no case IDs in the names. The file contents
are copied unchanged. Each track's session date goes in the workspace
manifest, following STORM's session rule: a track starting before 12Z
belongs to the previous day's session (the evening before). The manifest's
"origin" records which MESO-VIEW file a track came from, so re-running
updates that same file.

Re-running is safe: files already present with identical contents are
skipped; a changed file is updated and reported.

    python scripts/import_mesoview_tracks.py ~/Desktop/mesonet_data_viewer/data/storm_tracks
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="MESO-VIEW's data/storm_tracks folder")
    args = parser.parse_args()
    from core.storm_track import read_track_file, track_filename, unused_path
    from core.workspace import MANIFEST, SHARED_WORKSPACE, Workspace, session_day_of

    workspace = Workspace(SHARED_WORKSPACE)
    try:
        manifest = json.loads((workspace.root / MANIFEST).read_text())
    except (OSError, ValueError):
        manifest = {}
    by_origin = {t.get("origin"): t["file"] for t in manifest.get("tracks", []) if t.get("origin")}
    added = updated = unchanged = 0
    for path in sorted(args.source.rglob("*.csv")):
        if path.name.startswith("."):
            continue
        category = path.parent.name if path.parent != args.source else ""
        origin = f"MESO-VIEW {category}/{path.name}".replace(" /", " ")
        try:
            points = read_track_file(path)
        except ValueError as exc:
            print(f"skipped {path.name}: {exc}")
            continue
        if not points:
            print(f"skipped {path.name}: no usable points")
            continue
        first = min(p.time for p in points)
        data = path.read_bytes()
        existing = workspace.root / by_origin[origin] if origin in by_origin else None
        if existing is not None and existing.exists():
            if existing.read_bytes() == data:
                unchanged += 1
                continue
            target = existing
            updated += 1
        else:
            target = unused_path(workspace.root / track_filename(first))
            added += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        workspace.record_track(session_day_of(first.replace(tzinfo=None)), target, len(points), origin=origin)
        by_origin[origin] = target.name
    print(f"{SHARED_WORKSPACE}: {added} added, {updated} updated, {unchanged} already identical "
          f"-> {workspace.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
