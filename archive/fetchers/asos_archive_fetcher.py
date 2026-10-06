"""Historical ASOS/METAR surface observations for archive playback.

Sourced from IEM's historical request API (data.nssl.noaa.gov-independent --
this host is entirely unrelated to the confirmed-dead api.nssl.noaa.gov that
the six other live-mode regional-mesonet sources depend on):

    https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py

Confirmed live 2026-09-09: a bare `station=<ID>` query with no `network=`
returns real historical data (tested against 2013-05-31 for OUN/OKC/TUL/FSI).
`station=` is repeatable -- one request can cover many stations. Documented
and empirically confirmed: a 1-second-per-IP throttle, so batch requests are
paced sequentially, never thread-pooled.

Fetch-once/bisect-replay shape, matching
archive/fetchers/vehicle_obs_archive_fetcher.py's ArchiveVehicleObsFetcher --
except the station list is only known once the user draws a map bbox (there
is no prior discovery step), so this fetcher supports redrawing: set_bbox()
clears whatever was loaded for the previous box and starts a fresh, bounded
load, discarding a still-in-flight previous load via a generation counter.
"""
from __future__ import annotations

import bisect
import csv
import io
import logging
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

import config
from core.observation import Observation
from data.fetchers.surface_fetcher import MAX_ASOS_STATIONS, load_asos_station_roster
from core import package_sources

from data import endpoints
log = logging.getLogger(__name__)

_ASOS_HISTORY_URL = endpoints.IEM_ASOS_HISTORY
_REQUEST_FIELDS = ("tmpf", "dwpf", "sknt", "drct", "alti", "mslp")
_BATCH_SIZE = 50  # smaller than live's 100/request (IEM_CURRENTS_BATCH) -- this
# endpoint returns a full day of rows per station, not one current snapshot.
_BATCH_PACE_S = 1.0  # IEM's confirmed 1-second-per-IP throttle
_REQUEST_TIMEOUT = 30
_RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF_S = (2.0, 5.0)


