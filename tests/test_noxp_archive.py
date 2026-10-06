from datetime import date, datetime, timezone
from threading import Event

import numpy as np
import pytest
import xarray as xr

from archive.catalog import ScanCanceled
from archive.fetchers.noxp_archive_fetcher import (
    ROOT,
    DATA_ROOT,
    NoxpArchive,
    asset_from_url,
    read_noxp,
    _DISCOVERY_CACHE_TTL_SECONDS,
)


def test_inventory_unions_mirrors_and_uses_file_dates_with_resumable_budget(tmp_path):
    root = ROOT + 'catalog.html'
    flat, campaign = ROOT + 'Netcdf/catalog.html', ROOT + 'Vortex/catalog.html'
    # Same filename on two mirrors, and a next-day file under an older folder.
    name = '20090606-001200.netcdf'
    flat_file = flat + '?dataset=NSSL/NOXP/Netcdf/' + name
    other_file = campaign + '?dataset=NSSL/NOXP/Vortex/' + name
    pages = {root: '<a href="Netcdf/catalog.html">a</a><a href="Vortex/catalog.html">b</a>',
             flat: f'<a href="{flat_file}">f</a>', campaign: f'<a href="{other_file}">f</a>'}
    fetched = []
    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]
    adapter = NoxpArchive(tmp_path, fetch)
    partial = adapter.discover(date(2009, 6, 6), budget=1)
    assert partial.pending == 2 and not partial.complete
    full = adapter.discover(date(2009, 6, 6), budget=2)
    assert full.complete and {a.catalog_url for a in full.assets} == {flat_file, other_file}
    assert fetched.count(root) == 1
    assert not adapter.discover(date(2009, 6, 5)).assets


def test_inventory_keeps_failures_unknown_and_cancellation_propagates(tmp_path):
    def fetch(url, cancel):
        raise TimeoutError('catalog timed out')
    adapter = NoxpArchive(tmp_path, fetch)
    result = adapter.discover()
    assert not result.complete and 'timed out' in result.errors[0]
    cancel = Event()
    cancel.set()
    with pytest.raises(ScanCanceled):
        adapter.discover(cancel=cancel)


def test_resolver_uses_advertised_rrdd_link_and_rejects_foreign_host(tmp_path):
    url = ROOT + '2022/0928/catalog.html?dataset=NSSL/NOXP/2022/0928/cfrad.20220928_145307.nc'
    asset = asset_from_url(url)
    real = DATA_ROOT + '2022/0928/' + asset.name
    html = f'<a href="{real}">HTTPServer</a><a href="https://evil.test/{asset.name}">x</a>'
    adapter = NoxpArchive(tmp_path, lambda u, c: html)
    assert adapter.resolve(asset, Event()) == real
    assert 'NSSL/NOXP' not in adapter.resolve(asset, Event())
    adapter.fetch_catalog = lambda u, c: '<a href="https://evil.test/foo.nc">x</a>'
    with pytest.raises(ValueError, match='advertised'):
        adapter.resolve(asset, Event())


def test_sigmet_filename_date_is_not_parent_folder_date():
    url = ROOT + '2015/PECAN/20150602/catalog.html?dataset=NSSL/NOXP/2015/PECAN/20150602/NOX150603000019.RAWYNX9'
    asset = asset_from_url(url)
    assert asset.format == 'sigmet'
    assert asset.nominal_time == datetime(2015, 6, 3, 0, 0, 19, tzinfo=timezone.utc)


def test_cfradial_inventory_retains_cross_midnight_volume(tmp_path):
    url = ROOT + 'catalog.html'
    file = url + '?dataset=NSSL/NOXP/cfrad.20220928_235900.000_to_20220929_000200.000_NOXPRVP_RHI_corr.nc'
    adapter = NoxpArchive(tmp_path, lambda u, c: f'<a href="{file}">f</a>')
    assert len(adapter.discover(date(2022, 9, 29)).assets) == 1


