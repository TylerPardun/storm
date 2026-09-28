"""Tests for archive vehicle ground-speed calculations."""

from datetime import datetime, timedelta, timezone

from archive.vehicle_speed import calculate_vehicle_speed, format_vehicle_speed
from core.observation import Observation


def _observations(count: int, seconds_per_step: int = 1) -> list[Observation]:
    start = datetime(2026, 4, 16, tzinfo=timezone.utc)
    return [
        Observation(
            vehicle_id="p1",
            lat=35.0,
            lon=-97.0 + index * 0.00001,
            timestamp=start + timedelta(seconds=index * seconds_per_step),
        )
        for index in range(count)
    ]


def test_dense_speed_uses_one_and_fifteen_second_windows():
    observations = _observations(16)
    speed = calculate_vehicle_speed(
        observations,
        observations[-1].timestamp,
        short_seconds=1,
        average_seconds=15,
    )

    assert speed.short_mph is not None
    assert speed.average_mph is not None
    assert abs(speed.short_mph - speed.average_mph) < 0.01
    assert format_vehicle_speed(speed).startswith("Vehicle speed (GPS): ")
    assert "over 1 s" in format_vehicle_speed(speed) and "15 s avg" in format_vehicle_speed(speed)


def test_mqtt_speed_uses_ten_and_thirty_second_windows():
    observations = _observations(4, seconds_per_step=10)
    speed = calculate_vehicle_speed(
        observations,
        observations[-1].timestamp,
        short_seconds=10,
        average_seconds=30,
    )

    assert speed.short_mph is not None
    assert speed.average_mph is not None
    assert "over 10 s" in format_vehicle_speed(speed)
    assert "30 s avg" in format_vehicle_speed(speed)


def test_speed_is_unavailable_until_window_has_history():
    observation = _observations(1)[0]
    speed = calculate_vehicle_speed(
        [observation],
        observation.timestamp,
        short_seconds=1,
        average_seconds=15,
    )

    assert speed.short_mph is None
    assert speed.average_mph is None
    assert format_vehicle_speed(speed) == "Vehicle speed (GPS): -- mph over 1 s | -- mph 15 s avg"
