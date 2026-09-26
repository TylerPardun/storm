import math
from datetime import datetime, timedelta, timezone

import pytest

from core.storm_motion import StormMotion, mean_motion, motion_at, position_at
from core.storm_track import TrackPoint

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)


def _pt(i, minutes, lat, lon):
    return TrackPoint(point_id=i, time=T0 + timedelta(minutes=minutes), lat=lat, lon=lon)


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def test_eastward_storm_moves_from_the_west_at_the_haversine_speed():
    m = mean_motion([_pt(1, 0, 35.0, -98.0), _pt(2, 10, 35.0, -97.9)])
    expected = _haversine_m(35.0, -98.0, 35.0, -97.9) / 600
    assert m.speed_ms == pytest.approx(expected, rel=1e-3)
    assert m.direction_from_deg == pytest.approx(270.0)
    assert m.v_ms == pytest.approx(0.0)


@pytest.mark.parametrize("u, v, from_deg", [(0, 10, 180), (-10, 0, 90), (0, -10, 0), (10, 10, 225)])
def test_direction_is_where_the_storm_comes_from(u, v, from_deg):
    assert StormMotion(u, v).direction_from_deg == pytest.approx(from_deg)


def test_knots_and_description():
    m = StormMotion(0.0, 20.0)
    assert m.speed_kt == pytest.approx(38.88, abs=0.01)
    assert m.describe() == "from 180° at 20.0 m/s (39 kt)"


def test_mean_motion_is_end_to_end_so_a_detour_does_not_change_it():
    straight = [_pt(1, 0, 35.0, -98.0), _pt(2, 20, 35.2, -97.8)]
    detour = [straight[0], _pt(3, 10, 35.3, -98.1), straight[1]]
    assert mean_motion(detour) == mean_motion(straight)


def test_no_motion_without_two_distinct_times():
    assert mean_motion([]) is None
    assert mean_motion([_pt(1, 0, 35, -98)]) is None
    assert mean_motion([_pt(1, 0, 35, -98), _pt(2, 0, 35.1, -98)]) is None


def test_motion_at_uses_the_segment_and_averages_at_a_vertex():
    pts = [_pt(1, 0, 35.0, -98.0), _pt(2, 10, 35.0, -97.9), _pt(3, 20, 35.1, -97.9)]
    first = motion_at(pts, T0 + timedelta(minutes=5))
    second = motion_at(pts, T0 + timedelta(minutes=15))
    assert first.direction_from_deg == pytest.approx(270) and second.direction_from_deg == pytest.approx(180)
    at_vertex = motion_at(pts, T0 + timedelta(minutes=10))
    assert at_vertex.u_ms == pytest.approx(first.u_ms / 2) and at_vertex.v_ms == pytest.approx(second.v_ms / 2)


def test_nothing_is_extrapolated_beyond_the_track():
    pts = [_pt(1, 0, 35.0, -98.0), _pt(2, 10, 35.0, -97.9)]
    for minutes in (-1, 11):
        assert motion_at(pts, T0 + timedelta(minutes=minutes)) is None
        assert position_at(pts, T0 + timedelta(minutes=minutes)) is None


def test_position_is_linear_in_time_between_points():
    pts = [_pt(1, 0, 35.0, -98.0), _pt(2, 10, 35.2, -97.8)]
    assert position_at(pts, T0 + timedelta(minutes=5)) == pytest.approx((35.1, -97.9))
    assert position_at(pts, T0) == (35.0, -98.0)
