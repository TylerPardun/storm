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
    KNOWN_RAW_LIDAR_SOURCES, discover_raw_lidar, load_raw_lidar,
)

log = logging.getLogger(__name__)

_RAW_LIDAR_CACHE_DIR = Path.home() / ".cache" / "storm" / "raw_lidar"


class ArchiveRawLidarQuicklookFetcher(QObject):
    """Discovers raw-lidar assets for every known source on an archive
    date, and loads one selected asset's rays for the quicklook dialog."""

    assets_ready = pyqtSignal(dict)   # platform_id -> list[LidarAsset]
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
        self.rays_ready.emit(platform_id, rays)
