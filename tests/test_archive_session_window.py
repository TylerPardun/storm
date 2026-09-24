"""The archive session's playable span: its UTC day into the next morning,
ended by the day's activity."""

from datetime import datetime, timedelta, timezone

from archive.session import activity_end, last_moving_time, session_bounds
from archive.time_controller import TimeController
from core.observation import Observation


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _track(start: datetime, seconds: int, *, speed_deg_per_s: float, lat: float = 35.0) -> list[Observation]:
    return [Observation("probe1", lat + i * speed_deg_per_s, -97.0, start + timedelta(seconds=i))
            for i in range(seconds)]


def test_session_spans_its_utc_day_and_the_next_morning_to_06z():
    assert session_bounds(_utc(2024, 4, 27, 20, 15)) == (_utc(2024, 4, 27), _utc(2024, 4, 28, 6))
    # a naive start time is UTC, not local time
    assert session_bounds(datetime(2024, 4, 27, 20, 15)) == (_utc(2024, 4, 27), _utc(2024, 4, 28, 6))


def test_parked_vehicle_logging_overnight_is_not_activity():
    driving = _track(_utc(2024, 4, 28, 1, 0), 120, speed_deg_per_s=3e-4)   # ~33 m/s
    parked = _track(_utc(2024, 4, 28, 1, 2), 3 * 3600, speed_deg_per_s=0.0, lat=driving[-1].lat)

    # moving is judged over a minute, so it reads as moving until a minute
    # after the stop -- immaterial next to the half-hour margin
    stopped = last_moving_time(driving + parked)
    assert _utc(2024, 4, 28, 1, 1, 59) <= stopped <= _utc(2024, 4, 28, 1, 3)
    assert last_moving_time(parked) is None


def test_session_ends_half_an_hour_after_the_last_activity():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 28, 2, 33)]) == _utc(2024, 4, 28, 3, 3)


def test_session_can_end_before_00z_on_a_quiet_evening():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 27, 21, 10)]) == _utc(2024, 4, 27, 21, 40)


def test_session_end_is_capped_at_06z_next_day():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 28, 5, 50)]) == _utc(2024, 4, 28, 6)


def test_session_never_ends_before_the_time_it_was_opened_at():
    opened = _utc(2024, 4, 27, 23)
    assert activity_end(opened, [_utc(2024, 4, 27, 19)]) == _utc(2024, 4, 27, 23, 30)


def test_no_activity_evidence_leaves_the_decision_to_the_caller():
    assert activity_end(_utc(2024, 4, 27, 18), []) is None
    assert activity_end(_utc(2024, 4, 27, 18), [None]) is None


def test_clock_plays_past_00z_but_stops_at_the_session_end():
    tc = TimeController(_utc(2024, 4, 27, 23, 59, 50))
    tc.step(30)
    assert tc.current_time == _utc(2024, 4, 28, 0, 0, 20)

    tc.set_window_end(_utc(2024, 4, 28, 0, 10))
    tc.step(3600)
    assert tc.current_time == _utc(2024, 4, 28, 0, 9, 59)


def test_shrinking_the_window_pulls_the_clock_back_inside():
    tc = TimeController(_utc(2024, 4, 27, 20))
    tc.set_time(_utc(2024, 4, 28, 3))
    emitted = []
    tc.window_changed.connect(lambda start, end: emitted.append(end))

    tc.set_window_end(_utc(2024, 4, 27, 21, 40))

    assert emitted == [_utc(2024, 4, 27, 21, 40)]
    assert tc.current_time == _utc(2024, 4, 27, 21, 39, 59)
    assert tc.window_seconds() == int(timedelta(hours=21, minutes=40).total_seconds())


def test_slider_positions_are_seconds_since_the_sessions_midnight():
    tc = TimeController(_utc(2024, 4, 27, 12))
    tc.set_seconds_since_start(int(timedelta(hours=25, minutes=30).total_seconds()))
    assert tc.current_time == _utc(2024, 4, 28, 1, 30)
    assert tc.seconds_since_start() == int(timedelta(hours=25, minutes=30).total_seconds())
