"""Measured and derived surface quantities for observation trails, computed
once per observation set with MetPy (vectorized).

Thermodynamic quantities use each observation's own measured pressure, as
MESO-VIEW's src/derived.py does; an observation missing an input gets NaN
for that quantity -- nothing is computed from an assumed pressure or
moisture. Winds are taken as reported (earth-relative, meteorological
direction the wind blows from).

Storm-relative quantities need the storm track (core/storm_motion.py):
storm-relative wind subtracts the track's mean motion; radial and
tangential wind are that storm-relative wind resolved about the storm
center at each observation's own time (radial + outward, tangential +
counterclockwise/cyclonic). Observations outside the track's time span get
NaN -- the track is never extrapolated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Quantity:
    key: str
    label: str
    units: str
    colormap: str          # "thermal" (sequential) or "diverging"
    needs_track: bool = False
    base_of: str | None = None     # a perturbation: this quantity minus its base state


QUANTITIES: dict[str, Quantity] = {q.key: q for q in (
    Quantity("temperature", "Temperature", "°C", "thermal"),
    Quantity("dewpoint", "Dewpoint", "°C", "thermal"),
    Quantity("rh", "Relative humidity", "%", "thermal"),
    Quantity("pressure", "Pressure", "hPa", "thermal"),
    Quantity("wind_speed", "Wind speed", "m/s", "thermal"),
    Quantity("theta", "Potential temperature θ", "K", "thermal"),
    Quantity("theta_v", "Virtual potential temperature θv", "K", "thermal"),
    Quantity("theta_e", "Equivalent potential temperature θe", "K", "thermal"),
    Quantity("theta_w", "Wet-bulb potential temperature θw", "K", "thermal"),
    Quantity("mixing_ratio", "Mixing ratio", "g/kg", "thermal"),
    Quantity("u", "Wind u (east)", "m/s", "diverging"),
    Quantity("v", "Wind v (north)", "m/s", "diverging"),
    Quantity("sr_wind", "Storm-relative wind speed", "m/s", "thermal", needs_track=True),
    Quantity("radial_wind", "Radial wind (+ out from storm)", "m/s", "diverging", needs_track=True),
    Quantity("tangential_wind", "Tangential wind (+ cyclonic)", "m/s", "diverging", needs_track=True),
    # perturbations from the RAP/RUC base state (core/base_state.py)
    Quantity("theta_p", "θ′ (from base state)", "K", "diverging", base_of="theta"),
    Quantity("theta_v_p", "θv′ (from base state)", "K", "diverging", base_of="theta_v"),
    Quantity("theta_e_p", "θe′ (from base state)", "K", "diverging", base_of="theta_e"),
    Quantity("theta_w_p", "θw′ (from base state)", "K", "diverging", base_of="theta_w"),
    Quantity("mixing_ratio_p", "Mixing ratio′ (from base state)", "g/kg", "diverging", base_of="mixing_ratio"),
    Quantity("temperature_p", "T′ (from base state)", "°C", "diverging", base_of="temperature"),
    Quantity("dewpoint_p", "Td′ (from base state)", "°C", "diverging", base_of="dewpoint"),
    Quantity("u_p", "u′ (from base state)", "m/s", "diverging", base_of="u"),
    Quantity("v_p", "v′ (from base state)", "m/s", "diverging", base_of="v"),
)}

# The base-state field (core.base_state.BaseState) each perturbation subtracts,
# and the factor that puts it in the observation's units.
BASE_FIELDS = {"theta": ("th", 1.0), "theta_v": ("thv", 1.0), "theta_e": ("the", 1.0),
               "theta_w": ("thw", 1.0), "mixing_ratio": ("qv", 1000.0), "temperature": ("temp", 1.0),
               "dewpoint": ("dew", 1.0), "u": ("u", 1.0), "v": ("v", 1.0)}


def _array(values) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)


def observation_arrays(observations) -> dict[str, np.ndarray]:
    """Columns from core.observation.Observation records."""
    return {
        "time": np.array([o.timestamp.timestamp() for o in observations], dtype=np.float64),
        "lat": _array(o.lat for o in observations),
        "lon": _array(o.lon for o in observations),
        "temperature": _array(o.temperature_c for o in observations),
        "dewpoint": _array(o.dewpoint_c for o in observations),
        "pressure": _array(o.pressure_mb for o in observations),
        "wind_speed": _array(o.wind_speed_ms for o in observations),
        "wind_dir": _array(o.wind_dir_deg for o in observations),
    }


def compute(columns: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Every track-independent quantity in QUANTITIES, NaN where an input is missing."""
    import warnings
    import metpy.calc as mc
    from metpy.units import units

    T, Td, p = columns["temperature"], columns["dewpoint"], columns["pressure"]
    out = {k: columns[k].copy() for k in ("temperature", "dewpoint", "pressure", "wind_speed")}
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.simplefilter("ignore")
        Tq, Tdq, pq = T * units.degC, Td * units.degC, p * units.hPa
        out["rh"] = np.asarray(mc.relative_humidity_from_dewpoint(Tq, Tdq).to("percent").m)
        w = mc.saturation_mixing_ratio(pq, Tdq)          # vapour mixing ratio from dewpoint
        out["mixing_ratio"] = np.asarray(w.to("g/kg").m)
        out["theta"] = np.asarray(mc.potential_temperature(pq, Tq).to("K").m)
        out["theta_v"] = np.asarray(mc.virtual_potential_temperature(pq, Tq, w).to("K").m)
        out["theta_e"] = np.asarray(mc.equivalent_potential_temperature(pq, Tq, Tdq).to("K").m)
        out["theta_w"] = np.asarray(mc.wet_bulb_potential_temperature(pq, Tq, Tdq).to("K").m)
        direction = np.deg2rad(columns["wind_dir"])
        speed = columns["wind_speed"]
        out["u"] = -speed * np.sin(direction)
        out["v"] = -speed * np.cos(direction)
    return {k: np.asarray(v, dtype=np.float64) for k, v in out.items()}


