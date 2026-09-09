from datetime import date, datetime, timezone
from threading import Event

import numpy as np
import pytest
import xarray as xr

from archive.catalog import ScanCancelled
from archive.fetchers.noxp_archive_fetcher import (
    ROOT, DATA_ROOT, NoxpArchive, asset_from_url, parse_catalog, read_noxp,
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
    with pytest.raises(ScanCancelled):
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
