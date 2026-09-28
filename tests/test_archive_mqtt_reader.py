"""Tests for archive MQTT replay helpers."""

import json
from datetime import datetime, timezone
from urllib.error import URLError

from archive.fetchers import mqtt_reader as mr
from archive.fetchers.mqtt_reader import ArchiveMQTTReader, _fetch_topic_text, _parse_jsonl


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 4, 26, hour, minute, tzinfo=timezone.utc)


def test_parse_jsonl_accepts_scan_sector_timestamp():
    records = _parse_jsonl(json.dumps({
        "vehicle_id": "WX1",
        "active": True,
        "mode": "point",
        "lat": 35.0,
        "lon": -97.0,
        "timestamp": "2026-04-26T12:05:00Z",
    }))

    assert records == [(_utc(12, 5), records[0][1])]


def test_archive_reader_replays_latest_scan_sector_state():
    reader = ArchiveMQTTReader(session_date=_utc(0))
    emitted = []
    reader.scan_sector_received.connect(emitted.append)
    reader._loaded = True
    reader._data["scan_sectors"] = _parse_jsonl("\n".join([
        json.dumps({
            "vehicle_id": "WX1",
            "active": True,
            "mode": "point",
            "lat": 35.0,
            "lon": -97.0,
            "timestamp": "2026-04-26T12:05:00Z",
        }),
        json.dumps({
            "vehicle_id": "WX1",
            "active": True,
            "mode": "sector",
            "lat": 35.1,
            "lon": -97.1,
            "range_m": 8000,
            "azimuth_deg": 270,
            "beam_width_deg": 60,
            "timestamp": "2026-04-26T12:10:00Z",
        }),
        json.dumps({
            "vehicle_id": "WX1",
            "active": False,
            "mode": "sector",
            "lat": 35.1,
            "lon": -97.1,
            "range_m": 8000,
            "azimuth_deg": 270,
            "beam_width_deg": 60,
            "timestamp": "2026-04-26T12:20:00Z",
        }),
    ]))

    reader.on_time_changed(_utc(12, 15))
    assert emitted[-1].vehicle_id == "WX1"
    assert emitted[-1].active is True
    assert emitted[-1].mode == "sector"

    reader.on_time_changed(_utc(12, 25))
    assert emitted[-1].vehicle_id == "WX1"
    assert emitted[-1].active is False


def test_archive_reader_clears_scan_sectors_on_backward_seek():
    reader = ArchiveMQTTReader(session_date=_utc(0))
    cleared = []
    reader.scan_sectors_cleared.connect(lambda: cleared.append(True))
    reader._loaded = True

    reader.on_time_changed(_utc(12, 30))
    reader.on_time_changed(_utc(12, 0))

    assert cleared == [True]


# ---------------------------------------------------------------------------
# THREDDS-first / API-fallback (2026-09-08: api.nssl.noaa.gov was found
# down, the same storm.<topic>.<date> files are also mirrored on THREDDS)
# ---------------------------------------------------------------------------


def test_fetch_topic_text_prefers_thredds_when_available(monkeypatch):
    calls = []

    def fake_fetch_text(url, *, api_key=False):
        calls.append(url)
        assert "thredds" in url.lower()
        return "thredds-data"

    monkeypatch.setattr(mr, "_fetch_text", fake_fetch_text)

    text, source = _fetch_topic_text("scan_sectors", "20260621")

    assert (text, source) == ("thredds-data", "THREDDS")
    assert len(calls) == 1  # the API is never called once THREDDS answers


def test_fetch_topic_text_falls_back_to_api_on_a_clean_thredds_404(monkeypatch):
    def fake_fetch_text(url, *, api_key=False):
        if "thredds" in url.lower():
            return None  # THREDDS's own 404-means-no-file convention
        assert api_key is True  # the API fallback must send its auth header
        return "api-data"

    monkeypatch.setattr(mr, "_fetch_text", fake_fetch_text)

    text, source = _fetch_topic_text("scan_sectors", "20260621")

    assert (text, source) == ("api-data", "API")


def test_fetch_topic_text_falls_back_to_api_on_a_thredds_network_error(monkeypatch):
    # A THREDDS hiccup shouldn't sink the whole lookup when the API might
    # still answer -- fall through on any failure, not just a clean 404.
    def fake_fetch_text(url, *, api_key=False):
        if "thredds" in url.lower():
            raise URLError("timed out")
        return "api-data"

    monkeypatch.setattr(mr, "_fetch_text", fake_fetch_text)

    text, source = _fetch_topic_text("scan_sectors", "20260621")

    assert (text, source) == ("api-data", "API")


