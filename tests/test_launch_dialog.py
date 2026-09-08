"""Tests for the archive date picker's calendar button/popup, and the
"browse available cases" panel (archive.catalog wiring)."""

from datetime import date, datetime, timezone

from PyQt6.QtCore import QDate, QDateTime, QTime, Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from ui.launch.dialog import LaunchDialog, _CatalogQueryWorker


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


def test_populate_browse_year_combo_with_no_campaign_shows_union_of_all_years():
    _, dlg = _dialog()

    dlg._populate_browse_year_combo(None)

    years = [dlg._browse_year_combo.itemData(i) for i in range(dlg._browse_year_combo.count())]
    assert years[0] is None
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


def test_on_coverage_result_summarizes_present_platforms_by_family():
    from archive.catalog import ALL_PLATFORMS

    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2022, 5, 25), QTime(0, 0, 0)))
    dlg._browse_coverage_btn.setEnabled(False)
    present_ids = {p.platform_id for p in ALL_PLATFORMS[:2]}
    result = {p.platform_id: (p.platform_id in present_ids) for p in ALL_PLATFORMS}

    dlg._on_coverage_result(result)

    assert dlg._browse_coverage_btn.isEnabled() is True
    text = dlg._browse_coverage_lbl.text()
    assert f"2 of {len(ALL_PLATFORMS)} platforms have data for 2022-05-25" in text


def test_on_coverage_result_with_no_data_omits_the_breakdown_line():
    from archive.catalog import ALL_PLATFORMS

    _, dlg = _dialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(1999, 1, 1), QTime(0, 0, 0)))
    result = {p.platform_id: False for p in ALL_PLATFORMS}

    dlg._on_coverage_result(result)

    assert dlg._browse_coverage_lbl.text() == f"0 of {len(ALL_PLATFORMS)} platforms have data for 1999-01-01"


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
