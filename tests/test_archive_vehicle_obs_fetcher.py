"""Tests for one-second FOFS archive observations."""

from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest

from archive.fetchers import vehicle_obs_archive_fetcher as vof
from archive.fetchers.vehicle_obs_archive_fetcher import (
    ArchiveVehicleObsFetcher,
    _daily_url,
    _urlopen_with_retry,
    _apply_known_corrections,
    _candidate_file_dates,
    _drop_excursions,
    _drop_stale_fixes,
    _thredds_garbled_gps_date,
    load_dltruck_track,
    parse_vehicle_csv,
)
from core.observation import Observation


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


_HEADER = "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"


def _csv(*rows: tuple[str, str]) -> str:
    return _HEADER + "".join(
        f"1.0,180.0,20.0,15.0,970.0,{gps_date},{gps_time},35.0,-97.0\n"
        for gps_date, gps_time in rows
    )


def _times(observations) -> list[datetime]:
    return [obs.timestamp for obs in observations]


def _at(y, mo, d, h, mi, s) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def test_parse_vehicle_csv_maps_observation_fields():
    observations = parse_vehicle_csv(_CSV, "hailcam", "hailcam", file_date=date(2026, 4, 16))

    assert len(observations) == 2
    assert observations[0].timestamp == _utc(4)
    assert observations[0].vehicle_id == "hailcam"
    assert observations[0].icon_type == "hailcam"
    assert observations[0].temperature_c == 22.82
    assert observations[0].dewpoint_c == 19.5
    assert observations[0].wind_speed_ms == 2.3
    assert observations[0].wind_dir_deg == 215.2
    assert observations[0].pressure_mb == 977.29


# Every distinct (true day -> published gps_date) pair seen when each
# locally held raw logger file (LIFT 2024 Apr-Jun, 2017) was matched row by
# row against its published THREDDS CSV. The logger files themselves had
# the true DDMMYY date on every row.
_OBSERVED_THREDDS_GARBLING = {
    date(2024, 4, 15): "010524", date(2024, 4, 16): "010624",
    date(2024, 4, 25): "020524", date(2024, 4, 26): "020624",
    date(2024, 4, 27): "020724", date(2024, 4, 28): "020824",
    date(2024, 4, 30): "020404", date(2024, 5, 1): "100524",
    date(2024, 5, 2): "020524", date(2024, 5, 6): "060524",
    date(2024, 5, 7): "070524", date(2024, 5, 19): "010924",
    date(2024, 5, 20): "020405", date(2024, 5, 23): "020324",
    date(2024, 5, 24): "020424", date(2024, 5, 25): "020524",
    date(2024, 5, 26): "020624", date(2024, 5, 30): "020405",
    date(2024, 5, 31): "030124", date(2024, 6, 3): "030624",
    date(2024, 6, 4): "040624", date(2024, 6, 5): "050624",
    date(2024, 6, 15): "010524", date(2024, 6, 16): "010624",
    date(2024, 6, 17): "010724", date(2024, 6, 18): "010824",
    date(2017, 2, 13): "010317", date(2017, 3, 28): "020817",
    date(2017, 5, 18): "010817", date(2017, 5, 22): "020217",
    date(2017, 5, 23): "020317", date(2017, 5, 25): "020517",
    date(2017, 5, 26): "020617", date(2017, 5, 27): "020717",
    date(2017, 6, 13): "010317", date(2017, 6, 14): "010417",
}


@pytest.mark.parametrize("day,published", sorted(_OBSERVED_THREDDS_GARBLING.items()))
def test_thredds_garbled_gps_date_reproduces_every_observed_pair(day, published):
    assert _thredds_garbled_gps_date(day) == published


def test_garbled_file_is_dated_from_its_name_across_midnight():
    # probe1 raw/20240427.txt as published: 27 Apr rows read "020724",
    # rows after 00 UTC (28 Apr) read "020824", and 00:00:00-00:09:59 is
    # missing. The time of day is exact (checked against dltruck's track).
    text = _csv(("020724", "143848"), ("020724", "235959"), ("020824", "001000"))

    observations = parse_vehicle_csv(text, "probe1", file_date=date(2024, 4, 27))

    assert _times(observations) == [
        _at(2024, 4, 27, 14, 38, 48), _at(2024, 4, 27, 23, 59, 59), _at(2024, 4, 28, 0, 10, 0),
    ]


