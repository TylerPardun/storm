from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from core import derived
from core.observation import Observation
from core.storm_motion import StormMotion
from core.storm_track import TrackPoint
from core.trails import TrailBuilder, color_stops

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)


def _obs(vid, seconds, lat, lon, t=25.0, td=20.0, p=950.0, spd=10.0, wdir=180.0):
    return Observation(vid, lat, lon, T0 + timedelta(seconds=seconds), temperature_c=t, dewpoint_c=td,
                       pressure_mb=p, wind_speed_ms=spd, wind_dir_deg=wdir)


def test_thermodynamics_match_metpy_for_a_fixture_row():
    import metpy.calc as mc
    from metpy.units import units
    cols = derived.observation_arrays([_obs("p1", 0, 35, -98)])
    out = derived.compute(cols)
    p, t, td = 950 * units.hPa, 25 * units.degC, 20 * units.degC
    assert out["theta_e"][0] == pytest.approx(mc.equivalent_potential_temperature(p, t, td).m)
    assert out["theta"][0] == pytest.approx(mc.potential_temperature(p, t).m)
    assert out["mixing_ratio"][0] == pytest.approx(mc.saturation_mixing_ratio(p, td).to("g/kg").m)
    assert out["u"][0] == pytest.approx(0, abs=1e-9) and out["v"][0] == pytest.approx(10)   # from the south


def test_missing_pressure_is_not_assumed():
    out = derived.compute(derived.observation_arrays([_obs("p1", 0, 35, -98, p=None)]))
    assert np.isnan(out["theta"][0]) and np.isnan(out["theta_e"][0])
    assert out["temperature"][0] == 25 and np.isfinite(out["rh"][0])       # these don't need pressure


def test_radial_and_tangential_wind_about_the_storm_center():
    track = [TrackPoint(1, T0, 35.0, -98.0), TrackPoint(2, T0 + timedelta(minutes=10), 35.0, -98.0)]
    obs = [_obs("p1", 60, 35.0, -97.9, spd=10, wdir=270)]    # due east of a stationary center, wind from the west
    cols = derived.observation_arrays(obs)
    sr = derived.storm_relative(cols, derived.compute(cols), track, StormMotion(0.0, 0.0))
    assert sr["radial_wind"][0] == pytest.approx(10, abs=1e-6)             # blowing outward
    assert sr["tangential_wind"][0] == pytest.approx(0, abs=1e-6)
    late = derived.observation_arrays([_obs("p1", 3600, 35.0, -97.9)])    # after the track ends
    assert np.isnan(derived.storm_relative(late, derived.compute(late), track, StormMotion(0, 0))["radial_wind"][0])


def test_trails_window_gap_and_values():
    obs = [_obs("p1", s, 35 + s * 1e-4, -98, t=20 + s / 60) for s in range(0, 600, 10)]
    obs += [_obs("p1", 1200 + s, 35.1, -98 + s * 1e-4) for s in range(0, 120, 10)]   # after a 10-min gap
    fc, (vmin, vmax), n = TrailBuilder().build({"p1": obs}, "temperature", T0, T0 + timedelta(minutes=30))
    segments = [f for f in fc["features"] if f["properties"]["kind"] == "trail"]
    assert n == len(segments) == 59 + 11                                   # nothing bridges the gap
    assert vmin < vmax
    fc, _, n = TrailBuilder().build({"p1": obs}, "temperature", T0 + timedelta(minutes=5), T0 + timedelta(minutes=8))
    assert n == 18                                                         # 300-480 s: 19 points, 18 segments


def test_time_to_space_collapses_a_platform_moving_with_the_storm():
    track = [TrackPoint(1, T0, 35.0, -98.0), TrackPoint(2, T0 + timedelta(minutes=20), 35.0, -97.8)]
    # a probe 5 km north of the center the whole time, moving with it
    obs = [_obs("p1", s, 35.0 + 5 / 111.19, -98.0 + 0.2 * s / 1200) for s in range(0, 1200, 30)]
    fc, _, n = TrailBuilder().build({"p1": obs}, "temperature", T0, T0 + timedelta(minutes=19),
                                    track_points=track, time_to_space=True)
    coords = np.array([c for f in fc["features"] if f["properties"]["kind"] == "trail"
                       for c in f["geometry"]["coordinates"]])
    assert n > 10 and np.ptp(coords[:, 0]) < 1e-3 and np.ptp(coords[:, 1]) < 1e-3
    assert {f["properties"]["kind"] for f in fc["features"]} >= {"ring", "center"}


def test_color_scale_is_symmetric_for_signed_winds():
    stops = color_stops("radial_wind", -4, 4)
    values = stops[0::2]
    assert values[0] == -values[-1] and values[0] <= -4            # symmetric, covering the range
    middle = len(values) // 2                                      # an odd number of bands: one is centered on 0
    assert values[middle - 1] < 0 < values[middle]


def test_sea_level_pressure_platforms_get_no_pressure_based_values():
    obs = [_obs("ASOS KSPS", s, 33.98, -98.49, p=1013.0) for s in range(0, 600, 60)]
    for key, blank in (("theta_e", True), ("pressure", True), ("mixing_ratio", True), ("temperature", False)):
        builder = TrailBuilder()
        track = [TrackPoint(1, T0, 34.0, -98.8), TrackPoint(2, T0 + timedelta(minutes=10), 34.0, -98.6)]
        fc, _, n = builder.build({"ASOS KSPS": obs}, key, T0, T0 + timedelta(minutes=9),
                                 track_points=track, motion=StormMotion(10, 0), time_to_space=True,
                                 no_station_pressure=frozenset({"ASOS KSPS"}))
        assert (n == 0) == blank, key


