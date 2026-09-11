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


def test_az_offset_is_set_to_the_smallest_sorted_azimuth():
    # Regression test: noxp_volume_to_scan sorts az/data by azimuth (mirrors
    # ArchiveRadarFetcher._decode) but previously never passed az_offset to
    # NoxpRadarScan, silently defaulting to 0.0 and assuming ray 0 sits at
    # true north. A sweep missing its near-0 deg ray then renders rotated by
    # that gap -- same bug as the WSR-88D archive path.
    volume = _ppi_volume(n_sweeps=1, n_rays_per_sweep=4, n_gates=1)
    volume.azimuth_deg[:] = [12.0, 100.0, 200.0, 300.0]
    scan = noxp_volume_to_scan(volume, sweep_index=0, field_name="DBZ")
    assert scan.az_offset == pytest.approx(12.0)


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


def test_noxp_partial_sector_preserves_native_gates_and_units():
    from ui.map.noxp_overlay import render_noxp_to_png
    from PIL import Image
    from io import BytesIO
    v = _ppi_volume(n_sweeps=1, n_rays_per_sweep=61)
    v.azimuth_deg = np.linspace(240, 300, 61)
    v.range_m = np.tile(v.range_m, (61, 1))
    # Per-ray positions and range spacing must survive rendering.
    v.latitude = np.full(61, 35.)
    v.longitude = np.linspace(-97., -97.001, 61)
    v.range_m[1::2] *= 1.1
    png, bounds, scan, meta = render_noxp_to_png(v, 0, 'DBZ', 256)
    pixels = np.asarray(Image.open(BytesIO(png)))
    assert bounds[2] < -97
    assert 0 < (pixels[:, :, 3] > 0).mean() < .8
    assert meta['units'] == 'dBZ'
    assert meta['rays'] == 61
    assert scan.native_field == 'DBZ'


def test_noxp_site_at_returns_the_volumes_mean_position():
    from archive.fetchers.noxp_radar_archive_fetcher import noxp_site_at
    site = noxp_site_at(_ppi_volume())
    assert site == {"instrument": "NOXP", "lat": 35.0, "lon": -97.0}


def test_noxp_site_at_is_independent_of_scan_type():
    """Unlike noxp_volume_to_scan, a location marker is meaningful even
    for a volume that can't itself be map-rendered (e.g. RHI)."""
    from archive.fetchers.noxp_radar_archive_fetcher import noxp_site_at
    site = noxp_site_at(_ppi_volume(scan_type="rhi_unverified"))
    assert site == {"instrument": "NOXP", "lat": 35.0, "lon": -97.0}


def test_noxp_site_at_returns_none_without_a_usable_position():
    from archive.fetchers.noxp_radar_archive_fetcher import noxp_site_at
    volume = _ppi_volume()
    volume.latitude = np.array([np.nan])
    volume.longitude = np.array([np.nan])
    assert noxp_site_at(volume) is None
