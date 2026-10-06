"""Cached THREDDS date index. Listings establish availability, not data quality.

Scan registered instrument catalogs once, retain their dates in memory, and reuse
completed listings when the user changes dates. Failures remain unknown; an
unmarked calendar date is never proof that no observations exist.
"""
from __future__ import annotations

import re
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from config import NSSL_SSL_CONTEXT
from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS
from archive.fetchers.clamps_wind_archive_fetcher import KNOWN_CLAMPS_WIND_SOURCES
from archive.fetchers.clamps_tropoe_archive_fetcher import (
    KNOWN_CLAMPS_TROPOE_PLATFORMS, _VARIANT_PREFERENCE as _TROPOE_VARIANTS,
)
from archive.fetchers.clamps_surface_archive_fetcher import KNOWN_CLAMPS_SURFACE_SOURCES
from archive.fetchers.clamps_sonde_archive_fetcher import _SONDE_PLATFORM_DIR, _SONDE_DATASTREAM
from archive.fetchers.coptersonde_archive_fetcher import KNOWN_PERILS_IOPS
from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES
from archive.fetchers.noxp_archive_fetcher import NoxpArchive

from archive import thredds_paths as paths

_CATALOG_ROOT = paths.catalog_root() + "/"
_REQUEST_TIMEOUT = 8
_MAX_CATALOG_BYTES = 16 * 1024 * 1024
_DATE_RE = re.compile(r"(\d{8})")
CAMPAIGN_YEARS = {
    "VORTEX2": (2009, 2010), "RiVorS": (2017,), "TORUS": (2019, 2022, 2023),
    "PERiLS": (2022, 2023), "LIFT": (2024, 2025, 2026),
}


# Scheduling hints from the 2026-09-08 catalog audit, not availability data.
# Never exclude a source or date using these. Unknown/new sources go first;
# a live refresh still checks every catalog, even if a hint is out of date.
# Evidence: planning/evidence/archive-availability-review.json.
_LATEST_OBSERVED_YEAR = {
    'FOFS-dltruck': 2026,
    'FOFS-farfield': 2022,
    'FOFS-hailcam': 2026,
    'FOFS-mg1': 2015,
    'FOFS-mg2': 2015,
    'FOFS-mg3': 2015,
    'FOFS-noxp_scout': 2015,
    'FOFS-probe1': 2026,
    'FOFS-probe2': 2026,
    'FOFS-probe3': 2010,
    'FOFS-probe4': 2010,
    'FOFS-probe5': 2010,
    'FOFS-probe7': 2010,
    'FOFS-probe9': 2010,
    'FOFS-windsonde1': 2022,
    'FOFS-windsonde2': 2022,
    'WIND-DLTRUCK1-DL1-VAD': 2026,
    'WIND-DLTRUCK1-DL2-VAD': 0,  # No dates in the audit; still checked every scan.
    'WIND-DLTRUCK1-DL1-CSMWINDS': 2023,
    'WIND-DLTRUCK1-DL2-CSMWINDS': 2022,
    'WIND-CLAMPS1-VAD': 2026,
    'WIND-CLAMPS2-VAD': 2026,
    'TROPOE-CLAMPS1': 2024,
    'TROPOE-CLAMPS2': 2024,
    'SFC-CLAMPS2-met_tower': 2021,
    'SFC-CLAMPS1-mwr': 2023,
    'SFC-CLAMPS2-mwr': 2023,
    'SONDE-DLTRUCK1': 2026,
    'COPTERSONDE-PERILS': 2023,
    'NOXP-VORTEX2_2009': 2009,
    'NOXP-VORTEX2_2010': 2010,
    'NOXP-2022': 2022,
    'NOXP-2015': 2015, 'NOXP-2013': 2013, 'NOXP-2011': 2011, 'NOXP-2010': 2010,
    'NOXP-Colorado': 2011, 'NOXP-Netcdf': 2011, 'NOXP-Reeves': 2011,
}


# NOXP's real hierarchy (confirmed live, 2026-09-08/09 -- see
# planning/source-and-pilot-register.md, "NOXP archive hierarchy") is ten
# top-level branches under RRDD/NOXP, not the CAMPAIGN_YEARS scheme used
# elsewhere -- NOXP was not deployed for TORUS/PERiLS/LIFT. `real_time_test`
# is deliberately excluded (test content, matches NoxpArchive's own
# `_excluded` filter). Each entry is its own bounded crawl root so one
# campaign's discovery can never block another's, and so a partial crawl of
# one root doesn't imply anything about the rest.
_NOXP_CAMPAIGN_ROOTS: dict[str, str] = paths.NOXP_CAMPAIGNS

