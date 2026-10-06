"""CLAMPS trailer surface meteorology for archive playback.

Maps FRDD/CLAMPS surface-met fields onto STORM's existing Observation
shape (core/observation.py), the same "reuse an existing type" approach
used for CLAMPS winds (VADProfile), TROPoe (Sounding), and mobile
mesonet.

Two sources exist, and neither covers every platform/date:

  - A dedicated met tower ("clampsmetC2.a1") -- confirmed present for
    clamps2 only (no clamps1 equivalent found), spanning 2016-2021. Its
    own file attributes carry an explicit warning: "Wind speed direction
    needs offset applied to account for the trailer heading -- this has
    not been applied to these data." Rather than guess at the correction
    (no ground truth available here to verify a sign/offset convention
    against), wind_dir_deg is left unset for met-tower observations; the
    other fields (temperature, humidity, pressure, wind speed) don't
    depend on heading and are used as-is.
  - The MWR's embedded surface-met fields ("clampsmwr{C1,C2}.a1") --
    present on both trailers, and its own field comment confirms
    "orientation of the trailer has already been included" in wind
    direction, so it's trusted directly there.

The met tower, when present for the requested date, is preferred (a
dedicated instrument over an ancillary sensor on the MWR); the MWR
fields are the fallback everywhere else. Both share a per-record -999.0
fill-value sentinel with no declared _FillValue attribute (confirmed on
a real met tower file; the one MWR file inspected happened to have none,
but the same convention is assumed and filtered defensively).

dltruck1 also has an "ingested/dltruckdlsfcDL1.b1" datastream whose name
suggests truck surface obs, but real files pulled from it turned out to
be sparse ingest-test snippets (~30 seconds of data, at NSSL HQ's
location, not a deployed field day) -- not a usable surface-obs source,
and redundant with the truck's own FOFS mobile-mesonet stream regardless
(archive/fetchers/vehicle_obs_archive_fetcher.py already covers dltruck's
surface obs at 1-Hz for full deployed days, and every other known FOFS
platform). This adapter is scoped to the clamps1/clamps2 trailers, which
have no FOFS coverage at all.

Relative humidity is converted to dewpoint with the standard
Magnus-Tetens approximation (a=17.625, b=243.04 -- Alduchov & Eskridge
1996), since both sources report RH, not dewpoint, directly.
"""

from __future__ import annotations

import logging
import math
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.observation import Observation
from core import package_sources

from archive import thredds_paths as paths
log = logging.getLogger(__name__)

_CATALOG_ROOT = f"{paths.catalog_root()}/{paths.CLAMPS}"
_FRDD_ROOT = paths.file_url(paths.CLAMPS)
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 60
_RETRY_BACKOFF_S = (2.0, 5.0)
_FILL_SENTINEL_MAX = -900.0  # values at/below this are the observed -999.0-style fill


@dataclass(frozen=True)
class ClampsSurfaceSource:
    platform_id: str    # display id, e.g. "CLAMPS2"
    platform_dir: str   # THREDDS path under FRDD/CLAMPS, e.g. "clamps/clamps2"
    datastream: str     # e.g. "clampsmwrC2.a1"
    kind: str            # "met_tower" | "mwr"
    trust_wind_direction: bool


# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md. dltruck1 deliberately excluded;
# see module docstring. Order within each platform is preference order --
# fetch_clamps_surface_observations tries them in order and uses the
# first with data for the requested date.
KNOWN_CLAMPS_SURFACE_SOURCES: tuple[ClampsSurfaceSource, ...] = tuple(
    ClampsSurfaceSource(*row) for row in paths.CLAMPS_SURFACE_STREAMS)
# Files are found through the catalog, never by guessing names: a file's
# time suffix is when that day's recording started, and 658 of CLAMPS1's
# 1,256 MWR days (checked 2026-09-25) have no .000000 file. The MWR streams
# end in 2023 (CLAMPS1 2023-06-09, CLAMPS2 2023-11-28).
_LISTING_CACHE: dict[tuple[str, str], list[str]] = {}


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