def test_wind_barbs_along_a_trail_only_when_asked():
    from core.trails import BARBS_PER_TRAIL
    obs = [_obs("p1", s, 35 + s * 1e-4, -98, spd=12.9, wdir=225.0) for s in range(0, 1800, 2)]   # 25 kt from SW
    start, end = T0, T0 + timedelta(minutes=30)
    fc, _, _ = TrailBuilder().build({"p1": obs}, "temperature", start, end)
    assert not [f for f in fc["features"] if f["properties"]["kind"] == "barb"]
    fc, _, _ = TrailBuilder().build({"p1": obs}, "temperature", start, end, wind_barbs=True)
    barbs = [f["properties"] for f in fc["features"] if f["properties"]["kind"] == "barb"]
    assert BARBS_PER_TRAIL - 1 <= len(barbs) <= BARBS_PER_TRAIL + 1          # evenly spaced, not one per fix
    assert {(b["img"], b["dir"], b["kt"]) for b in barbs} == {("barb-25", 225.0, 25)}


def test_barb_icons_round_to_five_knots():
    from core.trails import barb_image
    assert [barb_image(k) for k in (0, 2.4, 2.6, 7.4, 7.6, 52)] == ["barb-0", "barb-0", "barb-5", "barb-5", "barb-10", "barb-50"]


def test_barbs_skip_missing_wind():
    obs = [_obs("p1", s, 35, -98 + s * 1e-4, spd=None) for s in range(0, 600, 2)]
    fc, _, _ = TrailBuilder().build({"p1": obs}, "temperature", T0, T0 + timedelta(minutes=10), wind_barbs=True)
    assert not [f for f in fc["features"] if f["properties"]["kind"] == "barb"]


def test_storm_relative_barbs_subtract_the_storm_motion():
    # platform in a 10 m/s southerly; storm moving north at 10 m/s -> calm storm-relative
    track = [TrackPoint(1, T0, 35.0, -98.0), TrackPoint(2, T0 + timedelta(minutes=30), 35.0 + 18.0 / 111.2, -98.0)]
    from core.storm_motion import mean_motion
    motion = mean_motion(track)
    obs = [_obs("p1", s, 35.1, -98 + s * 1e-5, spd=10.0, wdir=180.0) for s in range(0, 1800, 2)]
    args = ({"p1": obs}, "temperature", T0, T0 + timedelta(minutes=30))
    fc, _, _ = TrailBuilder().build(*args, track_points=track, motion=motion, wind_barbs=True)
    ground = [f["properties"] for f in fc["features"] if f["properties"]["kind"] == "barb"]
    fc, _, _ = TrailBuilder().build(*args, track_points=track, motion=motion, wind_barbs=True, storm_relative_barbs=True)
    relative = [f["properties"] for f in fc["features"] if f["properties"]["kind"] == "barb"]
    assert {b["img"] for b in ground} == {"barb-20"} and not any(b["sr"] for b in ground)
    assert {b["img"] for b in relative} == {"barb-0"} and all(b["sr"] for b in relative)


def _oklab(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _drawn(color, opacity, background):
    """`color` at `opacity` over `background`, as the map shows it."""
    mix = [round(opacity * int(color[i:i + 2], 16) + (1 - opacity) * int(background[i:i + 2], 16))
           for i in (1, 3, 5)]
    return "#" + "".join(f"{c:02x}" for c in mix)


def test_trail_colors_are_readable_on_the_map_and_outlined():
    """Every band color keeps 3:1 contrast with the dark map, and a dark
    outline is drawn under the trails (so their hues needn't avoid roads)."""
    from pathlib import Path
    from core import trails
    for kind in trails.PALETTES:
        color = trails.palette(kind)
        for i in range(11):
            assert trails.contrast(color(i / 10), trails.MAP_BACKGROUND) >= trails.MIN_CONTRAST, (kind, i)
    html = (Path(__file__).resolve().parents[1] / "ui" / "map" / "map_template.html").read_text()
    assert html.index("id: 'obs-trails-casing'") < html.index("id: 'obs-trails-line'")


def test_bands_have_round_edges_and_a_zero_band_for_signed_winds():
    from core.trails import band_edges, bin_edges, color_stops
    edges = bin_edges(331.3, 349.6)
    assert edges[0] <= 331.3 and edges[-1] >= 349.6 and 6 <= len(edges) - 1 <= 14
    assert all(abs(e * 2 - round(e * 2)) < 1e-9 for e in edges)          # round numbers
    signed = bin_edges(-7.3, 7.3, diverging=True)
    assert signed == [-x for x in reversed(signed)]                      # symmetric
    assert any(a < 0 < b for a, b in zip(signed, signed[1:]))            # a band straddles zero
    stops = color_stops("theta_e", 331.3, 349.6)
    assert band_edges(stops) == edges
    values = stops[0::2]
    assert all(b > a for a, b in zip(values, values[1:]))                # strictly increasing for MapLibre
    assert stops[1] == stops[3] and stops[3] != stops[5]                 # hard steps between bands
