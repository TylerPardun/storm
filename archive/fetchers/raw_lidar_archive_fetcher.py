"""CLAMPS b1 raw rays from the lidars' PPI and CSM (continuous scan mode)
files -- the scans STORM draws on the map. Fixed-point (stare) and "other"
(RHI) files are not part of STORM.

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
from archive import thredds_paths as paths


@dataclass(frozen=True)
class RawLidarSource:
    platform_id: str
    instrument: str   # the physical lidar, e.g. "DLTRUCK1" -- one instrument
                       # produces up to 4 platform_id entries below (one per
                       # scan-mode product), and the truck's lidar was also
                       # published under two stream names (DL1, DL2), so a
                       # single deployed lidar can show as several "sources".
    platform_dir: str
    datastream: str
    product: str
    mobile: bool

    @property
    def path(self):
        return paths.clamps_ingested(self.platform_dir, self.datastream)

    @property
    def stream(self) -> str:
        """The stream name inside the datastream, e.g. "DL2", "C1"."""
        return self.datastream.split('.')[0].split(f'dl{self.product}')[-1]


# DL1 and DL2 are the same physical lidar on the truck (confirmed by Tyler,
# 2026-09-25): DL2 files exist only 2022-05-13..06-16. On those days both
# streams can have a file for the same scan mode, and they differ, so both
# are kept and offered, labeled by stream -- nothing is deduplicated.
PRODUCTS = paths.RAW_LIDAR_PRODUCTS     # the PPI-type scan files; nothing else is read
KNOWN_RAW_LIDAR_SOURCES = tuple(
    RawLidarSource(f'{platform}-{product.upper()}', instrument, directory,
                   paths.raw_lidar_stream(prefix, product, unit), product, mobile)
    for platform, instrument, directory, prefix, unit, mobile in paths.RAW_LIDAR_PLATFORMS
    for product in PRODUCTS
)


@dataclass(frozen=True)
class LidarAsset:
    source: RawLidarSource
    filename: str
    catalog_url: str

    @property
    def url(self):
        return paths.file_url(f'{self.source.path}/{self.filename}')


def discover_raw_lidar(source: RawLidarSource, day: date, *, cancel=None, fetch_catalog=None):
    from archive.catalog import CatalogSpec, _CatalogLinks, _fetch_catalog_html
    prefix = f'{source.datastream}.{day:%Y%m%d}.'
    spec = CatalogSpec(source.path, '.cdf')
    html = (fetch_catalog or _fetch_catalog_html)(spec.url, cancel or Event())
    parser = _CatalogLinks(spec)
    parser.feed(html)
    return [LidarAsset(source, name, spec.url) for name in sorted(parser.filenames) if name.startswith(prefix)]


class _RangeFile:
    """Read-only file over HTTP range requests, 64 KB at a time, so h5py can
    read a netCDF4 file's header without downloading the file."""

    BLOCK = 64 * 1024

    def __init__(self, url: str, timeout: float = 20):
        self.url, self.timeout, self.pos, self.size, self._blocks = url, timeout, 0, None, {}

    def _block(self, i: int) -> bytes:
        if i not in self._blocks:
            from urllib.request import Request, urlopen
            import config
            request = Request(self.url, headers={'User-Agent': 'Mozilla/5.0 STORM/1.0',
                                                 'Range': f'bytes={i * self.BLOCK}-{(i + 1) * self.BLOCK - 1}'})
            with urlopen(request, timeout=self.timeout, context=config.NSSL_SSL_CONTEXT) as response:
                content_range = response.headers.get('Content-Range', '')
                data = response.read()
            if response.status != 206 or '/' not in content_range:
                raise OSError('server does not support range requests')
            self.size = int(content_range.rsplit('/', 1)[1])
            self._blocks[i] = data
        return self._blocks[i]

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        if self.size is None:
            self._block(0)
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, n=-1):
        if self.size is None:
            self._block(0)
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while len(out) < n and self.pos < self.size:
            i, offset = divmod(self.pos, self.BLOCK)
            chunk = self._block(i)[offset:offset + n - len(out)]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
        return bytes(out)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def read_site(asset: LidarAsset) -> dict | None:
    """A trailer's recorded site -- {lat, lon, altitude_m, description} --
    from the file's header only (one 64 KB range request; a day's
    fixed-point file can be 355 MB). None when the header has no usable
    site or can't be read this way (the full file then shows it when
    loaded)."""
    try:
        import h5py
        with h5py.File(_RangeFile(asset.url), 'r') as h5:
            attrs = dict(h5.attrs)
    except Exception:
        return None

    def number(name):
        value = attrs.get(name)
        try:
            value = float(np.ravel(value)[0]) if value is not None else float('nan')
        except (TypeError, ValueError, IndexError):
            return float('nan')
        return value if value > -900 else float('nan')

    lat, lon = number('Site_latitude'), number('Site_longitude')
    if not (np.isfinite(lat) and np.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0)):
        return None
    description = attrs.get('Site_description', '')
    if isinstance(description, bytes):
        description = description.decode('utf-8', 'replace')
    description = str(description).strip()
    return {'lat': lat, 'lon': lon, 'altitude_m': number('Site_altitude'),
            'description': '' if description in ('None', '-999') else description}


