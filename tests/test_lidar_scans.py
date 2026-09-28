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
    assert counts(scans) == "1 PPI · 1 VAD · 1 RHI · 1 stare"


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