def _sparse_file(path, rhi=False, bad_count=False):
    name = 'RHI_Reflectivity' if rhi else 'Reflectivity'
    ds = xr.Dataset({
        name: ('pixel', [10., -99901., 20.]),
        'pixel_x': ('pixel', [0, 0, 1]), 'pixel_y': ('pixel', [1, 3, 0]),
        'pixel_count': ('pixel', [2, 1, -1 if bad_count else 2]),
        'GateWidth': ('Azimuth', [75., 75.]), 'Azimuth': ('Azimuth', [10., 11.]),
    }, attrs=dict(DataType='SparseRadialSet', TypeName=name, Time=1265291364,
                  FractionalTime=.25, Elevation=.5, RangeToFirstGate=100.,
                  Latitude=35., Longitude=-97., Height=300., MissingData=-99900., RangeFolded=-99901.))
    ds[name].attrs['Units'] = 'dBZ'
    ds.to_netcdf(path, engine='scipy')


def test_sparse_runs_preserve_masks_range_and_fractional_time(tmp_path):
    path = tmp_path / 'r.netcdf'
    _sparse_file(path)
    result = read_noxp(path, 'wdss2')
    values = result.fields['Reflectivity']['data']
    assert values.shape == (2, 4)
    np.testing.assert_array_equal(values[0, 1:3], [10., 10.])
    assert values.mask[0, 0] and values.mask[0, 3]
    assert result.range_m[0, 1] == 175.
    assert result.time_epoch[0] == 1265291364.25
    assert result.fields['Reflectivity']['units'] == 'dBZ'


def test_sparse_rhi_does_not_invent_geographic_angles(tmp_path):
    path = tmp_path / 'r.netcdf'
    _sparse_file(path, rhi=True)
    result = read_noxp(path, 'wdss2')
    assert result.scan_type == 'rhi_unverified'
    assert np.isnan(result.azimuth_deg).all()
    assert result.provenance['native_azimuth'] == [10., 11.]
    _sparse_file(path, bad_count=True)
    with pytest.raises(ValueError, match='coordinates'):
        read_noxp(path, 'wdss2')


def test_cfradial_reader_preserves_sweeps_fields_and_mask(tmp_path):
    # Build a CF/Radial fixture independently of the reader's own writer.
    from netCDF4 import Dataset
    path = tmp_path / 'cfrad.nc'
    with Dataset(path, 'w') as ds:
        for name, size in [('time', 6), ('range', 4), ('sweep', 2), ('string_length', 20)]:
            ds.createDimension(name, size)
        def var(name, dims, values, units=None, dtype='f8', **kwargs):
            v = ds.createVariable(name, dtype, dims, **kwargs)
            v[:] = values
            if units:
                v.units = units
            return v
        var('time', ('time',), np.arange(6), 'seconds since 2022-09-28T00:00:00Z')
        var('range', ('range',), np.arange(4) * 100, 'meters')
        for name, value in [('latitude', 35.), ('longitude', -97.), ('altitude', 300.)]:
            var(name, (), value)
        var('sweep_mode', ('sweep', 'string_length'),
            np.array([list('azimuth_surveillance')] * 2, dtype='S1'), dtype='S1')
        var('fixed_angle', ('sweep',), [.5, 1.])
        var('sweep_number', ('sweep',), [0, 1], dtype='i4')
        var('sweep_start_ray_index', ('sweep',), [0, 3], dtype='i4')
        var('sweep_end_ray_index', ('sweep',), [2, 5], dtype='i4')
        var('azimuth', ('time',), [0, 120, 240] * 2)
        var('elevation', ('time',), [.5] * 3 + [1.] * 3)
        data = np.ones((6, 4))
        data[0, 0] = -9999.
        var('DBZ', ('time', 'range'), data, 'dBZ', fill_value=-9999.)
    result = read_noxp(path, 'cfradial')
    assert result.sweep_start.tolist() == [0, 3]
    assert result.sweep_end.tolist() == [2, 5]
    assert result.fields['DBZ']['data'].mask[0, 0]
    assert result.summary()['fields']['DBZ']['valid_cells'] == 23


