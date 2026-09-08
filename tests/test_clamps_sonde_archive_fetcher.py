"""Tests for CLAMPS mobile-sonde archive discovery (THREDDS-mirrored
.skewT.text files, reusing the live module's own parser)."""

from datetime import datetime, timezone
from urllib.error import HTTPError

from archive.fetchers import clamps_sonde_archive_fetcher as csf

_SAMPLE_SKEWT = """%TITLE%
NSSL_LIDAR 220524/0036
%RAW%
886.0,1074.68,22.0,15.15,120.0,19.44
871.0,1222.53,20.4,14.97,114.47,33.50
856.0,1372.9,18.7,14.64,114.85,35.50
841.0,1524.45,17.5,14.39,126.95,38.61
826.0,1678.38,16.2,14.13,130.54,39.29
%END%
"""

_CATALOG_HTML_TEMPLATE = """
<html><body>
<a href="catalog.html?dataset=FRDD/CLAMPS/dltruck/dltruck1/ingested/dltruckdlsonderawDL1.b1/upperair.NSSL_Lidar_sonde.{f1}.skewT.text">a</a>
<a href="catalog.html?dataset=FRDD/CLAMPS/dltruck/dltruck1/ingested/dltruckdlsonderawDL1.b1/upperair.NSSL_Lidar_sonde.{f2}.skewT.text">a</a>
</body></html>
"""


def test_list_catalog_filenames_extracts_dataset_hrefs(monkeypatch):
    html = _CATALOG_HTML_TEMPLATE.format(f1="202205240036", f2="202205241938")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return html.encode("utf-8")

    monkeypatch.setattr(csf, "_urlopen_with_retry", lambda request, timeout: FakeResponse())

    filenames = csf._list_catalog_filenames("dltruck/dltruck1", "dltruckdlsonderawDL1.b1")

    assert filenames == [
        "upperair.NSSL_Lidar_sonde.202205240036.skewT.text",
        "upperair.NSSL_Lidar_sonde.202205241938.skewT.text",
    ]


def test_list_catalog_filenames_returns_empty_on_404(monkeypatch):
    def raise_404(request, timeout):
        raise HTTPError("https://example.invalid", 404, "Not Found", {}, None)

    monkeypatch.setattr(csf, "_urlopen_with_retry", raise_404)

    assert csf._list_catalog_filenames("dltruck/dltruck1", "dltruckdlsonderawDL1.b1") == []


def test_fetch_clamps_sonde_soundings_filters_by_date_and_parses(monkeypatch):
    # Two launches on the requested date, one on a different date -- only
    # the matching two should be fetched and parsed.
    html = _CATALOG_HTML_TEMPLATE.format(f1="202205240036", f2="202205250000")
    monkeypatch.setattr(csf, "_list_catalog_filenames", lambda platform_dir, datastream: [
        "upperair.NSSL_Lidar_sonde.202205240036.skewT.text",
        "upperair.NSSL_Lidar_sonde.202205250000.skewT.text",
    ])

    fetched_urls = []

    class FakeResponse:
        def __init__(self, text):
            self._text = text

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._text.encode("utf-8")

    def fake_urlopen(request, timeout):
        fetched_urls.append(request.full_url)
        return FakeResponse(_SAMPLE_SKEWT)

    monkeypatch.setattr(csf, "_urlopen_with_retry", fake_urlopen)

    sset = csf.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, tzinfo=timezone.utc))

    assert len(fetched_urls) == 1
    assert "202205240036" in fetched_urls[0]
    assert sset is not None
    assert sset.source == "nssl"
    assert sset.station_id == "CLAMPS"
    assert len(sset.soundings) == 1
    assert sset.soundings[0].valid_time == datetime(2022, 5, 24, 0, 36, tzinfo=timezone.utc)
    assert len(sset.soundings[0].pressure) == 5


def test_fetch_clamps_sonde_soundings_returns_none_when_no_launches_that_day(monkeypatch):
    monkeypatch.setattr(csf, "_list_catalog_filenames", lambda platform_dir, datastream: [
        "upperair.NSSL_Lidar_sonde.202205250000.skewT.text",
    ])

    sset = csf.fetch_clamps_sonde_soundings(datetime(2022, 5, 24, tzinfo=timezone.utc))

    assert sset is None
