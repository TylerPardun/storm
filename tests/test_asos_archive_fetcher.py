import threading
from datetime import datetime, timezone

import pytest

from archive.fetchers import asos_archive_fetcher as aaf
from archive.fetchers.asos_archive_fetcher import (
    ArchiveAsosFetcher,
    _batch_url,
    fetch_asos_history,
    parse_asos_history_csv,
)

_ROSTER = {
    "OUN": {"lat": 35.24, "lon": -97.47, "name": "Norman"},
    "OKC": {"lat": 35.39, "lon": -97.60, "name": "Oklahoma City"},
}

_CSV = (
    "station,valid,tmpf,dwpf,relh,drct,sknt,alti,mslp\n"
    "OUN,2013-05-31 20:15,68.00,50.00,M,170.00,10.00,29.67,M\n"
    "OUN,2013-05-31 20:35,70.00,M,M,160.00,M,29.68,M\n"
    "OKC,2013-05-31 20:52,75.00,65.00,M,170.00,13.00,M,1003.10\n"
    "XXX,2013-05-31 20:52,75.00,65.00,M,170.00,13.00,M,1003.10\n"
)


def test_parse_asos_history_csv_converts_units_matching_live_mode():
    result = parse_asos_history_csv(_CSV, _ROSTER)
    oun = result["OUN"]
    assert len(oun) == 2
    assert oun[0].temperature_c == pytest.approx((68.0 - 32.0) * 5.0 / 9.0)
    assert oun[0].wind_speed_ms == pytest.approx(10.0 * 0.514444)
    assert oun[0].dewpoint_c == pytest.approx((50.0 - 32.0) * 5.0 / 9.0)


def test_parse_asos_history_csv_prefers_mslp_over_altimeter():
    result = parse_asos_history_csv(_CSV, _ROSTER)
    okc = result["OKC"][0]
    assert okc.pressure_mb == pytest.approx(1003.10)


def test_parse_asos_history_csv_missing_marker_becomes_none():
    result = parse_asos_history_csv(_CSV, _ROSTER)
    second = result["OUN"][1]
    assert second.dewpoint_c is None
    assert second.wind_speed_ms is None


def test_parse_asos_history_csv_drops_stations_missing_from_the_roster():
    result = parse_asos_history_csv(_CSV, _ROSTER)
    assert "XXX" not in result


def test_parse_asos_history_csv_sorts_each_station_by_time():
    unordered = (
        "station,valid,tmpf,dwpf,relh,drct,sknt,alti,mslp\n"
        "OUN,2013-05-31 21:00,70.00,M,M,M,M,M,M\n"
        "OUN,2013-05-31 20:00,68.00,M,M,M,M,M,M\n"
    )
    result = parse_asos_history_csv(unordered, _ROSTER)
    assert [o.timestamp.hour for o in result["OUN"]] == [20, 21]


def test_batch_url_repeats_station_param_and_formats_time_window():
    url = _batch_url(["OUN", "OKC"], datetime(2013, 5, 31, tzinfo=timezone.utc), datetime(2013, 6, 1, tzinfo=timezone.utc))
    assert url.count("station=OUN") == 1
    assert url.count("station=OKC") == 1
    assert "sts=2013-05-31T00%3A00%3A00Z" in url or "sts=2013-05-31T00:00:00Z" in url
    assert "ets=2013-06-01T00:00:00Z" in url
    assert "format=onlycomma" in url


