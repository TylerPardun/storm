from datetime import date, datetime, timezone

import pytest
from PyQt6.QtWidgets import QApplication

from core.storm_track import TrackPoint
from ui.dialogs.track_table_dialog import TrackTableDialog, parse_time, rows_to_points

DAY = date(2024, 4, 27)
T = lambda h, m, s=0: datetime(2024, 4, 27, h, m, s, tzinfo=timezone.utc)
P1 = TrackPoint(1, T(20, 0), 35.0, -97.0, radar_site="KTLX", product="N0B", source="manual")
P2 = TrackPoint(2, T(20, 4), 35.1, -97.1, source="manual")


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def test_times_with_or_without_a_date():
    assert parse_time("2024-04-28 00:03:10", DAY) == datetime(2024, 4, 28, 0, 3, 10, tzinfo=timezone.utc)
    assert parse_time("20:05", DAY) == T(20, 5)
    assert parse_time("8pm", DAY) is None


def test_unchanged_rows_are_kept_as_they_were_and_edits_are_marked():
    originals = {1: P1, 2: P2}
    points, errors = rows_to_points(
        [(1, "2024-04-27 20:00:00", "35.00000", "-97.00000"), (2, "20:05:00", "35.2", "-97.1")],
        originals, DAY)
    assert not errors
    assert points[0] is P1                                   # radar context and provenance kept
    assert (points[1].time, points[1].lat, points[1].source) == (T(20, 5), 35.2, "table_edited")


def test_new_rows_get_fresh_ids_and_bad_cells_are_reported():
    points, errors = rows_to_points([(None, "20:10", "35.3", "-97.3")], {1: P1, 2: P2}, DAY)
    assert points[0].point_id == 3 and points[0].source == "table"
    _, errors = rows_to_points([(1, "later", "95", "abc")], {1: P1}, DAY)
    assert set(errors) == {(0, 1), (0, 2), (0, 3)}


def test_two_points_at_the_same_second_are_ambiguous():
    _, errors = rows_to_points(
        [(1, "20:00:00", "35", "-97"), (2, "2024-04-27 20:00:00", "35.1", "-97.1")], {1: P1, 2: P2}, DAY)
    assert (0, 1) in errors and (1, 1) in errors


def test_dialog_applies_only_valid_edits_and_keeps_them_across_map_changes():
    d = TrackTableDialog(DAY)
    applied = []
    d.apply_requested.connect(applied.append)
    d.load([P1, P2])
    assert not d._btn_apply.isEnabled()

    d._table.item(1, 2).setText("36.0")                     # edit P2's latitude
    assert d._btn_apply.isEnabled()
    d.load([P1])                                            # the map changed meanwhile
    assert d.rows()[1][2] == "36.0" and "changed on the map" in d._status.text()

    d._table.item(0, 1).setText("nonsense")
    assert d.apply() is False and applied == []
    d._table.item(0, 1).setText("20:00:00")
    assert d.apply() is True and [p.lat for p in applied[0]] == [35.0, 36.0]
    assert not d._btn_apply.isEnabled()


def test_selection_and_double_click_signals():
    d = TrackTableDialog(DAY)
    d.load([P1, P2])
    selected, jumps = [], []
    d.point_selected.connect(selected.append)
    d.jump_requested.connect(jumps.append)
    d.select_point(2)                                       # from the map: no echo back
    assert selected == [] and d._selected_point_id() == 2
    d._table.selectRow(0)
    assert selected == [1]
    d._on_double_clicked(1, 1)
    assert jumps == [2]


def test_add_row_uses_the_clock_and_delete_row_removes_it():
    d = TrackTableDialog(DAY)
    d.load([P1])
    d.set_default_time(T(20, 7, 30))
    d._add_row()
    assert d.rows()[1] == (None, "2024-04-27 20:07:30", "", "")
    d._table.selectRow(1)
    d._delete_row()
    assert len(d.rows()) == 1
