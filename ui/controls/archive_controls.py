
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QToolButton, QComboBox, QFrame, QSlider, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
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


class _CoverageStrip(QWidget):
    """A thin strip under the time slider marking when something (e.g. the
    chosen lidar) has data: colored spans over the session window."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._tc = controller
        self._spans: list = []
        self._color = "#00CFFF"
        self.setFixedHeight(5)
        self.setVisible(False)

    def set_spans(self, spans, color: str, tooltip: str) -> None:
        self._spans, self._color = list(spans), color
        self.setToolTip(tooltip)
        self.setVisible(bool(self._spans))
        self.update()

    def paintEvent(self, _event) -> None:
        from PyQt6.QtGui import QColor, QPainter
        start, end = self._tc.window
        total = (end - start).total_seconds()
        if total <= 0:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(255, 255, 255, 18))
        # the slider's groove runs a handle-width in from each side
        inset = 8
        width = max(1, self.width() - 2 * inset)
        for a, b in self._spans:
            x0 = inset + width * max(0.0, (a - start).total_seconds() / total)
            x1 = inset + width * min(1.0, (b - start).total_seconds() / total)
            painter.fillRect(int(x0), 0, max(2, int(x1 - x0)), self.height(), QColor(self._color))


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
        # when the chosen lidar was scanning (set_coverage), under the slider
        self._coverage = _CoverageStrip(self._tc, self)
        root.addWidget(self._coverage)

        row2 = QHBoxLayout()
        row2.setSpacing(5)
        row2.setContentsMargins(0, 0, 0, 0)

        self._btn_start = self._ctrl_btn("-1m", "")
        self._btn_back  = self._ctrl_btn("⏮", "Previous radar scan (Left / A)")
        self._btn_play  = self._ctrl_btn("▶",  "Play / pause (Space)")
        self._btn_play.setCheckable(True)
        self._btn_fwd   = self._ctrl_btn("⏭", "Next radar scan (Right / D)")
        self._btn_end   = self._ctrl_btn("+1m", "")

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

        self._speed_div = self._vdiv()          # hidden with the speed control
        row2.addWidget(self._speed_div)

        from PyQt6.QtWidgets import QMenu
        case_btn = self._ctrl_btn("CASE", "Export this case's saved work and provenance, or open a case package")
        case_btn.setFixedWidth(44)
        case_menu = QMenu(case_btn)
        case_menu.addAction("Export case package…", self.export_case_requested.emit)
        case_menu.addAction("Open case package…", self.open_case_requested.emit)
        case_btn.setMenu(case_menu)
        case_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        case_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")   # arrow overlapped the text
        row2.addWidget(case_btn)

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
        self._speed_label.setVisible(not enabled)
        self._speed_combo.setVisible(not enabled)
        self._speed_div.setVisible(not enabled)
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

    def set_coverage(self, spans, color: str = "#00CFFF", tooltip: str = "") -> None:
        """Mark (start, end) spans under the slider, e.g. when a lidar was
        scanning; an empty list hides the strip."""
        self._coverage.set_spans(spans, color, tooltip)

    def _on_window_changed(self, start: datetime, end: datetime) -> None:
        self._coverage.update()
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setValue(self._tc.seconds_since_start())
        self._slider.blockSignals(False)

    def _on_slider_preview(self, value: int) -> None:
        """Live label update while dragging — no fetcher calls until release."""
        start, _ = self._tc.window
        self._update_time_display(start + timedelta(seconds=value))

    def _on_slider_committed(self, value: int) -> None:
        self._tc.pause()
        self._tc.set_seconds_since_start(value)

    def request_exit(self) -> None:
        """Ask, then leave this session to pick another day (the EXIT button
        at the map's top right, MainWindow)."""
        self._on_change_day_clicked()

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
