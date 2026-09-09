"""Damage-survey path overlays for archive playback, ported from MESO-VIEW's
mm_review/src/dat_tracks.py (a sibling app, not part of this repo).

Sourced from NOAA's Damage Assessment Toolkit (DAT) ArcGIS FeatureServer,
falling back to NCEI Storm Events bulk CSVs when DAT has nothing for a
box/date -- DAT coverage is sparse for older events. Both confirmed live
2026-09-09 against real 2013-05-31 Oklahoma data (the same case used
throughout this session for NOXP/ASOS): DAT returned real EF0-EF3 rated
tornado paths for that window.

Unlike MESO-VIEW, which builds this offline per pre-defined "case" as part
of a case-bundle pipeline, STORM has no such pipeline -- this is a live,
bbox-driven archive fetcher matching archive/fetchers/asos_archive_fetcher.py's
shape (an archive session date stands in for MESO-VIEW's obs_start/obs_end
case window, collapsed to one day +/- a tolerance).
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import math
import re
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

import config

log = logging.getLogger(__name__)

DAT_DAMAGE_LINES_URL = (
    "https://services.dat.noaa.gov/arcgis/rest/services/"
    "nws_damageassessmenttoolkit/DamageViewer/FeatureServer/1"
)
NCEI_EVENT_CSV_BASE_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles"

_DATE_TOLERANCE_DAYS = 1
_REQUEST_TIMEOUT = 20
_NCEI_TIMEOUT = 30

DEFAULT_OUT_FIELDS = [
    "objectid", "globalid", "event_id", "stormdate", "starttime", "endtime",
    "startlat", "startlon", "endlat", "endlon", "length", "width",
    "injuries", "fatalities", "efscale", "efnum", "maxwind", "wfo", "comments",
]

# The standard NWS/media EF-scale color convention (not MESO-VIEW-specific).
EF_COLORS = {
    "EF0": "#00ffc5", "EF1": "#55ff00", "EF2": "#ffff00", "EF3": "#e69800",
    "EF3+": "#e69800", "EF4": "#e60000", "EF5": "#a80084",
    "F0": "#00ffc5", "F1": "#55ff00", "F2": "#ffff00", "F3": "#e69800",
    "F4": "#e60000", "F5": "#a80084",
    "UNKNOWN": "#bee8ff", "N/A": "#bee8ff",
}

# Confirmed live against real DAT data (2026-09-09): -99 is DAT's own
# sentinel for "no value" on numeric fields (width, maxwind, and efnum when
# efscale itself is "N/A"/"UNKNOWN"). None of these fields can legitimately
# be negative, so any negative value is treated as missing -- MESO-VIEW's
# own hover code does not do this and will show e.g. "-99 yd" verbatim;
# fixed here since it's a real, verifiable defect, not a design choice.
_NUMERIC_SENTINEL_FIELDS = ("length", "width", "injuries", "fatalities", "maxwind")

# Storm Events detail records store event times in the local time zone named
# by CZ_TIMEZONE, not UTC. Most Storm Data records use standard-time
# abbreviations (EST/CST/MST/PST) even during the warm season, so the
# abbreviation is treated as an explicit fixed offset rather than applying
# DST rules.
NCEI_TIMEZONE_OFFSETS_HOURS = {
    "UTC": 0.0, "GMT": 0.0, "Z": 0.0,
    "EST": -5.0, "EDT": -4.0, "CST": -6.0, "CDT": -5.0,
    "MST": -7.0, "MDT": -6.0, "PST": -8.0, "PDT": -7.0,
    "AKST": -9.0, "AKDT": -8.0, "AST": -4.0, "ADT": -3.0,
    "HST": -10.0, "HDT": -9.0, "SST": -11.0, "CHST": 10.0,
}

EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _to_float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _sentinel_to_none(value: float | None) -> float | None:
    return None if value is not None and value < 0 else value


def _normalize_ef(props: dict) -> str:
    raw = props.get("efscale") or props.get("efnum") or props.get("rating") or "UNKNOWN"
    s = str(raw).strip().upper()
    if not s or s in {"NONE", "NULL", "NAN", "?"} or (s.lstrip("-").isdigit() and float(s) < 0):
        return "UNKNOWN"
    if s.isdigit():
        return f"EF{s}"
    if s.startswith(("EF", "F")):
        return s
    return s


def _normalize_feature(feature: dict, *, source: str) -> dict:
    f = dict(feature)
    props = dict(f.get("properties", {}) or {})
    for key in _NUMERIC_SENTINEL_FIELDS:
        if key in props:
            props[key] = _sentinel_to_none(_to_float(props.get(key)))
    ef = _normalize_ef(props)
    props["ef_label"] = ef
    props["ef_color"] = EF_COLORS.get(ef, EF_COLORS["UNKNOWN"])
    props["damage_source"] = source
    f["properties"] = props
    return f


def _parse_arcgis_time(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        v = float(value)
        if math.isfinite(v):
            seconds = v / 1000.0 if abs(v) > 10_000_000_000 else v
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    return None


def _feature_date(props: dict) -> datetime | None:
    for key in ("starttime", "stormdate", "endtime"):
        ts = _parse_arcgis_time(props.get(key))
        if ts is not None:
            return ts
    return None


def _iter_line_coords(geom: dict):
    if not isinstance(geom, dict):
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "LineString" and isinstance(coords, list):
        for xy in coords:
            if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                lon, lat = _to_float(xy[0]), _to_float(xy[1])
                if lon is not None and lat is not None:
                    yield lon, lat
    elif gtype == "MultiLineString" and isinstance(coords, list):
        for line in coords:
            if isinstance(line, list):
                for xy in line:
                    if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                        lon, lat = _to_float(xy[0]), _to_float(xy[1])
                        if lon is not None and lat is not None:
                            yield lon, lat


def _feature_intersects_bbox(feature: dict, west: float, south: float, east: float, north: float) -> bool:
    """A feature whose line crosses the box but has both endpoints outside
    it must still be kept -- checked via the segment's own bbox, not just
    "is any single vertex inside" (mirrors MESO-VIEW's _feature_intersects_bounds)."""
    xs, ys = [], []
    for lon, lat in _iter_line_coords(feature.get("geometry", {})):
        xs.append(lon)
        ys.append(lat)
        if west <= lon <= east and south <= lat <= north:
            return True
    if xs and ys:
        if max(xs) >= west and min(xs) <= east and max(ys) >= south and min(ys) <= north:
            return True
    props = feature.get("properties", {}) or {}
    for lon_key, lat_key in (("startlon", "startlat"), ("endlon", "endlat")):
        lon, lat = _to_float(props.get(lon_key)), _to_float(props.get(lat_key))
        if lon is not None and lat is not None and west <= lon <= east and south <= lat <= north:
            return True
    return False


def _date_within_tolerance(feature: dict, day: date, tolerance_days: int) -> bool:
    ts = _feature_date(feature.get("properties", {}) or {})
    if ts is None:
        return True  # an undated feature is never excluded on date grounds alone
    lo = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(days=tolerance_days)
    hi = lo + timedelta(days=1 + 2 * tolerance_days)
    return lo <= ts <= hi


def _arcgis_query_url(west: float, south: float, east: float, north: float) -> str:
    geometry = {
        "xmin": west, "ymin": south, "xmax": east, "ymax": north,
        "spatialReference": {"wkid": 4326},
    }
    params = {
        "f": "geojson",
        "where": "1=1",
        "geometry": json.dumps(geometry, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(DEFAULT_OUT_FIELDS),
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "2000",
    }
    return f"{DAT_DAMAGE_LINES_URL.rstrip('/')}/query?{urllib.parse.urlencode(params)}"


def fetch_dat_damage_paths(
    west: float, south: float, east: float, north: float, day: date, *, tolerance_days: int = _DATE_TOLERANCE_DAYS,
) -> dict:
    """Query DAT for damage-line features intersecting a bbox, filtered to
    the given day +/- tolerance_days. Never raises for "no results" -- only
    for a genuine request failure."""
    url = _arcgis_query_url(west, south, east, north)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
    with urlopen(request, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as response:
        payload = response.read().decode("utf-8")
    fc = json.loads(payload)
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise RuntimeError("DAT query did not return a GeoJSON FeatureCollection")

    features = []
    for feature in fc.get("features", []) or []:
        if not isinstance(feature, dict):
            continue
        if not _feature_intersects_bbox(feature, west, south, east, north):
            continue
        if not _date_within_tolerance(feature, day, tolerance_days):
            continue
        features.append(_normalize_feature(feature, source="NOAA DAT"))
    return {"type": "FeatureCollection", "features": features}


def _ncei_details_url(year: int) -> str:
    """Return the newest StormEvents_details CSV-gzip URL for a year."""
    base = NCEI_EVENT_CSV_BASE_URL.rstrip("/")
    index_url = base + "/"
    request = Request(index_url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
    with urlopen(request, timeout=_NCEI_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as response:
        html = response.read().decode("utf-8", errors="replace")
    pattern = re.compile(rf"StormEvents_details-ftp_v1\.0_d{int(year)}_c(\d{{8}})\.csv\.gz")
    matches = sorted(set(pattern.findall(html)))
    if not matches:
        raise FileNotFoundError(f"No NCEI StormEvents details CSV found for {year}")
    return f"{base}/StormEvents_details-ftp_v1.0_d{int(year)}_c{matches[-1]}.csv.gz"


def _ncei_timezone_offset_hours(row: dict) -> float | None:
    for col in ("CZ_TIMEZONE", "cz_timezone"):
        label = (row.get(col) or "").strip().upper()
        if label:
            compact = re.sub(r"[^A-Z0-9+\-]", "", label)
            if compact in NCEI_TIMEZONE_OFFSETS_HOURS:
                return NCEI_TIMEZONE_OFFSETS_HOURS[compact]
            match = re.search(r"(?:UTC|GMT)?([+\-]\d{1,2})(?::?(\d{2}))?$", compact)
            if match:
                hours = float(match.group(1))
                minutes = float(match.group(2) or 0) / 60.0
                return hours + (minutes if hours >= 0 else -minutes)
    return None


def _localize_ncei_time(value: str, row: dict) -> datetime | None:
    """Parse a Storm Events local timestamp and return it in UTC.

    BEGIN_DATE_TIME/END_DATE_TIME are local event times; CZ_TIMEZONE gives
    the local fixed offset (see NCEI_TIMEZONE_OFFSETS_HOURS). A row with no
    usable CZ_TIMEZONE falls back to treating the value as already-UTC,
    matching MESO-VIEW's own last-resort behavior, rather than dropping it.
    """
    if not value:
        return None
    naive = None
    # "17-NOV-13 13:14:00" is the real, confirmed format of NCEI's bulk
    # StormEvents_details CSVs (BEGIN_DATE_TIME/END_DATE_TIME) -- verified
    # live 2026-09-09 against a real downloaded 2013 file. The other two
    # formats are defensive only, in case NCEI changes its export format.
    for fmt in ("%d-%b-%y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(value.strip(), fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return None
    offset = _ncei_timezone_offset_hours(row)
    if offset is None:
        return naive.replace(tzinfo=timezone.utc)
    return (naive - timedelta(hours=offset)).replace(tzinfo=timezone.utc)


def _ncei_row_to_feature(row: dict) -> dict | None:
    begin_lat, begin_lon = _to_float(row.get("BEGIN_LAT")), _to_float(row.get("BEGIN_LON"))
    if begin_lat is None or begin_lon is None:
        return None
    end_lat, end_lon = _to_float(row.get("END_LAT")), _to_float(row.get("END_LON"))
    if end_lat is None or end_lon is None:
        end_lat, end_lon = begin_lat, begin_lon

    begin_t = _localize_ncei_time(row.get("BEGIN_DATE_TIME", ""), row)
    end_t = _localize_ncei_time(row.get("END_DATE_TIME", ""), row)
    rating = row.get("TOR_F_SCALE") or row.get("MAGNITUDE")

    props = {
        "event_id": (row.get("EVENT_ID") or "").strip() or None,
        "wfo": row.get("WFO"),
        "efscale": rating,
        "efnum": None,
        "length": _to_float(row.get("TOR_LENGTH")),
        "width": _to_float(row.get("TOR_WIDTH")),
        "injuries": _to_float(row.get("INJURIES_DIRECT")),
        "fatalities": _to_float(row.get("DEATHS_DIRECT")),
        "maxwind": None,
        "comments": row.get("EVENT_NARRATIVE") or row.get("EPISODE_NARRATIVE"),
    }
    if begin_t is not None:
        props["starttime"] = begin_t.timestamp() * 1000.0
    if end_t is not None:
        props["endtime"] = end_t.timestamp() * 1000.0

    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[begin_lon, begin_lat], [end_lon, end_lat]]},
        "properties": props,
    }
    return _normalize_feature(feature, source="NCEI Storm Events")


def fetch_ncei_storm_events_damage_paths(
    west: float, south: float, east: float, north: float, day: date, *, tolerance_days: int = _DATE_TOLERANCE_DAYS,
) -> dict:
    """Fetch tornado path lines from NCEI Storm Events bulk CSVs, used as a
    fallback when DAT has no matching damage line for this box/date."""
    lo = day - timedelta(days=tolerance_days)
    hi = day + timedelta(days=tolerance_days)
    features = []
    for year in range(lo.year, hi.year + 1):
        try:
            url = _ncei_details_url(year)
        except Exception as exc:  # noqa: BLE001
            log.warning("NCEI Storm Events index lookup failed for %s: %s", year, exc)
            continue
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
        with urlopen(request, timeout=_NCEI_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as response:
            raw = response.read()
        text = gzip.decompress(raw).decode("utf-8", errors="replace")
        for row in csv.DictReader(io.StringIO(text)):
            if (row.get("EVENT_TYPE") or "").strip().upper() != "TORNADO":
                continue
            feature = _ncei_row_to_feature(row)
            if feature is None:
                continue
            if not _feature_intersects_bbox(feature, west, south, east, north):
                continue
            if not _date_within_tolerance(feature, day, tolerance_days):
                continue
            features.append(feature)
    return {"type": "FeatureCollection", "features": features}


class ArchiveDamagePathsFetcher(QObject):
    """Loads DAT (falling back to NCEI Storm Events) damage-survey paths
    for a user-drawn map bbox on the archive session's date."""

    paths_ready = pyqtSignal(dict)  # the full, already-normalized GeoJSON FeatureCollection
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._session_date = session_date
        self._generation = 0
        self._cancel: threading.Event | None = None

    def set_bbox(self, west: float, south: float, east: float, north: float) -> None:
        """Redraw: a load already in flight for a previous box is
        abandoned -- its results, if they arrive late, are discarded by
        the generation check in _load."""
        if self._cancel is not None:
            self._cancel.set()
        self._generation += 1
        generation = self._generation
        self._cancel = threading.Event()
        threading.Thread(
            target=self._load, args=(generation, west, south, east, north, self._cancel), daemon=True,
        ).start()

    def _load(self, generation: int, west: float, south: float, east: float, north: float, cancel: threading.Event) -> None:
        day = self._session_date.date()
        try:
            dat_fc = fetch_dat_damage_paths(west, south, east, north, day)
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveDamagePathsFetcher: DAT fetch failed: %s", exc)
            dat_fc = dict(EMPTY_FC)

        if cancel.is_set() or generation != self._generation:
            return

        if dat_fc.get("features"):
            self.paths_ready.emit(dat_fc)
            return

        try:
            ncei_fc = fetch_ncei_storm_events_damage_paths(west, south, east, north, day)
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveDamagePathsFetcher: NCEI Storm Events fallback failed: %s", exc)
            if generation == self._generation and not cancel.is_set():
                self.error.emit(f"Damage paths unavailable: {exc}")
            return

        if cancel.is_set() or generation != self._generation:
            return
        self.paths_ready.emit(ncei_fc)
