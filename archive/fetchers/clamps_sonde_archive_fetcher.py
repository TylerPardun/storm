"""CLAMPS mobile radiosonde soundings, discovered directly from THREDDS,
for archive playback.

The live NSSL API's rolling sonde index (data/fetchers/clamps_sounding_
fetcher.py) serves .skewT.text files named
"upperair.NSSL_Lidar_sonde.<YYYYMMDDHHMM>.skewT.text" -- confirmed the
exact same files are mirrored onto THREDDS under dltruck1's ingested
sonde datastream, with permanent, verifiable per-date coverage
(2022-04-13 through at least 2026-07-02 confirmed present) rather than
the live API index's undocumented and unverified historical depth.
Reuses _parse_skewt/_FILENAME_RE from the live module directly instead
of reimplementing the parser -- it's the exact same file format.

Only dltruck1 (DL1 unit) has current coverage. clamps1 has its own
clampssonde{C1.b1}/clampssonderawC1.a1, but both only span 2015-06 to
2015-07 -- too old to matter for any campaign covered here, so not
included. No clamps2 sonde stream was found at all. dltruck1's .a1 level
turned out to be SHARPpy-format text (a different, not-yet-parsed
format), not another copy of the same skewT file -- so only .b1 is used.

Launch lat/lon: unlike the live API path, no raw/location-header
companion file was found alongside these THREDDS files (only the skewT
text itself), so location defaults to _parse_skewt's own built-in (0, 0)
"unknown" -- the same behavior the live module already has whenever its
own raw_url happens to be absent. Not fabricated from another source.
"""

from __future__ import annotations

import logging
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.sounding import SoundingSet
from data.fetchers.clamps_sounding_fetcher import _FILENAME_RE, _format_label, _parse_skewt

log = logging.getLogger(__name__)

_CATALOG_ROOT = "https://data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS"
_FILESERVER_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 30
_RETRY_BACKOFF_S = (2.0, 5.0)

# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md. Just one known source: see
# module docstring for why clamps1/clamps2 aren't included.
_SONDE_PLATFORM_DIR = "dltruck/dltruck1"
_SONDE_DATASTREAM = "dltruckdlsonderawDL1.b1"


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
    page. An empty list means no such datastream or a fetch failure --
    normal, not an error."""
    url = f"{_CATALOG_ROOT}/{platform_dir}/ingested/{datastream}/catalog.html"
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen_with_retry(request, timeout=_REQUEST_TIMEOUT) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 404/network/SSL, all "nothing here"
        log.debug("CLAMPS sonde catalog listing failed for %s/%s: %s", platform_dir, datastream, exc)
        return []

    prefix = re.escape(f"FRDD/CLAMPS/{platform_dir}/ingested/{datastream}/")
    pattern = re.compile(rf'dataset={prefix}([^"]+\.skewT\.text)"')
    return pattern.findall(html)


def fetch_clamps_sonde_soundings(archive_date: datetime) -> "SoundingSet | None":
    """Return every sonde launch found on THREDDS for this UTC date, or
    None if there are none (most days -- launches are sparse, event-
    driven, typically a handful per deployed day at most)."""
    date_str = archive_date.strftime("%Y%m%d")
    filenames = _list_catalog_filenames(_SONDE_PLATFORM_DIR, _SONDE_DATASTREAM)
    matches = sorted(f for f in filenames if f.startswith(f"upperair.NSSL_Lidar_sonde.{date_str}"))
    if not matches:
        return None

    soundings = []
    for idx, filename in enumerate(matches):
        m = _FILENAME_RE.search(filename)
        if not m:
            continue
        try:
            file_time = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        url = f"{_FILESERVER_ROOT}/{_SONDE_PLATFORM_DIR}/ingested/{_SONDE_DATASTREAM}/{filename}"
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with _urlopen_with_retry(request, timeout=_REQUEST_TIMEOUT) as response:
                text = response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - network/SSL errors, skip this launch
            log.warning("CLAMPS sonde fetch failed for %s: %s", filename, exc)
            continue

        snd = _parse_skewt(text, file_time, idx)
        if snd is not None:
            snd.label = _format_label(file_time)
            soundings.append(snd)

    if not soundings:
        return None

    surface_elev = float(soundings[0].height[0]) if soundings[0].height.size > 0 else 0.0
    return SoundingSet(
        lat=soundings[0].lat,
        lon=soundings[0].lon,
        elevation=surface_elev,
        fetch_time=archive_date,
        soundings=soundings,
        station_id="CLAMPS",
        station_name="NSSL CLAMPS DL Truck (THREDDS)",
        source="nssl",
    )