def test_fetch_asos_history_paces_between_batches_not_per_station(monkeypatch):
    sleeps = []
    monkeypatch.setattr(aaf.time, "sleep", lambda s: sleeps.append(s))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _CSV.encode()

    monkeypatch.setattr(aaf, "_urlopen_with_retry", lambda request: _Resp())

    station_ids = [f"S{i}" for i in range(120)]  # 3 batches at _BATCH_SIZE=50
    fetch_asos_history(station_ids, _ROSTER, datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert len(sleeps) == 2  # paced between batches, never after the last one


def test_fetch_asos_history_stops_early_when_canceled(monkeypatch):
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _CSV.encode()

    def fake_urlopen(request):
        calls.append(request)
        return _Resp()

    monkeypatch.setattr(aaf, "_urlopen_with_retry", fake_urlopen)
    monkeypatch.setattr(aaf.time, "sleep", lambda s: None)

    cancel = threading.Event()
    cancel.set()
    station_ids = [f"S{i}" for i in range(120)]
    result = fetch_asos_history(station_ids, _ROSTER, datetime(2013, 5, 31, tzinfo=timezone.utc), cancel=cancel)
    assert calls == []
    assert result == {}


def _utc(hour, minute=0):
    return datetime(2013, 5, 31, hour, minute, tzinfo=timezone.utc)


def _populated_fetcher():
    fetcher = ArchiveAsosFetcher(_utc(0))
    fetcher._observations = {
        stid: [obj for obj in parse_asos_history_csv(_CSV, _ROSTER).get(stid, [])]
        for stid in ("OUN", "OKC")
    }
    fetcher._timestamps = {stid: [o.timestamp for o in obs] for stid, obs in fetcher._observations.items()}
    fetcher._names = {"OUN": "Norman", "OKC": "Oklahoma City"}
    fetcher._loaded = True
    return fetcher


def test_on_time_changed_emits_only_stations_whose_index_actually_changed():
    fetcher = _populated_fetcher()
    updates = []
    fetcher.stations_updated.connect(lambda sid, name, obs: updates.append((sid, obs.timestamp)))

    fetcher.on_time_changed(_utc(20, 20))  # only OUN's first fix (20:15) is reached
    assert [sid for sid, _ in updates] == ["OUN"]

    updates.clear()
    fetcher.on_time_changed(_utc(20, 40))  # OUN advances to 20:35; OKC still has nothing yet
    assert [sid for sid, _ in updates] == ["OUN"]

    updates.clear()
    fetcher.on_time_changed(_utc(20, 40))  # no change since the last call
    assert updates == []

    updates.clear()
    fetcher.on_time_changed(_utc(21, 0))  # OKC's 20:52 fix is now reached too
    assert sorted(sid for sid, _ in updates) == ["OKC"]


def test_on_time_changed_before_any_data_emits_nothing():
    fetcher = _populated_fetcher()
    updates = []
    fetcher.stations_updated.connect(lambda *a: updates.append(a))
    fetcher.on_time_changed(_utc(0, 0))
    assert updates == []


def test_rewind_clears_and_reemits_on_the_next_forward_tick():
    fetcher = _populated_fetcher()
    cleared = []
    fetcher.stations_cleared.connect(lambda: cleared.append(True))
    updates = []
    fetcher.stations_updated.connect(lambda sid, name, obs: updates.append(sid))

    fetcher.on_time_changed(_utc(21, 0))
    assert cleared == []  # forward tick, no rewind yet
    updates.clear()

    fetcher.on_time_changed(_utc(20, 0))  # rewind
    assert cleared == [True]

    updates.clear()
    fetcher.on_time_changed(_utc(21, 0))  # forward again -- indices were cleared, so this re-emits
    assert sorted(updates) == ["OKC", "OUN"]


def test_on_time_changed_does_nothing_before_load_completes():
    fetcher = ArchiveAsosFetcher(_utc(0))
    updates = []
    fetcher.stations_updated.connect(lambda *a: updates.append(a))
    fetcher.on_time_changed(_utc(21, 0))
    assert updates == []


def test_set_bbox_redraw_discards_a_stale_in_flight_load(monkeypatch):
    # The first load blocks until released; a second set_bbox() call must
    # start a new generation whose result the first load's late completion
    # can never clobber.
    release_first = threading.Event()
    started_first = threading.Event()
    call_count = {"n": 0}

    def fake_load(self, generation, west, south, east, north, cancel):
        call_count["n"] += 1
        is_first_call = call_count["n"] == 1
        if is_first_call:
            started_first.set()
            release_first.wait(timeout=5)
        # simulate a real load completing and trying to publish its results
        if generation != self._generation:
            return
        self._observations = {"STALE": []} if is_first_call else {"FRESH": []}
        self._loaded = True

    monkeypatch.setattr(ArchiveAsosFetcher, "_load", fake_load)

    fetcher = ArchiveAsosFetcher(_utc(0))
    fetcher.set_bbox(-98, 35, -97, 36)
    assert started_first.wait(timeout=5)

    fetcher.set_bbox(-90, 30, -89, 31)  # redraw before the first load finishes
    release_first.set()

    import time
    time.sleep(0.3)  # let the first (stale) thread's late completion attempt run

    assert "STALE" not in fetcher._observations
    assert "FRESH" in fetcher._observations


def test_load_finished_emits_zero_when_bbox_has_no_stations():
    fetcher = ArchiveAsosFetcher(_utc(0))
    finished = []
    fetcher.load_finished.connect(finished.append)
    fetcher._load(fetcher._generation, 0.0, 0.0, 0.01, 0.01, threading.Event())
    assert finished == [0]
    assert fetcher._loaded is True
