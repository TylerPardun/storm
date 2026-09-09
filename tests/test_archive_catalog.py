"""Tests for the live, on-demand availability browsing layer."""

from datetime import date

from archive import catalog as cat


def test_registry_covers_every_family():
    grouped = cat.platforms_by_family()
    assert set(grouped.keys()) == {
        "FOFS Mobile Mesonet", "CLAMPS Winds", "CLAMPS TROPoe",
        "CLAMPS Surface", "CLAMPS Sondes", "PERiLS UAS",
    }
    assert len(grouped["FOFS Mobile Mesonet"]) == 16
    assert len(cat.ALL_PLATFORMS) == sum(len(v) for v in grouped.values())


def test_every_platform_id_is_unique():
    ids = [p.platform_id for p in cat.ALL_PLATFORMS]
    assert len(ids) == len(set(ids))


def test_dates_from_filenames_extracts_and_dedupes():
    filenames = [
        "probe1.mesonet.20220524.nc",
        "clampsdlvadC1.c1.20220524.000000.cdf",  # same date, different file -> dedupes
        "clampsdlvadC1.c1.20220525.000000.cdf",
        "no_date_here.txt",  # no 8-digit run -> ignored
    ]
    assert cat._dates_from_filenames(filenames) == [date(2022, 5, 24), date(2022, 5, 25)]


def test_campaign_for_year_and_years_for_campaign_are_inverses():
    assert cat.campaign_for_year(2022) in ("TORUS", "PERiLS")  # both ran in 2022
    assert 2024 in cat.years_for_campaign("LIFT")
    assert cat.campaign_for_year(2001) is None
    assert cat.years_for_campaign("NotACampaign") == ()


def test_list_fofs_dates_prefers_processed_when_it_has_entries(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        assert "processed" in url  # raw/ must not be queried when processed/ already answered
        return '<a href="catalog.html?dataset=FOFS/Mobile-Mesonet/data/probe1/processed/probe1.mesonet.20220524.nc">'

    monkeypatch.setattr(cat, "_fetch_catalog_html", fake_fetch)

    assert cat._list_fofs_dates("probe1") == [date(2022, 5, 24)]
    assert len(calls) == 1


def test_list_fofs_dates_falls_back_to_raw_when_processed_is_empty(monkeypatch):
    # mg1/mg2/mg3/noxp_scout/probe9 confirmed live (2026-09-08) to have no
    # processed/ directory at all on THREDDS, only raw/ -- without this
    # fallback they silently reported zero dates despite having real
    # archived history (mg1-3/noxp_scout: 2015; probe9: 2009-2010).
    def fake_fetch(url):
        if "processed" in url:
            return ""  # empty catalog -- no processed/ directory
        assert "raw" in url
        return (
            '<a href="http://data.nssl.noaa.gov/thredds/fileServer/'
            'FOFS/Mobile-Mesonet/data/mg1/raw/20150529.txt">'
        )

    monkeypatch.setattr(cat, "_fetch_catalog_html", fake_fetch)

    assert cat._list_fofs_dates("mg1") == [date(2015, 5, 29)]


def test_list_dates_for_platform_dispatches_by_family(monkeypatch):
    calls = []
    monkeypatch.setitem(cat._LIST_FUNCS, "FOFS Mobile Mesonet", lambda key: calls.append(key) or [date(2024, 4, 27)])

    platform = next(p for p in cat.ALL_PLATFORMS if p.family == "FOFS Mobile Mesonet")
    result = cat.list_dates_for_platform(platform)

    assert result == [date(2024, 4, 27)]
    assert calls == [platform.key]


def test_coverage_for_date_checks_each_platform_and_reports_presence(monkeypatch):
    target = date(2022, 5, 24)

    def fake_list_dates(platform):
        return [target] if platform.platform_id == "WIND-CLAMPS1-VAD" else []

    monkeypatch.setattr(cat, "list_dates_for_platform", fake_list_dates)

    subset = [p for p in cat.ALL_PLATFORMS if p.platform_id in ("WIND-CLAMPS1-VAD", "WIND-CLAMPS2-VAD")]
    result = cat.coverage_for_date(target, platforms=subset)

    assert result == {"WIND-CLAMPS1-VAD": True, "WIND-CLAMPS2-VAD": False}


def test_coverage_for_date_does_not_raise_when_a_platform_check_fails(monkeypatch):
    def fake_list_dates(platform):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(cat, "list_dates_for_platform", fake_list_dates)

    subset = [cat.ALL_PLATFORMS[0]]
    result = cat.coverage_for_date(date(2022, 5, 24), platforms=subset)

    assert result == {subset[0].platform_id: False}
