
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

import requests
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_S3_URL_TEMPLATE = "https://{bucket}.s3.amazonaws.com"
_GOES19_EAST_START = datetime(2025, 4, 4, 15, 0, tzinfo=timezone.utc)
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
        self._indexes_ready = False
        self._indexed_modes: set[str] = set()
        self._indexing_modes: set[str] = set()
        self._cache: dict[tuple[str, datetime], Optional[SatFrame]] = {}
        self._pending: set[tuple[str, datetime]] = set()
        self._fetch_lock = threading.Lock()
        self._current_archive_time: Optional[datetime] = None
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

    def on_time_changed(self, archive_time: datetime) -> None:
        self._current_archive_time = _ensure_utc(archive_time)
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
            self._indexes_ready = bool(self._indexed_modes)

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
                    f"No GOES-East archive imagery available for {self._date.strftime('%Y-%m-%d')}"
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
        base_url = _S3_URL_TEMPLATE.format(bucket=self._bucket)
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
                resp = requests.get(f"{base_url}/{params}", timeout=_REQUEST_TIMEOUT)
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
        threading.Thread(target=self._fetch_frame, args=(self._mode, ref), daemon=True).start()

    def _fetch_frame(self, mode: str, ref: _FrameRef) -> None:
        cache_key = (mode, ref.timestamp)
        try:
            url = f"{_S3_URL_TEMPLATE.format(bucket=self._bucket)}/{ref.key}"
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
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
            url = f"{_S3_URL_TEMPLATE.format(bucket=self._bucket)}/{key}"
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
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
    return "noaa-goes19" if dt >= _GOES19_EAST_START else "noaa-goes16"


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


def _render_goes_png(
    nc_bytes: bytes,
    mode: str,
    fallback_bbox: Optional[list[float]] = None,
    image_only: bool = True,
) -> tuple[bytes, list[float]] | tuple[None, list[float]]:
    try:
        import cartopy.crs as ccrs
        import matplotlib
        matplotlib.use("Agg")
        import numpy as np
        import xarray as xr
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib import colormaps
    except ImportError as exc:
        raise RuntimeError(
            "Archive GOES rendering requires xarray, matplotlib, and cartopy in the STORM environment"
        ) from exc

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
        data = np.clip(data, 0.0, 1.0)
        # apply gamma correction to match the visual brightness of live WMS tiles.
        data = np.where(np.isfinite(data), np.power(data, 0.5), np.nan)

        proj_var = ds["goes_imager_projection"]
        sat_height = float(proj_var.attrs["perspective_point_height"])
        lon0 = float(proj_var.attrs["longitude_of_projection_origin"])
        sweep = str(proj_var.attrs.get("sweep_angle_axis", "x"))
        semi_major = float(proj_var.attrs["semi_major_axis"])
        semi_minor = float(proj_var.attrs["semi_minor_axis"])
        x = np.asarray(ds["x"], dtype="float64") * sat_height
        y = np.asarray(ds["y"], dtype="float64") * sat_height

        bbox = _dataset_bbox(ds, fallback_bbox)
        if not image_only:
            return None, bbox

        west, south, east, north = bbox
        lon_span = max(1.0, east - west)
        lat_span = max(1.0, north - south)
        width_px = 1500 if mode == "conus" else 1100
        height_px = max(700, int(width_px * (lat_span / lon_span)))
        dpi = 100

        fig = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        fig.patch.set_alpha(0.0)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_axes([0, 0, 1, 1], projection=ccrs.PlateCarree())
        ax.set_extent([west, east, south, north], crs=ccrs.PlateCarree())
        ax.set_axis_off()

        cmap = colormaps["gray"].copy()
        cmap.set_bad((0, 0, 0, 0))
        globe = ccrs.Globe(
            semimajor_axis=semi_major,
            semiminor_axis=semi_minor,
            ellipse=None,
        )
        geos = ccrs.Geostationary(
            central_longitude=lon0,
            satellite_height=sat_height,
            sweep_axis=sweep,
            globe=globe,
        )
        ax.imshow(
            data,
            origin="upper",
            extent=[x.min(), x.max(), y.min(), y.max()],
            transform=geos,
            cmap=cmap,
            vmin=0.0,
            vmax=1.0,
            interpolation="bilinear",
        )

        buf = io.BytesIO()
        canvas.print_png(buf)
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
