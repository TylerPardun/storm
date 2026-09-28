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


def test_there_is_nothing_to_pick_in_the_panel_a_location_is_clicked_on_the_map():
    controls, _, available = _controls({"DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"]})
    assert available == [True]
    assert not hasattr(controls, "_source_combo") and not hasattr(controls, "_btn_locate")
    assert controls._location_label.text() == "Click a lidar location on the map"
    assert not controls._field_combo.isEnabled() and not controls._btn_viewer.isEnabled()


def test_every_lidars_files_are_grouped_by_lidar_for_the_survey():
    controls, built, _ = _controls({
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"],
        "DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20260517.000000.cdf"],
        "CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    grouped = controls.assets_by_instrument()
    assert sorted(grouped) == ["CLAMPS1", "DLTRUCK1"]
    assert len(grouped["DLTRUCK1"]) == 2 and len(grouped["CLAMPS1"]) == 1
    assert "Lidar truck, CLAMPS1 trailer" in controls._status_label.text()


def test_no_lidar_data():
    controls, _, available = _controls({})
    assert available == [False] and "No lidar data" in controls._status_label.text()
    assert controls.assets_by_instrument() == {}


def test_a_chosen_location_says_what_it_holds():
    controls, _, _ = _controls({"DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20260517.000000.cdf"]})
    controls.set_location("Lidar truck · stop 2 of 4 (41.438, -97.337), 82 km from KOAX")
    controls.set_fields({"velocity": "m/s", "intensity": ""})
    controls.set_coverage("Scanning 18:49–19:23Z  ·  12 VAD · 1 vertical stare", has_vertical=True)
    assert controls._location_label.text().startswith("Lidar truck · stop 2 of 4")
    assert controls.current_field() == "velocity" and controls._btn_viewer.isEnabled()
    assert not controls._scan_row.isHidden()
    controls.set_location(None)                       # nothing chosen again
    assert controls._location_label.text() == "Click a lidar location on the map"
    assert not controls._btn_viewer.isEnabled() and controls._scan_row.isHidden()


def test_map_is_on_by_default_with_the_radar_under_it():
    controls, _, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    radar, mapped = [], []
    controls.radar_visible_toggled.connect(radar.append)
    controls.map_toggled.connect(mapped.append)
    assert controls.map_is_on() and controls.radar_is_on()
    controls._chk_radar.setChecked(False)
    controls._chk_map.setChecked(False)
    assert mapped == [False] and radar == [False, True] and controls._chk_radar.isHidden()
