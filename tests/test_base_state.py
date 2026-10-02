import math
from datetime import datetime, timezone

import numpy as np
import pytest

from core import base_state as bs

UTC = timezone.utc


def columns(nz_t=None, ny=3, nx=3):
    """A uniform atmosphere on the 8 levels: z rises 250 m per level from 100 m,
    T falls 6.5 K/km from 30 C, RH 50 %, wind (5, 2) m/s grid-relative."""
    levels = np.array(bs.LEVELS_HPA)
    gh = np.array([100.0 + 250 * i for i in range(levels.size)])[:, None, None] * np.ones((1, ny, nx))
    t = 30.0 - 0.0065 * gh if nz_t is None else nz_t
    lat = 35.0 + 0.1 * np.arange(ny)[:, None] * np.ones((1, nx))
    lon = -97.0 + 0.1 * np.arange(nx)[None, :] * np.ones((ny, 1))
    return bs.Columns(levels=levels, gh=gh, t=t, rh=np.full(gh.shape, 50.0), u=np.full(gh.shape, 5.0),
                      v=np.full(gh.shape, 2.0), lat=lat, lon=lon, rotation=np.zeros((ny, nx)))


def test_state_between_levels_is_interpolated_in_height_and_log_pressure():
    when = datetime(2024, 4, 27, 21, tzinfo=UTC)
    s = bs.compute(columns(), 225.0, time=when, hour=when, center=(35.1, -96.9))
    assert s.n_points == 9 and s.model == "RAP"
    assert s.temp == pytest.approx(30.0 - 0.0065 * 225.0)
    assert s.pres == pytest.approx(math.exp(0.5 * math.log(1000) + 0.5 * math.log(975)))
    assert s.rh == pytest.approx(50.0, abs=0.5)
    assert (s.u, s.v) == (pytest.approx(5.0), pytest.approx(2.0))
    assert s.th > s.temp + 273.15 and s.thv > s.th and s.the > s.thv


def test_points_beyond_two_sigma_are_left_out():
    when = datetime(2009, 5, 27, 1, tzinfo=UTC)
    c = columns(ny=4, nx=4)
    c.t[:, 0, 0] += 15.0                       # one warm outlier column
    s = bs.compute(c, 225.0, time=when, hour=when, center=(35.1, -96.9))
    assert s.n_points == 15 and s.model == "RUC"
    assert s.temp == pytest.approx(30.0 - 0.0065 * 225.0)


def test_grid_relative_winds_are_turned_to_true_north():
    when = datetime(2024, 4, 27, 21, tzinfo=UTC)
    c = columns()
    c.rotation[:] = math.radians(90)           # grid x axis points south
    s = bs.compute(c, 225.0, time=when, hour=when, center=(35.1, -96.9))
    assert (s.u_grid, s.v_grid) == (pytest.approx(5.0), pytest.approx(2.0))
    assert (s.u, s.v) == (pytest.approx(2.0), pytest.approx(-5.0))


def test_model_hour_and_sources_by_era():
    assert bs.model_hour(datetime(2024, 4, 27, 20, 29, tzinfo=UTC)).hour == 20
    assert bs.model_hour(datetime(2024, 4, 27, 20, 30, tzinfo=UTC)).hour == 21
    ruc = bs.analysis_urls(datetime(2009, 5, 27, 1, tzinfo=UTC))
    assert ruc == ["https://www.ncei.noaa.gov/thredds/fileServer/model-ruc130anl/200905/20090527/"
                   "ruc2anl_130_20090527_0100_000.grb2"]
    assert "/historical/analysis/201905/20190526/rap_130_20190526_2200_000.grb2" in \
        bs.analysis_urls(datetime(2019, 5, 26, 22, tzinfo=UTC))[0]
    assert "/rap-130-13km/analysis/" in bs.analysis_urls(datetime(2024, 4, 27, 21, tzinfo=UTC))[0]


def test_analysis_windows_cover_plus_minus_two_and_a_half_minutes():
    t0 = datetime(2024, 4, 27, 21, 0, tzinfo=UTC).timestamp()
    assert bs.analysis_epoch(t0 + 149) == t0 and bs.analysis_epoch(t0 - 150) == t0
    assert bs.analysis_epoch(t0 + 150) == t0 + 300
    assert bs.analysis_epochs(t0, t0 + 600) == [t0, t0 + 300, t0 + 600]


