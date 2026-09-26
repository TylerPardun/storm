"""Floating drawer for the archive-mode subjective storm-track feature:
point count, the file being saved to, Load Track / Reset to Original,
Undo / Redo, Export As / Clear Track, and line/point visibility. Mirrors RawLidarControls'/
LandcoverControls' collapsible-drawer shell (ui/controls/raw_lidar_controls.py,
ui/controls/landcover_controls.py). No product/source pickers here -- those
live in radar_controls.py and are driven by the R/V shortcuts, not this panel.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QCheckBox, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class TrackControls(QWidget):
    """Signals
    -------
    load_requested()     user clicked "Load Track…"
    reset_requested()    user clicked "Reset to Original"
    undo_requested()     user clicked "Undo"
    redo_requested()     user clicked "Redo"
    export_requested()   user clicked "Export As…"
    clear_requested()    user clicked "Clear Track"
    layers_changed(bool, bool)  line / points visibility
    """

    load_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    export_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    layers_changed = pyqtSignal(bool, bool)

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

        self._file_label = QLabel("New track — nothing saved yet")
        self._file_label.setWordWrap(True)
        self._file_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        col.addWidget(self._file_label)

        load_row = QHBoxLayout()
        self._btn_load = QPushButton("LOAD TRACK…")
        self._btn_load.setToolTip("Open an existing track (CSV or Excel, from STORM or MESO-VIEW) to view and edit")
        self._btn_load.clicked.connect(self.load_requested.emit)
        load_row.addWidget(self._btn_load)
        self._btn_reset = QPushButton("RESET TO ORIGINAL")
        self._btn_reset.setToolTip("Undo every edit since the track was loaded")
        self._btn_reset.clicked.connect(self.reset_requested.emit)
        self._btn_reset.setEnabled(False)
        load_row.addWidget(self._btn_reset)
        col.addLayout(load_row)

        edit_row = QHBoxLayout()
        self._btn_undo = QPushButton("UNDO")
        self._btn_undo.setToolTip("Undo the last track edit (Ctrl+Z)")
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        edit_row.addWidget(self._btn_undo)
        self._btn_redo = QPushButton("REDO")
        self._btn_redo.setToolTip("Redo the last undone edit (Ctrl+Shift+Z)")
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        edit_row.addWidget(self._btn_redo)
        col.addLayout(edit_row)

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

        motion_title = QLabel("STORM MOTION")
        motion_title.setStyleSheet("color: #8E97AB; font-size: 9px; letter-spacing: 1px;")
        col.addWidget(motion_title)
        self._motion_mean = QLabel()
        self._motion_now = QLabel()
        for label, tip in (
            (self._motion_mean, "End-to-end: first point to last, as in MESO-VIEW"),
            (self._motion_now, "The track segment at the current time"),
        ):
            label.setToolTip(tip)
            label.setStyleSheet("color: #E3E8F2; font-size: 10px;")
            col.addWidget(label)
        self.set_motion(None, None)

        layer_row = QHBoxLayout()
        self._chk_line = QCheckBox("Line")
        self._chk_line.setChecked(True)
        self._chk_points = QCheckBox("Points")
        self._chk_points.setChecked(True)
        for chk in (self._chk_line, self._chk_points):
            chk.setStyleSheet("color: #B5BDCC; font-size: 10px;")
            chk.toggled.connect(self._emit_layers)
            layer_row.addWidget(chk)
        layer_row.addStretch()
        col.addLayout(layer_row)

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

    def _emit_layers(self, _checked: bool = False) -> None:
        self.layers_changed.emit(self._chk_line.isChecked(), self._chk_points.isChecked())

    def set_file(self, text: str) -> None:
        self._file_label.setText(text)

    def set_edit_state(self, *, loaded: bool, can_undo: bool, can_redo: bool) -> None:
        self._btn_reset.setEnabled(loaded)
        self._btn_undo.setEnabled(can_undo)
        self._btn_redo.setEnabled(can_redo)

    def set_point_count(self, count: int) -> None:
        self._count_label.setText(f"{count} point" + ("" if count == 1 else "s"))

    def set_motion(self, mean: str | None, now: str | None) -> None:
        """Motion readouts, e.g. "from 240° at 15.1 m/s (29 kt)"; None shows a dash."""
        self._motion_mean.setText(f"Mean  {mean or '— (needs 2 points)'}")
        self._motion_now.setText(f"Now   {now or '— (outside the track)'}")

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)
