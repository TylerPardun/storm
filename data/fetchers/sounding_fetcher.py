
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

import numpy as np

from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet, PRESSURE_LEVELS, SOUNDING_SLOTS

from data import endpoints
log = logging.getLogger(__name__)

_BASE_URL          = endpoints.OPEN_METEO_FORECAST
_ARCHIVE_URL       = endpoints.OPEN_METEO_HISTORICAL
_MODEL             = "ncep_hrrr_conus"
_REQUEST_TIMEOUT   = 20   # seconds

# variables fetched at every pressure level
_LEVEL_VARS = (
    "temperature",
    "dew_point",
    "wind_u_component",
    "wind_v_component",
    "geopotential_height",
)


class SoundingFetcher(QObject):
    """Fire-and-forget fetcher for HRRR point soundings.

    Signals:
        sounding_ready(SoundingSet)  — emitted on the main thread when data arrives
        fetch_error(str)             — emitted on recoverable errors
    """

    sounding_ready = pyqtSignal(object)  # SoundingSet
    fetch_error    = pyqtSignal(str)

    def fetch(self, lat: float, lon: float):
        """Start a background fetch for (lat, lon).  Returns immediately."""
        threading.Thread(
            target=self._bg_fetch,
            args=(lat, lon),
            daemon=True,
        ).start()
        log.debug("sounding fetch started (%.4f, %.4f)", lat, lon)



    def _bg_fetch(self, lat: float, lon: float):
        try:
            result = _fetch_sounding_set(lat, lon)
            self.sounding_ready.emit(result)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"Sounding fetch failed: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"Sounding error: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)




def _build_hourly_params() -> str:
    """Build the comma-joined list of pressure-level variable names."""
    parts = []
    for level in PRESSURE_LEVELS:
        for var in _LEVEL_VARS:
            parts.append(f"{var}_{level}hPa")
    return ",".join(parts)


def _fetch_sounding_set(lat: float, lon: float) -> SoundingSet:
    """Fetch F0–F3 from the forecast API and T-2/T-1 from the historical API."""
    hourly_params = _build_hourly_params()

    base = urlencode({
        "latitude":        lat,
        "longitude":       lon,
        "models":          _MODEL,
        "forecast_hours":  4,
        "wind_speed_unit": "ms",
        "timezone":        "UTC",
    })
    url = f"{_BASE_URL}?{base}&hourly={hourly_params}"
    log.debug("sounding URL: %s", url)
    with urlopen(url, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    fwd_data = json.loads(raw)
    log.debug("sounding forecast response: %.1f KB", len(raw) / 1024)

    result = _parse_response(lat, lon, fwd_data)

    return result


def _parse_response(lat: float, lon: float, data: dict) -> SoundingSet:
    """Parse the open-meteo JSON into a SoundingSet."""
    elevation  = float(data.get("elevation", 0.0))
    fetch_time = datetime.now(timezone.utc)
    hourly     = data["hourly"]

    # build an index from UTC timestamp → position in the time array
    time_index: dict[datetime, int] = {}
    for i, t_str in enumerate(hourly["time"]):
        # open-meteo returns ISO 8601 without timezone — all times are UTC
        t = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
        time_index[t] = i

    # f0 = first timestamp in the response (current analysis hour).
    f0_time = min(time_index.keys())

    soundings = []
    for offset, short_label, tab_label in SOUNDING_SLOTS:
        target = f0_time + timedelta(hours=offset)
        t_idx  = time_index.get(target)
        if t_idx is None:
            log.debug("sounding slot %s (%s) not in response", short_label, target)
            continue

        sounding = _extract_sounding(
            lat, lon, hourly, t_idx, offset, tab_label, elevation,
            valid_time=target,
        )
        if sounding is not None:
            soundings.append(sounding)

    if not soundings:
        raise ValueError("No valid sounding time slots found in API response")

    return SoundingSet(
        lat        = lat,
        lon        = lon,
        elevation  = elevation,
        fetch_time = fetch_time,
        soundings  = soundings,
    )


def _extract_sounding(
    lat: float,
    lon: float,
    hourly: dict,
    t_idx: int,
    slot_offset: int,
    label: str,
    site_elevation: float,
    valid_time: datetime = None,
) -> "Sounding | None":
    """Extract one vertical profile from the hourly data at time index t_idx.

    Skips any pressure level where any variable is missing (None from the API)
    or where the geopotential height is below the site elevation (below-ground).
    """
    pressures = []
    temps     = []
    dewpts    = []
    u_winds   = []
    v_winds   = []
    heights   = []

    for level in PRESSURE_LEVELS:
        t_arr  = hourly.get(f"temperature_{level}hPa")
        dp_arr = hourly.get(f"dew_point_{level}hPa")
        u_arr  = hourly.get(f"wind_u_component_{level}hPa")
        v_arr  = hourly.get(f"wind_v_component_{level}hPa")
        h_arr  = hourly.get(f"geopotential_height_{level}hPa")

        # skip if any variable array is absent or has a missing value at this time
        if any(arr is None for arr in (t_arr, dp_arr, u_arr, v_arr, h_arr)):
            continue
        t_val  = t_arr[t_idx]
        dp_val = dp_arr[t_idx]
        u_val  = u_arr[t_idx]
        v_val  = v_arr[t_idx]
        h_val  = h_arr[t_idx]

        if any(v is None for v in (t_val, dp_val, u_val, v_val, h_val)):
            continue

        # drop below-ground levels (geopotential height below site elevation)
        if h_val < site_elevation - 10:  # 10 m tolerance
            continue

        pressures.append(float(level))
        temps.append(float(t_val))
        dewpts.append(float(dp_val))
        u_winds.append(float(u_val))
        v_winds.append(float(v_val))
        heights.append(float(h_val))

    if len(pressures) < 5:
        log.warning("sounding slot %s has only %d valid levels — skipping", label, len(pressures))
        return None

    return Sounding(
        lat         = lat,
        lon         = lon,
        valid_time  = valid_time,
        slot_offset = slot_offset,
        label       = label,
        pressure    = np.array(pressures, dtype=np.float64),
        temperature = np.array(temps,     dtype=np.float64),
        dewpoint    = np.array(dewpts,    dtype=np.float64),
        u_wind      = np.array(u_winds,   dtype=np.float64),
        v_wind      = np.array(v_winds,   dtype=np.float64),
        height      = np.array(heights,   dtype=np.float64),
    )
