
import io
import json
import logging
import struct
import threading
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Optional


from core import package_sources
from PyQt6.QtCore import QObject, pyqtSignal
from data.fetchers.hazard_fetcher import (
    _spc_cat_key,
    _spc_prob_label,
    _nws_color_for_phenom,
)

from data import endpoints
log = logging.getLogger(__name__)

_IEM_SBW_URL   = endpoints.IEM_STORM_BASED_WARNINGS
# these endpoints require a 12-digit YYYYMMDDHHmm timestamp via ?ts=
_IEM_WATCH_URL = endpoints.IEM_SPC_WATCHES
# iem GIS shapefile endpoint for MCDs — returns a zip with .shp/.dbf/.prj
_IEM_MCD_GIS_URL = endpoints.IEM_SPC_MD_GIS

# spc direct archive — same GeoJSON schema as the live endpoint (LABEL, DN, etc.).
_SPC_OUTLOOK_ARCHIVE = endpoints.SPC_OUTLOOK_ARCHIVE
_SPC_EXTENDED_OUTLOOK_ARCHIVE = endpoints.SPC_EXTENDED_OUTLOOK_ARCHIVE

# SPC only started publishing day1/2 outlooks as .lyr.geojson on 2020-01-01;
# before that the archive only has the shapefile zip (day{N}otlk_..._-shp.zip,
# one zip per cycle bundling every product's .shp/.dbf together, unlike the
# geojson path which is one file per product). Confirmed by HEAD request:
# .../2019/day1otlk_20190608_1300_wind.lyr.geojson -> 404
# .../2019/day1otlk_20190608_1300-shp.zip -> 200
_SPC_CAT_DN_LABELS = {2: "MRGL", 3: "SLGT", 4: "ENH", 5: "MDT", 6: "HIGH"}

# cache resolution for time-varying fetches.
_CACHE_MINUTES = 5

_OUTLOOK_CYCLES = [
    (1, 0, 1, 0),     # 01Z issuance → 0100 archive file
    (6, 0, 12, 0),    # 06Z issuance → 1200 valid-time archive file
    (13, 0, 13, 0),
    (16, 30, 16, 30),
    (20, 0, 20, 0),
]

_DAY_OUTLOOK_CYCLES: dict[int, list[tuple[int, int, int, int]]] = {
    1: _OUTLOOK_CYCLES,
    2: [(6, 0, 7, 0), (17, 0, 17, 30)],
    3: [(7, 30, 8, 30), (19, 30, 19, 30)],
    4: [(9, 0, 0, 0)],
    5: [(9, 0, 0, 0)],
    6: [(9, 0, 0, 0)],
    7: [(9, 0, 0, 0)],
    8: [(9, 0, 0, 0)],
}

_EMPTY_FC = '{"type":"FeatureCollection","features":[]}'


