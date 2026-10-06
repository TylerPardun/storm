
import base64
import io
import logging
import os
import re
import tempfile
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from PyQt6.QtCore import QObject, pyqtSignal
from core import package_sources

from data import endpoints
log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"_s(?P<year>\d{4})(?P<jday>\d{3})(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})")

_MODE_CONFIG = {
    "conus": {
        "product": "ABI-L2-CMIPC",
        "label": "CONUS",
        "fixed_bbox": [-116.0, 28.0, -82.0, 49.0],
    },
    "meso1": {
        "product": "ABI-L2-CMIPM",
        "label": "MESO-1",
        "sector_token": "CMIPM1",
    },
    "meso2": {
        "product": "ABI-L2-CMIPM",
        "label": "MESO-2",
        "sector_token": "CMIPM2",
    },
}
_MAX_CACHE = 24
_REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class _FrameRef:
    timestamp: datetime
    key: str


class SatFrame:
    __slots__ = ("mode", "timestamp", "b64", "bbox")

    def __init__(self, mode: str, timestamp: datetime, b64: str, bbox: list[float]):
        self.mode = mode
        self.timestamp = timestamp
        self.b64 = b64
        self.bbox = bbox

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%MZ")

    @property
    def time_iso(self) -> str:
        return self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ArchiveSatelliteFetcher(QObject):
    """
    Historical GOES-East visible imagery backed by NOAA's public AWS archive.

    Supported modes:
      - conus  -> ABI-L2-CMIPC channel 02
      - meso1  -> ABI-L2-CMIPM sector M1 channel 02
      - meso2  -> ABI-L2-CMIPM sector M2 channel 02
    """

    frame_ready = pyqtSignal(object)          # SatFrame
    capabilities_loaded = pyqtSignal(list)    # list[str] for current mode
    loading_changed = pyqtSignal(bool)
    error = pyqtSignal(str)
    meso_sectors_updated = pyqtSignal(object)  # {1: bbox|None, 2: bbox|None}

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = _ensure_utc(session_date)
        self._bucket = _bucket_for_date(self._date)
        self._mode = ""   # no mode until user explicitly selects one
        self._indexes: dict[str, list[_FrameRef]] = {mode: [] for mode in _MODE_CONFIG}
        self._indexed_modes: set[str] = set()
        self._indexing_modes: set[str] = set()
        self._cache: dict[tuple[str, datetime], Optional[SatFrame]] = {}
        self._pending: set[tuple[str, datetime]] = set()
        self._fetch_lock = threading.Lock()
        self._current_archive_time: Optional[datetime] = None
        # Frames are fetched only while the satellite layer is on: each is an
        # S3 download plus a few hundred MB to warp into the map's projection,
        # and they were being made on every clock change for a hidden layer.
        self._active = False
        # one frame at a time; a request the clock has already moved past is skipped
        from concurrent.futures import ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goes-frame")
        self._meso_bboxes: dict[int, Optional[dict]] = {1: None, 2: None}


    def load_capabilities(self) -> None:
        # index all modes at startup so meso buttons are responsive immediately.
        for mode in _MODE_CONFIG:
            self._ensure_mode_index(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in _MODE_CONFIG:
            return
        self._mode = mode
        self._ensure_mode_index(mode)
        if mode in self._indexed_modes:
            self.capabilities_loaded.emit(self._mode_times_iso())
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def set_active(self, active: bool) -> None:
        """The satellite layer is on (fetch frames) or off (don't)."""
        self._active = active
        if active and self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def on_time_changed(self, archive_time: datetime) -> None:
        self._current_archive_time = _ensure_utc(archive_time)
        if not self._active:
            return
        ref = self._nearest_ref(self._mode, self._current_archive_time)
        if ref is None:
            return
        cache_key = (self._mode, ref.timestamp)
        frame = self._cache.get(cache_key)
        if frame is not None:
            self.frame_ready.emit(frame)
            return
        self._ensure_fetched(ref)


    def _ensure_mode_index(self, mode: str) -> None:
        if mode in self._indexed_modes or mode in self._indexing_modes:
            return
        self._indexing_modes.add(mode)
        threading.Thread(target=self._build_mode_index, args=(mode,), daemon=True).start()

    def _build_mode_index(self, mode: str) -> None:
        try:
            cfg = _MODE_CONFIG[mode]
            refs = self._list_day_refs(mode, cfg["product"])
            self._indexes[mode] = refs
            self._indexed_modes.add(mode)

            if refs and mode in ("meso1", "meso2"):
                # emit now (bbox still None) so the UI immediately enables the buttons.
                self.meso_sectors_updated.emit(dict(self._meso_bboxes))
                threading.Thread(
                    target=self._fetch_mode_bbox,
                    args=(mode, refs[-1].key),
                    daemon=True,
                ).start()

            if not refs and mode == "conus":
                self.error.emit(
                    f"No GOES-East imagery for {self._date:%Y-%m-%d}: the GOES-16 archive begins in 2017"
                    if self._date.year < 2017 else
                    f"No GOES-East archive imagery available for {self._date:%Y-%m-%d}"
                )
                self.capabilities_loaded.emit([])
                return

            if mode == self._mode:
                self.capabilities_loaded.emit(self._mode_times_iso())
            if self._current_archive_time is not None and mode == self._mode:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: index build failed: %s", exc)
            self.error.emit(f"Satellite index failed: {exc}")
        finally:
            self._indexing_modes.discard(mode)

    def _fetch_mode_bbox(self, mode: str, key: str) -> None:
        """Background: download one file's bbox so the map hover preview works."""
        idx = 1 if mode == "meso1" else 2
        bbox = self._read_bbox_for_key(mode, key)
        if bbox is not None:
            self._meso_bboxes[idx] = bbox
            self.meso_sectors_updated.emit(dict(self._meso_bboxes))

    def _list_day_refs(self, mode: str, product: str) -> list[_FrameRef]:
        refs: list[_FrameRef] = []
        base_url = endpoints.s3_bucket_url(self._bucket)
        sector_token = _MODE_CONFIG[mode].get("sector_token")

        # the session's UTC day plus the next morning up to its cap
        # (archive/session.py); the next day can be in a new year.
        from archive.session import session_bounds
        start, cap = session_bounds(self._date)
        hours = []
        hour_start = start
        while hour_start < cap:
            hours.append(hour_start)
            hour_start += timedelta(hours=1)

        for hour_start in hours:
            prefix = f"{product}/{hour_start:%Y}/{hour_start:%j}/{hour_start:%H}/"
            continuation = None
            while True:
                params = f"?list-type=2&prefix={quote(prefix)}"
                if continuation:
                    params += f"&continuation-token={quote(continuation)}"
                resp = package_sources.requests_get("satellite", f"{base_url}/{params}", timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                for key_el in root.findall(".//{*}Contents/{*}Key"):
                    key = (key_el.text or "").strip()
                    if not key.endswith(".nc"):
                        continue
                    if "C02" not in key:
                        continue
                    if sector_token and sector_token not in key:
                        continue
                    ts = _timestamp_from_key(key)
                    if ts is None:
                        continue
                    refs.append(_FrameRef(timestamp=ts, key=key))
                token_el = root.find("{*}NextContinuationToken")
                if token_el is None:
                    break
                continuation = token_el.text

        refs = [ref for ref in refs if ref.timestamp <= cap]
        refs.sort(key=lambda item: item.timestamp)
        log.info("ArchiveSatelliteFetcher: indexed %s %d frames", product, len(refs))
        return refs

    def _mode_times_iso(self) -> list[str]:
        return [
            ref.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            for ref in self._indexes.get(self._mode, [])
        ]

    def _nearest_ref(self, mode: str, when: datetime) -> Optional[_FrameRef]:
        refs = self._indexes.get(mode, [])
        if not refs:
            return None
        lo, hi = 0, len(refs) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if refs[mid].timestamp <= when:
                result = refs[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return result or refs[0]


    def _ensure_fetched(self, ref: _FrameRef) -> None:
        cache_key = (self._mode, ref.timestamp)
        with self._fetch_lock:
            if cache_key in self._pending or self._cache.get(cache_key) is not None:
                return
            self._pending.add(cache_key)
        self.loading_changed.emit(True)
        self._executor.submit(self._fetch_frame, self._mode, ref)

    def _fetch_frame(self, mode: str, ref: _FrameRef) -> None:
        cache_key = (mode, ref.timestamp)
        if (not self._active or mode != self._mode or self._current_archive_time is None
                or self._nearest_ref(mode, self._current_archive_time) != ref):
            with self._fetch_lock:                   # the clock moved on (or the layer went off) while queued
                self._pending.discard(cache_key)
            self.loading_changed.emit(bool(self._pending))
            return
        try:
            url = f"{endpoints.s3_bucket_url(self._bucket)}/{ref.key}"
            resp = package_sources.requests_get("satellite", url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            png_bytes, bbox = _render_goes_png(resp.content, mode, _MODE_CONFIG[mode].get("fixed_bbox"))
            frame = SatFrame(
                mode=mode,
                timestamp=ref.timestamp,
                b64=base64.b64encode(png_bytes).decode("ascii"),
                bbox=bbox,
            )
            self._cache[cache_key] = frame
            while len(self._cache) > _MAX_CACHE:
                oldest_key = min(self._cache.keys(), key=lambda item: item[1])
                del self._cache[oldest_key]

            if mode in ("meso1", "meso2"):
                idx = 1 if mode == "meso1" else 2
                bbox_dict = _bbox_to_dict(bbox)
                if self._meso_bboxes.get(idx) != bbox_dict:
                    self._meso_bboxes[idx] = bbox_dict
                    self.meso_sectors_updated.emit(dict(self._meso_bboxes))

            if mode == self._mode and self._current_archive_time is not None:
                nearest = self._nearest_ref(mode, self._current_archive_time)
                if nearest is not None and nearest.timestamp == ref.timestamp:
                    self.frame_ready.emit(frame)
        except Exception as exc:
            log.error("ArchiveSatelliteFetcher: frame fetch failed %s %s: %s", mode, ref.timestamp, exc)
            self.error.emit(f"Satellite frame failed: {exc}")
        finally:
            with self._fetch_lock:
                self._pending.discard(cache_key)
                if not self._pending:
                    self.loading_changed.emit(False)

    def _read_bbox_for_key(self, mode: str, key: str) -> Optional[dict]:
        try:
            url = f"{endpoints.s3_bucket_url(self._bucket)}/{key}"
            resp = package_sources.requests_get("satellite", url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            _, bbox = _render_goes_png(resp.content, mode, _MODE_CONFIG[mode].get("fixed_bbox"), image_only=False)
            return _bbox_to_dict(bbox)
        except Exception as exc:
            log.warning("ArchiveSatelliteFetcher: could not read bbox for %s: %s", mode, exc)
            return None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket_for_date(dt: datetime) -> str:
    return endpoints.goes_east_bucket(dt)


def _timestamp_from_key(key: str) -> Optional[datetime]:
    match = _TIME_RE.search(key)
    if not match:
        return None
    year = int(match.group("year"))
    jday = int(match.group("jday"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=jday - 1)
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _bbox_to_dict(bbox: list[float]) -> dict:
    west, south, east, north = bbox
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
    }


# GOES Cloud and Moisture Imagery: the infrared bands (7-16) are brightness
# temperature in kelvin, the visible/near-IR ones (1-6) reflectance (0-1).
IR_COLD_K, IR_WARM_K = 190.0, 310.0          # cold cloud tops white, warm ground dark


def _goes_gray(data, units: str):
    """0..1 brightness for display (NaN where there's no data). Treating band
    13's kelvins as 0..1 reflectance had clipped every pixel to 1."""
    import numpy as np
    if units.strip().upper() == "K":
        return np.clip((IR_WARM_K - data) / (IR_WARM_K - IR_COLD_K), 0.0, 1.0)
    return np.power(np.clip(data, 0.0, 1.0), 0.5)          # visible: gamma, as before


def _goes_to_map_image(gray, x_rad, y_rad, *, lon0, sat_height, semi_major, semi_minor, sweep,
                       bbox, width_px, height_px):
    """The scan (fixed-grid scan angles x_rad, y_rad) resampled onto the map
    image STORM shows: bbox [w, s, e, n], rows even in Web Mercator (how the
    map stretches an image between its corners), nearest scan pixel. Uses the
    GOES-R fixed-grid formulas (PUG vol. 3, 4.2.8) directly, so it needs only
    output-sized arrays (cartopy's warp took ~750 MB a frame)."""
    import numpy as np
    west, south, east, north = bbox
    lon = np.deg2rad(np.linspace(west, east, width_px, endpoint=False) + (east - west) / width_px / 2)

    def merc(lat_deg):
        return np.log(np.tan(np.pi / 4 + np.deg2rad(lat_deg) / 2))
    ym = np.linspace(merc(north), merc(south), height_px, endpoint=False) + (merc(south) - merc(north)) / height_px / 2
    lat = 2 * np.arctan(np.exp(ym)) - np.pi / 2

    r_eq, r_pol = semi_major, semi_minor
    H = sat_height + r_eq
    lat_c = np.arctan((r_pol ** 2 / r_eq ** 2) * np.tan(lat))[:, None]          # (rows, 1)
    e2 = (r_eq ** 2 - r_pol ** 2) / r_eq ** 2
    r_c = r_pol / np.sqrt(1 - e2 * np.cos(lat_c) ** 2)
    dlon = (lon - np.deg2rad(lon0))[None, :]                                      # (1, cols)
    sx = H - r_c * np.cos(lat_c) * np.cos(dlon)
    sy = -r_c * np.cos(lat_c) * np.sin(dlon)
    sz = r_c * np.sin(lat_c) * np.ones_like(dlon)
    visible = H * (H - sx) >= sy ** 2 + (r_eq ** 2 / r_pol ** 2) * sz ** 2
    if sweep == "x":
        xs = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
        ys = np.arctan(sz / sx)
    else:                                                    # sweep "y" (Himawari-style)
        xs = np.arctan(-sy / sx)
        ys = np.arcsin(sz / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
    del sx, sy, sz
    col = np.rint((xs - x_rad[0]) / (x_rad[1] - x_rad[0])).astype(np.int64)
    row = np.rint((ys - y_rad[0]) / (y_rad[1] - y_rad[0])).astype(np.int64)
    inside = visible & (col >= 0) & (col < gray.shape[1]) & (row >= 0) & (row < gray.shape[0])
    value = np.full((height_px, width_px), np.nan, dtype=np.float32)
    value[inside] = gray[row[inside], col[inside]]
    rgba = np.zeros((height_px, width_px, 4), dtype=np.uint8)
    good = np.isfinite(value)
    level = np.rint(value[good] * 255).astype(np.uint8)
    rgba[good, 0] = rgba[good, 1] = rgba[good, 2] = level
    rgba[good, 3] = 255
    return rgba


def _render_goes_png(
    nc_bytes: bytes,
    mode: str,
    fallback_bbox: Optional[list[float]] = None,
    image_only: bool = True,
) -> tuple[bytes, list[float]] | tuple[None, list[float]]:
    try:
        import numpy as np
        import xarray as xr
    except ImportError as exc:
        raise RuntimeError("Archive GOES rendering requires numpy and xarray in the STORM environment") from exc

    tmp_path = None
    ds = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
            tmp.write(nc_bytes)
            tmp_path = tmp.name

        try:
            ds = xr.open_dataset(tmp_path, engine="h5netcdf", mask_and_scale=True)
        except Exception:
            ds = xr.open_dataset(tmp_path, mask_and_scale=True)

        data = np.asarray(ds["CMI"]).astype("float32")
        fill = ds["CMI"].attrs.get("_FillValue")
        if fill is not None:
            data[data == fill] = np.nan
        data[~np.isfinite(data)] = np.nan
        data = _goes_gray(data, str(ds["CMI"].attrs.get("units", "")))

        proj_var = ds["goes_imager_projection"]
        sat_height = float(proj_var.attrs["perspective_point_height"])
        lon0 = float(proj_var.attrs["longitude_of_projection_origin"])
        sweep = str(proj_var.attrs.get("sweep_angle_axis", "x"))
        semi_major = float(proj_var.attrs["semi_major_axis"])
        semi_minor = float(proj_var.attrs["semi_minor_axis"])
        bbox = _dataset_bbox(ds, fallback_bbox)
        if not image_only:
            return None, bbox

        west, south, east, north = bbox
        lon_span = max(1.0, east - west)
        lat_span = max(1.0, north - south)
        width_px = 1500 if mode == "conus" else 1100
        height_px = max(700, int(width_px * (lat_span / lon_span)))
        rgba = _goes_to_map_image(
            data, np.asarray(ds["x"], dtype="float64"), np.asarray(ds["y"], dtype="float64"),
            lon0=lon0, sat_height=sat_height, semi_major=semi_major, semi_minor=semi_minor, sweep=sweep,
            bbox=bbox, width_px=width_px, height_px=height_px)
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=False)
        return buf.getvalue(), bbox
    finally:
        if ds is not None:
            ds.close()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _dataset_bbox(ds, fallback_bbox: Optional[list[float]]) -> list[float]:
    attrs = ds.attrs
    keys = (
        "geospatial_westbound_longitude",
        "geospatial_southbound_latitude",
        "geospatial_eastbound_longitude",
        "geospatial_northbound_latitude",
    )
    if all(key in attrs for key in keys):
        return [
            float(attrs["geospatial_westbound_longitude"]),
            float(attrs["geospatial_southbound_latitude"]),
            float(attrs["geospatial_eastbound_longitude"]),
            float(attrs["geospatial_northbound_latitude"]),
        ]
    if fallback_bbox:
        return list(fallback_bbox)

    # meso sector files don't carry geospatial_* attributes — derive the bbox
    try:
        import cartopy.crs as ccrs
        import numpy as np
        proj_var   = ds["goes_imager_projection"]
        sat_height = float(proj_var.attrs["perspective_point_height"])
        lon0       = float(proj_var.attrs["longitude_of_projection_origin"])
        sweep      = str(proj_var.attrs.get("sweep_angle_axis", "x"))
        semi_major = float(proj_var.attrs["semi_major_axis"])
        semi_minor = float(proj_var.attrs["semi_minor_axis"])
        x = np.asarray(ds["x"], dtype="float64") * sat_height
        y = np.asarray(ds["y"], dtype="float64") * sat_height

        globe = ccrs.Globe(semimajor_axis=semi_major, semiminor_axis=semi_minor, ellipse=None)
        geos  = ccrs.Geostationary(
            central_longitude=lon0,
            satellite_height=sat_height,
            sweep_axis=sweep,
            globe=globe,
        )
        pc = ccrs.PlateCarree()
        lons, lats = [], []
        for xi in (x.min(), x.max()):
            for yi in (y.min(), y.max()):
                try:
                    pt = pc.transform_point(xi, yi, geos)
                    if np.isfinite(pt[0]) and np.isfinite(pt[1]):
                        lons.append(pt[0])
                        lats.append(pt[1])
                except Exception:
                    pass
        if lons and lats:
            return [min(lons), min(lats), max(lons), max(lats)]
    except Exception as exc:
        log.warning("_dataset_bbox: projection fallback failed: %s", exc)

    raise RuntimeError("GOES file is missing geospatial bounds")
