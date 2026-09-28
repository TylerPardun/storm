
import json
import logging
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

import config
from core.sounding import Sounding, SoundingSet

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 20
_LOOKBACK_HOURS  = 12
_RAW_HEADER_BYTES = 4096
_API_INDEX_PATH = "data/sonde/index.json"

_TEST_FILE_URL = None

_FILENAME_RE = re.compile(r"upperair\.NSSL_Lidar_sonde\.(\d{12})\.skewT", re.IGNORECASE)
_LOCATION_RE = re.compile(
    r"Release point (latitude|longitude)\s+([-+]?\d+(?:\.\d+)?)[^NSEW]*([NSEW])?",
    re.IGNORECASE,
)

_HEADERS = {"User-Agent": "Mozilla/5.0 STORM/1.0"}


@dataclass(frozen=True)
class _SondeEntry:
    file_time: datetime
    skewt_url: str = ""
    raw_url: str = ""


class ClampsSoundingFetcher(QObject):
    """Fire-and-forget fetcher for NSSL CLAMPS DL Truck soundings.

    Signals:
        sounding_ready(SoundingSet)  — emitted on main thread when data arrives
        fetch_error(str)             — emitted on recoverable errors
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._busy_lock = threading.Lock()

    def fetch(self):
        """Start background fetch of last 12 hours of CLAMPS soundings."""
        with self._busy_lock:
            if self._busy:
                log.debug("CLAMPS sounding fetch skipped; request already in progress")
                return False
            self._busy = True
        threading.Thread(target=self._bg_fetch, daemon=True).start()
        log.debug("CLAMPS sounding fetch started")
        return True


    def _bg_fetch(self):
        try:
            sset = _fetch_sounding_set()
            self.sounding_ready.emit(sset)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"CLAMPS fetch failed: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"CLAMPS fetch error: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)
        finally:
            with self._busy_lock:
                self._busy = False



def _fetch_sounding_set() -> SoundingSet:
    now_utc = datetime.now(timezone.utc)

    if _TEST_FILE_URL:
        m = _FILENAME_RE.search(_TEST_FILE_URL)
        file_time = (
            datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            if m else now_utc
        )
        req = Request(_TEST_FILE_URL, headers=_HEADERS)
        with urlopen(req, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        snd = _parse_skewt(text, file_time, 0)
        if snd is None:
            raise ValueError("Failed to parse test CLAMPS file")
        snd.slot_offset = 0
        snd.label       = _format_label(file_time)
        soundings       = [snd]
        surface_elev    = float(snd.height[0]) if snd.height.size > 0 else 0.0
        return SoundingSet(
            lat=0.0, lon=0.0, elevation=surface_elev, fetch_time=now_utc,
            soundings=soundings, station_id="CLAMPS",
            station_name="NSSL lidar truck (DLTRUCK1) sonde", source="nssl",
        )

    return _fetch_sounding_set_from_api(now_utc)


def _fetch_sounding_set_from_api(now_utc: datetime) -> SoundingSet:
    cutoff = now_utc - timedelta(hours=_LOOKBACK_HOURS)
    entries = [
        entry for entry in _api_sonde_entries()
        if cutoff <= entry.file_time <= now_utc
    ]
    if not entries:
        raise ValueError("No CLAMPS API soundings found in the last 12 hours")

    entries.sort(key=lambda entry: entry.file_time)
    soundings: list[Sounding] = []
    for idx, entry in enumerate(entries):
        if not entry.skewt_url:
            raise ValueError("CLAMPS API index does not include sounding file URLs")
        try:
            snd = _fetch_and_parse_url(entry.skewt_url, entry.file_time, idx, entry.raw_url)
            if snd is not None:
                soundings.append(snd)
        except Exception as e:
            log.warning("Failed to fetch CLAMPS API file %s: %s", entry.skewt_url, e)

    return _soundings_to_set(soundings, now_utc)


def _soundings_to_set(soundings: list[Sounding], now_utc: datetime) -> SoundingSet:
    if not soundings:
        raise ValueError("No valid CLAMPS soundings could be parsed")

    # reassign slot_offset and labels after filtering
    for i, snd in enumerate(soundings):
        snd.slot_offset = i
        snd.label       = _format_label(snd.valid_time)

    surface_elev = float(soundings[0].height[0]) if soundings[0].height.size > 0 else 0.0
    set_lat, set_lon = _representative_location(soundings)

    return SoundingSet(
        lat          = set_lat,
        lon          = set_lon,
        elevation    = surface_elev,
        fetch_time   = now_utc,
        soundings    = soundings,
        station_id   = "CLAMPS",
        station_name = "NSSL lidar truck (DLTRUCK1) sonde",
        source       = "nssl",
    )


def _api_sonde_entries() -> list[_SondeEntry]:
    req = Request(_api_url(_API_INDEX_PATH), headers=_api_headers())
    with urlopen(req, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))

    values = payload.get("soundings")
    if values is None:
        values = payload.get("launches")
    if values is None:
        values = payload.get("launch_times")
    if not isinstance(values, list):
        raise ValueError("CLAMPS API index is missing launch_times or soundings")

    entries: list[_SondeEntry] = []
    for value in values:
        entry = _api_sonde_entry(value)
        if entry is not None:
            entries.append(entry)
    return entries


def _api_sonde_entry(value: object) -> "_SondeEntry | None":
    if isinstance(value, str):
        file_time = _parse_launch_time(value)
        if file_time is None:
            return None
        return _SondeEntry(file_time, _api_sonde_url(file_time))

    if not isinstance(value, dict):
        return None

    time_value = (
        value.get("valid_time")
        or value.get("launch_time")
        or value.get("time")
        or value.get("id")
    )
    file_time = _parse_launch_time(time_value)
    if file_time is None:
        return None

    skewt_url = (
        value.get("skewt_url")
        or value.get("skewT_url")
        or value.get("url")
        or value.get("file_url")
        or ""
    )
    raw_url = value.get("raw_url") or value.get("raw_file_url") or ""
    return _SondeEntry(
        file_time=file_time,
        skewt_url=_api_url(str(skewt_url)) if skewt_url else _api_sonde_url(file_time),
        raw_url=_api_url(str(raw_url)) if raw_url else "",
    )


def _api_sonde_url(file_time: datetime) -> str:
    stamp = file_time.strftime("%Y%m%d%H%M")
    return _api_url(f"data/sonde/upperair.NSSL_Lidar_sonde.{stamp}.skewT.text")


def _parse_launch_time(value: object) -> "datetime | None":
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{12})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _api_url(path_or_url: str) -> str:
    value = str(path_or_url or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    return urljoin(config.NSSL_API_ROOT.rstrip("/") + "/", value.lstrip("/"))


def _api_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(_HEADERS)
    if config.NSSL_API_KEY:
        headers["X-API-Key"] = config.NSSL_API_KEY
    if extra:
        headers.update(extra)
    return headers


def _fetch_and_parse_url(
    url: str,
    file_time: datetime,
    idx: int,
    raw_url: str = "",
) -> "Sounding | None":
    req = Request(url, headers=_api_headers())
    with urlopen(req, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    snd = _parse_skewt(text, file_time, idx)
    if snd is not None and raw_url:
        location = _fetch_raw_launch_location_url(raw_url)
        if location is not None:
            snd.lat, snd.lon = location
    return snd


def _fetch_raw_launch_location_url(raw_url: str) -> "tuple[float, float] | None":
    headers = _api_headers({"Range": f"bytes=0-{_RAW_HEADER_BYTES - 1}"})
    req = Request(raw_url, headers=headers)
    with urlopen(req, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT) as resp:
        text = resp.read(_RAW_HEADER_BYTES).decode("utf-8", errors="replace")
    return _parse_raw_launch_location(text)


def _parse_raw_launch_location(text: str) -> "tuple[float, float] | None":
    lat = None
    lon = None
    for line in text.splitlines():
        if line.strip() == "%RAW%":
            break
        m = _LOCATION_RE.search(line)
        if not m:
            continue
        value = float(m.group(2))
        hemi = (m.group(3) or "").upper()
        if hemi in ("S", "W"):
            value = -abs(value)
        elif hemi in ("N", "E"):
            value = abs(value)

        if m.group(1).lower() == "latitude":
            lat = value
        else:
            lon = value

    if lat is None or lon is None:
        return None
    return lat, lon


def _representative_location(soundings: list[Sounding]) -> tuple[float, float]:
    for snd in reversed(soundings):
        if snd.lat != 0.0 or snd.lon != 0.0:
            return float(snd.lat), float(snd.lon)
    return 0.0, 0.0


def _parse_skewt(text: str, file_time: datetime, idx: int) -> "Sounding | None":
    """Parse a .skewT text file into a Sounding.

    Columns: pressure(hPa), height(m MSL), temp(°C), dewpoint(°C),
             wind_dir(deg), wind_speed(m/s)
    """
    pressures: list[float] = []
    heights:   list[float] = []
    temps:     list[float] = []
    dewpts:    list[float] = []
    u_winds:   list[float] = []
    v_winds:   list[float] = []

    in_raw = False
    for line in text.splitlines():
        line = line.strip()
        if line == "%RAW%":
            in_raw = True
            continue
        if line in ("%END%", "%TITLE%"):
            in_raw = False
            continue
        if not in_raw or not line:
            continue

        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            pres = float(parts[0])
            hght = float(parts[1])
            temp = float(parts[2])
            dwpc = float(parts[3])
            wdir = float(parts[4])
            wspd = float(parts[5]) / 1.944   # convert knots to m/s
        except ValueError:
            continue

        # direction + speed (m/s) → meteorological u/v components (m/s)
        wdir_rad = math.radians(wdir)
        u = -wspd * math.sin(wdir_rad)
        v = -wspd * math.cos(wdir_rad)

        pressures.append(pres)
        heights.append(hght)
        temps.append(temp)
        dewpts.append(dwpc)
        u_winds.append(u)
        v_winds.append(v)

    if len(pressures) < 5:
        log.warning("CLAMPS skewT only has %d valid levels — skipping", len(pressures))
        return None

    return Sounding(
        lat         = 0.0,
        lon         = 0.0,
        valid_time  = file_time,
        slot_offset = idx,
        label       = "",
        pressure    = np.array(pressures, dtype=np.float64),
        temperature = np.array(temps,     dtype=np.float64),
        dewpoint    = np.array(dewpts,    dtype=np.float64),
        u_wind      = np.array(u_winds,   dtype=np.float64),
        v_wind      = np.array(v_winds,   dtype=np.float64),
        height      = np.array(heights,   dtype=np.float64),
    )


def _format_label(dt: datetime) -> str:
    return f"{dt.strftime('%H%M')}Z {dt.strftime('%b')} {dt.day}"
