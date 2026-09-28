"""PERiLS CopterSonde UAS vertical-profile ascents for archive playback.

Maps FRDD/CLAMPS-hosted CopterSonde (University of Oklahoma) ascent
files onto STORM's existing Sounding/SoundingSet shape
(core/sounding.py) -- the same "reuse an existing type" approach used
for CLAMPS winds, TROPoe, and mobile sondes.

Unlike every other source built this session, no general by-instrument
THREDDS path was found for this one -- confirmed by checking FRDD/UAS,
FRDD/CopterSonde, FRDD/CLAMPS/CopterSonde, FRDD/CLAMPS/campaigns/TORUS/
CopterSonde and RRDD/CopterSonde, all 404. The only home found is
campaign-scoped: FRDD/CLAMPS/campaigns/PERiLS/<year>/CopterSonde/v1/
IOP<N>/, organized by Intensive Observation Period rather than by date
directly. KNOWN_PERILS_IOPS is therefore a registry of known
(year, IOP) folders rather than a platform list; for a requested date,
every known IOP's file listing is checked and filtered by the date
embedded in each filename (there is no way to construct a file's URL
from the date alone, unlike the mesonet/wind-product sources).

One IOP folder can hold ascents from several different launch sites on
the same day (e.g. IOP1 2022-03-22 has "LakeVillage...", "PineyCreek...",
"Schlater..." flights); these are physically different locations, so
they are kept as separate SoundingSets keyed by the file's own
site+tail-number prefix, not merged into one.

Unit/label caveats found on a real file:
  - tdry is kelvin (every other source this session used Celsius);
    converted here.
  - pres is pascal (every other source used mb/hPa); converted here.
  - wind_u/wind_v are labeled "westward"/"northward" -- a non-standard
    sign convention worth being skeptical of rather than trusting
    outright. u/v are derived here from dir/wspd instead, using the same
    standard meteorological formula as core/vad.py.
  - alt's units attribute is bare "meter" with no AGL/MSL qualifier;
    treated as MSL to match Sounding.height's documented convention,
    since it reads as a GPS-style altitude rather than an explicit
    height-above-launch computation -- flagged as an assumption, not
    verified against ground truth.
  - The same undeclared-fill-value pattern seen elsewhere in CLAMPS data
    appears here too, at float64 precision (~9.97e36) in alt/pres/lat/
    lon for a flight's trailing samples -- filtered with a broad
    magnitude bound, not just isfinite().
"""

from __future__ import annotations

import io
import logging
import math
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.sounding import Sounding, SoundingSet
from core import package_sources

log = logging.getLogger(__name__)

_CATALOG_ROOT = "https://data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS/campaigns/PERiLS"
_FILESERVER_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS/campaigns/PERiLS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 30
_RETRY_BACKOFF_S = (2.0, 5.0)
_FILL_MAGNITUDE_MAX = 1.0e6  # comfortably below the ~9.97e36 sentinel, well above any real value

# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md "6". IOP3 is absent from 2022
# (not a gap in this list -- it wasn't flown/published).
KNOWN_PERILS_IOPS: tuple[tuple[str, str], ...] = (
    ("2022", "IOP1"), ("2022", "IOP2"), ("2022", "IOP4"),
    ("2023", "IOP1"), ("2023", "IOP2"), ("2023", "IOP3"), ("2023", "IOP4"), ("2023", "IOP5"),
)

_FILENAME_RE = re.compile(r"^(?P<site>.+)\.c1\.(?P<date>\d{8})\.(?P<time>\d{6})\.cdf$")


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