def test_garbled_day_one_is_not_mistaken_for_day_ten():
    # probe1 raw/20240430.txt: 30 Apr rows "020404", then 1 May rows
    # "100524" -- which read as a valid DDMMYY 10 May if taken on their own.
    text = _csv(("020404", "194849"), ("100524", "013000"))

    observations = parse_vehicle_csv(text, "probe1", file_date=date(2024, 4, 30))

    assert _times(observations) == [_at(2024, 4, 30, 19, 48, 49), _at(2024, 5, 1, 1, 30, 0)]


def test_swapped_file_name_is_dated_by_its_rows():
    # 2024-05-06's data was published as raw/20240605.txt; its rows are
    # still correct DDMMYY for 6 and 7 May.
    text = _csv(("060524", "165559"), ("060524", "235959"), ("070524", "001000"))

    observations = parse_vehicle_csv(text, "probe1", file_date=date(2024, 6, 5))

    assert _times(observations) == [
        _at(2024, 5, 6, 16, 55, 59), _at(2024, 5, 6, 23, 59, 59), _at(2024, 5, 7, 0, 10, 0),
    ]


def test_mmddyy_era_file_still_parses():
    # probe1 logged MMDDYY during TORUS 2019 ("052819" = 28 May 2019).
    observations = parse_vehicle_csv(_csv(("052819", "154739")), "probe1", file_date=date(2019, 5, 28))

    assert _times(observations) == [_at(2019, 5, 28, 15, 47, 39)]


def test_republished_correct_file_parses_including_unpadded_fields():
    # What a fixed THREDDS file should look like, including the rows the
    # current conversion drops and the logger's unpadded numbers.
    text = _csv(("60524", "235959"), ("70524", "0"), ("70524", "959"), ("070524", "001000"))

    observations = parse_vehicle_csv(text, "probe1", file_date=date(2024, 5, 6))

    assert _times(observations) == [
        _at(2024, 5, 6, 23, 59, 59), _at(2024, 5, 7, 0, 0, 0),
        _at(2024, 5, 7, 0, 9, 59), _at(2024, 5, 7, 0, 10, 0),
    ]


def test_rows_contradicting_the_file_dating_are_dropped():
    # probe2's 25 Apr 2024 logger file ends with no-fix rows still carrying
    # the receiver's default date (21 Aug 1999).
    text = _csv(("250424", "175430"), ("250424", "175431"), ("210899", "235956"))

    observations = parse_vehicle_csv(text, "probe2", file_date=date(2024, 4, 25))

    assert _times(observations) == [_at(2024, 4, 25, 17, 54, 30), _at(2024, 4, 25, 17, 54, 31)]


def test_no_fix_rows_do_not_fake_a_rollover_or_a_position():
    # probe1 raw/20110828.txt: correct "280811" dates throughout, with
    # no-fix rows at 000000 and (0, 0) mixed into the afternoon.
    text = _HEADER + (
        "1.0,180.0,20.0,15.0,970.0,280811,155311,34.9213,-78.0211\n"
        "1.0,180.0,20.0,15.0,970.0,280811,000000,0.0,0.0\n"
        "1.0,180.0,20.0,15.0,970.0,280811,155312,34.9214,-78.0212\n"
        "1.0,180.0,20.0,15.0,970.0,280811,225045,33.5240,-82.0352\n"
    )

    observations = parse_vehicle_csv(text, "probe1", file_date=date(2011, 8, 28))

    assert _times(observations) == [
        _at(2011, 8, 28, 15, 53, 11), _at(2011, 8, 28, 15, 53, 12), _at(2011, 8, 28, 22, 50, 45),
    ]


def test_nan_positions_are_dropped():
    # dltruck raw/20260306.txt has over a thousand rows with lat/lon "nan".
    text = _HEADER + (
        "1.0,180.0,20.0,15.0,970.0,060326,172755,35.1000,-97.4000\n"
        "1.0,180.0,20.0,15.0,970.0,060326,172756,nan,nan\n"
    )

    observations = parse_vehicle_csv(text, "dltruck", file_date=date(2026, 3, 6))

    assert _times(observations) == [_at(2026, 3, 6, 17, 27, 55)]


