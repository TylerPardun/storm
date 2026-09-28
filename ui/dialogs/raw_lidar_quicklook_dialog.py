"""Viewer for raw scanning-lidar files (archive/fetchers/raw_lidar_archive_fetcher.py
RawLidarRays) -- the scans that can't go on the map, shown with their times:

  RHI cross-section   the RHI at the archive clock's time: distance along
                      the ground vs height, titled with the scan's start/end
                      time and azimuth; follows the clock, and its step
                      buttons move the clock from RHI to RHI
  Time-height         the file's stares (e.g. vertical), height vs time, with
                      a line at the clock's time
  All rays            every ray against slant range, for anything else

Scans come from core/lidar_scans.py. A dark-themed window with a matplotlib
canvas and PNG export, modeled on VADDialog (ui/dialogs/vad_dialog.py)."""

import logging
from datetime import datetime, timezone

import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSizePolicy, QToolButton,
)
from PyQt6.QtCore import Qt, pyqtSignal
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


def _with_gaps(epochs, values):
    """Times and values with a blank column on each side of every gap
    (longer than a minute and five typical ray spacings), so a pause between
    stare periods shows as a gap instead of the neighboring rays stretched
    across it."""
    epochs = np.asarray(epochs, dtype=float)
    values = np.ma.filled(values, np.nan).astype(float)
    if epochs.size < 3:
        return epochs, values
    step = float(np.median(np.diff(epochs)))
    gaps = np.flatnonzero(np.diff(epochs) > max(60.0, 5 * step))
    if not gaps.size:
        return epochs, values
    blank = np.full((1, values.shape[1]), np.nan)
    times, rows, start = [], [], 0
    for g in gaps:
        times += [epochs[start:g + 1], [epochs[g] + step, epochs[g + 1] - step]]
        rows += [values[start:g + 1], blank, blank]
        start = g + 1
    times.append(epochs[start:])
    rows.append(values[start:])
    return np.concatenate(times), np.vstack(rows)


