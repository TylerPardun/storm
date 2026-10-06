
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QToolButton, QComboBox, QFrame, QSlider, QMessageBox, QPushButton,
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


class _HourAxis(QWidget):
    """Hour ticks and labels under the time slider (a tick every hour; a
    label every hour, or every 2 or 3 when the bar is narrow), aligned with
    the slider's groove."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._tc = controller
        self.setFixedHeight(17)

    def paintEvent(self, _event) -> None:
        from PyQt6.QtGui import QColor, QFont, QPainter
        start, end = self._tc.window
        total = (end - start).total_seconds()
        if total <= 0:
            return
        painter = QPainter(self)
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        inset, width = 8, max(1, self.width() - 16)
        per_hour = width * 3600 / total
        room = metrics.horizontalAdvance("00Z") + 12
        step = next((n for n in (1, 2, 3, 6) if per_hour * n >= room), 6)
        t = start.replace(minute=0, second=0, microsecond=0)
        if t < start:
            t += timedelta(hours=1)
        while t <= end:
            x = inset + width * (t - start).total_seconds() / total
            labeled = t.hour % step == 0
            painter.setPen(QColor("#49536F") if labeled else QColor("#323A50"))
            painter.drawLine(int(x), 0, int(x), 4 if labeled else 3)
            if labeled:
                label = f"{t:%H}Z"
                w = metrics.horizontalAdvance(label)
                painter.setPen(QColor("#6B7493") if t.hour == 0 else QColor("#5B6480"))
                painter.drawText(int(min(max(0, x - w / 2), self.width() - w)), 15, label)
            t += timedelta(hours=1)


class ArchiveControls(QWidget):
    """
    The archive bar across the bottom of the map: the one place for the
    archive clock and data status (header), the timeline (slider with hour
    labels and any coverage strip), and playback (Play, speed, scan steps).
    MainWindow adds the cursor position, vehicle count and status messages
    on the right of the controls row (add_info_widget), so archive mode has
    no separate status panel.

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
        root.setContentsMargins(12, 7, 12, 7)
        root.setSpacing(3)

        def label(text, style):
            lbl = QLabel(text)
            lbl.setStyleSheet(style)
            return lbl

        # ---- header: mode, the archive clock, then what's on screen --------
        head = QHBoxLayout()
        head.setSpacing(10)
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(label("● ARCHIVE", "color: #FF9F1C; font-size: 10px; font-weight: 700; letter-spacing: 1px;"))
        self._utc_label = label("--:--:-- UTC", "color: #F2F5FA; font-size: 16px; font-weight: 700;")
        head.addWidget(self._utc_label)
        self._date_label = label("---", "color: #C8D0DE; font-size: 11px; font-weight: 500;")
        head.addWidget(self._date_label)
        self._local_label = label("", "color: #8E97AB; font-size: 11px;")
        head.addWidget(self._local_label)
        head.addStretch()
        # the radar on screen: site and volume time, then its product / state
        self._radar_time_label = label("Radar —", "color: #E8EAF0; font-size: 11px; font-weight: 600;")
        self._radar_time_label.setToolTip("Volume time of the radar image on the map")
        head.addWidget(self._radar_time_label)
        self._radar_status = label("", "")
        head.addWidget(self._radar_status)
        head.addWidget(self._vdiv())
        self._sat_status = label("", "")
        head.addWidget(self._sat_status)
        self._obs_status = label("", "")
        head.addWidget(self._obs_status)
        self.set_radar_status("waiting")
        self.set_satellite_status("Sat: --")
        self.set_obs_status("OBS: MQTT")
        root.addLayout(head)

        # ---- timeline -------------------------------------------------------
        # seconds since the session start, running to the session's end (which
        # can be into the next UTC day). Dragging only updates the clock text
        # (cheap); the seek (which fans out to every fetcher) fires on release.
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setTracking(False)
        self._slider.setFixedHeight(16)
        self._slider.sliderMoved.connect(self._on_slider_preview)
        self._slider.valueChanged.connect(self._on_slider_committed)
        root.addWidget(self._slider)
        # when the chosen lidar was scanning (set_coverage), under the slider
        self._coverage = _CoverageStrip(self._tc, self)
        root.addWidget(self._coverage)
        self._hours = _HourAxis(self._tc, self)
        root.addWidget(self._hours)

        # ---- playback, then the info MainWindow adds on the right -----------
        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 2, 0, 0)
        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setObjectName("archivePlayButton")
        self._btn_play.setToolTip("Space")
        self._btn_play.setCheckable(True)
        self._btn_play.setFixedHeight(24)
        self._btn_play.setMinimumWidth(78)
        row.addWidget(self._btn_play)
        row.addSpacing(6)

        self._speed_label = label("SPEED", "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;")
        row.addWidget(self._speed_label)
        self._speed_combo = QComboBox()
        self._speed_combo.setObjectName("archiveSpeedCombo")
        for s in SPEED_OPTIONS:
            self._speed_combo.addItem(f"{s}×")
        self._speed_combo.setCurrentIndex(SPEED_OPTIONS.index(self._tc.speed))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setFixedWidth(54)
        row.addWidget(self._speed_combo)
        self._speed_div = self._vdiv()
        row.addWidget(self._speed_div)

        self._btn_start = self._ctrl_btn("-1m", "")
        self._btn_back = self._ctrl_btn("⏮", "Previous radar scan (Left / A)")
        self._btn_fwd = self._ctrl_btn("⏭", "Next radar scan (Right / D)")
        self._btn_end = self._ctrl_btn("+1m", "")
        for btn in (self._btn_start, self._btn_back, self._btn_fwd, self._btn_end):
            row.addWidget(btn)
        row.addStretch()
        self._info_row = row
        root.addLayout(row)
        self._precision_mode = False

        # populate the slider/labels from wherever the clock actually starts,
        # rather than leaving them at placeholder text until the first step.
        self._on_time_changed(self._tc.current_time)

    def add_info_widget(self, widget: QWidget) -> None:
        """Put a widget at the right end of the controls row (MainWindow:
        cursor position, vehicle count, status messages)."""
        if self._info_row.count() > 0 and self._info_row.itemAt(self._info_row.count() - 1).widget() is not None:
            self._info_row.addWidget(self._vdiv())
        self._info_row.addWidget(widget)

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

    @staticmethod
    def _dot(text: str, color: str) -> str:
        return f'<span style="color:{color}">●</span> {text}'

    def set_radar_status(self, text: str, error: bool = False) -> None:
        """e.g. 'Radar: REF 0.7°' or 'Radar: loading KOAX…' -- shown after the
        radar's site and time, without the 'Radar:' prefix."""
        self._radar_status.setText(str(text).removeprefix("Radar:").strip())
        self._radar_status.setStyleSheet(
            f"color: {'#FF8F8F' if error else '#8E97AB'}; font-size: 11px; font-weight: 600;")

    def set_rendered_radar(self, scan) -> None:
        if scan is None:
            self._radar_time_label.setText("Radar —")
            return
        timestamp = scan.scan_time.astimezone(timezone.utc)
        self._radar_time_label.setText(f"{scan.site} · {timestamp:%H:%M:%SZ}")
        self._radar_time_label.setToolTip(f"Radar image on the map: {scan.site}, volume time "
                                          f"{timestamp:%d %b %Y %H:%M:%S} UTC")

    def set_satellite_status(self, text: str, error: bool = False) -> None:
        state = str(text).removeprefix("Sat:").strip()
        color = "#FF8F8F" if error else ("#5B6480" if state in ("--", "waiting", "") or state.startswith("none") else "#39D98A")
        self._sat_status.setText(self._dot("Sat", color))
        self._sat_status.setToolTip(f"Satellite: {state}")
        self._sat_status.setStyleSheet("color: #8E97AB; font-size: 11px; font-weight: 600;")

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
        """e.g. 'OBS: 1-second partial 3/7' -> '● Mesonet 1-second partial 3/7'."""
        state = str(text).removeprefix("OBS:").strip()
        self._obs_status.setText(self._dot(f"Mesonet {state}", "#39D98A" if active else "#5B6480"))
        self._obs_status.setStyleSheet("color: #8E97AB; font-size: 11px; font-weight: 600;")

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
        self._shortcuts_enabled = True
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
        self._shortcuts: list[QShortcut] = []

        def key(seq, slot):
            sc = QShortcut(QKeySequence(seq), win)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)
            return sc
        key(Qt.Key.Key_Space, self._tc.toggle_play)
        key(Qt.Key.Key_Left, self._on_step_back)
        key(Qt.Key.Key_Right, self._on_step_forward)
        self._letter_step_shortcuts = [key(Qt.Key.Key_A, self._on_step_back),
                                       key(Qt.Key.Key_D, self._on_step_forward)]
        # , and < step back a radar frame, . and > forward (as in MESO-VIEW).
        # "<" arrives as "<" on some keyboards and Shift+"," on others.
        for seq in (",", "<", "Shift+<", "Shift+,"):
            key(seq, self._on_step_back)
        for seq in (".", ">", "Shift+>", "Shift+."):
            key(seq, self._on_step_forward)
        key(Qt.Key.Key_Home, self._on_skip_start)
        key(Qt.Key.Key_End, self._on_skip_end)
        self.set_shortcuts_enabled(self._shortcuts_enabled)

    def set_shortcuts_enabled(self, enabled: bool) -> None:
        """All of the bar's keys on or off (off while the Video Studio, which
        has its own, is open)."""
        self._shortcuts_enabled = enabled
        for shortcut in getattr(self, "_shortcuts", ()):
            shortcut.setEnabled(enabled)
        self.set_letter_step_keys_enabled(self._letter_step_keys_enabled)

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
        self._hours.update()
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
        self._btn_play.setText("⏸  Pause" if playing else "▶  Play")
        self._btn_play.blockSignals(False)

    def _on_speed_changed(self, idx: int) -> None:
        self._tc.set_speed_by_index(idx)

    def set_letter_step_keys_enabled(self, enabled: bool) -> None:
        """A/D step frames unless another tool (the TRACK editor) has taken
        them over; the arrows and , . < > always step."""
        self._letter_step_keys_enabled = enabled
        for shortcut in getattr(self, "_letter_step_shortcuts", ()):
            shortcut.setEnabled(enabled and self._shortcuts_enabled)

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
        self._date_label.setText(f"{t:%a} {t.day} {t:%b %Y}")
        self._utc_label.setText(t.strftime("%H:%M:%S UTC"))

        if _CT_TZ:
            ct = t.astimezone(_CT_TZ)
        if _MT_TZ:
            mt = t.astimezone(_MT_TZ)
        if _CT_TZ and _MT_TZ:
            self._local_label.setText(
                f"{ct.strftime('%H:%M %Z')} / {mt.strftime('%H:%M %Z')}"
            )
