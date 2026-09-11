from datetime import datetime, timezone

import numpy as np

from archive.catalog import KnownPlatform
from archive.fetchers.noxp_archive_fetcher import RadarAsset, RadarVolume
from ui.controls.noxp_controls import NoxpControls


def _platform(label="2013"):
    return KnownPlatform(f"NOXP-{label}", label, "NOXP Radar", f"RRDD/NOXP/{label}")


def _asset(name="NOX130531214405.RAWETW4", fmt="sigmet", when=datetime(2013, 5, 31, 21, 44, 5, tzinfo=timezone.utc)):
    return RadarAsset(f"https://example.test/{name}", name, fmt, when)


def _volume(scan_type="ppi", fields=("DBZ", "VEL"), n_sweeps=2):
    return RadarVolume(
        time_epoch=np.zeros(1), range_m=np.zeros(1), azimuth_deg=np.zeros(1), elevation_deg=np.zeros(1),
        latitude=np.array([35.0]), longitude=np.array([-97.0]), altitude_m=np.array([400.0]),
        fields={f: {"data": None, "units": ""} for f in fields},
        sweep_start=np.arange(n_sweeps), sweep_end=np.arange(n_sweeps),
        scan_type=scan_type, provenance={},
    )


def test_initial_status_is_a_real_placeholder_not_blank():
    # A genuinely-empty word-wrapped QLabel's sizeHint is unstable during
    # the drawer's open/close animation -- always keep real text in it.
    controls = NoxpControls()
    assert controls._status_label.text() == "Select a campaign to search"


def test_selecting_a_platform_clears_stale_volumes_and_shows_searching_not_no_results():
    controls = NoxpControls()
    controls.set_platforms([_platform("2013"), _platform("2022")])
    controls.set_assets([_asset()])  # simulate a previous campaign's results

    selected = []
    controls.platform_selected.connect(selected.append)
    controls._platform_combo.setCurrentIndex(1)

    assert selected == ["NOXP-2022"]
    assert controls._asset_combo.count() == 0
    assert controls._btn_render.isEnabled() is False
    # The critical regression this guards: switching platforms must never
    # show "No volumes found" before a search has actually run -- that
    # reads as a completed, failed search rather than one in progress.
    assert "Searching" in controls._status_label.text()
    assert "No volumes found" not in controls._status_label.text()


def test_set_assets_with_real_results_populates_combo_and_status():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    asset = _asset()
    controls.set_assets([asset])
    assert controls._asset_combo.count() == 1
    assert controls._asset_combo.itemData(0) is asset
    assert "Loading" in controls._status_label.text()


def test_set_assets_with_no_results_says_so_only_after_a_real_search():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    controls.set_assets([])
    assert "No volumes found for this date" in controls._status_label.text()


def test_selecting_an_asset_shows_loading_before_the_volume_arrives():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    first = _asset("NOX130531214405.RAWETW4")
    second = _asset("NOX130531220112.RAWETW5")
    controls.set_assets([first, second])

    selected = []
    controls.asset_selected.connect(selected.append)
    controls._asset_combo.setCurrentIndex(1)  # switch away from the default first item

    assert selected == [second]
    assert "Loading" in controls._status_label.text()
    assert second.name in controls._status_label.text()


def test_ppi_volume_populates_sweeps_and_fields_and_enables_render():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(scan_type="ppi", fields=("DBZ", "VEL"), n_sweeps=3))
    assert controls._sweep_combo.count() == 3
    assert [controls._field_combo.itemText(i) for i in range(controls._field_combo.count())] == ["DBZ", "VEL"]
    assert controls._btn_render.isEnabled() is True
    assert "ready to render" in controls._status_label.text()


def test_non_ppi_volume_blocks_render_and_explains_why():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(scan_type="rhi_unverified"))
    assert controls._sweep_combo.count() == 0
    assert controls._btn_render.isEnabled() is False
    assert controls._btn_render.isHidden()
    assert "RHI_UNVERIFIED" in controls._status_label.text()


def test_render_requested_carries_the_selected_sweep_and_field():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=2))
    controls._sweep_combo.setCurrentIndex(1)
    controls._field_combo.setCurrentText("VEL")

    requested = []
    controls.render_requested.connect(lambda idx, field: requested.append((idx, field)))
    controls._btn_render.click()

    assert requested == [(1, "VEL")]


def test_map_toggle_updates_field_and_sweep_without_reclicking():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=('DBZ', 'VEL'), n_sweeps=2))
    renders, toggles = [], []
    controls.render_requested.connect(lambda sweep, field: renders.append((sweep, field)))
    controls.map_toggled.connect(toggles.append)
    controls._btn_render.click()
    controls._field_combo.setCurrentText('VEL')
    controls._sweep_combo.setCurrentIndex(1)
    controls._btn_render.click()
    assert renders == [(0, 'DBZ'), (0, 'VEL'), (1, 'VEL')]
    assert toggles == [True, False]


