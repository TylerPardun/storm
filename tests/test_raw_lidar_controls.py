from types import SimpleNamespace

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


def test_the_truck_and_the_trailers_are_named_plainly_and_kept_apart():
    controls, _, available = _controls({
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"],
        "CLAMPS1-FP": ["clampsdlfpC1.b1.20260517.000000.cdf"]})
    names = [controls._source_combo.itemText(i) for i in range(controls._source_combo.count())]
    assert names == ["Lidar truck (DLTRUCK1)", "CLAMPS1 trailer (CLAMPS1)"]    # CLAMPS2: no data, not listed
    assert "Lidar truck, CLAMPS1 trailer" in controls._status_label.text()
    assert available == [True]


def test_no_lidar_data_means_nothing_to_open():
    controls, _, available = _controls({})
    assert controls._source_combo.count() == 0
    assert "No lidar data" in controls._status_label.text()
    assert available == [False]
    assert not controls._btn_view.isEnabled() and not controls._btn_map.isEnabled()


def test_files_are_described_not_named_and_both_streams_of_the_truck_are_offered():
    controls, _, _ = _controls({
        "DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20220513.000000.cdf"],
        "DLTRUCK1-DL2-FP": ["dltruckdlfpDL2.b1.20220513.000000.cdf"],
        "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20220513.000000.cdf", "dltruckdlppiDL1.b1.20220514.000000.cdf"]})
    labels = [controls._asset_combo.itemText(i) for i in range(controls._asset_combo.count())]
    assert labels == ["Scans (PPI/VAD) · 05/13 · file 1/2", "Scans (PPI/VAD) · 05/14 · file 2/2",
                      "Fixed-point stares (DL1) · 05/13", "Fixed-point stares (DL2) · 05/13"]
    assert not any(".cdf" in label for label in labels)


def test_view_and_map_ask_for_the_selected_file_and_radar_shows_with_the_map():
    controls, built, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    asset = built["CLAMPS1-PPI"][0]
    viewed, mapped, radar = [], [], []
    controls.view_requested.connect(lambda pid, a: viewed.append((pid, a)))
    controls.map_overlay_requested.connect(lambda pid, a, on: mapped.append((pid, a, on)))
    controls.radar_visible_toggled.connect(radar.append)
    controls._btn_view.click()
    assert viewed == [("CLAMPS1-PPI", asset)]
    assert controls._btn_map.isEnabled()
    controls._btn_map.setChecked(True)
    assert not controls._chk_radar.isHidden() and controls._chk_radar.isChecked()   # radar on by default
    controls._chk_radar.setChecked(False)
    controls._btn_map.setChecked(False)
    assert mapped == [("CLAMPS1-PPI", asset, True), ("CLAMPS1-PPI", asset, False)]
    assert radar == [False, True] and controls._chk_radar.isHidden()                # radar back when the lidar goes


def test_a_loaded_file_says_what_it_holds_and_map_follows_it():
    from datetime import datetime, timezone
    from core.lidar_scans import LidarScan
    import numpy as np
    controls, built, _ = _controls({"DLTRUCK1-DL1-FP": ["dltruckdlfpDL1.b1.20260517.000000.cdf"]})
    asset = built["DLTRUCK1-DL1-FP"][0]
    t = datetime(2026, 5, 17, 18, 49, tzinfo=timezone.utc)
    stare = LidarScan("Stare", np.arange(3), t, t, 90.0, 300.0, (90.0, 90.0), 0.0)
    rays = SimpleNamespace(provenance={"url": asset.url}, fields={"velocity": {"units": "m/s"}})
    assert controls.set_loaded_fields(rays, [stare])
    assert controls._contents_label.text() == "File: 1 stare"
    assert not controls._btn_map.isEnabled() and "use VIEW" in controls._btn_map.toolTip()
    rays.provenance = {"url": "another file"}
    assert not controls.set_loaded_fields(rays, [stare])          # a stale load can't replace it


def test_switching_lidar_turns_the_map_off():
    controls, built, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"],
                                    "DLTRUCK1-DL1-PPI": ["dltruckdlppiDL1.b1.20260517.000000.cdf"]})
    controls._source_combo.setCurrentIndex(controls._source_combo.findData("CLAMPS1"))
    controls._btn_map.setChecked(True)
    events = []
    controls.map_overlay_requested.connect(lambda pid, a, on: events.append((pid, on)))
    controls._source_combo.setCurrentIndex(controls._source_combo.findData("DLTRUCK1"))
    assert not controls._btn_map.isChecked() and events == [("CLAMPS1-PPI", False)]


def test_where_the_lidar_was_is_part_of_the_status():
    controls, _, _ = _controls({"CLAMPS1-PPI": ["clampsdlppiC1.b1.20260517.000000.cdf"]})
    controls.set_site_text("CLAMPS1", "at NWC Vehicle Bay (34.982, -97.520), 690 km from KOAX")
    assert controls._status_label.text().startswith("CLAMPS1 trailer at NWC Vehicle Bay")
