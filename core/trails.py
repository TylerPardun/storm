"""Observation trails: each platform's recent path as short line segments
colored by one measured or derived quantity (core/derived.py).

Trails cover a trailing window ending at the archive clock. A gap in the
data longer than max(2 min, 5x the platform's usual spacing) breaks the
line instead of bridging it. Dense (1 s) records are thinned to at most
`max_points` per platform for drawing; values are not smoothed.

Time-to-space (view-only, as MESO-VIEW's): each observation is drawn at its
offset from the storm center *at the observation's own time*, placed around
the storm center at the clock time. Values and timestamps are unchanged.
Observations outside the storm track's time span are left out in this mode.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from core import derived

# Color scales. Each kind of quantity uses an established colormap that
# changes hue as well as lightness, evenly (perceptually uniform where one
# exists: Smith & van der Walt's viridis family; ColorBrewer for the
# blue-to-red ones), so neighboring values are told apart -- the earlier
# one-hue ramps made gradients hard to read. Values are shown in bands with
# round-number edges (BIN_TARGET or so per view), like shaded contours, and
# the colorbar labels each edge. Every color keeps >= 3:1 contrast with the
# dark map (the darkest end of each colormap is left out), and the trails
# get a dark outline (map_template.html, obs-trails-casing) so they stand
# apart from roads and radar whatever their hue.
#   kind of quantity: (matplotlib colormap, which end(s) are too dark to use)
PALETTES = {
    "temperature": ("plasma", "low"),          # T, theta, theta-v: purple -> orange -> yellow
    "moisture": ("viridis", "low"),            # Td, RH, mixing ratio: blue -> green -> yellow (as MESO-VIEW)
    "theta_e": ("RdYlBu_r", "both"),           # theta-e, theta-w: blue (cold pool) -> red (inflow)
    "pressure": ("cividis", "low"),            # blue -> yellow, made for color-blind viewers
    "wind": ("magma", "low"),                  # wind speed, storm-relative speed
    "diverging": ("RdBu_r", "both"),           # signed winds: blue < 0 < red (MESO-VIEW's perturbations)
}
MAP_BACKGROUND = "#0D0D14"
MIN_CONTRAST = 3.0
BIN_TARGET = 10
RING_SPACING_KM = 5.0
RING_MAX_KM = 20.0
_palette_cache: dict = {}


def _luminance(rgb) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(hex_a: str, hex_b: str) -> float:
    a, b = (_luminance([int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]) for h in (hex_a, hex_b))
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def palette(kind: str):
    """f(fraction 0..1) -> '#rrggbb' for a kind of quantity, its darkest
    end trimmed so every color keeps MIN_CONTRAST against the map."""
    if kind not in _palette_cache:
        from matplotlib import colormaps
        from matplotlib.colors import to_hex
        name, dark_ends = PALETTES[kind]
        cmap = colormaps[name]

        def readable(f):
            return contrast(to_hex(cmap(f)), MAP_BACKGROUND) >= MIN_CONTRAST
        # start (and, for blue-to-red maps, end) where the colors become
        # readable on the map; trimmed equally at both ends so zero stays central
        lo = next(f / 100 for f in range(0, 60) if readable(f / 100) and (dark_ends == "low" or readable(1 - f / 100)))
        hi = 1.0 - lo if dark_ends == "both" else 1.0
        _palette_cache[kind] = lambda x, c=cmap, a=lo, b=hi: to_hex(c(a + (b - a) * min(max(x, 0.0), 1.0)))
    return _palette_cache[kind]


def _nice_step(span: float, target: int) -> float:
    raw = span / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    return next((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), 10 * magnitude)


def bin_edges(vmin: float, vmax: float, diverging: bool = False, target: int = BIN_TARGET) -> list[float]:
    """Round-number band edges covering [vmin, vmax]. Diverging: symmetric,
    with a band centered on zero."""
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        vmin, vmax = (vmin - 1, vmin + 1) if math.isfinite(vmin) else (0.0, 1.0)
    if diverging:
        edge = max(abs(vmin), abs(vmax))
        step = _nice_step(2 * edge, target)
        n = math.ceil(edge / step - 0.5)               # bands each side of the zero band
        return [round((k + 0.5) * step, 10) for k in range(-n - 1, n + 1)]
    step = _nice_step(vmax - vmin, target)
    first, last = math.floor(vmin / step) * step, math.ceil(vmax / step) * step
    count = max(1, round((last - first) / step))
    return [round(first + k * step, 10) for k in range(count + 1)]


def color_stops(quantity_key: str, vmin: float, vmax: float) -> list:
    """MapLibre 'interpolate' stops [v0, c0, v1, c1, ...] drawing the
    quantity's scale as bands: each band's color is given at both of its
    edges, so the ramp between bands is a hard step (the drawer's colorbar
    reads the same list)."""
    kind = derived.QUANTITIES[quantity_key].colormap
    edges = bin_edges(vmin, vmax, diverging=kind == "diverging")
    color = palette(kind)
    bands = len(edges) - 1
    gap = (edges[-1] - edges[0]) * 1e-6
    stops = []
    for i in range(bands):
        c = color((i + 0.5) / bands)
        stops += [edges[i] + (gap if i else 0.0), c, edges[i + 1], c]
    return stops


def band_edges(stops: list) -> list[float]:
    """The band edges a color_stops list was made from."""
    values = stops[0::2]
    return [values[0]] + [values[i] for i in range(1, len(values), 2)]


def value_range(quantity_key: str, values: np.ndarray) -> tuple[float, float]:
    """2nd-98th percentile of what's on screen; symmetric about 0 for signed winds."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return math.nan, math.nan
    lo, hi = np.percentile(finite, [2, 98])
    if derived.QUANTITIES[quantity_key].colormap == "diverging":
        edge = max(abs(lo), abs(hi), 0.5)
        return -edge, edge
    if hi - lo < 1e-6:
        lo, hi = lo - 0.5, hi + 0.5
    return float(lo), float(hi)


