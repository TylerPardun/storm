"""NOXP THREDDS inventory and scientific readers.

Catalog folders are discovery hints, never acquisition times. Inventory keeps
both processed mirrors and resolves downloads from advertised HTTPServer links.
IQ timeseries, unsupported ingest records and test content are explicitly outside the moment
reader's scope. Traversal is bounded and resumable within an adapter session.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

import numpy as np

ROOT = 'https://data.nssl.noaa.gov/thredds/catalog/RRDD/NOXP/'
DATA_ROOT = 'https://data.nssl.noaa.gov/thredds/fileServer/RRDD/NOXP/'
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}


@dataclass(frozen=True)
class RadarAsset:
    catalog_url: str
    name: str
    format: str
    nominal_time: datetime | None  # Filename hint, replaced by decoded times on load.
    nominal_end: datetime | None = None


@dataclass
class RadarInventory:
    assets: list[RadarAsset] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    catalogs_checked: int = 0
    pending: int = 0
    scope: str = 'Supported moment products; filename dates are discovery hints'

    @property
    def complete(self):
        return self.pending == 0 and not self.errors


class CatalogLinks(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.catalogs, self.datasets, self.downloads = set(), set(), set()

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'a':
            return
        href = dict(attrs).get('href')
        if not href:
            return
        url = urljoin(self.url, href)
        parsed = urlsplit(url)
        if url.startswith(DATA_ROOT) and not parsed.query:
            self.downloads.add(url)
        elif url.startswith(ROOT) and parsed.path.endswith('/catalog.html'):
            dataset = parse_qs(parsed.query).get('dataset')
            if dataset:
                self.datasets.add(url)
            elif not parsed.query:
                self.catalogs.add(url)


def parse_catalog(url, html):
    links = CatalogLinks(url)
    links.feed(html)
    return links


def asset_from_url(url):
    dataset = parse_qs(urlsplit(url).query).get('dataset', [''])[0]
    name = dataset.rsplit('/', 1)[-1]
    decoded_name = name.removesuffix('.gz')
    if decoded_name.endswith('.netcdf'):
        fmt = 'wdss2'
    elif decoded_name.startswith('cfrad.') and decoded_name.endswith('.nc'):
        fmt = 'cfradial'
    elif re.fullmatch(r'NOX\d{12}\.RAW\w+', decoded_name, re.I):
        fmt = 'sigmet'
    else:
        return None
    patterns = ((r'(\d{8}[-_]\d{6})', '%Y%m%d%H%M%S'),
                (r'NOX(\d{12})', '%y%m%d%H%M%S'))
    nominal = None
    for pattern, time_format in patterns:
        match = re.search(pattern, name)
        if match:
            try:
                nominal = datetime.strptime(re.sub('[-_]', '', match[1]), time_format).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
            break
    end_match = re.search(r'_to_(\d{8}_\d{6})', name)
    end = None
    if end_match:
        try:
            end = datetime.strptime(end_match[1], '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return RadarAsset(url, name, fmt, nominal, end)


def _excluded(url):
    parts = unquote(urlsplit(url).path).lower().split('/')
    return any(p == 'real_time_test' or p.startswith('timeseries') for p in parts)


def _year_priority(url, target):
    # Labels affect ordering only. Reeves/2011 contains 2010 files; excluding
    # mismatched folder years would hide real observations.
    parts = urlsplit(url).path.removeprefix(urlsplit(ROOT).path).split('/')
    years = []
    for part in parts:
        if re.fullmatch(r'20\d{2}', part):
            years.append(int(part))
        else:
            match = re.search(r'(20\d{2})[01]\d[0-3]\d', part)
            if match:
                years.append(int(match[1]))
    if not years:
        return 1
    return 0 if target.year in years else 2


class NoxpArchive:
    def __init__(self, cache_dir: Path, fetch_catalog=None):
        from archive.catalog import _fetch_catalog_html
        self.cache_dir = Path(cache_dir)
        self.fetch_catalog = fetch_catalog or _fetch_catalog_html
        self._catalogs = {}

    def discover(self, target: date | None = None, *, cancel=None, budget=80, progress=None, catalog_root=None):
        """Union supported files; retain unknown/budget-limited states.

        A second call reuses completed catalogs and spends its budget on the
        remaining pages. No data files or dataset-detail pages are downloaded.
        Folder years only prioritize requests. A complete scan examines every
        supported branch beneath the declared root, including misdated folders.
        """
        from archive.catalog import _check_cancel
        cancel = cancel or Event()
        result = RadarInventory()
        start_url = catalog_root or ROOT + 'catalog.html'
        if not start_url.startswith(ROOT) or not start_url.endswith('/catalog.html'):
            raise ValueError('Inventory root must be a NOXP directory catalog')
        result.scope += f'; root={start_url}'
        queue = deque([start_url])
        visited, assets = set(), {}
        requests = 0
        while queue:
            _check_cancel(cancel)
            url = queue.popleft()
            if url in visited:
                continue
            if url not in self._catalogs and requests >= budget:
                queue.appendleft(url)
                break
            visited.add(url)
            try:
                if url not in self._catalogs:
                    requests += 1
                    if cancel.wait(0.2):
                        _check_cancel(cancel)
                    html = self.fetch_catalog(url, cancel)
                    # 404 at a listed child is unresolved, not an empty catalog.
                    if not html:
                        raise ValueError('Catalog returned 404')
                    self._catalogs[url] = parse_catalog(url, html)
                links = self._catalogs[url]
                result.catalogs_checked += 1
                for child in sorted(links.catalogs, reverse=True):
                    if not child.startswith(url.rsplit('/', 1)[0] + '/') or child == url:
                        continue
                    if _excluded(child):
                        result.excluded.append(child)
                    else:
                        queue.append(child)
                if target is not None:
                    queue = deque(sorted(queue, key=lambda child: _year_priority(child, target)))
                for child in sorted(links.datasets):
                    asset = asset_from_url(child)
                    if asset is None:
                        result.excluded.append(child)
                    elif target is None or asset.nominal_time is None or asset.nominal_time.date() <= target <= (asset.nominal_end or asset.nominal_time).date():
                        assets[child] = asset  # Identity is the locator, never the basename.
                result.assets = sorted(assets.values(), key=lambda a: (a.nominal_time or datetime.min.replace(tzinfo=timezone.utc), a.catalog_url))
                result.pending = len(queue)
                if progress:
                    progress(replace(result, assets=list(result.assets), errors=list(result.errors), excluded=list(result.excluded)))
            except Exception as exc:
                from archive.catalog import ScanCancelled
                if isinstance(exc, ScanCancelled):
                    raise
                result.errors.append(f'{url}: {exc}')
        result.pending = len(queue)
        return result

    def resolve(self, asset, cancel):
        """Use the advertised HTTPServer URL, not the incorrect NSSL dataset key."""
        html = self.fetch_catalog(asset.catalog_url, cancel)
        downloads = parse_catalog(asset.catalog_url, html).downloads
        matches = [u for u in downloads if unquote(urlsplit(u).path).rsplit('/', 1)[-1] == asset.name]
        if len(matches) != 1:
            raise ValueError(f'Expected one advertised NOXP HTTPServer URL for {asset.name}')
        return matches[0]

    def load(self, asset: RadarAsset, *, cancel=None):
        cancel = cancel or Event()
        url = self.resolve(asset, cancel)
        path, provenance = download_asset(url, self.cache_dir, cancel, asset.name)
        volume = read_noxp(path, asset.format)
        volume.provenance.update(provenance, catalog_url=asset.catalog_url)
        return volume


def download_asset(url, cache_dir, cancel, filename, max_bytes=512 * 1024 * 1024):
    """Bounded atomic download with a content hash; never cache partial files."""
    import config
    from archive.catalog import _check_cancel
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = cache_dir / (key + Path(filename).suffix)
    record = path.with_suffix(path.suffix + '.json')
    if path.exists() and record.exists():
        provenance = json.loads(record.read_text())
        with path.open('rb') as cached:
            valid = provenance['sha256'] == hashlib.file_digest(cached, 'sha256').hexdigest()
        if valid:
            _check_cancel(cancel)
            return path, provenance
    import tempfile
    temporary = None
    try:
        digest, size = hashlib.sha256(), 0
        started = time.monotonic()
        _check_cancel(cancel)
        with urlopen(Request(url, headers=_HEADERS), timeout=30, context=config.NSSL_SSL_CONTEXT) as response:
            with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix='.part') as out:
                temporary = Path(out.name)
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes or time.monotonic() - started > 180:
                        raise ValueError('Scientific file exceeds download size/time limit')
                    digest.update(chunk)
                    out.write(chunk)
        _check_cancel(cancel)
        provenance = dict(url=url, sha256=digest.hexdigest(), bytes=size,
                          fetched_at=datetime.now(timezone.utc).isoformat(), download_seconds=time.monotonic() - started)
        temporary.replace(path)
        record.write_text(json.dumps(provenance, indent=2))
        return path, provenance
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class RadarVolume:
    time_epoch: np.ndarray
    range_m: np.ndarray
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    altitude_m: np.ndarray
    fields: dict[str, dict]  # Native names, masked data and units.
    sweep_start: np.ndarray
    sweep_end: np.ndarray  # Inclusive, as in CF/Radial.
    scan_type: str
    provenance: dict
    warnings: list[str] = field(default_factory=list)

    def summary(self):
        times = self.time_epoch[np.isfinite(self.time_epoch)]
        return dict(format=self.provenance['format'], scan_type=self.scan_type,
                    rays=int(self.time_epoch.size), gates=int(self.range_m.shape[-1]),
                    sweeps=int(self.sweep_start.size),
                    start=datetime.fromtimestamp(float(times.min()), timezone.utc).isoformat() if times.size else None,
                    end=datetime.fromtimestamp(float(times.max()), timezone.utc).isoformat() if times.size else None,
                    latitude=self.latitude.tolist(), longitude=self.longitude.tolist(),
                    fields={k: {'units': v.get('units', ''), 'valid_cells': int(np.ma.count(v['data']))} for k, v in self.fields.items()},
                    warnings=self.warnings, provenance=self.provenance)


def read_noxp(path, fmt):
    # Compressed WDSS files are common in Reeves/Colorado. Bound inflation too.
    with Path(path).open('rb') as stream:
        compressed = stream.read(2) == b'\x1f\x8b'
    if compressed:
        import gzip
        import tempfile
        with gzip.open(path, 'rb') as stream:
            content = stream.read(256 * 1024 * 1024 + 1)
        if len(content) > 256 * 1024 * 1024:
            raise ValueError('Compressed radar file exceeds decoded size limit')
        with tempfile.NamedTemporaryFile(suffix='.netcdf') as expanded:
            expanded.write(content)
            expanded.flush()
            volume = read_noxp(expanded.name, fmt)
        volume.provenance['compression'] = 'gzip'
        return volume
    if fmt == 'wdss2':
        return _read_sparse(path)
    if fmt not in ('sigmet', 'cfradial'):
        raise ValueError(f'Unsupported NOXP moment format: {fmt}')
    import pyart
    from netCDF4 import num2date
    reader = pyart.io.read_sigmet if fmt == 'sigmet' else pyart.io.read_cfradial
    options = {'file_field_names': True}
    if fmt == 'sigmet':
        # Native vendor names are not keys in Py-ART's default metadata table.
        # Supply its format mapping explicitly so retaining DBZ/VEL does not
        # silently strip the physical units from decoded Sigmet measurements.
        options['additional_metadata'] = {
            native: pyart.config.get_metadata(mapped)
            for native, mapped in pyart.config.get_field_mapping('sigmet').items() if mapped
        }
    radar = reader(str(path), **options)
    dates = num2date(radar.time['data'], radar.time['units'], only_use_cftime_datetimes=False,
                    only_use_python_datetimes=True)
    epochs = np.array([d.replace(tzinfo=timezone.utc).timestamp() for d in dates])
    fields = {k: {**v, 'data': np.ma.masked_invalid(v['data'])} for k, v in radar.fields.items()}
    volume = RadarVolume(epochs, radar.range['data'], radar.azimuth['data'], radar.elevation['data'],
                        radar.latitude['data'], radar.longitude['data'], radar.altitude['data'], fields,
                        radar.sweep_start_ray_index['data'], radar.sweep_end_ray_index['data'], radar.scan_type,
                        {'format': fmt, 'reader': f'Py-ART {pyart.__version__}',
                         'metadata': {k: str(v) for k, v in radar.metadata.items()},
                         'coordinate_source': 'file', 'altitude_reference': 'file-declared; not independently verified'})
    if not fields or not epochs.size:
        raise ValueError('NOXP file contains no moment fields or rays')
    return volume


def _read_sparse(path):
    import xarray as xr
    with Path(path).open('rb') as stream:
        engine = 'h5netcdf' if stream.read(8) == b'\x89HDF\r\n\x1a\n' else 'scipy'
    with xr.open_dataset(path, engine=engine, decode_times=False) as ds:
        if ds.attrs.get('DataType') != 'SparseRadialSet':
            raise ValueError('Only WDSS-II SparseRadialSet is supported')
        name = ds.attrs['TypeName']
        x, y, count = (np.asarray(ds[k].values, dtype=np.int64) for k in ('pixel_x', 'pixel_y', 'pixel_count'))
        values = np.asarray(ds[name].values, dtype=float)
        nrays = ds.sizes['Azimuth']
        if not (x.shape == y.shape == count.shape == values.shape) or not x.size:
            raise ValueError('Invalid or empty sparse moment runs')
        if np.any(x < 0) or np.any(x >= nrays) or np.any(y < 0) or np.any(count <= 0):
            raise ValueError('Invalid sparse run coordinates')
        ngates = int(np.max(y + count))
        if nrays * ngates > 20_000_000:
            raise ValueError('Sparse moment exceeds decoded allocation limit')
        data = np.ma.masked_all((nrays, ngates), dtype=np.float32)
        invalid = [ds.attrs.get('MissingData'), ds.attrs.get('RangeFolded'), ds[name].attrs.get('BackgroundValue')]
        for ray, gate, length, value in zip(x, y, count, values):
            if np.isfinite(value) and value not in invalid and abs(value) < 1e30:
                data[ray, gate:gate + length] = value
        widths = np.asarray(ds['GateWidth'].values, dtype=float)
        ranges = float(ds.attrs['RangeToFirstGate']) + widths[:, None] * np.arange(ngates)[None, :]
        angle = np.asarray(ds['Azimuth'].values, dtype=float)
        fixed = np.full(nrays, float(ds.attrs['Elevation']))
        rhi = name.startswith('RHI_')
        warnings = ['Sparse file omits full gate count; range extent covers encoded runs only.',
                    'File timestamp applies to the product; individual ray acquisition times are unavailable.',
                    'Range-folded and missing samples are masked; native metadata retains their codes.']
        if rhi:
            warnings.append('WDSS-II RHI angle convention is unverified; native angles retained in metadata, georeferenced rendering disabled.')
        return RadarVolume(
            np.full(nrays, float(ds.attrs['Time']) + float(ds.attrs.get('FractionalTime', 0))), ranges,
            np.full(nrays, np.nan) if rhi else angle, np.full(nrays, np.nan) if rhi else fixed,
            np.atleast_1d(ds.attrs['Latitude']), np.atleast_1d(ds.attrs['Longitude']), np.atleast_1d(ds.attrs['Height']),
            {name: {'data': data, 'units': ds[name].attrs.get('Units', '')}},
            np.array([0]), np.array([nrays - 1]), 'rhi_unverified' if rhi else 'ppi',
            {'format': 'wdss2', 'reader': 'STORM SparseRadialSet', 'coordinate_source': 'file',
             'metadata': {k: str(v) for k, v in ds.attrs.items()},
             'native_azimuth': angle.tolist(), 'native_elevation': float(fixed[0])}, warnings)
