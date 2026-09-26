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
    "_shortcut_focus_is_text_entry",
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


class _Clock:
    def __init__(self, now):
        self.current_time = now
        self.window = (now.replace(hour=0, minute=0, second=0), now.replace(hour=0) + timedelta(hours=30))


class _Button:
    def isChecked(self):
        return True


def _window(tmp_path, monkeypatch, now=datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)):
    stub_cls = type("TrackWindow", (), {name: MainWindow.__dict__[name] for name in _METHODS})
    w = stub_cls()
    w.map_widget = _Map()
    w._time_ctrl = _Clock(now)
    w.status_msg_label = _Label()
    w.track_controls = TrackControls()
    w.btn_track = _Button()
    w._current_track_radar_context = lambda: ("KTLX", "N0B", "Reflectivity", 0.5)
    w._init_track_state()
    w._track_edit_active = True
    monkeypatch.setattr("ui.app.main_window.default_track_dir", lambda: tmp_path)
    monkeypatch.setattr("core.storm_track.default_track_dir", lambda: tmp_path)
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

    assert w._track_saved_path == tmp_path / "storm_20240427_2000_track.csv"
    df = pd.read_csv(w._track_saved_path)
    assert df["lat"].tolist() == [35.0, 35.1]
    assert df["radar_site"].tolist() == ["KTLX", "KTLX"]
    assert len(_points(w)) == 2


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
    w._on_track_point_add(35.1, -97.1)
    w._on_track_point_select(1)
    w._delete_selected_track_point()
    assert [p.point_id for p in w._track_points] == [2]

    w._undo_track_edit()
    assert [p.point_id for p in w._track_points] == [1, 2]
    w._redo_track_edit()
    assert [p.point_id for p in w._track_points] == [2]
    assert len(pd.read_csv(w._track_saved_path)) == 1  # the file follows every step


def test_loading_a_meso_view_track_then_editing_saves_back_into_it(tmp_path, monkeypatch):
    track = tmp_path / "T10_20190528_2200_autosave_edited.csv"
    track.write_text(_MESO_VIEW_CSV)
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(track), ""))
    w = _window(tmp_path, monkeypatch, now=datetime(2019, 5, 28, 22, 3, tzinfo=timezone.utc))

    w._load_track_file()
    assert len(w._track_points) == 2 and w._track_saved_path == track
    assert "Loaded 2 track points" in w.status_msg_label.text
    assert list(tmp_path.iterdir()) == [track]   # loading alone writes nothing

    w._on_track_point_add(39.05, -99.02)
    df = pd.read_csv(track)
    assert len(df) == 3 and set(df["case_id"]) == {"T10"}   # still a MESO-VIEW T10 track

    _yes(monkeypatch)
    w._reset_track_to_original()
    assert len(pd.read_csv(track)) == 2
    w._undo_track_edit()                                  # reset itself can be undone
    assert len(w._track_points) == 3


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
