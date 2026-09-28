"""Floating drawer for the archive-mode subjective storm-track feature:
point count, the file being saved to, Load Track / Reset to Original,
Undo / Redo, Export As / Clear Track, and line/point visibility. Mirrors RawLidarControls'/
LandcoverControls' collapsible-drawer shell (ui/controls/raw_lidar_controls.py,
ui/controls/landcover_controls.py). No product/source pickers here -- those
live in radar_controls.py and are driven by the R/V shortcuts, not this panel.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QCheckBox, QComboBox, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


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
    workspace_selected(str)      user picked another workspace
    new_workspace_requested()    user picked "New workspace…"
    open_track_requested(str)    user opened one of this date's workspace tracks
    table_requested()            user clicked "Table…"
    open_folder_requested()      user clicked "Open Folder"
    """

    load_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    export_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    layers_changed = pyqtSignal(bool, bool)
    workspace_selected = pyqtSignal(str)
    new_workspace_requested = pyqtSignal()
    open_track_requested = pyqtSignal(str)
    table_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()

    _NEW_WORKSPACE = "New workspace…"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Two compact rows, like the other toolbar drawers: controls on the
        first, what's loaded / where it's saved / storm motion on the second."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("trackDrawer")
        # compact buttons so both rows fit within the toolbar's width
        self._drawer.setStyleSheet("QPushButton { padding: 2px 9px; min-width: 0px; font-size: 10px; }")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        small = "color: #B5BDCC; font-size: 10px;"

        def button(text, tip, signal):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedHeight(24)
            b.clicked.connect(signal.emit)
            return b

        # ---- row 1: workspace, saved tracks, editing, layers -------------
        row = QHBoxLayout()
        row.setSpacing(4)
        ws_label = QLabel("WORKSPACE")
        ws_label.setStyleSheet("color: #8E97AB; font-size: 9px; letter-spacing: 1px;")
        row.addWidget(ws_label)
        self._workspace_combo = QComboBox()
        self._workspace_combo.setFixedWidth(110)
        self._workspace_combo.setToolTip("Tracks are saved per workspace in STORM's data/storm_tracks folder")
        self._workspace_combo.activated.connect(self._on_workspace_activated)
        row.addWidget(self._workspace_combo)
        self._saved_combo = QComboBox()
        self._saved_combo.setFixedWidth(200)
        self._saved_combo.setToolTip("Tracks saved for this date, yours first; [MESO-VIEW] marks shared tracks")
        row.addWidget(self._saved_combo)
        self._btn_open_saved = QPushButton("OPEN")
        self._btn_open_saved.setToolTip("Open the selected track (a shared track opens as a copy in your workspace)")
        self._btn_open_saved.setFixedHeight(24)
        self._btn_open_saved.clicked.connect(self._on_open_saved)
        row.addWidget(self._btn_open_saved)
        row.addSpacing(8)
        self._btn_load = button("LOAD…", "Open a track file from anywhere (CSV or Excel, STORM or MESO-VIEW)",
                                self.load_requested)
        self._btn_reset = button("RESET", "Undo every edit since the track was loaded", self.reset_requested)
        self._btn_reset.setEnabled(False)
        self._btn_undo = button("UNDO", "Undo the last track edit (Ctrl+Z)", self.undo_requested)
        self._btn_redo = button("REDO", "Redo the last undone edit (Ctrl+Shift+Z)", self.redo_requested)
        self._btn_table = button("TABLE…", "Edit exact point times and positions in a table", self.table_requested)
        self._btn_export = button("EXPORT…", "Save a copy of the track as CSV or Excel", self.export_requested)
        self._btn_clear = button("CLEAR", "Start a new track (the current one stays saved)", self.clear_requested)
        for b in (self._btn_load, self._btn_reset, self._btn_undo, self._btn_redo,
                  self._btn_table, self._btn_export, self._btn_clear):
            row.addWidget(b)
        row.addStretch()
        col.addLayout(row)

        # ---- row 2: what's loaded, where it saves, storm motion ----------
        info = QHBoxLayout()
        info.setSpacing(8)
        self._count_label = QLabel("0 points")
        self._count_label.setStyleSheet(small)
        info.addWidget(self._count_label)
        self._file_label = QLabel("New track — nothing saved yet")
        self._file_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        info.addWidget(self._file_label)
        self._btn_open_folder = QPushButton("OPEN FOLDER")
        self._btn_open_folder.setToolTip("Show STORM's storm-track folder, where every track is saved")
        self._btn_open_folder.setFixedHeight(22)
        self._btn_open_folder.clicked.connect(self.open_folder_requested.emit)
        info.addWidget(self._btn_open_folder)
        info.addSpacing(8)
        motion_title = QLabel("STORM MOTION")
        motion_title.setStyleSheet("color: #8E97AB; font-size: 9px; letter-spacing: 1px;")
        info.addWidget(motion_title)
        self._motion_mean = QLabel()
        self._motion_now = QLabel()
        for label, tip in (
            (self._motion_mean, "End-to-end: first point to last, as in MESO-VIEW"),
            (self._motion_now, "The track segment at the current time"),
        ):
            label.setToolTip(tip)
            label.setStyleSheet("color: #E3E8F2; font-size: 10px;")
            info.addWidget(label)
        info.addStretch()
        self._chk_line = QCheckBox("Line")
        self._chk_line.setChecked(True)
        self._chk_points = QCheckBox("Points")
        self._chk_points.setChecked(True)
        for chk in (self._chk_line, self._chk_points):
            chk.setStyleSheet(small)
            chk.toggled.connect(self._emit_layers)
            info.addWidget(chk)
        col.addLayout(info)
        self.set_motion(None, None)
        self.set_saved_tracks([])

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        self._status_label.hide()
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

    def set_file(self, text: str, full_path: str = "") -> None:
        """Long paths are shortened in the middle; hover shows the full path."""
        metrics = self._file_label.fontMetrics()
        self._file_label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, 300))
        self._file_label.setToolTip(full_path or text)

    def set_edit_state(self, *, loaded: bool, can_undo: bool, can_redo: bool) -> None:
        self._btn_reset.setEnabled(loaded)
        self._btn_undo.setEnabled(can_undo)
        self._btn_redo.setEnabled(can_redo)

    def set_point_count(self, count: int) -> None:
        self._count_label.setText(f"{count} point" + ("" if count == 1 else "s"))

    def set_workspaces(self, names: list[str], active: str) -> None:
        self._workspace_combo.blockSignals(True)
        self._workspace_combo.clear()
        self._workspace_combo.addItems(names)
        self._workspace_combo.addItem(self._NEW_WORKSPACE)
        self._workspace_combo.setCurrentText(active)
        self._workspace_combo.blockSignals(False)

    def _on_workspace_activated(self, index: int) -> None:
        text = self._workspace_combo.itemText(index)
        if text == self._NEW_WORKSPACE:
            self.new_workspace_requested.emit()
        else:
            self.workspace_selected.emit(text)

    def set_saved_tracks(self, tracks: list[tuple[str, str]]) -> None:
        """(label, path) pairs for this date's workspace tracks."""
        self._saved_combo.clear()
        for label, path in tracks:
            self._saved_combo.addItem(label, userData=path)
        if not tracks:
            self._saved_combo.addItem("No saved tracks for this date")
        self._saved_combo.setEnabled(bool(tracks))
        self._btn_open_saved.setEnabled(bool(tracks))

    def _on_open_saved(self) -> None:
        path = self._saved_combo.currentData()
        if path:
            self.open_track_requested.emit(path)

    def set_motion(self, mean: str | None, now: str | None) -> None:
        """Motion readouts, e.g. "from 240° at 15.1 m/s (29 kt)"; None shows a dash."""
        self._motion_mean.setText(f"Mean {mean or '— (2 points needed)'}")
        self._motion_now.setText(f"   Now {now or '— (outside the track)'}")

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))
