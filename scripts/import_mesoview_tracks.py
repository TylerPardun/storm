"""Bring MESO-VIEW's storm tracks into STORM's shared track folder.

MESO-VIEW keeps them as <source>/{tornadic,nontornadic}/<case>_<YYYYMMDD>_<HHMM>_*.csv.
STORM keeps them flat in data/storm_tracks/Pardun_Tracks/ (core/workspace.py),
as STORM track files: named storm_YYYYMMDD_HHMM_track.csv from the track's
first point and holding only the columns STORM uses (core/storm_track.py) --
no case IDs or other MESO-VIEW bookkeeping. Each track's session date goes in
the workspace manifest, following STORM's session rule: a track starting
before 12Z belongs to the previous day's session (the evening before).

Re-running is safe: a track already there with the same points is skipped;
one whose points changed is rewritten in place and reported. A track counts
as already there when a track of the same session covers the same times
within 10 km -- so a track whose first point moved is still recognized.

    python scripts/import_mesoview_tracks.py ~/Desktop/mesonet_data_viewer/data/storm_tracks
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _same(a, b) -> bool:
    key = lambda p: (p.time, round(p.lat, 6), round(p.lon, 6), p.source)
    return sorted(map(key, a)) == sorted(map(key, b))


def _same_storm(a, b, km=10.0) -> bool:
    """Tracks that overlap in time and are within `km` at a shared time."""
    from core.storm_motion import position_at
    import math
    start, end = max(a[0].time, b[0].time), min(a[-1].time, b[-1].time)
    if start > end:
        return False
    for when in (start, start + (end - start) / 2, end):
        pa, pb = position_at(a, when), position_at(b, when)
        if pa is None or pb is None:
            return False
        dlat = math.radians(pb[0] - pa[0])
        dlon = math.radians(pb[1] - pa[1]) * math.cos(math.radians(pa[0]))
        if 6371.0 * math.hypot(dlat, dlon) > km:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="MESO-VIEW's data/storm_tracks folder")
    args = parser.parse_args()
    from core.storm_track import read_track_file, track_filename, write_track_csv
    from core.workspace import SHARED_WORKSPACE, Workspace, session_day_of

    workspace = Workspace(SHARED_WORKSPACE)
    added = updated = unchanged = 0
    for path in sorted(args.source.rglob("*.csv")):
        if path.name.startswith("."):
            continue
        try:
            points = read_track_file(path)
        except ValueError as exc:
            print(f"skipped {path.name}: {exc}")
            continue
        if not points:
            print(f"skipped {path.name}: no usable points")
            continue
        first = min(p.time for p in points)
        day = session_day_of(first.replace(tzinfo=None))
        target = workspace.root / track_filename(first)
        if not target.exists():
            target = next((t["path"] for t in workspace.tracks(day)
                           if _same_storm(read_track_file(t["path"]), points)), target)
        if target.exists():
            if _same(read_track_file(target), points):
                unchanged += 1
                continue
            print(f"updated {target.name} from {path.name}")
            updated += 1
        else:
            added += 1
        write_track_csv(points, target)
        workspace.record_track(day, target, len(points))
    print(f"{SHARED_WORKSPACE}: {added} added, {updated} updated, {unchanged} already the same "
          f"-> {workspace.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
