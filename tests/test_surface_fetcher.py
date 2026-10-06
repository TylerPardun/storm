from datetime import datetime, timezone
import sys
import types

if "PyQt6" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _Signal:
        def connect(self, *_args, **_kwargs):
            pass

        def emit(self, *_args, **_kwargs):
            pass

    class _QObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class _QTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _Signal()

        def setInterval(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    qtcore.QObject = _QObject
    qtcore.QTimer = _QTimer
    qtcore.pyqtSignal = lambda *_args, **_kwargs: _Signal()
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore

from data.fetchers import surface_fetcher as sf
from data.fetchers.surface_fetcher import SurfaceFetcher


def test_asos_station_cache_uses_bundled_data_file():
    assert sf._ASOS_STATIONS_FILE.name == "asos_stations.json"
    assert sf._ASOS_STATIONS_FILE.parent.name == "data"


def test_ensure_asos_stations_delegates_to_the_shared_roster_loader(monkeypatch):
    # load_asos_station_roster() is also used directly by archive mode
    # (archive/fetchers/asos_archive_fetcher.py) -- SurfaceFetcher must keep
    # using the same shared loader/cache, not its own separate copy.
    calls = []
    roster = {"OUN": {"lat": 35.24, "lon": -97.47, "name": "Norman"}}
    monkeypatch.setattr(sf, "load_asos_station_roster", lambda: (calls.append(1), roster)[1])

    fetcher = SurfaceFetcher()
    first = fetcher._ensure_asos_stations()
    second = fetcher._ensure_asos_stations()

    assert first is roster
    assert second is roster
    assert len(calls) == 1  # cached on the instance after the first call


def test_fetch_iem_batch_uses_utc_valid_timestamp():
    fetcher = SurfaceFetcher()
    fetcher._http_get = lambda _url: b"""{
      "data": [{
        "station": "OKC",
        "name": "OKLAHOMA CITY",
        "utc_valid": "2026-04-18T23:52:00Z",
        "tmpf": "68",
        "dwpf": "50",
        "sknt": "10",
        "drct": "180",
        "mslp": "1012.3",
        "lat": "35.3889",
        "lon": "-97.6006"
      }]
    }"""

    rows = fetcher._fetch_iem_batch(
        ["OKC"],
        {"OKC": {"lat": 35.3889, "lon": -97.6006, "name": "OKLAHOMA CITY"}},
    )

    assert len(rows) == 1
    obs = rows[0]["obs"]
    assert rows[0]["id"] == "surface:asos:OKC"
    assert obs.timestamp == datetime(2026, 4, 18, 23, 52, tzinfo=timezone.utc)


def test_fetch_ok_mesonet_uses_configured_api_headers():
    assert sf.OK_API_URL.endswith("/data/mesonet/ok_mesonet.json")
    fetcher = SurfaceFetcher()
    captured: dict[str, object] = {}

    def _http_get(url, ssl_ctx=None, headers=None):
        captured["url"] = url
        captured["ssl_ctx"] = ssl_ctx
        captured["headers"] = headers
        if url == sf.OK_META_URL:
            return b"STID,NAME,LAT,LON,PRES\nacme,Acme,34.80833,-98.02325,1012.4\n"
        return b"""{
          "time": "2026-04-23T19:25:00+00:00",
          "data": {
            "tair": {"acme": 26.55},
            "tdew": {"acme": 20.11},
            "wspd": {"acme": 11.17},
            "wdir": {"acme": 183.7},
            "pres": {"acme": 958.08}
          }
        }"""

    fetcher._http_get = _http_get
    rows = fetcher._fetch_ok_mesonet()

    assert captured["url"] == sf.OK_API_URL
    assert captured["ssl_ctx"] is None
    assert captured["headers"] == {"X-API-Key": sf.config.NSSL_API_KEY}
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:ok:acme"
    assert rows[0]["obs"].timestamp == datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)
    assert rows[0]["obs"].pressure_mb == 1012.4  # CSV MSLP, not JSON station pressure


