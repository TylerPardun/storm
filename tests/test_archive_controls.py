"""Tests for archive playback control timing."""

from datetime import datetime, timezone

from PyQt6.QtWidgets import QApplication

from archive.time_controller import TimeController
from ui.controls.archive_controls import ArchiveControls


def _controls():
    app = QApplication.instance() or QApplication([])
    controller = TimeController(
        datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    )
    return app, controller, ArchiveControls(controller)


def test_normal_archive_controls_use_minute_steps():
    _, controller, controls = _controls()

    controls._btn_start.click()
    assert controller.current_time.hour == 11
    assert controller.current_time.minute == 59

    controls._btn_end.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc
    )


def test_precision_archive_controls_use_ten_second_steps():
    _, controller, controls = _controls()
    controls.set_precision_mode(True)

    controls._btn_start.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 11, 59, 50, tzinfo=timezone.utc
    )

    controls._btn_end.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc
    )


def test_scan_step_buttons_jump_to_nearest_available_scan():
    """_btn_back/_btn_fwd jump to the nearest indexed radar scan, not a
    fixed time offset -- and that's true whether or not precision mode is
    on, since scan availability has nothing to do with dense-obs playback."""
    _, controller, controls = _controls()
    controls.set_available_scan_times([
        "2026-04-16T11:45:00Z",
        "2026-04-16T11:52:00Z",
        "2026-04-16T12:03:00Z",
        "2026-04-16T12:10:00Z",
    ])

    controls._btn_back.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 11, 52, 0, tzinfo=timezone.utc
    )

    controls._btn_fwd.click()
    controls._btn_fwd.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 12, 10, 0, tzinfo=timezone.utc
    )

    # already at the last scan -- no scan after it, so no-op.
    controls._btn_fwd.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 12, 10, 0, tzinfo=timezone.utc
    )


def test_scan_step_buttons_noop_without_a_loaded_index():
    _, controller, controls = _controls()
    start = controller.current_time

    controls._btn_back.click()
    controls._btn_fwd.click()

    assert controller.current_time == start


def test_scan_step_buttons_unaffected_by_precision_mode():
    _, controller, controls = _controls()
    controls.set_available_scan_times(["2026-04-16T11:52:00Z"])
    controls.set_precision_mode(True)

    controls._btn_back.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 11, 52, 0, tzinfo=timezone.utc
    )


def test_rendered_radar_time_survives_loading_and_clock_changes():
    from types import SimpleNamespace
    _, controller, controls = _controls()
    scan = SimpleNamespace(site='KTWX', scan_time=datetime(2026, 6, 11, 0, 3, 42, tzinfo=timezone.utc))
    controls.set_rendered_radar(scan)
    original = controls._radar_time_label.text()
    assert '11 Jun 2026 00:03:42Z' in original
    controls.set_radar_status('Radar: loading…')
    controller.step(10)
    assert controls._radar_time_label.text() == original
    controls.set_rendered_radar(None)
    assert 'KTWX' not in controls._radar_time_label.text()