class TrailBuilder:
    """Caches each platform's columns and derived quantities, so a clock
    tick only windows and draws."""

    def __init__(self, max_points: int = 600):
        self.max_points = max_points
        self._cache: dict[str, tuple[tuple, dict, dict]] = {}

    def _prepared(self, vehicle_id, observations, station_pressure: bool = True):
        key = ((len(observations), observations[0].timestamp, observations[-1].timestamp, station_pressure)
               if observations else ())
        cached = self._cache.get(vehicle_id)
        if cached is None or cached[0] != key:
            columns = derived.observation_arrays(observations)
            if not station_pressure:
                # e.g. ASOS reports sea-level pressure/altimeter, not the pressure
                # at the station: never use it for pressure or anything derived from it
                columns["pressure"][:] = np.nan
            cached = (key, columns, derived.compute(columns))
            self._cache[vehicle_id] = cached
        return cached[1], cached[2]

    def build(self, observations_by_vehicle: dict, quantity_key: str, start: datetime, end: datetime, *,
              track_points=(), motion=None, time_to_space: bool = False,
              no_station_pressure: frozenset = frozenset(), wind_barbs: bool = False,
              storm_relative_barbs: bool = False):
        """Returns (FeatureCollection dict, (vmin, vmax), n_values).
        `no_station_pressure`: platforms whose pressure isn't measured at the
        station (sea-level/altimeter), so pressure-based values stay blank.
        `wind_barbs` adds the measured wind as barb points along each trail
        (about BARBS_PER_TRAIL each, evenly spaced in time), drawn where the
        trail is drawn -- in time-to-space mode, at the relocated positions.
        `storm_relative_barbs` draws them storm-relative instead: the wind
        minus the track's mean storm motion, as sr_wind uses (core/derived.py),
        and only where the track covers the observation's time."""
        quantity = derived.QUANTITIES[quantity_key]
        t0, t1 = start.timestamp(), end.timestamp()
        features, shown = [], []
        center_now = None
        if time_to_space:
            clat, clon = derived.track_center(track_points, np.array([t1]))
            center_now = (float(clat[0]), float(clon[0])) if np.isfinite(clat[0]) else None
            if center_now is None:
                return _collection([]), (math.nan, math.nan), 0

        for vehicle_id, observations in observations_by_vehicle.items():
            if not observations:
                continue
            columns, values_all = self._prepared(vehicle_id, observations,
                                                 station_pressure=vehicle_id not in no_station_pressure)
            times = columns["time"]
            lo, hi = np.searchsorted(times, t0, "left"), np.searchsorted(times, t1, "right")
            if hi - lo < 2:
                continue
            idx = np.arange(lo, hi)
            idx = idx[np.isfinite(columns["lat"][idx]) & np.isfinite(columns["lon"][idx])]
            if idx.size > self.max_points:
                idx = idx[np.linspace(0, idx.size - 1, self.max_points).round().astype(int)]
            sub = {k: v[idx] for k, v in columns.items()}
            if quantity.needs_track:
                sub_derived = {k: v[idx] for k, v in values_all.items() if k in ("u", "v")}
                values = derived.storm_relative(sub, sub_derived, list(track_points), motion)[quantity_key]
            else:
                values = values_all[quantity_key][idx]
            lat, lon = sub["lat"], sub["lon"]
            if time_to_space:
                clat, clon = derived.track_center(track_points, sub["time"])
                east, north = derived.offsets_km(lat, lon, clat, clon)
                lat, lon = derived.from_offsets_km(east, north, *center_now)
                keep = np.isfinite(lat)
                lat, lon, values, sub_time = lat[keep], lon[keep], values[keep], sub["time"][keep]
            else:
                keep = np.ones(sub["time"].size, dtype=bool)
                sub_time = sub["time"]
            if sub_time.size < 2:
                continue
            if wind_barbs:
                speed, direction = sub["wind_speed"], sub["wind_dir"]
                if storm_relative_barbs:
                    speed, direction = _storm_relative_wind(values_all["u"][idx], values_all["v"][idx],
                                                            sub["time"], track_points, motion)
                features += _barbs(lat, lon, sub_time, speed[keep], direction[keep],
                                   vehicle_id, (t1 - t0) / BARBS_PER_TRAIL, storm_relative_barbs)
            spacing = np.diff(sub_time)
            gap = max(120.0, 5.0 * float(np.median(spacing)))
            for i in range(sub_time.size - 1):
                if spacing[i] > gap:
                    continue
                a, b = values[i], values[i + 1]
                value = np.nanmean([a, b]) if (np.isfinite(a) or np.isfinite(b)) else math.nan
                if not math.isfinite(value):
                    continue
                shown.append(value)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString",
                                 "coordinates": [[round(lon[i], 6), round(lat[i], 6)],
                                                 [round(lon[i + 1], 6), round(lat[i + 1], 6)]]},
                    "properties": {"kind": "trail", "v": round(float(value), 3), "vehicle": vehicle_id,
                                   "t": datetime.fromtimestamp(sub_time[i + 1], timezone.utc).strftime("%H:%M:%SZ")},
                })

        if center_now is not None:
            features += _rings(*center_now)
        vmin, vmax = value_range(quantity_key, np.array(shown))
        return _collection(features), (vmin, vmax), len(shown)


