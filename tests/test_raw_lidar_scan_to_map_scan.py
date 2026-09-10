from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from archive.fetchers.raw_lidar_archive_fetcher import (
    RawLidarRays, RawLidarSource, raw_lidar_scan_to_map_scan,
)

_WHEN = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


def _clamps_source(mobile=False):
    return RawLidarSource(
        platform_id="CLAMPS1-PPI", instrument="CLAMPS1",
        platform_dir="clamps/clamps1", datastream="clampsdlppiC1.b1",
        product="ppi", mobile=mobile,
    )


def _ppi_rays(source=None, n_rays=36, field="velocity", units="m/s"):
    source = source or _clamps_source()
    n_gates = 5
    az = np.linspace(0, 350, n_rays)
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
        scan_number=np.arange(n_rays),
        fields={field: {"data": data, "units": units}},
        housekeeping={},
        coordinate_source=np.full(n_rays, "file site attributes"),
        position_time_epoch=times,
        provenance={},
        warnings=[],
    )


def test_ppi_scan_georeferences_to_a_gridded_radarscan_near_the_site():
    rays = _ppi_rays()
    scan = raw_lidar_scan_to_map_scan(rays, _WHEN, "velocity")

    assert scan.site == "CLAMPS1"
    assert scan.data.shape == scan.lats.shape == scan.lons.shape
    assert scan.data.shape[0] == 36  # one row per ray
    assert scan.data.shape[1] == 5   # one column per range gate
    assert np.isfinite(scan.lats).all() and np.isfinite(scan.lons).all()
    # a few-hundred-meter scan should stay within ~0.01 deg of the site.
    assert np.abs(scan.lats - 36.0).max() < 0.01
    assert np.abs(scan.lons - (-97.0)).max() < 0.01


def test_velocity_field_gets_a_symmetric_diverging_colormap():
    scan = raw_lidar_scan_to_map_scan(_ppi_rays(), _WHEN, "velocity")
    assert scan.colormap == "nws_vel"
    assert scan.vmin == -scan.vmax


def test_non_velocity_field_gets_a_sequential_colormap_from_data_percentiles():
    rays = _ppi_rays(field="backscatter", units="")
    scan = raw_lidar_scan_to_map_scan(rays, _WHEN, "backscatter")
    assert scan.colormap == "nws_ref"
    assert scan.vmin < scan.vmax


def test_mobile_platform_is_refused():
    rays = _ppi_rays(source=_clamps_source(mobile=True))
    with pytest.raises(ValueError, match="mobile"):
        raw_lidar_scan_to_map_scan(rays, _WHEN, "velocity")


def test_unknown_field_is_refused():
    with pytest.raises(ValueError, match="Field"):
        raw_lidar_scan_to_map_scan(_ppi_rays(), _WHEN, "nope")


def test_too_few_rays_in_window_is_refused():
    rays = _ppi_rays(n_rays=3)
    with pytest.raises(ValueError, match="Only"):
        raw_lidar_scan_to_map_scan(rays, _WHEN, "velocity", window_seconds=90)
