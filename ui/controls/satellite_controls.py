
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QFrame, QToolButton, QLabel,
    QSlider, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt, QEvent


class SatelliteControls(QWidget):
    """
    Collapsible two-row drawer for GOES-East satellite overlay controls.

    Row 1: CONUS / MESO-1 / MESO-2 mode buttons + opacity slider
    Row 2: ⏮ ⏪ ▶/⏸ ⏩ ⏭  +  timeline slider  +  time label

    MESO buttons are disabled until SatelliteFetcher confirms sector exists.
    Playback controls are disabled until at least 2 frames are cached.

    Signals:
        mode_changed(str)      — "", "conus", "meso1", "meso2"
        opacity_changed(float) — 0.0–1.0
        frame_requested(int)   — user selected a specific cache frame index
        loop_toggled(bool)     — playback loop started/stopped
        meso_preview(int,bool) — hover preview for MESO-1/2 sector box
    """

    mode_changed    = pyqtSignal(str)
    opacity_changed = pyqtSignal(float)
    frame_requested = pyqtSignal(int)
    loop_toggled    = pyqtSignal(bool)
    speed_changed   = pyqtSignal(int)      # new interval in ms
    meso_preview    = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating  = False
        self._meso_bboxes: dict[int, dict | None] = {1: None, 2: None}
        self._setup_ui()


    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("satelliteDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._btn_conus = self._btn("CONUS")
        self._btn_meso1 = self._btn("MESO-1")
        self._btn_meso2 = self._btn("MESO-2")

        self._btn_meso1.setEnabled(False)
        self._btn_meso1.setToolTip("Mesoscale sector 1 · unavailable")
        self._btn_meso2.setEnabled(False)
        self._btn_meso2.setToolTip("Mesoscale sector 2 · unavailable")

        self._btn_meso1.installEventFilter(self)
        self._btn_meso2.installEventFilter(self)

        self._btn_conus.toggled.connect(lambda on: self._on_mode_toggled("conus", on))
        self._btn_meso1.toggled.connect(lambda on: self._on_mode_toggled("meso1", on))
        self._btn_meso2.toggled.connect(lambda on: self._on_mode_toggled("meso2", on))

        for b in (self._btn_conus, self._btn_meso1, self._btn_meso2):
            r1.addWidget(b)

        r1.addWidget(self._vdiv())

        lbl = QLabel("OPACITY")
        lbl.setStyleSheet("color: #B5BDCC; font-size: 10px; letter-spacing: 0.5px;")
        lbl.setFixedHeight(22)
        r1.addWidget(lbl)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(70)
        self._opacity_slider.setFixedHeight(22)
        self._opacity_slider.setFixedWidth(110)
        self._opacity_slider.setToolTip("Satellite image opacity")
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        r1.addWidget(self._opacity_slider)
        r1.addStretch(1)
        col.addWidget(row1)

        row2 = QWidget()
        row2.setObjectName("satPlaybackRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = self._pbtn("⏮", "Oldest frame")
        self._btn_back       = self._pbtn("⏪", "Step back one frame")
        self._btn_play       = self._pbtn("▶", "Play / Pause loop", checkable=True)
        self._btn_fwd        = self._pbtn("⏩", "Step forward one frame")
        self._btn_jump_end   = self._pbtn("⏭", "Latest (live)")

        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)

        for b in (self._btn_jump_start, self._btn_back, self._btn_play,
                  self._btn_fwd, self._btn_jump_end):
            b.setEnabled(False)
            r2.addWidget(b)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.setFixedHeight(26)
        self._frame_slider.setMinimumWidth(80)
        self._frame_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._frame_slider.setEnabled(False)
        self._frame_slider.sliderReleased.connect(self._on_slider_released)
        r2.addWidget(self._frame_slider)

        self._time_label = QLabel("--:--Z")
        self._time_label.setObjectName("satTimeLabel")
        self._time_label.setFixedHeight(26)
        self._time_label.setMinimumWidth(52)
        r2.addWidget(self._time_label)

        # speed selector — 0.5×, 1×, 2×, 3×
        self._speed_combo = QComboBox()
        self._speed_combo.setFixedHeight(26)
        self._speed_combo.setMaximumWidth(5)
        self._speed_combo.setToolTip("Playback speed")
        for label, ms in [("0.5×", 1200), ("1×", 600), ("2×", 300), ("3×", 200)]:
            self._speed_combo.addItem(label, userData=ms)
        self._speed_combo.setCurrentIndex(1)   # default 1×
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_combo)

        col.addWidget(row2)
        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    def _pbtn(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setFixedSize(32, 26)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div


    def set_meso_available(self, idx: int, available: bool, bbox: dict | None = None):
        btn = self._btn_meso1 if idx == 1 else self._btn_meso2
        btn.setEnabled(available)
        self._meso_bboxes[idx] = bbox if available else None
        if available:
            btn.setToolTip(f"Mesoscale sector {idx}")
        else:
            btn.setToolTip(f"Mesoscale sector {idx} · unavailable")
            if btn.isChecked():
                self._set_mode("conus")
                self.mode_changed.emit("conus")

    def set_cache_size(self, n: int):
        """Update slider range as frame cache grows; stay at live if already there."""
        was_live = self.is_at_latest_frame()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, n - 1))
        if was_live:
            self._frame_slider.setValue(n - 1)
        self._frame_slider.blockSignals(False)
        has_history = n > 1
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int):
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

    def set_scan_time(self, time_str: str):
        self._time_label.setText(time_str)

    def current_frame(self) -> int:
        return self._frame_slider.value()

    def current_mode(self) -> str:
        if self._btn_conus.isChecked():
            return "conus"
        if self._btn_meso1.isChecked():
            return "meso1"
        if self._btn_meso2.isChecked():
            return "meso2"
        return ""

    def is_at_latest_frame(self) -> bool:
        return self._frame_slider.value() >= self._frame_slider.maximum()

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def stop_loop(self):
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)

    def reset_cache_ui(self):
        self.stop_loop()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        self._time_label.setText("--:--Z")
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(False)

    def configure_for_archive(self, enabled: bool):
        row2 = self._drawer.layout().itemAt(1).widget()
        if row2 is not None:
            row2.setVisible(not enabled)
        if enabled:
            # meso buttons are disabled until live sectors are found; in archive
            self._btn_meso1.setEnabled(True)
            self._btn_meso1.setToolTip("Mesoscale sector 1")
            self._btn_meso2.setEnabled(True)
            self._btn_meso2.setToolTip("Mesoscale sector 2")

    def toggle_drawer(self, checked: bool):
        if checked:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            current = 0
        else:
            current = self.height()
            self.setMaximumHeight(current)
            target = 0
            self.meso_preview.emit(1, False)
            self.meso_preview.emit(2, False)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim

    def eventFilter(self, obj, event):
        if obj in (self._btn_meso1, self._btn_meso2):
            idx = 1 if obj is self._btn_meso1 else 2
            if event.type() == QEvent.Type.Enter:
                if obj.isEnabled() and self._meso_bboxes.get(idx):
                    self.meso_preview.emit(idx, True)
            elif event.type() == QEvent.Type.Leave:
                self.meso_preview.emit(idx, False)
        return super().eventFilter(obj, event)


    def _set_mode(self, mode: str):
        self._updating = True
        try:
            self._btn_conus.setChecked(mode == "conus")
            self._btn_meso1.setChecked(mode == "meso1")
            self._btn_meso2.setChecked(mode == "meso2")
        finally:
            self._updating = False

    def _on_mode_toggled(self, mode: str, checked: bool):
        if self._updating:
            return
        if checked:
            self._set_mode(mode)
            self.mode_changed.emit(mode)
        else:
            if not any((
                self._btn_conus.isChecked(),
                self._btn_meso1.isChecked(),
                self._btn_meso2.isChecked(),
            )):
                self.mode_changed.emit("")

    def _on_play_toggled(self, checked: bool):
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_slider_released(self):
        self.frame_requested.emit(self._frame_slider.value())

    def _on_jump_start(self):
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self):
        n = self._frame_slider.maximum()
        self.set_frame(n)
        self.frame_requested.emit(n)

    def _on_step_back(self):
        v = max(0, self._frame_slider.value() - 1)
        self.set_frame(v)
        self.frame_requested.emit(v)

    def _on_step_forward(self):
        v = min(self._frame_slider.maximum(), self._frame_slider.value() + 1)
        self.set_frame(v)
        self.frame_requested.emit(v)

    def _on_speed_changed(self, index: int):
        ms = self._speed_combo.itemData(index)
        if ms is not None:
            self.speed_changed.emit(ms)