def test_fetch_wtm_uses_configured_api_headers():
    assert sf.WTM_API_URL.endswith("/data/mesonet/wtx_mesonet.json")
    fetcher = SurfaceFetcher()
    fetcher._wtm_meta = {
        "test": {"lat": 32.0, "lon": -101.0, "name": "Test WTM"},
    }
    captured: dict[str, object] = {}

    def _http_get(url, ssl_ctx=None, headers=None):
        captured["url"] = url
        captured["ssl_ctx"] = ssl_ctx
        captured["headers"] = headers
        return b"""{
          "results": [{
            "mid": "test",
            "utc": "2026-04-23T19:25:00+00:00",
            "temp1p5m": 22.1,
            "dp1p5m": 10.2,
            "wspd10m": 8.3,
            "wdir10m": 190.0,
            "pres": 905.2
          }]
        }"""

    fetcher._http_get = _http_get
    rows = fetcher._fetch_wtm()

    assert captured["url"] == sf.WTM_API_URL
    assert captured["ssl_ctx"] is None
    assert captured["headers"] == {"X-API-Key": sf.config.NSSL_API_KEY}
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:wtm:test"
    assert rows[0]["obs"].timestamp == datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)


def test_fetch_ks_mesonet_uses_configured_api_headers_and_embedded_metadata():
    assert sf.KS_API_URL.endswith("/data/mesonet/ks_mesonet.json")
    fetcher = SurfaceFetcher()
    captured: dict[str, object] = {}

    def _http_get(url, ssl_ctx=None, headers=None):
        captured["url"] = url
        captured["ssl_ctx"] = ssl_ctx
        captured["headers"] = headers
        return b"""{
          "time": "2026-05-07T19:10:00+00:00",
          "results": [{
            "station": "Alma 5SE",
            "timestamp": "2026-05-07T19:05:00+00:00",
            "TEMP2MAVG": "22.1",
            "RELHUM2MAVG": "50.0",
            "PRESSUREAVG": "96.32",
            "SLPAVG": "101.25",
            "WDIR10M": "212.74",
            "WSPD10MAVG": "9.54",
            "WSPD10MMAX": "12.58",
            "PRECIP": "0.0",
            "meta": {
              "name": "Alma 5SE",
              "abbr": "Alma 5SE",
              "lat": 38.96615,
              "lon": -96.2063,
              "elevation": 428.0
            }
          }]
        }"""

    fetcher._http_get = _http_get
    rows = fetcher._fetch_ks_mesonet()

    assert captured["url"] == sf.KS_API_URL
    assert captured["ssl_ctx"] is None
    assert captured["headers"] == {"X-API-Key": sf.config.NSSL_API_KEY}
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:ks:alma_5se"
    assert rows[0]["source"] == "ks"
    assert rows[0]["name"] == "Alma 5SE"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 7, 19, 5, tzinfo=timezone.utc)
    assert obs.temperature_c == 22.1
    assert round(obs.dewpoint_c, 1) == 11.2
    assert obs.wind_speed_ms == 9.54
    assert obs.wind_dir_deg == 212.74
    assert obs.pressure_mb == 1012.5  # SLPAVG kPa -> hPa, not station pressure


