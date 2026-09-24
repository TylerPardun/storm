"""One-second FOFS mobile-mesonet observations for admin archive playback."""

from __future__ import annotations

import bisect
import csv
import io
import logging
import math
import ssl
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from archive.fofs_known_corrections import KNOWN_CORRECTIONS
from archive.vehicle_aliases import thredds_vehicle_id
from archive.vehicle_speed import VehicleSpeed, calculate_vehicle_speed
from core.observation import Observation

log = logging.getLogger(__name__)

_FILE_ROOT = (
    "https://data.nssl.noaa.gov/thredds/fileServer/"
    "FOFS/Mobile-Mesonet/data"
)
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_FRESH_SECONDS = 60


def _ssl_context() -> ssl.SSLContext:
    """Return the compatibility context required by data.nssl.noaa.gov."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


# data.nssl.noaa.gov sits behind a reverse proxy/WAF (duplicate security
# headers, a session cookie, and a 410 for requests without a plausible
# User-Agent -- confirmed by hand; see planning/source-and-pilot-register.md
# "Source access findings"). Under a burst of requests it has been observed
# to soft-throttle with connection timeouts rather than a clean 429, then
# recover on its own within minutes once traffic quiets down. 404/410 are
# real answers (no data / blocked) and must never be retried -- only
# timeouts, connection errors, and 5xx/429 origin responses are.
_RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF_S = (1.5, 4.0)  # sleep before each retry attempt


def _urlopen_with_retry(request: Request, *, timeout: int):
    attempts = len(_RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=timeout, context=_ssl_context())
        except HTTPError as exc:
            if exc.code not in _RETRYABLE_HTTP_CODES or attempt == attempts - 1:
                raise
        except URLError as exc:
            if attempt == attempts - 1:
                raise
        time.sleep(_RETRY_BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _daily_url(vehicle_id: str, date_str: str) -> str:
    directory = thredds_vehicle_id(vehicle_id)
    return f"{_FILE_ROOT}/{directory}/raw/{date_str}.txt"


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Values dropped as not being measurements -- only ones no real reading
# could produce, since genuine extremes (e.g. >50 m/s winds near tornadoes)
# must survive. The loggers write -999 when a sensor drops out (see the CR6
# program's HMP155 handling) and 999 for a calm or dead anemometer;
# beyond those, a failed fast-response thermistor reads -273.1 or -110 C,
# and no surface wind has ever been measured near 120 m/s.
_MISSING_AT_OR_BELOW = -900.0
_MISSING_WIND_AT_OR_ABOVE = 999.0
_PHYSICAL_LIMITS = {
    "t_fast": (-60.0, 60.0),
    "dewpoint": (-80.0, 40.0),
    "sfc_wspd": (0.0, 120.0),
    "sfc_wdir": (0.0, 360.0),
    "pressure": (500.0, 1100.0),
}
# A dewpoint can't meaningfully exceed the air temperature; allow for the
# fast and slow sensors disagreeing slightly.
_DEWPOINT_OVER_TEMPERATURE_C = 1.0
# Some files carry relative humidity under the dewpoint header (probe1,
# Aug 2020-2021: "dewpoint" equal to rh_slow row after row). When most rows
# match like that, the whole column is humidity, including values that
# would pass as plausible dewpoints, so none of it is used.
_RH_AS_DEWPOINT_TOLERANCE = 1.5
_RH_AS_DEWPOINT_FRACTION = 0.8
# Consecutive-second fixes further apart than this aren't the same track.
_SAME_TRACK_KM = 1.0
_MAX_SPEED_KMS = 0.09  # 90 m/s -- faster than any vehicle between fixes


def _measurement(row: dict, column: str) -> float | None:
    number = _float_or_none(row.get(column))
    if number is None or not math.isfinite(number) or number <= _MISSING_AT_OR_BELOW:
        return None
    if column in ("sfc_wspd", "sfc_wdir") and number >= _MISSING_WIND_AT_OR_ABOVE:
        return None
    if column == "sfc_wdir" and -360.0 < number < 720.0:
        number %= 360.0  # a few degrees past north either way is still a direction
    low, high = _PHYSICAL_LIMITS[column]
    return number if low <= number <= high else None


def _dewpoint(row: dict) -> float | None:
    dewpoint = _measurement(row, "dewpoint")
    temperatures = [t for t in (_float_or_none(row.get("t_slow")), _measurement(row, "t_fast"))
                    if t is not None and math.isfinite(t) and t > _MISSING_AT_OR_BELOW]
    if dewpoint is not None and temperatures and dewpoint > max(temperatures) + _DEWPOINT_OVER_TEMPERATURE_C:
        return None
    return dewpoint


def _dewpoint_column_is_humidity(rows: list[dict]) -> bool:
    pairs = [(_float_or_none(r.get("dewpoint")), _float_or_none(r.get("rh_slow"))) for r in rows]
    pairs = [(d, h) for d, h in pairs if d is not None and h is not None and h > 0]
    matching = sum(1 for d, h in pairs if abs(d - h) <= _RH_AS_DEWPOINT_TOLERANCE)
    return bool(pairs) and matching >= _RH_AS_DEWPOINT_FRACTION * len(pairs)


def _valid_position(lat: float, lon: float) -> bool:
    """Real coordinates, and not the receiver's null-island placeholders
    (exactly 0,0 or a partial fix within a degree or two of it)."""
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return False
    return not (abs(lat) < 2 and abs(lon) < 2)


def _km(a: Observation, b: Observation) -> float:
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _one_fix_per_second(observations: list[Observation]) -> tuple[list[Observation], int]:
    """Collapse same-timestamp rows to one, following the vehicle's track.

    Some published files interleave two tracks at identical timestamps
    (VORTEX2 2010: probe1's raw/20100524.txt repeats 23 May's last half
    hour, still moving, under 24 May's date). Keeping whichever row came
    first makes the vehicle flicker hundreds of km every second; instead
    each second keeps the row nearest the previous kept fix, starting from
    the first second that isn't ambiguous. Returns the kept rows and how
    many seconds had rows that disagreed on position.
    """
    groups: list[list[Observation]] = []
    for obs in observations:  # already sorted by timestamp
        if groups and groups[-1][0].timestamp == obs.timestamp:
            groups[-1].append(obs)
        else:
            groups.append([obs])
    if all(len(group) == 1 for group in groups):
        return observations, 0

    def ambiguous(group: list[Observation]) -> bool:
        return any(_km(group[0], other) > _SAME_TRACK_KM for other in group[1:])

    conflicts = sum(1 for group in groups if ambiguous(group))
    anchor = next((i for i, group in enumerate(groups) if not ambiguous(group)), 0)
    kept: list[Observation | None] = [None] * len(groups)
    kept[anchor] = groups[anchor][0]
    for i in range(anchor + 1, len(groups)):
        kept[i] = min(groups[i], key=lambda obs, prev=kept[i - 1]: _km(prev, obs))
    for i in range(anchor - 1, -1, -1):
        kept[i] = min(groups[i], key=lambda obs, nxt=kept[i + 1]: _km(nxt, obs))
    return kept, conflicts


# How gps_date strings in FOFS raw/*.txt files relate to the real UTC day.
#
# The mobile-mesonet CR6 logger records the receiver's own $GPRMC date as
# DDMMYY (FOFS/Mobile-Mesonet/logger/NSSL_MobileMesonet_CR6_loggerCode.CR6),
# and the original logger files carry correct dates and times throughout --
# confirmed 2026-09-24 against every locally available probe1/probe2 logger
# file from 2017 and LIFT 2024 (Probe_N_MMDDYYYY_QC_all.dat and the like).
# The damage happens later, in the step that converts those logger files
# into the published THREDDS CSVs:
#   * gps_date is sometimes rewritten as a garbled string, a pure function
#     of the true day (_thredds_garbled_gps_date), while gps_time stays
#     exact to the second (cross-checked against dltruck's independent GPS
#     track: same place at the same second);
#   * an MMDDYYYY logger filename is sometimes read as DDMMYYYY, filing a
#     day's data under its day/month swap (_swapped_file_date), e.g.
#     2024-05-06's data published as raw/20240605.txt;
#   * rows from 00:00:00-00:09:59 UTC are dropped (not recoverable here).
# Older "QC_met" logger files (2019-2023) carry only a decimal hour, no
# date, so the conversion took the date from the logger file's name and
# wrote it as MMDDYY. Those dates are only as good as that name reading:
# right for 2019's YYMMDD names, wrong for 2023's MMDDYYYY names whenever
# they could be read as DDMMYYYY -- which produced swapped-name duplicates
# of days that were also published correctly (_is_misfiled_duplicate).
#
# The processed/*.nc files come out of the same conversion -- their
# epochtime is NaN for garbled days and confidently wrong for swapped ones
# -- so they can't be used to check any of this and aren't read at all.
#
# Rather than guess per row, a whole file is resolved at once: each
# hypothesis (which day the file really starts on x which encoding it
# uses) predicts every row's exact gps_date string, and the one that
# predicts the most rows wins. Once the THREDDS files are republished
# correctly, they simply match the plain DDMMYY hypothesis.

def _ddmmyy(day: date) -> str:
    return day.strftime("%d%m%y")


def _mmddyy(day: date) -> str:
    return day.strftime("%m%d%y")


def _thredds_garbled_gps_date(day: date) -> str:
    """The gps_date the THREDDS conversion wrote for `day` when it garbled it.

    Derived from every raw-logger/published-CSV pair available (LIFT 2024
    Apr-Jun and 2017, ~1M rows): e.g. 27 Apr 2024 -> "020724", 30 Apr 2024
    -> "020404", 1 May 2024 -> "100524". Days 2-12 come through unchanged.
    Not every file was garbled, so this is one hypothesis among several,
    never assumed.
    """
    mm, yy = f"{day.month:02d}", f"{day.year % 100:02d}"
    if day.day == 1:
        return f"10{mm}{yy}"
    if day.day <= 12:
        return f"{day.day:02d}{mm}{yy}"
    tens, units = divmod(day.day, 10)
    if units == 0:
        return f"0204{mm}"
    return f"0{tens}0{units}{yy}"


# Order breaks ties between hypotheses that explain the same rows.
_GPS_DATE_ENCODINGS = (
    ("DDMMYY", _ddmmyy),
    ("THREDDS-garbled", _thredds_garbled_gps_date),
    ("MMDDYY", _mmddyy),
)

# A backwards jump in gps_time bigger than this, with gps_date changing
# too (true of every encoding above), is a UTC midnight rollover. The date
# condition keeps no-fix rows at 000000 (probe1's raw/20110828.txt) from
# counting as one.
_ROLLOVER_BACKSTEP_S = 12 * 3600


def _swapped_file_date(day: date) -> date | None:
    """The day/month swap of `day`, if one exists. Self-inverse: a file
    named for D holds data from _swapped_file_date(D) when the swap bug
    hit it, and D's own data may be filed under _swapped_file_date(D)."""
    if day.day > 12 or day.day == day.month:
        return None
    return date(day.year, day.day, day.month)


@dataclass(frozen=True)
class _FileDating:
    start_day: date  # the real UTC day of the file's first row
    encoding: str
    matched: int
    total: int


def _resolve_row_days(
    stamps: list[tuple[str, int]],
    file_date: date,
) -> tuple[list[date | None], _FileDating | None]:
    """Assign the real UTC day to each (gps_date, seconds-of-day) row of one
    file, or None where a row's gps_date contradicts the file's resolved
    dating (e.g. a no-fix row still carrying the receiver's default date).
    A file whose best hypothesis explains no more than half its rows is
    rejected entirely rather than trusted on thin evidence."""
    offsets: list[int] = []
    rollovers = 0
    previous: tuple[str, int] | None = None
    for gps_date, seconds in stamps:
        if (
            previous is not None
            and gps_date != previous[0]
            and seconds < previous[1] - _ROLLOVER_BACKSTEP_S
        ):
            rollovers += 1
        offsets.append(rollovers)
        previous = (gps_date, seconds)

    start_days = [file_date]
    swapped = _swapped_file_date(file_date)
    if swapped is not None:
        start_days.append(swapped)

    best: tuple[int, date, str, Callable[[date], str]] | None = None
    for start_day in start_days:
        for name, encode in _GPS_DATE_ENCODINGS:
            expected = {k: encode(start_day + timedelta(days=k)) for k in set(offsets)}
            matched = sum(
                1 for (gps_date, _), k in zip(stamps, offsets) if gps_date == expected[k]
            )
            if best is None or matched > best[0]:
                best = (matched, start_day, name, encode)

    if best is None or best[0] * 2 <= len(stamps):
        return [None] * len(stamps), None
    matched, start_day, name, encode = best
    days: list[date | None] = []
    for (gps_date, _), k in zip(stamps, offsets):
        day = start_day + timedelta(days=k)
        days.append(day if gps_date == encode(day) else None)
    return days, _FileDating(start_day, name, matched, len(stamps))


def parse_vehicle_csv(
    text: str,
    vehicle_id: str,
    icon_type: str | None = None,
    *,
    file_date: date,
) -> list[Observation]:
    """Parse one FOFS raw/<file_date>.txt file into timestamp-sorted
    observations, dated per the module notes above. The result can span
    two UTC days (files follow the crew's local day); callers filter."""
    return _one_fix_per_second(_parse_vehicle_file(text, vehicle_id, icon_type, file_date)[0])[0]


def _parse_vehicle_file(
    text: str,
    vehicle_id: str,
    icon_type: str | None,
    file_date: date,
) -> tuple[list[Observation], _FileDating | None]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            gps_date = row["gps_date"].strip().zfill(6)
            gps_time = row["gps_time"].strip().zfill(6)
            hour, minute, second = int(gps_time[:2]), int(gps_time[2:4]), int(gps_time[4:])
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
        if len(gps_time) != 6 or hour > 23 or minute > 59 or second > 59:
            continue
        if not _valid_position(lat, lon):
            continue
        rows.append((gps_date, hour * 3600 + minute * 60 + second, lat, lon, row))

    days, dating = _resolve_row_days([(r[0], r[1]) for r in rows], file_date)
    if dating is not None and (dating.start_day != file_date or dating.encoding != "DDMMYY"):
        log.info(
            "FOFS %s raw/%s.txt: dated as %s starting %s (%d/%d rows) -- see the "
            "gps_date notes in vehicle_obs_archive_fetcher",
            vehicle_id, file_date.strftime("%Y%m%d"), dating.encoding,
            dating.start_day.isoformat(), dating.matched, dating.total,
        )

    humidity_as_dewpoint = _dewpoint_column_is_humidity([r[4] for r in rows])
    if humidity_as_dewpoint:
        log.info("FOFS %s raw/%s.txt: dewpoint column holds relative humidity; not used",
                 vehicle_id, file_date.strftime("%Y%m%d"))

    observations: list[Observation] = []
    for (_, seconds, lat, lon, row), day in zip(rows, days):
        if day is None:
            continue
        observations.append(Observation(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
            icon_type=icon_type,
            temperature_c=_measurement(row, "t_fast"),
            dewpoint_c=None if humidity_as_dewpoint else _dewpoint(row),
            wind_speed_ms=_measurement(row, "sfc_wspd"),
            wind_dir_deg=_measurement(row, "sfc_wdir"),
            pressure_mb=_measurement(row, "pressure"),
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations, dating


@dataclass(frozen=True)
class _Correction:
    first: datetime
    last: datetime
    lat: float
    lon: float
    shift_days: int | None
    end_lat: float | None = None
    end_lon: float | None = None


_CORRECTIONS: dict[tuple[str, date], list[_Correction]] = {}
for _vehicle, _file, _first, _last, _lat, _lon, _shift, *_end in KNOWN_CORRECTIONS:
    _day = datetime.strptime(_file, "%Y%m%d").replace(tzinfo=timezone.utc)
    _CORRECTIONS.setdefault((_vehicle, _day.date()), []).append(_Correction(
        _day + timedelta(hours=int(_first[:2]), minutes=int(_first[3:5]), seconds=int(_first[6:])),
        _day + timedelta(hours=int(_last[:2]), minutes=int(_last[3:5]), seconds=int(_last[6:])),
        _lat, _lon, _shift, *_end,
    ))


def _follow_track(observations: list[Observation], correction: _Correction) -> set[int]:
    """Indexes of the rows forming the corrected stretch.

    Files interleave several tracks at the same timestamps, sometimes
    parked metres apart at the same spot, so the stretch is chosen as a
    whole: one row per second, every step physically possible, starting
    at the row at the entry's first fix, ending nearest its last fix when
    the entry gives one, and otherwise travelling the least total distance.
    """
    by_second: dict[datetime, list[tuple[int, Observation]]] = {}
    for i, obs in enumerate(observations):
        if correction.first <= obs.timestamp <= correction.last:
            by_second.setdefault(obs.timestamp, []).append((i, obs))
    seconds = sorted(by_second)
    if not seconds or seconds[0] != correction.first:
        return set()

    start = Observation(None, correction.lat, correction.lon, correction.first)
    first_i, first_obs = min(by_second[seconds[0]], key=lambda item: _km(start, item[1]))
    if _km(start, first_obs) > _SAME_TRACK_KM:
        return set()
    # layer: list of (distance so far, row index, obs, back-pointer into previous layer)
    layers = [[(0.0, first_i, first_obs, None)]]
    for second in seconds[1:]:
        previous = layers[-1]
        layer = []
        for i, obs in by_second[second]:
            best = None
            for k, (cost, _, prev_obs, _) in enumerate(previous):
                gap = (obs.timestamp - prev_obs.timestamp).total_seconds()
                step = _km(prev_obs, obs)
                if step <= _SAME_TRACK_KM + _MAX_SPEED_KMS * gap and (best is None or cost + step < best[0]):
                    best = (cost + step, k)
            if best is not None:
                layer.append((best[0], i, obs, best[1]))
        if layer:
            layers.append(layer)

    end = (Observation(None, correction.end_lat, correction.end_lon, correction.last)
           if correction.end_lat is not None else None)
    k = min(range(len(layers[-1])),
            key=lambda n: (_km(end, layers[-1][n][2]) if end else 0.0, layers[-1][n][0]))
    chosen: set[int] = set()
    for layer in reversed(layers):
        _, i, _, back = layer[k]
        chosen.add(i)
        k = back if back is not None else 0
    return chosen


def _apply_known_corrections(
    thredds_vehicle: str,
    file_date: date,
    observations: list[Observation],
) -> list[Observation]:
    """Move (or drop) the stretches of one file listed in
    archive/fofs_known_corrections.py."""
    corrections = _CORRECTIONS.get((thredds_vehicle, file_date))
    if not corrections:
        return observations
    shifts: dict[int, int | None] = {}
    for correction in corrections:
        for i in _follow_track(observations, correction):
            shifts[i] = correction.shift_days
    corrected: list[Observation] = []
    for i, obs in enumerate(observations):
        if i not in shifts:
            corrected.append(obs)
        elif shifts[i] is not None:
            corrected.append(_with_timestamp(obs, obs.timestamp + timedelta(days=shifts[i])))
    log.info("FOFS %s raw/%s.txt: %d rows re-dated or dropped per known corrections",
             thredds_vehicle, f"{file_date:%Y%m%d}", len(shifts))
    return corrected


def _with_timestamp(obs: Observation, timestamp: datetime) -> Observation:
    return Observation(
        vehicle_id=obs.vehicle_id, lat=obs.lat, lon=obs.lon, timestamp=timestamp,
        icon_type=obs.icon_type, temperature_c=obs.temperature_c, dewpoint_c=obs.dewpoint_c,
        wind_speed_ms=obs.wind_speed_ms, wind_dir_deg=obs.wind_dir_deg, pressure_mb=obs.pressure_mb,
    )


# A stretch of at most this long that the track jumps to and straight back
# from, impossibly fast both ways, is a GPS glitch.
_EXCURSION_S = 60


def _impossible_jump(a: Observation, b: Observation) -> bool:
    gap = max((b.timestamp - a.timestamp).total_seconds(), 1.0)
    distance = _km(a, b)
    return distance >= _SAME_TRACK_KM and distance / gap > _MAX_SPEED_KMS


def _drop_excursions(observations: list[Observation]) -> tuple[list[Observation], int]:
    """Drop short stretches the fix jumped away to and straight back from."""
    kept: list[Observation] = []
    dropped = 0
    i = 0
    while i < len(observations):
        if kept and _impossible_jump(kept[-1], observations[i]):
            j = i
            while (j < len(observations)
                   and (observations[j].timestamp - kept[-1].timestamp).total_seconds() <= _EXCURSION_S
                   and _impossible_jump(kept[-1], observations[j])):
                j += 1
            if j < len(observations) and not _impossible_jump(kept[-1], observations[j]):
                dropped += j - i
                i = j
                continue
        kept.append(observations[i])
        i += 1
    return kept, dropped


# A position that doesn't move at all right beside an impossible jump, while
# the other side of the jump is driving, is the receiver repeating its last
# fix before it has a new one. Only short frozen runs qualify, so a vehicle
# genuinely parked for a while is never touched.
_FROZEN_KM = 0.05
_STALE_MAX_S = 30 * 60
_CONTIGUOUS_S = 60


def _frozen_run(observations: list[Observation], index: int, step: int) -> list[int]:
    """Indexes of the unbroken run of rows at the same spot as
    observations[index], walking backwards (step -1) or forwards (+1)."""
    run = [index]
    j = index + step
    while 0 <= j < len(observations):
        gap = abs((observations[j].timestamp - observations[run[-1]].timestamp).total_seconds())
        if gap > _CONTIGUOUS_S or _km(observations[index], observations[j]) > _FROZEN_KM:
            break
        run.append(j)
        j += step
    return run


def _moving_from(observations: list[Observation], index: int, step: int) -> bool:
    """Whether the vehicle is driving over the minute starting at index."""
    j = index
    while (0 <= j + step < len(observations)
           and abs((observations[j + step].timestamp - observations[index].timestamp).total_seconds()) <= 60):
        j += step
    return _km(observations[index], observations[j]) > 10 * _FROZEN_KM


def _drop_stale_fixes(observations: list[Observation]) -> tuple[list[Observation], int]:
    stale: set[int] = set()
    for i in range(len(observations) - 1):
        a, b = observations[i], observations[i + 1]
        if not _impossible_jump(a, b):
            continue
        before, after = _frozen_run(observations, i, -1), _frozen_run(observations, i + 1, 1)
        for run, other_side, step in ((before, i + 1, 1), (after, i, -1)):
            duration = abs((observations[run[-1]].timestamp - observations[run[0]].timestamp).total_seconds())
            if duration <= _STALE_MAX_S and _moving_from(observations, other_side, step):
                stale.update(run)
                break
    return [obs for i, obs in enumerate(observations) if i not in stale], len(stale)


def _candidate_file_dates(session_day: date, thredds_vehicle: str | None = None) -> list[date]:
    """Every raw/<date>.txt that can hold rows from session_day: the day's
    own file and its neighbours (local-day files run past 00 UTC, and 2010
    files hold stretches of the adjacent days), the day/month-swapped names
    any of them could have been published under, and any file a known
    correction moves rows out of onto these days."""
    candidates: list[date] = []
    window = [session_day + timedelta(days=k) for k in (-1, 0, 1)]
    for day in window:
        for candidate in (day, _swapped_file_date(day)):
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
    if thredds_vehicle is not None:
        for (vehicle, file_date), corrections in sorted(_CORRECTIONS.items()):
            if vehicle != thredds_vehicle or file_date in candidates:
                continue
            if any(file_date + timedelta(days=c.shift_days + k) in window
                   for c in corrections if c.shift_days is not None for k in (-1, 0, 1)):
                candidates.append(file_date)
    return candidates


class ArchiveVehicleObsFetcher(QObject):
    """Loads and replays optional one-second observations for archive vehicles."""

    observation_ready = pyqtSignal(object)
    vehicles_cleared = pyqtSignal()
    load_finished = pyqtSignal(object)  # set[str] of MQTT vehicle IDs
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date_str = session_date.strftime("%Y%m%d")
        self._session_day = session_date.date()
        # the session runs into the next UTC morning (archive/session.py)
        from archive.session import session_bounds
        self._window_start, self._window_cap = session_bounds(session_date)
        self._observations: dict[str, list[Observation]] = {}
        self._timestamps: dict[str, list[datetime]] = {}
        self._last_indices: dict[str, int] = {}
        self._last_time: datetime | None = None
        self._loaded = False
        self.roster_size = 0

    @property
    def available_vehicle_ids(self) -> set[str]:
        return set(self._observations)

    def load(self, vehicles: dict[str, str | None]) -> None:
        """Probe and load daily files for MQTT vehicle IDs in a background thread."""
        threading.Thread(
            target=self._load_all,
            args=(dict(vehicles),),
            daemon=True,
        ).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            return
        if self._last_time is not None and archive_time < self._last_time:
            self._last_indices.clear()
            self.vehicles_cleared.emit()
        self._last_time = archive_time

        for vehicle_id in self._observations:
            index = self._index_at(vehicle_id, archive_time)
            if index < 0 or self._last_indices.get(vehicle_id) == index:
                continue
            self._last_indices[vehicle_id] = index
            self.observation_ready.emit(self._observations[vehicle_id][index])

    def has_fresh_observation(self, vehicle_id: str, archive_time: datetime) -> bool:
        """Return whether one-second data should supersede MQTT at this time."""
        index = self._index_at(vehicle_id, archive_time)
        if index < 0:
            return False
        age = (archive_time - self._observations[vehicle_id][index].timestamp).total_seconds()
        return 0 <= age <= _FRESH_SECONDS

    def _with_published_vehicles(self, vehicles: dict[str, str | None]) -> dict[str, str | None]:
        """Add every vehicle THREDDS has files for this session to the given
        roster (MQTT IDs -> icon type). A roster taken from recorded MQTT
        history alone misses probes that were never connected to STORM;
        one from a fixed list misses new vehicle folders."""
        from archive.vehicle_aliases import mqtt_vehicle_id
        published = available_vehicles(datetime(self._session_day.year, self._session_day.month,
                                                self._session_day.day, tzinfo=timezone.utc))
        if published is None:
            return vehicles
        present = {thredds_vehicle_id(v) for v in vehicles}
        merged = dict(vehicles)
        for vehicle in sorted(published - present):
            merged[mqtt_vehicle_id(vehicle)] = None
        return merged

    def activity_times(self) -> list[datetime]:
        """Each vehicle's last time actually driving -- a mesonet rack left
        logging overnight while parked doesn't count."""
        from archive.session import last_moving_time
        times = [last_moving_time(observations) for observations in self._observations.values()]
        return [t for t in times if t is not None]

    def history(self, vehicle_id: str, through_time: datetime) -> list[Observation]:
        """Return one-second observations for a vehicle through the requested time."""
        index = self._index_at(vehicle_id, through_time)
        if index < 0:
            return []
        return self._observations[vehicle_id][:index + 1]

    def speed(self, vehicle_id: str, archive_time: datetime) -> VehicleSpeed:
        return calculate_vehicle_speed(
            self._observations.get(vehicle_id, []),
            archive_time,
            short_seconds=1,
            average_seconds=15,
        )

    def first_vehicle_positions(self) -> list[tuple[str, float, float]]:
        result = []
        for vehicle_id, observations in self._observations.items():
            if observations:
                first = observations[0]
                result.append((vehicle_id, first.lat, first.lon))
        return result

    def vehicle_positions_near(self, target_time: datetime) -> list[tuple[str, float, float]]:
        """Same reasoning as ArchiveMQTTReader.vehicle_positions_near: the
        position nearest target_time, not each vehicle's first-ever fix --
        a deployment can stage from a base far from the actual intercept,
        so the day's first position is a poor proxy for where it was."""
        result = []
        for vehicle_id, observations in self._observations.items():
            if not observations:
                continue
            nearest = min(observations, key=lambda o: abs((o.timestamp - target_time).total_seconds()))
            result.append((vehicle_id, nearest.lat, nearest.lon))
        return result

    def _index_at(self, vehicle_id: str, archive_time: datetime) -> int:
        timestamps = self._timestamps.get(vehicle_id)
        if not timestamps:
            return -1
        return bisect.bisect_right(timestamps, archive_time) - 1

    def _load_all(self, vehicles: dict[str, str | None]) -> None:
        vehicles = self._with_published_vehicles(vehicles)
        self.roster_size = len(vehicles)
        loaded: dict[str, list[Observation]] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._fetch_vehicle, vehicle_id, icon_type): vehicle_id
                for vehicle_id, icon_type in vehicles.items()
            }
            for future in as_completed(futures):
                vehicle_id = futures[future]
                try:
                    observations = future.result()
                    if observations:
                        loaded[vehicle_id] = observations
                except Exception as exc:
                    log.warning("One-second archive load failed for %s: %s", vehicle_id, exc)
                    errors.append(f"{vehicle_id}: {exc}")

        self._observations = loaded
        self._timestamps = {
            vehicle_id: [obs.timestamp for obs in observations]
            for vehicle_id, observations in loaded.items()
        }
        self._loaded = True
        if errors and not loaded:
            self.error.emit("; ".join(errors))
        self.load_finished.emit(set(loaded))

    def _fetch_vehicle(
        self,
        vehicle_id: str,
        icon_type: str | None,
    ) -> list[Observation]:
        """Collect this session day's rows from every file that can hold
        them (see _candidate_file_dates), after known corrections. Missing
        candidates cost one fast 404 each. Rows found in more than one file
        (THREDDS has some days under both their real and swapped names)
        are kept once, and where rows disagree about where the vehicle was
        at a second the continuous track wins."""
        files: dict[date, tuple[list[Observation], _FileDating | None]] = {}

        def fetch(file_date: date) -> tuple[list[Observation], _FileDating | None]:
            if file_date not in files:
                files[file_date] = self._fetch_vehicle_file(vehicle_id, icon_type, file_date)
            return files[file_date]

        thredds_vehicle = thredds_vehicle_id(vehicle_id)
        collected: list[Observation] = []
        sources: list[str] = []
        for day in (self._session_day, self._session_day + timedelta(days=1)):
            for file_date in _candidate_file_dates(day, thredds_vehicle):
                observations, dating = fetch(file_date)
                if dating is not None and _is_misfiled_duplicate(file_date, observations, dating, fetch):
                    log.info(
                        "FOFS %s raw/%s.txt: skipped -- a copy of raw/%s.txt filed under a "
                        "misread date (see the gps_date notes in vehicle_obs_archive_fetcher)",
                        vehicle_id, f"{file_date:%Y%m%d}", f"{_swapped_file_date(file_date):%Y%m%d}",
                    )
                    continue
                observations = _apply_known_corrections(thredds_vehicle, file_date, observations)
                in_day = [obs for obs in observations
                          if obs.timestamp.date() == day and obs.timestamp <= self._window_cap]
                if in_day:
                    collected += in_day
                    sources.append(f"{file_date:%Y%m%d}.txt ({len(in_day)})")

        collected.sort(key=lambda obs: obs.timestamp)
        kept, conflicts = _one_fix_per_second(collected)
        kept, glitches = _drop_excursions(kept)
        kept, stale = _drop_stale_fixes(kept)
        glitches += stale
        log.info(
            "One-second archive: loaded %d observations for %s on %s from %s "
            "(%d seconds with conflicting positions resolved, %d glitch fixes dropped)",
            len(kept), vehicle_id, self._date_str, ", ".join(sources) or "no files",
            conflicts, glitches,
        )
        return kept

    def _fetch_vehicle_file(
        self,
        vehicle_id: str,
        icon_type: str | None,
        file_date: date,
    ) -> tuple[list[Observation], _FileDating | None]:
        collected: list[Observation] = []
        best: _FileDating | None = None
        for url in _file_urls(vehicle_id, file_date):
            observations, dating = self._fetch_one_file(url, vehicle_id, icon_type, file_date)
            collected += observations
            if dating is not None and (best is None or dating.matched > best.matched):
                best = dating
        collected.sort(key=lambda obs: obs.timestamp)
        return collected, best

    def _fetch_one_file(
        self,
        url: str,
        vehicle_id: str,
        icon_type: str | None,
        file_date: date,
    ) -> tuple[list[Observation], _FileDating | None]:
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with _urlopen_with_retry(request, timeout=30) as response:
                text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code in (404, 410):
                return [], None
            raise
        if text.strip() and not _is_published_format(text):
            # e.g. a _legacy_data original in its logger's own format -- the
            # same rows as the published copy, which is read instead
            log.debug("FOFS %s: %s is not in the published format; skipped", vehicle_id, url)
            return [], None
        observations, dating = _parse_vehicle_file(text, vehicle_id, icon_type, file_date)
        if not observations and text.strip():
            # A real, non-empty file whose gps_date values fit neither its
            # own name nor any known encoding of it -- e.g. a receiver with
            # no fix still reporting a stale default date (probe1's
            # raw/20170511.txt is entirely Feb 2017 no-fix rows). Worth a
            # WARNING because "0 observations" alone looks like an empty
            # file, and telling them apart otherwise means inspecting the
            # file by hand.
            log.warning(
                "One-second archive: %s's raw CSV %s has data but no rows could be dated "
                "from it (gps_date matches neither the file's date nor any known encoding) "
                "-- likely no GPS fix, not a fetch failure: %s",
                vehicle_id, file_date.strftime("%Y%m%d"), url,
            )
        return observations, dating


def _is_published_format(text: str) -> bool:
    """Whether a file is in the standard published CSV layout STORM parses."""
    header = text.lstrip().split("\n", 1)[0].lower()
    return all(column in header for column in ("gps_date", "gps_time", "lat", "lon"))


def _file_urls(vehicle_id: str, file_date: date) -> list[str]:
    """Every published copy of a vehicle's file for one date, wherever it
    sits in the THREDDS tree (archive/fofs_index.py). _legacy_data copies
    are the pre-conversion originals of a published file and are only
    tried when no other copy exists. Without an index, the usual location."""
    from archive import fofs_index
    index = fofs_index.get_index()
    if index is None:
        return [_daily_url(vehicle_id, file_date.strftime("%Y%m%d"))]
    files = index.files_for(thredds_vehicle_id(vehicle_id), file_date)
    current = [f for f in files if "/_legacy" not in f.path]
    return [f.url for f in (current or files)]


def available_vehicles(session_date: datetime) -> set[str] | None:
    """THREDDS vehicle names with a published file that can feed this
    session (its UTC day and the next morning), or None without an index."""
    from archive import fofs_index
    index = fofs_index.get_index()
    if index is None:
        return None
    day = session_date.date()
    vehicles = set()
    for vehicle in index.vehicles():
        dates = set(_candidate_file_dates(day, vehicle)) | set(_candidate_file_dates(day + timedelta(days=1), vehicle))
        if any(index.files_for(vehicle, d) for d in dates):
            vehicles.add(vehicle)
    return vehicles


def _is_misfiled_duplicate(
    file_date: date,
    observations: list[Observation],
    dating: _FileDating,
    fetch: Callable[[date], tuple[list[Observation], _FileDating | None]],
) -> bool:
    """Whether a file's dates were invented from a misread file name.

    MMDDYY dates came from the logger file's name, not the GPS, so a file
    named D with MMDDYY dates can't show by itself whether D was read
    right. It can be checked against its swap S: when raw/S.txt exists,
    carries GPS-derived dates for day S, and contains the same fixes at
    the same times of day, D is S's data under a misread date (as with
    probe1's raw/20230106.txt vs raw/20230601.txt). Without such a twin the
    file is trusted -- probe1's TORUS 2019 files are MMDDYY and correct.
    Positions are compared loosely because the two copies were written at
    different precisions (33.416 vs 33.41601); the misfiled 2023 copies
    also have their met columns scrambled, so they must not be shown.
    """
    swapped = _swapped_file_date(file_date)
    if dating.encoding != "MMDDYY" or swapped is None or not observations:
        return False
    twin_observations, twin_dating = fetch(swapped)
    if twin_dating is None or twin_dating.encoding == "MMDDYY" or twin_dating.start_day != swapped:
        return False

    twin_fixes: dict = {}
    for obs in twin_observations:
        if obs.timestamp.date() == swapped:
            twin_fixes.setdefault(obs.timestamp.time(), (obs.lat, obs.lon))
    first_day = [obs for obs in observations if obs.timestamp.date() == dating.start_day]
    shared = 0
    for obs in first_day:
        twin = twin_fixes.get(obs.timestamp.time())
        if twin is not None and abs(twin[0] - obs.lat) <= 1e-3 and abs(twin[1] - obs.lon) <= 1e-3:
            shared += 1
    return shared * 2 > len(first_day)


def load_dltruck_track(archive_date: datetime) -> list[Observation]:
    """Load the truck's measured FOFS track once for timestamp-based backfills."""
    try:
        return ArchiveVehicleObsFetcher(archive_date)._fetch_vehicle("dltruck", None)
    except Exception as exc:
        log.warning("DL Truck track lookup failed for %s: %s", archive_date.date(), exc)
        return []
