#!/usr/bin/env python3
"""Batch audit of FOFS one-second vehicle data exactly as STORM loads it.

Instead of opening each case in the app and looking for vehicles in the
wrong place, this runs STORM's real loader (ArchiveVehicleObsFetcher) for
every vehicle-day the THREDDS catalog can feed and flags what a person
would otherwise have to spot by eye:

  file level (each raw/<date>.txt in the catalog)
    relocated        the file's rows belong to another day (swapped name)
    garbled          dates recovered from the known THREDDS garbling
    misfiled_copy    MMDDYY copy of a correctly dated file (STORM skips it)
    undatable        rows present but none could be dated (no GPS fix, ...)
    partly_dated     under 95% of rows could be dated
  day level (each UTC session day, as the app would show it)
    teleport         consecutive fixes implying an impossible speed
    off_map          positions outside North America (e.g. 0,0)
    met_range        a met field mostly outside physical limits (shouldn't
                     happen: STORM drops such values while loading)
    wind_review      >5% of a day's winds above 50 m/s -- possible, so
                     kept, but worth a human look
    met_sentinel     -999-style missing-value markers shown as data
    catalog_day_empty  the catalog lists this date but STORM loads nothing
    midnight_hole    starts at 00:10:00 -- the conversion dropped 00:00-00:09
    long_gap         a gap of 15+ minutes inside the day's coverage

Downloads are cached on disk, keyed by THREDDS's own modified time, so
re-runs are nearly free and a republished file is re-fetched
automatically. Only files the catalog lists are requested at all (every
other candidate is a known 404), one vehicle at a time with pacing, since
data.nssl.noaa.gov throttles bursts (a first full-archive run takes
~20-30 min to fill the cache; avoid running it while using STORM live).

Outputs (outside the git checkout by default):
    case_data/evidence/fofs-audit-<timestamp>.json   everything
    case_data/evidence/fofs-audit-<timestamp>.csv    one row per finding
    (downloads are cached in ~/.cache/storm/fofs_raw, a few GB; developer tool only --
     STORM itself never reads this cache)
Exit status: 0 clean, 1 ERROR findings, 2 the audit itself failed partway.

Usage:
    python scripts/audit_fofs_vehicle_days.py                       # whole archive
    python scripts/audit_fofs_vehicle_days.py --vehicles probe1,probe2 --start 20240401 --end 20240630
    python scripts/audit_fofs_vehicle_days.py --dates 20240427,20240506
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

_STORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STORM_ROOT))

from archive.fetchers import vehicle_obs_archive_fetcher as vof  # noqa: E402
from core.observation import Observation  # noqa: E402

_REQUEST_PACING_S = 0.3
# The raw files total a few GB; stop caching (but keep auditing) rather
# than fill the disk the cache lives on.
_CACHE_MIN_FREE_BYTES = 2 * 1024**3

# What counts as "wrong" -- deliberately loose, so a flag means something a
# viewer would notice, not ordinary noise.
_TELEPORT_SPEED_MS = 90.0     # ~200 mph between consecutive fixes
_TELEPORT_MIN_KM = 1.0        # ignore GPS jitter
_REGION = (15.0, 60.0, -135.0, -60.0)  # lat_min, lat_max, lon_min, lon_max
# Same limits STORM applies when loading, so a met_range ERROR here means
# an impossible value reached the display.
_MET_LIMITS = {
    attribute: vof._PHYSICAL_LIMITS[column]
    for attribute, column in (("temperature_c", "t_fast"), ("dewpoint_c", "dewpoint"),
                              ("wind_speed_ms", "sfc_wspd"), ("wind_dir_deg", "sfc_wdir"),
                              ("pressure_mb", "pressure"))
}
# Winds this strong are possible near tornadoes but shouldn't be routine;
# a day where many readings are this high is worth a human look.
_WIND_REVIEW_MS = 50.0
_MET_SENTINEL_AT_OR_BELOW = -900.0
_MET_BAD_FRACTION = 0.05
_LONG_GAP_S = 15 * 60
_PARTLY_DATED_FRACTION = 0.95

log = logging.getLogger("audit_fofs")


@dataclass
class Finding:
    vehicle: str
    day: str          # YYYY-MM-DD session day, or the file's name date
    scope: str        # "file" or "day"
    severity: str     # ERROR / WARN / INFO
    code: str
    detail: str


@dataclass
class FileReport:
    vehicle: str
    file: str
    rows: int
    dated: int
    encoding: str | None
    start_day: str | None
    days: list[str] = field(default_factory=list)
    datable: int = 0      # rows with a real position whose date fit the file's dating
    positioned: int = 0   # rows with a real position (before duplicate seconds collapse)


@dataclass
class DayReport:
    vehicle: str
    day: str
    observations: int
    first: str | None
    last: str | None
    elapsed_s: float


# --- cached THREDDS access ---------------------------------------------------

class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, *_):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ThreddsCache:
    """Stands in for vof._urlopen_with_retry during the audit: mesonet file
    requests are answered from the shared FOFS file index (not in it ->
    404, no request) and a disk cache keyed by the file's THREDDS modified
    time, wherever in the tree the file lives."""

    def __init__(self, cache_dir: Path, original):
        self._dir = cache_dir
        self._original = original
        self._lock = threading.Lock()
        self._index = None
        self.network_requests = 0
        self.cache_full = False

    @property
    def index(self):
        from archive import fofs_index
        with self._lock:
            if self._index is None:
                self._index = fofs_index.get_index()
                if self._index is None:
                    raise RuntimeError("FOFS file index unavailable")
                self._by_url = {f.url: f for f in self._index.files}
        return self._index

    def catalog(self, vehicle: str) -> dict[str, str]:
        """{YYYYMMDD: modified} for a vehicle's published (non-legacy) files."""
        listing: dict[str, str] = {}
        for f in self.index.files:
            if f.vehicle == vehicle and "/_legacy" not in f.path:
                listing.setdefault(f"{f.date:%Y%m%d}", f.modified)
        return listing

    def urlopen(self, request, *, timeout):
        url = request.full_url
        if "/fileServer/FOFS/Mobile-Mesonet/" not in url or not url.endswith(".txt"):
            return self._network(request, timeout=timeout)
        self.index  # noqa: B018 - builds the URL map
        entry = self._by_url.get(url)
        if entry is None:
            raise HTTPError(url, 404, "Not in the THREDDS catalog", {}, None)
        stamp = "".join(ch for ch in entry.modified if ch.isalnum())
        name = entry.path.rsplit("/", 1)[-1][:-4]
        if entry.path == f"FOFS/Mobile-Mesonet/data/{entry.vehicle}/raw/{name}.txt":
            folder = self._dir / entry.vehicle              # the original cache layout
        else:
            folder = self._dir / "_other" / entry.path.rsplit("/", 1)[0]
        path = folder / f"{name}.{stamp}.txt"
        if path.exists():
            return _Body(path.read_bytes())
        with self._network(request, timeout=timeout) as response:
            data = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free - len(data) < _CACHE_MIN_FREE_BYTES:
            if not self.cache_full:
                log.warning("Less than %d GB free under %s -- no longer caching downloads",
                            _CACHE_MIN_FREE_BYTES // 1024**3, self._dir)
            self.cache_full = True
            return _Body(data)
        for stale in path.parent.glob(f"{name}.*.txt"):
            stale.unlink()
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return _Body(data)

    def _network(self, request, timeout=60):
        self.network_requests += 1
        try:
            return self._original(request, timeout=timeout)
        finally:
            time.sleep(_REQUEST_PACING_S)


# --- checks --------------------------------------------------------------------

def _km(a: Observation, b: Observation) -> float:
    p = math.pi / 180
    h = (math.sin((b.lat - a.lat) * p / 2) ** 2
         + math.cos(a.lat * p) * math.cos(b.lat * p) * math.sin((b.lon - a.lon) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def check_day(vehicle: str, day: date, observations: list[Observation]) -> list[Finding]:
    """Everything about a loaded vehicle-day that would look wrong on the map."""
    findings: list[Finding] = []

    def add(severity, code, detail):
        findings.append(Finding(vehicle, day.isoformat(), "day", severity, code, detail))

    if not observations:
        return findings

    teleports = []
    for a, b in zip(observations, observations[1:]):
        dt = (b.timestamp - a.timestamp).total_seconds()
        km = _km(a, b)
        if km >= _TELEPORT_MIN_KM and (dt <= 0 or km * 1000 / dt > _TELEPORT_SPEED_MS):
            teleports.append((a, b, km, dt))
    if teleports:
        a, b, km, dt = max(teleports, key=lambda t: t[2])
        add("ERROR", "teleport",
            f"{len(teleports)} jumps; worst {km:.0f} km in {dt:.0f} s at "
            f"{a.timestamp:%H:%M:%S}->{b.timestamp:%H:%M:%S} "
            f"({a.lat:.3f},{a.lon:.3f})->({b.lat:.3f},{b.lon:.3f})")

    lat_min, lat_max, lon_min, lon_max = _REGION
    off = [o for o in observations if not (lat_min <= o.lat <= lat_max and lon_min <= o.lon <= lon_max)]
    if off:
        add("ERROR", "off_map", f"{len(off)} fixes outside North America, e.g. "
            f"({off[0].lat:.3f},{off[0].lon:.3f}) at {off[0].timestamp:%H:%M:%S}")

    for attribute, (low, high) in _MET_LIMITS.items():
        values = [getattr(o, attribute) for o in observations]
        values = [v for v in values if v is not None and math.isfinite(v)]
        if attribute == "wind_dir_deg":
            values = [v for v in values if v != 999]  # logger's "calm" code
        if not values:
            continue
        sentinel = sum(1 for v in values if v <= _MET_SENTINEL_AT_OR_BELOW)
        out = sum(1 for v in values if v > _MET_SENTINEL_AT_OR_BELOW and not low <= v <= high)
        if sentinel / len(values) > _MET_BAD_FRACTION:
            add("WARN", "met_sentinel", f"{attribute}: {sentinel}/{len(values)} values are "
                f"<= {_MET_SENTINEL_AT_OR_BELOW:.0f} (missing-value markers)")
        if out / len(values) > _MET_BAD_FRACTION:
            sample = next(v for v in values if v > _MET_SENTINEL_AT_OR_BELOW and not low <= v <= high)
            add("ERROR", "met_range", f"{attribute}: {out}/{len(values)} outside "
                f"[{low:g}, {high:g}], e.g. {sample:g} -- scrambled or unconverted column?")

    winds = [o.wind_speed_ms for o in observations if o.wind_speed_ms is not None]
    strong = sum(1 for w in winds if w > _WIND_REVIEW_MS)
    if winds and strong / len(winds) > _MET_BAD_FRACTION:
        add("WARN", "wind_review", f"{strong}/{len(winds)} wind speeds over {_WIND_REVIEW_MS:.0f} m/s "
            f"(max {max(winds):.1f}) -- possible, but unusually common; worth a look")

    first = observations[0].timestamp
    if (first.hour, first.minute, first.second) == (0, 10, 0):
        add("INFO", "midnight_hole", "starts exactly 00:10:00 -- 00:00:00-00:09:59 missing "
            "from the THREDDS conversion")
    gaps = [((b.timestamp - a.timestamp).total_seconds(), a.timestamp)
            for a, b in zip(observations, observations[1:])]
    long_gaps = [(s, t) for s, t in gaps if s >= _LONG_GAP_S]
    if long_gaps:
        seconds, at = max(long_gaps)
        add("INFO", "long_gap", f"{len(long_gaps)} gaps >= {_LONG_GAP_S // 60} min; "
            f"longest {seconds / 60:.0f} min after {at:%H:%M:%S}")
    return findings


def check_file(report: FileReport, misfiled_twin: str | None) -> list[Finding]:
    findings: list[Finding] = []

    def add(severity, code, detail):
        findings.append(Finding(report.vehicle, report.file, "file", severity, code, detail))

    name_day = f"{report.file[:4]}-{report.file[4:6]}-{report.file[6:]}"
    if report.rows and not report.dated:
        add("WARN", "undatable", f"{report.rows} rows, none datable -- no GPS fix or an "
            "unknown date encoding; STORM shows nothing from this file")
        return findings
    if misfiled_twin:
        add("INFO", "misfiled_copy", f"MMDDYY copy of raw/{misfiled_twin}.txt under a misread "
            f"date; STORM skips it, so the catalog's {name_day} shows no data for it")
    elif report.start_day and report.start_day != name_day:
        add("INFO", "relocated", f"rows belong to {', '.join(report.days)} "
            f"(name read with day/month swapped); STORM shows them there")
    if report.encoding == "THREDDS-garbled":
        add("INFO", "garbled", "gps_date garbled by the THREDDS conversion; recovered")
    if report.positioned and report.datable / report.positioned < _PARTLY_DATED_FRACTION:
        add("WARN", "partly_dated", f"only {report.datable}/{report.positioned} positioned rows "
            "datable; the rest carry dates that fit neither this file nor its neighbours")
    return findings


# --- driving STORM's loader ---------------------------------------------------

def _session_days(file_dates: list[date], vehicle: str | None = None) -> set[date]:
    """Every UTC day a vehicle's files can put data on, per STORM's own
    candidate-file rule (a day reads its neighbours' files, their swapped
    names, and any file a known correction moves rows out of)."""
    days: set[date] = set()
    for file_date in file_dates:
        for start in (file_date, vof._swapped_file_date(file_date)):
            if start is not None:
                days.update(start + timedelta(days=k) for k in (-1, 0, 1))
        for correction in vof._CORRECTIONS.get((vehicle, file_date), []):
            if correction.shift_days is not None:
                days.update(file_date + timedelta(days=correction.shift_days + k) for k in (-1, 0, 1))
    return days


def audit_vehicle(vehicle: str, cache: ThreddsCache, wanted) -> tuple[list, list, list]:
    listing = cache.catalog(vehicle)
    file_dates = sorted(date(int(n[:4]), int(n[4:6]), int(n[6:])) for n in listing)
    if not file_dates:
        return [], [], []

    @lru_cache(maxsize=16)
    def parsed(file_date: date):
        """Every published copy of this vehicle's file for the date, as the
        loader reads them (vof._file_urls / _is_published_format)."""
        rows, observations, dating = 0, [], None
        for url in vof._file_urls(vehicle, file_date):
            request = Request(url, headers={"User-Agent": vof._USER_AGENT})
            try:
                with cache.urlopen(request, timeout=120) as response:
                    text = response.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code in (404, 410):
                    continue
                raise
            if not vof._is_published_format(text):
                continue
            file_obs, file_dating = vof._parse_vehicle_file(text, vehicle, None, file_date)
            rows += max(text.count("\n") - 1, 0)
            observations += file_obs
            if file_dating is not None and (dating is None or file_dating.matched > dating.matched):
                dating = file_dating
        observations.sort(key=lambda o: o.timestamp)
        return rows, observations, dating

    def fetch_file(self, vehicle_id, icon_type, file_date):
        _, observations, dating = parsed(file_date)
        return observations, dating

    file_reports, day_reports, findings = [], [], []
    for file_date in file_dates:
        if not any(wanted(d) for d in _session_days([file_date], vehicle)):
            continue
        rows, observations, dating = parsed(file_date)
        report = FileReport(
            vehicle, f"{file_date:%Y%m%d}", rows, len(observations),
            dating.encoding if dating else None,
            dating.start_day.isoformat() if dating else None,
            sorted({o.timestamp.date().isoformat() for o in observations}),
            dating.matched if dating else 0,
            dating.total if dating else 0,
        )
        twin = None
        if dating is not None and vof._is_misfiled_duplicate(
                file_date, observations, dating, lambda d: parsed(d)[1:]):
            twin = f"{vof._swapped_file_date(file_date):%Y%m%d}"
        file_reports.append(report)
        findings += check_file(report, twin)

    listed = {f"{d:%Y%m%d}" for d in file_dates}
    original_fetch_file = vof.ArchiveVehicleObsFetcher._fetch_vehicle_file
    for day in sorted(d for d in _session_days(file_dates, vehicle) if wanted(d)):
        fetcher = vof.ArchiveVehicleObsFetcher(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
        fetcher._fetch_vehicle_file = fetch_file.__get__(fetcher)
        t0 = time.time()
        observations = fetcher._fetch_vehicle(vehicle, None)
        day_reports.append(DayReport(
            vehicle, day.isoformat(), len(observations),
            observations[0].timestamp.isoformat() if observations else None,
            observations[-1].timestamp.isoformat() if observations else None,
            round(time.time() - t0, 2),
        ))
        findings += check_day(vehicle, day, observations)
        if not observations and f"{day:%Y%m%d}" in listed:
            findings.append(Finding(vehicle, day.isoformat(), "day", "WARN", "catalog_day_empty",
                                    "catalog lists this date but STORM loads nothing for it "
                                    "(see this file's file-level finding for why)"))
    assert vof.ArchiveVehicleObsFetcher._fetch_vehicle_file is original_fetch_file
    return file_reports, day_reports, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vehicles", help="Comma-separated THREDDS vehicle names (default: every vehicle in the catalog)")
    parser.add_argument("--start", help="First day to audit, YYYYMMDD")
    parser.add_argument("--end", help="Last day to audit, YYYYMMDD")
    parser.add_argument("--dates", help="Comma-separated YYYYMMDD days to audit (instead of a range)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Vehicles audited in parallel (default 1; the server throttles bursts)")
    parser.add_argument("--cache-dir", default=None,
                        help="Download cache (default: ~/.cache/storm/fofs_raw)")
    parser.add_argument("--out", default=None, help="Output directory (default: case_data/evidence)")
    args = parser.parse_args()

    def parse_day(text: str) -> date:
        return datetime.strptime(text.strip(), "%Y%m%d").date()

    only = {parse_day(d) for d in args.dates.split(",")} if args.dates else None
    start = parse_day(args.start) if args.start else date.min
    end = parse_day(args.end) if args.end else date.max

    def wanted(day: date) -> bool:
        return day in only if only is not None else start <= day <= end

    case_data = _STORM_ROOT.parent / "case_data"
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path.home() / ".cache" / "storm" / "fofs_raw"
    out_dir = Path(args.out) if args.out else case_data / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger(vof.__name__).setLevel(logging.ERROR)  # reported as findings instead

    cache = ThreddsCache(cache_dir, vof._urlopen_with_retry)

    vof._urlopen_with_retry = cache.urlopen
    vehicles = ([v.strip() for v in args.vehicles.split(",")] if args.vehicles
                else sorted(cache.index.vehicles()))
    t0 = time.time()
    file_reports, day_reports, findings = [], [], []
    failure = None
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for vehicle, result in zip(vehicles, pool.map(lambda v: audit_vehicle(v, cache, wanted), vehicles)):
                files, days, found = result
                file_reports += files
                day_reports += days
                findings += found
                print(f"  {vehicle:11s} {len(files):4d} files  {sum(d.observations > 0 for d in days):4d} days "
                      f"with data  {sum(f.severity == 'ERROR' for f in found):3d} errors  "
                      f"{sum(f.severity == 'WARN' for f in found):3d} warnings", flush=True)
    except Exception as exc:  # noqa: BLE001 - still write what finished
        failure = f"{type(exc).__name__}: {exc}"
        print(f"\nAUDIT INCOMPLETE -- {failure}; writing results for the vehicles that finished")

    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda f: (order[f.severity], f.code, f.vehicle, f.day))
    stamp = datetime.now().strftime("%Y%m%dT%H%M")
    json_path = out_dir / f"fofs-audit-{stamp}.json"
    csv_path = out_dir / f"fofs-audit-{stamp}.csv"
    json_path.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(),
        "arguments": vars(args),
        "network_requests": cache.network_requests,
        "incomplete": failure,
        "findings": [asdict(f) for f in findings],
        "files": [asdict(r) for r in file_reports],
        "days": [asdict(r) for r in day_reports],
    }, indent=1))
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Finding.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(f) for f in findings)

    print(f"\n{len(file_reports)} files, {len(day_reports)} vehicle-days, "
          f"{cache.network_requests} network requests, {time.time() - t0:.0f}s")
    for (severity, code), count in sorted(Counter((f.severity, f.code) for f in findings).items(),
                                          key=lambda kv: (order[kv[0][0]], kv[0][1])):
        print(f"  {severity:5s} {code:18s} {count}")
    for f in [f for f in findings if f.severity != "INFO"][:25]:
        print(f"  {f.severity:5s} {f.vehicle:10s} {f.day} {f.code}: {f.detail}")
    print(f"\n-> {csv_path}\n-> {json_path}")
    if failure:
        return 2
    return 1 if any(f.severity == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
