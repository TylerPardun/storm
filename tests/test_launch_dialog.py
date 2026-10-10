"""Archive discovery UI: calendar behavior, asynchronous state and teardown."""
from datetime import date, datetime, timezone

import pytest
from PyQt6.QtCore import QDate, QDateTime, QRect, QTime, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSpinBox, QToolButton

from archive.catalog import ALL_PLATFORMS, AvailabilitySnapshot, PlatformAvailability
from ui.launch.dialog import LaunchDialog, _AvailabilityCalendar, _YearGridPopup
from ui.launch.availability import AvailabilityWorker


@pytest.fixture(autouse=True)
def no_launch_network(monkeypatch):
    monkeypatch.setattr(LaunchDialog, "_start_update_check", lambda self: None)
    monkeypatch.setattr(AvailabilityWorker, "request", lambda *args: None)


def _dialog():
    return QApplication.instance(), LaunchDialog()


def _snapshot(dates=(), failed=False, partial=False):
    results = {p.platform_id: PlatformAvailability(frozenset(), 1, 1, ()) for p in ALL_PLATFORMS}
    results[ALL_PLATFORMS[0].platform_id] = PlatformAvailability(
        frozenset(dates), 1, 2 if partial else 1, ("server timed out",) if failed else ())
    return AvailabilitySnapshot(results, len(results), len(results) + int(partial))


def _apply(dlg, snapshot):
    dlg._select_mode("archive")
    dlg._on_availability_updated(dlg._availability_generation, snapshot)


def test_calendar_button_exists_and_date_field_still_supports_typed_entry():
    _, dlg = _dialog()
    assert dlg._cal_btn is not None
    # typed auto-formatting and the built-in popup remain intact --
    # the new button is additive, not a replacement.
    assert dlg._archive_dt_edit.displayFormat() == "yyyy-MM-dd  HH:mm:ss"
    assert dlg._archive_dt_edit.calendarPopup() is True


def test_clicking_calendar_button_opens_popup_preset_to_current_field_date():
    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2023, 6, 15), QTime(14, 30, 0)))

    dlg._cal_btn.click()

    assert dlg._calendar_popup is not None
    assert dlg._calendar_popup.isVisible()
    assert dlg._calendar_popup.selectedDate() == QDate(2023, 6, 15)


def test_on_date_picked_applies_date_and_preserves_time():
    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2023, 6, 15), QTime(14, 30, 45)))
    dlg._cal_btn.click()  # creates the popup

    dlg._on_date_picked(QDate(2022, 5, 24))

    result = dlg._archive_dt_edit.dateTime()
    assert result.date() == QDate(2022, 5, 24)
    assert result.time() == QTime(14, 30, 45)
    assert dlg._calendar_popup.isVisible() is False


def test_on_date_picked_matches_archive_start_time_output():
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2023, 6, 15), QTime(20, 0, 0)))
    dlg._cal_btn.click()

    dlg._on_date_picked(QDate(2019, 5, 28))

    assert dlg.archive_start_time() == datetime(2019, 5, 28, 20, 0, 0, tzinfo=timezone.utc)


def test_reopening_calendar_button_reuses_the_same_popup_instance():
    _, dlg = _dialog()
    dlg._cal_btn.click()
    first_popup = dlg._calendar_popup

    dlg._cal_btn.click()

    assert dlg._calendar_popup is first_popup


def test_combo_box_dropdown_highlight_matches_the_calendar_month_popup():
    # Regression test: QComboBox's own selection-background-color/
    # selection-color (not just the QComboBox QAbstractItemView rule) is
    # what actually renders a combo box dropdown's highlighted row --
    # these previously stayed at the old solid #00CFFF while every other
    # popup in this dialog (the calendar's month picker) used the softer
    # #123C50/#9BE8FF treatment, so campaign/year/instrument dropdowns
    # visibly clashed with the rest of the design.
    from ui.launch.styles import _DIALOG_STYLE
    import re
    combo_block = re.search(r"QComboBox \{[^}]*\}", _DIALOG_STYLE).group()
    menu_item_block = re.search(r"QCalendarWidget QMenu::item:selected \{[^}]*\}", _DIALOG_STYLE).group()
    assert "#123C50" in combo_block and "#9BE8FF" in combo_block
    assert "#123C50" in menu_item_block and "#9BE8FF" in menu_item_block


