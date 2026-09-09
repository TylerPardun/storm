from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from ui.controls.raw_lidar_controls import RawLidarControls


def _sources(n=2):
    return list(KNOWN_RAW_LIDAR_SOURCES[:n])


def test_initial_status_is_a_real_placeholder_not_blank():
    controls = RawLidarControls()
    assert controls._status_label.text() != ""


def test_set_assets_for_all_sources_populates_the_current_sources_files_only():
    controls = RawLidarControls()
    sources = _sources(2)
    controls.set_sources(sources)
    asset_a = LidarAsset(sources[0], 'a.20260517.000000.cdf', 'catalog')
    asset_b = LidarAsset(sources[1], 'b.20260517.000000.cdf', 'catalog')
    controls.set_assets_for_all_sources({sources[0].platform_id: [asset_a], sources[1].platform_id: [asset_b]})

    # combo starts on source 0 -- only that source's file should appear
    assert controls._asset_combo.count() == 1
    assert controls._asset_combo.itemData(0) is asset_a
    assert controls._btn_view.isEnabled() is True
    assert "2 of 2 source(s)" in controls._status_label.text()


def test_switching_source_refreshes_the_file_list_and_emits_source_selected():
    controls = RawLidarControls()
    sources = _sources(2)
    controls.set_sources(sources)
    asset_b = LidarAsset(sources[1], 'b.20260517.000000.cdf', 'catalog')
    controls.set_assets_for_all_sources({sources[1].platform_id: [asset_b]})
    assert controls._asset_combo.count() == 0  # source 0 has nothing

    selected = []
    controls.source_selected.connect(selected.append)
    controls._source_combo.setCurrentIndex(1)

    assert selected == [sources[1].platform_id]
    assert controls._asset_combo.count() == 1
    assert controls._asset_combo.itemData(0) is asset_b
    assert controls._btn_view.isEnabled() is True


def test_view_button_disabled_when_the_current_source_has_no_files():
    controls = RawLidarControls()
    sources = _sources(1)
    controls.set_sources(sources)
    controls.set_assets_for_all_sources({})
    assert controls._btn_view.isEnabled() is False


def test_view_quicklook_requested_carries_source_and_asset():
    controls = RawLidarControls()
    sources = _sources(1)
    controls.set_sources(sources)
    asset = LidarAsset(sources[0], 'a.20260517.000000.cdf', 'catalog')
    controls.set_assets_for_all_sources({sources[0].platform_id: [asset]})

    requested = []
    controls.quicklook_requested.connect(lambda pid, a: requested.append((pid, a)))
    controls._btn_view.click()

    assert requested == [(sources[0].platform_id, asset)]
