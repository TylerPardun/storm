"""Storm motion derived from a hand-placed storm track (core/storm_track.py),
following MESO-VIEW's definitions (mm_review/app.py, src/io.py):

- mean motion: end-to-end displacement over duration, first point to last.
  Intermediate points shape the path but don't change the mean, so moving
  either endpoint moves the readout -- MESO-VIEW's deliberate choice, and the
  value it hands to the hodograph.
- motion at a time: the straight segment between the placed points either
  side of it; exactly at a point, the average of its two segments (MESO-VIEW's
  central difference).

Positions between points are linear in time. Nothing is extrapolated: before
the first point or after the last there is no position or motion. Points
sharing a time contribute no segment. Distances use a local flat-earth
approximation (as MESO-VIEW does), accurate to well under 1% over the few
tens of kilometers between track points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from core.storm_track import TrackPoint

_EARTH_RADIUS_M = 6_371_000.0
_MS_TO_KT = 1.0 / 0.514444


@dataclass(frozen=True)
class StormMotion:
    u_ms: float  # eastward
    v_ms: float  # northward

    @property
    def speed_ms(self) -> float:
        return math.hypot(self.u_ms, self.v_ms)

    @property
    def speed_kt(self) -> float:
        return self.speed_ms * _MS_TO_KT

    @property
    def direction_from_deg(self) -> float:
        """Meteorological convention, like wind: the direction the storm moves from."""
        return (270.0 - math.degrees(math.atan2(self.v_ms, self.u_ms))) % 360.0

    def describe(self) -> str:
        return f"from {self.direction_from_deg:03.0f}° at {self.speed_ms:.1f} m/s ({self.speed_kt:.0f} kt)"


def _between(a: TrackPoint, b: TrackPoint) -> StormMotion | None:
    dt = (b.time - a.time).total_seconds()
    if dt <= 0:
        return None
    mean_lat = math.radians((a.lat + b.lat) / 2)
    dy = math.radians(b.lat - a.lat) * _EARTH_RADIUS_M
    dx = math.radians(b.lon - a.lon) * _EARTH_RADIUS_M * math.cos(mean_lat)
    return StormMotion(dx / dt, dy / dt)


def _ordered(points: list[TrackPoint]) -> list[TrackPoint]:
    return sorted(points, key=lambda p: p.time)


def mean_motion(points: list[TrackPoint]) -> StormMotion | None:
    """End-to-end motion, or None with fewer than two distinct times."""
    ordered = _ordered(points)
    if len(ordered) < 2:
        return None
    return _between(ordered[0], ordered[-1])


def motion_at(points: list[TrackPoint], when: datetime) -> StormMotion | None:
    """Motion of the track segment at `when`, or None outside the track."""
    ordered = _ordered(points)
    segments = [(a, b, m) for a, b in zip(ordered, ordered[1:]) if (m := _between(a, b))]
    touching = [m for a, b, m in segments if a.time <= when <= b.time]
    if not touching:
        return None
    return StormMotion(sum(m.u_ms for m in touching) / len(touching),
                       sum(m.v_ms for m in touching) / len(touching))


def position_at(points: list[TrackPoint], when: datetime) -> tuple[float, float] | None:
    """Storm center (lat, lon) at `when`, linear between placed points; None
    outside the track."""
    ordered = _ordered(points)
    for p in ordered:
        if p.time == when:
            return p.lat, p.lon
    for a, b in zip(ordered, ordered[1:]):
        if a.time < when < b.time:
            f = (when - a.time).total_seconds() / (b.time - a.time).total_seconds()
            return a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon)
    return None
