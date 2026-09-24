"""Tests for one-second FOFS archive observations."""

from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import numpy as np
import pytest

from archive.fetchers import vehicle_obs_archive_fetcher as vof
from archive.fetchers.vehicle_obs_archive_fetcher import (
    ArchiveVehicleObsFetcher,
    _daily_url,
    _urlopen_with_retry,
    load_dltruck_track,
    parse_vehicle_csv,
    parse_vehicle_netcdf,
)
from core.observation import Observation


def _write_processed_netcdf(path, epochtime, lat, lon, t_fast=None):
    """Build a minimal file matching the real FOFS processed/*.nc schema."""
    import xarray as xr

    n = len(epochtime)
    data_vars = {
        "epochtime": ("time", np.array(epochtime, dtype="float64")),
        "lat": ("time", np.array(lat, dtype="float64")),
        "lon": ("time", np.array(lon, dtype="float64")),
    }
    if t_fast is not None:
        data_vars["t_fast"] = ("time", np.array(t_fast, dtype="float64"))
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


_CSV = """sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon
2.3,215.2,22.82,19.5,977.29,160426,000004,33.24266,-97.60402
2.6,209.2,22.83,19.6,977.30,160426,000005,33.24267,-97.60403
"""


def _utc(second: int) -> datetime:
    return datetime(2026, 4, 16, tzinfo=timezone.utc) + timedelta(seconds=second)


def test_daily_url_uses_vehicle_alias():
    assert _daily_url("lid1", "20260416").endswith(
        "/dltruck/raw/20260416.txt"
    )
    assert _daily_url("p1", "20260416").endswith(
        "/probe1/raw/20260416.txt"
    )


def test_urlopen_with_retry_retries_transient_errors_then_succeeds(monkeypatch):
    # data.nssl.noaa.gov's WAF has been observed to soft-throttle bursts of
    # requests with connection timeouts that clear up within seconds -- a
    # timeout on the first attempt should not be treated as "no data."
    sleeps: list[float] = []
    monkeypatch.setattr(vof.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_urlopen(request, timeout, context):
        calls["n"] += 1
        if calls["n"] < 3:
            raise URLError("timed out")
        return "response"

    monkeypatch.setattr(vof, "urlopen", fake_urlopen)

    result = _urlopen_with_retry(vof.Request("https://example.invalid"), timeout=30)

    assert result == "response"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_urlopen_with_retry_does_not_retry_404():
    def fake_urlopen(request, timeout, context):
        raise HTTPError("https://example.invalid", 404, "Not Found", {}, None)

    original = vof.urlopen
    vof.urlopen = fake_urlopen
    try:
        with pytest.raises(HTTPError) as exc_info:
            _urlopen_with_retry(vof.Request("https://example.invalid"), timeout=30)
        assert exc_info.value.code == 404
    finally:
        vof.urlopen = original


def test_urlopen_with_retry_gives_up_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(vof.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_urlopen(request, timeout, context):
        calls["n"] += 1
        raise URLError("timed out")

    monkeypatch.setattr(vof, "urlopen", fake_urlopen)

    with pytest.raises(URLError):
        _urlopen_with_retry(vof.Request("https://example.invalid"), timeout=30)
    assert calls["n"] == len(vof._RETRY_BACKOFF_S) + 1


def test_parse_vehicle_csv_maps_observation_fields():
    observations = parse_vehicle_csv(_CSV, "hailcam", "hailcam")

    assert len(observations) == 2
    assert observations[0].timestamp == _utc(4)
    assert observations[0].vehicle_id == "hailcam"
    assert observations[0].icon_type == "hailcam"
    assert observations[0].temperature_c == 22.82
    assert observations[0].dewpoint_c == 19.5
    assert observations[0].wind_speed_ms == 2.3
    assert observations[0].wind_dir_deg == 215.2
    assert observations[0].pressure_mb == 977.29


def test_parse_vehicle_csv_accepts_mmddyy_when_matching_expected_date():
    # Some FOFS vehicle files log gps_date as MMDDYY instead of the usual
    # DDMMYY (observed for a TORUS 2019 probe on 2019-05-28: "052819").
    # Interpreted as DDMMYY that's an invalid month (28); the row should
    # be recovered by trying MMDDYY once an expected date is supplied.
    csv_text = (
        "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"
        "3.01,173.09,14.68,,895.06,052819,154739,39.3647,-101.043\n"
    )
    observations = parse_vehicle_csv(csv_text, "probe1", expected_date="20190528")

    assert len(observations) == 1
    assert observations[0].timestamp == datetime(2019, 5, 28, 15, 47, 39, tzinfo=timezone.utc)


def test_parse_vehicle_csv_rejects_rows_that_dont_match_expected_date():
    # A daily file's name doesn't guarantee its logged rows are actually
    # from that day (observed for a LIFT 2024 probe file named for
    # 2024-04-27 whose gps_date values were actually in February 2024).
    # With an expected date, a row that parses cleanly but lands on a
    # different day must be dropped rather than silently kept.
    csv_text = (
        "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"
        "0.59,99.3,24.15,19.95,967.97,020724,143848,35.1814,-97.4388\n"
    )
    observations = parse_vehicle_csv(csv_text, "probe1", expected_date="20240427")

    assert observations == []


def test_fetch_vehicle_csv_warns_when_a_real_file_has_no_matching_date_rows(monkeypatch, caplog):
    # A 0-observation result is ambiguous by itself: a genuinely empty file
    # and a real file whose every row is date-mismatched (see the test
    # above) both produce it. The date-mismatch case is worth a WARNING of
    # its own -- otherwise diagnosing it means downloading and inspecting
    # the raw file by hand (as this exact real case, LIFT 2024 probe1 on
    # 2024-04-27, originally required).
    csv_text = (
        "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"
        "0.59,99.3,24.15,19.95,967.97,020724,143848,35.1814,-97.4388\n"
    )

    class _FakeResponse:
        def read(self_inner):
            return csv_text.encode("utf-8")
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *exc):
            return False

    monkeypatch.setattr(vof, "urlopen", lambda request, timeout, context: _FakeResponse())

    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))
    with caplog.at_level("WARNING"):
        observations = fetcher._fetch_vehicle_csv("probe1", None)

    assert observations == []
    assert "none of it matches this date" in caplog.text
    assert "probe1" in caplog.text


