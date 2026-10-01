"""Opening STORM's MBTiles files (tiles/*.mbtiles) safely.

sqlite3.connect() on a path that doesn't exist creates an empty database
there -- so without this, a missing tiles/storm.mbtiles would be replaced by
a 0-byte file the next time anything looked up its bounds, and the map would
then try to use it. Everything here opens read-only and never creates.
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote


def usable(path: str) -> bool:
    """The file exists and isn't empty (a 0-byte file is no tile set)."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def connect(path: str) -> sqlite3.Connection:
    """A read-only connection; raises sqlite3.OperationalError if the file
    is missing instead of creating it."""
    return sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro", uri=True)


def bounds(path: str) -> tuple[float, float, float, float] | None:
    """(west, south, east, north) from the metadata, or None."""
    if not usable(path):
        return None
    try:
        conn = connect(path)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE name='bounds'").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        west, south, east, north = (float(v) for v in row[0].split(","))
        return west, south, east, north
    except (sqlite3.Error, ValueError):
        return None
