"""Tests for one-second FOFS archive observations."""

from datetime import datetime, timedelta, timezone

from archive.fetchers.vehicle_obs_archive_fetcher import (
    ArchiveVehicleObsFetcher,
    _daily_url,
    parse_vehicle_csv,
)


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
