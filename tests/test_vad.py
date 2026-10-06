"""Tests for VAD wind profile interpolation and derived parameters."""

from datetime import datetime, timezone

import numpy as np

from core.vad import (
    KNOT_TO_MS,
    VADProfile,
    VADSet,
    _storm_relative_helicity,
    calculate_vad_parameters,
)


def _profile(heights, directions, speeds):
    return VADProfile(
        timestamp=datetime(2026, 4, 18, 18, 0, tzinfo=timezone.utc),
        site="TLX",
        heights_m=np.array(heights, dtype=float),
        wind_dir=np.array(directions, dtype=float),
        wind_spd=np.array(speeds, dtype=float),
    )



def test_bulk_shear_display_units_remain_knots():
    prof = _profile([0, 1000, 3000, 6000], [270, 270, 270, 270], [0, 10, 30, 60])

    params = calculate_vad_parameters(prof)

    np.testing.assert_allclose(params.shear_0_1, 10.0, atol=1e-6)
    np.testing.assert_allclose(params.shear_0_3, 30.0, atol=1e-6)
    np.testing.assert_allclose(params.bwd_0_6, 60.0, atol=1e-6)


def test_shallow_profile_does_not_substitute_calm_upper_wind():
    prof = _profile([0, 1000, 3000], [270, 270, 270], [0, 10, 30])

    params = calculate_vad_parameters(prof)

    np.testing.assert_allclose(params.shear_0_1, 10.0, atol=1e-6)
    assert params.bwd_0_6 is None
    assert params.bunkers_spd is None
    assert params.srh_0_1 is None


def test_srh_uses_meters_per_second_components():
    # 10 kt eastward at the bottom, 10 kt northward at the top, zero storm motion.
    prof = _profile([0, 1000], [270, 180], [10, 10])

    srh = _storm_relative_helicity(prof, 0, 1000, 0.0, 0.0)

    np.testing.assert_allclose(srh, -(10 * KNOT_TO_MS) ** 2, rtol=1e-6)


def test_hodograph_bound_uses_max_across_entire_vad_set():
    early = VADProfile(
        timestamp=datetime(2026, 4, 18, 17, 30, tzinfo=timezone.utc),
        site="TLX",
        heights_m=np.array([0, 1000, 3000, 6000], dtype=float),
        wind_dir=np.array([270, 270, 270, 270], dtype=float),
        wind_spd=np.array([20, 30, 40, 50], dtype=float),
    )
    late = VADProfile(
        timestamp=datetime(2026, 4, 18, 18, 0, tzinfo=timezone.utc),
        site="TLX",
        heights_m=np.array([0, 1000, 3000, 6000], dtype=float),
        wind_dir=np.array([270, 270, 270, 270], dtype=float),
        wind_spd=np.array([30, 50, 70, 90], dtype=float),
    )

    vad_set = VADSet([late, early])

    assert vad_set.hodograph_bound_kt() == 110.0