def test_fetch_topic_text_returns_none_when_neither_source_has_the_date(monkeypatch):
    monkeypatch.setattr(mr, "_fetch_text", lambda url, **kw: None)

    text, source = _fetch_topic_text("scan_sectors", "20990101")

    assert text is None
    assert source == "API"


def test_thredds_failure_and_api_absence_is_unknown(monkeypatch):
    from archive.fetchers import mqtt_reader as reader
    import pytest
    def fetch(url, **kwargs):
        if 'thredds' in url:
            raise TimeoutError('timed out')
        return None
    monkeypatch.setattr(reader, '_fetch_text', fetch)
    with pytest.raises(RuntimeError, match='availability unknown'):
        reader._fetch_topic_text('scan_sectors', '20240427')


def test_only_an_http_404_is_a_clean_miss(monkeypatch):
    from archive.fetchers import mqtt_reader as reader
    from urllib.error import URLError
    import pytest
    def fail(*args, **kwargs):
        raise URLError('request failed for 20240427')
    monkeypatch.setattr(reader, 'urlopen', fail)
    with pytest.raises(URLError):
        reader._fetch_text('https://example.test/20240427')


# ---------------------------------------------------------------------------
# Annotations, cones and drawings at the playback time (2026-09-28). Real
# records from 17-18 May 2026: cones expire an hour after they're issued,
# and the crew edits by republishing the same id.
# ---------------------------------------------------------------------------

_CONES_20260517 = """
{"id": "eeacaa04", "lat": 40.61269656417417, "lon": -96.97905039839846, "heading": 186, "speed_kts": 30.271497349206385, "creator": "local", "created_at": "2026-05-17T01:06:45.368858+00:00", "valid_at": "2026-05-17T01:04:30+00:00", "expires_at": "2026-05-17T02:06:44.522916+00:00"}
{"id": "014b51d5", "lat": 40.980667415343646, "lon": -98.70595209924213, "heading": 235, "speed_kts": 40.575703617927964, "creator": "local", "created_at": "2026-05-17T21:39:53.042298+00:00", "valid_at": "2026-05-17T21:37:52+00:00", "expires_at": "2026-05-17T22:39:52.311024+00:00"}
{"id": "3933269d", "lat": 41.1156262975386, "lon": -98.42578123619359, "heading": 248, "speed_kts": 42.660523617977006, "creator": "local", "created_at": "2026-05-17T22:09:09.873397+00:00", "valid_at": "2026-05-17T22:03:48+00:00", "expires_at": "2026-05-17T23:09:08.968967+00:00"}
{"id": "014b51d5", "deleted": true, "deleted_at": "2026-05-17T22:09:39.622733+00:00"}
{"id": "3933269d", "lat": 41.1156262975386, "lon": -98.42578123619359, "heading": 245, "speed_kts": 40.0, "creator": "local", "created_at": "2026-05-17T22:09:51.734776+00:00", "valid_at": "2026-05-17T22:03:48+00:00", "expires_at": "2026-05-17T23:09:50.839635+00:00"}
{"id": "014b51d5", "deleted": true, "deleted_at": "2026-05-17T22:19:09.488983+00:00"}
{"id": "d847ecce", "lat": 40.41407252963177, "lon": -97.12214087556731, "heading": 222, "speed_kts": 45.33299946388733, "creator": "local", "created_at": "2026-05-18T00:42:22.192973+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:42:21.361227+00:00"}
{"id": "d847ecce", "lat": 40.37395995118936, "lon": -97.12842557991583, "heading": 222, "speed_kts": 45.33299946388733, "creator": "local", "created_at": "2026-05-18T00:42:34.363196+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:42:33.531852+00:00"}
{"id": "d847ecce", "lat": 40.37395995118936, "lon": -97.12842557991583, "heading": 235, "speed_kts": 40.0, "creator": "local", "created_at": "2026-05-18T00:42:48.519695+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:42:47.684232+00:00"}
{"id": "d847ecce", "lat": 40.41025901279002, "lon": -97.10454370339104, "heading": 235, "speed_kts": 40.0, "creator": "local", "created_at": "2026-05-18T00:43:10.489758+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:43:09.645540+00:00"}
{"id": "d847ecce", "lat": 40.42936350970771, "lon": -97.09574511730308, "heading": 235, "speed_kts": 40.0, "creator": "local", "created_at": "2026-05-18T00:43:26.399986+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:43:25.558110+00:00"}
{"id": "d847ecce", "lat": 40.42936350970771, "lon": -97.09574511730308, "heading": 235, "speed_kts": 35.0, "creator": "local", "created_at": "2026-05-18T00:43:33.888336+00:00", "valid_at": "2026-05-18T00:38:20+00:00", "expires_at": "2026-05-18T01:43:33.050767+00:00"}
{"id": "d847ecce", "deleted": true, "deleted_at": "2026-05-18T01:18:43.842278+00:00"}
"""