def test_availability_calendar_disallows_dates_after_today():
    calendar = _AvailabilityCalendar()
    assert calendar.maximumDate() == QDate.currentDate()


def _freeze_today(monkeypatch, year, month=6, day=15):
    # _YearGridPopup clamps its range to "no years beyond today" -- pin
    # "today" so these tests don't drift as real time passes.
    import ui.launch.dialog as dialog_module
    monkeypatch.setattr(dialog_module.QDate, "currentDate", staticmethod(lambda: QDate(year, month, day)))


def test_year_grid_popup_shows_a_dozen_years_centered_on_current_and_marks_it_selected(monkeypatch):
    _freeze_today(monkeypatch, 2030)  # comfortably past the centered window, no clamping
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)

    assert popup._range_lbl.text() == "2017 – 2028"
    assert [b.text() for b in popup._buttons] == [str(y) for y in range(2017, 2029)]
    selected = [b for b in popup._buttons if b.property("selected")]
    assert [b.text() for b in selected] == ["2022"]


def test_year_grid_popup_never_shows_or_pages_past_the_current_year(monkeypatch):
    # Field deployments have no data from the future -- the popup should
    # neither open on nor page into a range beyond "today".
    _freeze_today(monkeypatch, 2022)
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)

    assert popup._range_lbl.text() == "2011 – 2022"
    assert [b.text() for b in popup._buttons] == [str(y) for y in range(2011, 2023)]
    assert popup._next_btn.isEnabled() is False

    popup._shift(_YearGridPopup._COUNT)  # attempting to page forward is a no-op at the boundary
    assert popup._range_lbl.text() == "2011 – 2022"