def _list_catalog_filenames(year: str, iop: str) -> list[str]:
    """Return every ascent filename listed for one IOP's THREDDS catalog
    page. An empty list means no data for that IOP or a fetch failure --
    normal, not an error."""
    url = f"{_CATALOG_ROOT}/{year}/CopterSonde/v1/{iop}/catalog.html"
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        html = package_sources.read_url("coptersonde", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 404/network/SSL, all "nothing here"
        log.debug("CopterSonde catalog listing failed for %s/%s: %s", year, iop, exc)
        return []

    prefix = re.escape(f"FRDD/CLAMPS/campaigns/PERiLS/{year}/CopterSonde/v1/{iop}/")
    pattern = re.compile(rf'dataset={prefix}([^"/]+\.cdf)"')
    return pattern.findall(html)


def _valid(value: float) -> bool:
    import numpy as np
    return bool(np.isfinite(value) and abs(value) < _FILL_MAGNITUDE_MAX)


def parse_coptersonde_netcdf(data: bytes, site_id: str) -> "Sounding | None":
    """Parse one CopterSonde ascent file into a single Sounding (one file
    is one flight, i.e. one profile)."""
    import numpy as np
    import xarray as xr

    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        tdry_k = np.asarray(ds["tdry"].values, dtype="float64")
        td_c = np.asarray(ds["Td"].values, dtype="float64")
        pres_pa = np.asarray(ds["pres"].values, dtype="float64")
        wspd = np.asarray(ds["wspd"].values, dtype="float64")
        wdir = np.asarray(ds["dir"].values, dtype="float64")
        alt_m = np.asarray(ds["alt"].values, dtype="float64")
        lat = np.asarray(ds["lat"].values, dtype="float64")
        lon = np.asarray(ds["lon"].values, dtype="float64")

    valid = (
        np.isfinite(time_offset)
        & np.array([_valid(v) for v in tdry_k])
        & np.array([_valid(v) for v in pres_pa])
        & np.array([_valid(v) for v in alt_m])
        & np.array([_valid(v) for v in lat])
        & np.array([_valid(v) for v in lon])
    )
    if not np.any(valid):
        return None

    pressure_hpa = pres_pa[valid] / 100.0
    temperature_c = tdry_k[valid] - 273.15
    dewpoint_c = td_c[valid]
    height_msl_m = alt_m[valid]  # see module docstring: assumed MSL, not verified
    dir_valid = wdir[valid]
    spd_valid = wspd[valid]
    u_wind = -spd_valid * np.sin(np.deg2rad(dir_valid))
    v_wind = -spd_valid * np.cos(np.deg2rad(dir_valid))

    valid_time = datetime.fromtimestamp(base_time + float(time_offset[valid][0]), tz=timezone.utc)

    return Sounding(
        lat=float(lat[valid][0]),
        lon=float(lon[valid][0]),
        valid_time=valid_time,
        slot_offset=0,
        label=f"{site_id} {valid_time.strftime('%H%MZ %d %b')}",
        pressure=pressure_hpa,
        temperature=temperature_c,
        dewpoint=dewpoint_c,
        u_wind=u_wind,
        v_wind=v_wind,
        height=height_msl_m,
    )


def fetch_coptersonde_soundings(archive_date: datetime) -> "dict[str, SoundingSet]":
    """Return every CopterSonde site's ascents found for this UTC date,
    keyed by site+tail-number (the filename prefix before ".c1."). Most
    dates return an empty dict -- PERiLS flew a handful of IOP days per
    year, not continuously."""
    date_str = archive_date.strftime("%Y%m%d")
    by_site: dict[str, list[Sounding]] = {}

    for year, iop in KNOWN_PERILS_IOPS:
        if str(year) != str(archive_date.year):
            continue
        filenames = _list_catalog_filenames(year, iop)
        for filename in filenames:
            m = _FILENAME_RE.match(filename)
            if not m or m.group("date") != date_str:
                continue
            site_id = m.group("site")

            url = f"{_FILESERVER_ROOT}/{year}/CopterSonde/v1/{iop}/{filename}"
            request = Request(url, headers={"User-Agent": _USER_AGENT})
            try:
                data = package_sources.read_url("coptersonde", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - network/SSL errors, skip this flight
                log.warning("CopterSonde fetch failed for %s: %s", filename, exc)
                continue

            try:
                snd = parse_coptersonde_netcdf(data, site_id)
            except Exception as exc:  # noqa: BLE001 - malformed/unexpected schema
                log.warning("CopterSonde parse failed for %s: %s", filename, exc)
                continue

            if snd is not None:
                by_site.setdefault(site_id, []).append(snd)

    results: dict[str, SoundingSet] = {}
    for site_id, soundings in by_site.items():
        for idx, snd in enumerate(sorted(soundings, key=lambda s: s.valid_time)):
            snd.slot_offset = idx
        surface_elev = float(soundings[0].height[0]) if soundings[0].height.size > 0 else 0.0
        results[site_id] = SoundingSet(
            lat=soundings[0].lat,
            lon=soundings[0].lon,
            elevation=surface_elev,
            fetch_time=archive_date,
            soundings=soundings,
            station_id=site_id,
            station_name=f"PERiLS CopterSonde ({site_id})",
            # Deliberately not "nssl" or "clamps_tropoe" -- a UAS in-situ
            # profile is neither a CLAMPS radiosonde launch nor a remote-
            # sensing retrieval, and SoundingSet.source is used as a
            # dialog comparison-overlay key elsewhere in this codebase,
            # so reusing either would be both inaccurate and a collision
            # risk. No display wiring for this source yet (data layer
            # only, per instruction), so no is_coptersonde property/dialog
            # handling has been added -- add both together when display
            # work starts, rather than half-wiring one without the other.
            source="coptersonde",
        )
    return results