def test_logger_missing_value_markers_are_not_readings():
    # probe1 raw/20090512.txt: dropped sensors logged as -999, wind as 999.
    text = (
        "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"
        "999.00,999.00,-999.00,-999.00,-999.00,120509,001652,35.5152,-98.9727\n"
        "5.20,290.22,24.10,18.00,999.00,120509,001653,35.5152,-98.9727\n"
    )

    missing, ok = parse_vehicle_csv(text, "probe1", file_date=date(2009, 5, 12))

    assert (missing.wind_speed_ms, missing.wind_dir_deg, missing.temperature_c,
            missing.dewpoint_c, missing.pressure_mb) == (None, None, None, None, None)
    assert (ok.wind_speed_ms, ok.wind_dir_deg, ok.temperature_c, ok.pressure_mb) == (5.2, 290.22, 24.1, 999.0)


_FULL_HEADER = "sfc_wspd,sfc_wdir,t_slow,rh_slow,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"


def _full_row(gps_time, *, wspd=5.0, wdir=180.0, t_slow=25.0, rh=60.0, t_fast=25.0, td=16.0,
              p=960.0, lat=35.0, lon=-97.0, gps_date="270424"):
    return f"{wspd},{wdir},{t_slow},{rh},{t_fast},{td},{p},{gps_date},{gps_time},{lat},{lon}\n"


def _parse_full(*rows, file_date=date(2024, 4, 27)):
    return parse_vehicle_csv(_FULL_HEADER + "".join(rows), "probe1", file_date=file_date)


def test_extreme_but_physical_winds_are_kept():
    # Winds over 50 m/s have been measured near tornadoes; only values no
    # anemometer reading could produce are dropped.
    kept, dropped = _parse_full(_full_row("200000", wspd=76.2), _full_row("200001", wspd=130.0))

    assert (kept.wind_speed_ms, dropped.wind_speed_ms) == (76.2, None)


def test_wind_direction_just_past_north_is_wrapped_not_dropped():
    # probe1 2010 logs a few directions like -2.03 or 361.5.
    below, above = _parse_full(_full_row("200000", wdir=-2.03), _full_row("200001", wdir=361.5))

    assert (round(below.wind_dir_deg, 2), round(above.wind_dir_deg, 2)) == (357.97, 1.5)


def test_failed_thermistor_readings_are_dropped():
    # probe5 2008: -273.1 all day; probe2 3 Jun 2024: -88 to -113 for hours.
    dead, cold, ok = _parse_full(_full_row("200000", t_fast=-273.1), _full_row("200001", t_fast=-88.0),
                                 _full_row("200002", t_fast=24.3))

    assert (dead.temperature_c, cold.temperature_c, ok.temperature_c) == (None, None, 24.3)


def test_humidity_filed_as_dewpoint_is_not_used_at_all():
    # probe1 Aug 2020-2021: the "dewpoint" column repeats rh_slow, so even
    # values that look like plausible dewpoints (35.2 at 36.5 C) are humidity.
    rows = [_full_row(f"20000{i}", t_slow=36.5, rh=rh, t_fast=36.6, td=rh + 0.2)
            for i, rh in enumerate((35.0, 41.6, 42.3, 38.5, 36.6))]

    assert [obs.dewpoint_c for obs in _parse_full(*rows)] == [None] * 5


def test_dewpoint_above_temperature_is_dropped_row_by_row():
    ok, impossible = _parse_full(_full_row("200000", t_slow=25.0, t_fast=25.1, td=18.0),
                                 _full_row("200001", t_slow=25.0, t_fast=25.1, td=31.0))

    assert (ok.dewpoint_c, impossible.dewpoint_c) == (18.0, None)


def test_placeholder_positions_are_dropped():
    # probe9 2010: stray fixes at (0.130, 0.000), (0.117, -1.070), (96.166, -1.128).
    observations = _parse_full(_full_row("200000"), _full_row("200001", lat=0.130, lon=0.0),
                               _full_row("200002", lat=0.117, lon=-1.070), _full_row("200003", lat=96.166, lon=-1.128))

    assert _times(observations) == [_at(2024, 4, 27, 20, 0, 0)]


