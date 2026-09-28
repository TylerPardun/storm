from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from archive.fetchers.raw_lidar_archive_fetcher import (
    RawLidarRays, RawLidarSource,
)

_WHEN = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


def _clamps_source(mobile=False):
    return RawLidarSource(
        platform_id="CLAMPS1-PPI", instrument="CLAMPS1",
        platform_dir="clamps/clamps1", datastream="clampsdlppiC1.b1",
        product="ppi", mobile=mobile,
    )


def _ppi_rays(source=None, n_rays=121, field="velocity", units="m/s"):
    source = source or _clamps_source()
    n_gates = 5
    az = np.linspace(240, 300, n_rays)
    # rays_at() only looks backward from `when`, so keep every synthetic ray
    # at or before it (spanning the last n_rays*0.5s, well inside the
    # window_seconds default of 90).
    times = np.array([(_WHEN - timedelta(seconds=(n_rays - 1 - i) * 0.5)).timestamp()
                       for i in range(n_rays)])
    data = np.ma.masked_invalid(
        np.tile(np.linspace(-15, 15, n_gates), (n_rays, 1)).astype(np.float32)
    )
    return RawLidarRays(
        source=source,
        time_epoch=times,
        distance_m=np.array([75.0, 150.0, 225.0, 300.0, 375.0]),
        distance_kind="range",
        azimuth_deg=az,
        elevation_deg=np.full(n_rays, 2.0),
        latitude=np.full(n_rays, 36.0),
        longitude=np.full(n_rays, -97.0),
        altitude_m=np.full(n_rays, 300.0),
        heading_deg=np.full(n_rays, np.nan),
        scan_number=np.ones(n_rays),
        fields={field: {"data": data, "units": units}},
        housekeeping={},
        coordinate_source=np.full(n_rays, "file site attributes"),
        position_time_epoch=times,
        provenance={"azimuth_metadata": {"comment": "0 degrees is north"}},
        warnings=[],
    )


from io import BytesIO
from PIL import Image
from ui.map.lidar_overlay import render_lidar_to_png


def _render(rays, when=_WHEN):
    png, bounds, metadata = render_lidar_to_png(rays, when, grid_size=256)
    return np.asarray(Image.open(BytesIO(png))), bounds, metadata


def test_mobile_north_angles_map_westward_sector_without_heading():
    pixels, bounds, meta = _render(_ppi_rays(_clamps_source(mobile=True)))
    assert bounds[2] < -97  # every gate is west of the instrument
    assert bounds[0] > -97.01
    assert 0.2 < (pixels[:, :, 3] > 0).mean() < 0.8
    assert meta['rays'] == 121
    assert meta['units'] == 'm/s'
    assert meta['vmin'] == -meta['vmax']
    assert meta['additional_velocity_motion_correction'] is False


def test_unknown_mobile_reference_is_refused():
    rays = _ppi_rays(_clamps_source(mobile=True))
    rays.provenance = {}
    with pytest.raises(ValueError, match='north-referenced'):
        _render(rays)


def test_single_pointing_is_a_line_not_a_filled_sector():
    rays = _ppi_rays()
    rays.azimuth_deg[:] = 0
    pixels, bounds, meta = _render(rays)
    assert bounds[1] > 36  # north starts at the first range gate
    assert 0 < (pixels[:, :, 3] > 0).mean() < .02


def test_latest_scan_and_clock_limit_acquisition():
    rays = _ppi_rays()
    rays.scan_number[-10:] = 2
    _, _, meta = _render(rays, _WHEN - timedelta(seconds=2))
    assert meta['rays'] == 6
    assert datetime.fromisoformat(meta['end']) == _WHEN - timedelta(seconds=2)


def test_large_time_gap_does_not_join_scans_with_same_number():
    rays = _ppi_rays()
    rays.time_epoch[:-1] -= 30
    _, _, meta = _render(rays)
    assert meta['rays'] == 1


def test_masked_gates_are_transparent():
    rays = _ppi_rays()
    rays.fields['velocity']['data'][:] = np.ma.masked
    pixels, _, _ = _render(rays)
    assert not pixels[:, :, 3].any()