@dataclass
class RawLidarRays:
    source: RawLidarSource
    time_epoch: np.ndarray
    distance_m: np.ndarray    # slant range of each gate
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
    azimuth_known: np.ndarray | None = None   # per ray; None = all rays alike (provenance flag)
    instrument_azimuth_deg: np.ndarray | None = None   # as stored (the truck's: relative to the truck),
                                                       # for telling scan types apart (core/lidar_scans.py)

    @property
    def ground_geometry_valid(self):
        # Mapping needs true-north azimuths: the truck's are rotated from its
        # recorded heading at parse time (_truck_azimuth); without one they
        # can't be placed on a map.
        north = self.provenance.get("north_referenced")
        if north is None:
            north = "0 degrees is north" in str(self.provenance.get("azimuth_metadata", {}).get("comment", "")).lower()
        known = self.azimuth_known if self.azimuth_known is not None else (not self.source.mobile or north)
        return (np.isfinite(self.latitude) & np.isfinite(self.longitude)
                & np.isfinite(self.azimuth_deg) & np.isfinite(self.elevation_deg) & known)

    def rays_at(self, when: datetime, window_seconds=60):
        """Select real rays at/before the archive clock; do not carry across gaps."""
        if window_seconds <= 0:
            raise ValueError('window_seconds must be positive')
        epoch = when.timestamp()
        return np.flatnonzero((self.time_epoch <= epoch) & (self.time_epoch > epoch - window_seconds))

    def summary(self):
        return dict(platform=self.source.platform_id, product=self.source.product,
                    rays=int(self.time_epoch.size), gates=int(self.distance_m.size),
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


def _field_values(variable, valid):
    """A (time, range) field for the valid rays, in float32 (as the files
    store it) and masked where missing. Done in place: a lidar truck's day of
    CSM rays is large, and float64 copies of every field took ~0.5 GB."""
    values = np.asarray(variable.values)[valid].astype(np.float32, copy=False)
    values[~np.isfinite(values) | (values == -999) | (np.abs(values) > 1e30)] = np.nan
    return np.ma.masked_invalid(values, copy=False)


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
        if 'range' not in ds:
            raise ValueError('Not a PPI/CSM scan file: no slant range')
        dimension = 'range'
        axis = _values(ds[dimension])
        units = ds[dimension].attrs.get('units', '').lower().strip()
        # 2017-2020 CLAMPS files label it "km AGL", though it is slant range
        # ("Range from lidar ... height = range x sin(elevation)"); the unit is the first word
        units = units.split()[0] if units else units
        if units in ('km', 'kilometers', 'kilometres'):   # both spellings occur in files
            axis *= 1000
        elif units not in ('m', 'meters', 'metres'):
            raise ValueError(f'Unknown raw lidar range units: {units!r}')
        if axis.ndim != 1 or not np.isfinite(axis).all() or (axis < 0).any():
            raise ValueError('Invalid lidar range axis')
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
                fields[name] = {**variable.attrs, 'data': _field_values(variable, valid)}
            elif variable.dims == ('time',) and name not in ('time_offset', 'lat', 'lon', 'alt', 'heading', 'azimuth', 'elevation', 'snum'):
                housekeeping[name] = {**variable.attrs, 'data': _values(variable)[valid]}
        if 'velocity' not in fields:
            raise ValueError('Raw lidar file has no radial velocity field')
        warnings = ['Native quality/intensity fields are retained; no undocumented SNR threshold is applied.']
        if source.product == 'csm':
            warnings.append('Continuous scan mode: provider cautions that these rays require careful interpretation.')
        azimuth = rays('azimuth')
        stored_azimuth = azimuth.copy()
        reference = {'north_referenced': True, 'azimuth_reference': 'as stored in the file'}
        if source.mobile:
            azimuth, reference = _truck_azimuth(azimuth, ds.attrs.get('Trailer_heading'))
            warnings.append(reference['azimuth_reference'])
            warnings.append('No platform-motion correction is applied to radial velocity.')
        return RawLidarRays(source, times, axis, azimuth, rays('elevation'),
                            lat, lon, rays('alt'), rays('heading'), rays('snum'), fields, housekeeping,
                            origin, np.where(np.isfinite(lat), times, np.nan),
                            {'format': 'CLAMPS b1 raw lidar', 'metadata': {k: str(v) for k, v in ds.attrs.items()},
                             'distance_units_in_file': units,
                             'azimuth_metadata': dict(ds['azimuth'].attrs), **reference,
                             'altitude_reference': ds['alt'].attrs.get('units', 'unknown') if 'alt' in ds else 'unknown'}, warnings,
                            instrument_azimuth_deg=stored_azimuth)


def _truck_azimuth(azimuth, trailer_heading):
    """True-north azimuths for the LiDAR Truck.

    The truck's files store azimuth relative to the truck (0 deg = straight
    ahead of it), although the azimuth comment says "0 degrees is north"; the
    truck's heading is recorded in the Trailer_heading attribute but not
    applied, so true azimuth = stored + heading. Verified 2026-09-26
    (scripts/check_truck_lidar_orientation.py, planning evidence): VAD wind
    directions from the truck's PPI scans vs HRRR 80 m over 23 days / 62 scans
    had a median error of 75.7 deg as stored and 10 deg once rotated by the
    heading (85 % within 30 deg), while the CLAMPS trailers need no rotation.
    That check also showed radial velocity is positive AWAY from the lidar
    (rain in vertical stares is negative), despite the files' "positive
    values are towards the lidar" comment -- STORM already displays it that
    way (NWS colors), so only the truck azimuth needs correcting.
    A missing heading (-999, NaN, or the 0.0 default of the 2020-21 files)
    leaves the orientation unknown."""
    try:
        heading = float(trailer_heading)
    except (TypeError, ValueError):
        heading = float('nan')
    if not np.isfinite(heading) or heading <= -900 or heading == 0.0 or not 0 <= heading <= 360:
        return azimuth, {'north_referenced': False,
                         'azimuth_reference': 'Truck heading not recorded in this file, so scan directions '
                                              'relative to north are unknown; not placed on the map.'}
    return (azimuth + heading) % 360.0, {
        'north_referenced': True, 'truck_heading_deg': heading,
        'azimuth_reference': f'Truck-relative azimuths rotated to true north using the recorded truck '
                             f'heading ({heading:.1f} deg).'}


def load_raw_lidar(asset: LidarAsset, cache_dir, *, cancel=None, track_loader=None):
    from archive.catalog import _check_cancel
    from archive.fetchers.vehicle_obs_archive_fetcher import load_dltruck_track
    cancel = cancel or Event()
    path, provenance = download_asset(asset.url, cache_dir, cancel, asset.filename)
    _check_cancel(cancel)
    result = parse_raw_lidar(path, asset.source)
    result.provenance.update(provenance, catalog_url=asset.catalog_url)
    if asset.source.mobile:
        tracks, day_obs = {}, {}

        def track_for(when):
            if when.date() not in tracks:
                day_obs[when.date()] = (track_loader or load_dltruck_track)(when) or []
                tracks[when.date()] = PositionTrack(day_obs[when.date()])
            return tracks[when.date()]

        for i in np.flatnonzero(~np.isfinite(result.latitude)):
            _check_cancel(cancel)
            when = datetime.fromtimestamp(float(result.time_epoch[i]), timezone.utc)
            fix = track_for(when).nearest(when)
            if fix is not None:
                result.latitude[i], result.longitude[i] = fix.lat, fix.lon
                result.coordinate_source[i] = 'FOFS dltruck GPS within 60 s'
                result.position_time_epoch[i] = fix.timestamp.timestamp()
        if result.provenance.get('north_referenced'):
            result.heading_deg = np.full(result.time_epoch.size, result.provenance['truck_heading_deg'])
        else:
            _estimate_truck_heading(result, track_for, day_obs, cancel)
    _check_cancel(cancel)
    return result


def _estimate_truck_heading(result, track_for, day_obs, cancel, max_gap_seconds=60.0):
    """Fill a heading missing from a truck lidar file from the truck's own
    compass (FOFS mesonet compass_dir), ray by ray -- the truck can move
    during a file. Checked 2026-09-26: that compass matches the files'
    recorded Trailer_heading to a median 0.2 deg (45 scans), and rotating
    heading-less scans by it gives lidar wind directions within a median
    9.6 deg of HRRR (34 scans). Rays without a compass reading within 60 s
    stay unoriented and are not mapped."""
    from bisect import bisect_left
    from archive.catalog import _check_cancel
    heading = np.full(result.time_epoch.size, np.nan)
    series = {}
    for i, epoch in enumerate(result.time_epoch):
        _check_cancel(cancel)
        when = datetime.fromtimestamp(float(epoch), timezone.utc)
        track_for(when)
        if when.date() not in series:
            readings = sorted((o.timestamp.timestamp(), o.heading_deg) for o in day_obs[when.date()]
                              if getattr(o, 'heading_deg', None) is not None)
            series[when.date()] = ([t for t, _ in readings], [h for _, h in readings])
        times, values = series[when.date()]
        j = bisect_left(times, epoch)
        best = min((k for k in (j - 1, j) if 0 <= k < len(times)), key=lambda k: abs(times[k] - epoch), default=None)
        if best is not None and abs(times[best] - epoch) <= max_gap_seconds:
            heading[i] = values[best]
    known = np.isfinite(heading)
    result.heading_deg = heading
    result.azimuth_known = known
    result.azimuth_deg = np.where(known, (result.azimuth_deg + np.nan_to_num(heading)) % 360.0, result.azimuth_deg)
    if not known.any():
        result.provenance['azimuth_reference'] += ' No truck compass reading covers these times either.'
        result.warnings[:] = [result.provenance['azimuth_reference'] if w.startswith('Truck heading not recorded') else w
                              for w in result.warnings]
        return
    angles = np.deg2rad(heading[known])
    typical = float(np.rad2deg(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) % 360.0)
    spread = float(np.rad2deg(np.sqrt(-2 * np.log(max(np.hypot(np.sin(angles).mean(), np.cos(angles).mean()), 1e-9)))))
    share = known.mean()
    message = (f'Truck heading is missing from this lidar file; estimated from the truck compass '
               f'(FOFS mesonet): {typical:.0f} deg' + (f', varying ±{spread:.0f} deg as the truck moved' if spread > 10 else '')
               + ('' if share == 1 else f'; {100 * (1 - share):.0f}% of rays had no compass reading and are not mapped') + '.')
    result.provenance.update(north_referenced=True, heading_source='estimated from truck compass (FOFS compass_dir)',
                             heading_missing_in_file=True, truck_heading_estimate_deg=round(typical, 1),
                             estimated_rays=int(known.sum()), azimuth_reference=message)
    result.warnings[:] = [message if w.startswith('Truck heading not recorded') else w for w in result.warnings]
