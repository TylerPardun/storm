"""CLAMPS Doppler-lidar wind profiles (VAD/CSM winds) for archive playback.

Maps FRDD/CLAMPS THREDDS products onto STORM's existing VADProfile/VADSet
shape (core/vad.py) so the existing VADDialog hodograph display can be
reused for these instead of building a new viewer.

Two product families are known to carry the same core fields (height,
horizontal wind speed/direction, an RMS fit-quality metric) despite
different filename conventions and update cadence:

  - "vad"      -- classic VAD wind retrieval, sparse (roughly hourly)
  - "csmwinds" -- continuous-scan-mode-derived winds, dense (near-continuous)

Platform coverage is intentionally not scoped to any field campaign or
region: KNOWN_CLAMPS_WIND_SOURCES lists physical instrument locations on
THREDDS (which change rarely), and every date is queried against all of
them directly rather than against a campaign/date roster. A date with no
data for a source is expected and not an error -- see
_fetch_platform_wind_set.
"""

from __future__ import annotations

import io
import logging
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from core.vad import VADProfile, VADSet

log = logging.getLogger(__name__)

_FRDD_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS"
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_REQUEST_TIMEOUT = 60  # these files run tens to ~100MB; slower than mesonet CSV/nc
_RETRY_BACKOFF_S = (2.0, 5.0)


@dataclass(frozen=True)
class ClampsWindSource:
    """One physical instrument/datastream known to produce a wind profile."""
    platform_id: str    # display id, e.g. "CLAMPS1"
    platform_dir: str    # THREDDS path under FRDD/CLAMPS, e.g. "clamps/clamps1"
    datastream: str      # e.g. "clampsdlvadC1.c1" -- also the file prefix


# Discovered by browsing the THREDDS catalog directly (not inferred from a
# campaign), 2026-09-08 -- see planning/source-and-pilot-register.md. Add
# platforms here as NSSL adds instruments/units; this is instrument
# identity, not per-campaign/date scoping.
KNOWN_CLAMPS_WIND_SOURCES: tuple[ClampsWindSource, ...] = (
    ClampsWindSource("DLTRUCK1-DL1-VAD", "dltruck/dltruck1", "dltruckdlvadDL1.c1"),
    ClampsWindSource("DLTRUCK1-DL2-VAD", "dltruck/dltruck1", "dltruckdlvadDL2.c1"),
    ClampsWindSource("DLTRUCK1-DL1-CSMWINDS", "dltruck/dltruck1", "dltruckdlcsmwindsDL1.c1"),
    ClampsWindSource("DLTRUCK1-DL2-CSMWINDS", "dltruck/dltruck1", "dltruckdlcsmwindsDL2.c1"),
    ClampsWindSource("CLAMPS1-VAD", "clamps/clamps1", "clampsdlvadC1.c1"),
    ClampsWindSource("CLAMPS2-VAD", "clamps/clamps2", "clampsdlvadC2.c1"),
)


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _urlopen_with_retry(request: Request, *, timeout: int):
    """See archive/fetchers/vehicle_obs_archive_fetcher.py for why this
    exists: data.nssl.noaa.gov soft-throttles bursts of requests with
    connection timeouts rather than a clean 429/503. Never retry 404/410
    (a real "no file for this platform/date" answer)."""
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


def _source_url(source: ClampsWindSource, date_str: str) -> str:
    return (
        f"{_FRDD_ROOT}/{source.platform_dir}/processed/{source.datastream}/"
        f"{source.datastream}.{date_str}.000000.cdf"
    )


