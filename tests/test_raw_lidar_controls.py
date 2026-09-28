from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from ui.controls.raw_lidar_controls import RawLidarControls


def _source(platform_id):
    return next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id == platform_id)


def _controls(assets):
    """assets: {platform_id: [filename, ...]}"""
    controls = RawLidarControls()
    controls.set_sources(KNOWN_RAW_LIDAR_SOURCES)
    available, chosen = [], []
    controls.availability_changed.connect(available.append)
    controls.instrument_selected.connect(chosen.append)
    built = {pid: [LidarAsset(_source(pid), name, "catalog") for name in names] for pid, names in assets.items()}
    controls.set_assets_for_all_sources(built)
    return controls, built, available, chosen


def test_you_choose_a_lidar_not_a_file_and_nothing_loads_until_you_do():
    controls, _, available, chosen = _controls({
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"],
        "CLAMPS1-FP": ["clampsdlfpC1.b1.20260517.000000.cdf"]})
    names = [controls._source_combo.itemText(i) for i in range(controls._source_combo.count())]
    assert names == ["Choose a lidar…", "Lidar truck (DLTRUCK1)", "CLAMPS1 trailer (CLAMPS1)"]   # CLAMPS2: no data
    assert available == [True] and chosen == [] and controls.current_instrument() == ""
    assert "Lidar truck, CLAMPS1 trailer" in controls._status_label.text()
    controls.select_instrument("CLAMPS1")
    assert chosen == ["CLAMPS1"]


def test_all_of_a_lidars_files_go_together():
    controls, built, _, _ = _controls({
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf", "dltruckdlppiDL1.b1.20260518.000000.cdf"],
        "DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20260517.000000.cdf"],
        "CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    truck = controls.assets_for("DLTRUCK1")

    assert len(truck) == 3 and not any(a.source.instrument == "CLAMPS1" for a in truck)


def test_no_lidar_data_means_nothing_to_choose():
    controls, _, available, _ = _controls({})
    assert controls._source_combo.count() == 1
    assert "No lidar data" in controls._status_label.text() and available == [False]


def test_map_is_on_by_default_with_the_radar_under_it():
    controls, _, _, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    radar, mapped = [], []
    controls.radar_visible_toggled.connect(radar.append)
    controls.map_toggled.connect(mapped.append)
    assert controls.map_is_on() and controls.radar_is_on()
    controls._chk_radar.setChecked(False)
    controls._chk_map.setChecked(False)
    assert mapped == [False] and radar == [False, True] and controls._chk_radar.isHidden()   # radar back


def test_what_the_lidar_did_and_the_viewer_follow_its_scans():
    controls, _, _, _ = _controls({"DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20260517.000000.cdf"]})
    controls.select_instrument("DLTRUCK1")
    assert not controls._btn_viewer.isEnabled()
    controls.set_coverage("Scanning 18:49–19:23Z, 20:07–20:39Z  ·  2 stares", has_vertical=True)
    assert controls._btn_viewer.isEnabled() and not controls._coverage_label.isHidden()
    controls.set_coverage("Scanning 18:49–22:10Z  ·  55 VAD", has_vertical=False)
    assert not controls._btn_viewer.isEnabled()


def test_big_files_wait_for_a_click():
    controls, _, _, _ = _controls({"CLAMPS1-FP": ["clampsdlfpC1.b1.20260517.000000.cdf"]})
    controls.select_instrument("CLAMPS1")
    asked = []
    controls.load_large_requested.connect(lambda: asked.append(True))
    controls.set_large_files(355_425_788, "stares")
    assert controls._btn_large.text() == "LOAD STARES (355 MB)" and not controls._btn_large.isHidden()
    controls._btn_large.click()
    assert asked == [True] and not controls._btn_large.isEnabled()


def test_where_the_lidar_was_is_part_of_the_status():
    controls, _, _, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    controls.set_site_text("CLAMPS1", "at NWC Vehicle Bay (34.982, -97.520), 711 km from KOAX")
    controls.select_instrument("CLAMPS1")
    controls.set_progress("loading its scans (2 files)…")
    assert controls._status_label.text() == ("CLAMPS1 trailer at NWC Vehicle Bay (34.982, -97.520), 711 km from KOAX"
                                             "  ·  loading its scans (2 files)…")
