"""The audit's checks must fire on the real failure shapes they exist for."""

from datetime import date, datetime, timedelta, timezone

from core.observation import Observation
from scripts.audit_fofs_vehicle_days import FileReport, _session_days, check_day, check_file

_DAY = date(2024, 4, 27)


def _track(n=5, start=(20, 0, 0), lat=35.0, lon=-97.0, **met):
    t0 = datetime(2024, 4, 27, *start, tzinfo=timezone.utc)
    defaults = dict(temperature_c=24.0, dewpoint_c=18.0, wind_speed_ms=5.0,
                    wind_dir_deg=180.0, pressure_mb=967.0)
    defaults.update(met)
    return [Observation("probe1", lat + i * 1e-4, lon, t0 + timedelta(seconds=i), **defaults)
            for i in range(n)]


def _codes(findings):
    return {f.code for f in findings}


def test_clean_track_has_no_findings():
    assert check_day("probe1", _DAY, _track()) == []


def test_jump_to_null_island_is_a_teleport_and_off_map():
    track = _track()
    track.insert(2, Observation("probe1", 0.0, 0.0, track[1].timestamp + timedelta(milliseconds=500)))

    assert {"teleport", "off_map"} <= _codes(check_day("probe1", _DAY, track))


def test_impossible_met_values_are_flagged():
    findings = check_day("probe1", _DAY, _track(wind_speed_ms=130.0, pressure_mb=18.3))

    flagged = {f.detail.split(":")[0] for f in findings if f.code == "met_range"}
    assert flagged == {"wind_speed_ms", "pressure_mb"}


def test_frequent_strong_winds_are_flagged_for_review_not_as_errors():
    findings = check_day("probe1", _DAY, _track(wind_speed_ms=76.0))

    assert _codes(findings) == {"wind_review"}


def test_missing_value_markers_are_flagged_separately():
    findings = check_day("probe1", _DAY, _track(dewpoint_c=-999.0))

    assert _codes(findings) == {"met_sentinel"}


def test_calm_wind_code_is_not_a_range_error():
    assert check_day("probe1", _DAY, _track(wind_dir_deg=999.0)) == []


def test_midnight_hole_and_long_gap_are_reported():
    early = _track(n=2, start=(0, 10, 0))
    late = _track(n=2, start=(3, 0, 0))

    assert _codes(check_day("probe1", _DAY, early + late)) == {"midnight_hole", "long_gap"}


def test_file_findings_describe_where_the_data_went():
    relocated = FileReport("probe1", "20240605", 100, 100, "DDMMYY", "2024-05-06", ["2024-05-06", "2024-05-07"], 100, 100)
    undatable = FileReport("probe1", "20170511", 100, 0, None, None, [], 0, 0)
    garbled = FileReport("probe1", "20240427", 100, 100, "THREDDS-garbled", "2024-04-27", ["2024-04-27"], 100, 100)
    copy = FileReport("probe1", "20230106", 100, 100, "MMDDYY", "2023-01-06", ["2023-01-06"], 100, 100)
    # Collapsed duplicate seconds and dropped placeholder positions aren't "undated".
    deduplicated = FileReport("probe1", "20100524", 100, 60, "DDMMYY", "2010-05-24", ["2010-05-24"], 99, 100)
    partly = FileReport("probe2", "20100503", 100, 62, "DDMMYY", "2010-05-03", ["2010-05-03"], 62, 100)

    assert _codes(check_file(relocated, None)) == {"relocated"}
    assert _codes(check_file(undatable, None)) == {"undatable"}
    assert _codes(check_file(garbled, None)) == {"garbled"}
    assert _codes(check_file(copy, "20230601")) == {"misfiled_copy"}
    assert _codes(check_file(deduplicated, None)) == set()
    assert _codes(check_file(partly, None)) == {"partly_dated"}


def test_session_days_follow_the_loaders_candidate_rule():
    assert _session_days([date(2024, 6, 5)]) == {
        date(2024, 6, 4), date(2024, 6, 5), date(2024, 6, 6),
        date(2024, 5, 5), date(2024, 5, 6), date(2024, 5, 7),
    }
    # probe4's raw/20100611.txt also feeds 8 June through a known correction.
    assert date(2010, 6, 8) in _session_days([date(2010, 6, 11)], "probe4")
