"""Archive discovery UI: calendar behavior, asynchronous state and teardown."""
from datetime import date, datetime, timezone

import pytest
from PyQt6.QtCore import QDate, QDateTime, QTime, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSpinBox, QToolButton

from archive.catalog import ALL_PLATFORMS, AvailabilitySnapshot, PlatformAvailability
from ui.launch.dialog import LaunchDialog, _YearGridPopup
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


def test_browse_section_starts_collapsed_and_toggle_button_expands_it():
    # isVisibleTo(dlg), not isVisible(): the dialog itself is never shown in
    # this test, so isVisible() would be False regardless of the section's
    # own explicit visibility flag. _browse_section also lives inside
    # _archive_section, which is itself hidden outside archive mode -- select
    # that mode first so only _browse_section's own flag is under test.
    _, dlg = _dialog()
    dlg._select_mode("archive")
    assert dlg._browse_section.isVisibleTo(dlg) is False

    dlg._toggle_browse_section()

    assert dlg._browse_section.isVisibleTo(dlg) is True
    assert dlg._browse_toggle_btn.text().startswith("▾")


def test_populate_browse_year_combo_with_a_campaign_shows_only_its_years():
    _, dlg = _dialog()

    dlg._populate_browse_year_combo("LIFT")

    years = [dlg._browse_year_combo.itemData(i) for i in range(dlg._browse_year_combo.count())]
    assert years == [None, 2024, 2025, 2026]


def test_populate_browse_year_combo_with_no_campaign_shows_a_broad_year_range():
    # Not campaign-scoped: covers real pre-2009 history (confirmed live
    # 2026-09-08 -- probe9 has 2009-2010 VORTEX2-era data, mg1-3/
    # noxp_scout have 2015 data, none tied to any CAMPAIGN_YEARS entry)
    # rather than being capped to the union of campaign years.
    _, dlg = _dialog()

    dlg._populate_browse_year_combo(None)

    years = [dlg._browse_year_combo.itemData(i) for i in range(dlg._browse_year_combo.count())]
    assert years[0] is None
    assert 1999 in years
    assert 2009 in years and 2017 in years and 2026 in years
    assert years == sorted(years, key=lambda y: (y is not None, y))


def test_on_browse_campaign_changed_repopulates_years_for_the_selected_campaign():
    _, dlg = _dialog()

    index = dlg._browse_campaign_combo.findData("RiVorS")
    dlg._browse_campaign_combo.setCurrentIndex(index)  # fires _on_browse_campaign_changed

    years = [dlg._browse_year_combo.itemData(i) for i in range(dlg._browse_year_combo.count())]
    assert years == [None, 2017]


def test_post_layout_adjust_gives_the_coverage_label_enough_height_for_its_full_text():
    # Regression test: a word-wrapped QLabel's height as settled by a
    # plain QVBoxLayout reliably lands a few px under its own
    # heightForWidth() (confirmed live 2026-09-08 on a long multi-family
    # "Found! ..." summary) -- _post_layout_adjust must force it to the
    # correct height, not just resize the dialog around a short label.
    # No dlg.show()/QTest.qWait: adjustSize()/heightForWidth() are plain
    # layout computation and don't need a real on-screen window, and
    # skipping it keeps this test out of this file's rare pre-existing
    # calendar-construction crash (see planning/archive-browse-backlog.md)
    # since that only reproduces when many dialogs are actually shown.
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()
    lbl = dlg._browse_coverage_lbl
    # a long enough string to actually need multiple wrapped lines at
    # this dialog's width, exercising the same shortfall as a real
    # multi-family coverage result
    lbl.setText(
        "Found! 24 of 29 platforms have data for 2026-09-09:\n"
        "FOFS Mobile Mesonet: DL Truck (mesonet), Far Field, Hail Cam, MG1, MG2, "
        "MG3, NOXP Scout, Probe 1, Probe 2, Probe 3, Probe 4, Probe 5, Probe 7, "
        "Probe 9, Wind Sonde 1, Wind Sonde 2\n"
        "CLAMPS Winds: DL Truck — Lidar 1 (VAD), DL Truck — Lidar 2 (VAD), "
        "DL Truck — Lidar 1 (CSM), DL Truck — Lidar 2 (CSM), CLAMPS 1 (VAD), "
        "CLAMPS 2 (VAD)\n"
        "CLAMPS TROPoe: CLAMPS 1, CLAMPS 2"
    )

    dlg._post_layout_adjust()

    assert lbl.height() >= lbl.heightForWidth(lbl.width())


