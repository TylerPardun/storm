
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QToolButton, QComboBox, QFrame, QSlider, QMessageBox,
    QDialog, QPushButton, QTimeEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTime
from PyQt6.QtGui import QKeySequence, QShortcut

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

from archive.time_controller import TimeController, SPEED_OPTIONS

log = logging.getLogger(__name__)

_CT_TZ = ZoneInfo("America/Chicago") if _HAS_ZONEINFO else None
_MT_TZ = ZoneInfo("America/Denver")  if _HAS_ZONEINFO else None


class ArchiveControls(QWidget):
    """
    Floating bottom bar providing time navigation controls for archive mode.

    Connects to a TimeController and exposes:
      • Play / pause
      • Normal mode: ±10-second and ±1-minute steps
      • Precision mode: ±1-second and ±10-second steps
      • Scrubber slider (seconds since midnight UTC of the session date)
      • Speed selector
      • Jump-to-time dialog

    Signals
    -------
    tilt_changed(int)   — user changed tilt index
    product_changed(str) — user changed Level-2 product
    """

    tilt_changed         = pyqtSignal(int)
    product_changed      = pyqtSignal(str)
    change_day_requested = pyqtSignal()   # user confirmed exiting this session to pick a new day
    export_case_requested = pyqtSignal()  # CASE > Export case package…
    open_case_requested = pyqtSignal()    # CASE > Open case package…

    def __init__(self, time_controller: TimeController, parent=None):
        super().__init__(parent)
        self._tc = time_controller
        self._session_date: Optional[datetime] = None
        self._scan_times: list[datetime] = []

        self.setObjectName("archiveControls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build_ui()
        self._connect_controller()


    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 5, 10, 5)
        root.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.setContentsMargins(0, 0, 0, 0)

        archive_badge = QLabel("ARCHIVE")
        archive_badge.setStyleSheet(
            "color: #FF9F1C; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        row1.addWidget(archive_badge)

        row1.addWidget(self._vdiv())

        self._date_label = QLabel("---- -- --")
        self._date_label.setStyleSheet(
            "color: #C8D0DE; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._date_label)

        self._utc_label = QLabel("--:--:-- UTC")
        self._utc_label.setStyleSheet(
            "color: #E8EAF0; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._utc_label)

        row1.addWidget(self._vdiv())

        self._local_label = QLabel("--:-- CT / --:-- MT")
        self._local_label.setStyleSheet(
            "color: #8E97AB; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._local_label)

        row1.addStretch()

        self._radar_status = QLabel("Radar: --")
        self._radar_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._radar_status)

        row1.addWidget(self._vdiv())

        self._sat_status = QLabel("Sat: --")
        self._sat_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._sat_status)

        row1.addWidget(self._vdiv())

        self._obs_status = QLabel("OBS: MQTT")
        self._obs_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._obs_status)

        root.addLayout(row1)

        self._radar_time_label = QLabel("Radar image: —")
        self._radar_time_label.setStyleSheet("color: #E8EAF0; font-size: 11px; font-weight: 600;")
        self._radar_time_label.setToolTip("Volume time of the displayed radar image")
        root.addWidget(self._radar_time_label)

        # scrubber — seconds since 00:00 UTC of the session date, running to
        # the session's end (which can be into the next UTC day). Dragging
        # only updates the time label (cheap); the actual seek (which fans
        # out to every fetcher's on_time_changed) only fires on release, so
        # scrubbing never floods the archive fetchers with intermediate seeks.
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setTickInterval(3600)   # one tick per hour
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTracking(False)
        self._slider.setFixedHeight(18)
        self._slider.sliderMoved.connect(self._on_slider_preview)
        self._slider.valueChanged.connect(self._on_slider_committed)
        root.addWidget(self._slider)

        row2 = QHBoxLayout()
        row2.setSpacing(5)
        row2.setContentsMargins(0, 0, 0, 0)

        self._btn_start = self._ctrl_btn("-1m", "Step back 1 minute")
        self._btn_back  = self._ctrl_btn("⏮", "Previous radar scan (Left / A)")
        self._btn_play  = self._ctrl_btn("▶",  "Play / pause (Space)")
        self._btn_play.setCheckable(True)
        self._btn_fwd   = self._ctrl_btn("⏭", "Next radar scan (Right / D)")
        self._btn_end   = self._ctrl_btn("+1m", "Step forward 1 minute")

        for btn in (self._btn_start, self._btn_back, self._btn_play,
                    self._btn_fwd, self._btn_end):
            row2.addWidget(btn)

        row2.addWidget(self._vdiv())

        self._speed_label = QLabel("SPEED")
        self._speed_label.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.addWidget(self._speed_label)

        self._speed_combo = QComboBox()
        self._speed_combo.setObjectName("archiveSpeedCombo")
        for s in SPEED_OPTIONS:
            self._speed_combo.addItem(f"{s}×")
        self._speed_combo.setCurrentIndex(SPEED_OPTIONS.index(self._tc.speed))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setFixedWidth(54)
        row2.addWidget(self._speed_combo)

        row2.addWidget(self._vdiv())

        jump_btn = self._ctrl_btn("JUMP", "Jump to a specific time (this day only)")
        jump_btn.setFixedWidth(44)
        jump_btn.setStyleSheet(jump_btn.styleSheet() or "")
        jump_btn.clicked.connect(self._show_jump_dialog)
        row2.addWidget(jump_btn)

        row2.addWidget(self._vdiv())

        from PyQt6.QtWidgets import QMenu
        case_btn = self._ctrl_btn("CASE", "Export this case's saved work and provenance, or open a case package")
        case_btn.setFixedWidth(44)
        case_menu = QMenu(case_btn)
        case_menu.addAction("Export case package…", self.export_case_requested.emit)
        case_menu.addAction("Open case package…", self.open_case_requested.emit)
        case_btn.setMenu(case_menu)
        case_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row2.addWidget(case_btn)

        change_day_btn = self._ctrl_btn("EXIT", "Exit this session and pick a different day")
        change_day_btn.setFixedWidth(40)
        change_day_btn.setStyleSheet(
            "QToolButton { color: #F87171; border: 1px solid rgba(248, 113, 113, 0.4); "
            "border-radius: 4px; } "
            "QToolButton:hover { background-color: rgba(248, 113, 113, 0.12); }"
        )
        change_day_btn.clicked.connect(self._on_change_day_clicked)
        row2.addWidget(change_day_btn)

        root.addLayout(row2)
        self._precision_mode = False

        # populate the slider/labels from wherever the clock actually starts,
        # rather than leaving them at placeholder text until the first step.
        self._on_time_changed(self._tc.current_time)

    def _ctrl_btn(self, text: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 22)
        return btn

    def _vdiv(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.VLine)
        d.setStyleSheet("color: #394056; margin: 3px 0;")
        return d

    def set_radar_status(self, text: str, error: bool = False) -> None:
        self._radar_status.setText(text)
        color = "#FF8F8F" if error else "#8E97AB"
        self._radar_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_rendered_radar(self, scan) -> None:
        if scan is None:
            self._radar_time_label.setText("Radar image: —")
            return
        timestamp = scan.scan_time.astimezone(timezone.utc)
        self._radar_time_label.setText(
            f"Radar image: {scan.site} · {timestamp:%d %b %Y %H:%M:%SZ}"
        )

    def set_satellite_status(self, text: str, error: bool = False) -> None:
        self._sat_status.setText(text)
        color = "#FF8F8F" if error else "#8E97AB"
        self._sat_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_available_scan_times(self, iso_times: list[str]) -> None:
        """Record the current radar station's scan index (ISO UTC strings,
        already chronological -- see ArchiveRadarFetcher.index_loaded) so
        the scan-step buttons and Jump dialog can target real scan times
        instead of arbitrary seconds. Re-fires whenever the station or day
        changes, replacing the previous list."""
        self._scan_times = [
            datetime.fromisoformat(t.replace("Z", "+00:00")) for t in iso_times
        ]

    def set_obs_status(self, text: str, active: bool = False) -> None:
        self._obs_status.setText(text)
        color = "#39D98A" if active else "#8E97AB"
        self._obs_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_precision_mode(self, enabled: bool) -> None:
        """Use one-second playback navigation when dense observations are
        available. Only the +/-1m fine-tune buttons change meaning here --
        the scan-step buttons (_btn_back/_btn_fwd) always mean "nearest
        radar scan," precision mode or not."""
        self._precision_mode = enabled
        self._btn_start.setText("-10")
        self._btn_end.setText("+10")
        self._btn_start.setToolTip("Step back 10 seconds")
        self._btn_end.setToolTip("Step forward 10 seconds")
        self._speed_label.setVisible(not enabled)
        self._speed_combo.setVisible(not enabled)
        self._tc.set_precision_playback(enabled)

    def _connect_controller(self):
        self._tc.time_changed.connect(self._on_time_changed)
        self._tc.window_changed.connect(self._on_window_changed)
        self._tc.playing_changed.connect(self._on_playing_changed)

        self._btn_start.clicked.connect(self._on_skip_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.clicked.connect(self._tc.toggle_play)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_end.clicked.connect(self._on_skip_end)

        # keyboard shortcuts — these require a parent window to be set.
        self._shortcuts_installed = False
        self._letter_step_keys_enabled = True

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shortcuts_installed:
            self._install_shortcuts()

    def _install_shortcuts(self):
        win = self.window()
        if win is None:
            return
        self._shortcuts_installed = True
        QShortcut(QKeySequence(Qt.Key.Key_Space),  win).activated.connect(self._tc.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left),   win).activated.connect(self._on_step_back)
        QShortcut(QKeySequence(Qt.Key.Key_Right),  win).activated.connect(self._on_step_forward)
        self._letter_step_shortcuts = [
            QShortcut(QKeySequence(Qt.Key.Key_A), win),
            QShortcut(QKeySequence(Qt.Key.Key_D), win),
        ]
        self._letter_step_shortcuts[0].activated.connect(self._on_step_back)
        self._letter_step_shortcuts[1].activated.connect(self._on_step_forward)
        self.set_letter_step_keys_enabled(self._letter_step_keys_enabled)
        # , and < step back a radar frame, . and > forward (as in MESO-VIEW).
        # "<" arrives as "<" on some keyboards and Shift+"," on others.
        for seq in (",", "<", "Shift+<", "Shift+,"):
            QShortcut(QKeySequence(seq), win).activated.connect(self._on_step_back)
        for seq in (".", ">", "Shift+>", "Shift+."):
            QShortcut(QKeySequence(seq), win).activated.connect(self._on_step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Home),   win).activated.connect(self._on_skip_start)
        QShortcut(QKeySequence(Qt.Key.Key_End),    win).activated.connect(self._on_skip_end)


    def _on_time_changed(self, t: datetime) -> None:
        self._update_time_display(t)
        # reflect the new position on the slider without re-triggering a seek
        self._slider.blockSignals(True)
        self._slider.setValue(self._tc.seconds_since_start())
        self._slider.blockSignals(False)

    def _on_window_changed(self, start: datetime, end: datetime) -> None:
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setValue(self._tc.seconds_since_start())
        self._slider.blockSignals(False)
        self._slider.setToolTip(f"Session ends {end:%Y-%m-%d %H:%M} UTC")

    def _on_slider_preview(self, value: int) -> None:
        """Live label update while dragging — no fetcher calls until release."""
        start, _ = self._tc.window
        self._update_time_display(start + timedelta(seconds=value))

    def _on_slider_committed(self, value: int) -> None:
        self._tc.pause()
        self._tc.set_seconds_since_start(value)

    def _on_change_day_clicked(self) -> None:
        reply = QMessageBox.question(
            self, "Change Day",
            "Exit this archive session and return to the launch screen to pick "
            "a different day?\n\nCurrent playback position will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._tc.pause()
            self.change_day_requested.emit()

    def _on_playing_changed(self, playing: bool) -> None:
        self._btn_play.blockSignals(True)
        self._btn_play.setChecked(playing)
        self._btn_play.setText("⏸" if playing else "▶")
        self._btn_play.blockSignals(False)

    def _on_speed_changed(self, idx: int) -> None:
        self._tc.set_speed_by_index(idx)

    def set_letter_step_keys_enabled(self, enabled: bool) -> None:
        """A/D step frames unless another tool (the TRACK editor) has taken
        them over; the arrows and , . < > always step."""
        self._letter_step_keys_enabled = enabled
        for shortcut in getattr(self, "_letter_step_shortcuts", ()):
            shortcut.setEnabled(enabled)

    def _on_step_back(self) -> None:
        self._jump_to_scan(-1)

    def _on_step_forward(self) -> None:
        self._jump_to_scan(1)

    def _jump_to_scan(self, direction: int) -> None:
        """Jump to the nearest available radar scan before (-1) or after
        (+1) the current archive time. No-op if the scan index hasn't
        loaded yet (e.g. still fetching, or no station selected)."""
        if not self._scan_times:
            return
        current = self._tc.current_time
        if direction < 0:
            candidates = [t for t in self._scan_times if t < current]
            target = candidates[-1] if candidates else None
        else:
            candidates = [t for t in self._scan_times if t > current]
            target = candidates[0] if candidates else None
        if target is None:
            return
        self._tc.pause()
        self._tc.set_time(target)

    def _on_skip_start(self) -> None:
        self._tc.step(-10 if self._precision_mode else -60)

    def _on_skip_end(self) -> None:
        self._tc.step(10 if self._precision_mode else 60)

    def _show_jump_dialog(self) -> None:
        dlg = _JumpToTimeDialog(self._tc.current_time, self._tc.window, self._scan_times, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tc.pause()
            self._tc.set_time(dlg.chosen_time())


    def _update_time_display(self, t: datetime) -> None:
        self._date_label.setText(t.strftime("%Y-%m-%d"))
        self._utc_label.setText(t.strftime("%H:%M:%S UTC"))

        if _CT_TZ:
            ct = t.astimezone(_CT_TZ)
        if _MT_TZ:
            mt = t.astimezone(_MT_TZ)
        if _CT_TZ and _MT_TZ:
            self._local_label.setText(
                f"{ct.strftime('%H:%M %Z')} / {mt.strftime('%H:%M %Z')}"
            )


    def add_radar_selectors(
        self,
        products: list[str],      # list of (pyart_field, display_label)
        tilts: list[float],
    ) -> None:
        """
        Dynamically insert product and tilt combo boxes into the control row.
        Called by MainWindow once the first Level-2 scan arrives.
        """
        # already added.
        if hasattr(self, "_product_combo"):
            return

        row2 = self.layout().itemAt(1).layout()

        div = self._vdiv()
        row2.insertWidget(row2.count() - 1, div)

        prod_lbl = QLabel("PROD")
        prod_lbl.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 1, prod_lbl)

        self._product_combo = QComboBox()
        self._product_combo.setObjectName("archiveProductCombo")
        self._product_combo.setFixedWidth(120)
        for field, label in products:
            self._product_combo.addItem(label, userData=field)
        self._product_combo.currentIndexChanged.connect(
            lambda i: self.product_changed.emit(
                self._product_combo.itemData(i) or self._product_combo.itemText(i)
            )
        )
        row2.insertWidget(row2.count() - 1, self._product_combo)

        tilt_lbl = QLabel("TILT")
        tilt_lbl.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 1, tilt_lbl)

        self._tilt_combo = QComboBox()
        self._tilt_combo.setObjectName("archiveTiltCombo")
        self._tilt_combo.setFixedWidth(70)
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        self._tilt_combo.currentIndexChanged.connect(
            lambda i: self.tilt_changed.emit(i)
        )
        row2.insertWidget(row2.count() - 1, self._tilt_combo)

    def update_tilt_list(self, tilts: list[float]) -> None:
        """Refresh the tilt combo when a new scan arrives with different tilts."""
        if not hasattr(self, "_tilt_combo"):
            return
        current = self._tilt_combo.currentIndex()
        self._tilt_combo.blockSignals(True)
        self._tilt_combo.clear()
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        self._tilt_combo.setCurrentIndex(min(current, self._tilt_combo.count() - 1))
        self._tilt_combo.blockSignals(False)



