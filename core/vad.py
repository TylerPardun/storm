
import math
from dataclasses import dataclass
from datetime import datetime
import numpy as np

KNOT_TO_MS = 0.514444
MS_TO_KNOT = 1.0 / KNOT_TO_MS


def _wind_components_from_dir_speed(wind_dir: np.ndarray, wind_spd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return meteorological wind components in the same speed unit as wind_spd."""
    u = -wind_spd * np.sin(np.deg2rad(wind_dir))
    v = -wind_spd * np.cos(np.deg2rad(wind_dir))
    return u, v


def _wind_dir_speed_from_components(u: float, v: float) -> tuple[float, float]:
    spd = float(np.hypot(u, v))
    direction = float((np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0)
    return direction, spd


@dataclass(frozen=True)
class VADProfile:
    """Single VAD wind profile from NEXRAD NVW product.
    
    Attributes:
        timestamp: UTC time of the VAD analysis
        site: Radar site identifier (e.g., "TLX")
        heights_m: Array of height levels in meters AGL
        wind_dir: Array of wind directions in degrees (0-360)
        wind_spd: Array of wind speeds in knots
        rms_error: RMS error of the VAD fit (quality indicator)
    """
    timestamp: datetime
    site: str
    heights_m: np.ndarray
    wind_dir: np.ndarray
    wind_spd: np.ndarray
    rms_error: float = 0.0
    
    def __post_init__(self):
        """Validate that arrays have consistent shapes."""
        if not (len(self.heights_m) == len(self.wind_dir) == len(self.wind_spd)):
            raise ValueError("VAD arrays must have matching lengths")
    
    @property
    def label(self) -> str:
        """Human-readable label for this profile."""
        return f"{self.site} VAD {self.timestamp.strftime('%H:%M')}Z"
    
    def u_component(self) -> np.ndarray:
        """Calculate U (eastward) wind component in knots."""
        return _wind_components_from_dir_speed(self.wind_dir, self.wind_spd)[0]
    
    def v_component(self) -> np.ndarray:
        """Calculate V (northward) wind component in knots."""
        return _wind_components_from_dir_speed(self.wind_dir, self.wind_spd)[1]

    def u_component_ms(self) -> np.ndarray:
        """Calculate U (eastward) wind component in m/s."""
        return self.u_component() * KNOT_TO_MS

    def v_component_ms(self) -> np.ndarray:
        """Calculate V (northward) wind component in m/s."""
        return self.v_component() * KNOT_TO_MS
    

    def interpolate_components_to_height(self, target_height_m: float, unit: str = "m/s") -> tuple[float, float]:
        """Interpolate U/V wind components to a height.

        Args:
            target_height_m: Height in metres using the profile's height datum.
            unit: "m/s" or "kt".

        Returns:
            (u, v), or (nan, nan) if the requested height is outside the profile.
        """
        if len(self.heights_m) == 0:
            return (np.nan, np.nan)
        if target_height_m < self.heights_m.min() or target_height_m > self.heights_m.max():
            return (np.nan, np.nan)

        if unit == "kt":
            u = self.u_component()
            v = self.v_component()
        elif unit == "m/s":
            u = self.u_component_ms()
            v = self.v_component_ms()
        else:
            raise ValueError("unit must be 'm/s' or 'kt'")

        return (
            float(np.interp(target_height_m, self.heights_m, u)),
            float(np.interp(target_height_m, self.heights_m, v)),
        )


@dataclass(frozen=True)
class VADParameters:
    """Hodograph-derived VAD parameters.

    Shear, ground-relative wind, storm-relative wind, and Bunkers motion are
    exposed in knots for display. SRH is exposed in m^2/s^2.
    """
    bwd_0_6: float | None
    shear_0_1: float | None
    shear_0_3: float | None
    bunkers_dir: float | None
    bunkers_spd: float | None
    bunkers_u_ms: float | None
    bunkers_v_ms: float | None
    srh_0_1: float | None
    srh_0_3: float | None
    grw_0_1: float | None
    grw_0_3: float | None
    grw_0_6: float | None
    srw_0_1: float | None
    srw_0_3: float | None
    srw_0_6: float | None


def _layer_components(profile: VADProfile, z_bottom: float, z_top: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Return heights/u/v in m/s for a layer, including interpolated bounds."""
    if z_bottom < profile.heights_m.min() or z_top > profile.heights_m.max() or z_top <= z_bottom:
        return None

    u_bot, v_bot = profile.interpolate_components_to_height(z_bottom, unit="m/s")
    u_top, v_top = profile.interpolate_components_to_height(z_top, unit="m/s")
    if np.isnan(u_bot) or np.isnan(u_top):
        return None

    inside = (profile.heights_m > z_bottom) & (profile.heights_m < z_top)
    heights = np.concatenate(([z_bottom], profile.heights_m[inside], [z_top]))
    u = np.concatenate(([u_bot], profile.u_component_ms()[inside], [u_top]))
    v = np.concatenate(([v_bot], profile.v_component_ms()[inside], [v_top]))
    return heights, u, v


def _bulk_shear_kt(profile: VADProfile, z_bottom: float, z_top: float) -> float | None:
    u_bot, v_bot = profile.interpolate_components_to_height(z_bottom, unit="m/s")
    u_top, v_top = profile.interpolate_components_to_height(z_top, unit="m/s")
    if np.isnan(u_bot) or np.isnan(u_top):
        return None
    return float(np.hypot(u_top - u_bot, v_top - v_bot) * MS_TO_KNOT)


def _mean_wind_ms(profile: VADProfile, z_bottom: float, z_top: float) -> tuple[float, float] | None:
    layer = _layer_components(profile, z_bottom, z_top)
    if layer is None:
        return None
    heights, u, v = layer
    if len(u) < 2:
        return None
    depth = heights[-1] - heights[0]
    if depth <= 0:
        return None
    return (
        float(np.trapezoid(u, heights) / depth),
        float(np.trapezoid(v, heights) / depth),
    )


def _bunkers_right_mover_ms(profile: VADProfile, z_base: float) -> tuple[float, float] | None:
    mean = _mean_wind_ms(profile, z_base, z_base + 6000.0)
    u_base, v_base = profile.interpolate_components_to_height(z_base, unit="m/s")
    u_6km, v_6km = profile.interpolate_components_to_height(z_base + 6000.0, unit="m/s")
    if mean is None or np.isnan(u_base) or np.isnan(u_6km):
        return None

    shear_u = u_6km - u_base
    shear_v = v_6km - v_base
    shear_mag = float(np.hypot(shear_u, shear_v))
    if shear_mag == 0.0:
        return mean

    deviation = 7.5  # m/s, Bunkers et al. right-moving supercell offset.
    u_mean, v_mean = mean
    return (
        float(u_mean + deviation * (-shear_v / shear_mag)),
        float(v_mean + deviation * (shear_u / shear_mag)),
    )


def _storm_relative_helicity(profile: VADProfile, z_bottom: float, z_top: float,
                             storm_u_ms: float, storm_v_ms: float) -> float | None:
    layer = _layer_components(profile, z_bottom, z_top)
    if layer is None:
        return None
    _, u, v = layer
    if len(u) < 2:
        return None

    u_sr = u - storm_u_ms
    v_sr = v - storm_v_ms
    layers = u_sr[1:] * v_sr[:-1] - u_sr[:-1] * v_sr[1:]
    return float(np.sum(layers))


def _layer_mean_speed_kt(profile: VADProfile, z_bottom: float, z_top: float) -> float | None:
    mean = _mean_wind_ms(profile, z_bottom, z_top)
    if mean is None:
        return None
    return float(np.hypot(*mean) * MS_TO_KNOT)


def _layer_mean_srw_kt(profile: VADProfile, z_bottom: float, z_top: float,
                       storm_u_ms: float, storm_v_ms: float) -> float | None:
    mean = _mean_wind_ms(profile, z_bottom, z_top)
    if mean is None:
        return None
    u_mean, v_mean = mean
    return float(np.hypot(u_mean - storm_u_ms, v_mean - storm_v_ms) * MS_TO_KNOT)


def calculate_vad_parameters(profile: VADProfile) -> VADParameters:
    """Calculate VAD hodograph parameters with m/s internal units.

    Layer depths are relative to the lowest available VAD height. Parameters
    requiring data above the top of the profile return None instead of silently
    substituting calm wind.
    """
    z_base = float(profile.heights_m.min())
    rm = _bunkers_right_mover_ms(profile, z_base)
    if rm is None:
        rm_u = rm_v = None
        rm_dir = rm_spd = None
    else:
        rm_u, rm_v = rm
        rm_dir, rm_spd_ms = _wind_dir_speed_from_components(rm_u, rm_v)
        rm_spd = rm_spd_ms * MS_TO_KNOT

    def srh(depth_m: float) -> float | None:
        if rm_u is None or rm_v is None:
            return None
        return _storm_relative_helicity(profile, z_base, z_base + depth_m, rm_u, rm_v)

    def srw(depth_m: float) -> float | None:
        if rm_u is None or rm_v is None:
            return None
        return _layer_mean_srw_kt(profile, z_base, z_base + depth_m, rm_u, rm_v)

    return VADParameters(
        bwd_0_6=_bulk_shear_kt(profile, z_base, z_base + 6000.0),
        shear_0_1=_bulk_shear_kt(profile, z_base, z_base + 1000.0),
        shear_0_3=_bulk_shear_kt(profile, z_base, z_base + 3000.0),
        bunkers_dir=rm_dir,
        bunkers_spd=rm_spd,
        bunkers_u_ms=rm_u,
        bunkers_v_ms=rm_v,
        srh_0_1=srh(1000.0),
        srh_0_3=srh(3000.0),
        grw_0_1=_layer_mean_speed_kt(profile, z_base, z_base + 1000.0),
        grw_0_3=_layer_mean_speed_kt(profile, z_base, z_base + 3000.0),
        grw_0_6=_layer_mean_speed_kt(profile, z_base, z_base + 6000.0),
        srw_0_1=srw(1000.0),
        srw_0_3=srw(3000.0),
        srw_0_6=srw(6000.0),
    )


@dataclass(frozen=True)
class VADSet:
    """Collection of VAD profiles for time-series display."""
    profiles: list[VADProfile]
    
    def __post_init__(self):
        """Sort profiles by timestamp."""
        object.__setattr__(self, 'profiles', sorted(self.profiles, key=lambda p: p.timestamp))
    
    def __len__(self) -> int:
        return len(self.profiles)
    
    def __getitem__(self, idx: int) -> VADProfile:
        return self.profiles[idx]
    
    @property
    def site(self) -> str:
        """Radar site (assumes all profiles from same site)."""
        return self.profiles[0].site if self.profiles else ""
    
    @property
    def timestamps(self) -> list[datetime]:
        """List of all profile timestamps."""
        return [p.timestamp for p in self.profiles]

    def hodograph_bound_kt(self, *, min_bound: float = 60.0, padding: float = 15.0) -> float:
        """Return a stable hodograph half-width for the whole fetched set."""
        max_radius = 0.0
        for profile in self.profiles:
            u = profile.u_component()
            v = profile.v_component()
            if len(u):
                max_radius = max(max_radius, float(np.max(np.hypot(u, v))))

            params = calculate_vad_parameters(profile)
            if params.bunkers_spd is not None and np.isfinite(params.bunkers_spd):
                max_radius = max(max_radius, float(params.bunkers_spd))

        bound = max(float(min_bound), max_radius + float(padding))
        return float(math.ceil(bound / 10.0) * 10.0)
