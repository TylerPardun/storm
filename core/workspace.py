"""Storm tracks, organized by workspace inside STORM's own folder
(data/storm_tracks) so every track -- the shared set and anything users
create or edit -- lives in one known place:

    data/storm_tracks/<workspace>/
        manifest.json                     each track's session date, for resuming a case
        storm_20240427_2003_track.csv     named from the track's first point
        ...

"Pardun_Tracks" holds the shared tracks that come with STORM
(scripts/import_mesoview_tracks.py); users work in their own workspace
("My work" by default), where opening a shared track edits a copy of the
same name.

A track belongs to the archive session it was drawn in, recorded in the
manifest. A file the manifest doesn't know goes by its first point: before
12Z it counts toward the previous day's session (the evening before, in the
US). Track files are ordinary STORM/MESO-VIEW track CSVs (core/storm_track.py)
so they can be opened anywhere. The manifest is small and rebuildable; entries
whose file is gone are dropped. Files are written atomically (temp file, then
rename) so a crash mid-save can't leave half a track.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_WORKSPACE = "My work"
MANIFEST = "manifest.json"
_BAD_NAME = re.compile(r"[^A-Za-z0-9 ._-]")


TRACKS_ROOT = Path(__file__).resolve().parents[1] / "data" / "storm_tracks"
SHARED_WORKSPACE = "Pardun_Tracks"
# earlier names of workspaces, so a saved choice still finds its folder
_RENAMED = {"MESO-VIEW": SHARED_WORKSPACE}
_STAMP = re.compile(r"(\d{8})_(\d{4})")


def workspaces_root() -> Path:
    return TRACKS_ROOT


def clean_name(name: str) -> str:
    """A workspace name that is safe as a folder name on every platform."""
    cleaned = _BAD_NAME.sub("", name).strip(" .")
    return cleaned[:60]


def list_workspaces(root: Path | None = None) -> list[str]:
    root = root or workspaces_root()
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
    return names if DEFAULT_WORKSPACE in names else [DEFAULT_WORKSPACE] + names


def tracks_for_date(session_day: date, root: Path | None = None) -> list[dict]:
    """Every workspace's tracks for a date, each dict also naming its
    workspace, workspaces in alphabetical order."""
    found = []
    for name in list_workspaces(root):
        for track in Workspace(name, root=root).tracks(session_day):
            found.append({**track, "workspace": name})
    return found


def session_day_of(first_point: datetime) -> date:
    """The session a track starting at `first_point` (UTC) belongs to."""
    return (first_point - timedelta(hours=12)).date()


def _first_point_time(path: Path) -> datetime | None:
    """From the file name (storm_YYYYMMDD_HHMM_track.csv and MESO-VIEW's
    <case>_YYYYMMDD_HHMM_*.csv), else from the file itself."""
    match = _STAMP.search(path.name)
    if match:
        try:
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M")
        except ValueError:
            pass
    from core.storm_track import read_track_file
    try:
        points = read_track_file(path)
    except Exception:
        return None
    return min(p.time for p in points).replace(tzinfo=None) if points else None


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Workspace:
    def __init__(self, name: str, root: Path | None = None):
        name = _RENAMED.get(name, name)
        self.name = clean_name(name) or DEFAULT_WORKSPACE
        self.root = (root or workspaces_root()) / self.name

    def contains(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    def _read_manifest(self) -> dict:
        try:
            data = json.loads((self.root / MANIFEST).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def tracks(self, session_day: date) -> list[dict]:
        """This date's tracks, most recently edited first: dicts with
        path, points, updated_at and origin (file it was copied from, or "")."""
        if not self.root.is_dir():
            return []
        known = {t.get("file"): t for t in self._read_manifest().get("tracks", [])}
        wanted = session_day.isoformat()
        found = []
        for path in self.root.glob("*.csv"):
            if path.name.startswith("."):    # hidden / AppleDouble "._" files on external drives
                continue
            entry = known.get(path.name, {})
            day = entry.get("session_date")
            if not day:
                first = _first_point_time(path)
                day = session_day_of(first).isoformat() if first else None
            if day != wanted:
                continue
            found.append({
                "path": path,
                "points": entry.get("points"),
                "updated_at": entry.get("updated_at") or datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "origin": entry.get("origin", ""),
            })
        return sorted(found, key=lambda t: t["updated_at"], reverse=True)

    def record_track(self, session_day: date, path: Path, points: int, origin: str = "") -> None:
        """Note a saved track, and the session it belongs to, in the manifest."""
        path = Path(path)
        manifest = self._read_manifest()
        entries = {t.get("file"): t for t in manifest.get("tracks", [])
                   if (self.root / str(t.get("file"))).exists()}
        previous = entries.get(path.name, {})
        entries[path.name] = {
            **previous,                     # keeps any extra fields
            "file": path.name,
            "session_date": session_day.isoformat(),
            "points": points,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "origin": origin or previous.get("origin", ""),
        }
        manifest.update({
            "workspace": self.name,
            "tracks": sorted(entries.values(), key=lambda t: t["file"]),
        })
        atomic_write_text(self.root / MANIFEST, json.dumps(manifest, indent=2) + "\n")
