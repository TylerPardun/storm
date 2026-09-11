
import atexit
import gzip
import io
import logging
import os
import shutil
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from core.level2_radar_scan import Level2RadarScan, L2_PRODUCTS, DEFAULT_L2_PRODUCT

log = logging.getLogger(__name__)

_S3_BASE = "https://unidata-nexrad-level2.s3.amazonaws.com"
_S3_NS   = "http://s3.amazonaws.com/doc/2006-03-01/"

# maps L2_PRODUCTS keys → MetPy Level2File moment name (plain ASCII string).
_MOMENT_MAP: dict[str, str] = {
    "reflectivity":              "REF",
    "velocity":                  "VEL",
    "spectrum_width":            "SW",
    "differential_reflectivity": "ZDR",
    "cross_correlation_ratio":   "RHO",
    "differential_phase":        "PHI",
    "specific_differential_phase": "KDP",
    "clutter_filter_power_removed": "CFP",
}

# stations not archived in the Unidata/NOAA Level-2 S3 bucket.
ARCHIVE_UNAVAILABLE_STATIONS: frozenset[str] = frozenset({
    "KOUN",   # NSSL research WSR-88D, Norman OK — not in public archive
})

BUFFER_BEFORE = 4
BUFFER_AFTER  = 4


class ArchiveRadarFetcher(QObject):
    """
    Fetches and decodes NEXRAD Level-2 archive data for a single station.

    Signals
    -------
    scan_ready(Level2RadarScan)
        Emitted on the main thread when the scan for the current archive time
        has been decoded and is ready to render.
    index_loaded(list[str])
        Emitted once the scan-time index for the session date has been fetched.
        Payload is a list of ISO-format scan time strings.
    loading_changed(bool)
        True while a file is being downloaded or decoded.
    error(str)
        Human-readable error message.
    """

    scan_ready      = pyqtSignal(object)   # Level2RadarScan
    index_loaded    = pyqtSignal(list)     # list[str] scan time ISO strings
    loading_changed = pyqtSignal(bool)
    error           = pyqtSignal(str)

    def __init__(self, station: str, session_date: datetime, parent=None):
        """
        Parameters
        ----------
        station : str
            4-letter NEXRAD ID, e.g. "KTLX".
        session_date : datetime
            UTC datetime of the archive session (only the date portion is used).
        """
        super().__init__(parent)
        self._closed = threading.Event()
        self._signal_lock = threading.Lock()
        self._parse_lock = threading.Lock()
        self._station     = station.upper()
        self._date        = session_date
        self._product     = DEFAULT_L2_PRODUCT
        self._tilt_idx    = 0          # index into available tilts list

        # raw Level-2 files are written to a per-session tmp directory (~30 MB
        self._tmpdir = tempfile.mkdtemp(prefix="storm_radar_")
        atexit.register(shutil.rmtree, self._tmpdir, True)

        # bounded pool for fetch+decode -- _maintain_buffer can want up to
        # BUFFER_BEFORE+BUFFER_AFTER+1 (9) scans fetched at once when the
        # buffer first fills or shifts; each fetch+decode was measured
        # pushing peak process RSS by 400MB-1GB (MetPy parses every tilt/
        # moment in the file, not just the one being displayed), so letting
        # all 9 run concurrently was the dominant driver of the archive-mode
        # memory blowup. Capping this bounds the worst-case overlap instead.
        self._fetch_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="wsr88d-fetch-decode"
        )

        # maps scan_time → path of the raw .dat file in _tmpdir (None = fetch pending).
        self._raw_cache: dict[datetime, Optional[str]] = {}
        # maps scan_time → parsed metpy Level2File (binary structure only,
        # independent of product/tilt) so switching product/tilt on an
        # already-fetched scan doesn't re-parse the raw bytes.
        self._parsed_cache: dict[datetime, object] = {}
        self._decoded_cache: dict[tuple[datetime, str, int], Level2RadarScan] = {}
        # sorted list of all known scan times for the date.
        self._index: list[datetime] = []
        self._index_lock = threading.Lock()

        self._current_archive_time: Optional[datetime] = None
        self._last_emitted_key: Optional[tuple] = None  # suppress re-render of same scan
        self._fetch_lock = threading.Lock()
        self._pending_fetches: set[datetime] = set()
        self._pending_decodes: set[tuple[datetime, str, int]] = set()


    @property
    def station(self) -> str:
        return self._station

    def _emit(self, signal, *args):
        # Serialize shutdown with signal delivery; no queued updates after close.
        with self._signal_lock:
            if not self._closed.is_set():
                signal.emit(*args)

    def _submit(self, task, *args):
        try:
            self._fetch_executor.submit(task, *args)
        except RuntimeError:
            if not self._closed.is_set():
                raise

    def shutdown(self) -> None:
        with self._signal_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        self._fetch_executor.shutdown(wait=False, cancel_futures=True)
        # Running downloads may still own these files. Finish cleanup off the
        # GUI thread, after the executor releases them.
        def finish():
            self._fetch_executor.shutdown(wait=True, cancel_futures=True)
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        threading.Thread(target=finish, daemon=True, name="radar-cleanup").start()

    def set_station(self, station: str) -> None:
        self._station = station.upper()
        for path in self._raw_cache.values():
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._raw_cache.clear()
        self._parsed_cache.clear()
        self._decoded_cache.clear()
        self._index = []
        self._pending_fetches.clear()
        self._pending_decodes.clear()
        self.load_index()

    def set_product(self, pyart_field: str) -> None:
        """Switch the rendered product and reuse cached raw/decoded volumes."""
        if pyart_field == self._product:
            return
        self._product = pyart_field
        self._last_emitted_key = None
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def set_tilt_index(self, idx: int) -> None:
        """Switch the elevation tilt and reuse cached raw/decoded volumes."""
        self._tilt_idx = idx
        self._last_emitted_key = None
        if self._current_archive_time is not None:
            self.on_time_changed(self._current_archive_time)

    def load_index(self) -> None:
        """Fetch the list of available scans from AWS (background thread)."""
        if self._closed.is_set():
            return
        t = threading.Thread(target=self._fetch_index, daemon=True)
        t.start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """
        Called by TimeController whenever the archive clock advances.
        Finds the correct scan for the given time, emits it if cached,
        or triggers a background fetch if not.
        """
        if self._closed.is_set():
            return
        self._current_archive_time = archive_time
        scan_time = self._nearest_scan_before(archive_time)
        if scan_time is None:
            return
        cache_key = self._decode_key(scan_time)
        scan = self._decoded_cache.get(cache_key)
        if scan is not None:
            if cache_key != self._last_emitted_key:
                self._last_emitted_key = cache_key
                self._emit(self.scan_ready, scan)
        elif self._raw_cache.get(scan_time) is not None:
            self._ensure_decoded(scan_time)
        else:
            self._ensure_fetched(scan_time)

        # maintain buffer: pre-fetch neighbouring scans.
        self._maintain_buffer(scan_time)


    def _fetch_index(self) -> None:
        """List all files for the station/date from the AWS S3 bucket."""
        prefix = (
            f"{self._date.strftime('%Y/%m/%d')}/{self._station}/"
        )
        url = f"{_S3_BASE}/?prefix={prefix}&list-type=2"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            times = self._parse_s3_listing(resp.text)
            with self._index_lock:
                self._index = sorted(times)
            log.info(
                "ArchiveRadarFetcher: %d scans indexed for %s on %s",
                len(self._index), self._station, self._date.strftime("%Y-%m-%d"),
            )
            if not times:
                self._emit(self.error,
                    f"No archive data found for {self._station} on "
                    f"{self._date.strftime('%Y-%m-%d')} — "
                    f"station may not be in the public archive"
                )
                return
            self._emit(self.index_loaded,
                [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in self._index]
            )
            # pre-fetch the scan nearest to the current archive time.
            if self._current_archive_time and self._index:
                self.on_time_changed(self._current_archive_time)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: index fetch failed: %s", exc)
            self._emit(self.error, f"Radar index failed for {self._station}: {exc}")

    def _parse_s3_listing(self, xml_text: str) -> list:
        """Parse S3 XML listing and extract scan datetimes."""
        times = []
        try:
            root = ET.fromstring(xml_text)
            ns = _S3_NS
            for content in root.findall(f"{{{ns}}}Contents"):
                key = content.findtext(f"{{{ns}}}Key", "")
                # filename pattern: KXXX20230515_201500_V06 (and variants)
                fname = key.split("/")[-1]
                if not fname or "MDM" in fname:
                    continue
                dt = _parse_l2_filename_time(fname, self._station)
                if dt:
                    times.append(dt)
        except Exception as exc:
            log.warning("ArchiveRadarFetcher: S3 parse error: %s", exc)
        return times


    def _nearest_scan_before(self, t: datetime) -> Optional[datetime]:
        """Return the latest scan_time <= t from the index."""
        with self._index_lock:
            idx = self._index
        if not idx:
            return None
        # binary search for the last element <= t.
        lo, hi = 0, len(idx) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if idx[mid] <= t:
                result = idx[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _ensure_fetched(self, scan_time: datetime) -> None:
        """Kick off a background raw-file fetch for scan_time if needed."""
        with self._fetch_lock:
            if scan_time in self._pending_fetches:
                return
            if self._raw_cache.get(scan_time) is not None:
                return
            if self._closed.is_set():
                return
            self._pending_fetches.add(scan_time)
            self._raw_cache[scan_time] = None

        self._emit(self.loading_changed, True)
        self._submit(self._fetch_raw_then_decode, scan_time)

    def _ensure_decoded(self, scan_time: datetime) -> None:
        cache_key = self._decode_key(scan_time)
        with self._fetch_lock:
            if cache_key in self._pending_decodes or cache_key in self._decoded_cache:
                return
            if self._raw_cache.get(scan_time) is None:
                return
            if self._closed.is_set():
                return
            self._pending_decodes.add(cache_key)
        self._emit(self.loading_changed, True)
        self._submit(self._decode_cached_scan, scan_time, cache_key)

    def _fetch_raw_then_decode(self, scan_time: datetime) -> None:
        cache_key = self._decode_key(scan_time)
        decoding = False
        try:
            file_bytes = self._download_scan(scan_time)
            if file_bytes is None or self._closed.is_set():
                return
            # persist raw bytes to tmp file so later product/tilt switches
            # (after this call returns) can still find the raw scan on disk.
            path = os.path.join(
                self._tmpdir,
                f"{self._station}_{scan_time.strftime('%Y%m%d_%H%M%S')}.dat",
            )
            with open(path, "wb") as fh:
                fh.write(file_bytes)
            self._raw_cache[scan_time] = path

            with self._fetch_lock:
                if cache_key in self._decoded_cache or cache_key in self._pending_decodes:
                    return
                self._pending_decodes.add(cache_key)
                decoding = True
            # decode the bytes already in memory instead of re-reading them
            # back from the file we just wrote.
            self._decode_and_cache(scan_time, file_bytes, cache_key)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: fetch failed for %s: %s", scan_time, exc)
            self._emit(self.error, f"Radar fetch error: {exc}")
        finally:
            with self._fetch_lock:
                self._pending_fetches.discard(scan_time)
                if decoding:
                    self._pending_decodes.discard(cache_key)
                self._update_loading_state()

    def _decode_cached_scan(self, scan_time: datetime, cache_key: tuple) -> None:
        try:
            path = self._raw_cache.get(scan_time)
            if not path or not os.path.exists(path):
                return
            with open(path, "rb") as fh:
                file_bytes = fh.read()
            self._decode_and_cache(scan_time, file_bytes, cache_key)
        finally:
            with self._fetch_lock:
                self._pending_decodes.discard(cache_key)
                self._update_loading_state()

    def _decode_and_cache(self, scan_time: datetime, file_bytes: bytes, cache_key: tuple) -> None:
        try:
            if self._closed.is_set():
                return
            scan = self._decode(scan_time, file_bytes, product=cache_key[1], tilt_idx=cache_key[2])
            if self._closed.is_set():
                return
            self._decoded_cache[cache_key] = scan
            if (
                self._current_archive_time is not None
                and self._nearest_scan_before(self._current_archive_time) == scan_time
                and self._decode_key(scan_time) == cache_key
            ):
                self._emit(self.scan_ready, scan)
        except Exception as exc:
            log.error("ArchiveRadarFetcher: decode failed for %s: %s", scan_time, exc)
            self._emit(self.error, f"Radar decode error: {exc}")

    def _update_loading_state(self) -> None:
        if not self._pending_fetches and not self._pending_decodes:
            self._emit(self.loading_changed, False)

    def _decode_key(self, scan_time: datetime) -> tuple[datetime, str, int]:
        return (scan_time, self._product, self._tilt_idx)

    def _download_scan(self, scan_time: datetime) -> Optional[bytes]:
        """Fetch the raw Level-2 file bytes from AWS S3."""
        with self._index_lock:
            idx = self._index
        # find a matching filename in the index for this exact scan time.
        prefix = (
            f"{scan_time.strftime('%Y/%m/%d')}/{self._station}/"
            f"{self._station}{scan_time.strftime('%Y%m%d_%H%M%S')}"
        )
        # try V06 first, then V03; the bucket was reorganized in 2025 to
        # store objects gzip-compressed with a ".gz" suffix on the key, so
        # also try gzip-suffixed variants of each.
        for suffix in ("_V06", "_V03", "", "_V06.gz", "_V03.gz", ".gz"):
            if self._closed.is_set():
                return None
            url = f"{_S3_BASE}/{prefix}{suffix}"
            try:
                resp = requests.get(url, timeout=60, stream=True)
                if resp.status_code == 200:
                    data = resp.content
                    # S3 doesn't set Content-Encoding for these objects, so
                    # requests won't auto-decompress -- detect gzip by magic
                    # bytes regardless of which suffix matched.
                    if data[:2] == b"\x1f\x8b":
                        data = gzip.decompress(data)
                    log.debug(
                        "ArchiveRadarFetcher: downloaded %s (%.1f MB)",
                        url.split("/")[-1], len(data) / 1e6,
                    )
                    return data
            except Exception:
                continue
        log.warning("ArchiveRadarFetcher: could not download scan %s", scan_time)
        return None

    def _get_parsed(self, scan_time: datetime, file_bytes: bytes):
        """Parse the raw Level-2 bytes with MetPy, reusing a cached parse.

        Parsing is independent of the selected product/tilt, so a scan that
        was already fetched only needs to be parsed once even if the user
        switches products or tilts repeatedly.
        """
        with self._parse_lock:
            f = self._parsed_cache.get(scan_time)
            if f is None:
                from metpy.io import Level2File
                f = Level2File(io.BytesIO(file_bytes))
                self._parsed_cache[scan_time] = f
            return f

    def _decode(self, scan_time: datetime, file_bytes: bytes, *, product=None, tilt_idx=None) -> Optional[Level2RadarScan]:
        """Decode a Level-2 file with MetPy and return a Level2RadarScan."""
        from pyproj import Proj, Transformer
        from core.mem_probe import peak_rss_mb, log_delta
        import time as _time
        _t0 = _time.perf_counter()
        _rss_before = peak_rss_mb()

        requested_product = self._product if product is None else product
        requested_tilt = self._tilt_idx if tilt_idx is None else tilt_idx
        f = self._get_parsed(scan_time, file_bytes)

        if not f.sweeps:
            raise RuntimeError("No sweeps found in Level-2 file")

        sweeps = [sweep for sweep in f.sweeps if sweep]
        if not sweeps:
            raise RuntimeError("No valid sweeps in Level-2 file")
        available_tilts = []
        sweep_products = []
        for sweep in sweeps:
            rad0 = sweep[0]
            hdr = rad0.header if hasattr(rad0, "header") else rad0[0]
            available_tilts.append(round(float(hdr.el_angle), 1))
            present = set()
            for rad in sweep:
                moments = rad.moments if hasattr(rad, "moments") else rad[1]
                present.update((k.decode() if isinstance(k, bytes) else k).strip() for k in moments)
            sweep_products.append({p for p in L2_PRODUCTS if _MOMENT_MAP[p] in present})
        available_products = [p for p in L2_PRODUCTS if any(p in fields for fields in sweep_products)]
        compatible = [i for i, fields in enumerate(sweep_products) if requested_product in fields]
        if not compatible:
            raise RuntimeError(f"No {requested_product} data in this radar volume")
        preferred = max(0, min(requested_tilt, len(sweeps) - 1))
        tilt_idx = min(compatible, key=lambda i: (abs(available_tilts[i] - available_tilts[preferred]), abs(i - preferred)))
        tilt_deg = available_tilts[tilt_idx]
        sweep = sweeps[tilt_idx]
        is_msg31 = hasattr(sweep[0], "moments")
        product = requested_product
        moment = _MOMENT_MAP[product]

        radar_lat = radar_lon = None
        for rad in sweep:
            vc = getattr(rad, "vol_consts", None)
            if vc is not None:
                radar_lat = float(vc.lat)
                radar_lon = float(vc.lon)
                break
        if radar_lat is None:
            # msg1 files don't carry lat/lon — fall back to NEXRAD_SITES table.
            from ui.controls.radar_controls import NEXRAD_SITES
            for sid, _name, lat, lon in NEXRAD_SITES:
                if sid == self._station:
                    radar_lat, radar_lon = lat, lon
                    break
        if radar_lat is None:
            raise RuntimeError(f"Cannot determine radar location for {self._station}")

        azimuths  = []
        gate_rows = []
        first_gate_km = gate_width_km = None

        for rad in sweep:
            hdr     = rad.header if is_msg31 else rad[0]
            moments = rad.moments if is_msg31 else rad[1]

            # metPy MSG31 keys are bytes (b'REF'), MSG1 keys are str ('REF').
            entry = moments.get(moment.encode(), moments.get(moment))
            if entry is None:
                continue

            data_hdr, vals = entry
            if first_gate_km is None:
                first_gate_km = float(data_hdr.first_gate)   # km
                gate_width_km = float(data_hdr.gate_width)   # km

            azimuths.append(float(hdr.az_angle))
            gate_rows.append(vals)

        if not azimuths:
            raise RuntimeError(f"No {moment} data found in sweep {tilt_idx}")

        # metPy returns radials in transmission order (first az often ~180-200°).
        sort_idx = np.argsort(azimuths)
        azimuths  = [azimuths[i] for i in sort_idx]
        gate_rows = [gate_rows[i] for i in sort_idx]

        n_rays  = len(azimuths)
        n_gates = max(len(r) for r in gate_rows)
        data = np.full((n_rays, n_gates), np.nan, dtype=np.float32)
        for i, row in enumerate(gate_rows):
            ng = len(row)
            data[i, :ng] = row  # already NaN for MISSING/RANGE_FOLD

        # velocity is in m/s from MetPy — convert to knots.
        if product in ("velocity", "spectrum_width"):
            data *= 1.94384

        az_rad   = np.deg2rad(np.array(azimuths, dtype=np.float64))
        ranges   = first_gate_km + np.arange(n_gates, dtype=np.float64) * gate_width_km
        # [n_rays, n_gates] east/north offsets in km
        x_km = np.outer(np.sin(az_rad), ranges)
        y_km = np.outer(np.cos(az_rad), ranges)

        aeqd = Proj(proj="aeqd", lat_0=radar_lat, lon_0=radar_lon, datum="WGS84", units="km")
        xform = Transformer.from_proj(aeqd, Proj("epsg:4326"), always_xy=True)
        lons, lats = xform.transform(x_km, y_km)

        meta = L2_PRODUCTS[product]
        log_delta(f"ArchiveRadarFetcher._decode {self._station} {scan_time}",
                   _rss_before, peak_rss_mb(), (_time.perf_counter() - _t0) * 1000.0)
        scan = Level2RadarScan(
            site=self._station,
            product=meta["label"].split("(")[-1].rstrip(")"),
            scan_time=scan_time,
            data=data,
            lats=lats.astype(np.float32),
            lons=lons.astype(np.float32),
            vmin=meta["vmin"],
            vmax=meta["vmax"],
            units=meta["units"],
            colormap=meta["colormap"],
            tilt_deg=tilt_deg,
            available_tilts=available_tilts,
            available_products=available_products,
            pyart_field=product,
            # _sample_scan_to_grid (ui/map/radar_overlay.py) maps ray index
            # -> azimuth by uniform division of 360 deg, assuming row 0 sits
            # at az_offset. Rows here are sorted by azimuth above, so
            # azimuths[0] is whatever the *smallest* azimuth in this
            # particular scan happens to be -- not reliably 0 deg. A scan
            # missing its near-0 deg ray (or any other completeness quirk)
            # then renders rotated by that gap until the next, more-complete
            # scan resets it. The live decoder (data/radar/radar_decoder.py)
            # already sets this correctly; the archive path never did.
            az_offset=float(azimuths[0]),
        )
        scan.tilt_index = tilt_idx
        return scan



    def _maintain_buffer(self, current_scan_time: datetime) -> None:
        """Pre-fetch BUFFER_BEFORE + BUFFER_AFTER scans around current_scan_time."""
        with self._index_lock:
            idx = self._index
        if not idx or current_scan_time not in idx:
            return
        pos = idx.index(current_scan_time)
        lo = max(0, pos - BUFFER_BEFORE)
        hi = min(len(idx) - 1, pos + BUFFER_AFTER)
        for i in range(lo, hi + 1):
            t = idx[i]
            if self._raw_cache.get(t) is None and t not in self._pending_fetches:
                self._ensure_fetched(t)

        # evict scans far outside the buffer to keep memory manageable.
        evict_lo = max(0, pos - BUFFER_BEFORE - 4)
        evict_hi = min(len(idx) - 1, pos + BUFFER_AFTER + 4)
        for t in list(self._raw_cache.keys()):
            i = idx.index(t) if t in idx else -1
            if i == -1 or i < evict_lo or i > evict_hi:
                path = self._raw_cache.pop(t, None)
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                self._parsed_cache.pop(t, None)
                for key in [k for k in self._decoded_cache if k[0] == t]:
                    del self._decoded_cache[key]



def _parse_l2_filename_time(fname: str, station: str) -> Optional[datetime]:
    """
    Extract UTC datetime from a Level-2 filename.
    Expected pattern: KXXX20230515_201500_V06
    """
    try:
        # strip station prefix (4 chars).
        rest = fname[4:]
        # rest = "20230515_201500_V06"
        date_part, rest2 = rest.split("_", 1)
        time_part = rest2.split("_")[0]
        dt_str = f"{date_part}{time_part}"
        return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception as exc:
        log.debug("ArchiveRadarFetcher: could not parse filename %r: %s", fname, exc)
        return None