def test_interleaved_second_track_is_dropped_in_favor_of_the_continuous_one():
    # probe1 raw/20100524.txt: from 23:34:05 every second has two rows, one
    # continuing the file's own track (41.13 N) and one ~300 km away that
    # continues the previous day's last fix (38.48 N).
    own = [(f"2334{s:02d}", 41.1270 - s * 1e-4, -100.672) for s in range(0, 8)]
    stray = [(f"2334{s:02d}", 38.4822, -100.955 - s * 4e-4) for s in range(5, 8)]
    rows = sorted(own + stray, key=lambda r: r[0])

    observations = _parse_full(*(_full_row(t, lat=la, lon=lo, gps_date="240510") for t, la, lo in rows),
                               file_date=date(2010, 5, 24))

    assert len(observations) == 8
    assert all(obs.lat > 41 for obs in observations)


def test_interleaved_tracks_from_the_first_row_still_follow_the_real_track():
    rows = [_full_row("200000", lat=38.48, gps_date="240510"), _full_row("200000", lat=41.13, gps_date="240510"),
            _full_row("200001", lat=41.1301, gps_date="240510"), _full_row("200002", lat=41.1302, gps_date="240510")]

    observations = _parse_full(*rows, file_date=date(2010, 5, 24))

    assert [round(obs.lat, 2) for obs in observations] == [41.13, 41.13, 41.13]


def test_file_with_no_datable_rows_yields_nothing():
    # probe1 raw/20170511.txt is entirely no-fix rows dated Feb 2017.
    text = _csv(("080217", "041651"), ("090217", "041652"), ("100217", "041653"))

    assert parse_vehicle_csv(text, "probe1", file_date=date(2017, 5, 11)) == []


def test_candidate_files_cover_neighboring_days_and_swapped_names():
    assert _candidate_file_dates(date(2024, 4, 27)) == [date(2024, 4, 26), date(2024, 4, 27), date(2024, 4, 28)]
    assert _candidate_file_dates(date(2024, 5, 6)) == [
        date(2024, 5, 5), date(2024, 5, 6), date(2024, 6, 5), date(2024, 5, 7), date(2024, 7, 5),
    ]


def test_candidate_files_include_sources_of_known_corrections():
    # probe4's raw/20100611.txt holds a stretch of 8 June (shift -3).
    assert date(2010, 6, 11) in _candidate_file_dates(date(2010, 6, 8), "probe4")
    assert date(2010, 6, 11) not in _candidate_file_dates(date(2010, 6, 8), "probe1")


def _obs(lat, lon, hh, mm, ss, day=date(2010, 5, 24)):
    return Observation("probe1", lat, lon, datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=timezone.utc))


def test_known_correction_moves_only_the_listed_track_through_interleaved_rows(monkeypatch):
    # Two tracks share every second; the listed one (starting 38.4822 N at
    # 23:34:05) belongs to the previous day.
    monkeypatch.setattr(vof, "_CORRECTIONS", {("probe1", date(2010, 5, 24)): [vof._Correction(
        datetime(2010, 5, 24, 23, 34, 5, tzinfo=timezone.utc),
        datetime(2010, 5, 24, 23, 34, 8, tzinfo=timezone.utc), 38.4822, -100.9551, -1)]})
    rows = []
    for s in range(5, 9):
        rows += [_obs(41.127 - s * 1e-4, -100.672, 23, 34, s), _obs(38.4822, -100.9551 - s * 4e-4, 23, 34, s)]

    corrected = _apply_known_corrections("probe1", date(2010, 5, 24), rows)

    moved = [o for o in corrected if o.timestamp.day == 23]
    stayed = [o for o in corrected if o.timestamp.day == 24]
    assert len(moved) == len(stayed) == 4
    assert all(o.lat < 39 for o in moved) and all(o.lat > 41 for o in stayed)


def test_known_correction_can_drop_a_stretch(monkeypatch):
    monkeypatch.setattr(vof, "_CORRECTIONS", {("probe9", date(2010, 6, 9)): [vof._Correction(
        datetime(2010, 6, 9, 18, 3, 17, tzinfo=timezone.utc),
        datetime(2010, 6, 9, 18, 3, 18, tzinfo=timezone.utc), 40.015, -105.135, None)]})
    day = date(2010, 6, 9)
    rows = [_obs(40.015, -105.135, 18, 3, 17, day), _obs(40.015, -105.135, 18, 3, 18, day),
            _obs(40.406, -104.993, 18, 4, 0, day)]

    assert [o.lat for o in _apply_known_corrections("probe9", day, rows)] == [40.406]


