"""Metadata discovery must distinguish missing files from failed checks."""
from datetime import date, datetime, timezone
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import pytest

from archive import catalog as cat
from archive.fetchers.noxp_archive_fetcher import RadarAsset, RadarInventory


def _page(spec, *names):
    return '<html><title>THREDDS Catalog</title>' + ''.join(
        f"<a href='catalog.html?dataset={quote(spec.path + '/' + name, safe='')}'>file</a>"
        for name in names) + '</html>'


def _fofs():
    return next(p for p in cat.ALL_PLATFORMS if p.family == "FOFS Mobile Mesonet")


def test_registry_unique_and_every_registered_source_has_catalogs():
    assert len({p.platform_id for p in cat.ALL_PLATFORMS}) == len(cat.ALL_PLATFORMS)
    assert len(cat.platforms_by_family()["FOFS Mobile Mesonet"]) == 16
    for platform in cat.ALL_PLATFORMS:
        assert cat.catalogs_for_platform(platform)


def test_dates_ignore_invalid_dates_and_deduplicate():
    assert cat._dates_from_filenames(['x.20240427.nc', '20240427.txt', '20240230.nc', 'readme']) == [date(2024, 4, 27)]


def test_processed_and_raw_dates_are_unioned_with_encoded_and_direct_links():
    p = _fofs()
    processed, raw = cat.catalogs_for_platform(p)
    pages = {processed.url: _page(processed, '20240427.nc'), raw.url:
             f'<a href="/thredds/fileServer/{raw.path}/20250501.txt">raw</a>'}
    result = list(cat.AvailabilityIndex([p], lambda url, _: pages[url], pace=0).scan(Event()))[-1]
    assert result.dates == {date(2024, 4, 27), date(2025, 5, 1)}
    assert result.platforms[p.platform_id].complete


def test_links_restrict_directory_host_and_file_type():
    spec = cat.catalogs_for_platform(_fofs())[0]
    parser = cat._CatalogLinks(spec)
    parser.feed(_page(spec, '20240427.nc', '20240101.txt', '../other/20240102.nc') +
                f'<a href="https://other.test/catalog.html?dataset={spec.path}/20240103.nc">other</a>')
    assert parser.filenames == {'20240427.nc'}


def test_tropoe_accepts_both_netcdf_extensions():
    platform = next(p for p in cat.ALL_PLATFORMS if p.family == "CLAMPS TROPoe")
    spec = cat.catalogs_for_platform(platform)[0]
    parser = cat._CatalogLinks(spec)
    parser.feed(_page(spec, 'x.20240427.nc', 'x.20250501.cdf'))
    assert len(parser.filenames) == 2


def test_failed_catalog_keeps_positive_dates_and_unknown_completeness():
    p = _fofs()
    processed, raw = cat.catalogs_for_platform(p)
    def fetch(url, _):
        if url == raw.url:
            raise TimeoutError('timeout')
        return _page(processed, '20240427.nc')
    result = list(cat.AvailabilityIndex([p], fetch, pace=0).scan(Event()))[-1]
    assert result.dates == {date(2024, 4, 27)}
    assert result.checked == result.total == 2
    assert not result.platforms[p.platform_id].complete
    assert 'timeout' in result.platforms[p.platform_id].errors[0]


def test_session_reuses_completed_and_failed_attempts_until_refresh():
    calls = []
    def fetch(url, _):
        calls.append(url)
        raise TimeoutError('timeout')
    index = cat.AvailabilityIndex([_fofs()], fetch, pace=0)
    list(index.scan(Event()))
    list(index.scan(Event()))
    assert len(calls) == 2
    index.clear()
    list(index.scan(Event()))
    assert len(calls) == 4


def test_cancellation_retains_completed_listing_and_stops_remaining_requests():
    cancel = Event()
    calls = []
    spec = cat.catalogs_for_platform(_fofs())[0]
    def fetch(url, token):
        calls.append(url)
        token.set()
        return _page(spec, '20240427.nc')
    index = cat.AvailabilityIndex([_fofs()], fetch, pace=0)
    with pytest.raises(cat.ScanCancelled):
        list(index.scan(cancel))
    assert calls == [spec.url]
    assert index.snapshot().dates == {date(2024, 4, 27)}
    assert index.snapshot().checked == 1


