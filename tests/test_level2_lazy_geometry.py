"""Archive Level-2 scans compute gate positions only where the renderer needs them."""
from datetime import datetime, timezone

import numpy as np

from core.level2_radar_scan import Level2RadarScan
from ui.map.radar_overlay import ARCHIVE_SUPERRES_CROP_RADIUS_M, render_scan_to_png


def _scan(lazy=True):
    az = np.arange(0.25, 360, 0.5)
    ranges = 2.125 + 0.25 * np.arange(400)
    data = np.tile(np.linspace(0, 60, 400, dtype=np.float32), (len(az), 1))
    scan = Level2RadarScan(
        site="KTLX", product="REF", scan_time=datetime(2013, 5, 31, 23, 23, tzinfo=timezone.utc),
        data=data, lats=None, lons=None, vmin=-32.0, vmax=90.0, units="dBZ", colormap="nws_ref",
        tilt_deg=0.5, available_tilts=[0.5], available_products=["reflectivity"], pyart_field="reflectivity",
        az_offset=float(az[0]), gate_geometry=(35.333, -97.278, az, ranges))
    if not lazy:
        _ = scan.lats                       # the full grids, as every scan carried before
    return scan


def test_subsets_match_the_full_grids():
    lazy, full = _scan(), _scan(lazy=False)
    for got, want in zip(lazy.first_ring(), (full.lats[:, 0], full.lons[:, 0])):
        assert np.array_equal(got, want)
    edge_lats, edge_lons = lazy.extent_points()
    assert edge_lats.min() == full.lats.min() and edge_lats.max() == full.lats.max()
    assert edge_lons.min() == full.lons.min() and edge_lons.max() == full.lons.max()
    assert lazy._lats is None               # none of that built the full grids


def test_rendering_never_builds_the_full_grids_and_bounds_are_unchanged():
    lazy, full = _scan(), _scan(lazy=False)
    for crop in (None, ARCHIVE_SUPERRES_CROP_RADIUS_M):
        _, lazy_bounds, _ = render_scan_to_png(lazy, grid_size=256, crop_radius_m=crop)
        _, full_bounds, _ = render_scan_to_png(full, grid_size=256, crop_radius_m=crop)
        assert np.allclose(lazy_bounds, full_bounds)
    assert lazy._lats is None


def test_full_grids_are_still_available_on_request():
    scan = _scan()
    assert scan.lats.shape == scan.data.shape == scan.lons.shape
    assert scan.lats.dtype == np.float32
