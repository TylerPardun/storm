"""Tests for the archive date picker's calendar button/popup, and the
"browse available cases" panel (archive.catalog wiring)."""

from datetime import date, datetime, timezone

from PyQt6.QtCore import QDate, QDateTime, QTime, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QSpinBox, QToolButton

from ui.launch.dialog import LaunchDialog, _CatalogQueryWorker, _YearGridPopup


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


# ---------------------------------------------------------------------------
# "Browse available cases" panel (archive.catalog wiring)
# ---------------------------------------------------------------------------


def test_catalog_query_worker_emits_finished_with_the_function_result():
    results = []
    worker = _CatalogQueryWorker(lambda a, b: a + b, 2, 3)
    worker.finished.connect(results.append)
    worker.failed.connect(lambda msg: results.append(("failed", msg)))

    worker._run()  # call directly -- avoid real threading in a test

    assert results == [5]


def test_catalog_query_worker_emits_failed_on_exception():
    def boom():
        raise RuntimeError("network exploded")

    results = []
    worker = _CatalogQueryWorker(boom)
    worker.finished.connect(lambda r: results.append(("finished", r)))
    worker.failed.connect(results.append)

    worker._run()

    assert results == ["network exploded"]


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


def test_on_dates_found_populates_the_list_most_recent_first():
    _, dlg = _dialog()
    dlg._select_mode("archive")  # _archive_section and _browse_section must both
    dlg._toggle_browse_section()  # be visible for isVisibleTo checks below
    dlg._browse_find_btn.setEnabled(False)

    dlg._on_dates_found([date(2022, 5, 24), date(2022, 5, 25), date(2023, 3, 3)])

    assert dlg._browse_find_btn.isEnabled() is True
    assert dlg._browse_dates_list.isVisibleTo(dlg) is True
    assert dlg._browse_dates_list.count() == 3
    assert dlg._browse_dates_list.item(0).text() == "2023-03-03"
    assert dlg._browse_dates_list.item(0).data(Qt.ItemDataRole.UserRole) == date(2023, 3, 3)
    assert "3 date(s) found" in dlg._browse_status_lbl.text()


def test_on_dates_found_filters_by_the_selected_year():
    _, dlg = _dialog()
    year_index = dlg._browse_year_combo.findData(2022) if dlg._browse_year_combo.findData(2022) >= 0 else -1
    if year_index < 0:
        dlg._browse_year_combo.addItem("2022", 2022)
        year_index = dlg._browse_year_combo.findData(2022)
    dlg._browse_year_combo.setCurrentIndex(year_index)

    dlg._on_dates_found([date(2022, 5, 24), date(2023, 3, 3)])

    assert dlg._browse_dates_list.count() == 1
    assert dlg._browse_dates_list.item(0).text() == "2022-05-24"


def test_on_dates_found_with_no_matches_hides_the_list():
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()
    dlg._browse_dates_list.setVisible(True)
    assert dlg._browse_dates_list.isVisibleTo(dlg) is True  # sanity check before the real assertion

    dlg._on_dates_found([])

    assert dlg._browse_dates_list.isVisibleTo(dlg) is False
    assert "No dates found" in dlg._browse_status_lbl.text()


def test_on_browse_date_chosen_applies_date_and_preserves_time():
    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2020, 1, 1), QTime(9, 15, 0)))
    item = QListWidgetItem("2022-05-24")
    item.setData(Qt.ItemDataRole.UserRole, date(2022, 5, 24))

    dlg._on_browse_date_chosen(item)

    result = dlg._archive_dt_edit.dateTime()
    assert result.date() == QDate(2022, 5, 24)
    assert result.time() == QTime(9, 15, 0)
    assert "2022-05-24" in dlg._browse_status_lbl.text()


def test_on_coverage_result_announces_found_and_lists_stage_names_by_family():
    from archive.catalog import ALL_PLATFORMS

    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()  # _archive_section/_browse_section must be visible for isVisibleTo checks
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2022, 5, 25), QTime(0, 0, 0)))
    dlg._browse_coverage_btn.setEnabled(False)
    dlg._browse_coverage_progress.setVisible(True)
    assert dlg._browse_coverage_progress.isVisibleTo(dlg) is True  # sanity check before the real assertion
    present = ALL_PLATFORMS[:2]  # two platforms, possibly different families
    present_ids = {p.platform_id for p in present}
    result = {p.platform_id: (p.platform_id in present_ids) for p in ALL_PLATFORMS}

    dlg._on_coverage_result(result)

    assert dlg._browse_coverage_btn.isEnabled() is True
    assert dlg._browse_coverage_progress.isVisibleTo(dlg) is False  # spinner stops
    text = dlg._browse_coverage_lbl.text()
    assert f"Found! 2 of {len(ALL_PLATFORMS)} platforms have data for 2022-05-25" in text
    for p in present:
        assert p.display_name in text  # stage names, not just a per-family count
    assert dlg._browse_coverage_lbl.styleSheet() == "color: #4ADE80;"


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


def test_on_coverage_result_with_no_data_shows_a_plain_not_found_message():
    from archive.catalog import ALL_PLATFORMS

    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(1999, 1, 1), QTime(0, 0, 0)))
    dlg._browse_coverage_lbl.setStyleSheet("color: #4ADE80;")  # from a previous, successful check
    result = {p.platform_id: False for p in ALL_PLATFORMS}

    dlg._on_coverage_result(result)

    assert dlg._browse_coverage_lbl.text() == "No data found for 1999-01-01 on any known platform."
    assert dlg._browse_coverage_lbl.styleSheet() == ""  # green highlight cleared, not left over


def test_check_coverage_clicked_shows_the_progress_spinner(monkeypatch):
    # don't let the worker actually start a real network thread -- only
    # the synchronous UI-state-setting half of the click handler is
    # under test here, matching how _on_find_dates_clicked is treated
    # elsewhere in this file.
    monkeypatch.setattr(_CatalogQueryWorker, "start", lambda self: None)
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()

    dlg._on_check_coverage_clicked()

    assert dlg._browse_coverage_btn.isEnabled() is False
    assert dlg._browse_coverage_progress.isVisibleTo(dlg) is True


def test_on_coverage_failed_hides_the_progress_spinner():
    _, dlg = _dialog()
    dlg._select_mode("archive")
    dlg._toggle_browse_section()
    dlg._browse_coverage_progress.setVisible(True)
    assert dlg._browse_coverage_progress.isVisibleTo(dlg) is True  # sanity check before the real assertion

    dlg._on_coverage_failed("boom")

    assert dlg._browse_coverage_progress.isVisibleTo(dlg) is False


def test_on_find_dates_failed_reenables_button_and_reports_in_status_label():
    _, dlg = _dialog()
    dlg._browse_find_btn.setEnabled(False)

    dlg._on_find_dates_failed("boom")

    assert dlg._browse_find_btn.isEnabled() is True
    assert dlg._browse_status_lbl.text() == "Query failed: boom"


def test_on_coverage_failed_reenables_button_and_reports_in_coverage_label_not_status_label():
    _, dlg = _dialog()
    dlg._browse_coverage_btn.setEnabled(False)
    dlg._browse_status_lbl.setText("unrelated")

    dlg._on_coverage_failed("boom")

    assert dlg._browse_coverage_btn.isEnabled() is True
    assert dlg._browse_coverage_lbl.text() == "Query failed: boom"
    assert dlg._browse_status_lbl.text() == "unrelated"  # regression check: each worker's failure goes to its own label


# ---------------------------------------------------------------------------
# Year-grid popup (click the calendar's year field to jump to a year)
# ---------------------------------------------------------------------------


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
