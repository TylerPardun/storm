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


def _shown_in_window():
    from PyQt6.QtWidgets import QMainWindow
    app, controller, controls = _controls()
    win = QMainWindow()
    win.setCentralWidget(controls)
    win.show()
    app.processEvents()
    controls.set_available_scan_times([
        "2026-04-16T11:45:00Z", "2026-04-16T11:52:00Z", "2026-04-16T12:03:00Z",
    ])
    return app, controller, controls, win


def _press(app, win, key, modifier=None):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    QTest.keyClick(win, key, modifier or Qt.KeyboardModifier.NoModifier)
    app.processEvents()


def test_comma_period_and_angle_brackets_step_radar_frames():
    from PyQt6.QtCore import Qt
    app, controller, _, win = _shown_in_window()
    at = lambda h, m: datetime(2026, 4, 16, h, m, tzinfo=timezone.utc)

    _press(app, win, Qt.Key.Key_Comma)
    assert controller.current_time == at(11, 52)
    _press(app, win, Qt.Key.Key_Less, Qt.KeyboardModifier.ShiftModifier)
    assert controller.current_time == at(11, 45)
    _press(app, win, Qt.Key.Key_Period)
    assert controller.current_time == at(11, 52)
    _press(app, win, Qt.Key.Key_Greater, Qt.KeyboardModifier.ShiftModifier)
    assert controller.current_time == at(12, 3)
    _press(app, win, Qt.Key.Key_Less)       # "<" without Shift (other layouts)
    assert controller.current_time == at(11, 52)
    win.close()


def test_letter_step_keys_can_be_handed_to_another_tool():
    """Two shortcuts on one key fire neither, so while the TRACK editor
    owns D the step shortcut must be off, and D reaches the editor."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence, QShortcut
    app, controller, controls, win = _shown_in_window()
    deleted = []
    track_d = QShortcut(QKeySequence("D"), win)
    track_d.activated.connect(lambda: deleted.append(True))

    track_d.setEnabled(True)
    controls.set_letter_step_keys_enabled(False)
    _press(app, win, Qt.Key.Key_D)
    assert deleted == [True]
    assert controller.current_time == datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)

    track_d.setEnabled(False)
    controls.set_letter_step_keys_enabled(True)
    _press(app, win, Qt.Key.Key_D)
    assert deleted == [True]
    assert controller.current_time == datetime(2026, 4, 16, 12, 3, tzinfo=timezone.utc)
    win.close()