class RawLidarQuicklookDialog(QDialog):
    """step_requested(int): move the archive clock to the previous (-1) or
    next (+1) RHI (MainWindow does it and calls set_time)."""

    step_requested = pyqtSignal(int)

    VIEWS = ("RHI cross-section", "Time-height (stares)", "All rays vs range")

    def __init__(self, platform_id: str, parent=None, preloaded_rays=None, title: str = "", when=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.platform_id = platform_id
        self._rays = preloaded_rays
        self._scans = []
        self._when = when
        self._title = title or platform_id

        self.setWindowTitle(f"Lidar viewer — {self._title}")
        self.setMinimumSize(640, 480)
        self.resize(820, 580)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
        )

        self._build_ui()
        if self._rays is not None:
            self.set_rays(self._rays)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(5)

        header = QHBoxLayout()
        self._header_label = QLabel(self._title)
        self._header_label.setStyleSheet(f"color: {_TEXT}; font-size: 11px; font-weight: 700;")
        header.addWidget(self._header_label, stretch=1)

        self._view_combo = QComboBox()
        self._view_combo.currentIndexChanged.connect(lambda _i: self._redraw())
        header.addWidget(self._view_combo)
        self._btn_prev = QToolButton()
        self._btn_prev.setText("⏮")
        self._btn_prev.setToolTip("Previous RHI (moves the clock)")
        self._btn_prev.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_prev.clicked.connect(lambda: self.step_requested.emit(-1))
        self._btn_next = QToolButton()
        self._btn_next.setText("⏭")
        self._btn_next.setToolTip("Next RHI (moves the clock)")
        self._btn_next.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_next.clicked.connect(lambda: self.step_requested.emit(1))
        header.addWidget(self._btn_prev)
        header.addWidget(self._btn_next)

        header.addWidget(QLabel("Field"))
        self._field_combo = QComboBox()
        self._field_combo.currentTextChanged.connect(lambda _t: self._redraw())
        header.addWidget(self._field_combo)

        self._btn_save_png = QToolButton()
        self._btn_save_png.setText("SAVE")
        self._btn_save_png.setToolTip("Save PNG")
        self._btn_save_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_save_png.clicked.connect(lambda: save_widget_png(self, "storm_lidar", "Save Lidar PNG"))
        self._btn_copy_png = QToolButton()
        self._btn_copy_png.setText("COPY")
        self._btn_copy_png.setToolTip("Copy PNG")
        self._btn_copy_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_copy_png.clicked.connect(lambda: copy_widget_png(self, "storm_lidar"))
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

    # ---- data in ----------------------------------------------------------
    def set_rays(self, rays, title: str | None = None) -> None:
        """Show a (new) file; the view list offers what it holds."""
        from core.lidar_scans import classify_scans
        self._rays = rays
        self._scans = getattr(rays, "scans", None) or classify_scans(rays)
        if title:
            self._title = title
            self.setWindowTitle(f"Lidar viewer — {title}")
            self._header_label.setText(title)
        kinds = {s.kind for s in self._scans}
        views = [v for v, kind in zip(self.VIEWS, ("RHI", "Stare", None)) if kind is None or kind in kinds]
        current = self._view_combo.currentText()
        self._view_combo.blockSignals(True)
        self._view_combo.clear()
        self._view_combo.addItems(views)
        self._view_combo.setCurrentIndex(max(0, views.index(current) if current in views else 0))
        self._view_combo.blockSignals(False)
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        names = list(rays.fields)
        self._field_combo.addItems(names)
        if "velocity" in names:
            self._field_combo.setCurrentText("velocity")
        self._field_combo.blockSignals(False)
        self._redraw()

    def set_time(self, when) -> None:
        """The archive clock moved: follow it (RHI at that time; time line)."""
        self._when = when
        if self.isVisible():
            self._redraw()

    # ---- drawing ------------------------------------------------------------
    def _style(self, field_name, data):
        from matplotlib import colormaps
        if "vel" in field_name.lower():
            from ui.map.radar_overlay import NWS_VEL_CMAP
            return NWS_VEL_CMAP, -30.0, 30.0
        finite = np.ma.compressed(np.ma.masked_invalid(data))
        lo, hi = np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0)
        return colormaps["turbo"], float(lo), float(hi if hi > lo else lo + 1)

    def _finish(self, ax, mesh, field_name, units):
        cbar = self._fig.colorbar(mesh, ax=ax)
        cbar.ax.tick_params(colors=_TEXT, labelsize=8)
        cbar.set_label(f"{field_name} ({units})" if units else field_name, color=_TEXT, fontsize=9)
        ax.tick_params(colors=_TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(_BORDER)

    def _message(self, ax, text):
        ax.text(0.5, 0.5, text, color=_MUTED, ha="center", va="center", transform=ax.transAxes)
        self._canvas.draw_idle()

    def _redraw(self) -> None:
        self._fig.clear()
        ax = self._fig.add_subplot(111, facecolor=_AX_BG)
        rays, field_name, view = self._rays, self._field_combo.currentText(), self._view_combo.currentText()
        rhi = view == self.VIEWS[0]
        self._btn_prev.setVisible(rhi)
        self._btn_next.setVisible(rhi)
        if rays is None or not field_name or rays.time_epoch.size == 0:
            self._status_label.setText("")
            return self._message(ax, "No data")
        data = rays.fields[field_name]["data"]
        units = rays.fields[field_name].get("units", "")
        cmap, lo, hi = self._style(field_name, data)
        if rhi:
            self._draw_rhi(ax, data, field_name, units, cmap, lo, hi)
        elif view == self.VIEWS[1]:
            self._draw_stares(ax, data, field_name, units, cmap, lo, hi)
        else:
            self._draw_all(ax, data, field_name, units, cmap, lo, hi)
        self._fig.tight_layout()
        self._canvas.draw_idle()

    def _draw_rhi(self, ax, data, field_name, units, cmap, lo, hi):
        from core.lidar_scans import scan_at
        rhis = [s for s in self._scans if s.kind == "RHI"]
        scan = scan_at(rhis, self._when, kinds=("RHI",), hold_s=24 * 3600) if self._when is not None else None
        if scan is None and rhis:
            scan = rhis[0] if self._when is None or self._when < rhis[0].start else rhis[-1]
        if scan is None:
            self._status_label.setText("")
            return self._message(ax, "No RHI scans in this file")
        number = rhis.index(scan) + 1
        self._btn_prev.setEnabled(number > 1)
        self._btn_next.setEnabled(number < len(rhis))
        rays, idx = self._rays, scan.indices
        order = np.argsort(rays.elevation_deg[idx])
        idx = idx[order]
        elevation = np.deg2rad(rays.elevation_deg[idx])
        distance = np.asarray(rays.distance_m, dtype=float)
        # cell edges in elevation and range, so each gate is drawn where it was measured
        el_edges = np.concatenate(([elevation[0] - (elevation[1] - elevation[0]) / 2] if elevation.size > 1 else [elevation[0]],
                                   (elevation[1:] + elevation[:-1]) / 2,
                                   [elevation[-1] + (elevation[-1] - elevation[-2]) / 2] if elevation.size > 1 else [elevation[-1]]))
        steps = np.diff(distance)
        r_edges = np.concatenate(([max(0.0, distance[0] - steps[0] / 2)], (distance[1:] + distance[:-1]) / 2,
                                  [distance[-1] + steps[-1] / 2]))
        R, E = np.meshgrid(r_edges, el_edges)
        x, y = R * np.cos(E) / 1000.0, R * np.sin(E) / 1000.0
        values = np.ma.masked_invalid(np.ma.filled(data[idx], np.nan))
        mesh = ax.pcolormesh(x, y, values, cmap=cmap, vmin=lo, vmax=hi, shading="flat")
        self._finish(ax, mesh, field_name, units)
        ax.set_xlabel("Distance along the ground (km)", color=_TEXT, fontsize=9)
        ax.set_ylabel("Height above the lidar (km)", color=_TEXT, fontsize=9)
        ax.set_xlim(0, min(float(np.nanmax(x)), 6.0))
        ax.set_ylim(0, min(float(np.nanmax(y)), 3.0))
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(f"RHI {number} of {len(rhis)}  ·  {scan.start:%Y-%m-%d %H:%M:%S}–{scan.end:%H:%M:%S} UTC\n"
                     f"{scan.azimuth_text()}  ·  elevation {scan.elevation_span[0]:.0f}–{scan.elevation_span[1]:.0f}°",
                     color=_TEXT, fontsize=10)
        self._status_label.setText(f"{scan.rays} rays · the scan at or before the archive clock "
                                   f"({self._when:%H:%M:%S} UTC)" if self._when is not None else f"{scan.rays} rays")

    def _draw_stares(self, ax, data, field_name, units, cmap, lo, hi):
        rays = self._rays
        idx = np.concatenate([s.indices for s in self._scans if s.kind == "Stare"])
        idx.sort()
        epochs, values = _with_gaps(rays.time_epoch[idx], data[idx])
        times = [datetime.fromtimestamp(float(t), timezone.utc) for t in epochs]
        elevation = float(np.median(rays.elevation_deg[idx]))
        height = np.asarray(rays.distance_m, dtype=float) * np.sin(np.deg2rad(elevation))
        mesh = ax.pcolormesh(times, height / 1000.0, values.T,
                             shading="nearest", cmap=cmap, vmin=lo, vmax=hi)
        self._finish(ax, mesh, field_name, units)
        if self._when is not None:
            ax.axvline(self._when, color=_STORM_ACCENT, lw=1.2)
        ax.set_ylabel("Height above the lidar (km)", color=_TEXT, fontsize=9)
        ax.set_xlabel("Time (UTC)", color=_TEXT, fontsize=9)
        ax.set_ylim(0, 6)   # boundary-layer focus; raw lidar can report well above this
        ax.set_title(f"Stares at {elevation:.0f}° elevation  ·  {times[0]:%Y-%m-%d %H:%M}–{times[-1]:%H:%M} UTC",
                     color=_TEXT, fontsize=10)
        self._fig.autofmt_xdate()
        self._status_label.setText(f"{idx.size} rays · line: archive clock"
                                   + (f" ({self._when:%H:%M:%S} UTC)" if self._when is not None else ""))

    def _draw_all(self, ax, data, field_name, units, cmap, lo, hi):
        rays = self._rays
        epochs, values = _with_gaps(rays.time_epoch, data)
        times = [datetime.fromtimestamp(float(t), timezone.utc) for t in epochs]
        mesh = ax.pcolormesh(times, np.asarray(rays.distance_m) / 1000.0, values.T,
                             shading="nearest", cmap=cmap, vmin=lo, vmax=hi)
        self._finish(ax, mesh, field_name, units)
        if self._when is not None:
            ax.axvline(self._when, color=_STORM_ACCENT, lw=1.2)
        ax.set_ylabel(f"{rays.distance_kind} along the beam (km)", color=_TEXT, fontsize=9)
        ax.set_xlabel("Time (UTC)", color=_TEXT, fontsize=9)
        ax.set_ylim(0, 6)
        ax.set_title(f"All rays  ·  {times[0]:%Y-%m-%d %H:%M}–{times[-1]:%H:%M} UTC  ·  mixed pointing, so "
                     f"range is along each beam", color=_TEXT, fontsize=10)
        self._fig.autofmt_xdate()
        summary = rays.summary()
        self._status_label.setText(f"{summary['rays']} rays, {summary['start']} → {summary['end']}")