_DRAWINGS_20260517 = """
{"id": "cb97a3d0", "drawing_type": "cold_front", "coordinates": [[42.750718348053, -99.48151347365541], [41.406715861582285, -100.42768504404339]], "title": "Cold Front", "creator": "local", "created_at": "2026-05-17T17:48:10.961306+00:00", "flipped": false, "color": "#4A9EFF", "line_style": "solid", "expires_at": "2026-05-18T07:59:59.089416+00:00"}
{"id": "cb97a3d0", "drawing_type": "cold_front", "coordinates": [[42.750718348053, -99.48151347365541], [41.406715861582285, -100.42768504404339]], "title": "Cold Front", "creator": "local", "created_at": "2026-05-17T17:48:10.961306+00:00", "flipped": true, "color": "#4A9EFF", "line_style": "solid", "expires_at": "2026-05-18T07:59:59.188249+00:00"}
"""


def _may17(hour, minute=0, day=17):
    return datetime(2026, 5, day, hour, minute, tzinfo=timezone.utc)


def _cone_reader():
    reader = ArchiveMQTTReader(session_date=_may17(20))
    shown = {}
    reader.cone_received.connect(lambda cone: shown.__setitem__(cone.id, cone))
    reader.cone_deleted.connect(lambda cone_id: shown.pop(cone_id))
    reader._loaded = True
    reader._data["cones"] = _parse_jsonl(_CONES_20260517)
    return reader, shown


def test_a_cone_disappears_when_it_expires():
    reader, shown = _cone_reader()
    reader.on_time_changed(_may17(1, 30))
    assert set(shown) == {"eeacaa04"}                  # issued 01:06Z, the evening before
    reader.on_time_changed(_may17(23, 8))
    assert "eeacaa04" not in shown                      # expired 02:06Z -- not still up at 2308Z
    reader.on_time_changed(_may17(23, 30))
    assert shown == {}                                  # 3933269d expired 23:09Z


def test_a_cone_shows_when_issued_in_its_latest_version():
    reader, shown = _cone_reader()
    reader.on_time_changed(_may17(22, 0))
    assert set(shown) == {"014b51d5"}                   # 3933269d not issued until 22:09Z
    reader.on_time_changed(datetime(2026, 5, 17, 22, 9, 30, tzinfo=timezone.utc))
    assert shown["3933269d"].speed_kts > 42             # first version
    reader.on_time_changed(_may17(22, 15))
    assert shown["3933269d"].speed_kts == 40.0          # edited at 22:09:51Z
    reader.on_time_changed(_may17(0, 44, day=18))
    assert shown["d847ecce"].speed_kts == 35.0          # the last of six edits


def test_seeking_back_shows_what_existed_then():
    reader, shown = _cone_reader()
    reader.on_time_changed(_may17(22, 15))
    reader.on_time_changed(_may17(21, 45))
    assert set(shown) == {"014b51d5"}
    reader.on_time_changed(_may17(22, 15))
    assert set(shown) == {"3933269d"}                   # 014b51d5 was deleted at 22:09Z


def test_an_edited_drawing_shows_its_latest_version():
    reader = ArchiveMQTTReader(session_date=_may17(20))
    shown = {}
    reader.drawing_received.connect(lambda d: shown.__setitem__(d.id, d))
    reader._loaded = True
    reader._data["drawings"] = _parse_jsonl(_DRAWINGS_20260517)
    reader.on_time_changed(_may17(20))
    assert shown["cb97a3d0"].flipped is True            # the cold front was flipped after drawing
