from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from core.velocity_processing import dealias, storm_relative, sweep_nyquist


def _fold(v, nyquist):
    return (v + nyquist) % (2 * nyquist) - nyquist


def test_region_based_dealiasing_recovers_a_folded_wind_field():
    """A smooth 35 m/s flow seen by a radar with an 20 m/s Nyquist folds on
    the upwind/downwind beams; dealiasing must unfold it back."""
    az = np.arange(0, 360, 1.0)
    ranges = 2.0 + np.arange(200) * 0.25
    true = np.outer(35.0 * np.cos(np.deg2rad(az - 225.0)), np.ones_like(ranges)).astype(np.float32)
    folded = _fold(true, 20.0).astype(np.float32)
    folded[:, 150:] = np.nan                                    # no echo far out
    out = dealias(folded, az, ranges, 0.5, 20.0)
    good = np.isfinite(folded)
    assert np.isnan(out[~good]).all()                           # never invents gates
    assert np.abs(out[good] - true[good]).max() < 0.5
    assert np.nanmax(np.abs(folded)) <= 20.0 < np.nanmax(np.abs(out))


def test_storm_relative_removes_the_motion_along_each_beam():
    az = np.array([0.0, 90.0, 180.0, 270.0])
    vel = np.zeros((4, 3), dtype=np.float32)
    out = storm_relative(vel, az, 0.0, u_ms=0.0, v_ms=10.0)     # storm heading north at 10 m/s
    np.testing.assert_allclose(out[:, 0], [-10.0, 0.0, 10.0, 0.0], atol=1e-5)
    tilted = storm_relative(vel, az, 60.0, u_ms=0.0, v_ms=10.0)
    assert tilted[0, 0] == pytest.approx(-5.0)                  # cos(60°) of the motion
    assert vel.max() == 0                                       # input untouched


def test_nyquist_from_either_message_type():
    msg31 = [SimpleNamespace(radial_consts=SimpleNamespace(nyq_vel=28.04), header=None)]
    msg1 = [SimpleNamespace(header=SimpleNamespace(nyq_vel=23.5))]
    missing = [SimpleNamespace(header=SimpleNamespace(el_angle=0.5))]
    assert sweep_nyquist(msg31) == 28.04
    assert sweep_nyquist(msg1) == 23.5
    assert sweep_nyquist(missing) is None
