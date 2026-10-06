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
    # survey() / load_files(): each lidar's PPI and CSM files, loaded one by one
    instrument_file_ready = pyqtSignal(str, object)       # instrument, RawLidarRays (with .scans)
    instrument_large_file = pyqtSignal(str, object, int)  # instrument, LidarAsset, bytes -- not loaded unasked
    instrument_loaded = pyqtSignal(str, int)              # instrument, files loaded
    survey_done = pyqtSignal()                            # survey(): every lidar's files looked at

    # The survey skips files this big unless already downloaded (a memory
    # guard); they load once their lidar is chosen.
    AUTO_LOAD_MAX_BYTES = 150 * 1024 * 1024
    _LOAD_ORDER = {"ppi": 0, "csm": 1}
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._busy = False             # discovery running

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

    def survey(self, assets_by_instrument: dict) -> None:
        """Load every lidar's files for the session in the background (one
        lidar after another), so where and when each scanned is known before
        anything is chosen. Large files that aren't cached are reported with
        instrument_large_file instead (load_files fetches them later)."""
        def run():
            for instrument, assets in assets_by_instrument.items():
                self._load_files(instrument, assets, force=False)
            self.survey_done.emit()
        threading.Thread(target=run, daemon=True).start()

    def load_files(self, instrument: str, assets) -> None:
        """Load these files of one lidar now, whatever their size."""
        threading.Thread(target=self._load_files, args=(instrument, list(assets), True), daemon=True).start()

    def _load_files(self, instrument, assets, force) -> None:
        from core.lidar_scans import classify_scans
        loaded = 0
        for asset in sorted(assets, key=lambda a: (self._LOAD_ORDER.get(a.source.product, 9), a.filename)):
            if not force and not self._cached(asset):
                size = _remote_size(asset.url)
                if size is not None and size > self.AUTO_LOAD_MAX_BYTES:
                    self.instrument_large_file.emit(instrument, asset, size)
                    continue
            try:
                rays = load_raw_lidar(asset, _RAW_LIDAR_CACHE_DIR)
                rays.scans = classify_scans(rays)
            except Exception as exc:  # noqa: BLE001
                log.warning("Lidar %s: %s failed to load: %s", instrument, asset.filename, exc)
                self.error.emit(f"Lidar {asset.filename}: {exc}")
                continue
            if not rays.scans:
                # only wind-profile (VAD) rings or other non-PPI rays: nothing to
                # draw, so don't keep it (a day of these held hundreds of MB)
                log.info("Lidar %s: %s has no PPI scans; not kept", instrument, asset.filename)
                continue
            loaded += 1
            self.instrument_file_ready.emit(instrument, rays)
        self.instrument_loaded.emit(instrument, loaded)

    @staticmethod
    def _cached(asset) -> bool:
        import hashlib
        key = hashlib.sha256(asset.url.encode()).hexdigest()
        path = _RAW_LIDAR_CACHE_DIR / (key + Path(asset.filename).suffix)
        return path.exists() and path.with_suffix(path.suffix + ".json").exists()