def test_misdated_folder_year_is_not_excluded(tmp_path):
    root, child = ROOT + 'catalog.html', ROOT + 'Reeves/2011/catalog.html'
    url = child + '?dataset=NSSL/NOXP/Reeves/2011/NOX_20110201_2/20100201-234755.netcdf.gz'
    pages = {root: f'<a href="{child}">misdated</a>', child: f'<a href="{url}">file</a>'}
    result = NoxpArchive(tmp_path, lambda u, c: pages[u]).discover(date(2010, 2, 1))
    assert result.complete and len(result.assets) == 1
    assert result.assets[0].format == 'wdss2'


def test_gzipped_sparse_moment_is_decoded_without_losing_compression_provenance(tmp_path):
    import gzip
    path = tmp_path / 'r.netcdf'
    _sparse_file(path)
    compressed = tmp_path / 'r.netcdf.gz'
    compressed.write_bytes(gzip.compress(path.read_bytes()))
    result = read_noxp(compressed, 'wdss2')
    assert result.provenance['compression'] == 'gzip'
    assert result.summary()['fields']['Reflectivity']['valid_cells'] == 4


def test_requested_year_runs_first_without_claiming_other_branches_absent(tmp_path):
    root = ROOT + 'catalog.html'
    current, old = ROOT + '2022/catalog.html', ROOT + '2010/catalog.html'
    pages = {root: f'<a href="{old}">old</a><a href="{current}">new</a>', current: '<html>empty</html>'}
    calls = []
    def fetch(url, cancel):
        calls.append(url)
        return pages[url]
    result = NoxpArchive(tmp_path, fetch).discover(date(2022, 9, 28), budget=2)
    assert calls == [root, current]
    assert result.pending == 1 and not result.complete


def test_radar_asset_has_no_url_only_catalog_url():
    """Regression test for an AttributeError that crashed the app: unlike
    LidarAsset (raw_lidar_archive_fetcher.py), which has a real .url
    property, RadarAsset only carries catalog_url -- the actual download
    URL isn't resolved until deep inside NoxpArchive.load(). MainWindow's
    NOXP handlers must key off catalog_url (which NoxpArchive.load()
    preserves into the loaded volume's provenance), not a nonexistent
    .url attribute."""
    from archive.fetchers.noxp_archive_fetcher import RadarAsset
    asset = RadarAsset(
        catalog_url='https://example.test/catalog.html?dataset=NSSL/NOXP/2013/x.RAWETW4',
        name='x.RAWETW4', format='sigmet',
        nominal_time=datetime(2013, 5, 31, 21, 44, 5, tzinfo=timezone.utc),
    )
    assert not hasattr(asset, 'url')
    assert asset.catalog_url == 'https://example.test/catalog.html?dataset=NSSL/NOXP/2013/x.RAWETW4'


def test_dated_subdirectory_mismatch_is_skipped_without_spending_a_request(tmp_path):
    """A folder whose own name encodes one specific date that isn't the
    target (e.g. "20130409_moment" when searching 2013-05-31) should never
    be opened at all -- the whole point is not spending a request on it."""
    root = ROOT + 'catalog.html'
    moment = ROOT + 'moment2013/catalog.html'
    wrong_day = ROOT + 'moment2013/20130409_moment/catalog.html'
    right_day = ROOT + 'moment2013/20130531_moment/catalog.html'
    right_day_file = right_day + '?dataset=NSSL/NOXP/2013/moment2013/20130531_moment/NOX130531214405.RAWETW4'
    pages = {
        root: f'<a href="{moment}">moment2013</a>',
        moment: f'<a href="{wrong_day}">a</a><a href="{right_day}">b</a>',
        right_day: f'<a href="{right_day_file}">f</a>',
    }
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        if url == wrong_day:
            raise AssertionError('wrong_day should never be fetched at all')
        return pages[url]

    adapter = NoxpArchive(tmp_path, fetch)
    result = adapter.discover(date(2013, 5, 31), budget=10)

    assert wrong_day not in fetched
    assert right_day in fetched
    assert wrong_day in result.excluded
    assert {a.catalog_url for a in result.assets} == {right_day_file}


