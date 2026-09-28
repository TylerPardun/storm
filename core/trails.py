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

_THERMAL = ["#313695", "#4575b4", "#74add1", "#abd9e9", "#fee090", "#fdae61", "#f46d43", "#d73027", "#a50026"]
_DIVERGING = ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"]
RING_SPACING_KM = 5.0
RING_MAX_KM = 20.0


def color_stops(quantity_key: str, vmin: float, vmax: float) -> list:
    """MapLibre 'interpolate' stops [v0, c0, v1, c1, ...] for the quantity's scale."""
    colors = _DIVERGING if derived.QUANTITIES[quantity_key].colormap == "diverging" else _THERMAL
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        vmin, vmax = (vmin - 1, vmin + 1) if math.isfinite(vmin) else (0.0, 1.0)
    stops = []
    for i, color in enumerate(colors):
        stops += [vmin + (vmax - vmin) * i / (len(colors) - 1), color]
    return stops


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
              no_station_pressure: frozenset = frozenset(), wind_barbs: bool = False):
        """Returns (FeatureCollection dict, (vmin, vmax), n_values).
        `no_station_pressure`: platforms whose pressure isn't measured at the
        station (sea-level/altimeter), so pressure-based values stay blank.
        `wind_barbs` adds the measured wind as barb points along each trail
        (about BARBS_PER_TRAIL each, evenly spaced in time), drawn where the
        trail is drawn -- in time-to-space mode, at the relocated positions."""
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
                features += _barbs(lat, lon, sub_time, sub["wind_speed"][keep], sub["wind_dir"][keep],
                                   vehicle_id, (t1 - t0) / BARBS_PER_TRAIL)
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


def _barbs(lat, lon, times, speed_ms, direction, vehicle_id, spacing_s) -> list:
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
                                        "kt": round(kt), "vehicle": vehicle_id,
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
