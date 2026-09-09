from datetime import datetime, timezone

import numpy as np
import pytest

from archive.fetchers.noxp_archive_fetcher import RadarVolume
from archive.fetchers.noxp_radar_archive_fetcher import noxp_volume_to_scan
from core.noxp_radar_scan import field_meta


def _ppi_volume(n_sweeps=2, n_rays_per_sweep=4, n_gates=3, scan_type="ppi"):
    total = n_sweeps * n_rays_per_sweep
    az = np.tile(np.linspace(0, 360, n_rays_per_sweep, endpoint=False), n_sweeps)
    el = np.repeat([0.5, 1.5][:n_sweeps], n_rays_per_sweep)
    time_epoch = np.arange(total, dtype=float) + 1_700_000_000.0
    data = np.arange(total * n_gates, dtype=float).reshape(total, n_gates)
    return RadarVolume(
        time_epoch=time_epoch,
        range_m=np.array([1000.0, 2000.0, 3000.0][:n_gates]),
        azimuth_deg=az,
        elevation_deg=el,
        latitude=np.array([35.0]),
        longitude=np.array([-97.0]),
        altitude_m=np.array([400.0]),
        fields={"DBZ": {"data": np.ma.masked_invalid(data), "units": "dBZ"}},
        sweep_start=np.array([i * n_rays_per_sweep for i in range(n_sweeps)]),
        sweep_end=np.array([(i + 1) * n_rays_per_sweep - 1 for i in range(n_sweeps)]),
        scan_type=scan_type,
        provenance={"format": "sigmet"},
    )


def test_sweep_slicing_picks_exactly_that_sweeps_rays():
    volume = _ppi_volume()
    scan = noxp_volume_to_scan(volume, sweep_index=1, field_name="DBZ")
    assert scan.data.shape == (4, 3)
    assert scan.sweep_index == 1
    assert scan.elevation_deg == pytest.approx(1.5)
    assert scan.available_sweeps == [(0, pytest.approx(0.5)), (1, pytest.approx(1.5))]


def test_projected_position_matches_a_hand_computed_point_due_north():
    volume = _ppi_volume(n_sweeps=1, n_rays_per_sweep=1, n_gates=1)
    volume.azimuth_deg[:] = 0.0  # due north
    volume.range_m = np.array([1000.0])  # 1 km
    scan = noxp_volume_to_scan(volume, sweep_index=0, field_name="DBZ")
    # 1 km due north of 35.0N, -97.0W is ~0.00899 deg latitude north, same longitude.
    assert scan.lats[0, 0] == pytest.approx(35.00899, abs=1e-4)
    assert scan.lons[0, 0] == pytest.approx(-97.0, abs=1e-4)


def test_rhi_unverified_scan_type_is_rejected_before_projection():
    volume = _ppi_volume(scan_type="rhi_unverified")
    with pytest.raises(ValueError, match="rhi_unverified"):
        noxp_volume_to_scan(volume, sweep_index=0, field_name="DBZ")


def test_unrecognized_field_gets_generic_fallback_not_keyerror():
    meta = field_meta("SOME_NEW_FIELD_NOBODY_HAS_SEEN")
    assert meta["label"] == "SOME_NEW_FIELD_NOBODY_HAS_SEEN"
    assert meta["vmin"] < meta["vmax"]

    volume = _ppi_volume()
    volume.fields["SOME_NEW_FIELD_NOBODY_HAS_SEEN"] = volume.fields["DBZ"]
    scan = noxp_volume_to_scan(volume, sweep_index=0, field_name="SOME_NEW_FIELD_NOBODY_HAS_SEEN")
    assert scan.native_field == "SOME_NEW_FIELD_NOBODY_HAS_SEEN"


def test_missing_field_raises_with_available_fields_listed():
    volume = _ppi_volume()
    with pytest.raises(ValueError, match="VEL"):
        noxp_volume_to_scan(volume, sweep_index=0, field_name="VEL")


def test_out_of_range_sweep_index_raises():
    volume = _ppi_volume(n_sweeps=2)
    with pytest.raises(ValueError, match="sweep_index"):
        noxp_volume_to_scan(volume, sweep_index=5, field_name="DBZ")


def test_2d_per_ray_range_from_wdss2_sparse_is_sliced_by_sweep_too():
    # WDSS-II sparse PPI volumes carry a per-ray range axis (GateWidth varies
    # by ray), unlike Sigmet/CfRadial's single shared 1D range axis.
    volume = _ppi_volume(n_sweeps=2, n_rays_per_sweep=2, n_gates=2)
    volume.range_m = np.tile([[500.0, 1500.0]], (4, 1))  # [total_rays, n_gates]
    scan = noxp_volume_to_scan(volume, sweep_index=1, field_name="DBZ")
    assert scan.data.shape == (2, 2)
    assert scan.lats.shape == (2, 2)