def test_ambiguous_subdirectory_names_are_never_skipped_by_date_pruning(tmp_path):
    """A folder name with no encoded date (e.g. VORTEX2's "Ingest") must
    still always be visited -- date pruning only ever removes folders it's
    certain don't match, never ones it's merely unsure about."""
    root = ROOT + 'catalog.html'
    ingest = ROOT + 'Vortex/2010/Ingest/catalog.html'
    pages = {root: f'<a href="{ingest}">Ingest</a>', ingest: ''}
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    adapter = NoxpArchive(tmp_path, fetch)
    adapter.discover(date(2013, 5, 31), budget=10)

    assert ingest in fetched


def test_sibling_catalog_pages_are_fetched_even_beyond_serial_expectations(tmp_path):
    """Regression guard for the batched-concurrency rewrite: a wide set of
    sibling pages (more than one batch's worth) must all still be reached
    and unioned correctly, not just the first _CONCURRENCY of them."""
    root = ROOT + 'catalog.html'
    siblings = [ROOT + f'2013/day{i:02d}/catalog.html' for i in range(10)]
    files = {s: s + f'?dataset=NSSL/NOXP/2013/day{i:02d}/f{i}.netcdf'
             for i, s in enumerate(siblings)}
    pages = {root: ''.join(f'<a href="{s}">s</a>' for s in siblings)}
    pages.update({s: f'<a href="{files[s]}">f</a>' for s in siblings})

    adapter = NoxpArchive(tmp_path, lambda u, c: pages[u])
    result = adapter.discover(budget=50)

    assert result.complete
    assert {a.catalog_url for a in result.assets} == set(files.values())


def _dated_pages():
    root, child = ROOT + 'catalog.html', ROOT + '2013/catalog.html'
    file = child + '?dataset=NSSL/NOXP/2013/NOX130531214405.RAWETW4'
    return root, {root: f'<a href="{child}">c</a>', child: f'<a href="{file}">f</a>'}, file


def test_decisive_discovery_with_matches_is_cached_across_instances(tmp_path):
    """A date-scoped crawl that actually found assets is decisive -- a
    brand new NoxpArchive pointed at the same cache_dir (standing in for
    a fresh app launch) must return the same answer without touching the
    network at all."""
    root, pages, file = _dated_pages()

    def fetch(url, cancel):
        return pages[url]

    first = NoxpArchive(tmp_path, fetch)
    result = first.discover(date(2013, 5, 31), budget=10)
    assert result.complete and {a.catalog_url for a in result.assets} == {file}

    def fail(url, cancel):
        raise AssertionError(f'unexpected network fetch for {url}')

    second = NoxpArchive(tmp_path, fail)
    cached = second.discover(date(2013, 5, 31), budget=10)
    assert {a.catalog_url for a in cached.assets} == {file}
    assert cached.pending == 0


def test_confirmed_empty_date_is_also_cached_across_instances(tmp_path):
    """A fully exhausted crawl that found nothing for the target date is
    just as decisive as finding something -- and is the common case (NOXP
    is rarely deployed), so it's worth skipping the re-crawl too."""
    root, pages, file = _dated_pages()

    def fetch(url, cancel):
        return pages[url]

    first = NoxpArchive(tmp_path, fetch)
    result = first.discover(date(2013, 5, 30), budget=10)  # file is dated 5/31
    assert result.complete and not result.assets

    def fail(url, cancel):
        raise AssertionError(f'unexpected network fetch for {url}')

    second = NoxpArchive(tmp_path, fail)
    cached = second.discover(date(2013, 5, 30), budget=10)
    assert not cached.assets and cached.pending == 0


