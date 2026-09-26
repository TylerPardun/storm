from datetime import datetime, timezone

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.sounding import Sounding
from core.storm_motion import StormMotion


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def _sounding():
    n = 40
    return Sounding(
        lat=35.0, lon=-98.0, valid_time=datetime(2024, 4, 27, 20, tzinfo=timezone.utc),
        slot_offset=0, label="Analysis",
        pressure=np.linspace(1000, 200, n), temperature=np.linspace(28, -55, n),
        dewpoint=np.linspace(24, -80, n), u_wind=np.linspace(2, 30, n), v_wind=np.linspace(8, 5, n),
        height=np.linspace(300, 12000, n),
    )


def test_observed_motion_adds_srh_without_changing_bunkers_values():
    import metpy.calc as mc
    from metpy.units import units
    from ui.sounding.dialog import SoundingDialog
    snd = _sounding()
    dialog = SoundingDialog()
    dialog._update_params(snd)
    bunkers = dialog._param_labels["srh01"].text()
    assert dialog._param_labels["srh01_obs"].text() == "—"

    dialog.set_observed_storm_motion(StormMotion(12.0, 6.0))
    dialog._update_params(snd)
    assert dialog._param_labels["srh01"].text() == bunkers          # Bunkers-based SRH unchanged
    expected, _, _ = mc.storm_relative_helicity(
        (snd.height - snd.height[0]) * units.m, snd.u_wind * units("m/s"), snd.v_wind * units("m/s"),
        depth=1 * units.km, storm_u=12 * units("m/s"), storm_v=6 * units("m/s"))
    assert float(dialog._param_labels["srh01_obs"].text()) == pytest.approx(expected.m, abs=1)

    dialog.set_observed_storm_motion(None)
    dialog._update_params(snd)
    assert dialog._param_labels["srh01_obs"].text() == "—"