class ArchiveHazardFetcher(QObject):
    """
    Replays historical hazard data (warnings, watches, MDs, outlooks) in sync
    with the archive time controller.

    Signals
    -------
    spc_received(str, str, str, str, str, str)
        cat, wind, hail, tor, prob, sig GeoJSON strings — same signature as live fetcher.
    nws_received(str)
        NWS warnings GeoJSON string.
    watches_received(str)
        SPC watches GeoJSON string.
    spc_mds_received(str)
        SPC mesoscale discussions GeoJSON string.
    loading_changed(bool)
    error(str)
    """

    spc_received     = pyqtSignal(str, str, str, str, str, str)
    nws_received     = pyqtSignal(str)
    watches_received = pyqtSignal(str)
    spc_mds_received = pyqtSignal(str)
    loading_changed  = pyqtSignal(bool)
    error            = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = session_date

        # per-type caches: {rounded_time → geojson_str}
        self._sbw_cache:   dict[datetime, str] = {}
        self._watch_cache: dict[datetime, str] = {}
        self._mcd_cache:   dict[datetime, str] = {}

        # per-type in-flight sets + locks
        self._sbw_pending:   set[datetime] = set()
        self._watch_pending: set[datetime] = set()
        self._mcd_pending:   set[datetime] = set()
        self._sbw_lock   = threading.Lock()
        self._watch_lock = threading.Lock()
        self._mcd_lock   = threading.Lock()

        # outlook cache: {(day, cycle_time) → (cat, wind, hail, tor, prob, sig)}
        self._outlook_cache: dict[tuple[int, datetime], tuple[str, str, str, str, str, str]] = {}
        self._current_cycle: Optional[tuple[int, datetime]] = None
        self._outlook_pending: set[tuple[int, datetime]] = set()
        self._current_archive_time: Optional[datetime] = None
        self._spc_day = 1

        # immediately ready — no pre-fetch step required.
        self._watches_loaded = True


    def load_day_data(self) -> None:
        """No-op: all data is now fetched on demand. _watches_loaded stays True."""
        pass

    def on_time_changed(self, archive_time: datetime) -> None:
        """Called by TimeController on every tick."""
        self._current_archive_time = archive_time
        self._update_warnings(archive_time)
        self._update_watches(archive_time)
        self._update_mds(archive_time)
        self._update_outlook(archive_time)

    def refresh_now(self) -> None:
        """Re-emit or fetch hazard data for the current archive time."""
        if self._current_archive_time is None:
            return
        self._current_cycle = None
        self.on_time_changed(self._current_archive_time)

    def set_spc_day(self, day: int) -> None:
        self._spc_day = max(1, min(8, int(day or 1)))
        self._current_cycle = None


    def _update_warnings(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._sbw_cache.get(rounded)
        if cached is not None:
            self.nws_received.emit(cached)
            return
        with self._sbw_lock:
            if rounded in self._sbw_pending:
                return
            self._sbw_pending.add(rounded)
        threading.Thread(target=self._fetch_sbw, args=(rounded,), daemon=True).start()

    def _fetch_sbw(self, rounded_time: datetime) -> None:
        try:
            ts = rounded_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = package_sources.requests_get("hazards", _IEM_SBW_URL, params={"ts": ts}, timeout=20)
            resp.raise_for_status()
            geojson_str = _normalize_sbw_geojson(resp.text)
            self._sbw_cache[rounded_time] = geojson_str
            self.nws_received.emit(geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: SBW fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._sbw_lock:
                self._sbw_pending.discard(rounded_time)


    def _update_watches(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._watch_cache.get(rounded)
        if cached is not None:
            self.watches_received.emit(cached)
            return
        with self._watch_lock:
            if rounded in self._watch_pending:
                return
            self._watch_pending.add(rounded)
        threading.Thread(target=self._fetch_watches, args=(rounded,), daemon=True).start()

    def _fetch_watches(self, rounded_time: datetime) -> None:
        try:
            ts = rounded_time.strftime("%Y%m%d%H%M")   # spcwatch.py requires YYYYMMDDHHmm
            resp = package_sources.requests_get("hazards", _IEM_WATCH_URL, params={"ts": ts}, timeout=20)
            resp.raise_for_status()
            geojson_str = _normalize_watch_geojson(resp.text)
            self._watch_cache[rounded_time] = geojson_str
            self.watches_received.emit(geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: Watch fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._watch_lock:
                self._watch_pending.discard(rounded_time)


    def _update_mds(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._mcd_cache.get(rounded)
        if cached is not None:
            self.spc_mds_received.emit(cached)
            return
        with self._mcd_lock:
            if rounded in self._mcd_pending:
                return
            self._mcd_pending.add(rounded)
        threading.Thread(target=self._fetch_mds, args=(rounded,), daemon=True).start()

    def _fetch_mds(self, rounded_time: datetime) -> None:
        try:
            # use a ±2-hour window to catch MCDs that started before rounded_time
            t_start = rounded_time - timedelta(hours=2)
            t_end   = rounded_time + timedelta(hours=2)
            params = {
                "year1":   t_start.year,
                "month1":  t_start.month,
                "day1":    t_start.day,
                "hour1":   t_start.hour,
                "minute1": t_start.minute,
                "year2":   t_end.year,
                "month2":  t_end.month,
                "day2":    t_end.day,
                "hour2":   t_end.hour,
                "minute2": t_end.minute,
            }
            resp = package_sources.requests_get("hazards", _IEM_MCD_GIS_URL, params=params, timeout=30)
            resp.raise_for_status()
            if not resp.content.startswith(b"PK"):
                # IEM answers a burst of requests with a short non-zip reply; that
                # isn't "no MDs", so don't cache it -- the next clock change asks again
                log.info("ArchiveHazardFetcher: MCD reply for %s wasn't a zip (%r); will retry",
                         rounded_time, resp.content[:80])
                return

            features = _parse_mcd_shapefile_zip(resp.content, rounded_time)
            geojson_str = _normalize_mcd_geojson(
                json.dumps({"type": "FeatureCollection", "features": features})
            )
            self._mcd_cache[rounded_time] = geojson_str
            self.spc_mds_received.emit(geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: MCD fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._mcd_lock:
                self._mcd_pending.discard(rounded_time)


    def _update_outlook(self, t: datetime) -> None:
        """Fetch the selected SPC outlook day/cycle valid at time t."""
        day = self._spc_day
        cycle = _current_outlook_cycle(t, day)
        cache_key = (day, cycle)
        if cache_key == self._current_cycle:
            cached = self._outlook_cache.get(cache_key)
            if cached:
                self.spc_received.emit(*cached)
            return
        self._current_cycle = cache_key
        cached = self._outlook_cache.get(cache_key)
        if cached:
            self.spc_received.emit(*cached)
            return
        if cache_key in self._outlook_pending:
            return
        self._outlook_pending.add(cache_key)
        threading.Thread(target=self._fetch_outlook, args=(day, cycle), daemon=True).start()

    def _fetch_outlook(self, day: int, cycle: datetime) -> None:
        try:
            results: dict[str, str] = {}
            candidates = _outlook_cycle_candidates(cycle, day)
            products = _archive_product_suffixes(day)
            sig_products = _archive_sig_suffixes(day)
            any_found = False

            for key, suffix in products.items():
                found = False
                for ct in candidates:
                    url = _archive_spc_url(day, ct, suffix)
                    try:
                        resp = package_sources.requests_get("hazards", url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                        results[key] = _normalize_archive_spc_geojson(
                            key,
                            resp.text,
                            day=day,
                            product=key,
                        )
                        found = True
                        log.debug(
                            "ArchiveHazardFetcher: day %s outlook %s found at %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                        )
                        break
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook %s @ %s failed: %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                            exc,
                        )
                if not found:
                    results[key] = _EMPTY_FC
                any_found = any_found or found

            for key, suffix in sig_products.items():
                base_key = key if key in ("prob", "sig") else key
                found = False
                for ct in candidates:
                    url = _archive_spc_url(day, ct, suffix)
                    try:
                        resp = package_sources.requests_get("hazards", url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                        sig_geojson = _normalize_archive_spc_geojson(
                            "significant",
                            resp.text,
                            day=day,
                            product=base_key,
                            force_label="SIGN",
                        )
                        if key == "sig":
                            results["sig"] = sig_geojson
                        else:
                            results[key] = _merge_significant_geojson(
                                results.get(key, _EMPTY_FC),
                                sig_geojson,
                            )
                        found = True
                        break
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook %s significant @ %s failed: %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                            exc,
                        )
                any_found = any_found or found

            # SPC only started publishing day1/2 outlooks as .lyr.geojson on
            # 2020-01-01 -- every candidate above 404s for anything older,
            # silently (a 404 is treated as "try the next candidate", not an
            # error), which is why this could fail with no warning logged at
            # all. Older dates are only in the archive as one shapefile zip
            # per cycle bundling every product together; try it once, across
            # the same candidate cycles, only once the geojson path has
            # genuinely found nothing (2020+ dates always succeed above and
            # never reach this).
            if not any_found and day in (1, 2):
                for ct in candidates:
                    zip_url = _archive_spc_shapefile_zip_url(day, ct)
                    try:
                        resp = package_sources.requests_get("hazards", zip_url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook shapefile zip @ %s failed: %s",
                            day, ct.strftime("%Y%m%d_%H%M"), exc,
                        )
                        continue
                    by_suffix = _parse_outlook_shapefile_zip(resp.content)
                    if not by_suffix:
                        continue
                    log.info(
                        "ArchiveHazardFetcher: day %s outlook found via pre-2020 shapefile archive at %s",
                        day, ct.strftime("%Y%m%d_%H%M"),
                    )
                    # Route through the same normalization the geojson path
                    # uses (_normalize_archive_spc_geojson) rather than
                    # serializing the raw shapefile records directly -- the
                    # frontend colors categorical fills off props["cat"]
                    # and probabilistic fills off props["LABEL"] (a percent
                    # string like "5"/"15"/"30"), neither of which the raw
                    # DBF fields carry as-is (only DN, and LABEL for cat).
                    # Without this, every shapefile-sourced feature rendered
                    # with its match expression's fallback color instead of
                    # its real risk level -- confirmed live against a real
                    # case (2010-05-10) where this fallback path is the only
                    # way outlook data is found at all.
                    for key, suffix in products.items():
                        features = by_suffix.get(suffix, [])
                        raw_geojson = json.dumps({"type": "FeatureCollection", "features": features})
                        results[key] = _normalize_archive_spc_geojson(
                            key, raw_geojson, day=day, product=key,
                        )
                    for key, suffix in sig_products.items():
                        base_key = key if key in ("prob", "sig") else key
                        sig_features = by_suffix.get(suffix, [])
                        if not sig_features:
                            continue
                        raw_sig_geojson = json.dumps({"type": "FeatureCollection", "features": sig_features})
                        sig_geojson = _normalize_archive_spc_geojson(
                            "significant", raw_sig_geojson, day=day, product=base_key, force_label="SIGN",
                        )
                        if key == "sig":
                            results["sig"] = sig_geojson
                        else:
                            results[key] = _merge_significant_geojson(
                                results.get(key, _EMPTY_FC),
                                sig_geojson,
                            )
                    break
                else:
                    log.warning("ArchiveHazardFetcher: day %s outlook not found for any candidate cycle (geojson or shapefile)", day)

            payload = (
                results.get("categorical", _EMPTY_FC),
                results.get("wind",        _EMPTY_FC),
                results.get("hail",        _EMPTY_FC),
                results.get("tornado",     _EMPTY_FC),
                results.get("prob",        _EMPTY_FC),
                results.get("sig",         _EMPTY_FC),
            )
            self._outlook_cache[(day, cycle)] = payload
            self.spc_received.emit(*payload)
        except Exception as exc:
            log.error("ArchiveHazardFetcher: outlook fetch error: %s", exc)
            self.error.emit(f"Outlook fetch error: {exc}")
        finally:
            self._outlook_pending.discard((day, cycle))



def _parse_mcd_shapefile_zip(zip_bytes: bytes, filter_time: datetime) -> list:
    """
    Parse the IEM GIS shapefile zip for MCDs.
    Returns a list of GeoJSON Feature dicts filtered to those valid at filter_time.

    The zip contains: .shp (Polygon type 5), .dbf (with ISSUE/EXPIRE/NUM fields).
    DBF field ISSUE and EXPIRE are 12-char strings in YYYYMMDDHHmm format.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        log.warning("MCD shapefile: bad zip: %s", exc)
        return []

    # find .shp and .dbf members (case-insensitive).
    names = zf.namelist()
    shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
    dbf_name = next((n for n in names if n.lower().endswith(".dbf")), None)
    if not shp_name or not dbf_name:
        log.warning("MCD shapefile: missing .shp or .dbf in zip (files: %s)", names)
        return []

    shp_data = zf.read(shp_name)
    dbf_data = zf.read(dbf_name)

    geometries = _parse_shp(shp_data)
    records    = _parse_dbf(dbf_data)

    if len(geometries) != len(records):
        log.warning(
            "MCD shapefile: geometry count %d != record count %d",
            len(geometries), len(records)
        )
        # still proceed with min(len) pairs.

    features = []
    for geom, rec in zip(geometries, records):
        if geom is None:
            continue
        issue_str  = (rec.get("ISSUE")  or "").strip()
        expire_str = (rec.get("EXPIRE") or "").strip()
        num_raw    = rec.get("NUM")
        try:
            num = int(float(num_raw)) if num_raw is not None else 0
        except (TypeError, ValueError):
            num = 0
        if not num:
            continue

        # parse ISSUE/EXPIRE (YYYYMMDDHHmm, 12 chars).
        issue  = _parse_yyyymmddHHMM(issue_str)
        expire = _parse_yyyymmddHHMM(expire_str)
        if issue is None or expire is None:
            continue
        if not (issue <= filter_time <= expire):
            continue

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": dict(rec, num=num),
        })

    return features


def _parse_shp(data: bytes, transform=None) -> list:
    """
    Minimal parser for ESRI Shapefile (.shp).
    Supports Polygon (type 5) only.  Returns a list of GeoJSON geometry dicts
    (or None for null/unsupported records).

    `transform`, if given, is a callable (x, y) -> (x2, y2) applied to every
    raw point as it's read -- SPC's shapefiles store coordinates in a
    projected CRS (Lambert Conformal Conic; see
    _parse_outlook_shapefile_zip), not GeoJSON's required WGS84 lon/lat, so
    the real caller always passes a proper reprojection. None (the
    default) is an identity no-op, kept for callers/tests that already
    hand in lon/lat-like values directly.
    """
    if len(data) < 100:
        return []

    pos = 100  # skip 100-byte file header
    geometries = []

    while pos < len(data):
        if pos + 12 > len(data):
            break
        # record header: record number (big-endian int32), content length (big-endian int32, in 16-bit words).
        _rec_num, content_words = struct.unpack_from(">ii", data, pos)
        pos += 8
        content_bytes = content_words * 2
        if content_bytes < 4 or pos + content_bytes > len(data):
            break
        shape_type = struct.unpack_from("<i", data, pos)[0]
        if shape_type == 0:
            # null shape.
            geometries.append(None)
            pos += content_bytes
            continue
        if shape_type != 5:
            # non-polygon; skip.
            geometries.append(None)
            pos += content_bytes
            continue

        # polygon: bounding box (4 doubles), num_parts (int32), num_points (int32),
        offset = pos + 4  # skip shape_type field
        if offset + 32 + 8 > len(data):
            geometries.append(None)
            pos += content_bytes
            continue
        offset += 32  # skip bounding box
        num_parts, num_points = struct.unpack_from("<ii", data, offset)
        offset += 8
        if num_parts <= 0 or num_points <= 0:
            geometries.append(None)
            pos += content_bytes
            continue
        part_starts = list(struct.unpack_from(f"<{num_parts}i", data, offset))
        offset += num_parts * 4
        pts_raw = struct.unpack_from(f"<{num_points * 2}d", data, offset)
        if transform is None:
            points = [(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]
        else:
            points = [transform(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]

        # split points into rings using part_starts.
        rings = []
        for idx_r, start in enumerate(part_starts):
            end = part_starts[idx_r + 1] if idx_r + 1 < num_parts else num_points
            ring = [list(pt) for pt in points[start:end]]
            rings.append(ring)

        geometries.append({"type": "Polygon", "coordinates": rings})
        pos += content_bytes

    return geometries


def _parse_dbf(data: bytes) -> list:
    """
    Minimal parser for dBASE III+ (.dbf).
    Returns a list of dicts, one per record, with string or numeric values.
    """
    if len(data) < 32:
        return []

    # header: version(1), date(3), num_records(4LE), header_bytes(2LE), record_bytes(2LE).
    num_records  = struct.unpack_from("<I", data, 4)[0]
    header_bytes = struct.unpack_from("<H", data, 8)[0]
    record_bytes = struct.unpack_from("<H", data, 10)[0]

    # field descriptors start at byte 32, each 32 bytes, terminated by 0x0D.
    fields = []
    pos = 32
    while pos < header_bytes - 1 and data[pos] != 0x0D:
        if pos + 32 > len(data):
            break
        raw_name = data[pos:pos + 11]
        name  = raw_name.split(b"\x00")[0].decode("ascii", errors="replace").strip()
        ftype = chr(data[pos + 11])
        flen  = data[pos + 16]
        fields.append((name, ftype, flen))
        pos += 32

    records = []
    rec_pos = header_bytes
    for _ in range(num_records):
        if rec_pos + record_bytes > len(data):
            break
        deletion_flag = data[rec_pos]
        if deletion_flag == 0x2A:  # '*' = deleted
            rec_pos += record_bytes
            continue
        field_pos = rec_pos + 1  # skip deletion flag
        rec = {}
        for name, ftype, flen in fields:
            raw = data[field_pos:field_pos + flen].decode("ascii", errors="replace").strip()
            if ftype == "N":
                try:
                    rec[name] = float(raw) if raw else None
                except ValueError:
                    rec[name] = None
            else:
                rec[name] = raw
            field_pos += flen
        records.append(rec)
        rec_pos += record_bytes

    return records


def _archive_spc_shapefile_zip_url(day: int, cycle: datetime) -> str:
    """Pre-2020 day1/2 outlook archive URL -- one zip per cycle, bundling
    every product's .shp/.dbf/.prj together (unlike the per-product
    .lyr.geojson files _archive_spc_url builds for 2020+)."""
    return (
        f"{_SPC_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
        f"day{day}otlk_{cycle.strftime('%Y%m%d')}_{cycle.strftime('%H%M')}-shp.zip"
    )


def _shapefile_lonlat_transform(prj_wkt: str):
    """Build an (x, y) -> (lon, lat) callable from a shapefile's own .prj
    (ESRI WKT) contents. Confirmed live (2026-09) against a real archive
    zip: SPC's outlook shapefiles are Lambert Conformal Conic (standard
    parallels 33N/45N, central meridian 0), not GeoJSON's required WGS84
    lon/lat -- every product's .prj in a given zip carries the identical
    definition. Raw .shp coordinates used directly as lon/lat (this
    fallback's original behavior) put every polygon at a wildly wrong
    location -- e.g. one real Oklahoma categorical-outlook ring's raw
    coordinates inverse-projected as plain Web Mercator landed in
    northern Canada, nowhere near the visible map extent.
    """
    from pyproj import CRS, Transformer
    crs = CRS.from_wkt(prj_wkt)
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return lambda x, y: transformer.transform(x, y)


# Degrees, applied post-reprojection. SPC's pre-2020 archive shapefiles are
# raw, unsmoothed marching-squares contours off their underlying probability
# grid -- confirmed live: a real outlook ring had thousands of vertices in an
# alternating-diagonal staircase pattern (e.g. consecutive vertex deltas of
# (0.050, -0.021) then (0.026, 0.040) repeating), nothing like the smoother
# curves SPC's own 2020+ .lyr.geojson archive already publishes pre-smoothed.
# ~0.05 deg (~5 km over CONUS) removes that staircase noise without visibly
# eating into the outlook's real shape at the zoom levels this renders at.
_OUTLOOK_SIMPLIFY_TOLERANCE_DEG = 0.05


def _simplify_outlook_geometry(geom: dict, tolerance_deg: float) -> dict:
    """Douglas-Peucker-simplify a reprojected Polygon's ring(s) to smooth
    out grid-contour staircasing. Falls back to the original geometry
    whenever shapely can't produce a valid, non-empty result (e.g. a
    sliver ring simplifying away entirely) -- a jagged polygon is still
    correct; a missing one wouldn't be."""
    if geom.get("type") != "Polygon":
        return geom
    try:
        from shapely.geometry import mapping, shape
        simplified = shape(geom).simplify(tolerance_deg, preserve_topology=True)
        if simplified.is_empty or not simplified.is_valid or simplified.geom_type != "Polygon":
            return geom
        return mapping(simplified)
    except Exception as exc:
        log.warning("Outlook shapefile zip: geometry simplification failed, using raw ring: %s", exc)
        return geom


def _parse_outlook_shapefile_zip(zip_bytes: bytes) -> dict[str, list[dict]]:
    """Parse a pre-2020 outlook zip into {suffix: [GeoJSON Feature, ...]},
    keyed by the same suffix strings _archive_product_suffixes /
    _archive_sig_suffixes use (cat, wind, hail, torn, sigwind, sighail,
    sigtorn) -- one member pair per suffix, named
    day{N}otlk_..._{suffix}.shp/.dbf. Each record's only real field is a
    numeric DN: the categorical outlook's risk-level code (2-6, mapped via
    _SPC_CAT_DN_LABELS) or the probabilistic layers' percent-contour value,
    which _spc_prob_label already reads directly off props["DN"].
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        log.warning("Outlook shapefile zip: bad zip: %s", exc)
        return {}

    names = zf.namelist()
    # Every product's .prj in a real zip carries the identical projection
    # (confirmed live) -- read whichever one is present once, rather than
    # re-parsing it per suffix. Without a usable transform, raw .shp
    # coordinates (a projected CRS, not lon/lat) would misplace every
    # polygon, so this is treated the same as an unreadable zip rather
    # than silently emitting geometry at the wrong location.
    prj_name = next((n for n in names if n.lower().endswith(".prj")), None)
    if prj_name is None:
        log.warning("Outlook shapefile zip: no .prj found; refusing to guess a coordinate system")
        return {}
    try:
        transform = _shapefile_lonlat_transform(zf.read(prj_name).decode("ascii", errors="replace"))
    except Exception as exc:
        log.warning("Outlook shapefile zip: could not parse %s: %s", prj_name, exc)
        return {}

    by_suffix: dict[str, list[dict]] = {}
    for suffix in ("cat", "wind", "hail", "torn", "sigwind", "sighail", "sigtorn"):
        shp_name = next((n for n in names if n.lower().endswith(f"_{suffix}.shp")), None)
        dbf_name = next((n for n in names if n.lower().endswith(f"_{suffix}.dbf")), None)
        if not shp_name or not dbf_name:
            continue
        geometries = _parse_shp(zf.read(shp_name), transform=transform)
        records = _parse_dbf(zf.read(dbf_name))

        features = []
        for geom, rec in zip(geometries, records):
            if geom is None:
                continue
            geom = _simplify_outlook_geometry(geom, _OUTLOOK_SIMPLIFY_TOLERANCE_DEG)
            dn_raw = rec.get("DN")
            try:
                dn = int(dn_raw) if dn_raw is not None else None
            except (TypeError, ValueError):
                dn = None
            if dn is None:
                continue
            if suffix.startswith("sig") and dn <= 0:
                # DN=0 on a sig* layer is SPC's "no significant risk"
                # placeholder -- the shapefile format needs at least one
                # record even when there's nothing to draw, so it ships a
                # degenerate polygon instead of an empty file. Confirmed
                # against a real archive day (2019-06-08): sigwind/sigtorn
                # both carried a tiny throwaway DN=0 polygon that day, while
                # sighail's real DN=10 significant-hail contour came through
                # as expected. Rendering the DN=0 one would draw a false
                # "significant" overlay where none exists.
                continue
            props = dict(rec, DN=dn)
            if suffix == "cat":
                label = _SPC_CAT_DN_LABELS.get(dn)
                if label is None:
                    continue
                props["LABEL"] = label
            features.append({"type": "Feature", "geometry": geom, "properties": props})
        by_suffix[suffix] = features

    return by_suffix


def _parse_yyyymmddHHMM(s: str) -> Optional[datetime]:
    """Parse a 12-char YYYYMMDDHHmm string into a UTC-aware datetime."""
    if not s or len(s) < 12:
        return None
    try:
        return datetime(
            int(s[0:4]), int(s[4:6]),  int(s[6:8]),
            int(s[8:10]), int(s[10:12]),
            tzinfo=timezone.utc,
        )
    except (ValueError, OverflowError):
        return None



def _normalize_sbw_geojson(raw_text: str) -> str:
    """Normalize IEM SBW GeoJSON to match the live WWA MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem uses 'phenomena' or 'phenomenon'; live mode uses 'phenom'
        phenom = str(
            props.get("phenomena") or props.get("phenomenon") or props.get("phenom", "")
        ).upper()
        props["phenom"]    = phenom
        props["prod_type"] = str(
            props.get("type_") or props.get("ps") or props.get("prod_type") or phenom
        )
        props["nws_color"]   = _nws_color_for_phenom(phenom)
        props["wfo"]         = str(props.get("wfo", "")).upper()
        props["event"]       = str(props.get("eventid") or props.get("event", ""))
        props["warning_url"] = str(props.get("href") or props.get("warning_url", ""))
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_watch_geojson(raw_text: str) -> str:
    """Normalize IEM spc_watch.geojson to match the live WWA MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem spc_watch: 'type' is TOR/SVR, 'num' is the watch number
        watch_type = str(props.get("type") or props.get("phenomena") or "").upper()
        num_raw    = props.get("num") or props.get("number") or props.get("event", "")
        try:
            num_str = str(int(num_raw)).zfill(4)
        except (TypeError, ValueError):
            num_str = str(num_raw)
        is_tor = watch_type in ("TOR", "TO")
        props["watch_num"]   = num_str
        props["watch_color"] = "#FF0000" if is_tor else "#4169E1"
        props["event"]       = "Tornado Watch" if is_tor else "Severe Thunderstorm Watch"
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_mcd_geojson(raw_text: str) -> str:
    """Normalize IEM spc_mcd.geojson to match the live SPC MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem spc_mcd: 'num' is the MD number
        num_raw = props.get("num") or props.get("number", "")
        try:
            name = f"MD {int(num_raw):04d}"
        except (TypeError, ValueError):
            name = str(num_raw) or "MD"
        if name.strip().upper() in ("MD", "MD 0000"):
            continue
        props["name"] = name
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_archive_spc_geojson(
    kind: str,
    raw_text: str,
    *,
    day: int | None = None,
    product: str | None = None,
    force_label: str | None = None,
) -> str:
    """Normalize archive SPC GeoJSON to the same property schema live mode uses."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text

    features = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom = feature.get("geometry")
        if not geom:
            continue
        if geom.get("type") == "GeometryCollection" and not geom.get("geometries"):
            continue
        if kind == "categorical":
            cat = _spc_cat_key(props)
            if not cat:
                continue
            props["cat"] = cat
        else:
            label = force_label or _spc_prob_label(props)
            if label is not None:
                props["LABEL"] = label
        if day is not None:
            props["spc_day"] = day
        if product:
            props["spc_product"] = product
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props,
        })

    return json.dumps({"type": "FeatureCollection", "features": features})


def _merge_significant_geojson(base_text: str, sig_text: str) -> str:
    """Merge significant polygons into a probabilistic product collection."""
    try:
        base = json.loads(base_text or _EMPTY_FC)
        sig = json.loads(sig_text or _EMPTY_FC)
    except json.JSONDecodeError:
        return base_text

    base_features = [
        f for f in (base.get("features") or [])
        if str((f.get("properties") or {}).get("LABEL", "")).upper() != "SIGN"
    ]
    base_features.extend(sig.get("features") or [])
    return json.dumps({"type": "FeatureCollection", "features": base_features})


def _archive_product_suffixes(day: int) -> dict[str, str]:
    if day in (1, 2):
        return {
            "categorical": "cat",
            "wind": "wind",
            "hail": "hail",
            "tornado": "torn",
        }
    if day == 3:
        return {
            "categorical": "cat",
            "prob": "prob",
        }
    if 4 <= day <= 8:
        return {"prob": f"day{day}prob"}
    return {"categorical": "cat"}


def _archive_sig_suffixes(day: int) -> dict[str, str]:
    if day in (1, 2):
        return {
            "wind": "sigwind",
            "hail": "sighail",
            "tornado": "sigtorn",
        }
    if day == 3:
        return {
            "prob": "sigprob",
            "sig": "sigprob",
        }
    return {}


def _archive_spc_url(day: int, cycle: datetime, suffix: str) -> str:
    if 4 <= day <= 8:
        return (
            f"{_SPC_EXTENDED_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
            f"{suffix}_{cycle.strftime('%Y%m%d')}.lyr.geojson"
        )
    return (
        f"{_SPC_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
        f"day{day}otlk_{cycle.strftime('%Y%m%d')}_{cycle.strftime('%H%M')}_{suffix}.lyr.geojson"
    )



def _round_to_minutes(dt: datetime, minutes: int) -> datetime:
    """Round dt down to the nearest N-minute boundary."""
    total_secs = int(dt.timestamp())
    step = minutes * 60
    return datetime.fromtimestamp(total_secs - (total_secs % step), tz=timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None


def _outlook_cycle_candidates(cycle: datetime, day: int) -> list[datetime]:
    """Return archive filename cycles to try, newest first."""
    if 4 <= day <= 8:
        return [cycle, cycle - timedelta(days=1)]

    cycles = _DAY_OUTLOOK_CYCLES.get(day, _DAY_OUTLOOK_CYCLES[1])
    candidates: list[datetime] = [cycle]
    midnight = cycle.replace(hour=0, minute=0, second=0, microsecond=0)

    for h_eff, m_eff, h_file, m_file in sorted(cycles, reverse=True):
        _ = (h_eff, m_eff)
        ct = midnight.replace(hour=h_file, minute=m_file)
        if ct < cycle and ct not in candidates:
            candidates.append(ct)

    prev_midnight = midnight - timedelta(days=1)
    for _h_eff, _m_eff, h_file, m_file in sorted(cycles, reverse=True):
        candidates.append(prev_midnight.replace(hour=h_file, minute=m_file))
    return candidates


def _current_outlook_cycle(t: datetime, day: int = 1) -> datetime:
    """Return the archive file cycle for the selected SPC day valid at time t."""
    day = max(1, min(8, int(day or 1)))
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    cycles = _DAY_OUTLOOK_CYCLES.get(day, _DAY_OUTLOOK_CYCLES[1])

    if 4 <= day <= 8:
        issue_time = midnight.replace(hour=cycles[0][0], minute=cycles[0][1])
        return midnight if t >= issue_time else midnight - timedelta(days=1)

    candidate: datetime | None = None
    for h_eff, m_eff, h_file, m_file in cycles:
        effective = midnight.replace(hour=h_eff, minute=m_eff)
        if effective <= t:
            candidate = midnight.replace(hour=h_file, minute=m_file)
    if candidate is None:
        prev_midnight = midnight - timedelta(days=1)
        _h_eff, _m_eff, h_file, m_file = cycles[-1]
        candidate = prev_midnight.replace(hour=h_file, minute=m_file)
    return candidate
