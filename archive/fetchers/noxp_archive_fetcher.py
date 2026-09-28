"""NOXP THREDDS inventory and scientific readers.

Catalog folders are discovery hints, never acquisition times. Inventory keeps
both processed mirrors and resolves downloads from advertised HTTPServer links.
IQ timeseries, unsupported ingest records and test content are explicitly outside the moment
reader's scope. Traversal is bounded and resumable within an adapter session.
"""
from __future__ import annotations

import hashlib
import json
import logging
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

log = logging.getLogger(__name__)

ROOT = 'https://data.nssl.noaa.gov/thredds/catalog/RRDD/NOXP/'
DATA_ROOT = 'https://data.nssl.noaa.gov/thredds/fileServer/RRDD/NOXP/'
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
# Per-(catalog root, date) resolved-asset cache -- one full THREDDS crawl for
# a known past date is *usually* a fixed answer (published field-campaign
# archives are rarely revised), but "rarely" isn't "never" -- late data
# delivery, reprocessing, or a catalog reorganization on NSSL's end are all
# real possibilities this shouldn't trust forever without ever rechecking.
# A decisive result is cached for _DISCOVERY_CACHE_TTL_SECONDS, long enough
# that browsing many cases across a research session (the reason this cache
# exists at all -- see the root index below) doesn't keep re-paying the
# crawl cost, but short enough to self-heal on its own if upstream changes.
# archive/catalog.py's own AvailabilityIndex date cache uses a much shorter
# 24h TTL for the same reason (calendar shading) at a much smaller scale
# (whole-day-count changes, not one campaign's full page tree). The launch
# dialog's "Refresh" button (_refresh_availability) also clears this cache
# immediately via clear_catalog_cache(), for whenever 30 days is too long
# to wait. This is a separate file, keyed differently (root+date, not just
# root), so the two caches don't collide.
_ASSET_INDEX_FILENAME = 'date_index.json'
_DISCOVERY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
# Per-root, every-date-at-once index. A single-date crawl only has to visit
# a fraction of a campaign root, so the cache above helps only when the
# *exact same date* is revisited -- browsing a whole campaign one case at a
# time (the more common workflow) gets no benefit from it. This second
# cache holds one full unfiltered crawl's result (target=None, so nothing
# is folder-pruned by date -- see _dated_subdir_mismatch), grouped by every
# date it found, and is trusted for a lookup only once `fully_indexed` is
# True for that root -- i.e. the crawl actually reached the end of the
# tree, not just "whatever a partial crawl happened to see." Once true,
# *every* date in that root -- including ones never directly searched for
# -- answers instantly. Building it reuses whatever pages any earlier
# crawl (targeted or not) already fetched, via the shared in-memory
# _catalogs page memo, so it never re-fetches a page twice. Subject to the
# same _DISCOVERY_CACHE_TTL_SECONDS expiry as the per-date cache above.
_ROOT_INDEX_FILENAME = 'root_index.json'


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
    requests_made: int = 0
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


_DATED_SUBDIR = re.compile(r'^(\d{8})(?:_.*)?$')


def _dated_subdir_mismatch(child_url, target):
    """True only when this catalog URL's own directory name encodes one
    specific YYYYMMDD date (e.g. "20130531_moment") that isn't target --
    safe to skip entirely rather than spend a request opening it. Ambiguous
    names (no encoded date, e.g. "Ingest"/"Product_Raw") are never skipped
    this way; unlike _year_priority (ordering only), this actually removes
    the folder from the crawl, so it stays conservative on purpose."""
    if target is None or not child_url.endswith('/catalog.html'):
        return False
    parts = child_url.rstrip('/').split('/')
    if len(parts) < 2:
        return False
    match = _DATED_SUBDIR.match(parts[-2])
    if not match:
        return False
    try:
        return datetime.strptime(match[1], '%Y%m%d').date() != target
    except ValueError:
        return False


