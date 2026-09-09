"""Time-height quicklook for CLAMPS raw scanning-lidar ray products
(archive/fetchers/raw_lidar_archive_fetcher.py's RawLidarRays). Modeled on
VADDialog's shell (ui/dialogs/vad_dialog.py): a dark-themed QDialog with a
matplotlib canvas and PNG export, given a preloaded result rather than
fetching live data itself.

Deliberately never touches scan geometry (azimuth/elevation/lat/lon) --
a time-height plot needs only per-ray time and range/height, so this is
the same code path for a stationary CLAMPS site and the mobile DL Truck,
whose ground_geometry_valid is always False (truck heading/motion
correction is unverified even once GPS position is filled in)."""

import logging
from datetime import datetime, timezone

import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSizePolicy, QToolButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from config import ACCENT_COLOR as _STORM_ACCENT
from ui.export_tools import copy_widget_png, save_widget_png

log = logging.getLogger(__name__)

_FIG_BG = "#0a0a0f"
_AX_BG = "#0f0f1a"
_BORDER = "#2a2a40"
_TEXT = "#e8eaf0"
_MUTED = "#666688"

_EXPORT_BTN_QSS = f"""
QToolButton {{
    background-color: #151522;
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QToolButton:hover {{ border-color: {_STORM_ACCENT}; color: {_STORM_ACCENT}; }}
QToolButton:pressed {{ background-color: #202033; }}
"""


def _force_bg(widget):
    bg, txt = QColor(_FIG_BG), QColor(_TEXT)
    pal = widget.palette()
    for role in (
        QPalette.ColorRole.Window, QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
        QPalette.ColorRole.Midlight, QPalette.ColorRole.Light,
        QPalette.ColorRole.Mid, QPalette.ColorRole.Dark, QPalette.ColorRole.Shadow,
    ):
        pal.setColor(role, bg)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Text, QPalette.ColorRole.BrightText,
    ):
        pal.setColor(role, txt)
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


class RawLidarQuicklookDialog(QDialog):
    """Time-height quicklook for one loaded raw-lidar file."""

    def __init__(self, platform_id: str, parent=None, preloaded_rays=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.platform_id = platform_id
        self._rays = preloaded_rays

        self.setWindowTitle(f"Raw Lidar Quicklook — {platform_id}")
        self.setMinimumSize(640, 480)
        self.resize(760, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
        )

        self._build_ui()
        if self._rays is not None:
            self._populate_field_combo()
            self._redraw()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(5)

        header = QHBoxLayout()
        self._header_label = QLabel(f"Raw Lidar  ·  {self.platform_id}")
        self._header_label.setStyleSheet(f"color: {_TEXT}; font-size: 11px; font-weight: 700;")
        header.addWidget(self._header_label, stretch=1)

        header.addWidget(QLabel("Field"))
        self._field_combo = QComboBox()
        self._field_combo.currentTextChanged.connect(self._on_field_changed)
        header.addWidget(self._field_combo)

        self._btn_save_png = QToolButton()
        self._btn_save_png.setText("SAVE")
        self._btn_save_png.setToolTip("Save this quicklook as a PNG")
        self._btn_save_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_save_png.clicked.connect(lambda: save_widget_png(self, "storm_raw_lidar", "Save Raw Lidar PNG"))
        self._btn_copy_png = QToolButton()
        self._btn_copy_png.setText("COPY")
        self._btn_copy_png.setToolTip("Copy this quicklook PNG to the clipboard")
        self._btn_copy_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_copy_png.clicked.connect(lambda: copy_widget_png(self, "storm_raw_lidar"))
        header.addWidget(self._btn_save_png)
        header.addWidget(self._btn_copy_png)
        root.addLayout(header)

        self._fig = Figure(facecolor=_FIG_BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setAutoFillBackground(False)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._canvas, stretch=1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        root.addWidget(self._status_label)

    def set_rays(self, rays) -> None:
        """Load a new result into an already-open dialog (e.g. the user
        picked a different file from the controls drawer without closing
        this window)."""
        self._rays = rays
        self._populate_field_combo()
        self._redraw()

    def _populate_field_combo(self) -> None:
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        if self._rays is not None:
            for name in sorted(self._rays.fields):
                self._field_combo.addItem(name)
        self._field_combo.blockSignals(False)

    def _on_field_changed(self, _text: str) -> None:
        self._redraw()

    def _redraw(self) -> None:
        self._fig.clear()
        ax = self._fig.add_subplot(111, facecolor=_AX_BG)
        rays = self._rays
        field_name = self._field_combo.currentText()

        if rays is None or not field_name or rays.time_epoch.size == 0:
            ax.text(0.5, 0.5, "No data", color=_MUTED, ha="center", va="center", transform=ax.transAxes)
            self._canvas.draw_idle()
            self._status_label.setText("")
            return

        times = [datetime.fromtimestamp(float(t), timezone.utc) for t in rays.time_epoch]
        data = rays.fields[field_name]["data"]
        units = rays.fields[field_name].get("units", "")

        mesh = ax.pcolormesh(
            times, rays.distance_m, np.ma.filled(data, np.nan).T,
            shading="nearest", cmap="turbo",
        )
        cbar = self._fig.colorbar(mesh, ax=ax)
        cbar.ax.tick_params(colors=_TEXT, labelsize=8)
        cbar.set_label(f"{field_name} ({units})" if units else field_name, color=_TEXT, fontsize=9)

        ax.set_ylabel(f"{rays.distance_kind} (m)", color=_TEXT, fontsize=9)
        ax.set_xlabel("Time (UTC)", color=_TEXT, fontsize=9)
        ax.tick_params(colors=_TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(_BORDER)
        self._fig.autofmt_xdate()
        self._fig.tight_layout()
        self._canvas.draw_idle()

        summary = rays.summary()
        status = f"{summary['rays']} rays, {summary['start']} → {summary['end']}"
        if rays.source.mobile:
            status += " — mobile platform, ground geometry not established"
        self._status_label.setText(status)
