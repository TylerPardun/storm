"""Floating drawer for the archive-mode subjective storm-track feature:
point count, Export As, Clear Track. Mirrors RawLidarControls'/
LandcoverControls' collapsible-drawer shell (ui/controls/raw_lidar_controls.py,
ui/controls/landcover_controls.py). No product/source pickers here -- those
live in radar_controls.py and are driven by the R/V shortcuts, not this panel.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class TrackControls(QWidget):
    """Signals
    -------
    export_requested()   user clicked "Export As…"
    clear_requested()    user clicked "Clear Track"
    """

    export_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("trackDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        self._count_label = QLabel("0 points")
        self._count_label.setStyleSheet("color: #B5BDCC; font-size: 10px; letter-spacing: 0.5px;")
        col.addWidget(self._count_label)

        btn_row = QHBoxLayout()
        self._btn_export = QPushButton("EXPORT AS…")
        self._btn_export.setToolTip("Save a copy of the track as CSV or Excel")
        self._btn_export.clicked.connect(self.export_requested.emit)
        btn_row.addWidget(self._btn_export)

        self._btn_clear = QPushButton("CLEAR TRACK")
        self._btn_clear.setToolTip("Remove all track points and start a new file")
        self._btn_clear.clicked.connect(self.clear_requested.emit)
        btn_row.addWidget(self._btn_clear)
        col.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        col.addWidget(self._status_label)

        outer.addWidget(self._drawer)

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

    def set_point_count(self, count: int) -> None:
        self._count_label.setText(f"{count} point" + ("" if count == 1 else "s"))

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)