def test_post_layout_adjust_resets_the_coverage_labels_minimum_height_when_cleared():
    # so a later, shorter result doesn't stay stuck at a prior tall size
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()
    dlg._browse_coverage_lbl.setText("a\nb\nc\nd\ne\nf")
    dlg._post_layout_adjust()
    assert dlg._browse_coverage_lbl.minimumHeight() > 0

    dlg._browse_coverage_lbl.setText("")
    dlg._post_layout_adjust()

    assert dlg._browse_coverage_lbl.minimumHeight() == 0


def test_year_grid_popup_shows_a_dozen_years_centered_on_current_and_marks_it_selected():
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)

    assert popup._range_lbl.text() == "2017 – 2028"
    assert [b.text() for b in popup._buttons] == [str(y) for y in range(2017, 2029)]
    selected = [b for b in popup._buttons if b.property("selected")]
    assert [b.text() for b in selected] == ["2022"]


def test_year_grid_popup_shift_pages_the_range_by_a_full_grid():
    _, dlg = _dialog()
    popup = _YearGridPopup(2022, parent=dlg)

    popup._shift(_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "2029 – 2040"

    popup._shift(-_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "2017 – 2028"


def test_year_grid_popup_pick_emits_the_picked_year_and_closes():
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
    calls, cancelled = [], []
    monkeypatch.setattr(dlg._availability, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(dlg._availability, "cancel", lambda: cancelled.append(True))
    dlg._select_mode("archive")
    dlg.show()
    assert dlg._availability_timer.isActive()
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    generation = dlg._availability_generation
    QTest.qWait(350)
    assert calls == [(generation, False)]
    assert len(cancelled) >= 3
    dlg._select_mode("viewer")
    assert not dlg._availability_timer.isActive()


def test_late_result_cannot_overwrite_new_date_or_resume_closed_dialog():
    _, dlg = _dialog()
    dlg._select_mode("archive")
    old_generation = dlg._availability_generation
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    dlg._on_availability_updated(old_generation, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    assert "2025-05-01" in dlg._browse_coverage_lbl.text()
    dlg.show()
    generation = dlg._availability_generation
    dlg.close()
    dlg._on_availability_updated(generation, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    assert not dlg._availability_timer.isActive()


def test_known_dates_mark_both_calendars_year_grid_and_year_filter():
    _, dlg = _dialog()
    dates = {date(2024, 4, 27), date(2015, 5, 15)}
    _apply(dlg, _snapshot(dates))
    dlg._cal_btn.click()
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        assert calendar._known_dates == dates
    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    dlg._open_year_grid(dlg._calendar_popup, year_btn)
    assert dlg._year_grid_popup._known_years == {2015, 2024}
    index = dlg._browse_year_combo.findData(2024)
    assert dlg._browse_year_combo.itemData(index, Qt.ItemDataRole.ForegroundRole).name() == "#00cfff"


def test_campaign_year_and_platform_filters_update_without_requests():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27), date(2025, 5, 1), date(2015, 5, 15)]))
    assert dlg._browse_dates_list.item(0).text() == "2025-05-01"
    dlg._browse_campaign_combo.setCurrentIndex(dlg._browse_campaign_combo.findData("LIFT"))
    assert dlg._browse_dates_list.count() == 2
    dlg._browse_year_combo.setCurrentIndex(dlg._browse_year_combo.findData(2024))
    assert dlg._browse_dates_list.count() == 1
    # First sorted instrument has no dates in this fixture unless it is FOFS dltruck.
    other = next(i for i in range(1, dlg._browse_platform_combo.count())
                 if dlg._browse_platform_combo.itemData(i).platform_id != ALL_PLATFORMS[0].platform_id)
    dlg._browse_platform_combo.setCurrentIndex(other)
    assert dlg._browse_dates_list.count() == 0
    assert not dlg._archive_dt_edit.calendarWidget()._known_dates


def test_coverage_counts_instruments_and_keeps_errors_distinct_from_absence():
    _, dlg = _dialog()
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    _apply(dlg, _snapshot([date(2024, 4, 27)], failed=True))
    assert "1 instruments with listed data" in dlg._browse_coverage_lbl.text()
    assert "incomplete" in dlg._browse_coverage_lbl.text()
    assert "server timed out" in dlg._browse_coverage_lbl.toolTip()
    assert dlg._browse_sources_list.count() == 1
    _apply(dlg, _snapshot(failed=True))
    assert "incomplete" in dlg._browse_coverage_lbl.text()
    assert not dlg._browse_coverage_progress.isVisibleTo(dlg)
    _apply(dlg, _snapshot())
    assert "check complete" in dlg._browse_coverage_lbl.text()
    assert dlg._browse_coverage_lbl.styleSheet() == ""


def test_partial_snapshot_keeps_progress_and_positive_dates():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)], partial=True))
    assert "Indexing" in dlg._browse_coverage_lbl.text()
    assert dlg._browse_coverage_progress.isVisibleTo(dlg)
    assert dlg._browse_dates_list.count() == 1