def test_year_grid_popup_shift_pages_the_range_by_a_full_grid(monkeypatch):
    _freeze_today(monkeypatch, 2050)  # comfortably past the shifted-forward window too
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)

    popup._shift(_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "2029 – 2040"

    popup._shift(-_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "2017 – 2028"


def test_year_grid_popup_pick_emits_the_picked_year_and_closes(monkeypatch):
    _freeze_today(monkeypatch, 2030)
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)
    picked = []
    popup.yearPicked.connect(picked.append)
    popup.show()

    popup._pick(3)  # start_year (2017) + index 3 -> 2020

    assert picked == [2020]
    assert popup.isVisible() is False


def test_calendar_year_button_has_a_click_consumer_installed_and_spin_is_locked_down():
    # qt_calendar_yearbutton is the widget the user actually sees/clicks;
    # qt_calendar_yearedit is Qt's own hidden-until-revealed editable
    # field behind it -- locked down defensively in case that reveal
    # ever still runs (see _style_calendar_nav_icons).
    _, dlg = _dialog()
    dlg._cal_btn.click()  # builds the popup calendar and wires its year button

    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    spin = dlg._calendar_popup.findChild(QSpinBox, "qt_calendar_yearedit")

    assert year_btn is not None
    assert spin is not None
    assert spin.isReadOnly() is True
    assert spin.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert spin.buttonSymbols() == QSpinBox.ButtonSymbols.NoButtons
    assert len(dlg._year_click_filters) >= 1


def test_open_year_grid_seeds_the_popup_from_the_calendars_shown_year():
    _, dlg = _dialog()
    dlg._cal_btn.click()
    calendar = dlg._calendar_popup
    calendar.setCurrentPage(2019, 3)
    year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")

    dlg._open_year_grid(calendar, year_btn)

    assert dlg._year_grid_popup is not None
    assert dlg._year_grid_popup._current_year == 2019


def test_on_year_picked_moves_the_calendar_to_that_year_keeping_the_month():
    _, dlg = _dialog()
    dlg._cal_btn.click()
    calendar = dlg._calendar_popup
    calendar.setCurrentPage(2023, 6)

    dlg._on_year_picked(calendar, 2019)

    assert calendar.yearShown() == 2019
    assert calendar.monthShown() == 6


def test_a_real_click_on_the_year_button_opens_the_grid_in_one_click():
    # Regression test for the "must click twice" bug: clicking
    # qt_calendar_yearbutton used to trigger Qt's own built-in
    # reveal-an-editable-spinbox behavior first (which looks exactly
    # like "now type a year"), and only a second click, now landing on
    # the newly-revealed spinbox, opened the grid. _ClickConsumer
    # swallows the press before Qt's internal handler ever runs, so one
    # real click is enough.
    _, dlg = _dialog()
    dlg._cal_btn.click()
    calendar = dlg._calendar_popup
    calendar.setCurrentPage(2023, 6)
    year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")

    assert dlg._year_grid_popup is None
    QTest.mouseClick(year_btn, Qt.MouseButton.LeftButton)

    assert dlg._year_grid_popup is not None
    assert dlg._year_grid_popup.isVisible() is True
    assert dlg._year_grid_popup._current_year == 2023
    # and Qt's own spinbox-reveal never fired: the year button is still
    # the visible control, not swapped out for the (locked-down) spinbox
    assert year_btn.isVisibleTo(calendar) is True


def test_startup_archive_schedules_discovery_and_date_changes_debounce(monkeypatch):
    _, dlg = _dialog()
    calls, canceled = [], []
    monkeypatch.setattr(dlg._availability, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(dlg._availability, "cancel", lambda: canceled.append(True))
    dlg._select_mode("archive")
    dlg.show()
    assert dlg._availability_timer.isActive()
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    generation = dlg._availability_generation
    QTest.qWait(350)
    assert calls == [(generation,)]
    assert len(canceled) >= 3
    dlg._select_mode("viewer")
    assert not dlg._availability_timer.isActive()


def test_late_result_cannot_overwrite_new_date_or_resume_closed_dialog():
    _, dlg = _dialog()
    dlg._select_mode("archive")
    old_generation = dlg._availability_generation
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    dlg._on_availability_updated(old_generation, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    dlg.show()
    generation = dlg._availability_generation
    dlg.close()
    dlg._on_availability_updated(generation, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    assert not dlg._availability_timer.isActive()


def test_known_dates_mark_both_calendars_and_year_grid():
    _, dlg = _dialog()
    dates = {date(2024, 4, 27), date(2015, 5, 15)}
    _apply(dlg, _snapshot(dates))
    dlg._cal_btn.click()
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        assert calendar._known_dates == dates
    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    dlg._open_year_grid(dlg._calendar_popup, year_btn)
    assert dlg._year_grid_popup._known_years == {2015, 2024}


def test_date_presence_keeps_errors_distinct_from_absence(caplog):
    _, dlg = _dialog()
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    _apply(dlg, _snapshot([date(2024, 4, 27)], failed=True))
    assert "server timed out" in caplog.text
    _apply(dlg, _snapshot(failed=True))
    assert not dlg._archive_scan_progress.isVisibleTo(dlg)
    _apply(dlg, _snapshot())


def test_partial_snapshot_keeps_progress_and_positive_dates():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)], partial=True))
    assert dlg._archive_scan_progress.isVisibleTo(dlg)
    assert len(dlg._archive_dt_edit.calendarWidget()._known_dates) == 1


def test_small_screen_scrolls_form_without_overlap_and_keeps_launch_visible(monkeypatch):
    from types import SimpleNamespace
    from PyQt6.QtCore import QRect
    _, dlg = _dialog()
    monkeypatch.setattr(dlg, "screen", lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 800, 600)))
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    dlg.show()
    dlg._toggle_data_section()          # make the form taller than the screen
    QTest.qWait(30)
    assert dlg.height() <= 540
    assert dlg._form_scroll.verticalScrollBar().maximum() > 0
    assert dlg._footer.geometry().bottom() < dlg.height()
    assert dlg._launch_btn.isVisibleTo(dlg)
    dlg._form_scroll.verticalScrollBar().setValue(dlg._form_scroll.verticalScrollBar().maximum())
    assert dlg._footer.geometry().bottom() < dlg.height()


def test_year_grid_opens_from_keyboard():
    _, dlg = _dialog()
    dlg._cal_btn.click()
    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    QTest.keyClick(year_btn, Qt.Key.Key_Space)
    assert dlg._year_grid_popup.isVisible()


def test_saved_archive_mode_hides_live_data_controls_after_construction(monkeypatch):
    from ui.launch import dialog as module
    class SavedSettings:
        def value(self, key, default, **kwargs):
            return {"launch/mode": "archive", "launch/auto_spc": True}.get(key, default)
    monkeypatch.setattr(module, "QSettings", SavedSettings)
    _, dlg = _dialog()
    assert dlg._selected_mode == "archive"
    assert dlg._data_toggle_btn.isHidden()
    assert dlg._data_section.isHidden()


def test_deleting_dialog_cancels_pending_layout_callbacks():
    from PyQt6.QtCore import QCoreApplication, QEvent
    _, dlg = _dialog()
    dlg._toggle_data_section()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Formerly a context-free singleShot(self.adjustSize) survived deletion
    # and raised from a Qt callback, aborting the native macOS test process.
    QTest.qWait(20)


def test_calendar_shades_known_dates():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    dlg._cal_btn.click()
    target = QDate(2024, 4, 27)
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        calendar.setSelectedDate(target.addDays(-1))
        cell = _render_calendar_cell(calendar, target)
        assert cell.pixelColor(8, 7).name() == '#123c50'
        assert cell.pixelColor(0, 0).name() == '#0d0d1a'
        # Bright text must be painted above the availability fill.
        assert any(
            cell.pixelColor(x, y).red() > 220
            for x in range(10, 34) for y in range(8, 26)
        )
        calendar.setSelectedDate(target)
        assert calendar.selectedDate() == target
        assert _render_calendar_cell(calendar, target).pixelColor(8, 7).name() == '#00cfff'
        calendar.setSelectedDate(target.addDays(-1))


def _render_calendar_cell(calendar, day):
    image = QImage(44, 34, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    calendar.paintCell(painter, QRect(0, 0, 44, 34), day)
    painter.end()
    return image


def test_surprise_me_picks_a_known_date_and_keeps_the_current_time():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27), date(2024, 5, 3)]))
    dlg._archive_dt_edit.setTime(QTime(20, 0, 0))

    dlg._on_surprise_me_clicked()

    picked = dlg._archive_dt_edit.date().toPyDate()
    assert picked in (date(2024, 4, 27), date(2024, 5, 3))
    assert dlg._archive_dt_edit.time() == QTime(20, 0, 0)


def test_surprise_me_with_no_snapshot_yet_shows_a_message_and_does_not_crash(monkeypatch):
    shown = []
    monkeypatch.setattr(
        "ui.launch.dialog.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args),
    )
    _, dlg = _dialog()
    dlg._select_mode("archive")
    assert dlg._availability_snapshot is None

    dlg._on_surprise_me_clicked()

    assert len(shown) == 1


def test_circular_scan_progress_tracks_the_snapshots_checked_and_total():
    """The wheel next to the date picker is the only scan-progress
    indicator now (the old linear bar in the browse drawer was removed as
    redundant) -- it should track the snapshot's checked/total and be
    visible only while the scan is still running."""
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)], partial=True))
    assert dlg._archive_scan_progress.isVisibleTo(dlg)
    snapshot = dlg._availability_snapshot
    assert dlg._archive_scan_progress._maximum == snapshot.total
    assert dlg._archive_scan_progress._value == snapshot.checked

    _apply(dlg, _snapshot())
    assert not dlg._archive_scan_progress.isVisibleTo(dlg)


def test_calendar_has_no_static_tooltip():
    _, dlg = _dialog()
    assert dlg._archive_dt_edit.calendarWidget().toolTip() == ""
