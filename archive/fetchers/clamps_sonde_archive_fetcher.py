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
text itself), so _parse_skewt always returns its own built-in (0, 0)
"unknown" here -- not fabricated from the skewT file itself. Backfilled
below (2026-09-08, Tyler) from the DL Truck's own FOFS mesonet GPS
track instead: it's the same physical vehicle the sonde launches from,
and that track is real for any date FOFS has it. Falls back to (0, 0)
when no valid GPS fix lies within 60 seconds of that launch. Each launch
retains its own position and the GPS timestamp used for the backfill.
"""

from __future__ import annotations

import logging
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archive.fetchers.vehicle_obs_archive_fetcher import load_dltruck_track
from core.sounding import SoundingSet
from archive.positions import PositionTrack
from data.fetchers.clamps_sounding_fetcher import _FILENAME_RE, _format_label, _parse_skewt
from core import package_sources

from archive import thredds_paths as paths
log = logging.getLogger(__name__)

_CATALOG_ROOT = f"{paths.catalog_root()}/{paths.CLAMPS}"
_FILESERVER_ROOT = paths.file_url(paths.CLAMPS)
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 30
_RETRY_BACKOFF_S = (2.0, 5.0)

# Discovered by browsing the THREDDS catalog directly, 2026-09-08 -- see
# planning/source-and-pilot-register.md. Just one known source: see
# module docstring for why clamps1/clamps2 aren't included.
_SONDE_PLATFORM_DIR = paths.SONDE_PLATFORM
_SONDE_DATASTREAM = paths.SONDE_STREAM


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
        except URLError:
            if attempt == attempts - 1:
                raise
        _time.sleep(_RETRY_BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _list_catalog_filenames(platform_dir: str, datastream: str) -> list[str]:
    """Return every filename listed for one datastream's THREDDS catalog
    page. An empty list means no such datastream or a fetch failure --
    normal, not an error."""
    url = paths.catalog_url(paths.clamps_ingested(platform_dir, datastream))
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        html = package_sources.read_url("clamps profiles", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 404/network/SSL, all "nothing here"
        log.debug("CLAMPS sonde catalog listing failed for %s/%s: %s", platform_dir, datastream, exc)
        return []

    prefix = re.escape(f"{paths.clamps_ingested(platform_dir, datastream)}/")
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

        url = paths.file_url(f"{paths.clamps_ingested(_SONDE_PLATFORM_DIR, _SONDE_DATASTREAM)}/{filename}")
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            text = package_sources.read_url("clamps profiles", request, _urlopen_with_retry, timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - network/SSL errors, skip this launch
            log.warning("CLAMPS sonde fetch failed for %s: %s", filename, exc)
            continue

        snd = _parse_skewt(text, file_time, idx)
        if snd is not None:
            snd.label = _format_label(file_time)
            soundings.append(snd)

    if not soundings:
        return None

    track = PositionTrack(load_dltruck_track(archive_date))
    for sounding in soundings:
        sounding.location_source = "unknown"
        fix = track.nearest(sounding.valid_time)
        if fix is not None:
            sounding.lat, sounding.lon = fix.lat, fix.lon
            sounding.location_source = "FOFS dltruck GPS matched to launch time (within 60 s)"
            sounding.location_time = fix.timestamp
    latest = soundings[-1]
    surface_elev = float(latest.height[0]) if latest.height.size else 0.0
    lat, lon = latest.lat, latest.lon
    return SoundingSet(
        lat=lat,
        lon=lon,
        elevation=surface_elev,
        fetch_time=archive_date,
        soundings=soundings,
        station_id="CLAMPS",
        station_name="NSSL lidar truck (DLTRUCK1) sonde, THREDDS",
        source="nssl",
    )
