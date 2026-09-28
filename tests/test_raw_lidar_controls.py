from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from ui.controls.raw_lidar_controls import RawLidarControls


def _dltruck1_dl1_sources():
    """The truck lidar's DL1-stream csm/ppi/fp/other variants -- one
    physical instrument, four scan-mode platform_ids."""
    return [s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id.startswith("DLTRUCK1-DL1-")]


def test_initial_status_is_a_real_placeholder_not_blank():
    controls = RawLidarControls()
    assert controls._status_label.text() != ""


def test_set_sources_groups_by_instrument_not_by_scan_mode():
    """One physical lidar registers 4 scan-mode platform_ids -- the top-level
    picker should show 1 instrument entry, not 4."""
    controls = RawLidarControls()
    controls.set_sources(_dltruck1_dl1_sources())
    assert controls._source_combo.count() == 1
    assert controls._source_combo.currentData() == "DLTRUCK1"


def test_scan_mode_combo_only_lists_products_that_have_data_and_shows_no_filenames():
    controls = RawLidarControls()
    sources = _dltruck1_dl1_sources()
    controls.set_sources(sources)
    ppi_source = next(s for s in sources if s.product == "ppi")
    asset = LidarAsset(ppi_source, "dlppiDL1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({ppi_source.platform_id: [asset]})

    # only the scan mode with data shows up, not all 4 registered variants.
    assert controls._asset_combo.count() == 1
    assert controls._asset_combo.itemText(0) == "PPI"
    assert ".cdf" not in controls._asset_combo.itemText(0)
    assert controls._btn_view.isEnabled() is True


def test_status_reports_instruments_not_a_raw_source_count():
    controls = RawLidarControls()
    sources = list(KNOWN_RAW_LIDAR_SOURCES)
    controls.set_sources(sources)
    ppi_source = next(s for s in sources if s.platform_id == "DLTRUCK1-DL1-PPI")
    asset = LidarAsset(ppi_source, "dlppiDL1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({ppi_source.platform_id: [asset]})

    status = controls._status_label.text()
    assert "DLTRUCK1" in status
    assert "of 16" not in status
    assert "source(s)" not in status


def test_status_when_nothing_has_data():
    controls = RawLidarControls()
    controls.set_sources(list(KNOWN_RAW_LIDAR_SOURCES))
    controls.set_assets_for_all_sources({})
    assert "No raw lidar data" in controls._status_label.text()
    assert controls._btn_view.isEnabled() is False


def test_discovery_selects_the_instrument_that_has_data():
    controls = RawLidarControls()
    controls.set_sources(list(KNOWN_RAW_LIDAR_SOURCES))
    clamps1_ppi = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1" and s.product == "ppi")
    asset = LidarAsset(clamps1_ppi, "clampsdlppiC1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({clamps1_ppi.platform_id: [asset]})

    assert controls._source_combo.count() == 1
    assert controls.current_instrument() == "CLAMPS1"
    assert controls.current_source() == clamps1_ppi
    assert controls._asset_combo.count() == 1
    assert controls._btn_view.isEnabled() is True


def test_view_quicklook_requested_carries_platform_id_and_asset():
    controls = RawLidarControls()
    sources = _dltruck1_dl1_sources()
    controls.set_sources(sources)
    fp_source = next(s for s in sources if s.product == "fp")
    asset = LidarAsset(fp_source, "dlfpDL1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({fp_source.platform_id: [asset]})

    requested = []
    controls.quicklook_requested.connect(lambda pid, a: requested.append((pid, a)))
    controls._btn_view.click()

    assert requested == [(fp_source.platform_id, asset)]


def test_map_button_visible_for_ppi_or_csm_including_mobile():
    controls = RawLidarControls()
    dltruck = _dltruck1_dl1_sources()
    clamps1 = [s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1"]
    controls.set_sources(dltruck + clamps1)

    assets = {s.platform_id: [LidarAsset(s, f"{s.platform_id}.cdf", "catalog")] for s in dltruck + clamps1}
    controls.set_assets_for_all_sources(assets)

    # Mobile PPI and CSM offer mapping; other products do not.
    for i in range(controls._asset_combo.count()):
        controls._asset_combo.setCurrentIndex(i)
        assert controls._btn_map.isEnabled() == (controls.current_source().product in ("ppi", "csm"))

    clamps1_index = controls._source_combo.findData("CLAMPS1")
    controls._source_combo.setCurrentIndex(clamps1_index)
    for i in range(controls._asset_combo.count()):
        controls._asset_combo.setCurrentIndex(i)
        product = controls._asset_combo.itemText(i).lower()
        assert controls._btn_map.isEnabled() == (product in ("ppi", "csm"))
        assert controls._btn_map.isHidden() == (product not in ("ppi", "csm"))


def test_map_toggled_emits_map_overlay_requested_with_enabled_flag():
    controls = RawLidarControls()
    clamps1_ppi = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1" and s.product == "ppi")
    controls.set_sources([clamps1_ppi])
    asset = LidarAsset(clamps1_ppi, "clampsdlppiC1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({clamps1_ppi.platform_id: [asset]})

    events = []
    controls.map_overlay_requested.connect(lambda pid, a, en: events.append((pid, a, en)))

    controls._btn_map.setChecked(True)
    controls._btn_map.setChecked(False)

    assert events == [
        (clamps1_ppi.platform_id, asset, True),
        (clamps1_ppi.platform_id, asset, False),
    ]


def test_switching_off_a_mappable_source_clears_the_map_toggle():
    """Leaving a checked CLAMPS PPI source for DLTRUCK1 must uncheck MAP
    (and thus emit enabled=False) rather than leaving a stale overlay on."""
    controls = RawLidarControls()
    dltruck = _dltruck1_dl1_sources()
    clamps1_ppi = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1" and s.product == "ppi")
    controls.set_sources(dltruck + [clamps1_ppi])
    asset = LidarAsset(clamps1_ppi, "clampsdlppiC1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({clamps1_ppi.platform_id: [asset]})

    clamps1_index = controls._source_combo.findData("CLAMPS1")
    controls._source_combo.setCurrentIndex(clamps1_index)
    controls._btn_map.setChecked(True)

    events = []
    controls.map_overlay_requested.connect(lambda pid, a, en: events.append((pid, a, en)))

    dltruck_index = controls._source_combo.findData("DLTRUCK1")
    controls._source_combo.setCurrentIndex(dltruck_index)

    assert controls._btn_map.isChecked() is False
    assert (clamps1_ppi.platform_id, asset, False) in events


def test_roster_hides_absent_instruments_but_preserves_unknown_sources():
    controls = RawLidarControls()
    controls.set_sources(KNOWN_RAW_LIDAR_SOURCES)
    mobile = KNOWN_RAW_LIDAR_SOURCES[0]
    unknown = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == 'CLAMPS1')
    asset = LidarAsset(mobile, 'a.cdf', 'catalog')
    controls.set_assets_for_all_sources({mobile.platform_id: [asset], unknown.platform_id: None})
    assert controls._source_combo.count() == 2
    assert controls._source_combo.findData('DLTRUCK1') == 0      # one entry for the truck lidar
    assert 'unavailable' in controls._source_combo.itemText(1)
    assert not controls._btn_map.isHidden()
    assert not hasattr(controls, '_map_reason')


def test_multiple_files_for_a_scan_mode_remain_selectable():
    controls = RawLidarControls()
    source = KNOWN_RAW_LIDAR_SOURCES[0]
    controls.set_sources([source])
    assets = [LidarAsset(source, name, 'catalog') for name in ('first.cdf', 'second.cdf')]
    controls.set_assets_for_all_sources({source.platform_id: assets})
    requested = []
    controls.quicklook_requested.connect(lambda pid, asset: requested.append(asset))
    controls._asset_combo.setCurrentIndex(1)
    controls._btn_view.click()
    assert requested == [assets[1]]


def test_fields_come_from_selected_file_and_stale_load_cannot_replace_them():
    from types import SimpleNamespace
    controls = RawLidarControls()
    source = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.product == 'csm')
    asset = LidarAsset(source, 'current.cdf', 'catalog')
    controls.set_sources([source])
    controls.set_assets_for_all_sources({source.platform_id: [asset]})
    rays = SimpleNamespace(provenance={'url': asset.url}, fields={
        'intensity': {'units': 'unitless'}, 'velocity': {'units': 'm/s'},
        'backscatter': {'units': 'km^-1 sr^-1'}})
    selected = []
    controls.field_selected.connect(selected.append)
    assert controls.set_loaded_fields(rays)
    assert selected == ['velocity']
    controls._field_combo.setCurrentIndex(0)
    assert selected[-1] == 'intensity'
    controls.set_map_scale({'field': 'intensity', 'vmin': 1, 'vmax': 2})
    assert controls._scale_label.text() == '1 … 2'
    rays.provenance = {'url': 'old-file'}
    assert not controls.set_loaded_fields(rays)
    assert controls._field_combo.currentData() == 'intensity'


def test_dl1_and_dl2_are_one_instrument_with_both_streams_offered():
    """DL1 and DL2 are the same lidar; on the 2022 days both streams have an
    fp file, and they differ, so both are offered, labeled by stream."""
    controls = RawLidarControls()
    controls.set_sources(KNOWN_RAW_LIDAR_SOURCES)
    fp = {s.stream: s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "DLTRUCK1" and s.product == "fp"}
    csm2 = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id == "DLTRUCK1-DL2-CSM")
    controls.set_assets_for_all_sources({
        fp["DL1"].platform_id: [LidarAsset(fp["DL1"], "dltruckdlfpDL1.b1.20220513.000000.cdf", "c")],
        fp["DL2"].platform_id: [LidarAsset(fp["DL2"], "dltruckdlfpDL2.b1.20220513.000000.cdf", "c")],
        csm2.platform_id: [LidarAsset(csm2, "dltruckdlcsmDL2.b1.20220513.000000.cdf", "c")],
    })
    assert [controls._source_combo.itemData(i) for i in range(controls._source_combo.count())] == ["DLTRUCK1"]
    labels = [controls._asset_combo.itemText(i) for i in range(controls._asset_combo.count())]
    assert labels == ["FP (DL1 stream)", "CSM", "FP (DL2 stream)"]
