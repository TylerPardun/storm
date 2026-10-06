"""Tests for the CLAMPS .skewT parser."""

from datetime import datetime, timezone

from data.fetchers.clamps_sounding_fetcher import (
    _FILENAME_RE,
    _api_sonde_entry,
    _format_label,
    _parse_raw_launch_location,
    _parse_skewt,
)


# minimal valid .skewT content
SAMPLE_SKEWT = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
   950.0,  550.0,  20.0,  14.0,  200.0,  8.0
   900.0,  700.0,  16.0,  10.0,  210.0, 10.0
   850.0,  900.0,  12.0,   6.0,  220.0, 12.0
   800.0, 1100.0,   8.0,   2.0,  230.0, 14.0
   700.0, 1500.0,   0.0,  -5.0,  250.0, 18.0
%END%
"""


class TestParseSkewt:
    def test_basic_parse(self):
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(SAMPLE_SKEWT, ft, 0)
        assert snd is not None
        assert len(snd.pressure) == 7
        assert snd.pressure[0] == 1013.0
        assert snd.pressure[-1] == 700.0
        assert snd.temperature[0] == 25.0
        assert snd.dewpoint[0] == 18.0
        assert snd.height[0] == 350.0

    def test_wind_components(self):
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(SAMPLE_SKEWT, ft, 0)
        # first row: wdir=180, wspd=5 kt → convert to m/s before components.
        expected = 5.0 / 1.944
        assert abs(snd.u_wind[0]) < 1e-10
        assert abs(snd.v_wind[0] - expected) < 1e-10

    def test_too_few_levels_returns_none(self):
        short = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        assert _parse_skewt(short, ft, 0) is None

    def test_empty_raw_section(self):
        empty = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        assert _parse_skewt(empty, ft, 0) is None

    def test_bad_lines_skipped(self):
        mixed = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  not,valid,data
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
  short,row
   950.0,  550.0,  20.0,  14.0,  200.0,  8.0
   900.0,  700.0,  16.0,  10.0,  210.0, 10.0
   850.0,  900.0,  12.0,   6.0,  220.0, 12.0
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(mixed, ft, 0)
        assert snd is not None
        assert len(snd.pressure) == 5  # bad lines skipped


class TestFilenameRegex:
    def test_matches_standard(self):
        m = _FILENAME_RE.search("upperair.NSSL_Lidar_sonde.202506061759.skewT")
        assert m is not None
        assert m.group(1) == "202506061759"

    def test_case_insensitive(self):
        m = _FILENAME_RE.search("upperair.NSSL_Lidar_sonde.202506061759.SKEWT")
        assert m is not None

    def test_no_match(self):
        m = _FILENAME_RE.search("random_file.txt")
        assert m is None


class TestApiSondeIndex:
    def test_launch_time_string_entry(self):
        entry = _api_sonde_entry("202604262003")
        assert entry is not None
        assert entry.file_time == datetime(2026, 4, 26, 20, 3, tzinfo=timezone.utc)
        assert entry.skewt_url.endswith(
            "/data/sonde/upperair.NSSL_Lidar_sonde.202604262003.skewT.text"
        )

    def test_rich_entry_accepts_relative_urls(self):
        entry = _api_sonde_entry({
            "valid_time": "2026-04-26T20:03:00Z",
            "skewt_url": "/data/sonde/files/upperair.NSSL_Lidar_sonde.202604262003.skewT",
            "raw_url": "/data/sonde/files/sharppyv2_20260426_2003.text",
        })
        assert entry is not None
        assert entry.file_time == datetime(2026, 4, 26, 20, 3, tzinfo=timezone.utc)
        assert entry.skewt_url.endswith(
            "/data/sonde/files/upperair.NSSL_Lidar_sonde.202604262003.skewT"
        )
        assert entry.raw_url.endswith("/data/sonde/files/sharppyv2_20260426_2003.text")


class TestFormatLabel:
    def test_format(self):
        dt = datetime(2026, 4, 6, 18, 30, tzinfo=timezone.utc)
        assert _format_label(dt) == "1830Z Apr 6"


class TestParseRawLaunchLocation:
    def test_location_header(self):
        text = """\
%TITLE%
NSSL_LIDAR 010101/0000
%LOCATION%
    Release point latitude                       \t35.857?N
    Release point longitude                      \t97.892?W
    Balloon release date and time                \t2026-04-26T20:03:58

%RAW%
963.50, 341.68, 29.61, 18.13, 217.00, 5.60
"""
        assert _parse_raw_launch_location(text) == (35.857, -97.892)

    def test_missing_location(self):
        assert _parse_raw_launch_location("%TITLE%\n%RAW%\n") is None