def track_center(track_points, times_epoch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Storm center (lat, lon) at each time, linear between track points;
    NaN outside the track's time span."""
    ordered = sorted(track_points, key=lambda p: p.time)
    lat = np.full(times_epoch.shape, np.nan)
    lon = np.full(times_epoch.shape, np.nan)
    if len(ordered) < 2:
        return lat, lon
    t = np.array([p.time.timestamp() for p in ordered])
    inside = (times_epoch >= t[0]) & (times_epoch <= t[-1])
    lat[inside] = np.interp(times_epoch[inside], t, [p.lat for p in ordered])
    lon[inside] = np.interp(times_epoch[inside], t, [p.lon for p in ordered])
    return lat, lon


def offsets_km(lat, lon, lat0, lon0) -> tuple[np.ndarray, np.ndarray]:
    """East/north distance (km) of (lat, lon) from (lat0, lon0), flat-earth."""
    east = np.deg2rad(lon - lon0) * _EARTH_RADIUS_KM * np.cos(np.deg2rad((lat + lat0) / 2))
    north = np.deg2rad(lat - lat0) * _EARTH_RADIUS_KM
    return east, north


def from_offsets_km(east, north, lat0, lon0) -> tuple[np.ndarray, np.ndarray]:
    lat = lat0 + np.rad2deg(north / _EARTH_RADIUS_KM)
    lon = lon0 + np.rad2deg(east / (_EARTH_RADIUS_KM * np.cos(np.deg2rad((lat + lat0) / 2))))
    return lat, lon


def storm_relative(columns, derived, track_points, motion) -> dict[str, np.ndarray]:
    """sr_wind / radial_wind / tangential_wind for these observations."""
    n = columns["time"].shape
    nan = {k: np.full(n, np.nan) for k in ("sr_wind", "radial_wind", "tangential_wind")}
    if motion is None or len(track_points) < 2:
        return nan
    su, sv = derived["u"] - motion.u_ms, derived["v"] - motion.v_ms
    clat, clon = track_center(track_points, columns["time"])
    east, north = offsets_km(columns["lat"], columns["lon"], clat, clon)
    distance = np.hypot(east, north)
    with np.errstate(invalid="ignore", divide="ignore"):
        ex, ny = east / distance, north / distance                 # unit vector out from the center
        radial = su * ex + sv * ny
        tangential = -su * ny + sv * ex                            # counterclockwise positive
    inside = np.isfinite(clat)
    return {
        "sr_wind": np.where(inside, np.hypot(su, sv), np.nan),
        "radial_wind": np.where(inside & (distance > 0), radial, np.nan),
        "tangential_wind": np.where(inside & (distance > 0), tangential, np.nan),
    }


def describe_value(key: str, value: float) -> str:
    q = QUANTITIES[key]
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.1f} {q.units}"
