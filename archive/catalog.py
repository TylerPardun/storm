"""Live, on-demand availability browsing across every archive data source
built this session: FOFS mobile mesonet, CLAMPS winds/TROPoe/surface/
sondes, and PERiLS CopterSonde.

Organizes by campaign, by year, by platform (its known dates), and by
coverage (how many known platforms have data on one date) -- see
planning/architecture-and-delivery.md "12" for the underlying pattern
this follows (per-instrument registries, query THREDDS directly, no
campaign/date roster gating what can be asked).

Deliberately no persistent cache: THREDDS doesn't change often, and the
answer here (Tyler, 2026-09-08) was a live check on each user query
rather than maintaining/refreshing a background index. Every function
that touches the network here does a real request when called -- there
is nothing precomputed to go stale.

This module is purely additive: it reuses each source's own catalog-
listing/registry code where that already exists (TROPoe, surface,
sondes, CopterSonde) and adds the same small amount of listing logic
for the three sources that only ever needed a single deterministic
per-date URL before (FOFS, CLAMPS winds), without changing any of that
existing, already-tested code.
"""

from __future__ import annotations

import logging
import re
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS
from archive.fetchers.clamps_wind_archive_fetcher import KNOWN_CLAMPS_WIND_SOURCES
from archive.fetchers.clamps_tropoe_archive_fetcher import (
    KNOWN_CLAMPS_TROPOE_PLATFORMS,
    _VARIANT_PREFERENCE as _TROPOE_VARIANTS,
    _list_catalog_filenames as _tropoe_list_filenames,
)
from archive.fetchers.clamps_surface_archive_fetcher import (
    KNOWN_CLAMPS_SURFACE_SOURCES,
    _list_catalog_filenames as _surface_list_filenames,
)
from archive.fetchers.clamps_sonde_archive_fetcher import (
    _SONDE_PLATFORM_DIR,
    _SONDE_DATASTREAM,
    _list_catalog_filenames as _sonde_list_filenames,
)
from archive.fetchers.coptersonde_archive_fetcher import (
    KNOWN_PERILS_IOPS,
    _list_catalog_filenames as _coptersonde_list_filenames,
)

log = logging.getLogger(__name__)

_FOFS_CATALOG_ROOT = "https://data.nssl.noaa.gov/thredds/catalog/FOFS/Mobile-Mesonet/data"
_CLAMPS_CATALOG_ROOT = "https://data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 30
_RETRY_BACKOFF_S = (2.0, 5.0)
_DATE_RE = re.compile(r"(\d{8})")

# Campaign date ranges are a static local reference, not a THREDDS query --
# see planning/source-and-pilot-register.md "3" (from CH2). Used only to
# label/filter results by campaign; discovery itself never depends on it.
CAMPAIGN_YEARS: dict[str, tuple[int, ...]] = {
    "VORTEX2": (2009, 2010),
    "RiVorS": (2017,),
    "TORUS": (2019, 2022, 2023),
    "PERiLS": (2022, 2023),
    "LIFT": (2024, 2025, 2026),
}


