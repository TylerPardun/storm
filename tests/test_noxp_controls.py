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
    assert "1 volume" in controls._status_label.text()


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
    assert "not confirmed-PPI" in controls._status_label.text()
    assert "rhi_unverified" in controls._status_label.text()


def test_render_requested_carries_the_selected_sweep_and_field():
    controls = NoxpControls()
    controls.set_volume_summary(_volume(fields=("DBZ", "VEL"), n_sweeps=2))
    controls._sweep_combo.setCurrentIndex(1)
    controls._field_combo.setCurrentText("VEL")

    requested = []
    controls.render_requested.connect(lambda idx, field: requested.append((idx, field)))
    controls._btn_render.click()

    assert requested == [(1, "VEL")]
