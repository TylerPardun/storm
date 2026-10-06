
import os
from pathlib import Path

from config import ACCENT_COLOR
from data import endpoints


DEFAULT_LAT  = 35.22
DEFAULT_LON  = -97.44
DEFAULT_ZOOM = 6
_STORM_BASE  = "storm://app"   # base for all asset/tile URLs

TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm.mbtiles")
)

STATIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "static")
)

_TEMPLATE_PATH = Path(__file__).with_name("map_template.html")

# Without tiles/storm.mbtiles the base map comes over the network from
# OpenFreeMap: the same OpenMapTiles schema (layers, classes, name fields)
# the local file uses, so the style is unchanged. Free, no key; its TileJSON
# carries the OpenStreetMap/OpenMapTiles attribution the map shows.
ONLINE_TILEJSON = endpoints.OPENFREEMAP_TILEJSON


def basemap_source() -> str:
    """'local' when tiles/storm.mbtiles is there, otherwise 'online'."""
    from core import mbtiles
    return "local" if mbtiles.usable(TILES_PATH) else "online"


def build_map_html() -> str:
    """Build the full HTML page for the MapLibre map."""
    if basemap_source() == "local":
        tile_source = f'tiles: ["{_STORM_BASE}/tiles/{{z}}/{{x}}/{{y}}.pbf"],'
    else:
        tile_source = f'url: "{ONLINE_TILEJSON}",'
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("__STORM_BASE__", _STORM_BASE)
        .replace('tiles: ["__TILE_URL__"],', tile_source)
        .replace("__ACCENT_COLOR__", ACCENT_COLOR)
        .replace("__DEFAULT_LON__", str(DEFAULT_LON))
        .replace("__DEFAULT_LAT__", str(DEFAULT_LAT))
        .replace("__DEFAULT_ZOOM__", str(DEFAULT_ZOOM))
    )
