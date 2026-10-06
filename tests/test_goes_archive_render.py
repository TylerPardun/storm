"""Archive GOES frames: infrared kelvins shown as IR (band 13 had been clipped
to solid white as if it were reflectance), resampled straight onto the map."""
import numpy as np
from pyproj import Proj

from archive.fetchers.satellite_archive_fetcher import _goes_gray, _goes_to_map_image

H, LON0, A, B = 35786023.0, -75.0, 6378137.0, 6356752.31414


def test_infrared_kelvins_become_cold_white_warm_dark():
    g = _goes_gray(np.array([180.0, 190.0, 250.0, 310.0, 330.0, np.nan], dtype=np.float32), "K")
    assert g[0] == g[1] == 1.0 and g[3] == g[4] == 0.0 and 0.4 < g[2] < 0.6 and np.isnan(g[5])
    vis = _goes_gray(np.array([0.0, 0.25, 1.5]), "1")
    assert vis.tolist() == [0.0, 0.5, 1.0]


def test_each_map_pixel_takes_the_scan_pixel_under_it():
    # a synthetic scan whose values say which column they came from
    x = np.linspace(-0.10, 0.06, 1600)                 # radians, CONUS-like
    y = np.linspace(0.13, 0.04, 900)
    col_id = np.tile(np.arange(x.size, dtype=np.float32), (y.size, 1)) / (x.size - 1)
    bbox = [-116.0, 28.0, -82.0, 49.0]
    rgba = _goes_to_map_image(col_id, x, y, lon0=LON0, sat_height=H, semi_major=A, semi_minor=B, sweep="x",
                              bbox=bbox, width_px=400, height_px=300)
    assert rgba.shape == (300, 400, 4) and (rgba[..., 3] == 255).mean() > 0.95
    p = Proj(proj="geos", h=H, lon_0=LON0, a=A, b=B, sweep="x")
    for r, c in [(150, 200), (20, 30), (280, 380)]:
        lon = bbox[0] + (c + 0.5) * (bbox[2] - bbox[0]) / 400
        def merc(v): return np.log(np.tan(np.pi / 4 + np.deg2rad(v) / 2))
        ym = merc(bbox[3]) + (r + 0.5) * (merc(bbox[1]) - merc(bbox[3])) / 300
        lat = np.rad2deg(2 * np.arctan(np.exp(ym)) - np.pi / 2)
        xs = p(lon, lat)[0] / H
        expected = round((xs - x[0]) / (x[1] - x[0])) / (x.size - 1)
        assert abs(rgba[r, c, 0] - round(expected * 255)) <= 1


def test_off_the_disk_is_transparent():
    x = np.linspace(-0.02, 0.02, 50)
    y = np.linspace(0.02, -0.02, 50)
    rgba = _goes_to_map_image(np.ones((50, 50), np.float32), x, y, lon0=LON0, sat_height=H, semi_major=A,
                              semi_minor=B, sweep="x", bbox=[60.0, -10.0, 100.0, 10.0], width_px=40, height_px=20)
    assert (rgba[..., 3] == 0).all()                    # the other side of the Earth from GOES-East