def test_fetch_co_mesonet_uses_configured_api_headers_and_metadata_file():
    assert sf.CO_API_URL.endswith("/data/mesonet/co_mesonet.json")
    assert sf.CO_META_URL.endswith("/data/mesonet/co_metadata.json")
    fetcher = SurfaceFetcher()
    captured: list[tuple[str, object, object]] = []

    def _http_get(url, ssl_ctx=None, headers=None):
        captured.append((url, ssl_ctx, headers))
        if url == sf.CO_META_URL:
            return b"""{
              "alt01": {
                "station": "alt01",
                "name": "Ault",
                "lat": 40.569,
                "lon": -104.7195
              }
            }"""
        return b"""{
          "which": "qc",
          "timezone": "utc",
          "units": "us",
          "stations": ["alt01"],
          "alt01": {
            "time": "2026-05-20T17:45",
            "t": 52.63,
            "rh": 0.645,
            "dewpt": 41.0,
            "windSpeed": 3.6,
            "windDir": 148.2,
            "gustSpeed": -999
          }
        }"""

    fetcher._http_get = _http_get
    rows = fetcher._fetch_co_mesonet()

    assert captured == [
        (sf.CO_META_URL, None, {"X-API-Key": sf.config.NSSL_API_KEY}),
        (sf.CO_API_URL, None, {"X-API-Key": sf.config.NSSL_API_KEY}),
    ]
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:co:alt01"
    assert rows[0]["source"] == "co"
    assert rows[0]["name"] == "Ault"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 20, 17, 45, tzinfo=timezone.utc)
    assert round(obs.temperature_c, 1) == 11.5
    assert round(obs.dewpoint_c, 1) == 5.0
    assert round(obs.wind_speed_ms, 2) == 1.61
    assert obs.wind_dir_deg == 148.2
    assert obs.pressure_mb is None


def test_fetch_ne_mesonet_uses_configured_api_headers_and_embedded_metadata():
    assert sf.NE_API_URL.endswith("/data/mesonet/ne_mesonet.json")
    fetcher = SurfaceFetcher()
    captured: dict[str, object] = {}

    def _http_get(url, ssl_ctx=None, headers=None):
        captured["url"] = url
        captured["ssl_ctx"] = ssl_ctx
        captured["headers"] = headers
        return b"""{
          "data": [{
            "id": 54,
            "name": "Holdrege 5N",
            "latitude": 40.5045,
            "longitude": -99.360083,
            "timestamp": "2026-05-20T20:21:00.000000Z",
            "data": {
              "temperature": {"twoMeter": 12.91},
              "dewPoint": {"twoMeter": 6.43},
              "pressure": {"seaLevel": 1023.71},
              "wind": {
                "threeMeter": {
                  "speed": {"avg": 2.4851},
                  "direction": {"avg": 65.2785}
                },
                "tenMeter": {
                  "speed": {"avg": null},
                  "direction": {"avg": null}
                }
              }
            }
          }]
        }"""

    fetcher._http_get = _http_get
    rows = fetcher._fetch_ne_mesonet()

    assert captured["url"] == sf.NE_API_URL
    assert captured["ssl_ctx"] is None
    assert captured["headers"] == {"X-API-Key": sf.config.NSSL_API_KEY}
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:ne:54"
    assert rows[0]["source"] == "ne"
    assert rows[0]["name"] == "Holdrege 5N"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 20, 20, 21, tzinfo=timezone.utc)
    assert obs.temperature_c == 12.91
    assert obs.dewpoint_c == 6.43
    assert obs.wind_speed_ms == 2.4851
    assert obs.wind_dir_deg == 65.2785
    assert obs.pressure_mb == 1023.71


def test_diagnostics_snapshot_tracks_valid_and_fetch_times():
    fetcher = SurfaceFetcher()
    attempt = datetime(2026, 4, 23, 19, 30, tzinfo=timezone.utc)
    valid = datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)

    class _Obs:
        timestamp = valid

    fetcher._ok_enabled = True
    fetcher._update_source_diag(
        "ok",
        "OK",
        attempt,
        [{"obs": _Obs()}],
        False,
        None,
    )

    snap = fetcher._diagnostics_snapshot()
    assert snap["ok"]["label"] == "OK"
    assert snap["ok"]["last_attempt"] == attempt
    assert snap["ok"]["last_success"] == attempt
    assert snap["ok"]["valid_time"] == valid
    assert snap["ok"]["count"] == 1
    assert snap["ok"]["stale"] is False
