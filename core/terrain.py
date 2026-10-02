"""Ground elevation for base states (core/base_state.py): the mean terrain
height over a lat/lon box, the way MESO-VIEW defined Z ("mean USGS 3DEP DEM
elevation inside the mobile-mesonet lat/lon footprint for each analysis
window").

Elevations come from the public AWS Terrain Tiles (Terrarium PNG encoding,
elevation-tiles-prod; in the US built from USGS 3DEP), at zoom 13 (~16 m
pixels at 35 N), or coarser for wide boxes so no box needs more than about
6x6 tiles -- small downloads instead of 1-degree GeoTIFFs. Tiles are
kept in data/terrain_cache. Every pixel whose center lies in the box counts
equally, as a mean over the DEM grid does.
"""
from __future__ import annotations

import io
import logging
import math
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ZOOM = 13                  # finest zoom used (~16 m pixels at 35 N)
MAX_TILES_ACROSS = 6       # coarser zoom for wide boxes: a mean over tens of km doesn't need 16 m
TILE_URL = "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"
_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = _ROOT / "data" / "terrain_cache"
MIN_BOX_DEG = 0.002        # MESO-VIEW widened tinier footprints to this span


def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _tile(z: int, x: int, y: int, cache_dir: Path) -> np.ndarray | None:
    """Elevation (m) of one 256x256 Terrarium tile."""
    from PIL import Image
    path = Path(cache_dir) / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        data = path.read_bytes()
    else:
        from urllib.request import Request, urlopen
        from core import package_sources
        url = TILE_URL.format(z=z, x=x, y=y)
        for attempt in (1, 2):              # one retry: a single dropped request shouldn't blank a window
            try:
                data = package_sources.read_url("terrain", Request(url, headers={"User-Agent": "STORM/1.0"}),
                                                urlopen, timeout=30)
                break
            except OSError as exc:
                if attempt == 2:
                    log.warning("Terrain tile %s: %s", url, exc)
                    return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def zoom_for(west: float, south: float, east: float, north: float) -> int:
    """The finest zoom (<= ZOOM) at which the box spans <= MAX_TILES_ACROSS tiles."""
    for z in range(ZOOM, 4, -1):
        x0, y0 = _tile_xy(north, west, z)
        x1, y1 = _tile_xy(south, east, z)
        if int(x1) - int(x0) + 1 <= MAX_TILES_ACROSS and int(y1) - int(y0) + 1 <= MAX_TILES_ACROSS:
            return z
    return 5


def mean_elevation(west: float, south: float, east: float, north: float, *,
                   zoom: int | None = None, cache_dir: Path = CACHE_DIR) -> float:
    """Mean ground elevation (m above sea level) over the box, or NaN."""
    if east - west < MIN_BOX_DEG:
        mid = (east + west) / 2
        west, east = mid - MIN_BOX_DEG / 2, mid + MIN_BOX_DEG / 2
    if north - south < MIN_BOX_DEG:
        mid = (north + south) / 2
        south, north = mid - MIN_BOX_DEG / 2, mid + MIN_BOX_DEG / 2
    if zoom is None:
        zoom = zoom_for(west, south, east, north)
    x0, y0 = _tile_xy(north, west, zoom)          # top-left in tile units
    x1, y1 = _tile_xy(south, east, zoom)
    px0, py0 = int(math.floor(x0 * 256 - 0.5)) + 1, int(math.floor(y0 * 256 - 0.5)) + 1
    px1, py1 = int(math.floor(x1 * 256 - 0.5)), int(math.floor(y1 * 256 - 0.5))
    if px1 < px0 or py1 < py0:                    # box narrower than a pixel: nearest pixel
        px0 = px1 = int(x0 * 256)
        py0 = py1 = int(y0 * 256)
    total, count = 0.0, 0
    for tx in range(px0 // 256, px1 // 256 + 1):
        for ty in range(py0 // 256, py1 // 256 + 1):
            elev = _tile(zoom, tx, ty, cache_dir)
            if elev is None:
                return float("nan")
            cx0, cx1 = max(px0 - tx * 256, 0), min(px1 - tx * 256, 255)
            cy0, cy1 = max(py0 - ty * 256, 0), min(py1 - ty * 256, 255)
            block = elev[cy0:cy1 + 1, cx0:cx1 + 1]
            total += float(block.sum())
            count += block.size
    return total / count if count else float("nan")


def footprint_elevation(lats, lons) -> float:
    """Mean elevation over the lat/lon box spanned by these positions."""
    lats, lons = np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    ok = np.isfinite(lats) & np.isfinite(lons)
    if not ok.any():
        return float("nan")
    return mean_elevation(float(lons[ok].min()), float(lats[ok].min()),
                          float(lons[ok].max()), float(lats[ok].max()))
