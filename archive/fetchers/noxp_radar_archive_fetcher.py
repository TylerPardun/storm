"""NOXP mobile-radar archive fetcher: Qt-facing wrapper around
archive/fetchers/noxp_archive_fetcher.py's NoxpArchive, plus the
sweep-extraction/georeferencing step that turns one decoded RadarVolume
sweep into a map-renderable core.noxp_radar_scan.NoxpRadarScan.

Discovery is scoped per platform (campaign root), not looped across every
registered NOXP platform on every call: each campaign root is its own
bounded THREDDS crawl (unlike the flat, single-request catalogs the other
archive fetchers loop over), so discovering across all ~10 registered
roots for one date would cost on the order of a minute, not a second --
confirmed live against the real server while wiring this up (a single
bounded crawl of one root alone took several seconds even scoped to a
handful of subcatalogs). The UI is expected to let the user pick a
platform first (a static, no-network choice -- ALL_PLATFORMS already
knows every NOXP campaign root) and only then trigger discovery for it.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.noxp_radar_scan import NoxpRadarScan, field_meta

log = logging.getLogger(__name__)

_NOXP_CACHE_DIR = Path.home() / ".cache" / "storm" / "noxp"
_DISCOVERY_BUDGET = 40  # per platform, per click -- see module docstring


def noxp_volume_to_scan(volume, sweep_index: int, field_name: str) -> NoxpRadarScan:
    """Slice one sweep out of a decoded RadarVolume and georeference it via
    an aeqd projection centered on the volume's own file-declared position
    (NOXP is mobile -- there is no fixed site table to fall back on, unlike
    NEXRAD's radar_archive_fetcher.py, which this otherwise mirrors).

    Raises ValueError for anything that isn't safe to map-render: an
    unconfirmed-geometry volume (WDSS-II RHI), an out-of-range sweep index,
    a field the volume doesn't have, or missing azimuth/position data --
    never silently renders unverified geometry.
    """
    from pyproj import Proj, Transformer

    if volume.scan_type != "ppi":
        raise ValueError(
            f"Cannot map-render a {volume.scan_type!r} volume -- only confirmed PPI "
            "geometry is rendered (RHI/unverified-angle volumes can still be inspected, "
            "just not overlaid on the map)"
        )
    n_sweeps = int(volume.sweep_start.size)
    if not (0 <= sweep_index < n_sweeps):
        raise ValueError(f"sweep_index {sweep_index} out of range (volume has {n_sweeps} sweeps)")
    if field_name not in volume.fields:
        raise ValueError(f"Field {field_name!r} not present in this volume (have: {sorted(volume.fields)})")

    start = int(volume.sweep_start[sweep_index])
    end = int(volume.sweep_end[sweep_index]) + 1
    az = np.asarray(volume.azimuth_deg[start:end], dtype=np.float64)
    el = np.asarray(volume.elevation_deg[start:end], dtype=np.float64)
    times = np.asarray(volume.time_epoch[start:end], dtype=np.float64)
    data = volume.fields[field_name]["data"][start:end]
    range_m = volume.range_m[start:end] if np.ndim(volume.range_m) == 2 else volume.range_m

    finite_az = np.isfinite(az)
    if not finite_az.any():
        raise ValueError("Sweep has no usable azimuth angles")

    order = np.argsort(az)
    az, data = az[order], data[order]
    if np.ndim(range_m) == 2:
        range_m = range_m[order]

    lat0 = float(np.nanmean(volume.latitude))
    lon0 = float(np.nanmean(volume.longitude))
    if not (np.isfinite(lat0) and np.isfinite(lon0)):
        raise ValueError("Volume has no usable radar location")

    az_rad = np.deg2rad(az)
    range_km = np.asarray(range_m, dtype=np.float64) / 1000.0
    if range_km.ndim == 1:
        x_km = np.outer(np.sin(az_rad), range_km)
        y_km = np.outer(np.cos(az_rad), range_km)
    else:
        x_km = np.sin(az_rad)[:, None] * range_km
        y_km = np.cos(az_rad)[:, None] * range_km

    aeqd = Proj(proj="aeqd", lat_0=lat0, lon_0=lon0, datum="WGS84", units="km")
    xform = Transformer.from_proj(aeqd, Proj("epsg:4326"), always_xy=True)
    lons, lats = xform.transform(x_km, y_km)

    def sweep_elevation(i):
        s, e = int(volume.sweep_start[i]), int(volume.sweep_end[i]) + 1
        sweep_el = np.asarray(volume.elevation_deg[s:e], dtype=np.float64)
        return float(np.nanmean(sweep_el)) if np.isfinite(sweep_el).any() else float("nan")

    meta = field_meta(field_name)
    finite_times = times[np.isfinite(times)]
    scan_time = (
        datetime.fromtimestamp(float(np.nanmean(finite_times)), timezone.utc)
        if finite_times.size else datetime.fromtimestamp(0, timezone.utc)
    )
    return NoxpRadarScan(
        site="NOXP",
        product=meta["label"],
        scan_time=scan_time,
        data=np.ma.filled(np.ma.masked_invalid(data), np.nan).astype(np.float32),
        lats=lats.astype(np.float32),
        lons=lons.astype(np.float32),
        vmin=meta["vmin"], vmax=meta["vmax"], units=meta["units"], colormap=meta["colormap"],
        sweep_index=sweep_index,
        elevation_deg=sweep_elevation(sweep_index),
        available_sweeps=[(i, sweep_elevation(i)) for i in range(n_sweeps)],
        native_field=field_name,
    )


class ArchiveNoxpRadarFetcher(QObject):
    """Discovers and loads NOXP volumes for one archive-mode platform/date
    at a time, off the Qt main thread."""

    assets_ready = pyqtSignal(str, object)   # platform_id, list[RadarAsset]
    volume_loaded = pyqtSignal(str, object)  # platform_id, RadarVolume
    error = pyqtSignal(str)

    def __init__(self, parent=None, noxp_factory=None):
        super().__init__(parent)
        self._busy = False
        self._lock = threading.Lock()
        # Overridable so tests can substitute a fake NoxpArchive instead of
        # hitting the network; defaults to the real thing.
        self._noxp_factory = noxp_factory or self._default_noxp

    @staticmethod
    def _default_noxp():
        from archive.fetchers.noxp_archive_fetcher import NoxpArchive
        return NoxpArchive(_NOXP_CACHE_DIR)

    def _start(self, target) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._run, args=(target,), daemon=True).start()
        return True

    def _run(self, target) -> None:
        try:
            target()
        finally:
            with self._lock:
                self._busy = False

    def discover(self, platform_id: str, catalog_root: str, archive_date: datetime) -> bool:
        """Bounded crawl of one platform's campaign root for one date."""
        return self._start(lambda: self._do_discover(platform_id, catalog_root, archive_date))

    def _do_discover(self, platform_id: str, catalog_root: str, archive_date: datetime) -> None:
        """The real discovery logic, directly callable (no thread) for tests.

        NoxpArchive.discover() catches its own per-request failures into
        inventory.errors/inventory.pending rather than raising -- it always
        returns normally, even when it found nothing because every request
        failed (e.g. a transient WAF timeout under heavy concurrent archive
        startup load, observed directly while wiring this up). Emitting a
        bare empty assets_ready in that case would be indistinguishable
        from a genuine "no NOXP data this date," which this codebase is
        careful never to claim on an unmarked/failed check (see
        archive/catalog.py's module docstring). So: always emit whatever
        real assets were found, and separately surface incompleteness.
        """
        archive = self._noxp_factory()
        try:
            inventory = archive.discover(
                target=archive_date.date() if isinstance(archive_date, datetime) else archive_date,
                budget=_DISCOVERY_BUDGET, catalog_root=catalog_root,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveNoxpRadarFetcher: discovery failed for %s: %s", platform_id, exc)
            self.error.emit(f"NOXP discovery failed: {exc}")
            return
        self.assets_ready.emit(platform_id, inventory.assets)
        if inventory.errors or inventory.pending:
            # Full URLs/exception text go to the log; the on-screen status
            # stays short so it reads at a glance instead of wrapping to
            # several lines of raw request detail.
            if inventory.errors:
                reason = inventory.errors[0].split(": ", 1)[-1]
                if len(inventory.errors) > 1:
                    reason += f" (+{len(inventory.errors) - 1} more)"
            else:
                reason = f"{inventory.pending} subcatalog(s) unchecked"
            note = (
                f"NOXP search incomplete ({reason}) -- "
                f"found {len(inventory.assets)} so far, more may exist; try again"
            )
            log.warning(
                "ArchiveNoxpRadarFetcher: search for %s was incomplete -- errors=%s pending=%s found=%d",
                platform_id, inventory.errors, inventory.pending, len(inventory.assets),
            )
            self.error.emit(note)

    def load_volume(self, platform_id: str, asset) -> bool:
        return self._start(lambda: self._do_load_volume(platform_id, asset))

    def _do_load_volume(self, platform_id: str, asset) -> None:
        archive = self._noxp_factory()
        try:
            volume = archive.load(asset)
        except Exception as exc:  # noqa: BLE001
            log.warning("ArchiveNoxpRadarFetcher: load failed for %s: %s", asset.name, exc)
            self.error.emit(f"NOXP volume load failed: {exc}")
            return
        self.volume_loaded.emit(platform_id, volume)