def test_short_out_and_back_glitch_is_dropped():
    # probe7 2010-05-14: 59 km away at 16:51:26, back 16 s later.
    track = [_obs(31.727, -102.616 + s * 1e-4, 16, 51, s) for s in range(20, 26)]
    glitch = [_obs(31.991, -102.078, 16, 51, s) for s in range(26, 41)]
    back = [_obs(31.727, -102.6134 + s * 1e-4, 16, 51, s) for s in range(42, 50)]

    kept, dropped = _drop_excursions(track + glitch + back)

    assert dropped == len(glitch)
    assert all(o.lat < 31.8 for o in kept)


def test_frozen_fix_beside_a_jump_to_a_moving_track_is_dropped():
    # probe2 2017-06-11: 25 s parked at one spot, then 26 km away and
    # immediately driving at 35 m/s -- the receiver was repeating its last fix.
    day = date(2017, 6, 11)
    frozen = [_obs(41.109, -100.766, 15, 4, s, day) for s in range(23, 49)]
    driving = [_obs(41.141, -101.077 - s * 3e-4, 15, 5, s, day) for s in range(0, 60)]

    kept, dropped = _drop_stale_fixes(frozen + driving)

    assert dropped == len(frozen)
    assert kept == driving


def test_genuinely_parked_vehicle_beside_a_jump_is_kept():
    # Parked on both sides (probe9 2010-04-30): no side is driving, so
    # nothing is known to be stale.
    day = date(2010, 4, 30)
    rows = ([_obs(35.189, -97.374, 20, 38, s, day) for s in range(0, 49)]
            + [_obs(35.182, -97.439, 20, 38, s, day) for s in range(49, 60)])

    assert _drop_stale_fixes(rows) == (rows, 0)


def test_correction_follows_the_track_ending_at_the_known_spot_through_a_shared_parking_spot(monkeypatch):
    # probe5 raw/20100611.txt: 8 June's stretch and 11 June's own track sit
    # at the same spot at Limon; 11 June's leaves first.
    limon = (39.271, -103.707)
    monkeypatch.setattr(vof, "_CORRECTIONS", {("probe5", date(2010, 6, 11)): [vof._Correction(
        datetime(2010, 6, 11, 21, 0, 0, tzinfo=timezone.utc),
        datetime(2010, 6, 11, 21, 0, 5, tzinfo=timezone.utc), *limon, -3, *limon)]})
    day = date(2010, 6, 11)
    rows = []
    for s in range(0, 6):
        rows.append(_obs(*limon, 21, 0, s, day))                                   # stays parked
        rows.append(_obs(limon[0] + s * 2e-4, limon[1] - s * 2e-4, 21, 0, s, day))  # drives off

    corrected = _apply_known_corrections("probe5", day, rows)

    moved = [o for o in corrected if o.timestamp.day == 8]
    assert len(moved) == 6 and all((o.lat, o.lon) == limon for o in moved)


def test_correction_keeps_its_stretch_when_tracks_share_a_parking_spot_metres_apart(monkeypatch):
    # probe4 raw/20100611.txt: 8 June's stretch drives to Limon and parks
    # ~20 m from where 11 June's own track is parked; 11 June's leaves first.
    # The stretch must stay on the copy that drove in and is still there at
    # its known end.
    day = date(2010, 6, 11)
    monkeypatch.setattr(vof, "_CORRECTIONS", {("probe4", day): [vof._Correction(
        datetime(2010, 6, 11, 21, 0, 0, tzinfo=timezone.utc),
        datetime(2010, 6, 11, 21, 0, 9, tzinfo=timezone.utc), 39.2750, -103.7073, -3, 39.2711, -103.7074)]})
    rows = []
    for s in range(10):
        arriving = (max(39.2711, 39.2750 - s * 1e-3), -103.7074)          # drives in, then parks
        own = (39.2709 + (s >= 7) * (s - 6) * 2e-3, -103.7073)             # parked, leaves at s=7
        rows += [_obs(*arriving, 21, 0, s, day), _obs(*own, 21, 0, s, day)]

    corrected = _apply_known_corrections("probe4", day, rows)

    moved = sorted((o for o in corrected if o.timestamp.day == 8), key=lambda o: o.timestamp)
    assert len(moved) == 10
    assert round(moved[-1].lat, 4) == 39.2711