def _urlopen_with_retry(request: Request):
    attempts = len(_RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=_REQUEST_TIMEOUT, context=config.NSSL_SSL_CONTEXT)
        except HTTPError as exc:
            if exc.code not in _RETRYABLE_HTTP_CODES or attempt == attempts - 1:
                raise
        except URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(_RETRY_BACKOFF_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _stations_in_bbox(roster: dict[str, dict], west: float, south: float, east: float, north: float) -> list[str]:
    return [
        stid for stid, meta in roster.items()
        if south <= meta["lat"] <= north and west <= meta["lon"] <= east
    ]


def _batch_url(station_ids: list[str], sts: datetime, ets: datetime) -> str:
    parts = [f"data={f}" for f in _REQUEST_FIELDS]
    parts += [f"station={s}" for s in station_ids]
    parts += [
        f"sts={sts.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"ets={ets.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "tz=UTC",
        "format=onlycomma",
    ]
    return f"{_ASOS_HISTORY_URL}?{'&'.join(parts)}"


def parse_asos_history_csv(raw_text: str, roster: dict[str, dict]) -> dict[str, list[Observation]]:
    """Parse one onlycomma CSV response into per-station, time-sorted
    Observation lists. Unit conversions match SurfaceFetcher._fetch_iem_batch
    exactly (°F->°C, knots->m/s, altimeter in-Hg->mb) so live and archive
    ASOS data read the same way once normalized. A row for a station not in
    the roster (no known lat/lon) is dropped, same as live mode."""
    result: dict[str, list[Observation]] = {}
    reader = csv.DictReader(io.StringIO(raw_text))
    for row in reader:
        stid = (row.get("station") or "").strip().upper()
        meta = roster.get(stid)
        if not stid or meta is None:
            continue

        ts = _parse_valid(row.get("valid"))
        if ts is None:
            continue

        tmpf = _float_or_none(row.get("tmpf"))
        dwpf = _float_or_none(row.get("dwpf"))
        temp_c = (tmpf - 32.0) * 5.0 / 9.0 if tmpf is not None else None
        dew_c = (dwpf - 32.0) * 5.0 / 9.0 if dwpf is not None else None

        sknt = _float_or_none(row.get("sknt"))
        wind_ms = sknt * 0.514444 if sknt is not None else None

        wdir = _float_or_none(row.get("drct"))

        mslp = _float_or_none(row.get("mslp"))
        pres = mslp if (mslp is not None and 870.0 <= mslp <= 1090.0) else None
        if pres is None:
            alti = _float_or_none(row.get("alti"))
            if alti is not None and 27.0 <= alti <= 32.0:
                pres = alti * 33.8639

        obs = Observation(
            vehicle_id=f"surface:asos:{stid}",
            lat=meta["lat"],
            lon=meta["lon"],
            timestamp=ts,
            icon_type="mesonet",
            temperature_c=temp_c,
            dewpoint_c=dew_c,
            wind_speed_ms=wind_ms,
            wind_dir_deg=wdir,
            pressure_mb=pres,
        )
        result.setdefault(stid, []).append(obs)

    for stid in result:
        result[stid].sort(key=lambda o: o.timestamp)
    return result


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "" or value == "M":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_valid(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_asos_history(
    station_ids: list[str], roster: dict[str, dict], day: datetime, *, cancel: threading.Event | None = None,
) -> dict[str, list[Observation]]:
    """Fetch and parse ASOS history for the given stations over the archive
    session's span -- its UTC day and the next morning to the 06Z cap
    (archive/session.py) -- in paced batches. `cancel`, if given, is
    checked between batches so a redrawn bbox can abandon an in-flight
    load promptly."""
    from archive.session import session_bounds
    sts, ets = session_bounds(day)

    merged: dict[str, list[Observation]] = {}
    batches = [station_ids[i:i + _BATCH_SIZE] for i in range(0, len(station_ids), _BATCH_SIZE)]
    for i, batch in enumerate(batches):
        if cancel is not None and cancel.is_set():
            return merged
        url = _batch_url(batch, sts, ets)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
        raw_text = package_sources.read_url("asos", request, _urlopen_with_retry).decode("utf-8", errors="replace")
        for stid, obs_list in parse_asos_history_csv(raw_text, roster).items():
            merged.setdefault(stid, []).extend(obs_list)
        if i < len(batches) - 1:
            time.sleep(_BATCH_PACE_S)

    for stid in merged:
        merged[stid].sort(key=lambda o: o.timestamp)
    return merged


class ArchiveAsosFetcher(QObject):
    """Loads and replays historical ASOS observations for a user-drawn map
    bbox on the archive clock."""

    stations_updated = pyqtSignal(str, str, object)  # station_id, name, Observation
    stations_cleared = pyqtSignal()
    load_finished = pyqtSignal(int)  # station count actually loaded
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._session_date = session_date
        self._observations: dict[str, list[Observation]] = {}
        self._timestamps: dict[str, list[datetime]] = {}
        self._names: dict[str, str] = {}
        self._last_indices: dict[str, int] = {}
        self._last_time: datetime | None = None
        self._loaded = False
        self._generation = 0
        self._cancel: threading.Event | None = None

    def set_bbox(self, west: float, south: float, east: float, north: float) -> None:
        """Clear whatever was loaded for a previous box and start a fresh,
        bounded background load for this one. A load already in flight for
        an earlier box is abandoned -- its results, if they arrive late,
        are discarded by the generation check in _load."""
        if self._cancel is not None:
            self._cancel.set()
        self._generation += 1
        generation = self._generation
        self._cancel = threading.Event()

        self._observations = {}
        self._timestamps = {}
        self._names = {}
        self._last_indices = {}
        self._loaded = False
        self.stations_cleared.emit()

        threading.Thread(
            target=self._load, args=(generation, west, south, east, north, self._cancel), daemon=True,
        ).start()

    def clear(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        self._generation += 1
        self._observations = {}
        self._timestamps = {}
        self._names = {}
        self._last_indices = {}
        self._loaded = False
        self.stations_cleared.emit()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            return
        if self._last_time is not None and archive_time < self._last_time:
            self._last_indices.clear()
            self.stations_cleared.emit()
        self._last_time = archive_time

        for station_id in self._observations:
            index = self._index_at(station_id, archive_time)
            if index < 0 or self._last_indices.get(station_id) == index:
                continue
            self._last_indices[station_id] = index
            obs = self._observations[station_id][index]
            self.stations_updated.emit(station_id, self._names.get(station_id, station_id), obs)

    def _index_at(self, station_id: str, archive_time: datetime) -> int:
        timestamps = self._timestamps.get(station_id)
        if not timestamps:
            return -1
        return bisect.bisect_right(timestamps, archive_time) - 1

    def _load(self, generation: int, west: float, south: float, east: float, north: float, cancel: threading.Event) -> None:
        try:
            roster = load_asos_station_roster()
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveAsosFetcher: station roster load failed: %s", exc)
            self.error.emit(f"ASOS station roster unavailable: {exc}")
            return

        station_ids = _stations_in_bbox(roster, west, south, east, north)
        if not station_ids:
            if generation == self._generation:
                self._loaded = True
                self.load_finished.emit(0)
            return
        if len(station_ids) > MAX_ASOS_STATIONS:
            log.warning(
                "ArchiveAsosFetcher: bbox contains %d stations; capping at %d",
                len(station_ids), MAX_ASOS_STATIONS,
            )
            station_ids = station_ids[:MAX_ASOS_STATIONS]

        try:
            by_station = fetch_asos_history(station_ids, roster, self._session_date, cancel=cancel)
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveAsosFetcher: history fetch failed: %s", exc)
            if generation == self._generation:
                self.error.emit(f"ASOS history fetch failed: {exc}")
            return

        if generation != self._generation or cancel.is_set():
            return  # a newer bbox has already been drawn; discard this stale result

        self._observations = by_station
        self._timestamps = {stid: [o.timestamp for o in obs] for stid, obs in by_station.items()}
        self._names = {stid: roster.get(stid, {}).get("name", stid) for stid in by_station}
        self._loaded = True
        self.load_finished.emit(len(by_station))
