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
