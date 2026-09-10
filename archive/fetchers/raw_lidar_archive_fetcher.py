"""CLAMPS b1 raw rays: CSM, PPI, fixed point and other scans.

These are measured radial velocities/backscatter, not VAD wind retrievals.
The 136 MB HPL catalog is deliberately outside automatic discovery.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event
import numpy as np

from archive.positions import PositionTrack
from archive.fetchers.noxp_archive_fetcher import download_asset


@dataclass(frozen=True)
class RawLidarSource:
    platform_id: str
    instrument: str   # the physical lidar unit, e.g. "DLTRUCK1-DL1" -- one
                       # instrument produces up to 4 platform_id entries
                       # below (one per scan-mode product), which is why a
                       # single deployed lidar can show as several "sources".
    platform_dir: str
    datastream: str
    product: str
    mobile: bool

    @property
    def path(self):
        return f'FRDD/CLAMPS/{self.platform_dir}/ingested/{self.datastream}'


KNOWN_RAW_LIDAR_SOURCES = tuple(
    RawLidarSource(f'{platform}-{product.upper()}', platform, directory, f'{prefix}dl{product}{unit}.b1', product, mobile)
    for platform, directory, prefix, unit, mobile in (
        ('DLTRUCK1-DL1', 'dltruck/dltruck1', 'dltruck', 'DL1', True),
        ('DLTRUCK1-DL2', 'dltruck/dltruck1', 'dltruck', 'DL2', True),
        ('CLAMPS1', 'clamps/clamps1', 'clamps', 'C1', False),
        ('CLAMPS2', 'clamps/clamps2', 'clamps', 'C2', False),
    )
    for product in ('csm', 'ppi', 'fp', 'other')
)


@dataclass(frozen=True)
class LidarAsset:
    source: RawLidarSource
    filename: str
    catalog_url: str

    @property
    def url(self):
        return f'https://data.nssl.noaa.gov/thredds/fileServer/{self.source.path}/{self.filename}'


def discover_raw_lidar(source: RawLidarSource, day: date, *, cancel=None, fetch_catalog=None):
    from archive.catalog import CatalogSpec, _CatalogLinks, _fetch_catalog_html
    spec = CatalogSpec(source.path, '.cdf')
    html = (fetch_catalog or _fetch_catalog_html)(spec.url, cancel or Event())
    parser = _CatalogLinks(spec)
    parser.feed(html)
    prefix = f'{source.datastream}.{day:%Y%m%d}.'
    return [LidarAsset(source, name, spec.url) for name in sorted(parser.filenames) if name.startswith(prefix)]


@dataclass
class RawLidarRays:
    source: RawLidarSource
    time_epoch: np.ndarray
    distance_m: np.ndarray
    distance_kind: str  # "range" or file-declared "height"; never silently interchange.
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    altitude_m: np.ndarray
    heading_deg: np.ndarray
    scan_number: np.ndarray
    fields: dict
    housekeeping: dict
    coordinate_source: np.ndarray
    position_time_epoch: np.ndarray
    provenance: dict
    warnings: list[str]

    @property
    def ground_geometry_valid(self):
        # North-referenced file angles do not need another heading rotation.
        north = "0 degrees is north" in str(self.provenance.get("azimuth_metadata", {}).get("comment", "")).lower()
        return (np.isfinite(self.latitude) & np.isfinite(self.longitude)
                & np.isfinite(self.azimuth_deg) & np.isfinite(self.elevation_deg)
                & (not self.source.mobile or north))

    def rays_at(self, when: datetime, window_seconds=60):
        """Select real rays at/before the archive clock; do not carry across gaps."""
        if window_seconds <= 0:
            raise ValueError('window_seconds must be positive')
        epoch = when.timestamp()
        return np.flatnonzero((self.time_epoch <= epoch) & (self.time_epoch > epoch - window_seconds))

    def summary(self):
        return dict(platform=self.source.platform_id, product=self.source.product,
                    rays=int(self.time_epoch.size), gates=int(self.distance_m.size),
                    distance_kind=self.distance_kind,
                    start=datetime.fromtimestamp(float(self.time_epoch[0]), timezone.utc).isoformat(),
                    end=datetime.fromtimestamp(float(self.time_epoch[-1]), timezone.utc).isoformat(),
                    fields={k: {'units': v.get('units', ''), 'valid_cells': int(np.ma.count(v['data']))} for k, v in self.fields.items()},
                    positioned_rays=int(np.count_nonzero(np.isfinite(self.latitude))),
                    position_sources={str(v): int(np.count_nonzero(self.coordinate_source == v)) for v in np.unique(self.coordinate_source)},
                    georeferenced_rays=int(np.count_nonzero(self.ground_geometry_valid)),
                    warnings=self.warnings, provenance=self.provenance)


def _values(variable):
    values = np.asarray(variable.values, dtype=float).copy()
    # b1 files also use undeclared -999 and netCDF default-fill sentinels.
    values[~np.isfinite(values) | (values == -999) | (np.abs(values) > 1e30)] = np.nan
    return values


def parse_raw_lidar(path, source: RawLidarSource):
    import xarray as xr
    with Path(path).open('rb') as stream:
        engine = 'h5netcdf' if stream.read(8) == b'\x89HDF\r\n\x1a\n' else 'scipy'
    with xr.open_dataset(path, engine=engine, decode_times=False) as ds:
        epoch = float(ds['base_time'].values) + _values(ds['time_offset'])
        valid = np.flatnonzero(np.isfinite(epoch) & (epoch > 0) & (epoch < 32503680000))
        valid = valid[np.argsort(epoch[valid], kind='stable')]
        if not valid.size:
            raise ValueError('Raw lidar has no usable acquisition times')
        times = epoch[valid]
        dimension = 'range' if 'range' in ds else 'height'
        axis = _values(ds[dimension])
        units = ds[dimension].attrs.get('units', '').lower().strip()
        if units in ('km', 'kilometers', 'kilometres', 'km agl', 'km msl'):
            axis *= 1000
        elif units not in ('m', 'meters', 'metres', 'm agl', 'm msl'):
            raise ValueError(f'Unknown raw lidar {dimension} units: {units!r}')
        if axis.ndim != 1 or not np.isfinite(axis).all() or (axis < 0).any():
            raise ValueError('Invalid lidar range/height axis')
        def rays(name):
            if name not in ds:
                return np.full(times.size, np.nan)
            array = _values(ds[name])
            return np.full(times.size, float(array)) if array.ndim == 0 else array[valid]
        lat, lon = rays('lat'), rays('lon')
        good = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180) & ((lat != 0) | (lon != 0))
        origin = np.where(good, 'file rays', 'unknown').astype('U32')
        lat[~good], lon[~good] = np.nan, np.nan
        if not source.mobile:
            site_lat, site_lon = float(ds.attrs.get('Site_latitude', np.nan)), float(ds.attrs.get('Site_longitude', np.nan))
            if abs(site_lat) <= 90 and abs(site_lon) <= 180 and (site_lat, site_lon) != (0, 0):
                lat[~good], lon[~good], origin[~good] = site_lat, site_lon, 'file site attributes'
        fields, housekeeping = {}, {}
        for name, variable in ds.data_vars.items():
            if variable.dims == ('time', dimension):
                fields[name] = {**variable.attrs, 'data': np.ma.masked_invalid(_values(variable)[valid])}
            elif variable.dims == ('time',) and name not in ('time_offset', 'lat', 'lon', 'alt', 'heading', 'azimuth', 'elevation', 'snum'):
                housekeeping[name] = {**variable.attrs, 'data': _values(variable)[valid]}
        if 'velocity' not in fields:
            raise ValueError('Raw lidar file has no radial velocity field')
        warnings = ['Native quality/intensity fields are retained; no undocumented SNR threshold is applied.']
        if source.product == 'csm':
            warnings.append('Continuous scan mode: provider cautions that these rays require careful interpretation.')
        if source.mobile:
            warnings.append('Mobile mapping requires file-declared north azimuth. No additional platform-motion correction is applied to radial velocity.')
        return RawLidarRays(source, times, axis, dimension, rays('azimuth'), rays('elevation'),
                            lat, lon, rays('alt'), rays('heading'), rays('snum'), fields, housekeeping,
                            origin, np.where(np.isfinite(lat), times, np.nan),
                            {'format': 'CLAMPS b1 raw lidar', 'metadata': {k: str(v) for k, v in ds.attrs.items()},
                             'distance_units_in_file': units,
                             'azimuth_metadata': dict(ds['azimuth'].attrs),
                             'altitude_reference': ds['alt'].attrs.get('units', 'unknown') if 'alt' in ds else 'unknown'}, warnings)


def load_raw_lidar(asset: LidarAsset, cache_dir, *, cancel=None, track_loader=None):
    from archive.catalog import _check_cancel
    from archive.fetchers.vehicle_obs_archive_fetcher import load_dltruck_track
    cancel = cancel or Event()
    path, provenance = download_asset(asset.url, cache_dir, cancel, asset.filename)
    _check_cancel(cancel)
    result = parse_raw_lidar(path, asset.source)
    result.provenance.update(provenance, catalog_url=asset.catalog_url)
    if asset.source.mobile:
        tracks = {}
        for i in np.flatnonzero(~np.isfinite(result.latitude)):
            _check_cancel(cancel)
            when = datetime.fromtimestamp(float(result.time_epoch[i]), timezone.utc)
            if when.date() not in tracks:
                tracks[when.date()] = PositionTrack((track_loader or load_dltruck_track)(when))
            fix = tracks[when.date()].nearest(when)
            if fix is not None:
                result.latitude[i], result.longitude[i] = fix.lat, fix.lon
                result.coordinate_source[i] = 'FOFS dltruck GPS within 60 s'
                result.position_time_epoch[i] = fix.timestamp.timestamp()
    _check_cancel(cancel)
    return result
