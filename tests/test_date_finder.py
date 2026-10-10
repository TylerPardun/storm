"""Find dates window: matching rules and the round trip to the launch panel."""
from datetime import date
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from archive.catalog import ALL_PLATFORMS, AvailabilitySnapshot, PlatformAvailability
from ui.launch.availability import AvailabilityWorker
from ui.launch.date_finder import FindDatesDialog, dates_by_site, match_dates, sites_on
from ui.launch.dialog import LaunchDialog

A, B, C = date(2024, 5, 23), date(2024, 6, 2), date(2019, 5, 17)
BY_SITE = {"Truck": frozenset({A, B}), "CLAMPS 1": frozenset({A, C}), "Probe 1": frozenset({B})}


def test_no_instruments_chosen_matches_any_site():
    assert match_dates(BY_SITE) == {A, B, C}


def test_all_of_requires_every_chosen_site():
    assert match_dates(BY_SITE, chosen=("Truck", "CLAMPS 1")) == {A}
    assert match_dates(BY_SITE, chosen=("CLAMPS 1", "Probe 1")) == set()


def test_any_of_takes_the_union():
    assert match_dates(BY_SITE, chosen=("CLAMPS 1", "Probe 1"), require_all=False) == {A, B, C}


def test_year_month_and_campaign_filters_combine():
    assert match_dates(BY_SITE, years=(2020, 2026)) == {A, B}
    assert match_dates(BY_SITE, months={5}) == {A, C}
    assert match_dates(BY_SITE, months={5}, campaign_years=(2019,)) == {C}


def test_empty_index_matches_nothing():
    assert match_dates({}) == set()
    assert match_dates({}, chosen=("Truck",)) == set()


def test_sites_on_lists_every_site_with_data_that_day():
    assert sites_on(A, BY_SITE) == ["CLAMPS 1", "Truck"]


def test_dates_by_site_unions_a_sites_products():
    plats = [SimpleNamespace(platform_id="x"), SimpleNamespace(platform_id="y")]
    snap = SimpleNamespace(platforms={"x": SimpleNamespace(dates={A}), "y": SimpleNamespace(dates={B})})
    assert dates_by_site(snap, {"Truck": plats, "Gone": [SimpleNamespace(platform_id="z")]}) == {
        "Truck": {A, B}, "Gone": set()}
    assert dates_by_site(None, {"Truck": plats}) == {}


def _snapshot(per_platform):
    results = {p.platform_id: PlatformAvailability(frozenset(per_platform.get(p.platform_id, ())), 1, 1, ())
               for p in ALL_PLATFORMS}
    return AvailabilitySnapshot(results, len(results), len(results))


def _check(dialog, site):
    for i in range(dialog._instruments.count()):
        item = dialog._instruments.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == site:
            item.setCheckState(Qt.CheckState.Checked)
            return
    raise AssertionError(site)


def test_dialog_filters_and_lists_matching_dates():
    QApplication.instance()
    snap = _snapshot({"FOFS-dltruck": {A, B}, "FOFS-probe1": {B, C}})
    dlg = FindDatesDialog()
    dlg.set_snapshot(snap)
    assert dlg.matching_dates() == {A, B, C}
    _check(dlg, "LiDAR Truck")
    _check(dlg, "Probe 1")
    assert dlg.matching_dates() == {B}
    assert dlg._days.count() == 1 and "LiDAR Truck" in dlg._days.item(0).text()
    dlg._mode_any.setChecked(True)
    assert dlg.matching_dates() == {A, B, C}
    dlg._month_btns[4].setChecked(True)                       # May
    assert dlg.matching_dates() == {A, C}
    dlg._on_month_clicked((2024, 5))
    assert [dlg._days.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg._days.count())] == [A]


def test_campaign_sets_the_year_range():
    QApplication.instance()
    dlg = FindDatesDialog()
    dlg._campaign.setCurrentIndex(dlg._campaign.findData("TORUS"))
    assert dlg._year_range() == (2019, 2023)


def test_choosing_a_date_sets_the_launch_date_and_keeps_the_time(monkeypatch):
    monkeypatch.setattr(LaunchDialog, "_start_update_check", lambda self: None)
    monkeypatch.setattr(AvailabilityWorker, "request", lambda *args: None)
    QApplication.instance()
    launch = LaunchDialog()
    launch._select_mode("archive")
    launch._on_availability_updated(launch._availability_generation, _snapshot({"FOFS-dltruck": {A}}))
    time_before = launch._archive_dt_edit.time()
    launch._open_date_finder()
    finder = launch._date_finder
    assert finder.matching_dates() == {A}
    finder._days.setCurrentRow(0)
    finder._use_btn.click()
    assert launch._archive_dt_edit.date().toPyDate() == A
    assert launch._archive_dt_edit.time() == time_before
    assert not finder.isVisible()


def test_an_instrument_covers_every_product_it_carries():
    """LiDAR Truck: its mesonet and its lidar days both count."""
    QApplication.instance()
    mesonet_day, lidar_day = date(2024, 4, 27), date(2024, 4, 28)
    dlg = FindDatesDialog()
    dlg.set_snapshot(_snapshot({"FOFS-dltruck": {mesonet_day}, "RAW-LIDAR-DLTRUCK1-DL1-CSM": {lidar_day}}))
    _check(dlg, "LiDAR Truck")
    assert dlg.matching_dates() == {mesonet_day, lidar_day}
