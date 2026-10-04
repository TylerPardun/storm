from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

from core.lidar_scans import classify_scans, scan_at

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


def _ppi(start, number, el=2.0):
    return (start, [(a, el) for a in range(0, 360, 45)], 2, number)


def test_only_low_angle_azimuth_sweeps_are_scans():
    rays = _rays([
        (0, [(a, 60.0) for a in range(0, 360, 45)], 2, 1),            # VAD ring: high elevation
        (100, [(a, 3.0) for a in range(200, 300, 2)], 0.5, 2),        # low sector PPI
        (200, [(320.6, e) for e in np.arange(0, 80, 2.0)], 0.5, 3),   # elevation sweep
        (400, [(0.0, 90.0)] * 5, 3, np.nan),                          # fixed pointing up
        (500, [(300.0, 1.0)] * 10, 3, np.nan),                        # fixed pointing low
    ])
    scans = classify_scans(rays)
    assert len(scans) == 1 and scans[0].rays == 50 and scans[0].elevation_deg == 3.0
    assert scans[0].describe() == "PPI 18:01:40Z · 3.0° · 50 rays"


def test_fixed_pointing_isnt_a_sweep_while_the_truck_turns():
    rays = _rays([(0, [(300.0, 2.0)] * 20, 3, np.nan)])
    rays.instrument_azimuth_deg = rays.azimuth_deg.copy()                  # as pointed: fixed
    rays.azimuth_deg = (rays.azimuth_deg + np.linspace(0, 90, 20)) % 360   # true north: the truck turned
    assert classify_scans(rays) == []


def test_a_scan_number_spanning_elevations_is_split():
    rays = _rays([(0, [(a, 2.0) for a in range(0, 360, 30)] + [(a, 8.0) for a in range(0, 360, 30)], 1, 1)])
    assert [s.elevation_deg for s in classify_scans(rays)] == [2.0, 8.0]


def test_the_map_shows_the_latest_scan_held_until_the_next():
    scans = classify_scans(_rays([_ppi(0, 1), _ppi(600, 2)]))
    assert len(scans) == 2
    assert scan_at(scans, T0 - timedelta(minutes=1)) is None               # before any scan
    assert scan_at(scans, T0 + timedelta(minutes=5)) is scans[0]           # held until the next
    assert scan_at(scans, T0 + timedelta(minutes=10, seconds=30)) is scans[1]
    assert scan_at(scans, T0 + timedelta(minutes=40)) is None              # too old to show


def test_unknown_truck_heading_is_carried_on_the_scan():
    rays = _rays([_ppi(0, 1)])
    rays.provenance = {"north_referenced": False}
    rays.azimuth_known = None
    assert not classify_scans(rays)[0].north_referenced


def test_a_lidars_files_make_one_timeline_with_scanning_periods():
    from core.lidar_scans import describe_periods, periods, timeline
    early = _rays([_ppi(0, 1), _ppi(600, 2)])
    late = _rays([_ppi(3600, 3)])
    for rays in (early, late):
        rays.scans = classify_scans(rays)
    scans = timeline([late, early])
    assert [s.source for s in scans] == [early, early, late]
    spans = periods(scans)
    assert len(spans) == 2                                   # 18:00-18:10 and 19:00
    assert describe_periods(spans) == "Scanning 18:00–18:10Z, 19:00–19:00Z"
    next_day = [(T0 + timedelta(hours=6), T0 + timedelta(hours=8))]     # 00:00-02:00Z the next day
    assert describe_periods(next_day) == "Scanning 00:00–02:00Z"
    across = [(T0, T0 + timedelta(hours=8))]
    assert describe_periods(across) == "Scanning 18:00Z May 17 – 02:00Z May 18"
    assert [s.source for s in timeline([early, late], start=T0 + timedelta(minutes=30))] == [late]


def test_a_scan_running_past_the_session_start_is_cut_to_the_session():
    from core.lidar_scans import timeline
    rays = _rays([(0, [(a % 360, 2.0) for a in range(0, 1000, 10)], 6, 1)])     # 18:00 onward, 10 min
    rays.scans = classify_scans(rays)
    [scan] = timeline([rays], start=T0 + timedelta(minutes=5))
    assert scan.start == T0 + timedelta(minutes=5) and scan.rays == 50 and scan.source is rays


def test_the_truck_has_one_location_per_stop_and_a_trailer_one_at_its_site():
    from core.lidar_scans import locations
    rays = _rays([_ppi(0, 1),             # stop 1
                  _ppi(600, 2),           # stop 1 again, 100 m on
                  _ppi(1200, 3)])         # stop 2, 5 km away
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
