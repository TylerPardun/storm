from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from ui.controls.raw_lidar_controls import RawLidarControls


def _dltruck1_dl1_sources():
    """The first 4 entries are DLTRUCK1-DL1's csm/ppi/fp/other variants --
    one physical instrument, four scan-mode platform_ids."""
    return [s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "DLTRUCK1-DL1"]


def test_initial_status_is_a_real_placeholder_not_blank():
    controls = RawLidarControls()
    assert controls._status_label.text() != ""


def test_set_sources_groups_by_instrument_not_by_scan_mode():
    """One physical lidar registers 4 scan-mode platform_ids -- the top-level
    picker should show 1 instrument entry, not 4."""
    controls = RawLidarControls()
    controls.set_sources(_dltruck1_dl1_sources())
    assert controls._source_combo.count() == 1
    assert controls._source_combo.currentData() == "DLTRUCK1-DL1"


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
    ppi_source = next(s for s in sources if s.instrument == "DLTRUCK1-DL1" and s.product == "ppi")
    asset = LidarAsset(ppi_source, "dlppiDL1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({ppi_source.platform_id: [asset]})

    status = controls._status_label.text()
    assert "DLTRUCK1-DL1" in status
    assert "of 16" not in status
    assert "source(s)" not in status


def test_status_when_nothing_has_data():
    controls = RawLidarControls()
    controls.set_sources(list(KNOWN_RAW_LIDAR_SOURCES))
    controls.set_assets_for_all_sources({})
    assert "No raw lidar data" in controls._status_label.text()
    assert controls._btn_view.isEnabled() is False


def test_switching_instrument_refreshes_scan_modes_and_emits_source_selected():
    controls = RawLidarControls()
    controls.set_sources(list(KNOWN_RAW_LIDAR_SOURCES))
    clamps1_ppi = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1" and s.product == "ppi")
    asset = LidarAsset(clamps1_ppi, "clampsdlppiC1.b1.20260608.000000.cdf", "catalog")
    controls.set_assets_for_all_sources({clamps1_ppi.platform_id: [asset]})

    selected = []
    controls.source_selected.connect(selected.append)

    clamps1_index = controls._source_combo.findData("CLAMPS1")
    controls._source_combo.setCurrentIndex(clamps1_index)

    assert selected == [clamps1_ppi.platform_id]
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


def test_map_button_enabled_only_for_stationary_ppi_or_csm():
    """MAP should only ever be usable for a source raw_lidar_scan_to_map_scan
    can actually georeference: not mobile, and a real scanning product."""
    controls = RawLidarControls()
    dltruck = _dltruck1_dl1_sources()  # all mobile -- MAP should stay off
    clamps1 = [s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "CLAMPS1"]
    controls.set_sources(dltruck + clamps1)

    assets = {s.platform_id: [LidarAsset(s, f"{s.platform_id}.cdf", "catalog")] for s in dltruck + clamps1}
    controls.set_assets_for_all_sources(assets)

    # still on DLTRUCK1-DL1 (mobile) -- MAP must stay disabled regardless of product.
    for i in range(controls._asset_combo.count()):
        controls._asset_combo.setCurrentIndex(i)
        assert controls._btn_map.isEnabled() is False

    clamps1_index = controls._source_combo.findData("CLAMPS1")
    controls._source_combo.setCurrentIndex(clamps1_index)
    for i in range(controls._asset_combo.count()):
        controls._asset_combo.setCurrentIndex(i)
        product = controls._asset_combo.itemText(i).lower()
        assert controls._btn_map.isEnabled() == (product in ("ppi", "csm"))


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

    dltruck_index = controls._source_combo.findData("DLTRUCK1-DL1")
    controls._source_combo.setCurrentIndex(dltruck_index)

    assert controls._btn_map.isChecked() is False
    assert (clamps1_ppi.platform_id, asset, False) in events
