"""Floating drawer for observation trails (core/trails.py): which quantity
colors the trails, how far back they reach, time-to-space, and the color
bar, and wind barbs along the trails. Same collapsible-drawer shell as
TrackControls."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.derived import QUANTITIES

_GROUPS = (
    ("Measured", ("temperature", "dewpoint", "rh", "pressure", "wind_speed")),
    ("Thermodynamic", ("theta", "theta_v", "theta_e", "theta_w", "mixing_ratio")),
    ("Kinematic", ("u", "v", "sr_wind", "radial_wind", "tangential_wind")),
)
WINDOWS_MIN = (5, 10, 15, 30, 60)
NEEDS_TRACK_TEXT = "Needs storm motion: track the storm ▸"
NEEDS_TRACK_TIP = ("Storm-relative tools use the storm's motion, taken from a storm track. "
                   "Open TRACK and click the storm's center on the map at two or more times.")


def needs_track_link(on_click) -> QPushButton:
    """The link shown beside storm-relative options while there is no storm motion."""
    link = QPushButton(NEEDS_TRACK_TEXT)
    link.setObjectName("needsTrackLink")
    link.setFlat(True)
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    link.setToolTip(NEEDS_TRACK_TIP)
    link.clicked.connect(on_click)
    return link


class _ColorBar(QWidget):
    """The trail scale: its color bands, with each band edge labeled below."""

    BAR_H = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stops: list = []
        self._edges: list = []
        self.setFixedHeight(self.BAR_H + 14)

    def set_stops(self, stops: list) -> None:
        from core.trails import band_edges
        self._stops = stops
        self._edges = band_edges(stops) if len(stops) >= 4 else []
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        bar = self.rect().adjusted(0, 0, 0, -(self.height() - self.BAR_H))
        if len(self._stops) < 4:
            painter.fillRect(bar, QColor("#2A2B45"))
            return
        values, colors = self._stops[0::2], self._stops[1::2]
        lo, hi = values[0], values[-1]
        gradient = QLinearGradient(0, 0, self.width(), 0)
        for value, color in zip(values, colors):
            gradient.setColorAt((value - lo) / (hi - lo) if hi > lo else 0, QColor(color))
        painter.fillRect(bar, gradient)
        # edge labels: as many as fit without touching (always the two ends)
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        step = abs(self._edges[1] - self._edges[0]) if len(self._edges) > 1 else 1.0
        decimals = 0 if step >= 1 else (1 if step >= 0.1 else 2)
        texts = [f"{e:.{decimals}f}" for e in self._edges]
        width = self.width()
        xs = [(e - lo) / (hi - lo) * width if hi > lo else 0 for e in self._edges]
        widest = max(metrics.horizontalAdvance(t) for t in texts) + 6
        every = 1
        while every < len(texts) and width / max(1, (len(texts) - 1) / every) < widest:
            every += 1
        painter.setPen(QColor("#9BA3B2"))
        for i, (x, text) in enumerate(zip(xs, texts)):
            if i % every and i != len(texts) - 1:
                continue
            w = metrics.horizontalAdvance(text)
            painter.drawText(int(min(max(0, x - w / 2), width - w)), self.height() - 2, text)
            painter.drawLine(int(min(x, width - 1)), self.BAR_H, int(min(x, width - 1)), self.BAR_H + 2)


class TrailControls(QWidget):
    """settings_changed() -- any option changed; read them with settings()."""

    settings_changed = pyqtSignal()
    track_requested = pyqtSignal()       # "track the storm" link: open the TRACK drawer

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        drawer = QWidget()
        drawer.setObjectName("trailDrawer")
        row = QHBoxLayout(drawer)              # one row, like the other toolbar drawers
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        # trails live in the VEHICLES drawer; off until asked for, like "show data"
        self._chk_show = QCheckBox("show trails")
        self._chk_show.setChecked(False)
        self._chk_show.toggled.connect(self.settings_changed.emit)
        row.addWidget(self._chk_show)
        small = "color: #B5BDCC; font-size: 10px;"
        # checkboxes dim when unavailable (a plain color here would hide that)
        small_check = "QCheckBox { color: #B5BDCC; font-size: 10px; } QCheckBox:disabled { color: #4A5064; }"

        self._quantity = QComboBox()
        self._quantity.setFixedWidth(250)
        for group, keys in _GROUPS:
            self._quantity.addItem(f"— {group} —")
            header = self._quantity.model().item(self._quantity.count() - 1)
            header.setEnabled(False)
            for key in keys:
                self._quantity.addItem(QUANTITIES[key].label, key)
        self._quantity.setCurrentIndex(self._quantity.findData("theta_e"))
        self._quantity.currentIndexChanged.connect(self.settings_changed.emit)
        row.addWidget(self._quantity)

        label = QLabel("Last")
        label.setStyleSheet(small)
        row.addWidget(label)
        self._window = QComboBox()
        self._window.setFixedWidth(80)
        for minutes in WINDOWS_MIN:
            self._window.addItem(f"{minutes} min", minutes)
        self._window.setCurrentIndex(WINDOWS_MIN.index(15))
        self._window.currentIndexChanged.connect(self.settings_changed.emit)
        row.addWidget(self._window)
        self._time_to_space = QCheckBox("Time-to-space")
        self._time_to_space.setStyleSheet(small_check)
        self._time_to_space.toggled.connect(self.settings_changed.emit)
        row.addWidget(self._time_to_space)
        self._wind_barbs = QCheckBox("Wind barbs")
        self._wind_barbs.setStyleSheet(small_check)
        self._wind_barbs.setToolTip("Measured wind along each trail (knots; half barb 5, full 10, flag 50)")
        self._wind_barbs.toggled.connect(self.settings_changed.emit)
        row.addWidget(self._wind_barbs)
        self._sr_barbs = QCheckBox("Storm-relative")
        self._sr_barbs.setStyleSheet(small_check)
        self._sr_barbs.setEnabled(False)
        self._sr_barbs.toggled.connect(self.settings_changed.emit)
        self._wind_barbs.toggled.connect(self._update_sr_barbs)
        row.addWidget(self._sr_barbs)
        self._needs_track = needs_track_link(self.track_requested.emit)
        row.addWidget(self._needs_track)
        row.addSpacing(8)

        self._min_label, self._max_label = QLabel(""), QLabel("")
        for lbl in (self._min_label, self._max_label):
            lbl.setStyleSheet(small)
        self._bar = _ColorBar()
        self._bar.setFixedWidth(260)
        row.addWidget(self._min_label)
        row.addWidget(self._bar)
        row.addWidget(self._max_label)
        row.addSpacing(8)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        row.addWidget(self._status)
        row.addStretch()
        outer.addWidget(drawer)
        self.set_track_available(False)

    # ---- state -------------------------------------------------------
    def trails_on(self) -> bool:
        return self._chk_show.isChecked()

    def settings(self) -> tuple[str, int, bool, bool, bool]:
        """(quantity key, window minutes, time-to-space, wind barbs, storm-relative barbs)"""
        return (self._quantity.currentData(), self._window.currentData(),
                self._time_to_space.isChecked() and self._time_to_space.isEnabled(),
                self._wind_barbs.isChecked(),
                self._sr_barbs.isChecked() and self._sr_barbs.isEnabled())

    def _update_sr_barbs(self, *_args) -> None:
        """Storm-relative barbs need barbs on and a storm track."""
        self._sr_barbs.setEnabled(self._wind_barbs.isChecked() and self._track_available)
        self._sr_barbs.setToolTip(
            "Barbs show the wind minus the storm motion (the track's mean), where the track covers the time"
            if self._track_available else "Needs a storm track: place at least two TRACK points at different times")

    def set_track_available(self, available: bool) -> None:
        """Storm-relative quantities and time-to-space need a storm track."""
        self._track_available = available
        self._needs_track.setVisible(not available)
        self._update_sr_barbs()
        self._time_to_space.setEnabled(available)
        self._time_to_space.setToolTip(
            "Place each observation at its offset from the storm center at its own time"
            if available else "Needs a storm track: place at least two TRACK points at different times")
        model = self._quantity.model()
        for i in range(self._quantity.count()):
            key = self._quantity.itemData(i)
            if key and QUANTITIES[key].needs_track:
                model.item(i).setEnabled(available)
        if not available and QUANTITIES[self._quantity.currentData()].needs_track:
            self._quantity.setCurrentIndex(self._quantity.findData("theta_e"))

    def set_scale(self, stops: list, vmin: float, vmax: float, units: str) -> None:
        """The bar labels its own band edges; the units go after it."""
        self._bar.set_stops(stops)
        self._min_label.setText("")
        self._max_label.setText(units if stops else "")

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