def test_http_404_is_absent_but_410_remains_unknown(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError('url', 404, 'missing', {}, None)
    monkeypatch.setattr(cat, 'urlopen', missing)
    assert cat._fetch_catalog_html('https://example.test', Event()) == ''
    def unavailable(*args, **kwargs):
        raise HTTPError('url', 410, 'unavailable', {}, None)
    monkeypatch.setattr(cat, 'urlopen', unavailable)
    with pytest.raises(HTTPError):
        cat._fetch_catalog_html('https://example.test', Event())


def test_cancellation_stops_retry_backoff(monkeypatch):
    cancel = Event()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(True)
        cancel.set()
        raise URLError('timeout')
    monkeypatch.setattr(cat, 'urlopen', fetch)
    with pytest.raises(cat.ScanCancelled):
        cat._fetch_catalog_html('https://example.test', cancel)
    assert calls == [True]


def test_non_catalog_response_is_an_error(monkeypatch):
    import io
    monkeypatch.setattr(cat, 'urlopen', lambda *a, **kw: io.BytesIO(b'<html>Login required</html>'))
    with pytest.raises(ValueError, match='THREDDS catalog'):
        cat._fetch_catalog_html('https://example.test', Event())


def test_scan_prioritizes_recent_sources_without_losing_older_dates():
    by_id = {p.platform_id: p for p in cat.ALL_PLATFORMS}
    platforms = [by_id['FOFS-mg1'], by_id['FOFS-farfield'], by_id['FOFS-probe1'], by_id['FOFS-dltruck']]
    calls = []
    def fetch(url, _):
        calls.append(url)
        spec = next(s for p in platforms for s in cat.catalogs_for_platform(p) if s.url == url)
        return _page(spec, '20150501.txt' if '/raw/' in url else '20260501.nc')
    index = cat.AvailabilityIndex(platforms, fetch, pace=0)
    snapshots = list(index.scan(Event()))
    assert [url.split('/data/')[1] for url in calls[:4]] == [
        'probe1/processed/catalog.html', 'dltruck/processed/catalog.html',
        'probe1/raw/catalog.html', 'dltruck/raw/catalog.html',
    ]
    assert '/farfield/' in calls[4]
    assert '/mg1/' in calls[-1]
    assert snapshots[-1].checked == snapshots[-1].total == 8
    assert snapshots[-1].dates == {date(2015, 5, 1), date(2026, 5, 1)}


def test_year_partitioned_coptersonde_catalogs_are_newest_first():
    platform = next(p for p in cat.ALL_PLATFORMS if p.family == 'PERiLS UAS')
    specs = cat.AvailabilityIndex([platform])._scan_order()
    assert [s.year for s in specs] == [2023] * 5 + [2022] * 3


def _noxp_platform(campaign="VORTEX2_2010"):
    return next(p for p in cat.ALL_PLATFORMS if p.platform_id == f"NOXP-{campaign}")


class _FakeNoxp:
    """Stands in for NoxpArchive: records calls, returns a scripted RadarInventory."""
    def __init__(self, inventory):
        self.inventory = inventory
        self.calls = []

    def discover(self, *, cancel, budget, catalog_root):
        self.calls.append(catalog_root)
        return self.inventory


def _asset(name, when):
    return RadarAsset(catalog_url=f'https://example.test/{name}', name=name, format='sigmet', nominal_time=when)


def test_noxp_platforms_use_bounded_recursive_specs_not_the_unbounded_root():
    noxp_platforms = [p for p in cat.ALL_PLATFORMS if p.family == "NOXP Radar"]
    assert len(noxp_platforms) == len(cat._NOXP_CAMPAIGN_ROOTS)
    for platform in noxp_platforms:
        specs = cat.catalogs_for_platform(platform)
        assert len(specs) == 1
        assert isinstance(specs[0], cat.RecursiveCatalogSpec)
        assert specs[0].path != "RRDD/NOXP"
        assert specs[0].path.startswith("RRDD/NOXP/")


def test_noxp_scan_yields_dates_and_is_complete_when_crawl_finishes():
    platform = _noxp_platform()
    inventory = RadarInventory(
        assets=[_asset('NOX100430145529.RAW8K8H', datetime(2010, 4, 30, 14, 55, 29, tzinfo=timezone.utc))],
        pending=0,
    )
    fake = _FakeNoxp(inventory)
    index = cat.AvailabilityIndex([platform], lambda *a: '', pace=0, noxp=fake)
    result = list(index.scan(Event()))[-1]
    assert result.dates == {date(2010, 4, 30)}
    assert result.platforms[platform.platform_id].complete
    assert fake.calls == [cat.catalogs_for_platform(platform)[0].url]


def test_noxp_scan_with_pending_subcatalogs_shows_partial_dates_but_stays_incomplete():
    platform = _noxp_platform()
    inventory = RadarInventory(
        assets=[_asset('NOX100430145529.RAW8K8H', datetime(2010, 4, 30, 14, 55, 29, tzinfo=timezone.utc))],
        pending=94,
    )
    fake = _FakeNoxp(inventory)
    index = cat.AvailabilityIndex([platform], lambda *a: '', pace=0, noxp=fake)
    result = list(index.scan(Event()))[-1]
    # Real dates found so far are never withheld just because the crawl is incomplete.
    assert result.dates == {date(2010, 4, 30)}
    platform_result = result.platforms[platform.platform_id]
    assert not platform_result.complete
    assert '94' in platform_result.errors[0]


def test_noxp_scan_reuses_the_same_noxp_instance_so_refreshes_stay_cheap():
    platform = _noxp_platform()
    fake = _FakeNoxp(RadarInventory(assets=[], pending=0))
    index = cat.AvailabilityIndex([platform], lambda *a: '', pace=0, noxp=fake)
    list(index.scan(Event()))
    index.clear()
    list(index.scan(Event()))
    assert index._noxp is fake
    assert len(fake.calls) == 2  # scanned twice, but always the same underlying NoxpArchive


def test_refresh_updates_recency_hints_from_live_dates_without_skipping_sources():
    by_id = {p.platform_id: p for p in cat.ALL_PLATFORMS}
    platforms = [by_id['FOFS-dltruck'], by_id['FOFS-mg1']]
    def fetch(url, _):
        spec = next(s for p in platforms for s in cat.catalogs_for_platform(p) if s.url == url)
        stamp = '20270501' if '/mg1/' in url else '20260501'
        return _page(spec, stamp + ('.txt' if '/raw/' in url else '.nc'))
    index = cat.AvailabilityIndex(platforms, fetch, pace=0)
    list(index.scan(Event()))
    index.clear()
    assert '/mg1/' in index._scan_order()[0].url
    assert list(index.scan(Event()))[-1].checked == 4
