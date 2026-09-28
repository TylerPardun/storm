"""Vehicle ground-speed calculations for archive playback."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from core.observation import Observation

_MPS_TO_MPH = 2.2369362921


@dataclass(frozen=True)
class VehicleSpeed:
    short_seconds: int
    short_mph: float | None
    average_seconds: int
    average_mph: float | None


def _distance_m(first: Observation, last: Observation) -> float:
    radius_m = 6_371_000.0
    lat1 = math.radians(first.lat)
    lat2 = math.radians(last.lat)
    delta_lat = math.radians(last.lat - first.lat)
    delta_lon = math.radians(last.lon - first.lon)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _window_speed_mph(
    observations: Sequence[Observation],
    timestamps: Sequence[datetime],
    current_index: int,
    window_seconds: int,
) -> float | None:
    current = observations[current_index]
    target = current.timestamp - timedelta(seconds=window_seconds)
    start_index = bisect.bisect_right(timestamps, target) - 1
    if start_index < 0 or start_index >= current_index:
        return None

    start = observations[start_index]
    elapsed = (current.timestamp - start.timestamp).total_seconds()
    if elapsed <= 0:
        return None
    distance_m = sum(
        _distance_m(observations[index - 1], observations[index])
        for index in range(start_index + 1, current_index + 1)
    )
    return distance_m / elapsed * _MPS_TO_MPH


def calculate_vehicle_speed(
    observations: Sequence[Observation],
    archive_time: datetime,
    short_seconds: int,
    average_seconds: int,
) -> VehicleSpeed:
    """Calculate short-window and average ground speeds at archive_time."""
    timestamps = [observation.timestamp for observation in observations]
    current_index = bisect.bisect_right(timestamps, archive_time) - 1
    if current_index < 0:
        return VehicleSpeed(short_seconds, None, average_seconds, None)
    return VehicleSpeed(
        short_seconds=short_seconds,
        short_mph=_window_speed_mph(
            observations,
            timestamps,
            current_index,
            short_seconds,
        ),
        average_seconds=average_seconds,
        average_mph=_window_speed_mph(
            observations,
            timestamps,
            current_index,
            average_seconds,
        ),
    )


def format_vehicle_speed(speed: VehicleSpeed) -> str:
    """The vehicle hover tooltip's second line: how fast the vehicle itself
    was driving, from its GPS track -- labeled so it isn't read as wind."""
    short = "--" if speed.short_mph is None else f"{speed.short_mph:.0f}"
    average = "--" if speed.average_mph is None else f"{speed.average_mph:.0f}"
    return (
        f"Vehicle speed {short} mph ({speed.short_seconds} s) · "
        f"{average} mph ({speed.average_seconds}-s avg)"
    )