def _serve(monkeypatch, files: dict[str, str]):
    """Point the fetcher's HTTP layer at in-memory raw/<name>.txt files."""
    requested = []

    class _Response:
        def __init__(self, body):
            self._body = body
        def read(self):
            return self._body.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout, context):
        name = request.full_url.rsplit("/", 1)[-1]
        requested.append(name)
        if name not in files:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return _Response(files[name])

    monkeypatch.setattr(vof, "urlopen", fake_urlopen)
    return requested


def test_fetch_vehicle_collects_session_day_from_every_candidate_file(monkeypatch):
    # Session 2024-05-07: the probe's 6 May local-day file (published under
    # the swapped name 20240605) runs past 00 UTC into 7 May, and the same
    # rows also exist under a correct name -- kept once.
    swapped = _csv(("060524", "235959"), ("070524", "001000"), ("070524", "001001"))
    requested = _serve(monkeypatch, {
        "20240605.txt": swapped,
        "20240506.txt": _csv(("060524", "235959"), ("070524", "001000")),
    })
    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 5, 7, tzinfo=timezone.utc))

    observations = fetcher._fetch_vehicle("probe1", None)

    assert _times(observations) == [_at(2024, 5, 7, 0, 10, 0), _at(2024, 5, 7, 0, 10, 1)]
    assert {"20240506.txt", "20240605.txt", "20240507.txt", "20240705.txt"} <= set(requested)


def test_fetch_vehicle_runs_into_the_next_morning_up_to_the_cap(monkeypatch):
    # A session covers its UTC day and the next morning to 06Z
    # (archive/session.py): the evening drive after 00Z belongs to it.
    _serve(monkeypatch, {"20240427.txt": _csv(("270424", "235959"), ("280424", "013000"), ("280424", "063000"))})
    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))

    observations = fetcher._fetch_vehicle("probe1", None)

    assert _times(observations) == [_at(2024, 4, 27, 23, 59, 59), _at(2024, 4, 28, 1, 30, 0)]


def test_fetch_vehicle_ignores_a_swapped_file_holding_another_day(monkeypatch):
    # Session 2024-06-05 must not show 6 May's data just because it was
    # published as raw/20240605.txt.
    _serve(monkeypatch, {"20240605.txt": _csv(("060524", "165559"), ("070524", "001000"))})
    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 6, 5, tzinfo=timezone.utc))

    assert fetcher._fetch_vehicle("probe1", None) == []


def _qc_met_csv(gps_dates_and_times) -> str:
    """Rows at a fixed track position per second, as a QC_met-era file."""
    return _HEADER + "".join(
        f"1.0,180.0,20.0,15.0,970.0,{d},{t},{33.0 + i / 1e4:.4f},-102.0\n"
        for i, (d, t) in enumerate(gps_dates_and_times)
    )


def test_fetch_vehicle_skips_mmddyy_copy_filed_under_a_misread_date(monkeypatch):
    # 2023: the logger file P1_06012023 has no date column; the conversion
    # read its name as 6 Jan and published raw/20230106.txt with MMDDYY
    # dates for 6-7 Jan, while raw/20230601.txt has the same fixes with
    # GPS dates for 1 Jun. Session 2023-01-06 must show nothing.
    # The two copies also differ in position precision (33.416 vs 33.41601).
    times = ["155609", "155610", "155611"]
    misfiled = _qc_met_csv([("010623", t) for t in times]).replace("33.0000", "33.000").replace("33.0001", "33.000")
    _serve(monkeypatch, {
        "20230106.txt": misfiled,
        "20230601.txt": _qc_met_csv([("010623", t) for t in times]),
    })
    fetcher = ArchiveVehicleObsFetcher(datetime(2023, 1, 6, tzinfo=timezone.utc))

    assert fetcher._fetch_vehicle("probe1", None) == []


def test_fetch_vehicle_keeps_mmddyy_file_without_a_swapped_twin(monkeypatch):
    # probe1's TORUS 2019 files are MMDDYY and correctly named (e.g.
    # raw/20190601.txt); no raw/20190106.txt exists to contradict them.
    _serve(monkeypatch, {"20190601.txt": _qc_met_csv([("060119", "160841"), ("060119", "160842")])})
    fetcher = ArchiveVehicleObsFetcher(datetime(2019, 6, 1, tzinfo=timezone.utc))

    observations = fetcher._fetch_vehicle("probe1", None)

    assert _times(observations) == [_at(2019, 6, 1, 16, 8, 41), _at(2019, 6, 1, 16, 8, 42)]


