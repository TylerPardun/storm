
import json
import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

import config
from PyQt6.QtCore import QObject, pyqtSignal

from data import endpoints
log = logging.getLogger(__name__)

_ORS_BASE        = endpoints.OPENROUTESERVICE_DRIVING
_NOMINATIM_BASE  = endpoints.NOMINATIM_SEARCH
_USER_AGENT      = "STORM-App/1.0 (storm-chasing field operations)"
_REQUEST_TIMEOUT = 15  # seconds

# maps ORS step type integer → (arrow, verb)
_MANEUVER_MAP: dict[int, tuple[str, str]] = {
    0:  ("←",  "Turn left onto"),
    1:  ("→",  "Turn right onto"),
    2:  ("↙",  "Turn sharp left onto"),
    3:  ("↘",  "Turn sharp right onto"),
    4:  ("↖",  "Turn slight left onto"),
    5:  ("↗",  "Turn slight right onto"),
    6:  ("↑",  "Continue straight on"),
    7:  ("↻",  "Enter roundabout"),
    8:  ("↑",  "Exit roundabout onto"),
    9:  ("↩",  "Make a U-turn"),
    10: ("⦿", "Arrive at"),
    11: ("↑",  "Head"),
}


@dataclass
class RouteStep:
    icon: str
    instruction: str
    distance_m: float
    duration_s: float
    location: tuple[float, float] = (0.0, 0.0)   # (lat, lon) of maneuver point


@dataclass
class RouteResult:
    geometry: dict          # GeoJSON LineString
    steps: list[RouteStep]
    distance_m: float
    duration_s: float
    origin_latlon: tuple[float, float]
    dest_latlon: tuple[float, float]


class RoutingFetcher(QObject):
    """Fire-and-forget fetcher for OSRM routes and Nominatim geocoding.

    All network I/O runs on daemon threads. Signals are emitted on the
    calling thread (connected to the Qt event loop via queued connections).

    Signals:
        route_ready(RouteResult)   — route successfully computed
        geocode_ready(str, float, float, bool)
                                   — (display_name, lat, lon, is_origin)
        fetch_error(str)           — any failure
    """

    route_ready   = pyqtSignal(object)            # RouteResult
    geocode_ready = pyqtSignal(str, float, float, bool)  # name, lat, lon, is_origin
    fetch_error   = pyqtSignal(str)

    def fetch_route(
        self,
        origin_lat: float, origin_lon: float,
        dest_lat: float,   dest_lon: float,
    ):
        """Start a background route fetch. Returns immediately."""
        threading.Thread(
            target=self._bg_route,
            args=(origin_lat, origin_lon, dest_lat, dest_lon),
            daemon=True,
        ).start()

    def geocode(self, address: str, is_origin: bool = False):
        """Start a background geocoding request. Returns immediately."""
        threading.Thread(
            target=self._bg_geocode,
            args=(address, is_origin),
            daemon=True,
        ).start()


    def _bg_route(self, olat, olon, dlat, dlon):
        try:
            result = _fetch_route(olat, olon, dlat, dlon)
            self.route_ready.emit(result)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"Routing error: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"Routing failed: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)

    def _bg_geocode(self, address: str, is_origin: bool):
        try:
            name, lat, lon = _geocode_address(address)
            self.geocode_ready.emit(name, lat, lon, is_origin)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"Geocoding error: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"Geocoding failed: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)



def _get_json(url: str, headers: dict | None = None) -> dict | list:
    log.debug("fetch → %s", url)
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers)
    with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    log.debug("fetch ✓ %.1f KB ← %s", len(raw) / 1024, url[:80])
    return json.loads(raw)


def _geocode_address(address: str) -> tuple[str, float, float]:
    params = urlencode({"format": "json", "q": address, "limit": 1})
    url    = f"{_NOMINATIM_BASE}?{params}"
    data   = _get_json(url)
    if not data:
        raise ValueError(f"No results found for \"{address}\"")
    hit = data[0]
    return hit.get("display_name", address), float(hit["lat"]), float(hit["lon"])


def _fetch_route(
    olat: float, olon: float, dlat: float, dlon: float
) -> RouteResult:
    params = urlencode({
        "api_key": config.ORS_API_KEY,
        "start":   f"{olon},{olat}",
        "end":     f"{dlon},{dlat}",
    })
    url  = f"{_ORS_BASE}?{params}"
    data = _get_json(url)

    features = data.get("features", [])
    if not features:
        raise ValueError("ORS returned no route features")

    props      = features[0]["properties"]
    geometry   = features[0]["geometry"]          # GeoJSON LineString
    summary    = props.get("summary", {})
    distance_m = summary.get("distance", 0.0)
    duration_s = summary.get("duration", 0.0)

    # ors geometry coordinates are [lon, lat]; extract for step location lookup
    coords = geometry.get("coordinates", [])

    steps: list[RouteStep] = []
    for segment in props.get("segments", []):
        for step in segment.get("steps", []):
            step_type  = step.get("type", 6)      # default: straight
            name       = step.get("name", "") or ""
            icon, verb = _MANEUVER_MAP.get(step_type, ("↑", "Continue"))

            instruction = f"{verb} {name}".strip() if name else verb

            # way_points[0] is the index into geometry.coordinates for this step
            wp_idx = step.get("way_points", [0])[0]
            if wp_idx < len(coords):
                loc_lon, loc_lat = coords[wp_idx][0], coords[wp_idx][1]
            else:
                loc_lat, loc_lon = olat, olon

            steps.append(RouteStep(
                icon        = icon,
                instruction = instruction,
                distance_m  = step.get("distance", 0.0),
                duration_s  = step.get("duration", 0.0),
                location    = (loc_lat, loc_lon),
            ))

    return RouteResult(
        geometry      = geometry,
        steps         = steps,
        distance_m    = distance_m,
        duration_s    = duration_s,
        origin_latlon = (olat, olon),
        dest_latlon   = (dlat, dlon),
    )



def fmt_distance(meters: float) -> str:
    """Return a human-readable distance string (miles)."""
    miles = meters / 1609.34
    if miles < 0.1:
        feet = meters * 3.28084
        return f"{feet:.0f} ft"
    if miles < 10:
        return f"{miles:.1f} mi"
    return f"{miles:.0f} mi"


def fmt_duration(seconds: float) -> str:
    """Return a human-readable duration string."""
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours   = minutes // 60
    mins    = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"