class NoxpArchive:
    def __init__(self, cache_dir: Path, fetch_catalog=None, now=None):
        from archive.catalog import _fetch_catalog_html
        self.cache_dir = Path(cache_dir)
        self.fetch_catalog = fetch_catalog or _fetch_catalog_html
        self._catalogs = {}
        # Injectable so tests can simulate time passing without a real
        # sleep; defaults to the real wall clock in production.
        self._clock = now or time.time
        self._asset_index_path = self.cache_dir / _ASSET_INDEX_FILENAME
        self._asset_index = self._read_asset_index()
        self._root_index_path = self.cache_dir / _ROOT_INDEX_FILENAME
        self._root_index = self._read_json_index(self._root_index_path, 'root')

    def clear_catalog_cache(self):
        self._catalogs.clear()
        self._asset_index.clear()
        self._write_asset_index()
        self._root_index.clear()
        self._write_root_index()

    @staticmethod
    def _read_json_index(path: Path, label: str) -> dict:
        try:
            if not path.exists():
                return {}
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict) or payload.get('version') != 1:
                return {}
            entries = payload.get('entries', {})
            return entries if isinstance(entries, dict) else {}
        except (OSError, ValueError, TypeError, KeyError):
            log.warning('NoxpArchive: ignoring unreadable %s-index cache', label)
            return {}

    def _write_json_index(self, path: Path, entries: dict, label: str):
        import tempfile
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.cache_dir, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'entries': entries}, stream)
            temporary.replace(path)
        except OSError as exc:
            log.warning('NoxpArchive: could not save %s-index cache: %s', label, exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _read_asset_index(self) -> dict:
        return self._read_json_index(self._asset_index_path, 'date')

    def _write_asset_index(self):
        self._write_json_index(self._asset_index_path, self._asset_index, 'date')

    def _write_root_index(self):
        self._write_json_index(self._root_index_path, self._root_index, 'root')

    @staticmethod
    def _asset_index_key(catalog_root: str, target: date) -> str:
        return f'{catalog_root}|{target.isoformat()}'

    @staticmethod
    def _asset_to_row(a: "RadarAsset") -> dict:
        return {
            'catalog_url': a.catalog_url, 'name': a.name, 'format': a.format,
            'nominal_time': a.nominal_time.isoformat() if a.nominal_time else None,
            'nominal_end': a.nominal_end.isoformat() if a.nominal_end else None,
        }

    @staticmethod
    def _row_to_asset(row: dict) -> "RadarAsset":
        return RadarAsset(
            catalog_url=row['catalog_url'], name=row['name'], format=row['format'],
            nominal_time=datetime.fromisoformat(row['nominal_time']) if row['nominal_time'] else None,
            nominal_end=datetime.fromisoformat(row['nominal_end']) if row['nominal_end'] else None,
        )

    def _entry_expired(self, entry: dict) -> bool:
        # Missing checked_at (an entry from before this field existed)
        # counts as expired rather than erroring -- self-heals on the next
        # write instead of needing a one-off migration.
        age = self._clock() - float(entry.get('checked_at', 0))
        return not (0 <= age < _DISCOVERY_CACHE_TTL_SECONDS)

    def _cached_discovery(self, catalog_root: str, target: date) -> "RadarInventory | None":
        # A fully-indexed root (see discover()) answers any date within it,
        # including ones never directly searched for -- check that first,
        # since it's strictly more complete than the per-date cache below.
        root_entry = self._root_index.get(catalog_root)
        if root_entry is not None and root_entry.get('fully_indexed') and not self._entry_expired(root_entry):
            rows = root_entry['assets_by_date'].get(target.isoformat(), [])
            return RadarInventory(
                assets=[self._row_to_asset(row) for row in rows],
                scope=RadarInventory.scope + f'; root={catalog_root} (cached root index)',
            )
        entry = self._asset_index.get(self._asset_index_key(catalog_root, target))
        if entry is None or self._entry_expired(entry):
            return None
        return RadarInventory(
            assets=[self._row_to_asset(row) for row in entry['assets']],
            catalogs_checked=entry.get('catalogs_checked', 0),
            scope=RadarInventory.scope + f'; root={catalog_root} (cached date index)',
        )

    def _cache_discovery(self, catalog_root: str, target: date, result: "RadarInventory") -> None:
        self._asset_index[self._asset_index_key(catalog_root, target)] = {
            'assets': [self._asset_to_row(a) for a in result.assets],
            'catalogs_checked': result.catalogs_checked,
            'checked_at': self._clock(),
        }
        self._write_asset_index()

    def _cache_root_index(self, catalog_root: str, result: "RadarInventory") -> None:
        by_date: dict[str, list] = {}
        for a in result.assets:
            if a.nominal_time is None:
                continue
            by_date.setdefault(a.nominal_time.date().isoformat(), []).append(self._asset_to_row(a))
        self._root_index[catalog_root] = {
            'assets_by_date': by_date, 'fully_indexed': True, 'checked_at': self._clock(),
        }
        self._write_root_index()

    def is_root_fully_indexed(self, catalog_root: str) -> bool:
        entry = self._root_index.get(catalog_root)
        return bool(entry and entry.get('fully_indexed') and not self._entry_expired(entry))

    # Sibling catalog pages are independent HTTP GETs of small HTML, not the
    # bounded radar-volume downloads elsewhere in this codebase that stay
    # deliberately throttled for memory safety -- fetching a batch of these
    # concurrently is low-risk and is the single biggest lever on wall-clock
    # crawl time. Kept moderate (not higher) since this server has shown
    # WAF/bot-detection sensitivity elsewhere in this codebase.
    _CONCURRENCY = 6

    def discover(self, target: date | None = None, *, cancel=None, budget=80, progress=None, catalog_root=None):
        """Union supported files; retain unknown/budget-limited states.

        A second call reuses completed catalogs and spends its budget on the
        remaining pages. No data files or dataset-detail pages are downloaded.
        Folder years only prioritize requests. A complete scan examines every
        supported branch beneath the declared root, including misdated
        folders -- except a folder whose own name encodes one specific date
        that isn't target (_dated_subdir_mismatch), which is skipped
        entirely rather than opened, since opening it can only ever be
        wasted budget.

        A date-scoped call (target given) checks the on-disk date index
        first and returns instantly on a hit -- see _cache_discovery for
        what counts as decisive enough to have been cached.
        """
        from archive.catalog import _check_cancel
        from concurrent.futures import ThreadPoolExecutor
        cancel = cancel or Event()
        result = RadarInventory()
        start_url = catalog_root or ROOT + 'catalog.html'
        if not start_url.startswith(ROOT) or not start_url.endswith('/catalog.html'):
            raise ValueError('Inventory root must be a NOXP directory catalog')
        if target is not None:
            cached = self._cached_discovery(start_url, target)
            if cached is not None:
                return cached
        elif self.is_root_fully_indexed(start_url):
            # A background indexing pass revisiting an already-fully-indexed
            # root (e.g. a fresh app launch resuming it) has nothing left
            # to do -- the stored index already covers every date in it.
            entry = self._root_index[start_url]
            all_assets = [self._row_to_asset(row) for rows in entry['assets_by_date'].values() for row in rows]
            return RadarInventory(
                assets=sorted(all_assets, key=lambda a: (a.nominal_time or datetime.min.replace(tzinfo=timezone.utc), a.catalog_url)),
                scope=RadarInventory.scope + f'; root={start_url} (already fully indexed)',
            )
        result.scope += f'; root={start_url}'
        queue = deque([start_url])
        visited, assets = set(), {}
        requests = 0

        def _fetch_one(url):
            if url in self._catalogs:
                return url, self._catalogs[url], None
            try:
                html = self.fetch_catalog(url, cancel)
                # 404 at a listed child is unresolved, not an empty catalog.
                if not html:
                    return url, None, ValueError('Catalog returned 404')
                return url, parse_catalog(url, html), None
            except Exception as exc:  # noqa: BLE001 -- surfaced per-URL below, not raised here
                return url, None, exc

        with ThreadPoolExecutor(max_workers=self._CONCURRENCY) as pool:
            while queue:
                _check_cancel(cancel)
                batch = []
                while queue and len(batch) < self._CONCURRENCY:
                    url = queue[0]
                    if url in visited:
                        queue.popleft()
                        continue
                    if url not in self._catalogs and requests + len(batch) >= budget:
                        break   # leave it queued -- resumes on the next discover() call
                    batch.append(queue.popleft())
                if not batch:
                    break
                visited.update(batch)

                new_fetches = [u for u in batch if u not in self._catalogs]
                if new_fetches:
                    requests += len(new_fetches)
                    if cancel.wait(0.2):   # one pace per batch, not per request
                        _check_cancel(cancel)

                for url, links, exc in pool.map(_fetch_one, batch):
                    _check_cancel(cancel)
                    if exc is not None:
                        from archive.catalog import ScanCanceled
                        if isinstance(exc, ScanCanceled):
                            raise exc
                        result.errors.append(f'{url}: {exc}')
                        continue
                    self._catalogs[url] = links
                    result.catalogs_checked += 1
                    for child in sorted(links.catalogs, reverse=True):
                        if not child.startswith(url.rsplit('/', 1)[0] + '/') or child == url:
                            continue
                        if _excluded(child) or _dated_subdir_mismatch(child, target):
                            result.excluded.append(child)
                        else:
                            queue.append(child)
                    for child in sorted(links.datasets):
                        asset = asset_from_url(child)
                        if asset is None:
                            result.excluded.append(child)
                        elif target is None or asset.nominal_time is None or asset.nominal_time.date() <= target <= (asset.nominal_end or asset.nominal_time).date():
                            assets[child] = asset  # Identity is the locator, never the basename.

                if target is not None:
                    queue = deque(sorted(queue, key=lambda child: _year_priority(child, target)))
                result.assets = sorted(assets.values(), key=lambda a: (a.nominal_time or datetime.min.replace(tzinfo=timezone.utc), a.catalog_url))
                result.pending = len(queue)
                if progress:
                    progress(replace(result, assets=list(result.assets), errors=list(result.errors), excluded=list(result.excluded)))

        result.pending = len(queue)
        result.requests_made = requests
        # Only cache a decisive outcome: either matching assets were found
        # (same bar main_window.py's own startup retry loop already accepts
        # as "done"), or the crawl fully exhausted the tree with none found
        # (a genuine, confirmed "no NOXP this date" -- the common case).
        # An inconclusive partial-with-nothing-yet result is never cached,
        # so a resumed call still keeps making real progress instead of
        # replaying a stale empty answer.
        if target is not None and not result.errors and (result.assets or result.pending == 0):
            self._cache_discovery(start_url, target, result)
        # An unfiltered crawl (target=None) that reached the end of the
        # tree now knows every date in this root at once -- persist it so
        # *any* future date lookup here, including ones never directly
        # searched for, is an instant cache hit (see _cached_discovery).
        elif target is None and not result.errors and result.pending == 0:
            self._cache_root_index(start_url, result)
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
        from core.mem_probe import peak_rss_mb, log_delta
        from time import perf_counter
        t0 = perf_counter()
        rss_before = peak_rss_mb()
        cancel = cancel or Event()
        url = self.resolve(asset, cancel)
        path, provenance = download_asset(url, self.cache_dir, cancel, asset.name)
        volume = read_noxp(path, asset.format)
        volume.provenance.update(provenance, catalog_url=asset.catalog_url)
        log_delta(f"NoxpArchive.load {asset.name}", rss_before, peak_rss_mb(),
                   (perf_counter() - t0) * 1000.0)
        return volume


def _record_session(url, provenance):
    from core import provenance as session
    session.record("noxp radar" if "/NOXP/" in url else "raw lidar", url,
                   sha256=provenance.get("sha256"), size=provenance.get("bytes"))


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
            _record_session(url, provenance)
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
        _record_session(url, provenance)
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
