import json
from datetime import datetime, timedelta, timezone

import pytest

from core.studio import Keyframe, Overlays, Project, View, ease, interpolate

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)
A = View(lon=-98.9, lat=34.0, zoom=8.0)
B = View(lon=-98.5, lat=34.3, zoom=10.0, bearing=350.0)
C = View(lon=-98.0, lat=34.6, zoom=9.0)


def two(fps=10, easing="smooth"):
    p = Project(fps=fps)
    p.add(Keyframe(0.0, T0, A, easing=easing))
    p.add(Keyframe(10.0, T0 + timedelta(minutes=10), B))
    return p


def test_a_segment_plays_case_time_at_a_constant_rate():
    p = two()
    assert p.duration() == pytest.approx(10.0) and p.segment_speed(0) == pytest.approx(60.0)
    frames = p.plan()
    assert len(frames) == p.frame_count() == 101
    assert frames[0] == (T0, A) and frames[-1] == (T0 + timedelta(minutes=10), B)
    assert frames[50][0] == T0 + timedelta(minutes=5)


def test_camera_eases_while_the_clock_stays_linear():
    early = two().plan()[10][1]                              # 10 % of the way
    assert early.zoom - A.zoom < 0.1 * (B.zoom - A.zoom)     # smooth starts slowly
    assert two(easing="linear").plan()[10][1].zoom == pytest.approx(A.zoom + 0.2)


def test_holds_stay_put_and_ripple_later_keyframes():
    p = two()
    p.set_hold(p.keyframes[0], 2.0)
    assert p.keyframes[1].at == pytest.approx(12.0) and p.duration() == pytest.approx(12.0)
    assert p.evaluate(1.9) == (T0, A)
    p.set_hold(p.keyframes[1], 1.5)                          # a hold at the end lengthens the movie
    assert p.duration() == pytest.approx(13.5) and p.evaluate(13.0)[1] == B


def test_speed_and_length_edits_ripple():
    p = two()
    p.append(T0 + timedelta(minutes=20), C)                   # 600 case s at the default 300x: 2 s
    assert p.keyframes[2].at == pytest.approx(12.0)
    p.set_segment_speed(0, 120.0)                             # 600 s at 120x: 5 s
    assert [k.at for k in p.keyframes] == pytest.approx([0.0, 5.0, 7.0])
    p.set_segment_length(1, 4.0)
    assert p.duration() == pytest.approx(9.0) and p.segment_speed(1) == pytest.approx(150.0)


def test_inserting_inside_a_segment_splits_it_without_moving_anything():
    p = two()
    mid = p.add(Keyframe(4.0, T0 + timedelta(minutes=2), C))
    assert [k.at for k in p.keyframes] == pytest.approx([0.0, 4.0, 10.0]) and p.keyframes[1] is mid
    # on top of an existing keyframe: the later ones make room
    p.add(Keyframe(4.0, T0 + timedelta(minutes=2), A))
    assert len({round(k.at, 6) for k in p.keyframes}) == 4 and p.duration() > 10.0


def test_a_camera_only_move_keeps_the_clock_still():
    p = Project(fps=10)
    p.add(Keyframe(0.0, T0, A))
    p.append(T0, B)
    assert p.duration() == pytest.approx(2.0) and p.segment_speed(0) == 0.0
    assert all(t == T0 for t, _ in p.plan()) and p.plan()[-1][1] == B


def test_moving_keyframes_with_and_without_ripple():
    p = two()
    p.append(T0 + timedelta(minutes=20), C)
    mid, last = p.keyframes[1], p.keyframes[2]
    p.move(mid, 6.0)                                          # ripple: the last one comes along
    assert (mid.at, last.at) == pytest.approx((6.0, 8.0))
    p.move(mid, 20.0, ripple=False)                           # stays before its neighbor
    assert mid.at < last.at and last.at == pytest.approx(8.0)
    p.move(p.keyframes[0], 3.0)                               # the first keyframe stays at the start
    assert p.keyframes[0].at == 0.0


def test_removing_with_and_without_ripple():
    p = two()
    p.append(T0 + timedelta(minutes=20), C)
    p.remove(p.keyframes[1])
    assert [k.at for k in p.keyframes] == pytest.approx([0.0, 12.0])
    q = two()
    q.append(T0 + timedelta(minutes=20), C)
    q.remove(q.keyframes[1], ripple=True)                     # 20 case min at 300x: 4 s
    assert q.keyframes[1].at == pytest.approx(4.0)
    q.remove(q.keyframes[0])                                  # the next becomes the start
    assert q.keyframes[0].at == 0.0 and q.duration() == 0.0


