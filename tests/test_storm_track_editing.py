"""The TRACK tab's editing behaviour, run on MainWindow's own track methods
with the map, clock and radar stubbed out (MainWindow itself needs a live
map and network)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from ui.app.main_window import MainWindow
from ui.controls.track_controls import TrackControls

_METHODS = [
    "_init_track_state", "_push_track_geojson", "_nearest_track_point_id", "_autosave_track",
    "_refresh_track_controls", "_set_track_points", "_on_track_point_add", "_on_track_point_moved",
    "_on_track_point_select", "_delete_selected_track_point", "_undo_track_edit", "_redo_track_edit",
    "_load_track_file", "_reset_track_to_original", "_on_clear_track_requested",
    "_shortcut_focus_is_text_entry", "_delete_track_point", "_on_track_marker_add",
    "_on_track_marker_rename", "_on_track_marker_remove", "_add_track_point_at_marker",
    "track_storm_motion", "_refresh_track_motion", "_on_time_changed_update_track_highlight",
    "_track_workspace", "_track_session_day", "_refresh_track_workspace_lists",
    "_switch_track_workspace", "_new_track_workspace", "_open_track",
    "_open_track_table", "_apply_track_table", "_jump_to_track_point",
]

_MESO_VIEW_CSV = """point_id,time,lat,lon,source,case_id,track_file_kind,edited_at
36,2019-05-28 22:00:00,39.031152,-99.064058,manual,T10,complete,2026-05-31 22:29:23
35,2019-05-28 22:02:00,39.034136,-99.048691,manual,T10,complete,2026-05-31 22:29:23
"""


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _Map:
    def __init__(self):
        self.geojson = None

    def set_track_geojson(self, text):
        self.geojson = json.loads(text)


class _MarkerMap(_Map):
    def __init__(self):
        super().__init__()
        self.marker = "unset"

    def set_track_marker(self, marker):
        self.marker = None if marker is None else dict(marker)


class _Clock:
    def __init__(self, now):
        self.current_time = now
        self.window = (now.replace(hour=0, minute=0, second=0), now.replace(hour=0) + timedelta(hours=30))

    def pause(self):
        pass

    def set_time(self, when):
        self.current_time = when


class _Settings:
    """Stands in for QSettings so tests never touch the real preferences."""
    store: dict = {}

    def __init__(self, *args):
        pass

    def value(self, key, default=None, type=None):
        return self.store.get(key, default)

    def setValue(self, key, value):
        self.store[key] = value


class _Button:
    def isChecked(self):
        return True


def _window(tmp_path, monkeypatch, now=datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)):
    stub_cls = type("TrackWindow", (), {name: MainWindow.__dict__[name] for name in _METHODS})
    w = stub_cls()
    w.map_widget = _MarkerMap()
    w._track_marker = None
    w._time_ctrl = _Clock(now)
    w.status_msg_label = _Label()
    w.track_controls = TrackControls()
    w.btn_track = _Button()
    w._current_track_radar_context = lambda: ("KTLX", "N0B", "Reflectivity", 0.5)
    w._archive_time = now
    w._init_track_state()
    w._track_edit_active = True
    monkeypatch.setattr("ui.app.main_window.default_track_dir", lambda: tmp_path)
    monkeypatch.setattr("core.storm_track.default_track_dir", lambda: tmp_path)
    monkeypatch.setattr("core.workspace.workspaces_root", lambda: tmp_path / "ws")
    monkeypatch.setattr("ui.app.main_window.QSettings", _Settings)
    monkeypatch.setattr(_Settings, "store", {})
    w.track_controls.open_track_requested.connect(lambda path: w._open_track(Path(path)))
    w.notices = []                                          # stand-in for the save notice dialog
    w._notify_track_saved = lambda path, what="saved": w.notices.append((Path(path), what))
    return w


def _yes(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def _points(w):
    return [f for f in w.map_widget.geojson["features"] if f["geometry"]["type"] == "Point"]


def test_there_is_no_track_until_a_point_is_placed(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    assert w._track_points == [] and w._track_saved_path is None
    assert list(tmp_path.iterdir()) == []


def test_placing_points_autosaves_a_new_file(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    w._time_ctrl.current_time += timedelta(minutes=2)
    w._on_track_point_add(35.1, -97.1)

    case_dir = tmp_path / "ws" / "My work" / "20240427"
    assert w._track_saved_path == case_dir / "storm_20240427_2000_track.csv"
    df = pd.read_csv(w._track_saved_path)
    assert df["lat"].tolist() == [35.0, 35.1]
    assert df["radar_site"].tolist() == ["KTLX", "KTLX"]
    assert len(_points(w)) == 2
    manifest = json.loads((case_dir / "manifest.json").read_text())
    assert manifest["tracks"][0]["file"] == "storm_20240427_2000_track.csv"
    assert manifest["tracks"][0]["points"] == 2 and manifest["session_date"] == "2024-04-27"
    assert [p.name for p in case_dir.iterdir()] and not list(case_dir.glob(".*tmp"))  # no stray temp files


def test_dragging_a_point_retimes_it_unless_alt_is_held(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    w._time_ctrl.current_time += timedelta(minutes=5)

    w._on_track_point_moved(1, 35.5, -97.5, True)       # Alt held: keep the time
    assert (w._track_points[0].lat, w._track_points[0].time.minute) == (35.5, 0)
    assert w._track_points[0].source == "moved_position_only"

    w._on_track_point_moved(1, 35.6, -97.6, False)      # plain drag: now is when
    assert (w._track_points[0].lat, w._track_points[0].time.minute) == (35.6, 5)
    assert w._track_points[0].source == "moved_retimed"


def test_delete_undo_and_redo(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    w._time_ctrl.current_time += timedelta(minutes=2)
    w._on_track_point_add(35.1, -97.1)
    w._on_track_point_select(1)
    w._delete_selected_track_point()
    assert [p.point_id for p in w._track_points] == [2]

    w._undo_track_edit()
    assert [p.point_id for p in w._track_points] == [1, 2]
    w._redo_track_edit()
    assert [p.point_id for p in w._track_points] == [2]
    assert len(pd.read_csv(w._track_saved_path)) == 1  # the file follows every step


def test_an_imported_track_is_edited_as_a_workspace_copy(tmp_path, monkeypatch):
    track = tmp_path / "T10_20190528_2200_autosave_edited.csv"
    track.write_text(_MESO_VIEW_CSV)
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(track), ""))
    w = _window(tmp_path, monkeypatch, now=datetime(2019, 5, 28, 22, 3, tzinfo=timezone.utc))

    w._load_track_file()
    assert len(w._track_points) == 2 and w._track_saved_path is None
    assert "original is unchanged" in w.status_msg_label.text
    assert list(tmp_path.iterdir()) == [track]   # loading alone writes nothing

    w._on_track_point_add(39.05, -99.02)
    copy = tmp_path / "ws" / "My work" / "20190528" / track.name
    assert w._track_saved_path == copy
    df = pd.read_csv(copy)
    assert len(df) == 3 and set(df["case_id"]) == {"T10"}   # still a MESO-VIEW T10 track
    assert track.read_text() == _MESO_VIEW_CSV              # original untouched
    manifest = json.loads((copy.parent / "manifest.json").read_text())
    assert manifest["tracks"][0]["origin"] == str(track)

    _yes(monkeypatch)
    w._reset_track_to_original()
    assert len(pd.read_csv(copy)) == 2
    w._undo_track_edit()                                  # reset itself can be undone
    assert len(w._track_points) == 3


def test_a_workspace_track_reopens_and_edits_in_place(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    saved = w._track_saved_path
    listed = [w.track_controls._saved_combo.itemData(i) for i in range(w.track_controls._saved_combo.count())]
    assert listed == [str(saved)]

    w2 = _window(tmp_path, monkeypatch)                   # a later session on the same date
    w2._refresh_track_workspace_lists()
    w2.track_controls._btn_open_saved.click()
    assert [p.lat for p in w2._track_points] == [35.0] and w2._track_saved_path == saved
    w2._time_ctrl.current_time += timedelta(minutes=3)
    w2._on_track_point_add(35.1, -97.1)
    assert len(pd.read_csv(saved)) == 2 and len(list(saved.parent.glob("*.csv"))) == 1


def test_workspaces_keep_tracks_apart(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    mine = w._track_saved_path

    from PyQt6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Tyler / test", True))
    w._new_track_workspace()                              # unsafe characters are dropped
    assert w._track_workspace().name == "Tyler  test" and w._track_points == []
    # the other workspace's track for this date is still offered, tagged with its workspace
    assert w.track_controls._saved_combo.itemText(0).startswith(f"[My work] {mine.name}")

    w._on_track_point_add(36.0, -98.0)
    assert w._track_saved_path.parent == tmp_path / "ws" / "Tyler  test" / "20240427"
    assert len(pd.read_csv(mine)) == 1                    # the other workspace's track is untouched

    w._switch_track_workspace("My work")
    assert w.track_controls._saved_combo.itemData(0) == str(mine)


def test_a_track_from_another_date_says_so(tmp_path, monkeypatch):
    track = tmp_path / "T10_20190528_2200_autosave_edited.csv"
    track.write_text(_MESO_VIEW_CSV)
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(track), ""))
    w = _window(tmp_path, monkeypatch)                    # a 2024-04-27 session

    w._load_track_file()
    assert "none fall in this session (2019-05-28 track)" in w.status_msg_label.text


def test_unreadable_files_are_refused_without_losing_the_current_track(tmp_path, monkeypatch):
    bad = tmp_path / "notes.csv"
    bad.write_text("a,b\n1,2\n")
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    _yes(monkeypatch)
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)

    w._load_track_file()
    assert warnings and "notes.csv" in warnings[0]
    assert len(w._track_points) == 1 and w._track_loaded_path is None


def test_clear_starts_a_new_file_and_keeps_the_old_one(tmp_path, monkeypatch):
    _yes(monkeypatch)
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    first = w._track_saved_path

    w._on_clear_track_requested()
    assert w._track_points == [] and w._track_saved_path is None and first.exists()
    w._on_track_point_add(35.2, -97.2)
    assert w._track_saved_path != first                   # same minute, new file


def test_clicking_at_a_frame_that_has_a_point_moves_that_point(tmp_path, monkeypatch):
    """MESO-VIEW's way to refine an earlier point: step back to its frame
    and click -- one point per frame, never a stacked duplicate."""
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)                 # 20:00
    w._time_ctrl.current_time += timedelta(minutes=4)
    w._on_track_point_add(35.1, -97.1)                 # 20:04
    w._time_ctrl.current_time -= timedelta(minutes=4)  # step back to 20:00

    w._on_track_point_add(35.02, -97.03)
    assert [(p.point_id, p.lat) for p in w._track_points] == [(1, 35.02), (2, 35.1)]
    assert w._track_selected_id == 1 and "moved point 1" in w.status_msg_label.text
    w._undo_track_edit()
    assert w._track_points[0].lat == 35.0


def test_a_single_session_marker_and_adding_a_point_at_it(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._add_track_point_at_marker()
    assert w._track_points == [] and "no marker" in w.status_msg_label.text

    w._on_track_marker_add(35.3, -97.3)
    w._on_track_marker_add(35.4, -97.4)                # replaces: only ever one
    assert w.map_widget.marker == {"lat": 35.4, "lon": -97.4, "label": "M1"}

    w._add_track_point_at_marker()
    assert (w._track_points[0].lat, w._track_points[0].lon) == (35.4, -97.4)
    assert list(tmp_path.glob("*marker*")) == []       # the marker is never saved

    w._on_track_marker_remove()
    assert w._track_marker is None and w.map_widget.marker is None


def test_renaming_the_marker_keeps_its_label_when_moved(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("meso A", True))
    w = _window(tmp_path, monkeypatch)
    w._on_track_marker_add(35.3, -97.3)
    w._on_track_marker_rename()
    w._on_track_marker_add(35.5, -97.5)
    assert w.map_widget.marker["label"] == "meso A"


def test_right_click_delete_removes_that_point(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    w._time_ctrl.current_time += timedelta(minutes=2)
    w._on_track_point_add(35.1, -97.1)
    w._delete_track_point(1)
    assert [p.point_id for p in w._track_points] == [2]


class _Radar:
    def __init__(self, codes, current):
        self.codes, self.product = codes, current

    def current_product(self):
        return self.product

    def set_current_product(self, code):
        if code in self.codes:
            self.product = code


@pytest.mark.parametrize("codes, start, flipped", [
    (["reflectivity", "velocity", "differential_reflectivity"], "reflectivity", "velocity"),  # archive WSR-88D
    (["reflectivity", "velocity", "differential_reflectivity"], "velocity", "reflectivity"),
    (["reflectivity", "velocity", "differential_reflectivity"], "differential_reflectivity", "reflectivity"),
    (["DBZ", "VEL", "ZDR"], "DBZ", "VEL"),                                                     # NOXP
    (["N0B", "N0U"], "N0U", "N0B"),                                                            # live
])
def test_r_flips_reflectivity_and_velocity_for_every_radar_source(codes, start, flipped):
    w = type("W", (), {"_toggle_radar_product_shortcut": MainWindow.__dict__["_toggle_radar_product_shortcut"],
                       "_shortcut_focus_is_text_entry": lambda self: False})()
    w.radar_controls = _Radar(codes, start)
    w._toggle_radar_product_shortcut()
    assert w.radar_controls.product == flipped


def test_the_panel_shows_mean_and_current_storm_motion(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -98.0)
    assert "needs 2 points" in w.track_controls._motion_mean.text()
    w._time_ctrl.current_time += timedelta(minutes=10)
    w._on_track_point_add(35.0, -97.9)                      # due east
    assert w.track_storm_motion().direction_from_deg == pytest.approx(270)
    assert w.track_controls._motion_mean.text().startswith("Mean  from 270° at 15.2 m/s")
    assert "from 270°" in w.track_controls._motion_now.text()

    w._time_ctrl.current_time += timedelta(minutes=5)      # past the last point
    w._on_time_changed_update_track_highlight(w._time_ctrl.current_time)
    assert "outside the track" in w.track_controls._motion_now.text()


def test_the_table_follows_the_map_and_edits_come_back_as_one_undo_step(tmp_path, monkeypatch):
    from ui.dialogs.track_table_dialog import TrackTableDialog

    class _Unparented(TrackTableDialog):                    # the stub window isn't a QWidget
        def __init__(self, day, parent=None):
            super().__init__(day, None)
    monkeypatch.setattr("ui.dialogs.track_table_dialog.TrackTableDialog", _Unparented)
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    w._open_track_table()
    table = w._track_table
    w._time_ctrl.current_time += timedelta(minutes=4)
    w._on_track_point_add(35.1, -97.1)                      # placed on the map
    assert [r[0] for r in table.rows()] == [1, 2] and table._selected_point_id() is None

    w._on_track_point_select(2)                             # selected on the map
    assert table._selected_point_id() == 2
    table._table.selectRow(0)                               # selected in the table
    assert w._track_selected_id == 1

    table._table.item(0, 2).setText("35.05")
    table.apply()
    assert w._track_points[0].lat == 35.05 and "table edits applied" in w.status_msg_label.text
    w._undo_track_edit()
    assert w._track_points[0].lat == 35.0 and table.rows()[0][2] == "35.00000"

    table._on_double_clicked(1, 1)                          # go to point 2's time
    assert w._time_ctrl.current_time == w._track_points[1].time and w._track_selected_id == 2


def test_the_first_save_tells_the_user_where_the_track_went(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    w._on_track_point_add(35.0, -97.0)
    assert w.notices == [(w._track_saved_path, "saved")]
    assert w._track_saved_path.parent == tmp_path / "ws" / "My work" / "20240427"
    w._time_ctrl.current_time += timedelta(minutes=2)
    w._on_track_point_add(35.1, -97.1)
    assert len(w.notices) == 1                              # autosaves after the first don't nag


def test_shared_tracks_for_the_date_are_listed_and_open_as_a_copy(tmp_path, monkeypatch):
    shared = tmp_path / "ws" / "MESO-VIEW" / "20240427"
    shared.mkdir(parents=True)
    original = shared / "N38_20240427_1815_autosave_edited.csv"
    original.write_text("point_id,time,lat,lon,case_id\n1,2024-04-27 18:15:00,34.0,-99.0,N38\n"
                        "2,2024-04-27 18:20:00,34.05,-98.95,N38\n")
    before = original.read_text()
    w = _window(tmp_path, monkeypatch)
    w._refresh_track_workspace_lists()
    combo = w.track_controls._saved_combo
    assert combo.itemText(0).startswith("[MESO-VIEW] N38_20240427_1815") and combo.isEnabled()
    w.track_controls._btn_open_saved.click()
    w._on_track_point_add(34.1, -98.9)
    assert original.read_text() == before                   # the shared track is untouched
    assert w._track_saved_path == tmp_path / "ws" / "My work" / "20240427" / original.name
    assert pd.read_csv(w._track_saved_path)["case_id"].tolist() == ["N38"] * 3
