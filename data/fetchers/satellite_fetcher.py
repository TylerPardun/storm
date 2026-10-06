
import base64
import logging
import math
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from data import endpoints
log = logging.getLogger(__name__)

IEM_WMS       = endpoints.IEM_GOES_EAST_WMS
IEM_CAPS_URL  = IEM_WMS + "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetCapabilities"

NOWCOAST_WMS      = endpoints.NOWCOAST_SATELLITE_WMS
NOWCOAST_CAPS_URL = NOWCOAST_WMS + "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"

CAPS_POLL_MS = 5 * 60 * 1000   # 5 min — matches CONUS scan cadence
MESO_POLL_MS =      60 * 1000  # 1 min — matches MESO scan cadence

MAX_FRAMES      = 10
REQUEST_TIMEOUT = 20

# fixed CONUS image extent — used as nominal bbox for time-only SatFrames.
CONUS_BBOX = [-116.0, 28.0, -82.0, 49.0]

# meso images are square (sector ≈ 1000×1000 km)
MESO_W, MESO_H = 2048, 2048


@dataclass
class SatFrame:
    timestamp: datetime
    b64:       str          # empty string for CONUS (tile-based); PNG for MESO
    bbox:      list         # [west, south, east, north]

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%MZ")

    @property
    def time_iso(self) -> str:
        """ISO 8601 UTC string for WMS TIME parameter."""
        return self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SatelliteFetcher(QObject):
    """
    Background poller for satellite imagery.

    Signals:
        meso_sectors_updated(object)  — {1: bbox_or_None, 2: bbox_or_None}
        frames_updated(str, object)   — (mode, list[SatFrame]) when the cache
                                        changes for that mode.
            mode is one of "conus", "meso1", "meso2".

    CONUS frames have b64='' — the map uses a MapLibre raster tile source
    pointed at nowCOAST WMS with the frame's TIME value.
    MESO frames have b64 set to the PNG data.
    """

    meso_sectors_updated = pyqtSignal(object)        # dict
    frames_updated       = pyqtSignal(str, object)   # mode, list[SatFrame]
    fetch_error          = pyqtSignal(str)           # error message

    def __init__(self, parent=None):
        super().__init__(parent)

        self._meso_bboxes: dict[int, dict | None] = {1: None, 2: None}
        self._frames:      dict[str, list]         = {"conus": [], "meso1": [], "meso2": []}
        self._lock = threading.Lock()

        # per-key inflight guard so parallel polls don't stack up
        self._inflight: dict[str, bool] = {
            "caps": False, "meso1": False, "meso2": False,
            "meso1_hist": False, "meso2_hist": False,
        }

        self._caps_timer = QTimer(self)
        self._meso_timer = QTimer(self)

        self._caps_timer.timeout.connect(self._poll_caps)
        self._meso_timer.timeout.connect(self._poll_meso)


    def start(self):
        self._caps_timer.start(CAPS_POLL_MS)
        self._meso_timer.start(MESO_POLL_MS)

        # stagger initial fetches to avoid simultaneous TLS handshakes
        QTimer.singleShot(2_000, self._poll_caps)
        log.info("SatelliteFetcher: started")

    def stop(self):
        for t in (self._caps_timer, self._meso_timer):
            t.stop()

    def fetch_now(self, mode: str = ""):
        """Trigger an immediate fetch — pass mode="conus"/"meso1"/"meso2" or
        empty string to refresh everything."""
        targets = [mode] if mode else ["caps", "meso"]
        for m in targets:
            if m in ("caps", "conus"):
                QTimer.singleShot(0, self._poll_caps)
            elif m in ("meso", "meso1", "meso2"):
                QTimer.singleShot(0, self._poll_meso)

    def fetch_history(self, mode: str, count: int = MAX_FRAMES):
        """Backfill recent frames for a mode.
        CONUS: triggers a caps refresh (nowCOAST TIME list is the history).
        MESO:  fetches recent frames from IEM."""
        mode = (mode or "").strip().lower()
        if mode not in ("conus", "meso1", "meso2"):
            return
        if mode == "conus":
            # conus history comes from nowCOAST caps TIME dimension
            self._spawn("caps", self._worker_caps)
            return
        count = max(1, min(int(count), MAX_FRAMES))
        key = f"{mode}_hist"
        self._spawn(key, lambda: self._worker_history(mode, count))

    def frames(self, mode: str) -> list:
        """Return a snapshot of the cached frames for the given mode."""
        with self._lock:
            return list(self._frames.get(mode, []))


    def _poll_caps(self):
        self._spawn("caps", self._worker_caps)

    def _poll_meso(self):
        self._spawn("meso1", lambda: self._worker_meso(1))
        self._spawn("meso2", lambda: self._worker_meso(2))

    def _spawn(self, key: str, fn):
        with self._lock:
            if self._inflight.get(key):
                return
            self._inflight[key] = True
        threading.Thread(target=self._guarded(key, fn), daemon=True).start()

    def _guarded(self, key: str, fn):
        def _run():
            try:
                fn()
            except Exception as exc:
                log.warning("SatelliteFetcher[%s]: %s", key, exc)
                self.fetch_error.emit(str(exc))
            finally:
                with self._lock:
                    self._inflight[key] = False
        return _run


    def _worker_caps(self):
        with urlopen(IEM_CAPS_URL, timeout=REQUEST_TIMEOUT) as resp:
            iem_xml = resp.read()
        iem_root = ET.fromstring(iem_xml.decode("utf-8", errors="replace"))
        sectors  = _parse_meso_bboxes(iem_root)
        with self._lock:
            self._meso_bboxes = sectors
        self.meso_sectors_updated.emit(sectors)
        log.debug(
            "SatelliteFetcher: IEM caps MESO-1=%s  MESO-2=%s",
            sectors.get(1), sectors.get(2),
        )

        try:
            with urlopen(NOWCOAST_CAPS_URL, timeout=REQUEST_TIMEOUT) as resp:
                nc_xml = resp.read()
            nc_root = ET.fromstring(nc_xml.decode("utf-8", errors="replace"))
            times   = _parse_nowcoast_times(nc_root)
            if times:
                frames = [
                    SatFrame(timestamp=_parse_time(t), b64="", bbox=list(CONUS_BBOX))
                    for t in times[-MAX_FRAMES:]
                ]
                with self._lock:
                    self._frames["conus"] = frames
                self.frames_updated.emit("conus", list(frames))
                log.debug(
                    "SatelliteFetcher: nowCOAST CONUS times=%d (latest: %s)",
                    len(times), times[-1],
                )
        except Exception as exc:
            log.warning("SatelliteFetcher: nowCOAST caps error: %s", exc)

    def _worker_meso(self, idx: int):
        with self._lock:
            bbox = self._meso_bboxes.get(idx)
        if not bbox:
            return
        w = bbox["west"]; s = bbox["south"]; e = bbox["east"]; n = bbox["north"]
        layer = f"mesoscale-{idx}_ch02"
        url = _iem_wms_url(layer, w, s, e, n, MESO_W, MESO_H)
        b64 = self._fetch_image(url)
        if b64:
            self._push_frame(f"meso{idx}", SatFrame(
                timestamp=datetime.now(timezone.utc),
                b64=b64,
                bbox=[w, s, e, n],
            ))

    def _worker_history(self, mode: str, count: int):
        """Backfill MESO frames (CONUS history is handled via _worker_caps)."""
        idx   = 1 if mode == "meso1" else 2
        with self._lock:
            bbox = self._meso_bboxes.get(idx)

        if not bbox:
            # caps may not have arrived yet — fetch them first.
            try:
                self._worker_caps()
            except Exception as exc:
                log.warning("SatelliteFetcher: caps fetch in history worker failed: %s", exc)
            with self._lock:
                bbox = self._meso_bboxes.get(idx)

        if not bbox:
            self._worker_meso(idx)
            return

        # iem has no TIME dimension, so just fetch the latest frame as a fallback.
        self._worker_meso(idx)

    def _fetch_image(self, url: str) -> str:
        """Download a WMS GetMap PNG and return it base64-encoded, or "" on error."""
        try:
            req = Request(url, headers={"User-Agent": "STORM/1.0"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                ct = resp.headers.get("Content-Type", "")
                if "image" not in ct:
                    log.warning("SatelliteFetcher: unexpected Content-Type: %s", ct)
                    return ""
                data = resp.read()
            return base64.b64encode(data).decode("ascii")
        except Exception as exc:
            log.warning("SatelliteFetcher: image fetch error: %s", exc)
            return ""

    def _push_frame(self, mode: str, frame: SatFrame):
        with self._lock:
            cache = self._frames.setdefault(mode, [])
            cache.append(frame)
            if len(cache) > MAX_FRAMES:
                cache.pop(0)
            frames_copy = list(cache)
        self.frames_updated.emit(mode, frames_copy)
        log.debug(
            "SatelliteFetcher[%s]: cached %s (%d/%d)",
            mode, frame.time_str, len(frames_copy), MAX_FRAMES,
        )



def _parse_meso_bboxes(root: ET.Element) -> dict:
    _strip_ns(root)
    sectors: dict = {1: None, 2: None}
    for layer in root.iter("Layer"):
        name_el = layer.find("Name")
        bbox_el  = layer.find("LatLonBoundingBox")
        if name_el is None or bbox_el is None:
            continue
        name = (name_el.text or "").strip()
        if name == "mesoscale-1_ch02":
            idx = 1
        elif name == "mesoscale-2_ch02":
            idx = 2
        else:
            continue
        try:
            sectors[idx] = {
                "west":  float(bbox_el.attrib["minx"]),
                "south": float(bbox_el.attrib["miny"]),
                "east":  float(bbox_el.attrib["maxx"]),
                "north": float(bbox_el.attrib["maxy"]),
            }
        except (KeyError, ValueError):
            pass
    return sectors


def _parse_nowcoast_times(root: ET.Element) -> list[str]:
    """Return ISO time strings for goes_visible_imagery from nowCOAST WMS 1.3.0 caps."""
    _strip_ns(root)

    def _direct_time(layer_el: ET.Element) -> str:
        for dim in layer_el.findall("Dimension"):
            if (dim.attrib.get("name") or "").lower() == "time":
                return (dim.text or "").strip()
        return ""

    def _walk(layer_el: ET.Element, inherited: str) -> list[str]:
        time_text = _direct_time(layer_el) or inherited
        name_el   = layer_el.find("Name")
        if name_el is not None and (name_el.text or "").strip() == "goes_visible_imagery":
            if time_text:
                return [t.strip() for t in time_text.split(",") if t.strip()]
        for child in layer_el.findall("Layer"):
            result = _walk(child, time_text)
            if result:
                return result
        return []

    cap = root.find("Capability")
    top = cap if cap is not None else root
    for layer in top.findall("Layer"):
        result = _walk(layer, "")
        if result:
            return result
    return []


def _parse_time(tstr: str) -> datetime:
    s = (tstr or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


_MERC_R = 6378137.0
_MERC_LAT_LIMIT = 85.05112878


def _lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-_MERC_LAT_LIMIT, min(_MERC_LAT_LIMIT, lat))
    x = math.radians(lon) * _MERC_R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _MERC_R
    return x, y


def _iem_wms_url(layer: str, west: float, south: float, east: float, north: float,
                 width: int, height: int, time_str: str | None = None) -> str:
    # render in Web Mercator so the pixel grid matches the MapLibre basemap.
    minx, miny = _lonlat_to_merc(west, south)
    maxx, maxy = _lonlat_to_merc(east, north)
    base = (
        f"{IEM_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        f"&LAYERS={layer}&SRS=EPSG:3857"
        f"&BBOX={minx},{miny},{maxx},{maxy}&WIDTH={width}&HEIGHT={height}"
        f"&FORMAT=image/png&TRANSPARENT=TRUE&STYLES="
    )
    if time_str:
        base += f"&TIME={time_str}"
    return base


def _strip_ns(el: ET.Element):
    if "}" in el.tag:
        el.tag = el.tag.split("}", 1)[1]
    for child in el:
        _strip_ns(child)
