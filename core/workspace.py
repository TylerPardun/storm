"""Storm tracks, organised by workspace and archive session date, inside
STORM's own folder (data/storm_tracks) so every track -- the shared
MESO-VIEW set and anything users create or edit -- lives in one known place:

    data/storm_tracks/<workspace>/<YYYYMMDD>/
        manifest.json                 what's here, for resuming a case
        storm_20240427_2003_track.csv
        ...

"MESO-VIEW" holds the shared tracks imported from MESO-VIEW
(scripts/import_mesoview_tracks.py); users work in their own workspace
("My work" by default), where opening a shared track edits a copy.

Track files are ordinary STORM/MESO-VIEW track CSVs (core/storm_track.py) so
they can be opened anywhere. The manifest is small and rebuildable: a track
file missing from it is still listed, and entries whose file is gone are
dropped. Files are written atomically (temp file, then rename) so a crash
mid-save can't leave half a track.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_WORKSPACE = "My work"
MANIFEST = "manifest.json"
_BAD_NAME = re.compile(r"[^A-Za-z0-9 ._-]")


TRACKS_ROOT = Path(__file__).resolve().parents[1] / "data" / "storm_tracks"
SHARED_WORKSPACE = "MESO-VIEW"


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
    workspace -- the active one's first, then the rest alphabetically."""
    found = []
    for name in list_workspaces(root):
        for track in Workspace(name, root=root).tracks(session_day):
            found.append({**track, "workspace": name})
    return found


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
        self.name = clean_name(name) or DEFAULT_WORKSPACE
        self.root = (root or workspaces_root()) / self.name

    def case_dir(self, session_day: date) -> Path:
        return self.root / session_day.strftime("%Y%m%d")

    def contains(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    def _read_manifest(self, session_day: date) -> dict:
        path = self.case_dir(session_day) / MANIFEST
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def tracks(self, session_day: date) -> list[dict]:
        """This date's tracks, most recently edited first: dicts with
        path, points, updated_at and origin (file it was imported from, or "")."""
        folder = self.case_dir(session_day)
        if not folder.is_dir():
            return []
        known = {t.get("file"): t for t in self._read_manifest(session_day).get("tracks", [])}
        found = []
        for path in folder.glob("*.csv"):
            if path.name.startswith("."):    # hidden / AppleDouble "._" files on external drives
                continue
            entry = known.get(path.name, {})
            found.append({
                "path": path,
                "points": entry.get("points"),
                "updated_at": entry.get("updated_at") or datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "origin": entry.get("origin", ""),
                "category": entry.get("category", ""),
                "case_id": entry.get("case_id", ""),
            })
        return sorted(found, key=lambda t: t["updated_at"], reverse=True)

    def record_track(self, session_day: date, path: Path, points: int, origin: str = "") -> None:
        """Note a saved track in the date's manifest."""
        path = Path(path)
        manifest = self._read_manifest(session_day)
        entries = {t.get("file"): t for t in manifest.get("tracks", [])
                   if (self.case_dir(session_day) / str(t.get("file"))).exists()}
        previous = entries.get(path.name, {})
        entries[path.name] = {
            **previous,                     # keeps extra fields, e.g. MESO-VIEW category / case_id
            "file": path.name,
            "points": points,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "origin": origin or previous.get("origin", ""),
        }
        manifest.update({
            "workspace": self.name,
            "session_date": session_day.isoformat(),
            "tracks": sorted(entries.values(), key=lambda t: t["file"]),
        })
        atomic_write_text(self.case_dir(session_day) / MANIFEST, json.dumps(manifest, indent=2) + "\n")
