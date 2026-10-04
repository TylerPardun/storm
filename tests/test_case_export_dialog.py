from datetime import datetime, timezone

import pytest
from PyQt6.QtWidgets import QApplication

from ui.dialogs.case_export_dialog import CaseExportDialog

UTC = timezone.utc
SESSION = (datetime(2024, 4, 27, 12, tzinfo=UTC), datetime(2024, 4, 28, 6, tzinfo=UTC))
B = "https://unidata-nexrad-level2.s3.amazonaws.com/2024/04/27/KFDR/"


def radar(hhmmss, size):
    return {"kind": "radar", "url": f"{B}KFDR20240427_{hhmmss}_V06", "bytes": size}


SOURCES = [radar("200445", 36_000_000), radar("230000", 36_000_000),
           {"kind": "mesonet", "url": "https://x/probe1/qc_v2/20240427.txt", "bytes": 400_000_000}]


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def test_everything_is_included_by_default_and_can_be_unticked():
    d = CaseExportDialog(SOURCES, tracks=2, session=SESSION, default_frame=SESSION)
    assert d.choices() == (True, {"radar", "mesonet"}, None)      # whole session -> no frame
    assert "472.0 MB" in d._total.text()
    d._boxes["mesonet"].setChecked(False)
    assert d.choices()[:2] == (True, {"radar"}) and "72.0 MB" in d._total.text()
    d._set_all(False)
    assert d.choices()[:2] == (False, set())


def test_no_tracks_means_the_tracks_box_is_off_and_disabled():
    d = CaseExportDialog(SOURCES[:1], tracks=0, session=SESSION, default_frame=SESSION)
    assert not d._boxes["tracks"].isEnabled() and d.choices()[:2] == (False, {"radar"})


def test_the_time_frame_trims_scans_but_not_daily_files():
    frame = (datetime(2024, 4, 27, 20, tzinfo=UTC), datetime(2024, 4, 27, 21, tzinfo=UTC))
    d = CaseExportDialog(SOURCES, tracks=1, session=SESSION, default_frame=frame)
    assert d.choices()[2] == frame
    assert d._details["radar"].text() == "1 file · 36.0 MB"         # the 23Z volume is outside
    assert d._details["mesonet"].text().startswith("1 file")        # daily file kept whole
    d._whole_session()
    assert d._details["radar"].text().startswith("2 files") and d.choices()[2] is None


def test_an_end_before_the_start_cannot_be_exported():
    frame = (datetime(2024, 4, 27, 21, tzinfo=UTC), datetime(2024, 4, 27, 20, tzinfo=UTC))
    d = CaseExportDialog(SOURCES, tracks=1, session=SESSION, default_frame=frame)
    assert not d._export.isEnabled()


def test_files_not_loaded_yet_can_be_added():
    listed = [{"kind": "radar", "url": f"{B}KFDR20240427_201000_V06", "bytes": 14_000_000, "listed": True},
              {"kind": "raw lidar", "url": "https://t/clampsdlppiC2.b1.20240427.000000.cdf",
               "bytes": 338_400_000, "listed": True}]
    frame = (datetime(2024, 4, 27, 20, tzinfo=UTC), datetime(2024, 4, 27, 21, tzinfo=UTC))
    d = CaseExportDialog(SOURCES, tracks=0, session=SESSION, default_frame=frame, listed=listed)
    assert d.include_listed() and "(2 files · 352.4 MB)" in d._listed_box.text()
    assert d._details["radar"].text().startswith("2 files") and "Raw lidar scans" in d._boxes["raw lidar"].text()
    d._listed_box.setChecked(False)
    assert not d.include_listed() and d._details["radar"].text().startswith("1 file")
