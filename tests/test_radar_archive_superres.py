
from datetime import datetime, timezone

import numpy as np
import pytest

from core.level2_radar_scan import Level2RadarScan
from ui.map.radar_overlay import _lonlat_to_merc, _merc_to_lonlat, render_scan_to_png
from ui.app.radar_superres_cache import RadarSuperresCache


def _make_scan(site="KTLX", radar_lat=35.333, radar_lon=-97.278):
    # small synthetic PPI: 8 azimuths x 6 gates, gate spacing ~250m super-res.
    num_az, num_rng = 8, 6
    az = np.linspace(0.0, 360.0, num_az, endpoint=False)
    gate_width_deg = 0.0025  # crude lon-degree step per gate for a synthetic grid
    lats = np.full((num_az, num_rng), radar_lat, dtype=float)
    lons = np.zeros((num_az, num_rng), dtype=float)
    for i in range(num_az):
        for j in range(num_rng):
            # push points outward along longitude only — good enough for a
            # synthetic fixture; real geometry isn't the thing under test here.
            lons[i, j] = radar_lon + (j + 1) * gate_width_deg
            lats[i, j] = radar_lat + (i - num_az / 2) * 0.001
    data = np.random.default_rng(0).uniform(-10, 60, size=(num_az, num_rng))
    return Level2RadarScan(
        site=site,
        product="REF",
        scan_time=datetime(2013, 5, 31, 23, 0, tzinfo=timezone.utc),
        data=data,
        lats=lats,
        lons=lons,
        vmin=-32.0,
        vmax=90.0,
        units="dBZ",
        colormap="nws_ref",
        tilt_deg=0.5,
        available_tilts=[0.5, 1.5],
        available_products=["reflectivity"],
        pyart_field="reflectivity",
    )


def test_render_scan_to_png_default_crop_matches_full_extent():
    scan = _make_scan()
    png_no_crop, bounds_no_crop, _ = render_scan_to_png(scan, grid_size=32)
    png_explicit_none, bounds_explicit_none, _ = render_scan_to_png(
        scan, grid_size=32, crop_radius_m=None
    )
    assert png_no_crop == png_explicit_none
    assert bounds_no_crop == bounds_explicit_none

    # sanity: bounds should bracket the synthetic scan's own lon/lat extent
    # (allow a hair of float slop from the Mercator round-trip).
    west, south, east, north = bounds_no_crop
    tol = 1e-6
    assert west <= float(scan.lons.min()) + tol
    assert east >= float(scan.lons.max()) - tol
    assert south <= float(scan.lats.min()) + tol
    assert north >= float(scan.lats.max()) - tol


def test_render_scan_to_png_crop_radius_centers_on_radar_site():
    scan = _make_scan()
    radius_m = 50_000.0
    png_bytes, bounds, elapsed_ms = render_scan_to_png(
        scan, grid_size=32, crop_radius_m=radius_m
    )
    assert isinstance(png_bytes, bytes) and len(png_bytes) > 0
    assert elapsed_ms >= 0.0

    radar_lat = float(scan.lats[:, 0].mean())
    radar_lon = float(scan.lons[:, 0].mean())
    cx, cy = _lonlat_to_merc(radar_lon, radar_lat)

    west, south, east, north = bounds
    x_min, y_min = _lonlat_to_merc(west, south)
    x_max, y_max = _lonlat_to_merc(east, north)

    assert x_min == pytest.approx(float(cx) - radius_m, abs=1.0)
    assert x_max == pytest.approx(float(cx) + radius_m, abs=1.0)
    assert y_min == pytest.approx(float(cy) - radius_m, abs=1.0)
    assert y_max == pytest.approx(float(cy) + radius_m, abs=1.0)

    # the crop is a square in Web Mercator meters.
    assert (x_max - x_min) == pytest.approx(y_max - y_min, abs=1.0)


def test_render_scan_to_png_crop_radius_independent_of_scan_extent():
    """A tiny crop radius produces tight bounds regardless of the synthetic
    scan's own (much larger, in this fixture) lon/lat extent."""
    scan = _make_scan()
    png_bytes, bounds, _ = render_scan_to_png(scan, grid_size=16, crop_radius_m=200.0)
    west, south, east, north = bounds
    assert (east - west) < (float(scan.lons.max()) - float(scan.lons.min()))
    assert (north - south) < (float(scan.lats.max()) - float(scan.lats.min()))


# --- RadarSuperresCache -----------------------------------------------------


def test_cache_hit_and_miss():
    cache = RadarSuperresCache(capacity=3)
    key = RadarSuperresCache.make_key("KTLX", "reflectivity", 0.5, datetime(2013, 5, 31, 23, 0))
    assert cache.get(key) is None
    cache.put(key, b"png-bytes", [1, 2, 3, 4])
    hit = cache.get(key)
    assert hit == (b"png-bytes", [1, 2, 3, 4])


def test_cache_evicts_oldest_at_capacity():
    cache = RadarSuperresCache(capacity=2)
    t = datetime(2013, 5, 31, 23, 0)
    k1 = RadarSuperresCache.make_key("KTLX", "reflectivity", 0.5, t)
    k2 = RadarSuperresCache.make_key("KTLX", "reflectivity", 1.5, t)
    k3 = RadarSuperresCache.make_key("KTLX", "reflectivity", 2.5, t)

    cache.put(k1, b"a", [])
    cache.put(k2, b"b", [])
    assert len(cache) == 2

    cache.put(k3, b"c", [])
    assert len(cache) == 2
    assert cache.get(k1) is None  # oldest evicted
    assert cache.get(k2) is not None
    assert cache.get(k3) is not None


def test_cache_key_includes_station_so_different_stations_dont_collide():
    t = datetime(2013, 5, 31, 23, 0)
    k_ktlx = RadarSuperresCache.make_key("KTLX", "reflectivity", 0.5, t)
    k_koun = RadarSuperresCache.make_key("KOUN", "reflectivity", 0.5, t)
    assert k_ktlx != k_koun

    cache = RadarSuperresCache(capacity=5)
    cache.put(k_ktlx, b"ktlx-png", [])
    cache.put(k_koun, b"koun-png", [])
    assert cache.get(k_ktlx) == (b"ktlx-png", [])
    assert cache.get(k_koun) == (b"koun-png", [])


def test_cache_get_marks_entry_most_recently_used():
    cache = RadarSuperresCache(capacity=2)
    t = datetime(2013, 5, 31, 23, 0)
    k1 = RadarSuperresCache.make_key("KTLX", "reflectivity", 0.5, t)
    k2 = RadarSuperresCache.make_key("KTLX", "reflectivity", 1.5, t)
    k3 = RadarSuperresCache.make_key("KTLX", "reflectivity", 2.5, t)

    cache.put(k1, b"a", [])
    cache.put(k2, b"b", [])
    cache.get(k1)  # k1 is now most-recently-used; k2 is now the oldest
    cache.put(k3, b"c", [])

    assert cache.get(k2) is None  # k2 evicted, not k1
    assert cache.get(k1) is not None
    assert cache.get(k3) is not None
