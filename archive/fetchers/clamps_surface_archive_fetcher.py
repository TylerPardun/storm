"""CLAMPS trailer surface meteorology for archive playback.

Maps the surface-met fields embedded in FRDD/CLAMPS MWR ingested files
onto STORM's existing Observation shape (core/observation.py), the same
"reuse an existing type" approach used for CLAMPS winds (VADProfile) and
TROPoe (Sounding).

dltruck1 also has an "ingested/dltruckdlsfcDL1.b1" datastream whose name
suggests truck surface obs, but real files pulled from it turned out to
be sparse ingest-test snippets (~30 seconds of data, at NSSL HQ's
location, not a deployed field day) -- not a usable surface-obs source,
and redundant with the truck's own FOFS mobile-mesonet stream regardless
(archive/fetchers/vehicle_obs_archive_fetcher.py already covers dltruck's
surface obs at 1-Hz for full deployed days, and every other known FOFS
platform). This adapter is scoped to the clamps1/clamps2 trailers
instead, which have no FOFS coverage at all -- the MWR's sfc_* fields
are the only surface-obs source found for them.

Relative humidity is converted to dewpoint with the standard
Magnus-Tetens approximation (a=17.625, b=243.04 -- Alduchov & Eskridge
1996), since the source reports RH, not dewpoint, directly.
"""

from __future__ import annotations

import io
import logging
import math
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.observation import Observation

log = logging.getLogger(__name__)

_FRDD_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 60
_RETRY_BACKOFF_S = (2.0, 5.0)


@dataclass(frozen=True)
class ClampsSurfaceSource:
    platform_id: str    # display id, e.g. "CLAMPS2"
    platform_dir: str   # THREDDS path under FRDD/CLAMPS, e.g. "clamps/clamps2"
    datastream: str     # e.g. "clampsmwrC2.a1"


# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md. dltruck1 deliberately excluded;
# see module docstring.
KNOWN_CLAMPS_SURFACE_SOURCES: tuple[ClampsSurfaceSource, ...] = (
    ClampsSurfaceSource("CLAMPS1", "clamps/clamps1", "clampsmwrC1.a1"),
    ClampsSurfaceSource("CLAMPS2", "clamps/clamps2", "clampsmwrC2.a1"),
)


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


def _source_url(source: ClampsSurfaceSource, date_str: str) -> str:
    return (
        f"{_FRDD_ROOT}/{source.platform_dir}/ingested/{source.datastream}/"
        f"{source.datastream}.{date_str}.000000.cdf"
    )


def _dewpoint_c_from_rh(temp_c: float, rh_pct: float) -> "float | None":
    if rh_pct <= 0.0 or rh_pct > 100.0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(rh_pct / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def parse_clamps_surface_netcdf(data: bytes, platform_id: str) -> list[Observation]:
    """Parse a CLAMPS MWR ingested file's embedded surface-met fields into
    Observations. The platform is a fixed trailer site, not a moving
    vehicle -- lat/lon/alt are per-file scalars, not per-record."""
    import numpy as np
    import xarray as xr

    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        sfc_temp = np.asarray(ds["sfc_temp"].values, dtype="float64")
        sfc_rh = np.asarray(ds["sfc_rh"].values, dtype="float64")
        sfc_pres = np.asarray(ds["sfc_pres"].values, dtype="float64")
        sfc_wspd = np.asarray(ds["sfc_wspd"].values, dtype="float64")
        sfc_wdir = np.asarray(ds["sfc_wdir"].values, dtype="float64")
        lat = float(ds["lat"].values)
        lon = float(ds["lon"].values)

    observations: list[Observation] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        temp = sfc_temp[i]
        if not (np.isfinite(t_off) and np.isfinite(temp)):
            continue

        rh = sfc_rh[i]
        dewpoint = _dewpoint_c_from_rh(float(temp), float(rh)) if np.isfinite(rh) else None

        observations.append(Observation(
            vehicle_id=platform_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc),
            icon_type=None,
            temperature_c=float(temp),
            dewpoint_c=dewpoint,
            wind_speed_ms=float(sfc_wspd[i]) if np.isfinite(sfc_wspd[i]) else None,
            wind_dir_deg=float(sfc_wdir[i]) if np.isfinite(sfc_wdir[i]) else None,
            pressure_mb=float(sfc_pres[i]) if np.isfinite(sfc_pres[i]) else None,
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations


def fetch_clamps_surface_observations(source: ClampsSurfaceSource, date_str: str) -> "list[Observation] | None":
    """Return observations for one platform/date, or None if unavailable
    (missing file, fetch failure, or a file with nothing usable -- all
    expected, everyday outcomes for a date this trailer wasn't deployed
    or ingesting, not errors)."""
    url = _source_url(source, date_str)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen_with_retry(request, timeout=_REQUEST_TIMEOUT) as response:
            data = response.read()
    except HTTPError as exc:
        if exc.code in (404, 410):
            return None
        log.warning("CLAMPS surface fetch failed for %s: %s", source.platform_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - network/SSL errors
        log.warning("CLAMPS surface fetch failed for %s: %s", source.platform_id, exc)
        return None

    try:
        observations = parse_clamps_surface_netcdf(data, source.platform_id)
    except Exception as exc:  # noqa: BLE001 - malformed/unexpected schema
        log.warning("CLAMPS surface parse failed for %s: %s", source.platform_id, exc)
        return None

    return observations or None
