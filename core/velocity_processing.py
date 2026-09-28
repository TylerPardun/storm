"""Optional processing of archive WSR-88D radial velocity, applied to one
decoded sweep (m/s, rays sorted by azimuth):

- Dealiasing: Py-ART's region-based unfolding with MESO-VIEW's settings
  (mm_review/src/radar_render.py: centered, rays wrap around), using the
  sweep's own Nyquist velocity. It can fail or be unavailable (no Nyquist
  recorded); callers then keep the raw velocity and say so -- a raw field
  is never labeled as dealiased.
- Storm-relative velocity: subtract the storm motion's component along each
  beam, u*sin(az) + v*cos(az), scaled by cos(elevation) for the beam's tilt.
  Depends on the chosen motion (the storm track's mean motion), which is
  recorded with the result.

Both work on a copy; the raw sweep is left untouched.
"""
from __future__ import annotations

import logging
import warnings

import numpy as np

log = logging.getLogger(__name__)


def sweep_nyquist(sweep) -> float | None:
    """Nyquist velocity (m/s) of a MetPy Level2File sweep, if recorded."""
    for radial in sweep:
        consts = getattr(radial, "radial_consts", None)          # message 31
        value = getattr(consts, "nyq_vel", None) if consts is not None else None
        if value is None:
            header = radial.header if hasattr(radial, "header") else radial[0]
            value = getattr(header, "nyq_vel", None)             # message 1
        if value is not None and np.isfinite(value) and value > 0:
            return float(value)
    return None


def dealias(vel_ms: np.ndarray, azimuths_deg, ranges_km, elevation_deg: float,
            nyquist_ms: float) -> np.ndarray:
    """Region-based dealiased copy of one sweep (m/s). Raises on failure."""
    import os
    os.environ.setdefault("PYART_QUIET", "1")   # no citation banner on stdout
    import pyart

    n_rays, n_gates = vel_ms.shape
    radar = pyart.testing.make_empty_ppi_radar(n_gates, n_rays, 1)
    radar.azimuth["data"] = np.asarray(azimuths_deg, dtype=np.float64)
    radar.elevation["data"] = np.full(n_rays, float(elevation_deg))
    radar.range["data"] = np.asarray(ranges_km, dtype=np.float64) * 1000.0
    radar.fixed_angle["data"] = np.array([float(elevation_deg)])
    radar.instrument_parameters = {
        "nyquist_velocity": {"data": np.full(n_rays, float(nyquist_ms))},
    }
    radar.add_field("velocity", {
        "data": np.ma.masked_invalid(vel_ms.astype(np.float64)),
        "units": "meters_per_second",
        "_FillValue": -9999.0,
    })
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = pyart.correct.dealias_region_based(
            radar, vel_field="velocity", nyquist_vel=float(nyquist_ms),
            keep_original=False, centered=True, rays_wrap_around=True,
        )
    out = np.ma.filled(np.ma.masked_invalid(result["data"]).astype(np.float32), np.nan)
    out[~np.isfinite(vel_ms)] = np.nan          # never invent gates
    return out


def storm_relative(vel_ms: np.ndarray, azimuths_deg, elevation_deg: float,
                   u_ms: float, v_ms: float) -> np.ndarray:
    """Copy of the sweep with the storm motion removed along each beam."""
    az = np.deg2rad(np.asarray(azimuths_deg, dtype=np.float64))
    toward = (u_ms * np.sin(az) + v_ms * np.cos(az)) * np.cos(np.deg2rad(elevation_deg))
    return (vel_ms - toward[:, None]).astype(np.float32)