def test_asset_near_picks_the_nearest_before_and_falls_back_to_earliest():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    early = _asset("a", when=datetime(2013, 5, 31, 20, 0, 0, tzinfo=timezone.utc))
    mid = _asset("b", when=datetime(2013, 5, 31, 21, 0, 0, tzinfo=timezone.utc))
    late = _asset("c", when=datetime(2013, 5, 31, 22, 0, 0, tzinfo=timezone.utc))
    controls.set_assets([early, mid, late])

    assert controls.asset_near(datetime(2013, 5, 31, 21, 30, 0, tzinfo=timezone.utc)) is mid
    assert controls.asset_near(datetime(2013, 5, 31, 23, 0, 0, tzinfo=timezone.utc)) is late
    # nothing discovered yet at this time -- falls back to the earliest,
    # not None, so the clock-follow path in MainWindow has somewhere to start.
    assert controls.asset_near(datetime(2013, 5, 31, 19, 0, 0, tzinfo=timezone.utc)) is early


def test_asset_near_with_nothing_discovered_returns_none():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    assert controls.asset_near(datetime(2013, 5, 31, 21, 0, 0, tzinfo=timezone.utc)) is None


def test_set_current_asset_silently_does_not_emit_asset_selected():
    controls = NoxpControls()
    controls.set_platforms([_platform()])
    first = _asset("a", when=datetime(2013, 5, 31, 20, 0, 0, tzinfo=timezone.utc))
    second = _asset("b", when=datetime(2013, 5, 31, 21, 0, 0, tzinfo=timezone.utc))
    controls.set_assets([first, second])

    selected = []
    controls.asset_selected.connect(selected.append)
    controls.set_current_asset_silently(second)

    assert selected == []
    assert controls._asset_combo.currentData() is second


def test_keep_map_on_reloads_with_map_checked_and_preserves_field_and_sweep():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=3))
    controls._field_combo.setCurrentText("VEL")
    controls._sweep_combo.setCurrentIndex(2)
    controls._btn_render.setChecked(True)

    renders = []
    controls.render_requested.connect(lambda sweep, field: renders.append((sweep, field)))

    # a new volume arrives (clock-driven switch) with the same fields/sweep count
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=3), keep_map_on=True)

    assert controls._btn_render.isChecked() is True
    assert controls._field_combo.currentText() == "VEL"
    assert controls._sweep_combo.currentIndex() == 2
    assert renders == [(2, "VEL")]


def test_default_volume_switch_still_clears_map_despite_being_checked():
    """Without keep_map_on, a manual asset pick must still always drop MAP
    -- this is the pre-existing, deliberate default behavior."""
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=2))
    controls._btn_render.setChecked(True)

    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=2))

    assert controls._btn_render.isChecked() is False


def test_keep_map_on_with_a_field_that_no_longer_exists_falls_back_gracefully():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=2))
    controls._field_combo.setCurrentText("VEL")
    controls._btn_render.setChecked(True)

    renders = []
    controls.render_requested.connect(lambda sweep, field: renders.append((sweep, field)))
    # the new volume only has DBZ -- VEL is gone.
    controls.set_volume_summary(_volume(fields=("DBZ",), n_sweeps=2), keep_map_on=True)

    assert controls._btn_render.isChecked() is True
    assert controls._field_combo.currentText() == "DBZ"
    assert renders == [(0, "DBZ")]


def test_select_campaign_for_year_matches_a_label_containing_that_year():
    controls = NoxpControls()
    controls.set_platforms([_platform("2010"), _platform("2013"), _platform("2022")])
    assert controls.select_campaign_for_year(2013) is True
    assert controls.current_platform().display_name == "2013"


def test_select_campaign_for_year_matches_a_compound_label():
    controls = NoxpControls()
    vortex = KnownPlatform("NOXP-VORTEX2_2009", "VORTEX2 2009", "NOXP Radar", "RRDD/NOXP/Vortex/2009")
    controls.set_platforms([_platform("2010"), vortex])
    assert controls.select_campaign_for_year(2009) is True
    assert controls.current_platform() is vortex


def test_select_campaign_for_year_with_no_match_leaves_selection_unchanged():
    controls = NoxpControls()
    controls.set_platforms([_platform("2010"), _platform("2022")])
    assert controls.select_campaign_for_year(1999) is False
    assert controls.current_platform().display_name == "2010"


def test_select_campaign_for_year_does_not_trigger_a_search():
    """Pre-selecting the matching campaign is silent -- the existing
    drawer-open flow is what actually kicks off discovery."""
    controls = NoxpControls()
    controls.set_platforms([_platform("2010"), _platform("2013")])
    selected = []
    controls.platform_selected.connect(selected.append)
    controls.select_campaign_for_year(2013)
    assert selected == []