def _mobile(t0, n=100):
    t = t0 - 120 + 4.0 * np.arange(n)
    return {"time": t, "lat": np.linspace(35.0, 35.1, n), "lon": np.linspace(-97.2, -97.0, n)}


def test_inputs_use_the_track_center_and_the_mobile_footprint():
    from core.storm_track import TrackPoint
    t0 = int(datetime(2024, 4, 27, 21, 0, tzinfo=UTC).timestamp())
    boxes = []
    elevation = lambda lat, lon: boxes.append((lat.min(), lat.max(), lon.min(), lon.max())) or 400.0
    track = [TrackPoint(1, datetime(2024, 4, 27, 20, 50, tzinfo=UTC), 35.5, -97.5),
             TrackPoint(2, datetime(2024, 4, 27, 21, 10, tzinfo=UTC), 35.7, -97.1)]
    got = bs.inputs_for(t0, [_mobile(t0)], track, elevation=elevation)
    assert got.center_source == "track" and got.center == (pytest.approx(35.6), pytest.approx(-97.3))
    assert got.z_m == 400.0 and got.n_obs == 68          # 4-s data from t0-120; window ends t0+150
    no_track = bs.inputs_for(t0, [_mobile(t0)], [], elevation=elevation)
    assert no_track.center_source == "observations"
    assert bs.inputs_for(t0 + 3600, [_mobile(t0)], track, elevation=elevation) is None


def test_series_lookup_and_trail_perturbations():
    from core import derived
    from core.trails import TrailBuilder
    from core.observation import Observation
    t0 = int(datetime(2024, 4, 27, 21, 0, tzinfo=UTC).timestamp())
    series = bs.BaseStateSeries()
    when = datetime.fromtimestamp(t0, UTC)
    state = bs.compute(columns(), 225.0, time=when, hour=when, center=(35.1, -96.9))
    series.put(bs.Inputs(t0, (35.1, -96.9), "track", 225.0, 10), state)
    assert series.values("thv", np.array([t0 - 100, t0 + 200])).tolist()[0] == pytest.approx(state.thv)
    assert math.isnan(series.values("thv", np.array([t0 + 200]))[0])

    obs = [Observation(vehicle_id="p1", timestamp=datetime.fromtimestamp(t0 - 60 + 10 * i, UTC),
                       lat=35.0 + 0.001 * i, lon=-97.0, temperature_c=28.0, dewpoint_c=18.0,
                       pressure_mb=975.0, wind_speed_ms=5.0, wind_dir_deg=180.0) for i in range(12)]
    fc, _, n = TrailBuilder().build({"p1": obs}, "theta_v_p", datetime.fromtimestamp(t0 - 60, UTC),
                                    datetime.fromtimestamp(t0 + 60, UTC), base_states=series)
    assert n == 11
    expected = derived.compute(derived.observation_arrays(obs[:1]))["theta_v"][0] - state.thv
    assert fc["features"][0]["properties"]["v"] == pytest.approx(expected, abs=1e-3)
    _, _, none = TrailBuilder().build({"p1": obs}, "theta_v_p", datetime.fromtimestamp(t0 - 60, UTC),
                                      datetime.fromtimestamp(t0 + 60, UTC))
    assert none == 0                                    # no base state: nothing drawn


def test_every_quantity_is_offered_in_trails():
    from core.derived import QUANTITIES
    from ui.controls.trail_controls import _GROUPS
    assert {k for _, keys in _GROUPS for k in keys} == set(QUANTITIES)


def test_unreadable_terrain_is_a_retryable_failure_not_no_data():
    t0 = int(datetime(2024, 4, 27, 21, 0, tzinfo=UTC).timestamp())
    with pytest.raises(ConnectionError):
        bs.inputs_for(t0, [_mobile(t0)], [], elevation=lambda lat, lon: float("nan"))


def test_an_unreachable_analysis_is_retryable_and_a_missing_one_is_not(tmp_path, monkeypatch):
    from urllib.error import HTTPError, URLError
    from core import package_sources
    when = datetime(2024, 4, 27, 21, tzinfo=UTC)

    def missing(*_a, **_k):
        raise HTTPError("u", 404, "not found", {}, None)
    monkeypatch.setattr(package_sources, "read_url", missing)
    assert bs.fetch_analysis(when, tmp_path) is None

    def offline(*_a, **_k):
        raise URLError("no route")
    monkeypatch.setattr(package_sources, "read_url", offline)
    with pytest.raises(ConnectionError):
        bs.fetch_analysis(when, tmp_path)
