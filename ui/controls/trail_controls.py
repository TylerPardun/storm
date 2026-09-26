"""Floating drawer for observation trails (core/trails.py): which quantity
colours the trails, how far back they reach, time-to-space, and the colour
bar. Same collapsible-drawer shell as TrackControls."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.derived import QUANTITIES

_GROUPS = (
    ("Measured", ("temperature", "dewpoint", "rh", "pressure", "wind_speed")),
    ("Thermodynamic", ("theta", "theta_v", "theta_e", "theta_w", "mixing_ratio")),
    ("Kinematic", ("u", "v", "sr_wind", "radial_wind", "tangential_wind")),
)
WINDOWS_MIN = (5, 10, 15, 30, 60)


class _ColorBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stops: list = []
        self.setFixedHeight(10)

    def set_stops(self, stops: list) -> None:
        self._stops = stops
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if len(self._stops) < 4:
            painter.fillRect(self.rect(), QColor("#2A2B45"))
            return
        values, colors = self._stops[0::2], self._stops[1::2]
        lo, hi = values[0], values[-1]
        gradient = QLinearGradient(0, 0, self.width(), 0)
        for value, color in zip(values, colors):
            gradient.setColorAt((value - lo) / (hi - lo) if hi > lo else 0, QColor(color))
        painter.fillRect(self.rect(), gradient)


class TrailControls(QWidget):
    """settings_changed() -- any option changed; read them with settings()."""

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        drawer = QWidget()
        drawer.setObjectName("trailDrawer")
        col = QVBoxLayout(drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        self._quantity = QComboBox()
        for group, keys in _GROUPS:
            self._quantity.addItem(f"— {group} —")
            header = self._quantity.model().item(self._quantity.count() - 1)
            header.setEnabled(False)
            for key in keys:
                self._quantity.addItem(QUANTITIES[key].label, key)
        self._quantity.setCurrentIndex(self._quantity.findData("theta_e"))
        self._quantity.currentIndexChanged.connect(self.settings_changed.emit)
        col.addWidget(self._quantity)

        row = QHBoxLayout()
        label = QLabel("Last")
        label.setStyleSheet("color: #B5BDCC; font-size: 10px;")
        row.addWidget(label)
        self._window = QComboBox()
        for minutes in WINDOWS_MIN:
            self._window.addItem(f"{minutes} min", minutes)
        self._window.setCurrentIndex(WINDOWS_MIN.index(15))
        self._window.currentIndexChanged.connect(self.settings_changed.emit)
        row.addWidget(self._window)
        self._time_to_space = QCheckBox("Time-to-space")
        self._time_to_space.setStyleSheet("color: #B5BDCC; font-size: 10px;")
        self._time_to_space.toggled.connect(self.settings_changed.emit)
        row.addWidget(self._time_to_space)
        row.addStretch()
        col.addLayout(row)

        self._bar = _ColorBar()
        col.addWidget(self._bar)
        ends = QHBoxLayout()
        self._min_label, self._max_label = QLabel(""), QLabel("")
        for lbl in (self._min_label, self._max_label):
            lbl.setStyleSheet("color: #B5BDCC; font-size: 10px;")
        ends.addWidget(self._min_label)
        ends.addStretch()
        ends.addWidget(self._max_label)
        col.addLayout(ends)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        col.addWidget(self._status)
        outer.addWidget(drawer)
        self.set_track_available(False)

    # ---- state -------------------------------------------------------
    def settings(self) -> tuple[str, int, bool]:
        """(quantity key, window minutes, time-to-space)"""
        return (self._quantity.currentData(), self._window.currentData(),
                self._time_to_space.isChecked() and self._time_to_space.isEnabled())

    def set_track_available(self, available: bool) -> None:
        """Storm-relative quantities and time-to-space need a storm track."""
        self._time_to_space.setEnabled(available)
        self._time_to_space.setToolTip(
            "Place each observation at its offset from the storm centre at its own time"
            if available else "Needs a storm track: place at least two TRACK points at different times")
        model = self._quantity.model()
        for i in range(self._quantity.count()):
            key = self._quantity.itemData(i)
            if key and QUANTITIES[key].needs_track:
                model.item(i).setEnabled(available)
        if not available and QUANTITIES[self._quantity.currentData()].needs_track:
            self._quantity.setCurrentIndex(self._quantity.findData("theta_e"))

    def set_scale(self, stops: list, vmin: float, vmax: float, units: str) -> None:
        self._bar.set_stops(stops)
        ok = vmin == vmin and vmax == vmax      # not NaN
        self._min_label.setText(f"{vmin:.1f} {units}" if ok else "")
        self._max_label.setText(f"{vmax:.1f} {units}" if ok else "")

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def toggle_drawer(self, checked: bool) -> None:
        if checked:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            current = 0
        else:
            current = self.height()
            self.setMaximumHeight(current)
            target = 0
        if self._animation:
            self._animation.stop()
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim
