"""Tests for the archive date picker's calendar button/popup."""

from datetime import datetime, timezone

from PyQt6.QtCore import QDate, QDateTime, QTime
from PyQt6.QtWidgets import QApplication

from ui.launch.dialog import LaunchDialog


def _dialog():
    app = QApplication.instance() or QApplication([])
    return app, LaunchDialog()


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