BARBS_PER_TRAIL = 15
_MS_TO_KT = 1.943844


def barb_image(speed_kt: float) -> str:
    """The map's barb icon for a speed: 'barb-0' calm (under 2.5 kt), else
    the speed rounded to the nearest 5 kt (the barb's own resolution)."""
    return f"barb-{int(5 * round(speed_kt / 5)) if speed_kt >= 2.5 else 0}"


def _storm_relative_wind(u, v, times, track_points, motion):
    """(speed m/s, direction the storm-relative wind comes from), NaN where
    there's no storm motion or the track doesn't cover the time."""
    if motion is None or len(track_points) < 2:
        nan = np.full(np.shape(times), np.nan)
        return nan, nan
    su, sv = u - motion.u_ms, v - motion.v_ms
    covered = np.isfinite(derived.track_center(list(track_points), times)[0])
    speed = np.where(covered, np.hypot(su, sv), np.nan)
    direction = np.where(covered, np.degrees(np.arctan2(-su, -sv)) % 360, np.nan)
    return speed, direction


def _barbs(lat, lon, times, speed_ms, direction, vehicle_id, spacing_s, storm_relative=False) -> list:
    """Barb points along one trail: the first observation with a wind at or
    after each multiple of spacing_s, so barbs stay put as the clock runs."""
    features, last_slot = [], None
    for i in range(times.size):
        slot = int(times[i] // spacing_s)
        if slot == last_slot:
            continue
        if not (np.isfinite(speed_ms[i]) and np.isfinite(direction[i]) and np.isfinite(lat[i])):
            continue
        last_slot = slot
        kt = float(speed_ms[i]) * _MS_TO_KT
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [round(float(lon[i]), 6), round(float(lat[i]), 6)]},
                         "properties": {"kind": "barb", "img": barb_image(kt), "dir": round(float(direction[i]) % 360, 1),
                                        "kt": round(kt), "vehicle": vehicle_id, "sr": storm_relative,
                                        "t": datetime.fromtimestamp(times[i], timezone.utc).strftime("%H:%M:%SZ")}})
    return features


def _collection(features):
    return {"type": "FeatureCollection", "features": features}


def _rings(lat0: float, lon0: float) -> list:
    """Storm-centered range rings (time-to-space mode) and the center point."""
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon0, lat0]},
                 "properties": {"kind": "center"}}]
    angles = np.linspace(0, 2 * np.pi, 73)
    radius = RING_SPACING_KM
    while radius <= RING_MAX_KM + 1e-9:
        lat, lon = derived.from_offsets_km(radius * np.sin(angles), radius * np.cos(angles), lat0, lon0)
        features.append({"type": "Feature",
                         "geometry": {"type": "LineString",
                                      "coordinates": [[round(x, 6), round(y, 6)] for x, y in zip(lon, lat)]},
                         "properties": {"kind": "ring", "label": f"{radius:g} km"}})
        radius += RING_SPACING_KM
    return features