def _index(monkeypatch, paths: list[str]):
    from archive import fofs_index
    files = [fofs_index.FofsFile(fofs_index.vehicle_for_path(p), fofs_index._date_from_name(p.rsplit("/", 1)[-1]), p, "")
             for p in paths]
    index = fofs_index.FofsIndex(files, 0.0)
    monkeypatch.setattr(fofs_index, "get_index", lambda *a, **k: index)
    return index


def test_files_are_found_wherever_the_catalog_lists_them(monkeypatch):
    # The tree gets reorganized; the loader follows the catalog, not a
    # hard-coded path.
    _index(monkeypatch, ["FOFS/Mobile-Mesonet/data/probe1/qc_v2/20240427.txt"])
    requested = []

    class _Response:
        def read(self):
            return _csv(("270424", "200000")).encode()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout, context):
        requested.append(request.full_url)
        return _Response()

    monkeypatch.setattr(vof, "urlopen", fake_urlopen)
    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))

    observations = fetcher._fetch_vehicle("probe1", None)

    assert _times(observations) == [_at(2024, 4, 27, 20, 0, 0)]
    assert requested == ["https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/data/probe1/qc_v2/20240427.txt"]


def test_legacy_originals_are_skipped_when_a_published_copy_exists(monkeypatch):
    _index(monkeypatch, ["FOFS/Mobile-Mesonet/data/_legacy_data/probe1/20230601.txt",
                         "FOFS/Mobile-Mesonet/data/probe1/raw/20230601.txt"])
    assert vof._file_urls("probe1", date(2023, 6, 1)) == [
        "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/data/probe1/raw/20230601.txt"]


def test_files_not_in_the_published_format_are_ignored():
    legacy = "Record Number,Date Time,Derived WS,GPSDate,GPSTime\n5797,2023-06-01 00:00:01.0,10.55,010623,000000\n"
    assert not vof._is_published_format(legacy)
    assert vof._is_published_format(_csv(("010623", "000000")))


def test_available_vehicles_come_from_the_catalog(monkeypatch):
    _index(monkeypatch, ["FOFS/Mobile-Mesonet/data/dltruck/raw/20250605.txt",
                         "FOFS/Mobile-Mesonet/data/newprobe/raw/20250606.txt",
                         "FOFS/Mobile-Mesonet/data/probe1/raw/20250610.txt"])
    assert vof.available_vehicles(datetime(2025, 6, 5, tzinfo=timezone.utc)) == {"dltruck", "newprobe"}


def test_fetch_vehicle_file_warns_when_a_real_file_has_no_datable_rows(monkeypatch, caplog):
    # A 0-observation result is ambiguous by itself: a genuinely empty file
    # and a real file none of whose rows can be dated both produce it.
    _serve(monkeypatch, {"20170511.txt": _csv(("080217", "041651"), ("090217", "041652"))})
    fetcher = ArchiveVehicleObsFetcher(datetime(2017, 5, 11, tzinfo=timezone.utc))

    with caplog.at_level("WARNING"):
        observations, _ = fetcher._fetch_vehicle_file("probe1", None, date(2017, 5, 11))

    assert observations == []
    assert "no rows could be dated" in caplog.text
    assert "probe1" in caplog.text


def test_fetch_vehicle_file_does_not_warn_for_a_genuinely_empty_file(monkeypatch, caplog):
    _serve(monkeypatch, {"20240427.txt": ""})
    fetcher = ArchiveVehicleObsFetcher(datetime(2024, 4, 27, tzinfo=timezone.utc))

    with caplog.at_level("WARNING"):
        observations, _ = fetcher._fetch_vehicle_file("probe1", None, date(2024, 4, 27))

    assert observations == []
    assert "no rows could be dated" not in caplog.text


def test_fetcher_looks_up_and_emits_latest_observation():
    fetcher = ArchiveVehicleObsFetcher(_utc(0))
    observations = parse_vehicle_csv(_CSV, "hailcam", file_date=date(2026, 4, 16))
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