@dataclass(frozen=True)
class KnownPlatform:
    """One physical instrument/platform this module knows how to check
    for data. `family` groups related platforms for display (e.g. all
    FOFS vehicles); `key` is opaque and only meaningful to `list_dates`."""
    platform_id: str
    display_name: str
    family: str
    key: object


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _urlopen_with_retry(request: Request, *, timeout: int):
    """See vehicle_obs_archive_fetcher.py: data.nssl.noaa.gov soft-throttles
    bursts of requests with connection timeouts rather than a clean
    429/503. Never retry 404/410 (a real "nothing here" answer)."""
    import time as _time

    attempts = len(_RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=timeout, context=_ssl_context())
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except URLError as exc:
            if attempt == attempts - 1:
                raise
        _time.sleep(_RETRY_BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _fetch_catalog_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen_with_retry(request, timeout=_REQUEST_TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 404/network/SSL, all "nothing here"
        log.debug("Catalog listing failed for %s: %s", url, exc)
        return ""


def _dates_from_filenames(filenames: list[str]) -> list[date]:
    """Pull the first 8-digit YYYYMMDD run out of each filename. Every
    source built this session embeds the date this way regardless of
    the rest of its naming convention."""
    found: set[date] = set()
    for name in filenames:
        m = _DATE_RE.search(name)
        if not m:
            continue
        try:
            found.add(datetime.strptime(m.group(1), "%Y%m%d").date())
        except ValueError:
            continue
    return sorted(found)


def _list_fofs_dates(vehicle_dir: str) -> list[date]:
    url = f"{_FOFS_CATALOG_ROOT}/{vehicle_dir}/processed/catalog.html"
    html = _fetch_catalog_html(url)
    filenames = re.findall(rf'dataset=FOFS/Mobile-Mesonet/data/{re.escape(vehicle_dir)}/processed/([^"]+\.nc)"', html)
    return _dates_from_filenames(filenames)


def _list_wind_dates(source) -> list[date]:
    url = f"{_CLAMPS_CATALOG_ROOT}/{source.platform_dir}/processed/{source.datastream}/catalog.html"
    html = _fetch_catalog_html(url)
    prefix = re.escape(f"FRDD/CLAMPS/{source.platform_dir}/processed/{source.datastream}/")
    filenames = re.findall(rf'dataset={prefix}([^"]+\.cdf)"', html)
    return _dates_from_filenames(filenames)


def _list_tropoe_dates(platform) -> list[date]:
    """Union of dates across every retrieval variant -- cheap enough for
    a single explicit user query (a handful of listing requests)."""
    all_dates: set[date] = set()
    for variant in _TROPOE_VARIANTS:
        datastream = f"clampstropoe10.{variant}.{platform.unit_suffix}"
        filenames = _tropoe_list_filenames(platform.platform_dir, datastream)
        all_dates.update(_dates_from_filenames(filenames))
    return sorted(all_dates)


def _list_surface_dates(source) -> list[date]:
    filenames = _surface_list_filenames(source.platform_dir, source.datastream)
    return _dates_from_filenames(filenames)


def _list_sonde_dates(_unused=None) -> list[date]:
    filenames = _sonde_list_filenames(_SONDE_PLATFORM_DIR, _SONDE_DATASTREAM)
    return _dates_from_filenames(filenames)


def _list_coptersonde_dates(_unused=None) -> list[date]:
    all_dates: set[date] = set()
    for year, iop in KNOWN_PERILS_IOPS:
        filenames = _coptersonde_list_filenames(year, iop)
        all_dates.update(_dates_from_filenames(filenames))
    return sorted(all_dates)


def _build_registry() -> list[KnownPlatform]:
    """display_name deliberately omits the family name (already shown
    alongside it wherever this is rendered, e.g. "CLAMPS Winds —
    CLAMPS1-VAD") -- repeating it in both halves just pushed every UI
    that shows "family — display_name" past a normal dialog's width."""
    platforms: list[KnownPlatform] = []

    for vehicle in KNOWN_FOFS_PLATFORMS:
        platforms.append(KnownPlatform(
            platform_id=f"FOFS-{vehicle}",
            display_name=vehicle,
            family="FOFS Mobile Mesonet",
            key=vehicle,
        ))

    for source in KNOWN_CLAMPS_WIND_SOURCES:
        platforms.append(KnownPlatform(
            platform_id=f"WIND-{source.platform_id}",
            display_name=source.platform_id,
            family="CLAMPS Winds",
            key=source,
        ))

    for platform in KNOWN_CLAMPS_TROPOE_PLATFORMS:
        platforms.append(KnownPlatform(
            platform_id=f"TROPOE-{platform.platform_id}",
            display_name=platform.platform_id,
            family="CLAMPS TROPoe",
            key=platform,
        ))

    for source in KNOWN_CLAMPS_SURFACE_SOURCES:
        platforms.append(KnownPlatform(
            platform_id=f"SFC-{source.platform_id}-{source.kind}",
            display_name=f"{source.platform_id} ({source.kind})",
            family="CLAMPS Surface",
            key=source,
        ))

    platforms.append(KnownPlatform(
        platform_id="SONDE-DLTRUCK1",
        display_name="dltruck1",
        family="CLAMPS Sondes",
        key=None,
    ))

    platforms.append(KnownPlatform(
        platform_id="COPTERSONDE-PERILS",
        display_name="CopterSonde",
        family="PERiLS UAS",
        key=None,
    ))

    return platforms


ALL_PLATFORMS: list[KnownPlatform] = _build_registry()

_LIST_FUNCS = {
    "FOFS Mobile Mesonet": _list_fofs_dates,
    "CLAMPS Winds": _list_wind_dates,
    "CLAMPS TROPoe": _list_tropoe_dates,
    "CLAMPS Surface": _list_surface_dates,
    "CLAMPS Sondes": _list_sonde_dates,
    "PERiLS UAS": _list_coptersonde_dates,
}


def list_dates_for_platform(platform: KnownPlatform) -> list[date]:
    """Every date this one platform has data for, found live right now.
    A single query (or a small handful, for multi-variant sources) --
    cheap enough to run whenever the user picks a platform to browse."""
    fn = _LIST_FUNCS[platform.family]
    return fn(platform.key)


def platforms_by_family() -> dict[str, list[KnownPlatform]]:
    grouped: dict[str, list[KnownPlatform]] = {}
    for p in ALL_PLATFORMS:
        grouped.setdefault(p.family, []).append(p)
    return grouped


def campaign_for_year(year: int) -> "str | None":
    for name, years in CAMPAIGN_YEARS.items():
        if year in years:
            return name
    return None


def years_for_campaign(name: str) -> tuple[int, ...]:
    return CAMPAIGN_YEARS.get(name, ())


def coverage_for_date(target: date, platforms: "list[KnownPlatform] | None" = None) -> dict[str, bool]:
    """Check every known platform (or a given subset) for this one date.
    An explicit, heavier query by design -- checks every platform live,
    not a lookup against anything precomputed. Runs the checks
    concurrently (matching the worker-pool pattern already used for
    live vehicle-obs fetching) since this is real end-user app usage,
    not the sandboxed/backgrounded dev-shell context noted elsewhere in
    this codebase where concurrent requests were unreliable."""
    targets = platforms if platforms is not None else ALL_PLATFORMS

    def _check(p: KnownPlatform) -> tuple[str, bool]:
        try:
            return p.platform_id, target in list_dates_for_platform(p)
        except Exception as exc:  # noqa: BLE001 - a coverage check must not raise
            log.warning("Coverage check failed for %s: %s", p.platform_id, exc)
            return p.platform_id, False

    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for platform_id, present in pool.map(_check, targets):
            results[platform_id] = present
    return results