# Catalog pages already fetched are memoized on this NoxpArchive instance
# and reused across date selections. Explicit refresh invalidates both the
# date results and the underlying catalog memo.
_NOXP_CACHE_DIR = Path.home() / ".cache" / "storm" / "noxp"
_NOXP_SCAN_BUDGET = 2  # Pages per branch, not scientific file downloads.
_NOXP_STARTUP_BUDGET = 10  # Shared network-request cap across all NOXP roots.
_CACHE_TTL_SECONDS = 24 * 60 * 60
_DATE_CACHE_PATH = Path.home() / '.cache' / 'storm' / 'archive-dates.json'
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnownPlatform:
    platform_id: str
    display_name: str
    family: str
    key: object
    # The physical vehicle/instrument site this belongs to, e.g. "DL Truck"
    # or "CLAMPS 1" -- distinct from `family` (which data-fetching system
    # produced this entry, e.g. "CLAMPS Raw Lidar" vs "CLAMPS Winds").
    # One vehicle commonly has several families; this is what groups them
    # back together for a human choosing "which instrument," not "which
    # fetcher." Defaults to "" for callers (tests, NOXP) that don't need
    # cross-family grouping.
    site: str = ""


class ScanCanceled(Exception):
    pass


def _check_cancel(cancel: Event):
    if cancel.is_set():
        raise ScanCanceled()


def _catalog_kind(url: str) -> str:
    """Which case-package data type a THREDDS listing belongs to."""
    if "/NOXP/" in url:
        return "noxp radar"
    if "/FOFS/" in url:
        return "mesonet"
    return "raw lidar" if "/CLAMPS/" in url else "listings"


def _fetch_catalog_html(url: str, cancel: Event) -> str:
    """One paced request with one interruptible retry; never bypass TLS checks.

    404 means a registered optional directory is absent. 410 is deliberately
    unknown: this host has also returned it for catalogs accessible in browsers.
    A socket already blocked in urllib returns at its timeout; cancellation
    stops reading, retries and subsequent requests, without killing a thread.
    """
    from core import package_sources, provenance
    packaged = package_sources.get(url)       # an opened case package's copy of this listing
    if packaged is not None:
        return packaged.decode("utf-8", errors="replace")
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    for attempt in range(2):
        _check_cancel(cancel)
        try:
            with urlopen(request, timeout=_REQUEST_TIMEOUT, context=NSSL_SSL_CONTEXT) as response:
                chunks, size = [], 0
                deadline = time.monotonic() + 15
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _MAX_CATALOG_BYTES or time.monotonic() > deadline:
                        raise ValueError("Catalog exceeds listing size/time limit")
                html = b"".join(chunks).decode("utf-8", errors="replace")
                if "thredds" not in html.lower() or "catalog" not in html.lower():
                    raise ValueError("Server did not return a THREDDS catalog")
                provenance.record(_catalog_kind(url), url, html.encode("utf-8"))
                return html
        except HTTPError as exc:
            if exc.code == 404:
                return ""
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise
        except (URLError, TimeoutError):
            if attempt:
                raise
        if cancel.wait(0.75):
            raise ScanCanceled()
    raise AssertionError("unreachable")


def _dates_from_filenames(filenames) -> list[date]:
    found = set()
    for name in filenames:
        match = _DATE_RE.search(name)
        if match:
            try:
                found.add(datetime.strptime(match[1], "%Y%m%d").date())
            except ValueError:
                pass
    return sorted(found)


@dataclass(frozen=True)
class CatalogSpec:
    path: str
    suffix: str | tuple[str, ...]
    year: int | None = None  # Only for genuinely year-partitioned catalogs.

    @property
    def url(self):
        return f"{_CATALOG_ROOT}{self.path}/catalog.html"


@dataclass(frozen=True)
class RecursiveCatalogSpec:
    """A bounded, budget-limited crawl root (NOXP), unlike CatalogSpec's
    single flat directory listing. `year` exists only so this can share
    _scan_order()'s priority logic without a special case there."""
    path: str
    year: int | None = None

    @property
    def url(self):
        return f"{_CATALOG_ROOT}{self.path}/catalog.html"