@pytest.mark.parametrize('change,match', [('height','slant range'), ('stale','recent'), ('field','No velocity')])
def test_invalid_inputs(change, match):
    rays = _ppi_rays()
    if change == 'height':
        rays.distance_kind = 'height'
    elif change == 'stale':
        rays.time_epoch -= 3600
    else:
        rays.fields = {}
    with pytest.raises(ValueError, match=match):
        _render(rays)


def test_north_crossing_stays_north_and_does_not_fill_south():
    rays = _ppi_rays()
    rays.azimuth_deg = np.linspace(350, 370, 121) % 360
    pixels, bounds, _ = _render(rays)
    assert bounds[1] > 36
    assert 0 < (pixels[:, :, 3] > 0).mean() < .5


def test_moving_origins_move_geographic_coverage():
    rays = _ppi_rays()
    _, original, _ = _render(rays)
    rays.longitude += .1
    _, moved, _ = _render(rays)
    assert moved[0] - original[0] == pytest.approx(.1, abs=1e-6)
    assert moved[2] - original[2] == pytest.approx(.1, abs=1e-6)


def test_superseded_render_cannot_inject_and_pending_time_is_rendered():
    # Exercise the real UI callback without constructing the full application.
    from types import SimpleNamespace
    from unittest.mock import Mock
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'ui/app/main_window.py').read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MainWindow')
    callback = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_on_lidar_overlay_render_ready')
    namespace = {}
    exec(compile(ast.Module(body=[callback], type_ignores=[]), '<callback>', 'exec'), namespace)
    overlay = Mock()
    window = SimpleNamespace(
        _lidar_overlay_render_in_flight=True,
        _lidar_instrument='DLTRUCK1',
        raw_lidar_controls=SimpleNamespace(map_is_on=lambda: True),
        _lidar_overlay_generation=3,
        _lidar_overlay=overlay,
        _lidar_overlay_pending=True,
        _render_lidar_overlay=Mock(),
    )
    namespace['_on_lidar_overlay_render_ready'](window, {'generation': 2, 'png': b'old', 'bounds': []})
    overlay.inject.assert_not_called()
    window._render_lidar_overlay.assert_called_once()
    assert not window._lidar_overlay_render_in_flight


def test_instrument_location_expires_across_gaps_and_uses_latest_position():
    from ui.map.lidar_overlay import lidar_site_at
    rays = _ppi_rays()
    rays.latitude[-1] = 35
    site = lidar_site_at(rays, _WHEN)
    assert site == {'instrument': 'CLAMPS1', 'lat': 35.0, 'lon': -97.0}
    assert lidar_site_at(rays, _WHEN + timedelta(minutes=2)) is None


def test_sector_and_lidar_projection_share_north_clockwise_bearings():
    from core.scan_sector import _project
    from pyproj import Geod
    geod = Geod(ellps='WGS84')
    for az in (240, 270, 300):
        lat, lon = _project(38.94633, -97.214, az, 8000)
        bearing, _, distance = geod.inv(-97.214, 38.94633, lon, lat)
        assert bearing % 360 == pytest.approx(az, abs=.2)
        assert distance == pytest.approx(8000, abs=25)


def test_all_native_fields_render_with_their_own_units():
    rays = _ppi_rays(field='backscatter', units='km^-1 sr^-1')
    _, _, metadata = render_lidar_to_png(rays, _WHEN, 'backscatter', 256)
    assert metadata['field'] == 'backscatter'
    assert metadata['units'] == 'km^-1 sr^-1'
    assert metadata['vmin'] < metadata['vmax']


def test_declared_fixed_site_persists_between_scans_but_not_outside_file():
    from ui.map.lidar_overlay import lidar_site_at
    rays = _ppi_rays()
    rays.time_epoch[:-1] -= 600
    rays.provenance['metadata'] = {'Site_latitude': '34.9822433', 'Site_longitude': '-97.5200901'}
    site = lidar_site_at(rays, _WHEN - timedelta(minutes=2))
    assert site['lat'] == pytest.approx(34.9822433)
    assert lidar_site_at(rays, _WHEN + timedelta(minutes=2)) is None
    rays.source = _clamps_source(mobile=True)
    assert lidar_site_at(rays, _WHEN - timedelta(minutes=2)) is None
