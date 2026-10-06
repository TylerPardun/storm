
import logging

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QHBoxLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QColor

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from core.observation import Observation

log = logging.getLogger(__name__)

_FIG_BG     = "#0a0a0f"
_AX_BG      = "#0f0f1a"
_BORDER     = "#2a2a40"
_TEXT       = "#e8eaf0"
_MUTED      = "#666688"
_CURSOR_CLR = "#ffffff"

_LEGEND_QSS = (
    "background-color: transparent; "
    "font-size: 10px; font-family: monospace; font-weight: 600;"
)

# semantic variable colors (used on plot lines/scatter)
_CLR_TEMP = "#ff5555"   # red
_CLR_DEWP = "#55cc66"   # green
_CLR_WSPD = "#4fc3f7"   # sky blue
_CLR_WDIR = "#ffa040"   # orange
_CLR_PRES = "#ce93d8"   # lavender

# per-slot styling — max 3 simultaneous vehicles
_MAX_VEHICLES     = 3
_SLOT_LINESTYLES  = ['-', '--', ':']
_SLOT_MARKERS     = ['o', '^', 's']    # wdir scatter marker per slot
_SLOT_GLYPHS      = ['━━', '╌╌', '┈┈'] # legend line-style indicator per slot

# per-vehicle palette used only for toggle buttons and legend vehicle-id text
_VEHICLE_PALETTE = [
    "#ff6b6b",  # coral red
    "#4fc3f7",  # sky blue
    "#81c784",  # soft green
    "#ffd54f",  # amber
    "#ce93d8",  # lavender
    "#ff8a65",  # peach/orange
    "#4db6ac",  # teal
    "#f06292",  # pink
    "#aed581",  # lime
    "#4dd0e1",  # cyan
]

_GAP_S = 30  # NaN gap-break threshold (seconds)


def _force_bg(widget):
    bg  = QColor(_FIG_BG)
    txt = QColor(_TEXT)
    pal = widget.palette()
    for role in (
        QPalette.ColorRole.Window, QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
        QPalette.ColorRole.Midlight, QPalette.ColorRole.Light,
        QPalette.ColorRole.Mid, QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
    ):
        pal.setColor(role, bg)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Text, QPalette.ColorRole.BrightText,
    ):
        pal.setColor(role, txt)
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


def _build_arrays(obs_list: list) -> tuple:
    """Convert an observation list to numpy arrays with NaN gap-breaks.

    Returns (times, temp_f, dewp_f, wspd_kt, wdir_deg, pres_mb, idx_to_obs).
    idx_to_obs maps plot-array index → original Observation (NaN rows omitted).
    """
    from datetime import timedelta

    times_l, temp_l, dewp_l, wspd_l, wdir_l, pres_l = [], [], [], [], [], []
    idx_to_obs: dict[int, Observation] = {}
    prev_ts = None

    for o in obs_list:
        if prev_ts is not None:
            gap = (o.timestamp - prev_ts).total_seconds()
            if gap > _GAP_S:
                times_l.append(prev_ts + timedelta(seconds=1))
                temp_l.append(np.nan); dewp_l.append(np.nan)
                wspd_l.append(np.nan); wdir_l.append(np.nan)
                pres_l.append(np.nan)

        idx = len(times_l)
        idx_to_obs[idx] = o
        times_l.append(o.timestamp)
        temp_l.append(o.temperature_c * 9/5 + 32
                      if o.temperature_c is not None else np.nan)
        dewp_l.append(o.dewpoint_c * 9/5 + 32
                      if o.dewpoint_c is not None else np.nan)
        wspd_l.append(o.wind_speed_ms * 1.94384
                      if o.wind_speed_ms is not None else np.nan)
        wdir_l.append(o.wind_dir_deg
                      if o.wind_dir_deg is not None else np.nan)
        pres_l.append(o.pressure_mb
                      if o.pressure_mb is not None else np.nan)
        prev_ts = o.timestamp

    return (
        np.array(times_l),
        np.array(temp_l), np.array(dewp_l),
        np.array(wspd_l), np.array(wdir_l),
        np.array(pres_l), idx_to_obs,
    )


class VehicleTimeseriesDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Vehicle Timeseries")
        self.setMinimumSize(800, 600)
        self.resize(1100, 740)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
        )

        self._vehicles: dict[str, list[Observation]] = {}
        self._enabled_vehicles: set[str] = set()
        self._vehicle_colors: dict[str, str] = {}   # button/legend id colors only
        self._vehicle_slots: dict[str, int] = {}    # vid → slot index (0-2)
        self._free_slots: list[int] = [0, 1, 2]
        self._vehicle_toggles: dict[str, QPushButton] = {}
        self._vehicle_arrays: dict[str, tuple] = {}
        self._primary_id: str = ""
        self._snap_vid: str = ""
        self._cursor_vlines: list = []
        self._zoom_start = None
        self._zoom_rect_patches: list = []

        self._build_ui()


    def load(self, primary_id: str, all_vehicles: dict[str, list[Observation]]):
        """Load all vehicle data. primary_id is pre-selected; others start off."""
        self._vehicles = dict(all_vehicles)
        self._primary_id = primary_id
        self._enabled_vehicles = {primary_id}
        self._vehicle_slots = {}
        self._free_slots = [0, 1, 2]
        self._assign_colors()
        self._vehicle_slots[primary_id] = self._free_slots.pop(0)
        self._rebuild_toggle_row()
        self._draw()
        if not self.isVisible():
            self.show()
        self.raise_()

    def update_vehicle(self, vehicle_id: str, observations: list[Observation]):
        """Update one vehicle's data; redraws only if that vehicle is enabled."""
        self._vehicles[vehicle_id] = observations
        if vehicle_id not in self._vehicle_colors:
            self._assign_colors()
            self._rebuild_toggle_row()
        if vehicle_id in self._enabled_vehicles:
            self._draw()


    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(4)

        # header
        self._header_label = QLabel("")
        self._header_label.setStyleSheet(
            f"background-color: transparent; color: {_TEXT}; "
            "font-size: 11px; font-weight: bold;"
        )
        root.addWidget(self._header_label)

        # vehicle toggle row
        self._toggle_scroll = QScrollArea()
        self._toggle_scroll.setFixedHeight(36)
        self._toggle_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._toggle_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._toggle_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._toggle_scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {_FIG_BG}; border: none; }}"
            f"QWidget     {{ background-color: {_FIG_BG}; }}"
        )
        self._toggle_container = QWidget()
        self._toggle_container.setStyleSheet(f"background-color: {_FIG_BG};")
        self._toggle_layout = QHBoxLayout(self._toggle_container)
        self._toggle_layout.setContentsMargins(0, 2, 0, 2)
        self._toggle_layout.setSpacing(6)
        self._toggle_layout.addStretch()
        self._toggle_scroll.setWidget(self._toggle_container)
        self._toggle_scroll.setWidgetResizable(True)
        root.addWidget(self._toggle_scroll)

        # matplotlib canvas
        self._fig    = Figure(facecolor=_FIG_BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setAutoFillBackground(False)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._canvas.mpl_connect("motion_notify_event",  self._on_mouse_move)
        self._canvas.mpl_connect("axes_leave_event",     self._on_axes_leave)
        self._canvas.mpl_connect("scroll_event",         self._on_scroll)
        self._canvas.mpl_connect("button_press_event",   self._on_button_press)
        self._canvas.mpl_connect("button_release_event", self._on_button_release)
        root.addWidget(self._canvas, stretch=1)

        # qlabel overlays (cursor readout, positioned over the canvas gaps)
        self._leg_temp_label = QLabel(self._canvas)
        self._leg_temp_label.setStyleSheet(_LEGEND_QSS)
        self._leg_temp_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._leg_wind_label = QLabel(self._canvas)
        self._leg_wind_label.setStyleSheet(_LEGEND_QSS)
        self._leg_wind_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._leg_pres_label = QLabel(self._canvas)
        self._leg_pres_label.setStyleSheet(_LEGEND_QSS)
        self._leg_pres_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._legend_labels = [
            self._leg_temp_label, self._leg_wind_label, self._leg_pres_label]
        for lbl in self._legend_labels:
            lbl.hide()

        self._canvas.mpl_connect(
            "resize_event", lambda _: self._position_legend_labels())


    def _assign_colors(self):
        """Assign palette colors in stable first-seen order."""
        for vid in self._vehicles:
            if vid not in self._vehicle_colors:
                idx = len(self._vehicle_colors)
                self._vehicle_colors[vid] = (
                    _VEHICLE_PALETTE[idx % len(_VEHICLE_PALETTE)])

    def _rebuild_toggle_row(self):
        """Rebuild vehicle pill buttons from current vehicle set."""
        for btn in list(self._vehicle_toggles.values()):
            self._toggle_layout.removeWidget(btn)
            btn.deleteLater()
        self._vehicle_toggles.clear()

        for vid in self._vehicles:
            color = self._vehicle_colors[vid]
            btn = QPushButton(vid)
            btn.setCheckable(True)
            btn.setChecked(vid in self._enabled_vehicles)
            btn.setFixedHeight(26)
            btn.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self._apply_toggle_style(btn, color, btn.isChecked())
            btn.toggled.connect(
                lambda checked, v=vid, b=btn:
                self._on_vehicle_toggled(v, b, checked))
            # insert before the trailing stretch
            self._toggle_layout.insertWidget(
                self._toggle_layout.count() - 1, btn)
            self._vehicle_toggles[vid] = btn

        self._update_toggle_disabled_states()

    def _apply_toggle_style(self, btn: QPushButton, color: str, checked: bool):
        if checked:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: #0a0a0f; "
                f"border: 1px solid {color}; border-radius: 12px; "
                f"padding: 2px 12px; font-size: 11px; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: {color}dd; }}"
                f"QPushButton:disabled {{ background-color: {color}55; "
                f"color: #666688; border-color: transparent; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {color}; "
                f"border: 1px solid {color}55; border-radius: 12px; "
                f"padding: 2px 12px; font-size: 11px; font-weight: bold; }}"
                f"QPushButton:hover {{ border-color: {color}; "
                f"background-color: {color}22; }}"
                f"QPushButton:disabled {{ color: #333355; "
                f"border-color: #222233; }}"
            )

    def _on_vehicle_toggled(self, vid: str, btn: QPushButton, checked: bool):
        if checked:
            if len(self._enabled_vehicles) >= _MAX_VEHICLES or not self._free_slots:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
                return
            self._vehicle_slots[vid] = self._free_slots.pop(0)
            self._enabled_vehicles.add(vid)
        else:
            if vid in self._vehicle_slots:
                self._free_slots.append(self._vehicle_slots.pop(vid))
                self._free_slots.sort()
            self._enabled_vehicles.discard(vid)
        self._apply_toggle_style(btn, self._vehicle_colors[vid], checked)
        self._update_toggle_disabled_states()
        self._draw()

    def _update_toggle_disabled_states(self):
        at_cap = len(self._enabled_vehicles) >= _MAX_VEHICLES
        for vid, btn in self._vehicle_toggles.items():
            if vid not in self._enabled_vehicles:
                btn.setEnabled(not at_cap)


    def _draw(self):
        enabled = [v for v in self._vehicles
                   if v in self._enabled_vehicles and self._vehicles[v]]
        if not enabled:
            return

        # build per-vehicle arrays
        self._vehicle_arrays = {
            vid: _build_arrays(self._vehicles[vid]) for vid in enabled
        }

        # cursor snap anchor: prefer primary vehicle if it's enabled
        self._snap_vid = (
            self._primary_id
            if self._primary_id in self._vehicle_arrays
            else enabled[0]
        )
        self._plot_times = self._vehicle_arrays[self._snap_vid][0]

        # header
        snap_times = self._plot_times
        n_obs = len(self._vehicles.get(self._snap_vid, []))
        time_range = (
            f"{snap_times[0].strftime('%H:%M')} – "
            f"{snap_times[-1].strftime('%H:%M UTC')}"
        ) if len(snap_times) else ""
        extras = f"  ·  +{len(enabled)-1} more" if len(enabled) > 1 else ""
        self._header_label.setText(
            f"{self._snap_vid}  ·  {n_obs} obs  ·  {time_range}{extras}")

        self._fig.clear()
        self._cursor_vlines = []

        from matplotlib.gridspec import GridSpec
        gs = GridSpec(3, 1, figure=self._fig,
                      hspace=0.35, left=0.08, right=0.92,
                      top=0.96, bottom=0.12)

        ax1 = self._fig.add_subplot(gs[0])
        ax2 = self._fig.add_subplot(gs[1], sharex=ax1)
        ax3 = self._fig.add_subplot(gs[2], sharex=ax1)
        ax2_right = ax2.twinx()

        for ax in (ax1, ax2, ax3):
            ax.set_facecolor(_AX_BG)
            for sp in ax.spines.values():
                sp.set_edgecolor(_BORDER)
            ax.tick_params(colors=_MUTED, labelsize=8)
            ax.grid(True, color=_BORDER, alpha=0.45, linewidth=0.5)

        ax2_right.tick_params(colors=_MUTED, labelsize=8)
        for sp in ax2_right.spines.values():
            sp.set_edgecolor(_BORDER)

        # slightly heavier lines when only one vehicle for readability
        lw = 1.4 if len(enabled) == 1 else 1.2
        ms = 3.5 if len(enabled) == 1 else 2.5

        t_min = t_max = None

        for vid in enabled:
            times, temp_f, dewp_f, wspd_kt, wdir_deg, pres_mb, _ = (
                self._vehicle_arrays[vid])
            slot = self._vehicle_slots[vid]
            ls   = _SLOT_LINESTYLES[slot]
            mkr  = _SLOT_MARKERS[slot]

            # panel 1 — Temp (red), Dewpoint (green); line style = vehicle slot
            ax1.plot(times, temp_f, color=_CLR_TEMP, linewidth=lw,
                     linestyle=ls, marker='.', markersize=ms, zorder=3)
            ax1.plot(times, dewp_f, color=_CLR_DEWP, linewidth=lw * 0.85,
                     linestyle=ls, marker='.', markersize=ms * 0.8,
                     alpha=0.85, zorder=3)

            # panel 2 — Wind speed (blue); wdir scatter (orange, marker = slot)
            ax2.plot(times, wspd_kt, color=_CLR_WSPD, linewidth=lw,
                     linestyle=ls, marker='.', markersize=ms, zorder=3)
            ax2_right.scatter(times, wdir_deg,
                              color=_CLR_WDIR, marker=mkr,
                              s=8, alpha=0.75, zorder=3)

            # panel 3 — Pressure (lavender); line style = vehicle slot
            ax3.plot(times, pres_mb, color=_CLR_PRES, linewidth=lw,
                     linestyle=ls, marker='.', markersize=ms, zorder=3)

            if len(times):
                if t_min is None or times[0] < t_min:
                    t_min = times[0]
                if t_max is None or times[-1] > t_max:
                    t_max = times[-1]

        # axis labels
        ax1.set_ylabel('°F', color=_MUTED, fontsize=9)
        ax1.tick_params(labelbottom=False)

        ax2.set_ylabel('kt', color=_MUTED, fontsize=9)
        ax2.tick_params(labelbottom=False)
        ax2_right.set_ylabel('deg', color=_MUTED, fontsize=9)
        ax2_right.set_ylim(0, 360)
        ax2_right.set_yticks([0, 90, 180, 270, 360])

        ax3.set_ylabel('mb', color=_MUTED, fontsize=9)
        ax3.set_xlabel("Time (UTC)", color=_MUTED, fontsize=8)

        if t_min is not None:
            for ax in (ax1, ax2, ax3):
                ax.set_xlim(t_min, t_max)

        import matplotlib.dates as mdates
        tz = self._plot_times[0].tzinfo if len(self._plot_times) else None
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
        ax3.tick_params(axis="x", rotation=0)

        self._ax1 = ax1
        self._ax2 = ax2
        self._ax2_right = ax2_right
        self._ax3 = ax3

        self._canvas.draw_idle()
        self._set_legend_defaults()
        QTimer.singleShot(0, self._position_legend_labels)


    def _position_legend_labels(self):
        if not hasattr(self, '_ax1'):
            return

        canvas_h = self._canvas.height()
        canvas_w = self._canvas.width()

        for ax_above, ax_below, lbl in (
            (self._ax1, self._ax2, self._leg_temp_label),
            (self._ax2, self._ax3, self._leg_wind_label),
        ):
            bb_a = ax_above.get_position()
            bb_b = ax_below.get_position()
            px_x  = int(bb_a.x0 * canvas_w)
            lbl_w = int((bb_a.x1 - bb_a.x0) * canvas_w)
            gap_top = canvas_h - bb_a.y0 * canvas_h
            gap_bot = canvas_h - bb_b.y1 * canvas_h
            px_y  = int((gap_top + gap_bot) / 2 - 8)
            lbl.move(px_x, px_y)
            lbl.resize(lbl_w, 18)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.show()

        bb   = self._ax3.get_position()
        px_x  = int(bb.x0 * canvas_w)
        lbl_w = int((bb.x1 - bb.x0) * canvas_w)
        self._leg_pres_label.move(px_x, int(canvas_h - 22))
        self._leg_pres_label.resize(lbl_w, 18)
        self._leg_pres_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._leg_pres_label.show()

    def _legend_html(self, field: str, values: dict | None = None) -> str:
        """Build color-coded HTML for one legend label.

        field : 'temp' | 'wind' | 'pres'
        values: None → show defaults ("--")
                'temp' → {vid: (temp_str, dewp_str)}
                'wind' → {vid: (spd_str, dir_str)}
                'pres' → {vid: pres_str}
        """
        parts = []
        for vid in self._vehicle_arrays:
            btn_color = self._vehicle_colors.get(vid, _MUTED)
            slot      = self._vehicle_slots.get(vid, 0)
            glyph     = _SLOT_GLYPHS[slot]
            vid_html  = (
                f'<span style="color:{btn_color};">{glyph} {vid}</span>'
            )

            if field == 'temp':
                tv, tdv = (values or {}).get(vid, ("--", "--"))
                tc  = _CLR_TEMP if values else _MUTED
                tdc = _CLR_DEWP if values else _MUTED
                parts.append(
                    f'{vid_html}'
                    f'<span style="color:{tc};"> T:{tv}</span>'
                    f'<span style="color:{tdc};"> Td:{tdv}</span>'
                )
            elif field == 'wind':
                wsv, wdv = (values or {}).get(vid, ("--", "--"))
                wsc = _CLR_WSPD if values else _MUTED
                wdc = _CLR_WDIR if values else _MUTED
                parts.append(
                    f'{vid_html}'
                    f'<span style="color:{wsc};"> {wsv}</span>'
                    f'<span style="color:{wdc};"> ● {wdv}</span>'
                )
            elif field == 'pres':
                pv  = (values or {}).get(vid, "--")
                pc  = _CLR_PRES if values else _MUTED
                parts.append(
                    f'{vid_html}'
                    f'<span style="color:{pc};"> {pv}</span>'
                )
        return '&nbsp;&nbsp;&nbsp;'.join(parts)

    def _set_legend_defaults(self):
        self._leg_temp_label.setText(self._legend_html('temp'))
        self._leg_wind_label.setText(self._legend_html('wind'))
        self._leg_pres_label.setText(self._legend_html('pres'))


    def _on_mouse_move(self, event):
        if not hasattr(self, '_plot_times') or not self._vehicle_arrays:
            return

        if self._zoom_start is not None and event.xdata is not None:
            self._update_zoom_rect(event.xdata)
            return

        if event.inaxes not in (
            self._ax1, self._ax2, self._ax2_right, self._ax3
        ):
            return

        from matplotlib import dates as mdates
        mouse_time = mdates.num2date(event.xdata)

        # snap vline to primary vehicle's grid
        snap_times = self._vehicle_arrays[self._snap_vid][0]
        diffs = [abs((t - mouse_time).total_seconds()) for t in snap_times]
        snap_idx  = int(np.argmin(diffs))
        snap_time = snap_times[snap_idx]

        # per-vehicle readout values at cursor
        temp_vals: dict[str, tuple] = {}
        wind_vals: dict[str, tuple] = {}
        pres_vals: dict[str, str]   = {}
        ts_str = None

        for vid, arrays in self._vehicle_arrays.items():
            times, _, _, _, _, _, idx_to_obs = arrays
            if not len(times):
                continue
            vdiffs = [abs((t - mouse_time).total_seconds()) for t in times]
            idx = int(np.argmin(vdiffs))
            if idx not in idx_to_obs:
                continue
            obs = idx_to_obs[idx]
            if ts_str is None:
                ts_str = obs.timestamp.strftime('%H:%M:%S UTC')

            t_v  = (f"{obs.temperature_c * 9/5 + 32:.0f}°F"
                    if obs.temperature_c is not None else "--")
            td_v = (f"{obs.dewpoint_c * 9/5 + 32:.0f}°F"
                    if obs.dewpoint_c is not None else "--")
            ws_v = (f"{obs.wind_speed_ms * 1.94384:.0f}kt"
                    if obs.wind_speed_ms is not None else "--")
            wd_v = (f"{obs.wind_dir_deg:.0f}°"
                    if obs.wind_dir_deg is not None else "--")
            p_v  = (f"{obs.pressure_mb:.1f}mb"
                    if obs.pressure_mb is not None else "--")

            temp_vals[vid] = (t_v, td_v)
            wind_vals[vid] = (ws_v, wd_v)
            pres_vals[vid] = p_v

        ts_prefix = (
            f'<span style="color:{_MUTED};">{ts_str}</span>&nbsp;&nbsp;'
            if ts_str else ""
        )
        self._leg_temp_label.setText(
            ts_prefix + self._legend_html('temp', temp_vals))
        self._leg_wind_label.setText(
            self._legend_html('wind', wind_vals))
        self._leg_pres_label.setText(
            self._legend_html('pres', pres_vals))

        for vl in self._cursor_vlines:
            vl.remove()
        self._cursor_vlines.clear()
        for ax in (self._ax1, self._ax2, self._ax3):
            vl = ax.axvline(snap_time, color=_CURSOR_CLR,
                            linewidth=0.9, alpha=0.4, zorder=10)
            self._cursor_vlines.append(vl)
        self._canvas.draw_idle()

    def _clear_cursor(self):
        for vl in self._cursor_vlines:
            vl.remove()
        self._cursor_vlines.clear()
        self._set_legend_defaults()
        self._canvas.draw_idle()

    def _on_axes_leave(self, event):
        if self._zoom_start is not None:
            return
        self._clear_cursor()


    def _data_bounds(self):
        """Return (data_min_num, data_max_num) across all enabled vehicles."""
        import matplotlib.dates as mdates
        data_min = min(
            mdates.date2num(a[0][0])
            for a in self._vehicle_arrays.values() if len(a[0])
        )
        data_max = max(
            mdates.date2num(a[0][-1])
            for a in self._vehicle_arrays.values() if len(a[0])
        )
        return data_min, data_max

    def _on_scroll(self, event):
        if event.inaxes not in (
            self._ax1, self._ax2, self._ax2_right, self._ax3
        ):
            return
        if not self._vehicle_arrays:
            return

        xmin, xmax = self._ax1.get_xlim()
        step = getattr(event, 'step', 1)
        zoom_factor = 0.8 ** step
        cursor_x = event.xdata
        if cursor_x is None:
            return

        range_w  = xmax - xmin
        new_w    = range_w * zoom_factor
        cfrac    = (cursor_x - xmin) / range_w
        new_xmin = cursor_x - new_w * cfrac
        new_xmax = cursor_x + new_w * (1 - cfrac)

        data_min, data_max = self._data_bounds()
        new_xmin = max(new_xmin, data_min)
        new_xmax = min(new_xmax, data_max)

        for ax in (self._ax1, self._ax2, self._ax3):
            ax.set_xlim(new_xmin, new_xmax)
        self._canvas.draw_idle()

    def _on_button_press(self, event):
        if event.inaxes not in (
            self._ax1, self._ax2, self._ax2_right, self._ax3
        ):
            return
        if event.dblclick:
            if self._vehicle_arrays:
                data_min, data_max = self._data_bounds()
                for ax in (self._ax1, self._ax2, self._ax3):
                    ax.set_xlim(data_min, data_max)
                self._canvas.draw_idle()
            return
        if event.button != 1:
            return
        self._zoom_start = event.xdata

    def _on_button_release(self, event):
        if self._zoom_start is None:
            return
        if event.button != 1:
            return
        self._clear_zoom_rect()
        zoom_end = event.xdata
        if zoom_end is None or event.inaxes not in (
            self._ax1, self._ax2, self._ax2_right, self._ax3
        ):
            self._zoom_start = None
            return
        xmin, xmax = self._ax1.get_xlim()
        if abs(zoom_end - self._zoom_start) < (xmax - xmin) * 0.01:
            self._zoom_start = None
            return
        new_xmin = min(self._zoom_start, zoom_end)
        new_xmax = max(self._zoom_start, zoom_end)
        for ax in (self._ax1, self._ax2, self._ax3):
            ax.set_xlim(new_xmin, new_xmax)
        self._zoom_start = None
        self._canvas.draw_idle()

    def _update_zoom_rect(self, current_x):
        self._clear_zoom_rect()
        self._zoom_rect_patches = []
        xmin = min(self._zoom_start, current_x)
        xmax = max(self._zoom_start, current_x)
        for ax in (self._ax1, self._ax2, self._ax3):
            rect = ax.axvspan(xmin, xmax,
                              color=_CURSOR_CLR, alpha=0.12, zorder=5)
            self._zoom_rect_patches.append(rect)
        self._canvas.draw_idle()

    def _clear_zoom_rect(self):
        for p in getattr(self, '_zoom_rect_patches', []):
            p.remove()
        self._zoom_rect_patches = []