def test_interpolation_takes_the_short_way_round_and_ends_exactly():
    v = interpolate(View(0, 0, 5, bearing=350), View(0, 0, 5, bearing=10), 0.5)
    assert v.bearing == pytest.approx(0.0) or v.bearing == pytest.approx(360.0)
    end = interpolate(A, B, 1.0)
    assert (end.lon, round(end.lat, 9), end.zoom) == (B.lon, round(B.lat, 9), B.zoom)
    assert [ease(e, 0) for e in ("smooth", "linear", "ease-in", "ease-out")] == [0, 0, 0, 0]
    assert [ease(e, 1) for e in ("smooth", "linear", "ease-in", "ease-out")] == [1, 1, 1, 1]


def test_a_project_round_trips_through_json(tmp_path):
    p = Project(fps=24, resolution="4K (3840×2160)", format="gif",
                overlays=Overlays(time_stamp=True, status_line=False, legend=True), name="electra")
    p.add(Keyframe(0.0, T0, A, easing="ease-in", hold=0.5))
    p.add(Keyframe(4.5, T0 + timedelta(minutes=20), B))
    q = Project.load(p.save(tmp_path / "electra.json"))
    assert q.to_json() == p.to_json() and q.keyframes[0].time == T0


def test_version_1_projects_are_laid_out_in_movie_time(tmp_path):
    v1 = {"format_version": 1, "name": "old", "fps": 30, "keyframes": [
        {"time": "2024-04-27T20:00:00Z", "view": vars(A), "speed": 60, "hold": 1.0, "duration": None},
        {"time": "2024-04-27T20:10:00Z", "view": vars(B), "speed": 60, "hold": 0.0, "duration": 4.0},
        {"time": "2024-04-27T20:10:00Z", "view": vars(C), "speed": 60, "hold": 0.0, "duration": None},
    ]}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(v1))
    p = Project.load(path)
    assert [k.at for k in p.keyframes] == pytest.approx([0.0, 11.0, 15.0])


def test_time_steps_hold_the_clock_like_a_radar_loop():
    p = two()                                                  # 10 min of case over 10 s at 10 fps
    p.time_step = "smooth"
    assert p.distinct_frames() == 101
    p.time_step = "1 min"
    times = [t for t, _ in p.plan()]
    assert all(t.second == 0 for t in times) and len(set(times)) == 11
    scans = [T0 + timedelta(minutes=m, seconds=13) for m in (0, 4, 8)]
    p.time_step = "radar scans"
    assert sorted(set(t for t, _ in p.plan(scans))) == [T0] + scans   # before the first scan: minutes
    assert two().to_json()["time_step"] == "1 min"


def test_projects_without_a_time_step_stay_smooth():
    d = two().to_json()
    del d["time_step"]
    assert Project.from_json(d).time_step == "smooth"


def test_the_whole_movie_speeds_up_or_slows_down_together():
    p = two()
    p.append(T0 + timedelta(minutes=20), C)
    p.set_hold(p.keyframes[1], 1.0)
    p.retime(0.5)                                             # 2x faster
    assert [k.at for k in p.keyframes] == pytest.approx([0.0, 5.0, 6.5]) and p.keyframes[1].hold == 0.5
    assert p.segment_speed(0) == pytest.approx(120.0)
    p.set_duration(13.0)
    assert p.duration() == pytest.approx(13.0)


def test_a_keyframe_switches_the_radar_from_there_on(tmp_path):
    p = two()
    p.keyframes[0].radar = {"station": "KUEX", "product": "reflectivity", "tilt": 0}
    p.append(T0 + timedelta(minutes=20), C).radar = {"station": "KOAX", "product": "velocity", "tilt": 1}
    assert p.radar_at(0)["station"] == p.radar_at(9.9)["station"] == "KUEX"   # the keyframe without one keeps it
    assert p.radar_at(12.0)["station"] == "KOAX"
    q = Project.load(p.save(tmp_path / "radar.json"))
    assert q.radar_at(12.0) == {"station": "KOAX", "product": "velocity", "tilt": 1}
