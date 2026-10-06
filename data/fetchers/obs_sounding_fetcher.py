
import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet

from data import endpoints
log = logging.getLogger(__name__)

_IEM_BASE_URL    = endpoints.IEM_RAOB
_SPC_BASE_URL    = endpoints.SPC_SOUNDINGS
_IEM_TIMEOUT     = 5    # seconds — fast path; fall back to SPC on timeout/empty
_SPC_TIMEOUT     = 8    # seconds
_KNOTS_TO_MS     = 0.51444


class ObsSoundingFetcher(QObject):
    """Fire-and-forget fetcher for observed (radiosonde) soundings from IEM.

    Signals:
        sounding_ready(SoundingSet)  — emitted on the main thread when data arrives
        fetch_error(str)             — emitted on recoverable errors
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def fetch(
        self,
        station_id:   str,
        station_name: str,
        lat:          float,
        lon:          float,
        elevation:    float,
    ):
        """Start a background fetch for the given station.  Returns immediately."""
        threading.Thread(
            target=self._bg_fetch,
            args=(station_id, station_name, lat, lon, elevation),
            daemon=True,
        ).start()
        log.debug("obs sounding fetch started (%s)", station_id)


    def _bg_fetch(self, station_id, station_name, lat, lon, elevation):
        try:
            result = _fetch_sounding_set(station_id, station_name, lat, lon, elevation)
            self.sounding_ready.emit(result)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"OBS sounding fetch failed ({station_id}): {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"OBS sounding error ({station_id}): {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)



def _timestamps_for_local_day(lon: float) -> list[str]:
    """Return hourly UTC timestamps spanning the station's current local calendar day.

    The station's UTC offset is estimated as round(lon / 15) hours.  The local
    calendar day begins at 00:00 local (= 00:00 UTC − offset) and ends at
    23:00 local, but is capped at now_utc so future slots are never queried.
    The existing valid_time <= now_utc filter in _fetch_sounding_set() provides
    a second guard.

    Examples (CDT = UTC-6 est., lon ≈ -97):
      At 14Z Apr 13:  local day is Apr 13 → queries 12Z Apr 13 … 14Z Apr 13
      At 02Z Apr 14:  local day is still Apr 13 → queries 12Z Apr 13 … 02Z Apr 14
    """
    now_utc     = datetime.now(timezone.utc)
    utc_offset  = round(lon / 15)                    # hours, e.g. -97° → -6 (CST), -90° → -6
    local_tz    = timezone(timedelta(hours=utc_offset))

    # current local date at the station
    local_now   = now_utc.astimezone(local_tz)
    local_date  = local_now.date()

    # utc time corresponding to 00:00 local on that local date
    local_midnight_utc = datetime(
        local_date.year, local_date.month, local_date.day, 0, 0, 0,
        tzinfo=local_tz,
    ).astimezone(timezone.utc)

    start_utc = local_midnight_utc.replace(hour=12, minute=0, second=0, microsecond=0)

    timestamps: list[str] = []
    for hour in range(24):
        ts = start_utc + timedelta(hours=hour)
        if ts > now_utc:
            break
        timestamps.append(ts.strftime("%Y-%m-%dT%H:%M:%SZ"))

    return timestamps


def _fetch_iem_profiles(station_id: str, ts: str) -> list:
    """Fetch raw IEM profiles for one station/timestamp. Returns profile dicts."""
    url = f"{_IEM_BASE_URL}?station={station_id}&ts={ts}"
    log.debug("IEM sounding URL: %s", url)
    with urlopen(url, timeout=_IEM_TIMEOUT) as resp:
        raw = resp.read()
    data = json.loads(raw)
    return data.get("profiles", [])


def _fetch_spc_profiles(station_id: str, ts: str) -> list:
    """Fetch an SPC observed sounding text file and return it in IEM profile-dict format.

    SPC directory: https://www.spc.noaa.gov/exper/soundings/YYMMDDHH_OBS/
    File:          {STATION_ID}.txt  (e.g. OUN.txt)
    Returns [] when the file doesn't exist (404) or the %RAW% block is empty.
    """
    dt       = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    dir_name = dt.strftime("%y%m%d%H") + "_OBS"
    url      = f"{_SPC_BASE_URL}/{dir_name}/{station_id}.txt"
    log.debug("SPC sounding URL: %s", url)
    try:
        with urlopen(url, timeout=_SPC_TIMEOUT) as resp:
            text = resp.read().decode("ascii", errors="replace")
    except HTTPError as e:
        if e.code == 404:
            return []
        raise
    levels = _parse_spc_raw_block(text)
    if not levels:
        return []
    log.debug("SPC sounding: %d levels for %s @ %s", len(levels), station_id, ts)
    return [{"valid": ts, "profile": levels}]


def _parse_spc_raw_block(text: str) -> list:
    """Parse the %RAW% block of an SPC sounding text file into level dicts.

    Each CSV line: pressure(mb), height(m), temp(°C), dewpoint(°C), wind_dir(°), wind_spd(kts)
    -9999 indicates a missing value and is returned as None so _parse_profile
    handles it the same way it handles missing IEM fields.
    """
    levels = []
    in_raw = False
    for line in text.splitlines():
        line = line.strip()
        if line == "%RAW%":
            in_raw = True
            continue
        if line == "%END%":
            break
        if not in_raw or not line:
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            vals = [float(p.strip()) for p in parts[:6]]
        except ValueError:
            continue
        pres, hght, tmpc, dwpc, drct, sknt = vals
        levels.append({
            "pres": None if pres <= -9998 else pres,
            "hght": None if hght <= -9998 else hght,
            "tmpc": None if tmpc <= -9998 else tmpc,
            "dwpc": None if dwpc <= -9998 else dwpc,
            "drct": None if drct <= -9998 else drct,
            "sknt": None if sknt <= -9998 else sknt,
        })
    return levels


def _fetch_profiles_for_timestamp(station_id: str, ts: str) -> list:
    """Fetch profiles for one timestamp, routing by hour to avoid unnecessary requests.

    Synoptic hours (00Z, 12Z): try IEM first (fast, reliable), fall back to SPC.
    All other hours: go straight to SPC — IEM rarely ingests special launches
    quickly enough to be useful, and skipping it avoids a 5 s wait on an empty
    response before the SPC call even starts.
    """
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if dt.hour in (0, 12):
        try:
            profiles = _fetch_iem_profiles(station_id, ts)
            if profiles:
                return profiles
        except Exception as e:
            log.debug("IEM failed for %s @ %s (%s) — trying SPC", station_id, ts, e)
        return _fetch_spc_profiles(station_id, ts)
    else:
        return _fetch_spc_profiles(station_id, ts)


def _fetch_sounding_set(
    station_id:   str,
    station_name: str,
    lat:          float,
    lon:          float,
    elevation:    float,
) -> SoundingSet:
    now_utc    = datetime.now(timezone.utc)
    fetch_time = now_utc

    # collect all profiles across all hourly timestamps in parallel
    raw_profiles = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_fetch_profiles_for_timestamp, station_id, ts): ts
            for ts in _timestamps_for_local_day(lon)
        }
        for future in as_completed(futures):
            ts = futures[future]
            try:
                raw_profiles.extend(future.result())
            except Exception as e:
                log.warning("failed to fetch %s for %s: %s", station_id, ts, e)

    if not raw_profiles:
        raise ValueError(f"No sounding data returned for {station_id}")

    # parse each profile into a Sounding, filtering to valid_time <= now
    soundings = []
    for idx, prof in enumerate(raw_profiles):
        valid_str = prof.get("valid", "")
        try:
            valid_time = datetime.fromisoformat(valid_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            log.warning("unparseable valid time %r — skipping", valid_str)
            continue

        # skip future soundings
        if valid_time > now_utc:
            log.debug("skipping future obs sounding at %s", valid_str)
            continue

        sounding = _parse_profile(
            lat, lon, elevation, prof.get("profile", []), valid_time, idx
        )
        if sounding is not None:
            soundings.append(sounding)

    if not soundings:
        raise ValueError(f"No valid sounding time slots found for {station_id}")

    # some timestamps can resolve to the same valid profile; keep unique valid times.
    deduped: dict[datetime, Sounding] = {}
    for sounding in soundings:
        deduped[sounding.valid_time] = sounding

    # sort chronologically; use index as slot_offset
    soundings = list(deduped.values())
    soundings.sort(key=lambda s: s.valid_time)
    for i, s in enumerate(soundings):
        s.slot_offset = i
        s.label       = _format_label(s.valid_time)

    return SoundingSet(
        lat          = lat,
        lon          = lon,
        elevation    = elevation,
        fetch_time   = fetch_time,
        soundings    = soundings,
        station_id   = station_id,
        station_name = station_name,
        source       = "obs",
    )


def _parse_profile(
    lat:        float,
    lon:        float,
    site_elev:  float,
    levels:     list,
    valid_time: datetime,
    idx:        int,
) -> "Sounding | None":
    """Parse one IEM profile's level list into a Sounding.

    Skips levels where pressure, height, temperature, or dewpoint is missing.
    Wind is set to zero when direction or speed is missing.
    """
    pressures = []
    temps     = []
    dewpts    = []
    u_winds   = []
    v_winds   = []
    heights   = []

    for lv in levels:
        pres = lv.get("pres")
        hght = lv.get("hght")
        tmpc = lv.get("tmpc")
        dwpc = lv.get("dwpc")
        drct = lv.get("drct")
        sknt = lv.get("sknt")

        # required fields
        if any(v is None for v in (pres, hght, tmpc, dwpc)):
            continue

        # drop below-ground levels
        if float(hght) < site_elev - 10:
            continue

        # wind: use NaN components when missing so hodograph gaps render
        if drct is not None and sknt is not None:
            speed_ms  = float(sknt) * _KNOTS_TO_MS
            drct_rad  = math.radians(float(drct))
            u = -speed_ms * math.sin(drct_rad)
            v = -speed_ms * math.cos(drct_rad)
        else:
            u = np.nan
            v = np.nan

        pressures.append(float(pres))
        temps.append(float(tmpc))
        dewpts.append(float(dwpc))
        u_winds.append(u)
        v_winds.append(v)
        heights.append(float(hght))

    if len(pressures) < 5:
        log.warning(
            "obs sounding at %s has only %d valid levels — skipping",
            valid_time.isoformat(), len(pressures),
        )
        return None

    return Sounding(
        lat         = lat,
        lon         = lon,
        valid_time  = valid_time,
        slot_offset = idx,          # will be reassigned after sorting
        label       = "",           # will be set after sorting
        pressure    = np.array(pressures, dtype=np.float64),
        temperature = np.array(temps,     dtype=np.float64),
        dewpoint    = np.array(dewpts,    dtype=np.float64),
        u_wind      = np.array(u_winds,   dtype=np.float64),
        v_wind      = np.array(v_winds,   dtype=np.float64),
        height      = np.array(heights,   dtype=np.float64),
    )


def _format_label(dt: datetime) -> str:
    """Return a scrubber label like '00Z Mar 25'."""
    return f"{dt.hour:02d}Z {dt.strftime('%b')} {dt.day}"
