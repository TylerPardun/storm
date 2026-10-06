import threading
from datetime import datetime, timezone

from PyQt6.QtWidgets import QApplication

from archive.fetchers import damage_paths_archive_fetcher as dpf
from archive.fetchers.damage_paths_archive_fetcher import (
    ArchiveDamagePathsFetcher,
    _feature_intersects_bbox,
    _localize_ncei_time,
    _ncei_timezone_offset_hours,
    _normalize_ef,
    _normalize_feature,
)


def _qapp():
    return QApplication.instance() or QApplication(["storm"])


def _line_feature(coords, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


def test_normalize_ef_recognizes_real_ratings():
    assert _normalize_ef({"efscale": "EF3"}) == "EF3"
    assert _normalize_ef({"efscale": "N/A"}) == "N/A"
    assert _normalize_ef({"efscale": "UNKNOWN"}) == "UNKNOWN"
    assert _normalize_ef({}) == "UNKNOWN"
    assert _normalize_ef({"efscale": None, "efnum": -99}) == "UNKNOWN"


def test_normalize_feature_assigns_label_color_and_source():
    f = _line_feature([[-98.0, 35.0], [-97.9, 35.1]], efscale="EF3", length=16.2, width=4576.0)
    out = _normalize_feature(f, source="NOAA DAT")
    props = out["properties"]
    assert props["ef_label"] == "EF3"
    assert props["ef_color"] == "#e69800"
    assert props["damage_source"] == "NOAA DAT"


def test_normalize_feature_treats_negative_sentinel_as_missing_not_a_real_value():
    # DAT's own -99 "no value" sentinel, confirmed live against real data --
    # must never surface as a literal "-99 yd" in the UI.
    f = _line_feature([[-98.0, 35.0], [-97.9, 35.1]], efscale="N/A", width=-99, maxwind=-99, length=5.57)
    out = _normalize_feature(f, source="NOAA DAT")
    props = out["properties"]
    assert props["width"] is None
    assert props["maxwind"] is None
    assert props["length"] == 5.57  # a genuinely positive value is untouched


def test_feature_intersects_bbox_keeps_a_line_crossing_the_box_with_both_endpoints_outside():
    # A long line whose two endpoints both sit outside the box but whose
    # segment bbox still overlaps it must be kept -- not just "is any
    # single vertex inside."
    f = _line_feature([[-100.0, 35.0], [-95.0, 35.0]])
    assert _feature_intersects_bbox(f, -98.0, 34.5, -97.0, 35.5) is True


def test_feature_intersects_bbox_rejects_a_line_entirely_outside():
    f = _line_feature([[-100.0, 40.0], [-99.0, 41.0]])
    assert _feature_intersects_bbox(f, -98.0, 34.5, -97.0, 35.5) is False


def test_ncei_timezone_offset_handles_the_real_combined_abbreviation_format():
    # Real NCEI CZ_TIMEZONE values are "CST-6" style (abbreviation + explicit
    # offset appended), not bare "CST" -- confirmed against a real downloaded
    # 2013 StormEvents_details CSV.
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": "CST-6"}) == -6.0
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": "CDT-5"}) == -5.0
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": ""}) is None


def test_localize_ncei_time_converts_the_real_confirmed_format_to_utc():
    row = {"CZ_TIMEZONE": "CST-6"}
    ts = _localize_ncei_time("17-NOV-13 13:14:00", row)
    assert ts == datetime(2013, 11, 17, 19, 14, 0, tzinfo=timezone.utc)


def test_localize_ncei_time_falls_back_to_utc_when_timezone_is_unknown():
    ts = _localize_ncei_time("17-NOV-13 13:14:00", {})
    assert ts == datetime(2013, 11, 17, 13, 14, 0, tzinfo=timezone.utc)


def test_set_bbox_redraw_discards_a_stale_in_flight_load(monkeypatch):
    release_first = threading.Event()
    started_first = threading.Event()
    call_count = {"n": 0}

    def fake_load(self, generation, west, south, east, north, cancel):
        call_count["n"] += 1
        is_first_call = call_count["n"] == 1
        if is_first_call:
            started_first.set()
            release_first.wait(timeout=5)
        if generation != self._generation:
            return
        if is_first_call:
            self.paths_ready.emit({"type": "FeatureCollection", "features": [{"stale": True}]})
        else:
            self.paths_ready.emit({"type": "FeatureCollection", "features": [{"fresh": True}]})

    monkeypatch.setattr(ArchiveDamagePathsFetcher, "_load", fake_load)

    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)

    fetcher.set_bbox(-98, 35, -97, 36)
    assert started_first.wait(timeout=5)

    fetcher.set_bbox(-90, 30, -89, 31)  # redraw before the first load finishes
    release_first.set()

    import time
    t0 = time.time()
    while len(received) < 1 and time.time() - t0 < 5:
        app.processEvents()
        time.sleep(0.02)

    assert len(received) == 1
    assert received[0]["features"] == [{"fresh": True}]


def test_load_falls_back_to_ncei_only_when_dat_returns_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(dpf, "fetch_dat_damage_paths", lambda *a, **kw: calls.append("dat") or {"type": "FeatureCollection", "features": []})
    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths", lambda *a, **kw: calls.append("ncei") or {"type": "FeatureCollection", "features": [{"id": 1}]})

    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)

    import time
    t0 = time.time()
    while not received and time.time() - t0 < 5:
        app.processEvents()
        time.sleep(0.02)

    assert calls == ["dat", "ncei"]
    assert received[0]["features"] == [{"id": 1}]


def test_load_skips_ncei_when_dat_has_real_features(monkeypatch):
    calls = []
    monkeypatch.setattr(dpf, "fetch_dat_damage_paths", lambda *a, **kw: calls.append("dat") or {"type": "FeatureCollection", "features": [{"id": 1}]})
    def fail_ncei(*a, **kw):
        calls.append("ncei")
        raise AssertionError("NCEI should not be queried when DAT already has results")
    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths", fail_ncei)

    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)

    import time
    t0 = time.time()
    while not received and time.time() - t0 < 5:
        app.processEvents()
        time.sleep(0.02)

    assert calls == ["dat"]
    assert received[0]["features"] == [{"id": 1}]


def test_paths_surveyed_during_the_sessions_next_morning_are_included():
    # The archive session runs to 06Z the next day (archive/session.py).
    from datetime import date, datetime, timezone
    from archive.fetchers.damage_paths_archive_fetcher import _date_within_tolerance
    from archive.session import session_bounds

    day = date(2024, 4, 27)
    _, cap = session_bounds(datetime(2024, 4, 27, tzinfo=timezone.utc))
    at_cap = {"properties": {"starttime": cap.timestamp() * 1000}}
    assert _date_within_tolerance(at_cap, day, 1)
