from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 STORM/1.0"
SATSQUATCH_BASE_URL = "https://tiledata.satsquatch.com/tilesdata"
_MBTILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm.mbtiles")
)


MESOANALYSIS_PRODUCTS: tuple[tuple[str, str], ...] = (
    ("temp", "Temperature"),
    ("stpc", "STP"),
    ("srh01", "0-1 km SRH"),
    ("srh03", "0-3 km SRH"),
    ("sfclfc", "SFC LFC"),
    ("sfclcl", "SFC LCL"),
    ("sfccape", "SFC CAPE"),
    ("sfccinh", "SFC CINH"),
    ("scp", "SCP"),
    ("mulfc", "MU LFC"),
    ("mulcl", "MU LCL"),
    ("mucinh", "MU CINH"),
    ("mucape", "MU CAPE"),
    ("mslp", "MSLP"),
    ("mllfc", "ML LFC"),
    ("mllcl", "ML LCL"),
    ("mlcinh", "ML CINH"),
    ("mlcape", "ML CAPE"),
    ("dwpt", "Dewpoint"),
    ("cape03", "0-3 km CAPE"),
)

MESOANALYSIS_PRODUCT_UNITS: dict[str, str] = {
    "temp": "degF",
    "dwpt": "degF",
    "stpc": "",
    "scp": "",
    "srh01": "m2/s2",
    "srh03": "m2/s2",
    "sfccape": "J/kg",
    "mucape": "J/kg",
    "mlcape": "J/kg",
    "cape03": "J/kg",
    "sfccinh": "J/kg",
    "mucinh": "J/kg",
    "mlcinh": "J/kg",
    "sfclcl": "m",
    "sfclfc": "m",
    "mulcl": "m",
    "mulfc": "m",
    "mllcl": "m",
    "mllfc": "m",
    "mslp": "mb",
}


@dataclass(frozen=True)
class MesoanalysisProduct:
    product_id: str
    label: str


@dataclass(frozen=True)
class MesoanalysisTime:
    time_id: str
    label: str
    timestamp: datetime | None = None


class MesoanalysisFetcher(QObject):
    """Fetch Satsquatch mesoanalysis vector-tile manifests."""

    products_ready = pyqtSignal(object)              # list[MesoanalysisProduct]
    times_ready = pyqtSignal(str, object)            # product_id, list[MesoanalysisTime]
    overlay_ready = pyqtSignal(object)               # metadata dict for MapLibre
    fetch_error = pyqtSignal(str)

    def __init__(self, base_url: str = SATSQUATCH_BASE_URL, parent=None):
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._times: dict[str, list[MesoanalysisTime]] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    def refresh_products(self) -> None:
        self.products_ready.emit([
            MesoanalysisProduct(product_id=pid, label=label)
            for pid, label in MESOANALYSIS_PRODUCTS
        ])

    def fetch_times(self, product_id: str) -> None:
        threading.Thread(
            target=self._fetch_times_worker,
            args=(str(product_id or "").strip(),),
            daemon=True,
        ).start()

    def fetch_overlay(self, product_id: str, time_id: str) -> None:
        threading.Thread(
            target=self._fetch_overlay_worker,
            args=(str(product_id or "").strip(), str(time_id or "").strip()),
            daemon=True,
        ).start()

    def _fetch_times_worker(self, product_id: str) -> None:
        if not _is_known_product(product_id):
            return
        try:
            payload = _fetch_json(urljoin(self._base_url + "/", f"{product_id}.json"))
            times = [
                MesoanalysisTime(
                    time_id=str(time_id),
                    label=_time_label(str(time_id)),
                    timestamp=_parse_time_id(str(time_id)),
                )
                for time_id in payload.get("times", [])
                if str(time_id).strip()
            ][-4:]
            self._times[product_id] = times
            self.times_ready.emit(product_id, times)
        except Exception as exc:
            log.warning("Mesoanalysis times fetch failed: %s", exc)
            self.fetch_error.emit(f"Mesoanalysis {product_id}: {_friendly_error(exc)}")

    def _fetch_overlay_worker(self, product_id: str, time_id: str) -> None:
        if not _is_known_product(product_id) or not time_id:
            return
        try:
            product_base = urljoin(self._base_url + "/", f"{product_id}/{time_id}/")
            metadata = _fetch_json(urljoin(product_base, "metadata.json"))
            source_bounds = _parse_bounds(metadata.get("bounds", ""))
            bounds = _clip_to_mbtiles_bounds(source_bounds)
            source_layer = _source_layer(metadata, product_id, time_id)
            if len(bounds) != 4 or not source_layer:
                raise RuntimeError("metadata missing bounds or vector layer")

            tile_url = urljoin(product_base, "{z}/{x}/{y}.pbf")
            metadata.update({
                "product": product_id,
                "label": _product_label(product_id),
                "time": time_id,
                "time_label": _time_label(time_id),
                "tile_url": tile_url,
                "source_layer": source_layer,
                "bounds": bounds,
                "source_bounds": source_bounds,
                "label_units": _product_units(product_id),
                "minzoom": int(metadata.get("minzoom") or 0),
                "maxzoom": int(metadata.get("maxzoom") or 8),
            })
            self.overlay_ready.emit(metadata)
        except Exception as exc:
            log.warning("Mesoanalysis overlay fetch failed: %s", exc)
            self.fetch_error.emit(f"Mesoanalysis {product_id}: {_friendly_error(exc)}")


def _is_known_product(product_id: str) -> bool:
    return product_id in {pid for pid, _label in MESOANALYSIS_PRODUCTS}


def _product_label(product_id: str) -> str:
    return dict(MESOANALYSIS_PRODUCTS).get(product_id, product_id.upper())


def _product_units(product_id: str) -> str:
    return MESOANALYSIS_PRODUCT_UNITS.get(product_id, "")


def _fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_bounds(bounds_text: object) -> list[float]:
    if isinstance(bounds_text, (list, tuple)) and len(bounds_text) == 4:
        return [float(v) for v in bounds_text]
    parts = str(bounds_text or "").split(",")
    if len(parts) != 4:
        return []
    return [float(part.strip()) for part in parts]


def _source_layer(metadata: dict, product_id: str, time_id: str) -> str:
    try:
        tile_json = json.loads(str(metadata.get("json") or "{}"))
        layers = tile_json.get("vector_layers") or []
        if layers and layers[0].get("id"):
            return str(layers[0]["id"])
    except Exception:
        pass
    return f"{product_id}_{time_id}geojson"


@lru_cache(maxsize=1)
def _load_mbtiles_bounds() -> tuple[float, float, float, float] | None:
    from core import mbtiles      # read-only: never creates a missing file
    return mbtiles.bounds(_MBTILES_PATH)


def _clip_to_mbtiles_bounds(bounds: list[float]) -> list[float]:
    if len(bounds) != 4:
        return bounds
    mbtiles_bounds = _load_mbtiles_bounds()
    if not mbtiles_bounds:
        return bounds
    west = max(float(bounds[0]), mbtiles_bounds[0])
    south = max(float(bounds[1]), mbtiles_bounds[1])
    east = min(float(bounds[2]), mbtiles_bounds[2])
    north = min(float(bounds[3]), mbtiles_bounds[3])
    if west >= east or south >= north:
        return bounds
    return [west, south, east, north]


def _parse_time_id(time_id: str) -> datetime | None:
    try:
        return datetime.strptime(time_id, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _time_label(time_id: str) -> str:
    ts = _parse_time_id(time_id)
    if ts is None:
        return time_id
    return ts.strftime("%H:%MZ")


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, URLError):
        return str(exc.reason)
    return str(exc)