@dataclass(frozen=True)
class FofsIndexSpec:
    """A mobile-mesonet vehicle's dates from the crawled FOFS file index
    (archive/fofs_index.py) instead of one fixed folder, since that tree is
    reorganized often. `vehicle=None` collects every vehicle folder no
    registered platform covers, so a new vehicle's dates still show."""
    vehicle: str | None
    year: int | None = None

    @property
    def url(self):
        return f"{_CATALOG_ROOT}{paths.FOFS_MESONET}/catalog.html#{self.vehicle or 'other-vehicles'}"


class _CatalogLinks(HTMLParser):
    def __init__(self, spec, *, dates_only=False):
        super().__init__()
        self.spec = spec
        self.filenames = set()
        self.date_stamps = set()
        self._dates_only = dates_only

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlsplit(urljoin(self.spec.url, href))
        if parsed.hostname != urlsplit(_CATALOG_ROOT).hostname:
            return
        dataset = parse_qs(parsed.query).get("dataset", [None])[0]
        path = dataset if dataset is not None else unquote(parsed.path).removeprefix("/thredds/fileServer/")
        prefix = self.spec.path + "/"
        if path.startswith(prefix):
            name = path[len(prefix):]
            if "/" not in name and name.endswith(self.spec.suffix):
                if self._dates_only:
                    match = _DATE_RE.search(name)
                    if match:
                        self.date_stamps.add(match[1])
                else:
                    self.filenames.add(name)


def catalogs_for_platform(platform: KnownPlatform) -> tuple[CatalogSpec | RecursiveCatalogSpec, ...]:
    key = platform.key
    if platform.family == "FOFS Mobile Mesonet":
        return (FofsIndexSpec(key),)
    if platform.family == "CLAMPS Raw Lidar":
        return (CatalogSpec(key.path, ".cdf"),)
    if platform.family == "CLAMPS Winds":
        return (CatalogSpec(paths.clamps_processed(key.platform_dir, key.datastream), ".cdf"),)
    if platform.family == "CLAMPS TROPoe":
        return tuple(CatalogSpec(paths.clamps_processed(key.platform_dir, paths.clamps_tropoe_stream(v, key.unit_suffix)), (".nc", ".cdf")) for v in _TROPOE_VARIANTS)
    if platform.family == "CLAMPS Surface":
        return (CatalogSpec(paths.clamps_ingested(key.platform_dir, key.datastream), ".cdf"),)
    if platform.family == "CLAMPS Sondes":
        return (CatalogSpec(paths.clamps_ingested(_SONDE_PLATFORM_DIR, _SONDE_DATASTREAM), ".skewT.text"),)
    if platform.family == "PERiLS UAS":
        return tuple(CatalogSpec(paths.coptersonde_iop(y, iop), ".cdf", year=int(y)) for y, iop in KNOWN_PERILS_IOPS)
    if platform.family == "NOXP Radar":
        return (RecursiveCatalogSpec(key),)
    raise ValueError(f"Unregistered source family: {platform.family}")


@dataclass(frozen=True)
class PlatformAvailability:
    dates: frozenset[date]
    checked: int
    total: int
    errors: tuple[str, ...]

    @property
    def complete(self):
        return self.checked == self.total and not self.errors


@dataclass(frozen=True)
class AvailabilitySnapshot:
    platforms: dict[str, PlatformAvailability]
    checked: int
    total: int
    cached: bool = False

    @property
    def dates(self):
        return frozenset(d for result in self.platforms.values() for d in result.dates)


