from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from ui.controls.raw_lidar_controls import RawLidarControls


def _source(platform_id):
    return next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id == platform_id)


def _controls(assets):
    """assets: {platform_id: [filename, ...]}"""
    controls = RawLidarControls()
    controls.set_sources(KNOWN_RAW_LIDAR_SOURCES)
    available = []
    controls.availability_changed.connect(available.append)
    built = {pid: [LidarAsset(_source(pid), name, "catalog") for name in names] for pid, names in assets.items()}
    controls.set_assets_for_all_sources(built)
    return controls, built, available


def test_one_row_like_the_radar_and_nothing_to_pick_until_a_lidar_is_clicked():
    controls, _, available = _controls({"DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"]})
    assert available == [True]
    for gone in ("_btn_viewer", "_scan_row", "_chk_map", "_coverage_label", "_scale_label", "_status_label"):
        assert not hasattr(controls, gone)
    assert controls._location_button.text() == "Finding lidar scans…"
    controls.set_status("Pick a lidar on the map (5 locations)")
    assert controls._location_button.text() == "Pick a lidar on the map (5 locations)"
    assert not controls._field_combo.isEnabled()


def test_every_lidars_files_are_grouped_by_lidar_for_the_survey():
    controls, built, _ = _controls({
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"],
        "DLTRUCK1-DL1-CSM": ["dltruckdlcsmDL1.b1.20260517.000000.cdf"],
        "CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    grouped = controls.assets_by_instrument()
    assert sorted(grouped) == ["CLAMPS1", "DLTRUCK1"]
    assert len(grouped["DLTRUCK1"]) == 2 and len(grouped["CLAMPS1"]) == 1


def test_no_lidar_data():
    controls, _, available = _controls({})
    assert available == [False] and controls._location_button.text() == "No lidar data this date"
    assert controls.assets_by_instrument() == {}


def test_a_chosen_lidar_shows_its_name_with_details_on_hover():
    controls, _, _ = _controls({"DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"]})
    centered = []
    controls.location_requested.connect(lambda: centered.append(True))
    controls._location_button.click()
    assert centered == []                              # nothing chosen yet
    controls.set_location("Lidar truck · stop 2 of 4", "Lidar truck · stop 2 of 4 (41.438, -97.337)\nScanning …")
    controls.set_fields({"velocity": "m/s", "intensity": ""})
    assert controls._location_button.text() == "Lidar truck · stop 2 of 4"
    assert "(41.438, -97.337)" in controls._location_button.toolTip()
    assert controls.current_field() == "velocity" and controls._field_combo.isEnabled()
    controls._location_button.click()
    assert centered == [True]
    controls.set_status("Pick a lidar on the map")     # doesn't replace the chosen name
    assert controls._location_button.text() == "Lidar truck · stop 2 of 4"
    controls.set_location(None)
    assert controls._location_button.text() == "Pick a lidar on the map" and not controls._field_combo.isEnabled()


def test_heading_warning_is_a_small_symbol_with_the_explanation_on_hover():
    controls, _, _ = _controls({})
    assert controls._heading_warning.isHidden()
    controls.set_heading_notice({"heading_missing_in_file": True,
                                 "azimuth_reference": "Truck heading is missing from this lidar file; estimated…"})
    assert not controls._heading_warning.isHidden() and controls._heading_warning.text() == "⚠"
    assert "estimated" in controls._heading_warning.toolTip()
    controls.set_heading_notice({"north_referenced": True})
    assert controls._heading_warning.isHidden()


def test_radar_switch_under_the_lidar():
    controls, _, _ = _controls({})
    radar = []
    controls.radar_visible_toggled.connect(radar.append)
    assert controls.radar_is_on()
    controls._chk_radar.setChecked(False)
    assert radar == [False]