def test_list_selection_preserves_utc_time_and_automatically_changes_query():
    _, dlg = _dialog()
    dlg._archive_dt_edit.setTime(QTime(9, 15, 45))
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    generation = dlg._availability_generation
    dlg._on_browse_date_chosen(dlg._browse_dates_list.item(0))
    assert dlg._archive_dt_edit.date() == QDate(2024, 4, 27)
    assert dlg._archive_dt_edit.time() == QTime(9, 15, 45)
    assert dlg._availability_generation > generation


def test_refresh_clears_old_markers_and_requests_fresh_metadata(monkeypatch):
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    calls = []
    monkeypatch.setattr(dlg._availability, "request", lambda *args: calls.append(args))
    dlg.show()
    dlg._availability_refresh_btn.click()
    assert dlg._availability_snapshot is None
    assert not dlg._archive_dt_edit.calendarWidget()._known_dates
    QTest.qWait(350)
    assert calls[-1] == (dlg._availability_generation, True)


def test_small_screen_scrolls_form_without_overlap_and_keeps_launch_visible(monkeypatch):
    from types import SimpleNamespace
    from PyQt6.QtCore import QRect
    _, dlg = _dialog()
    monkeypatch.setattr(dlg, "screen", lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 800, 600)))
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    dlg.show()
    dlg._toggle_browse_section()
    QTest.qWait(30)
    assert dlg.height() <= 540
    assert dlg._form_scroll.verticalScrollBar().maximum() > 0
    assert dlg._footer.geometry().bottom() < dlg.height()
    assert dlg._launch_btn.isVisibleTo(dlg)
    assert dlg._browse_results.geometry().top() > dlg._browse_status_lbl.geometry().bottom()
    # Every visible direct child fits its form section, including wrapped hints.
    from PyQt6.QtWidgets import QWidget
    for child in dlg._browse_section.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if child.isVisibleTo(dlg._browse_section):
            assert child.geometry().bottom() < dlg._browse_section.height()
    dlg._form_scroll.verticalScrollBar().setValue(dlg._form_scroll.verticalScrollBar().maximum())
    assert dlg._footer.geometry().bottom() < dlg.height()


def test_calendar_selection_updates_existing_date_list_highlight():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27), date(2024, 4, 28)]))
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 28))
    assert dlg._browse_dates_list.currentItem().text() == "2024-04-28"
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    assert dlg._browse_dates_list.currentItem().text() == "2024-04-27"


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


def test_calendar_shades_known_dates_and_clears_shading_when_filtered_out():
    _, dlg = _dialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    dlg._cal_btn.click()
    target = QDate(2024, 4, 27)
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        assert calendar.dateTextFormat(target).background().color().name() == '#123c50'
        assert calendar.dateTextFormat(target).foreground().color().name() == '#9be8ff'
        calendar.setSelectedDate(target)
        assert calendar.selectedDate() == target
    dlg._browse_year_combo.setCurrentIndex(dlg._browse_year_combo.findData(2023))
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        assert calendar.dateTextFormat(target).background().style() == Qt.BrushStyle.NoBrush