def parse_clamps_wind_netcdf(data: bytes, platform_id: str) -> list[VADProfile]:
    """Parse a CLAMPS dlvad/dlcsmwinds file into VADProfiles.

    height is km AGL -> m; wspd/rms are m/s -> knots (VADProfile's unit);
    rms is per-(time,height) in the source file, reduced to a single
    per-profile scalar (nanmean across height) to fit VADProfile.rms_error,
    which is a single quality indicator, not a profile.

    Invalid cells are not consistently NaN, and not consistently the same
    kind of invalid across files or even across fields in the same file --
    none of these variables declare a _FillValue/missing_value attribute,
    so nothing here is nominal, all of it was found by inspecting real
    files:
      - a CLAMPS1 VAD file used a repeated finite sentinel in wspd
        (1412.7993 "m/s") and rms (-999.0) that lined up exactly with a
        large-negative r_sq on the same cells;
      - a dltruck dlcsmwinds file instead had rms entirely equal to the
        raw netCDF float _FillValue default (9.96921e+36) with no
        relationship to r_sq at all, while a separate ~5% of cells had
        wspd in the 1e9-1e10 range despite a perfectly plausible r_sq.
    So: r_sq (legitimately in roughly [0, 1]) is used as the primary
    per-cell validity mask when present; isfinite() on the plain fields
    is a backstop for files without r_sq; and a physical plausibility
    bound is applied to wspd and rms independently of both, since neither
    r_sq nor isfinite() alone was sufficient on real data.
    """
    import numpy as np
    import xarray as xr

    from core.vad import MS_TO_KNOT

    # decode_times=False: base_time/time_offset are read and combined by
    # hand below, same reasoning as vehicle_obs_archive_fetcher's epochtime.
    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        base_time = float(ds["base_time"].values)
        time_offset = np.asarray(ds["time_offset"].values, dtype="float64")
        height_m = np.asarray(ds["height"].values, dtype="float64") * 1000.0
        wspd_kt = np.asarray(ds["wspd"].values, dtype="float64") * MS_TO_KNOT
        wdir = np.asarray(ds["wdir"].values, dtype="float64")
        rms_kt = np.asarray(ds["rms"].values, dtype="float64") * MS_TO_KNOT if "rms" in ds else None
        r_sq = np.asarray(ds["r_sq"].values, dtype="float64") if "r_sq" in ds else None

    # Generous physical bounds -- wide enough to never reject a real
    # measurement, tight enough to reject the fill sentinels seen in real
    # files (wspd's repeated ~2745 kt value, and rms's float32 default
    # fill of ~1.9e37 kt after unit conversion).
    _MAX_PLAUSIBLE_WSPD_KT = 250.0
    _MAX_PLAUSIBLE_RMS_KT = 500.0

    profiles: list[VADProfile] = []
    for i in range(time_offset.size):
        t_off = time_offset[i]
        if not np.isfinite(t_off):
            continue
        spd_row = wspd_kt[i]
        dir_row = wdir[i]
        valid = (
            np.isfinite(spd_row) & (np.abs(spd_row) < _MAX_PLAUSIBLE_WSPD_KT)
            & np.isfinite(dir_row) & np.isfinite(height_m)
        )
        if r_sq is not None:
            valid &= np.isfinite(r_sq[i]) & (r_sq[i] > -100.0)
        if not np.any(valid):
            continue

        rms_error = 0.0
        if rms_kt is not None:
            row_rms = rms_kt[i][valid]
            row_rms = row_rms[np.isfinite(row_rms) & (np.abs(row_rms) < _MAX_PLAUSIBLE_RMS_KT)]
            if row_rms.size:
                rms_error = float(np.mean(row_rms))

        profiles.append(VADProfile(
            timestamp=datetime.fromtimestamp(base_time + float(t_off), tz=timezone.utc),
            site=platform_id,
            heights_m=height_m[valid],
            wind_dir=dir_row[valid],
            wind_spd=spd_row[valid],
            rms_error=rms_error,
        ))

    return profiles


def _fetch_platform_wind_set(source: ClampsWindSource, date_str: str) -> "VADSet | None":
    """Return a VADSet for one platform/date, or None if unavailable
    (missing file, fetch failure, or a file with no usable profiles --
    all expected, everyday outcomes, not errors)."""
    url = _source_url(source, date_str)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen_with_retry(request, timeout=_REQUEST_TIMEOUT) as response:
            data = response.read()
    except HTTPError as exc:
        if exc.code in (404, 410):
            return None
        log.warning("CLAMPS wind fetch failed for %s: %s", source.platform_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - network/SSL errors
        log.warning("CLAMPS wind fetch failed for %s: %s", source.platform_id, exc)
        return None

    try:
        profiles = parse_clamps_wind_netcdf(data, source.platform_id)
    except Exception as exc:  # noqa: BLE001 - malformed/unexpected schema
        log.warning("CLAMPS wind parse failed for %s: %s", source.platform_id, exc)
        return None

    if not profiles:
        return None
    return VADSet(profiles=profiles)


class ArchiveClampsWindFetcher(QObject):
    """Loads CLAMPS wind-profile sets for every known platform on an
    archive date, in a background thread."""

    sets_ready = pyqtSignal(dict)   # dict[str, VADSet], platform_id -> set
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._lock = threading.Lock()

    def fetch(self, archive_date: datetime) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(
            target=self._bg_fetch, args=(archive_date,), daemon=True,
        ).start()
        return True

    def _bg_fetch(self, archive_date: datetime) -> None:
        date_str = archive_date.strftime("%Y%m%d")
        results: dict[str, VADSet] = {}
        errors: list[str] = []
        for source in KNOWN_CLAMPS_WIND_SOURCES:
            try:
                vad_set = _fetch_platform_wind_set(source, date_str)
            except Exception as exc:  # noqa: BLE001 - keep going on unexpected errors
                errors.append(f"{source.platform_id}: {exc}")
                continue
            if vad_set is not None:
                results[source.platform_id] = vad_set

        with self._lock:
            self._busy = False
        if errors and not results:
            self.error.emit("; ".join(errors))
        self.sets_ready.emit(results)
