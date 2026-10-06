#!/usr/bin/env python3
"""
Download USGS National Map satellite imagery and package into an MBTiles file.

Source : USGS National Map — USGSImageryOnly (public domain, no API key required)
Output : tiles/satellite.mbtiles  (JPEG raster tiles, TMS y-axis, zoom 0-12)
Runtime: roughly 20-40 minutes on a decent connection with 20 workers
"""

import math
import os
import sqlite3
import time
import urllib.request
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data import endpoints  # noqa: E402

# ── Configuration ──────────────────────────────────────────────────────────────
WEST     = -116.0
SOUTH    =   28.0
EAST     =  -82.0
NORTH    =   49.0
MIN_ZOOM =  0
MAX_ZOOM = 12

OUTPUT   = os.path.join(os.path.dirname(__file__), "..", "tiles", "satellite.mbtiles")
WORKERS  = 20     # concurrent HTTP requests
BATCH    = 500    # tiles per SQLite commit
RETRIES  = 3
TIMEOUT  = 20     # seconds per request

# ArcGIS REST tile URL — note {z}/{y}/{x} order (row before column)
TILE_URL = endpoints.NATIONALMAP_SERVICES + "/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
HEADERS = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
# ──────────────────────────────────────────────────────────────────────────────


def _lon_to_x(lon: float, z: int) -> int:
    return int((lon + 180.0) / 360.0 * (1 << z))


def _lat_to_y(lat: float, z: int) -> int:
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0
    return int(y * (1 << z))


def _tile_coords(west, south, east, north, min_z, max_z):
    for z in range(min_z, max_z + 1):
        x0, x1 = _lon_to_x(west, z),  _lon_to_x(east, z)
        y0, y1 = _lat_to_y(north, z), _lat_to_y(south, z)  # north = smaller y
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


def _fetch(z: int, x: int, y: int):
    url = TILE_URL.format(z=z, y=y, x=x)
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return z, x, y, resp.read()
        except Exception:
            if attempt < RETRIES - 1:
                time.sleep(0.5 * (attempt + 1))
    return z, x, y, None


def _init_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level  INTEGER,
            tile_column INTEGER,
            tile_row    INTEGER,
            tile_data   BLOB,
            PRIMARY KEY (zoom_level, tile_column, tile_row)
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT)")
    meta = {
        "name":        "USGS Satellite",
        "type":        "baselayer",
        "version":     "1.0",
        "description": "USGS National Map USGSImageryOnly — public domain",
        "format":      "jpg",
        "minzoom":     str(MIN_ZOOM),
        "maxzoom":     str(MAX_ZOOM),
        "bounds":      f"{WEST},{SOUTH},{EAST},{NORTH}",
        "center":      f"{(WEST+EAST)/2:.1f},{(SOUTH+NORTH)/2:.1f},{(MIN_ZOOM+MAX_ZOOM)//2}",
    }
    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", meta.items())
    conn.commit()
    return conn


def _already_done(conn: sqlite3.Connection) -> set:
    """Return set of (z, x, y_xyz) already stored, for resume support."""
    cur = conn.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")
    return {(z, x, (1 << z) - 1 - y_tms) for z, x, y_tms in cur.fetchall()}


def main():
    output = os.path.normpath(OUTPUT)
    print(f"Output : {output}")
    print(f"Bounds : {WEST},{SOUTH} → {EAST},{NORTH}")
    print(f"Zooms  : {MIN_ZOOM}–{MAX_ZOOM}")
    print(f"Workers: {WORKERS}")
    print()

    conn    = _init_db(output)
    done    = _already_done(conn)
    pending = [t for t in _tile_coords(WEST, SOUTH, EAST, NORTH, MIN_ZOOM, MAX_ZOOM)
               if t not in done]
    total   = len(pending)

    print(f"Tiles already cached : {len(done):,}")
    print(f"Tiles to fetch       : {total:,}")
    if not total:
        print("Nothing to do — all tiles present.")
        conn.close()
        return
    print()

    start   = time.time()
    fetched = n_failed = 0
    batch   = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch, z, x, y): (z, x, y) for z, x, y in pending}
        for future in as_completed(futures):
            z, x, y, data = future.result()
            fetched += 1

            if data:
                y_tms = (1 << z) - 1 - y
                batch.append((z, x, y_tms, data))
            else:
                n_failed += 1

            if len(batch) >= BATCH:
                conn.executemany("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch)
                conn.commit()
                batch.clear()

            if fetched % 1000 == 0 or fetched == total:
                elapsed   = time.time() - start
                rate      = fetched / elapsed if elapsed else 0
                remaining = (total - fetched) / rate if rate else 0
                size_mb   = os.path.getsize(output) / 1024 ** 2
                print(
                    f"  {fetched:>7,}/{total:,}  "
                    f"({fetched/total*100:.1f}%)  "
                    f"{rate:.0f} tiles/s  "
                    f"ETA {remaining/60:.1f} min  "
                    f"size {size_mb:.0f} MB  "
                    f"failed={n_failed}"
                )

    if batch:
        conn.executemany("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch)
        conn.commit()

    conn.close()
    elapsed = time.time() - start
    size_gb = os.path.getsize(output) / 1024 ** 3
    print(f"\nFinished in {elapsed/60:.1f} min")
    print(f"File size : {size_gb:.2f} GB")
    print(f"Failed    : {n_failed} tiles")


if __name__ == "__main__":
    main()
