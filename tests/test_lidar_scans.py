from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

from core.lidar_scans import classify_scans, counts, nearest_scan_time, scan_at, step

T0 = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)


def _rays(segments):
    """segments: [(start_s, [(az, el), ...], ray_spacing_s, scan_number)]"""
    t, az, el, sn = [], [], [], []
    for start, pointing, spacing, number in segments:
        for i, (a, e) in enumerate(pointing):
            t.append(T0.timestamp() + start + i * spacing)
            az.append(a), el.append(e), sn.append(number)
    return SimpleNamespace(time_epoch=np.array(t), azimuth_deg=np.array(az, float),
                           elevation_deg=np.array(el, float), scan_number=np.array(sn, float))


def test_scans_are_named_by_geometry_not_by_file():
    rays = _rays([
        (0, [(a, 60.0) for a in range(0, 360, 45)], 2, 1),        # VAD ring
        (100, [(a, 3.0) for a in range(200, 300, 2)], 0.5, 2),    # low sector PPI
        (200, [(320.6, e) for e in np.arange(0, 80, 2.0)], 0.5, 3),   # RHI
        (400, [(0.0, 90.0)] * 5, 3, np.nan),                      # vertical stare...
        (430, [(0.0, 90.0)] * 5, 3, np.nan),                      # ...continued after a pause
    ])
    scans = classify_scans(rays)
    assert [s.kind for s in scans] == ["VAD", "PPI", "RHI", "Stare"]
    assert scans[3].rays == 10 and scans[2].azimuth_deg == 320.6
    assert [s.mappable for s in scans] == [True, True, False, False]
    assert counts(scans) == "1 PPI · 1 VAD · 1 RHI · 1 vertical stare"


def test_the_map_shows_the_latest_plan_view_scan_and_the_clock_can_jump_to_one():
    rays = _rays([(0, [(a, 60.0) for a in range(0, 360, 45)], 2, 1),
                  (600, [(a, 60.0) for a in range(0, 360, 45)], 2, 2)])
    scans = classify_scans(rays)
    assert scan_at(scans, T0 - timedelta(minutes=1)) is None               # before any scan
    assert scan_at(scans, T0 + timedelta(minutes=5)) is scans[0]           # held until the next
    assert scan_at(scans, T0 + timedelta(minutes=10, seconds=30)) is scans[1]
    assert scan_at(scans, T0 + timedelta(minutes=40)) is None              # too old to show
    assert nearest_scan_time(scans, T0 - timedelta(hours=1)) == scans[0].end   # first one ahead
    assert nearest_scan_time(scans, T0 + timedelta(hours=1)) == scans[1].end   # else the last one
    assert step(scans, scans[0].end, +1) is scans[1]
    assert step(scans, scans[1].end, -1) is scans[0]


def test_a_stare_stays_a_stare_while_the_truck_turns():
    rays = _rays([(0, [(300.0, 90.0)] * 20, 3, np.nan)])
    rays.instrument_azimuth_deg = rays.azimuth_deg.copy()          # as pointed: fixed
    rays.azimuth_deg = (rays.azimuth_deg + np.linspace(0, 90, 20)) % 360   # true north: the truck turned
    scans = classify_scans(rays)
    assert [s.kind for s in scans] == ["Stare"] and scans[0].rays == 20


def test_an_rhi_says_when_its_azimuth_is_only_relative_to_the_truck():
    rays = _rays([(0, [(320.6, e) for e in np.arange(0, 80, 2.0)], 0.5, 1)])
    rays.provenance = {"north_referenced": False}
    rays.azimuth_known = None
    scan = classify_scans(rays)[0]
    assert scan.kind == "RHI" and not scan.north_referenced
    assert "relative to the truck (heading unknown)" in scan.describe()


def test_a_lidars_files_make_one_timeline_with_scanning_periods():
    from core.lidar_scans import describe_periods, periods, scanning_at, timeline
    vad = _rays([(0, [(a, 60.0) for a in range(0, 360, 45)], 2, 1),
                 (600, [(a, 60.0) for a in range(0, 360, 45)], 2, 2)])
    stares = _rays([(3600, [(0.0, 90.0)] * 10, 3, np.nan)])
    for rays in (vad, stares):
        rays.scans = classify_scans(rays)
    scans = timeline([stares, vad])
    assert [s.kind for s in scans] == ["VAD", "VAD", "Stare"]
    assert scans[0].source is vad and scans[2].source is stares
    spans = periods(scans)
    assert len(spans) == 2                                   # 18:00-18:10 and 19:00
    assert describe_periods(spans) == "Scanning 18:00–18:10Z, 19:00–19:00Z"
    late = [(T0 + timedelta(hours=6), T0 + timedelta(hours=8))]          # 00:00-02:00Z the next day
    assert describe_periods(late) == "Scanning 00:00–02:00Z"
    across = [(T0, T0 + timedelta(hours=8))]
    assert describe_periods(across) == "Scanning 18:00Z May 17 – 02:00Z May 18"
    assert scanning_at(scans, T0 + timedelta(minutes=5)) and not scanning_at(scans, T0 + timedelta(minutes=40))
    assert [s.kind for s in timeline([vad, stares], start=T0 + timedelta(minutes=30))] == ["Stare"]


def test_a_low_angle_stare_is_a_beam_for_the_map_not_a_vertical_stare():
    rays = _rays([(0, [(300.0, 1.0)] * 10, 3, np.nan), (100, [(0.0, 90.0)] * 10, 3, np.nan)])
    scans = classify_scans(rays)
    assert [s.kind for s in scans] == ["Beam", "Stare"]
    assert scans[0].mappable and not scans[1].mappable


def test_the_truck_has_one_location_per_stop_and_a_trailer_one_at_its_site():
    from core.lidar_scans import locations
    rays = _rays([(0, [(a, 60.0) for a in range(0, 360, 45)], 2, 1),        # stop 1
                  (600, [(a, 60.0) for a in range(0, 360, 45)], 2, 2),      # stop 1 again, 100 m on
                  (1200, [(a, 60.0) for a in range(0, 360, 45)], 2, 3)])    # stop 2, 5 km away
    n = rays.time_epoch.size
    rays.latitude = np.array([35.0] * 8 + [35.0009] * 8 + [35.045] * 8)
    rays.longitude = np.full(n, -98.0)
    scans = classify_scans(rays)
    stops, unplaced = locations("DLTRUCK1", scans)
    assert [len(s.scans) for s in stops] == [2, 1] and unplaced == 0
    assert (stops[0].number, stops[0].lat, stops[1].lat) == (1, 35.0, 35.045)
    assert stops[0].start == scans[0].start and stops[0].end == scans[1].end
    site, _ = locations("CLAMPS1", scans, site={"lat": 34.98, "lon": -97.52, "description": "NWC Vehicle Bay"})
    assert len(site) == 1 and len(site[0].scans) == 3 and site[0].place == "NWC Vehicle Bay"


def test_a_stare_running_past_the_session_start_is_cut_to_the_session():
    from core.lidar_scans import timeline
    rays = _rays([(0, [(0.0, 90.0)] * 100, 60, np.nan)])       # 18:00 onward, one ray a minute
    rays.scans = classify_scans(rays)
    [stare] = timeline([rays], start=T0 + timedelta(minutes=30))
    assert stare.start == T0 + timedelta(minutes=30) and stare.rays == 70 and stare.source is rays