def test_inconclusive_partial_result_is_not_cached(tmp_path):
    """A budget-limited call that found nothing *yet* must not poison the
    disk cache with an empty answer -- a later call (same instance, or a
    fresh one after a relaunch) still has to keep making real progress."""
    root, pages, file = _dated_pages()
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    first = NoxpArchive(tmp_path, fetch)
    result = first.discover(date(2013, 5, 31), budget=1)  # only the root page
    assert not result.complete and not result.assets

    second = NoxpArchive(tmp_path, fetch)
    resumed = second.discover(date(2013, 5, 31), budget=10)
    assert resumed.complete and {a.catalog_url for a in resumed.assets} == {file}
    assert fetched.count(root) == 2  # no cache hit -- the second instance re-fetched


def test_refresh_clears_the_disk_date_index_too(tmp_path):
    root, pages, file = _dated_pages()
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    adapter = NoxpArchive(tmp_path, fetch)
    adapter.discover(date(2013, 5, 31), budget=10)
    adapter.clear_catalog_cache()
    adapter.discover(date(2013, 5, 31), budget=10)

    assert fetched.count(root) == 2


def _multi_date_pages():
    """Two files, different dates, both under one ambiguous (unprunable)
    parent -- close to the shape a real campaign root has when a folder
    isn't itself named after one exact day."""
    root, moment = ROOT + 'catalog.html', ROOT + 'moment/catalog.html'
    may30 = moment + '?dataset=NSSL/NOXP/moment/NOX130530214405.RAWETW4'
    may31 = moment + '?dataset=NSSL/NOXP/moment/NOX130531214405.RAWETW4'
    pages = {root: f'<a href="{moment}">m</a>', moment: f'<a href="{may30}">a</a><a href="{may31}">b</a>'}
    return root, pages, may30, may31


def test_unfiltered_crawl_that_completes_indexes_every_date_at_once(tmp_path):
    """A target=None crawl that reaches the end of the tree should let a
    *later, different-date* targeted lookup for a date it already saw
    answer instantly -- the whole point of browsing several cases from
    one campaign without re-crawling per case."""
    root, pages, may30, may31 = _multi_date_pages()

    def fetch(url, cancel):
        return pages[url]

    indexer = NoxpArchive(tmp_path, fetch)
    full = indexer.discover(catalog_root=root, budget=10)  # target=None
    assert full.complete
    assert indexer.is_root_fully_indexed(root)

    def fail(url, cancel):
        raise AssertionError(f'unexpected network fetch for {url}')

    # A date never explicitly searched for before, from a fresh instance --
    # still answered from the root index alone, no crawl needed.
    lookup = NoxpArchive(tmp_path, fail)
    result = lookup.discover(date(2013, 5, 30), catalog_root=root, budget=10)
    assert {a.catalog_url for a in result.assets} == {may30}
    other = lookup.discover(date(2013, 5, 31), catalog_root=root, budget=10)
    assert {a.catalog_url for a in other.assets} == {may31}
    # A date genuinely absent from the root is answered as empty, not
    # re-crawled, since the index is known-complete for this root.
    absent = lookup.discover(date(2013, 6, 1), catalog_root=root, budget=10)
    assert not absent.assets


def test_budget_limited_unfiltered_crawl_does_not_mark_the_root_indexed(tmp_path):
    root, pages, may30, may31 = _multi_date_pages()

    def fetch(url, cancel):
        return pages[url]

    indexer = NoxpArchive(tmp_path, fetch)
    partial = indexer.discover(catalog_root=root, budget=1)  # only the root page
    assert not partial.complete
    assert not indexer.is_root_fully_indexed(root)

    # A fresh instance must still do real work -- an incomplete crawl was
    # never decisive enough to cache.
    fetched = []

    def fetch2(url, cancel):
        fetched.append(url)
        return pages[url]

    second = NoxpArchive(tmp_path, fetch2)
    result = second.discover(date(2013, 5, 30), catalog_root=root, budget=10)
    assert {a.catalog_url for a in result.assets} == {may30}
    assert fetched  # it actually crawled, rather than trusting a partial index