class AvailabilityIndex:
    """Owned by one worker thread. Reuse successful disk listings for 24 hours.
    Date changes reuse session results; explicit refresh rechecks upstream.
    """
    def __init__(self, platforms=None, fetch=None, pace=0.2, noxp=None, cache_path=None, now=None):
        self.platforms = tuple(ALL_PLATFORMS if platforms is None else platforms)
        self._specs = {p.platform_id: catalogs_for_platform(p) for p in self.platforms}
        self._results = {}
        self._cached_dates = {}
        self._cache_records = {}
        self._cache_backed = set()
        self._budget_limited = set()
        self._clock = now or time.time
        # Injected/test inventories are isolated unless given an explicit cache.
        self._cache_path = Path(cache_path) if cache_path is not None else (_DATE_CACHE_PATH if platforms is None and fetch is None else None)
        self._latest_seen = {}
        self._fetch = fetch or _fetch_catalog_html
        self._pace = pace
        # Own one NoxpArchive so its internal per-URL catalog memo survives
        # across date selections, and so a recursive crawl
        # uses the same (possibly test-monkeypatched) fetch function as
        # every other spec, rather than NoxpArchive's own default.
        self._noxp = noxp or NoxpArchive(_NOXP_CACHE_DIR, fetch_catalog=self._fetch)
        self._read_cache()

    def _read_cache(self):
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            if self._cache_path.stat().st_size > 16 * 1024 * 1024:
                return
            payload = json.loads(self._cache_path.read_text())
            if not isinstance(payload, dict) or payload.get('version') != 1:
                return
            records = payload.get('catalogs', {})
            if not isinstance(records, dict):
                return
            for spec in {s for specs in self._specs.values() for s in specs}:
                record = records.get(spec.url)
                if not record:
                    continue
                dates = frozenset(date.fromisoformat(d) for d in record['dates'])
                self._cached_dates[spec] = dates
                self._cache_records[spec.url] = record
                self._cache_backed.add(spec)
                if dates:
                    self._latest_seen[spec] = max(dates).year
                age = self._clock() - float(record['checked_at'])
                # A budget_limited record is a real but incomplete crawl --
                # it used to count as "already checked" here (the `or
                # record.get('budget_limited')` let an errored record
                # through the not-error gate), which meant scan()'s `if
                # spec in self._results: continue` skipped it forever on
                # every future launch until someone clicked refresh. Its
                # dates are still loaded above (self._cached_dates) so the
                # UI shows what's already known immediately; just don't
                # mark it done, so scan() retries it automatically and
                # makes further progress on its own.
                if 0 <= age < _CACHE_TTL_SECONDS and not record.get('error') and not record.get('budget_limited'):
                    self._results[spec] = (dates, record.get('error', ''))
        except (OSError, ValueError, TypeError, KeyError):
            log.warning('Ignoring unreadable archive date cache')

    def _write_cache(self, spec):
        if self._cache_path is None:
            return
        dates, error = self._results[spec]
        # Failures retain previously known dates without declaring them current.
        if error:
            dates = dates | self._cached_dates.get(spec, frozenset())
            self._results[spec] = (dates, error)
        self._cache_records[spec.url] = dict(dates=sorted(d.isoformat() for d in dates),
                                            error=error, budget_limited=spec in self._budget_limited, checked_at=self._clock())
        self._cached_dates[spec] = dates
        if not error:
            self._cache_backed.discard(spec)
        import tempfile
        temporary = None
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self._cache_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'catalogs': self._cache_records}, stream)
            temporary.replace(self._cache_path)
        except OSError as exc:
            log.warning('Could not save archive date cache: %s', exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


    def clear(self):
        self._results.clear()
        self._budget_limited.clear()
        self._cache_backed.update(self._cached_dates)
        # Refresh must really recheck upstream catalogs, not replay stale HTML.
        if isinstance(self._noxp, NoxpArchive):
            self._noxp.clear_catalog_cache()

    def snapshot(self):
        platforms = {}
        for pid, specs in self._specs.items():
            results = [self._results[s] for s in specs if s in self._results]
            platforms[pid] = PlatformAvailability(
                frozenset(d for spec in specs for d in self._results.get(spec, (self._cached_dates.get(spec, ()), ''))[0]), len(results), len(specs),
                tuple(error for _, error in results if error),
            )
        return AvailabilitySnapshot(platforms, len(self._results), len({s for specs in self._specs.values() for s in specs}), bool(self._cache_backed))

    def _scan_order(self):
        """Recent sources first, then older ones; round-robin tied sources so
        one platform's raw/variant catalogs cannot delay every other platform.
        Flat catalogs arrive as complete listings, not per-year pages.
        """
        priorities = {}
        for platform in self.platforms:
            hint = _LATEST_OBSERVED_YEAR.get(platform.platform_id, 2026 if platform.family == 'CLAMPS Raw Lidar' else date.max.year)
            for position, spec in enumerate(self._specs[platform.platform_id]):
                year = spec.year or self._latest_seen.get(spec, hint)
                priority = (-year, isinstance(spec, RecursiveCatalogSpec), position)
                priorities[spec] = min(priorities.get(spec, priority), priority)
        return sorted(priorities, key=priorities.get)

    def scan(self, cancel: Event):
        _check_cancel(cancel)
        yield self.snapshot()
        noxp_remaining = _NOXP_STARTUP_BUDGET
        for spec in self._scan_order():
            _check_cancel(cancel)
            if spec in self._results:
                continue
            if cancel.wait(self._pace):
                raise ScanCanceled()
            try:
                if isinstance(spec, FofsIndexSpec):
                    from archive import fofs_index
                    index = fofs_index.get_index()
                    if index is None:
                        raise RuntimeError("FOFS file index unavailable")
                    vehicles = ({spec.vehicle} if spec.vehicle is not None
                                else index.vehicles() - set(KNOWN_FOFS_PLATFORMS))
                    dates = frozenset(d for v in vehicles for d in index.dates(v))
                    self._results[spec] = (dates, "")
                elif isinstance(spec, RecursiveCatalogSpec):
                    if noxp_remaining <= 0:
                        self._budget_limited.add(spec)
                        self._results[spec] = (self._cached_dates.get(spec, frozenset()),
                                               "Additional NOXP dates require on-demand radar discovery; startup budget reached.")
                        self._write_cache(spec)
                        yield self.snapshot()
                        continue
                    inventory = self._noxp.discover(cancel=cancel, budget=min(_NOXP_SCAN_BUDGET, noxp_remaining), catalog_root=spec.url)
                    noxp_remaining -= inventory.requests_made
                    # NOXP filenames don't reliably carry an 8-digit YYYYMMDD
                    # substring (Sigmet names use YYMMDDHHMMSS) -- the generic
                    # _dates_from_filenames regex silently misses them.
                    # asset_from_url() already parsed each real nominal_time
                    # correctly per format; use that instead.
                    dates = frozenset(a.nominal_time.date() for a in inventory.assets if a.nominal_time is not None)
                    # A bounded crawl reaching its budget mid-tree is expected,
                    # not an error -- but it must never be reported as a
                    # complete/final listing. Surface it via the existing
                    # errors channel (still real dates, just not the whole
                    # story yet) so PlatformAvailability.complete stays
                    # honest. More complete case discovery belongs to the on-demand
                    # radar controls, not an unbounded startup operation.
                    errors = tuple(inventory.errors)
                    if inventory.pending and not errors:
                        self._budget_limited.add(spec)
                    if inventory.pending:
                        errors = errors + (
                            f"{spec.url}: {inventory.pending} subcatalog(s) not yet scanned "
                            f"this budget -- dates shown are partial; NOXP controls discover additional files on demand",
                        )
                    self._results[spec] = (dates, "; ".join(errors))
                else:
                    html = self._fetch(spec.url, cancel)
                    parser = _CatalogLinks(spec, dates_only=True)
                    parser.feed(html)
                    dates = frozenset(_dates_from_filenames(parser.date_stamps))
                    self._results[spec] = (dates, "")
                if dates:
                    self._latest_seen[spec] = max(dates).year
            except ScanCanceled:
                raise
            except Exception as exc:
                self._results[spec] = (frozenset(), f"{spec.url}: {exc}")
            _check_cancel(cancel)
            self._write_cache(spec)
            yield self.snapshot()


# "Stage names" -- what a person in the field would actually call each
# platform -- rather than the raw THREDDS directory/datastream token.
# Researched 2026-09-08 against Tyler's mesonet_reader.py / io.py
# (_CANONICAL_NAME_MAP) and archive/vehicle_aliases.py ("lid1" -> "dltruck"
# confirms dltruck is the mobile Doppler-lidar truck, not a generic name).
# "DLTRUCK1" appears under four different families because it's one
# physical vehicle instrumented four ways: FOFS tracks its onboard
# mesonet probe/GPS, CLAMPS Raw Lidar tracks its two Doppler lidars'
# unprocessed scans, CLAMPS Winds tracks the wind profiles retrieved from
# that same lidar (published under two stream names, DL1 and DL2 -- one
# instrument, confirmed 2026-09-25 -- each with a VAD and a CSM-scan wind
# product), and CLAMPS Sondes tracks the mobile radiosonde
# launches made from it. Called "LiDAR Truck" here (its instrument
# registry site name -- see _vehicle_site()/_FOFS_VEHICLE_SITE below),
# not the raw "dltruck"/"DLTRUCK1" token.
# mg1/mg2/mg3 and noxp_scout are deliberately NOT expanded to a guessed
# full name -- planning/source-and-pilot-register.md already documents
# that their real meaning is unconfirmed (noxp_scout is not evidence of
# a mobile-radar archive); only case/punctuation is normalized here.
_FOFS_STAGE_NAMES: dict[str, str] = {
    "dltruck": "LiDAR Truck (mesonet)",
    "farfield": "FarField",
    "hailcam": "HailCam",
    "noxp_scout": "NOXP Scout",
    "probe1": "Probe 1", "probe2": "Probe 2", "probe3": "Probe 3",
    "probe4": "Probe 4", "probe5": "Probe 5", "probe7": "Probe 7",
    "probe9": "Probe 9",
    "windsonde1": "WindSonde 1", "windsonde2": "WindSonde 2",
    "mg1": "MG1", "mg2": "MG2", "mg3": "MG3",
}

_CLAMPS_WIND_STAGE_NAMES: dict[str, str] = {
    "CLAMPS1-VAD": "CLAMPS 1 (VAD)",
    "CLAMPS2-VAD": "CLAMPS 2 (VAD)",
    # DL1 and DL2 are one lidar published under two stream names (Tyler, 2026-09-25)
    "DLTRUCK1-DL1-VAD": "LiDAR Truck — Lidar (VAD)",
    "DLTRUCK1-DL2-VAD": "LiDAR Truck — Lidar, DL2 stream (VAD)",
    # "CSM wind", not "CSM" -- CLAMPS Raw Lidar already uses "(CSM)" for the
    # raw scan this is retrieved *from* (see RAW-LIDAR-DLTRUCK1-DL{1,2}-CSM
    # below); the identical abbreviation on two different files read as a
    # duplicate platform rather than a derived product.
    "DLTRUCK1-DL1-CSMWINDS": "LiDAR Truck — Lidar (CSM wind)",
    "DLTRUCK1-DL2-CSMWINDS": "LiDAR Truck — Lidar, DL2 stream (CSM wind)",
}

_SURFACE_KIND_LABELS: dict[str, str] = {
    "met_tower": "Met Tower",
    "mwr": "MWR",
}


def _clamps_site_label(platform_id: str) -> str:
    """"CLAMPS1" -> "CLAMPS 1" (a site number, not part of the word)."""
    return re.sub(r"(CLAMPS)(\d)", r"\1 \2", platform_id)


# The mobile Doppler-lidar truck's vehicle-grouping site name, shared by
# every family that references it (FOFS, CLAMPS Raw Lidar, CLAMPS Winds,
# CLAMPS Sondes) so they all land under the same site.
_LIDAR_TRUCK_SITE = "LiDAR Truck"

# FOFS vehicle key -> the vehicle-grouping site name, for the one vehicle
# (the lidar truck) that also appears under other families. Every other
# FOFS vehicle has no cross-family counterpart, so its site is just its
# own stage name -- see _build_registry()'s fallback below.
_FOFS_VEHICLE_SITE: dict[str, str] = {"dltruck": _LIDAR_TRUCK_SITE}


def _vehicle_site(platform_dir: str) -> str:
    """THREDDS platform_dir -> the physical vehicle/trailer it groups
    under, e.g. 'dltruck/dltruck1' -> 'LiDAR Truck', 'clamps/clamps1' ->
    'CLAMPS 1'. The truck's lidar streams (DL1/DL2) share the same
    platform_dir and so the same site -- one instrument on one vehicle."""
    if platform_dir.startswith("dltruck/"):
        return _LIDAR_TRUCK_SITE
    if platform_dir == "clamps/clamps1":
        return "CLAMPS 1"
    if platform_dir == "clamps/clamps2":
        return "CLAMPS 2"
    raise ValueError(f"Unrecognized CLAMPS platform_dir: {platform_dir!r}")


def _build_registry() -> list[KnownPlatform]:
    """display_name deliberately omits the family name (already shown
    alongside it wherever this is rendered, e.g. "CLAMPS Winds —
    CLAMPS 1 (VAD)") -- repeating it in both halves just pushed every UI
    that shows "family — display_name" past a normal dialog's width."""
    platforms: list[KnownPlatform] = []

    for vehicle in KNOWN_FOFS_PLATFORMS:
        platforms.append(KnownPlatform(
            platform_id=f"FOFS-{vehicle}",
            display_name=_FOFS_STAGE_NAMES.get(vehicle, vehicle),
            family="FOFS Mobile Mesonet",
            key=vehicle,
            site=_FOFS_VEHICLE_SITE.get(vehicle, _FOFS_STAGE_NAMES.get(vehicle, vehicle)),
        ))
    # Vehicle folders THREDDS adds that aren't registered above still get
    # their dates on the calendar (and are loaded like any other vehicle).
    platforms.append(KnownPlatform(
        platform_id="FOFS-other", display_name="Other mesonet vehicles",
        family="FOFS Mobile Mesonet", key=None, site="Other mesonet vehicles",
    ))

    for source in KNOWN_RAW_LIDAR_SOURCES:
        label_prefix = (source.platform_id.rsplit('-', 1)[0]
                        .replace('DLTRUCK1-DL1', 'LiDAR Truck — Lidar')
                        .replace('DLTRUCK1-DL2', 'LiDAR Truck — Lidar, DL2 stream')
                        .replace('CLAMPS', 'CLAMPS '))
        platforms.append(KnownPlatform(f'RAW-LIDAR-{source.platform_id}',
                                       f'{label_prefix} ({source.product.upper()})', 'CLAMPS Raw Lidar', source,
                                       site=_vehicle_site(source.platform_dir)))

    for source in KNOWN_CLAMPS_WIND_SOURCES:
        platforms.append(KnownPlatform(
            platform_id=f"WIND-{source.platform_id}",
            display_name=_CLAMPS_WIND_STAGE_NAMES.get(source.platform_id, source.platform_id),
            family="CLAMPS Winds",
            key=source,
            site=_vehicle_site(source.platform_dir),
        ))

    for platform in KNOWN_CLAMPS_TROPOE_PLATFORMS:
        platforms.append(KnownPlatform(
            platform_id=f"TROPOE-{platform.platform_id}",
            display_name=_clamps_site_label(platform.platform_id),
            family="CLAMPS TROPoe",
            key=platform,
            site=_vehicle_site(platform.platform_dir),
        ))

    for source in KNOWN_CLAMPS_SURFACE_SOURCES:
        kind_label = _SURFACE_KIND_LABELS.get(source.kind, source.kind)
        platforms.append(KnownPlatform(
            platform_id=f"SFC-{source.platform_id}-{source.kind}",
            display_name=f"{_clamps_site_label(source.platform_id)} — {kind_label}",
            family="CLAMPS Surface",
            key=source,
            site=_vehicle_site(source.platform_dir),
        ))

    platforms.append(KnownPlatform(
        platform_id="SONDE-DLTRUCK1",
        display_name="LiDAR Truck (mobile sonde)",
        family="CLAMPS Sondes",
        key=None,
        site=_vehicle_site(_SONDE_PLATFORM_DIR),
    ))

    platforms.append(KnownPlatform(
        platform_id="COPTERSONDE-PERILS",
        display_name="CopterSonde",
        family="PERiLS UAS",
        key=None,
        site="CopterSonde",
    ))

    for label, path in _NOXP_CAMPAIGN_ROOTS.items():
        platforms.append(KnownPlatform(
            platform_id=f"NOXP-{label.replace(' ', '_')}",
            display_name=label,
            family="NOXP Radar",
            key=path,
            # One "NOXP" choice covers every campaign-year root -- this is
            # a Browse-dropdown presentation grouping only; the underlying
            # per-year discovery/crawl logic (NoxpArchive, _NOXP_CAMPAIGN_ROOTS)
            # is untouched and still paused for its own reconciliation.
            site="NOXP",
        ))

    return platforms


ALL_PLATFORMS: list[KnownPlatform] = _build_registry()


def platforms_by_site() -> dict[str, list[KnownPlatform]]:
    """Group by physical vehicle/instrument site rather than by which
    fetcher produced the entry -- e.g. every DL Truck product (mesonet,
    both raw lidars, both wind retrievals, mobile sondes) comes back under
    one "DL Truck" key, instead of scattered across four families."""
    grouped = {}
    for platform in ALL_PLATFORMS:
        grouped.setdefault(platform.site, []).append(platform)
    return grouped