def _list_catalog_filenames(platform_dir: str, datastream: str) -> list[str]:
    """Return every filename listed for one datastream's THREDDS catalog
    page. An empty list means no such datastream (404) or a fetch
    failure -- normal, not an error, when a platform doesn't have it."""
    url = f"{_CATALOG_ROOT}/{platform_dir}/ingested/{datastream}/catalog.html"
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        html = package_sources.read_url("clamps surface", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 404/network/SSL, all treated as "nothing here"
        log.debug("CLAMPS surface catalog listing failed for %s/%s: %s", platform_dir, datastream, exc)
        return []

    prefix = re.escape(f"{paths.clamps_ingested(platform_dir, datastream)}/{datastream}.")
    pattern = re.compile(rf'dataset={prefix}([0-9.]+\.(?:nc|cdf))"')
    return [f"{datastream}.{m}" for m in pattern.findall(html)]


def _find_files(source: ClampsSurfaceSource, date_str: str) -> list[str]:
    """fileServer URLs of every file this source has for the date, in time
    order (a restarted instrument writes a second file for the day)."""
    key = (source.platform_dir, source.datastream)
    filenames = _LISTING_CACHE.get(key)
    if filenames is None:
        filenames = _list_catalog_filenames(source.platform_dir, source.datastream)
        if filenames:                      # an empty answer may be a failed fetch; ask again next time
            _LISTING_CACHE[key] = filenames
    prefix = f"{source.datastream}.{date_str}."
    return [paths.file_url(f"{paths.clamps_ingested(source.platform_dir, source.datastream)}/{name}")
            for name in sorted(f for f in filenames if f.startswith(prefix))]


def _dewpoint_c_from_rh(temp_c: float, rh_pct: float) -> "float | None":
    if rh_pct <= 0.0 or rh_pct > 100.0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(rh_pct / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def _valid(value: float) -> bool:
    import numpy as np
    return bool(np.isfinite(value) and value > _FILL_SENTINEL_MAX)


def parse_clamps_surface_netcdf(data: bytes, platform_id: str, trust_wind_direction: bool = True) -> list[Observation]:
    """Parse a CLAMPS surface-met file (met tower or MWR-embedded, same
    core variable names) into Observations. lat/lon may be a per-file
    scalar (MWR: a stationary trailer for that file) or a per-record
    array (met tower: confirmed varying within a single file on a real
    sample -- read as given rather than assuming a fixed site)."""
    import numpy as np
    import xarray as xr

    # Both readers are already declared in envs/storm.yml. Met-tower files
    # use classic NetCDF; MWR files and some tests use HDF5-backed NetCDF.
    # Select by file signature instead of requiring an undeclared netCDF4.
    import io

    engine = "scipy" if data.startswith(b"CDF") else "h5netcdf"
    with xr.open_dataset(io.BytesIO(data), engine=engine, decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        sfc_temp = np.asarray(ds["sfc_temp"].values, dtype="float64")
        sfc_rh = np.asarray(ds["sfc_rh"].values, dtype="float64")
        sfc_pres = np.asarray(ds["sfc_pres"].values, dtype="float64")
        sfc_wspd = np.asarray(ds["sfc_wspd"].values, dtype="float64")
        sfc_wdir = np.asarray(ds["sfc_wdir"].values, dtype="float64")
        lat_raw = np.asarray(ds["lat"].values, dtype="float64")
        lon_raw = np.asarray(ds["lon"].values, dtype="float64")

    # Some files (e.g. CLAMPS1 MWR, 2023) hold sfc_temp in kelvin while the
    # variable is labeled degC. A file-wide median above 150 is impossible in
    # degC (the surface record is ~57 degC), so the whole file is kelvin.
    valid_temp = sfc_temp[np.isfinite(sfc_temp) & (sfc_temp > _FILL_SENTINEL_MAX)]
    if valid_temp.size and float(np.median(valid_temp)) > 150.0:
        log.info("CLAMPS surface %s: sfc_temp is in kelvin despite its degC label; converted", platform_id)
        sfc_temp = np.where(sfc_temp > _FILL_SENTINEL_MAX, sfc_temp - 273.15, sfc_temp)

    per_record_location = lat_raw.ndim > 0
    lat_arr = lat_raw if per_record_location else None
    lon_arr = lon_raw if per_record_location else None
    site_lat = float(lat_raw) if not per_record_location else 0.0
    site_lon = float(lon_raw) if not per_record_location else 0.0

    observations: list[Observation] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        temp = sfc_temp[i]
        if not (np.isfinite(t_off) and _valid(temp)):
            continue

        lat = float(lat_arr[i]) if per_record_location else site_lat
        lon = float(lon_arr[i]) if per_record_location else site_lon
        if not (_valid(lat) and _valid(lon) and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0)):
            continue

        rh = sfc_rh[i]
        dewpoint = _dewpoint_c_from_rh(float(temp), float(rh)) if _valid(rh) else None

        wdir = sfc_wdir[i]
        observations.append(Observation(
            vehicle_id=platform_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc),
            icon_type=None,
            temperature_c=float(temp),
            dewpoint_c=dewpoint,
            wind_speed_ms=float(sfc_wspd[i]) if _valid(sfc_wspd[i]) else None,
            wind_dir_deg=float(wdir) if (trust_wind_direction and _valid(wdir)) else None,
            pressure_mb=float(sfc_pres[i]) if _valid(sfc_pres[i]) else None,
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations


def fetch_clamps_surface_observations(archive_date: datetime) -> "list[Observation] | None":
    """Try each known source in preference order (met tower before MWR,
    per-platform), retaining every platform with data for this date."""
    date_str = archive_date.strftime("%Y%m%d")
    by_platform = {}
    for source in KNOWN_CLAMPS_SURFACE_SOURCES:
        if source.platform_id in by_platform:
            continue
        urls = _find_files(source, date_str)
        if not urls:
            log.debug("CLAMPS surface: no %s file for %s on %s", source.kind, source.platform_id, date_str)
            continue

        observations = []
        for url in urls:
            request = Request(url, headers={"User-Agent": _USER_AGENT})
            try:
                data = package_sources.read_url("clamps surface", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - network/SSL errors, try the next file/source
                log.warning("CLAMPS surface fetch failed for %s (%s): %s", source.platform_id, source.kind, exc)
                continue
            try:
                observations += parse_clamps_surface_netcdf(data, source.platform_id, source.trust_wind_direction)
            except Exception as exc:  # noqa: BLE001 - malformed/unexpected schema
                log.warning("CLAMPS surface parse failed for %s (%s): %s", source.platform_id, source.kind, exc)

        if observations:
            unique = {obs.timestamp: obs for obs in observations}   # a restart can overlap
            by_platform[source.platform_id] = [unique[t] for t in sorted(unique)]

    return sorted((obs for rows in by_platform.values() for obs in rows), key=lambda obs: obs.timestamp) or None