def test_a_fully_indexed_root_short_circuits_a_repeat_background_pass(tmp_path):
    """Once a root is fully indexed, re-submitting the same unfiltered
    background-indexing crawl (e.g. on the next app launch) should be an
    instant no-op, not a full re-walk of the tree."""
    root, pages, may30, may31 = _multi_date_pages()

    def fetch(url, cancel):
        return pages[url]

    NoxpArchive(tmp_path, fetch).discover(catalog_root=root, budget=10)

    def fail(url, cancel):
        raise AssertionError(f'unexpected network fetch for {url}')

    again = NoxpArchive(tmp_path, fail).discover(catalog_root=root, budget=10)
    assert again.complete
    assert {a.catalog_url for a in again.assets} == {may30, may31}


def test_refresh_clears_the_root_index_too(tmp_path):
    root, pages, may30, may31 = _multi_date_pages()
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    adapter = NoxpArchive(tmp_path, fetch)
    adapter.discover(catalog_root=root, budget=10)
    assert adapter.is_root_fully_indexed(root)

    adapter.clear_catalog_cache()
    assert not adapter.is_root_fully_indexed(root)

    adapter.discover(catalog_root=root, budget=10)
    assert fetched.count(root) == 2


class _FakeClock:
    """A settable clock, so a cache entry's age can be advanced past its
    TTL deterministically instead of waiting 30 real days."""
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


def test_per_date_cache_entry_expires_and_is_revalidated(tmp_path):
    root, pages, file = _dated_pages()
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    clock = _FakeClock()
    first = NoxpArchive(tmp_path, fetch, now=clock)
    first.discover(date(2013, 5, 31), budget=10)
    assert fetched.count(root) == 1

    # Still fresh -- a later instance sharing the same (unexpired) clock
    # basis must not re-fetch.
    clock.now += _DISCOVERY_CACHE_TTL_SECONDS - 1
    second = NoxpArchive(tmp_path, fetch, now=clock)
    second.discover(date(2013, 5, 31), budget=10)
    assert fetched.count(root) == 1

    # Past the TTL -- must revalidate against upstream again, not trust the
    # stale answer forever.
    clock.now += 2
    third = NoxpArchive(tmp_path, fetch, now=clock)
    result = third.discover(date(2013, 5, 31), budget=10)
    assert fetched.count(root) == 2
    assert {a.catalog_url for a in result.assets} == {file}


def test_root_index_entry_expires_and_is_revalidated(tmp_path):
    root, pages, may30, may31 = _multi_date_pages()
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    clock = _FakeClock()
    first = NoxpArchive(tmp_path, fetch, now=clock)
    first.discover(catalog_root=root, budget=10)
    assert fetched.count(root) == 1
    assert first.is_root_fully_indexed(root)

    clock.now += _DISCOVERY_CACHE_TTL_SECONDS + 1
    second = NoxpArchive(tmp_path, fetch, now=clock)
    assert not second.is_root_fully_indexed(root)  # stale -- no longer trusted
    result = second.discover(date(2013, 5, 30), catalog_root=root, budget=10)
    assert fetched.count(root) == 2  # revalidated against upstream, not replayed
    assert {a.catalog_url for a in result.assets} == {may30}


def test_missing_checked_at_is_treated_as_expired_not_a_crash(tmp_path):
    """A cache file written before this TTL existed has no checked_at at
    all -- must self-heal (treated as stale) rather than KeyError."""
    import json
    root, pages, file = _dated_pages()
    cache_dir = tmp_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / 'date_index.json').write_text(json.dumps({
        'version': 1,
        'entries': {f'{root}|2013-05-31': {'assets': [], 'catalogs_checked': 1}},
    }))
    fetched = []

    def fetch(url, cancel):
        fetched.append(url)
        return pages[url]

    adapter = NoxpArchive(cache_dir, fetch)
    result = adapter.discover(date(2013, 5, 31), budget=10)
    assert fetched  # revalidated instead of trusting the ageless entry
    assert {a.catalog_url for a in result.assets} == {file}
