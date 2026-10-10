"""Wind-profile time-height grid and the VAD window's time-height view."""
from datetime import datetime, timedelta, timezone

import numpy as np
from PyQt6.QtWidgets import QApplication

from core.vad import VADProfile, VADSet, time_height_grid

T0 = datetime(2026, 5, 17, 19, 0, tzinfo=timezone.utc)


def _profile(minutes, heights=(10.0, 20.0, 30.0), speed=20.0, direction=180.0):
    n = len(heights)
    return VADProfile(T0 + timedelta(minutes=minutes), "DL1", np.array(heights, float),
                      np.full(n, direction), np.full(n, speed))


def test_profiles_share_one_height_grid_without_extrapolation():
    vs = VADSet([_profile(0, (10, 20, 30)), _profile(1, (10, 20))])
    grid = time_height_grid(vs, dz_m=5.0)
    assert grid.u_kt.shape == (len(grid.heights_m), 2)
    top = np.argmin(np.abs(grid.heights_m - 30.0))
    assert np.isfinite(grid.speed_kt[top, 0]) and np.isnan(grid.speed_kt[top, 1])   # 2nd stops at 20 m
    assert np.allclose(grid.speed_kt[np.isfinite(grid.speed_kt)], 20.0)
    assert np.allclose(grid.v_kt[np.isfinite(grid.v_kt)], 20.0)       # from the south: v positive


def test_a_lone_profile_beside_a_gap_stays_narrow():
    vs = VADSet([_profile(0), _profile(1), _profile(2), _profile(40), _profile(41)])
    edges, column = time_height_grid(vs).column_edges()
    assert list(column) == [0, 1, 2, -1, 3, 4]                          # the gap is its own empty column
    widths = np.diff(edges)
    assert widths[column == 2][0] <= 60.0 + 1e-6                        # 1-min spacing: at most a minute wide
    assert widths[column == -1][0] > 30 * 60                            # the gap stays empty


def test_window_opens_at_the_clock_and_draws_time_height():
    QApplication.instance()
    from ui.dialogs.vad_dialog import VADDialog
    vs = VADSet([_profile(m) for m in (0, 10, 20, 30)])
    dlg = VADDialog("DL1", preloaded_set=vs, start_time=T0 + timedelta(minutes=25))
    dlg._fetch_data()
    assert dlg._cur_idx == 2                                            # latest at or before 19:25
    dlg._set_view("time_height")
    assert dlg._th_ax is not None and dlg._hodo_ax is None
    dlg._set_view("hodograph")
    assert dlg._hodo_ax is not None