def test_fetch_vehicle_csv_does_not_warn_for_a_genuinely_empty_file(monkeypatch, caplog):
    class _FakeResponse:
        def read(self_inner):
            return b""
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *exc):
            return False

    monkeypatch.setattr(vof, "urlopen", lambda request, timeout, context: _FakeResponse())

    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))
    with caplog.at_level("WARNING"):
        observations = fetcher._fetch_vehicle_csv("probe1", None)

    assert observations == []
    assert "none of it matches this date" not in caplog.text


def test_parse_vehicle_netcdf_uses_epochtime_directly(tmp_path):
    epoch0 = datetime(2022, 5, 24, tzinfo=timezone.utc).timestamp()
    path = tmp_path / "probe1.mesonet.20220524.nc"
    _write_processed_netcdf(
        path,
        epochtime=[epoch0, epoch0 + 1],
        lat=[33.8, 33.81],
        lon=[-102.7, -102.71],
        t_fast=[20.5, 20.6],
    )

    observations = parse_vehicle_netcdf(path.read_bytes(), "probe1")

    assert len(observations) == 2
    assert observations[0].timestamp == datetime(2022, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
    assert observations[0].lat == 33.8
    assert observations[0].temperature_c == 20.5


def test_parse_vehicle_netcdf_drops_rows_with_masked_epochtime(tmp_path):
    # Mirrors a real observed case (LIFT 2024 probe1): the processing
    # pipeline flags an entire vehicle-day's time data as unusable by
    # masking epochtime, while lat/lon/temperature remain populated. Those
    # rows must be dropped, not assigned a fabricated timestamp.
    epoch0 = datetime(2024, 4, 27, tzinfo=timezone.utc).timestamp()
    path = tmp_path / "probe1.mesonet.20240427.nc"
    _write_processed_netcdf(
        path,
        epochtime=[np.nan, np.nan, epoch0],
        lat=[35.1, 35.2, 35.3],
        lon=[-97.4, -97.5, -97.6],
    )

    observations = parse_vehicle_netcdf(path.read_bytes(), "probe1")

    assert len(observations) == 1
    assert observations[0].lat == 35.3


def test_fetcher_looks_up_and_emits_latest_observation():
    fetcher = ArchiveVehicleObsFetcher(_utc(0))
    observations = parse_vehicle_csv(_CSV, "hailcam")
    fetcher._observations = {"hailcam": observations}
    fetcher._timestamps = {
        "hailcam": [obs.timestamp for obs in observations]
    }
    fetcher._loaded = True
    emitted = []
    fetcher.observation_ready.connect(emitted.append)

    fetcher.on_time_changed(_utc(4))
    fetcher.on_time_changed(_utc(5))

    assert emitted == observations
    assert fetcher.has_fresh_observation("hailcam", _utc(59)) is True
    assert fetcher.has_fresh_observation("hailcam", _utc(66)) is False
    assert fetcher.history("hailcam", _utc(4)) == observations[:1]


def _obs_at(lat, lon):
    return Observation("dltruck", lat, lon, _utc(0))


def test_dltruck_track_retains_paired_fixes(monkeypatch):
    observations = [_obs_at(35.0, -97.0), _obs_at(35.2, -97.2)]
    requested = []
    def fetch(self, vehicle_id, icon_type):
        requested.append(vehicle_id)
        return observations
    monkeypatch.setattr(ArchiveVehicleObsFetcher, "_fetch_vehicle", fetch)
    assert load_dltruck_track(_utc(0)) == observations
    assert requested == ["dltruck"]


def test_dltruck_track_failure_is_unknown(monkeypatch):
    def fail(*args):
        raise URLError("timed out")
    monkeypatch.setattr(ArchiveVehicleObsFetcher, "_fetch_vehicle", fail)
    assert load_dltruck_track(_utc(0)) == []


def test_position_track_uses_launch_time_and_rejects_stale_invalid_fixes():
    from archive.positions import PositionTrack
    observations = [Observation("dltruck", 36, -98, _utc(120)),
                    Observation("dltruck", 35, -97, _utc(0)),
                    Observation("dltruck", -999, -999, _utc(60))]
    track = PositionTrack(observations)
    assert track.nearest(_utc(119)).lat == 36
    assert track.nearest(_utc(20)).lat == 35
    assert track.nearest(_utc(60)).lat == 35  # ties prefer earlier actual fix
    assert track.nearest(_utc(181)) is None
    assert track.nearest(float("nan")) is None


def test_vehicle_positions_near_picks_nearest_not_first_fix():
    """Same regression this fixes as ArchiveMQTTReader.vehicle_positions_near:
    a deployment can stage from a base far from the actual intercept, so
    picking the day's first fix is a poor proxy for where a vehicle
    actually was at the requested archive start time."""
    fetcher = ArchiveVehicleObsFetcher(_utc(0))
    fetcher._observations = {
        "dltruck": [
            Observation("dltruck", 35.0, -97.0, _utc(0)),      # staged near OKC
            Observation("dltruck", 34.7, -102.8, _utc(3600)),  # drove to near Lubbock
            Observation("dltruck", 34.5, -103.0, _utc(7200)),
        ],
    }

    near_start = fetcher.vehicle_positions_near(_utc(0))
    assert near_start == [("dltruck", 35.0, -97.0)]

    near_lubbock = fetcher.vehicle_positions_near(_utc(3650))
    assert near_lubbock == [("dltruck", 34.7, -102.8)]


def test_vehicle_positions_near_with_no_observations_is_empty():
    fetcher = ArchiveVehicleObsFetcher(_utc(0))
    fetcher._observations = {"dltruck": []}
    assert fetcher.vehicle_positions_near(_utc(0)) == []
