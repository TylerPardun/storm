import pytest
from PyQt6.QtWidgets import QApplication

from ui.dialogs.case_export_dialog import CaseExportDialog


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def test_everything_is_included_by_default_and_can_be_unticked():
    d = CaseExportDialog([("radar", "WSR-88D radar volumes", 5, 72_000_000),
                          ("satellite", "Satellite imagery", 52, 400_000_000)], tracks=2)
    assert d.choices() == (True, {"radar", "satellite"})
    assert "472.0 MB" in d._total.text()
    d._boxes["satellite"].setChecked(False)
    assert d.choices() == (True, {"radar"}) and "72.0 MB" in d._total.text()
    d._set_all(False)
    assert d.choices() == (False, set())


def test_no_tracks_means_the_tracks_box_is_off_and_disabled():
    d = CaseExportDialog([("radar", "WSR-88D radar volumes", 1, 10)], tracks=0)
    assert not d._boxes["tracks"].isEnabled() and d.choices() == (False, {"radar"})
