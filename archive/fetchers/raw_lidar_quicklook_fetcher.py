"""Qt-facing archive fetcher wrapper for CLAMPS raw scanning-lidar
quicklooks. Thin by design: archive/fetchers/raw_lidar_archive_fetcher.py's
discover_raw_lidar()/load_raw_lidar() already do the real work, including
the mobile DL Truck's GPS backfill (archive/positions.py). This module
only adds the background-thread/signal plumbing so MainWindow can drive it
without blocking the UI, following ArchiveClampsWindFetcher's pattern.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from archive.fetchers.raw_lidar_archive_fetcher import (
    KNOWN_RAW_LIDAR_SOURCES, discover_raw_lidar, load_raw_lidar, read_site,
)

log = logging.getLogger(__name__)

_RAW_LIDAR_CACHE_DIR = Path.home() / ".cache" / "storm" / "raw_lidar"


def _remote_size(url: str) -> int | None:
    """A file's size from a HEAD request, or None if it can't be told."""
    from urllib.request import Request, urlopen
    import config
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
        with urlopen(request, timeout=20, context=config.NSSL_SSL_CONTEXT) as response:
            length = response.headers.get("Content-Length")
        return int(length) if length else None
    except Exception:  # noqa: BLE001
        return None


class ArchiveRawLidarQuicklookFetcher(QObject):
    """Discovers raw-lidar assets for every known source on an archive
    date, and loads one selected asset's rays for the quicklook dialog."""

    assets_ready = pyqtSignal(dict)   # platform_id -> list[LidarAsset]
    sites_ready = pyqtSignal(dict)    # instrument -> {lat, lon, altitude_m, description} (trailers)
    # load_instrument: one lidar's files for the session, loaded one by one
    instrument_file_ready = pyqtSignal(str, object)       # instrument, RawLidarRays (with .scans)
    instrument_large_file = pyqtSignal(str, object, int)  # instrument, LidarAsset, bytes -- not loaded unasked
    instrument_loaded = pyqtSignal(str, int)              # instrument, files loaded

    # A lidar's files load by themselves when it's chosen, except ones this
    # big (a trailer's day of vertical stares can be 355 MB), which wait for
    # the user to ask -- unless already downloaded.
    AUTO_LOAD_MAX_BYTES = 150 * 1024 * 1024
    # scan files first (small, and what the map shows), stares last
    _LOAD_ORDER = {"ppi": 0, "other": 1, "csm": 2, "fp": 3}
    rays_ready = pyqtSignal(str, object)  # platform_id, RawLidarRays
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._busy = False             # discovery running
        # loads: one runs, the latest request waits; older requests are
        # replaced, and a superseded load's result is dropped, not shown
        self._load_running = False
        self._load_pending = None
        self._load_seq = 0
        self._latest_load = 0

    def fetch(self, archive_date: datetime) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True

        def run():
            try:
                self._do_fetch(archive_date)
            finally:
                with self._lock:
                    self._busy = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def _do_fetch(self, archive_date: datetime) -> None:
        """The real discovery logic, directly callable (no thread) for tests."""
        day = archive_date.date() if isinstance(archive_date, datetime) else archive_date
        # The lidars run continuously and their files are daily, so the next
        # day's files are offered too: the session runs into that morning
        # (archive/session.py).
        days = (day, day + timedelta(days=1))
        results: dict[str, list | None] = {}
        errors: list[str] = []
        for source in KNOWN_RAW_LIDAR_SOURCES:
            assets = []
            try:
                for n, d in enumerate(days):
                    try:
                        assets += discover_raw_lidar(source, d) or []
                    except Exception:
                        if n == 0:
                            raise
            except Exception as exc:  # noqa: BLE001
                results[source.platform_id] = None
                errors.append(f"{source.platform_id}: {exc}")
                continue
            if assets:
                results[source.platform_id] = assets
        if errors:
            self.error.emit("; ".join(errors))
        self.assets_ready.emit(results)
        # Where each trailer was, from one of its files' header, so it can be
        # shown on the map before any file is loaded. (The truck moves; its
        # position comes from its GPS track instead.)
        sites = {}
        for source in KNOWN_RAW_LIDAR_SOURCES:
            if source.mobile or source.instrument in sites or not results.get(source.platform_id):
                continue
            site = read_site(results[source.platform_id][0])
            if site is not None:
                sites[source.instrument] = site
        if sites:
            self.sites_ready.emit(sites)

    def load_instrument(self, instrument: str, assets, force_urls=()) -> None:
        """Load every file of one lidar (a replaced selection stops the old
        one). Files over AUTO_LOAD_MAX_BYTES that aren't cached are reported
        with instrument_large_file instead, unless their URL is in force_urls."""
        with self._lock:
            self._load_seq += 1
            self._latest_load = seq = self._load_seq
        ordered = sorted(assets, key=lambda a: (self._LOAD_ORDER.get(a.source.product, 9), a.filename))
        threading.Thread(target=self._load_instrument, args=(instrument, ordered, set(force_urls), seq),
                         daemon=True).start()

    def _load_instrument(self, instrument, assets, force_urls, seq) -> None:
        from core.lidar_scans import classify_scans
        loaded = 0
        for asset in assets:
            if self._is_superseded(seq):
                return
            if asset.url not in force_urls and not self._cached(asset):
                size = _remote_size(asset.url)
                if size is not None and size > self.AUTO_LOAD_MAX_BYTES:
                    self.instrument_large_file.emit(instrument, asset, size)
                    continue
            try:
                rays = load_raw_lidar(asset, _RAW_LIDAR_CACHE_DIR)
                rays.scans = classify_scans(rays)
            except Exception as exc:  # noqa: BLE001
                log.warning("Lidar %s: %s failed to load: %s", instrument, asset.filename, exc)
                if not self._is_superseded(seq):
                    self.error.emit(f"Lidar {asset.filename}: {exc}")
                continue
            if self._is_superseded(seq):
                return
            loaded += 1
            self.instrument_file_ready.emit(instrument, rays)
        if not self._is_superseded(seq):
            self.instrument_loaded.emit(instrument, loaded)

    @staticmethod
    def _cached(asset) -> bool:
        import hashlib
        key = hashlib.sha256(asset.url.encode()).hexdigest()
        path = _RAW_LIDAR_CACHE_DIR / (key + Path(asset.filename).suffix)
        return path.exists() and path.with_suffix(path.suffix + ".json").exists()

    def load(self, platform_id: str, asset) -> bool:
        """Load an asset's rays. While another load runs, this becomes the
        next one (replacing any request still waiting). Always accepted."""
        with self._lock:
            self._load_seq += 1
            self._latest_load = self._load_seq
            request = (self._load_seq, platform_id, asset)
            if self._load_running:
                self._load_pending = request
                return True
            self._load_running = True
        threading.Thread(target=self._load_loop, args=(request,), daemon=True).start()
        return True

    def _load_loop(self, request) -> None:
        while request is not None:
            seq, platform_id, asset = request
            try:
                self._do_load(platform_id, asset, seq)
            finally:
                with self._lock:
                    request, self._load_pending = self._load_pending, None
                    if request is None:
                        self._load_running = False

    def _is_superseded(self, seq) -> bool:
        with self._lock:
            return seq is not None and seq != self._latest_load

    def _do_load(self, platform_id: str, asset, seq=None) -> None:
        try:
            rays = load_raw_lidar(asset, _RAW_LIDAR_CACHE_DIR)
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveRawLidarQuicklookFetcher: load failed for %s: %s", asset.filename, exc)
            if not self._is_superseded(seq):
                self.error.emit(f"Raw lidar load failed: {exc}")
            return
        if self._is_superseded(seq):
            log.debug("ArchiveRawLidarQuicklookFetcher: dropping superseded load of %s", asset.filename)
            return
        from core.lidar_scans import classify_scans
        try:
            scans = classify_scans(rays)            # the scans the file holds, by geometry
        except Exception as exc:  # noqa: BLE001
            log.warning("Lidar scans could not be classified for %s: %s", asset.filename, exc)
            scans = []
        try:
            rays.scans = scans
        except AttributeError:
            pass
        self.rays_ready.emit(platform_id, rays)