class _JumpToTimeDialog(QDialog):
    """Jump within the current session only -- every fetcher was indexed for
    the session's span at construction, so there's nowhere else this dialog
    could go without leaving them pointed at stale data (use the DAY button
    to change days). When the session runs past 00Z a date choice appears,
    since a time like 01:30 could fall on either day."""

    def __init__(
        self,
        current_time: datetime,
        window: "tuple[datetime, datetime]",
        scan_times: "list[datetime] | None" = None,
        parent=None,
    ):
        super().__init__(parent)
        self._window = window
        start, end = window
        self._days = sorted({(start + timedelta(days=k)).date()
                             for k in range((end - start).days + 1)
                             if start + timedelta(days=k) < end})
        self.setWindowTitle("Jump to Time")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setFixedWidth(280)
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0F; }
            QLabel  { color: #8E97AB; font-size: 11px; background: transparent; }
            QTimeEdit, QComboBox {
                background-color: #1A1A2E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
                color: #E8EAF0;
                font-size: 13px;
                padding: 6px 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A2E;
                color: #E8EAF0;
                selection-background-color: #00CFFF;
                selection-color: #0A0A0F;
                outline: none;
            }
            QPushButton {
                background-color: #00CFFF;
                border: none;
                border-radius: 6px;
                color: #0A0A0F;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 20px;
            }
            QPushButton:hover { background-color: #33D9FF; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        # scroll through actual radar scan times when we have them -- faster
        # and less error-prone than typing a time that may fall in a gap
        # between scans. Manual entry below still works for times that
        # aren't tied to a scan (e.g. a specific vehicle/report time).
        session_scans = sorted(t for t in (scan_times or []) if start <= t < end)
        multi_day = len(self._days) > 1
        self._scan_combo = None
        if session_scans:
            layout.addWidget(QLabel("Scroll to a radar scan:"))
            self._scan_combo = QComboBox()
            for t in session_scans:
                self._scan_combo.addItem(t.strftime("%m-%d %H:%M:%S" if multi_day else "%H:%M:%S"), userData=t)
            nearest = min(session_scans, key=lambda t: abs(t - current_time))
            self._scan_combo.setCurrentIndex(session_scans.index(nearest))
            self._scan_combo.currentIndexChanged.connect(self._on_scan_picked)
            layout.addWidget(self._scan_combo)

        self._day_combo = None
        if multi_day:
            layout.addWidget(QLabel("Or enter a UTC date and time:"))
            self._day_combo = QComboBox()
            for day in self._days:
                self._day_combo.addItem(day.isoformat(), userData=day)
            self._day_combo.setCurrentIndex(self._days.index(current_time.date())
                                            if current_time.date() in self._days else 0)
            layout.addWidget(self._day_combo)
        else:
            layout.addWidget(QLabel(f"Or enter a UTC time ({self._days[0].isoformat()}):"))

        self._t_edit = QTimeEdit()
        self._t_edit.setDisplayFormat("HH:mm:ss")
        self._t_edit.setTime(QTime(current_time.hour, current_time.minute, current_time.second))
        layout.addWidget(self._t_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("JUMP")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_scan_picked(self, idx: int) -> None:
        t = self._scan_combo.itemData(idx)
        if t is not None:
            self._t_edit.setTime(QTime(t.hour, t.minute, t.second))
            if self._day_combo is not None and t.date() in self._days:
                self._day_combo.setCurrentIndex(self._days.index(t.date()))

    def chosen_time(self) -> datetime:
        """The chosen UTC time; the time controller keeps it inside the session."""
        day = self._day_combo.currentData() if self._day_combo is not None else self._days[0]
        qt_t = self._t_edit.time()
        return datetime(day.year, day.month, day.day, qt_t.hour(), qt_t.minute(), qt_t.second(),
                        tzinfo=timezone.utc)
