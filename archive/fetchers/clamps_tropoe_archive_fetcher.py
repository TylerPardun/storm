"""CLAMPS TROPoe thermodynamic profiles (temperature/humidity vs. height)
for archive playback.

Maps FRDD/CLAMPS AERI/MWR TROPoe physical-retrieval output onto STORM's
existing Sounding/SoundingSet shape (core/sounding.py), the same "reuse
the existing display" approach as clamps_wind_archive_fetcher.py does for
VADProfile/VADDialog.

TROPoe is NOT run by STORM, and is not guaranteed to exist for every
CLAMPS deployment/date -- it is an optional, already-processed product
some (not all) deployments have. Several retrieval variants can exist
per platform (combined AERI+MWR, AERI-only, MWR-only; v1/v2 processing
versions); _VARIANT_PREFERENCE tries them in scientific preference order
and uses whichever variant actually has a file for the requested date.

Unlike the wind/mesonet products, TROPoe filenames embed a non-zero
start time (e.g. "<datastream>.20220525.001005.nc") that isn't
predictable from the date alone, and file extension varies (.nc vs
.cdf seen across datastreams) -- so discovery here lists the THREDDS
catalog page and matches by date prefix, rather than constructing a
deterministic URL.

TROPoe does not retrieve wind (AERI/MWR have no Doppler capability);
Sounding.u_wind/v_wind are filled with NaN -- explicitly absent, not
fabricated as calm wind.
"""

from __future__ import annotations

import io
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.sounding import Sounding, SoundingSet
from core import package_sources

log = logging.getLogger(__name__)

_CATALOG_ROOT = "https://data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS"
_FILESERVER_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 30
_RETRY_BACKOFF_S = (2.0, 5.0)

# Scientific preference order: combined AERI+MWR retrievals use more
# information than either alone; newer processing versions preferred
# over older. See archive/fetchers/clamps_wind_archive_fetcher.py for
# why this can't be a single deterministic path per platform: not every
# variant is populated for every platform/date.
_VARIANT_PREFERENCE: tuple[str, ...] = (
    "aeri_mwr.v2", "aeri_mwr.v1", "aeri.v2", "aeri.v1", "mwr.v2", "mwr.v1",
)


@dataclass(frozen=True)
class ClampsTropoePlatform:
    platform_id: str    # display id, e.g. "CLAMPS2"
    platform_dir: str   # THREDDS path under FRDD/CLAMPS, e.g. "clamps/clamps2"
    unit_suffix: str     # datastream suffix, e.g. "C2"


# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md. TROPoe needs AERI+MWR, which are
# on the clamps1/clamps2 trailers, not the dltruck scanning-lidar truck.
KNOWN_CLAMPS_TROPOE_PLATFORMS: tuple[ClampsTropoePlatform, ...] = (
    ClampsTropoePlatform("CLAMPS1", "clamps/clamps1", "C1"),
    ClampsTropoePlatform("CLAMPS2", "clamps/clamps2", "C2"),
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


def _list_catalog_filenames(platform_dir: str, datastream: str) -> list[str]:
    """Return every filename listed for one datastream's THREDDS catalog
    page. An empty list means no such datastream (404) -- normal when a
    platform doesn't run a given TROPoe variant, not an error."""
    url = f"{_CATALOG_ROOT}/{platform_dir}/processed/{datastream}/catalog.html"
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        html = package_sources.read_url("clamps profiles", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in (404, 410):
            return []
        log.warning("CLAMPS TROPoe catalog listing failed for %s/%s: %s", platform_dir, datastream, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - network/SSL errors
        log.warning("CLAMPS TROPoe catalog listing failed for %s/%s: %s", platform_dir, datastream, exc)
        return []

    prefix = re.escape(f"FRDD/CLAMPS/{platform_dir}/processed/{datastream}/{datastream}.")
    pattern = re.compile(rf'dataset={prefix}([0-9.]+\.(?:nc|cdf))"')
    return [f"{datastream}.{m}" for m in pattern.findall(html)]


def _find_file_for_date(platform: ClampsTropoePlatform, date_str: str) -> "tuple[str, str] | None":
    """Try each known retrieval variant in preference order for one
    platform/date. Returns (datastream, filename) for the first variant
    that has a file, or None if none do."""
    for variant in _VARIANT_PREFERENCE:
        datastream = f"clampstropoe10.{variant}.{platform.unit_suffix}"
        filenames = _list_catalog_filenames(platform.platform_dir, datastream)
        prefix = f"{datastream}.{date_str}."
        matches = sorted(f for f in filenames if f.startswith(prefix))
        if matches:
            return datastream, matches[0]
    return None


def parse_clamps_tropoe_netcdf(data: bytes, platform_id: str) -> list[Sounding]:
    """Parse a CLAMPS TROPoe retrieval file into Soundings.

    Only rows with qc_flag == 0 and converged_flag == 1 are kept -- on a
    real file, a rejected retrieval still carries plausible-looking but
    physically meaningless values (e.g. a flat, unconverged profile
    repeating the prior guess), not NaN, so the flags must be checked
    rather than relying on isfinite().
    """
    import numpy as np
    import xarray as xr

    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        height_m = np.asarray(ds["height"].values, dtype="float64") * 1000.0
        temperature = np.asarray(ds["temperature"].values, dtype="float64")
        dewpoint = np.asarray(ds["dewpt"].values, dtype="float64")
        pressure = np.asarray(ds["pressure"].values, dtype="float64")
        qc_flag = np.asarray(ds["qc_flag"].values, dtype="float64")
        converged_flag = np.asarray(ds["converged_flag"].values, dtype="float64")
        lat = float(ds["lat"].values)
        lon = float(ds["lon"].values)
        alt = float(ds["alt"].values)

    height_msl_m = height_m + alt
    no_wind = np.full(height_msl_m.shape, np.nan, dtype="float64")

    soundings: list[Sounding] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        if not np.isfinite(t_off):
            continue
        if qc_flag[i] != 0.0 or converged_flag[i] != 1.0:
            continue

        valid_time = datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc)
        soundings.append(Sounding(
            lat=lat,
            lon=lon,
            valid_time=valid_time,
            slot_offset=len(soundings),
            label=valid_time.strftime("%H%MZ %d %b"),
            pressure=pressure[i],
            temperature=temperature[i],
            dewpoint=dewpoint[i],
            u_wind=no_wind,
            v_wind=no_wind,
            height=height_msl_m,
        ))

    return soundings


def fetch_clamps_tropoe_soundings(archive_date: datetime) -> "SoundingSet | None":
    """Return a SoundingSet for the first known CLAMPS platform that has a
    usable TROPoe retrieval on this date, or None if none do. Platforms
    are physically separate sites; unlike wind profiles this returns at
    most one -- picking a second platform's data would silently mix two
    locations into what looks like one continuous set."""
    date_str = archive_date.strftime("%Y%m%d")
    for platform in KNOWN_CLAMPS_TROPOE_PLATFORMS:
        found = _find_file_for_date(platform, date_str)
        if found is None:
            continue
        datastream, filename = found
        url = f"{_FILESERVER_ROOT}/{platform.platform_dir}/processed/{datastream}/{filename}"
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            data = package_sources.read_url("clamps profiles", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - network/SSL errors, try next platform
            log.warning("CLAMPS TROPoe fetch failed for %s: %s", platform.platform_id, exc)
            continue

        try:
            soundings = parse_clamps_tropoe_netcdf(data, platform.platform_id)
        except Exception as exc:  # noqa: BLE001 - malformed/unexpected schema
            log.warning("CLAMPS TROPoe parse failed for %s: %s", platform.platform_id, exc)
            continue

        if not soundings:
            continue

        return SoundingSet(
            lat=soundings[0].lat,
            lon=soundings[0].lon,
            elevation=float(soundings[0].height[0]),
            fetch_time=archive_date,
            soundings=soundings,
            station_id=platform.platform_id,
            station_name=f"CLAMPS TROPoe ({datastream.split('.', 2)[1]})",
            source="clamps_tropoe",
        )

    return None
