
import base64
import hashlib
import hmac
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QToolButton, QFileDialog, QFrame,
    QApplication, QMessageBox, QSizePolicy, QWidget,
    QDateTimeEdit, QAbstractButton, QSpinBox, QComboBox, QCheckBox,
    QCalendarWidget,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QSize, QDateTime, QPointF, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPolygonF, QPen

import config as _config
from ui.launch.icons import combo_down_arrow_qss, _svg_pixmap
from ui.launch.styles import (
    _DIALOG_STYLE, _ICON_SELECTED_STYLE, _LOG_BTN_STYLE, _MODE_BTN_SELECTED_STYLE,
    _MODE_BTN_STYLE, _UPD_AVAILABLE, _UPD_CHECKING, _UPD_CURRENT, _UPD_ERROR,
    _UPD_SUCCESS, _UPD_WARNING,
)
from ui.launch.update_dialogs import _CondaUpdateDialog, _LogViewerDialog, UpdateWorker



_PBKDF2_ITERATIONS = 600_000

def _verify_pbkdf2(passphrase: str, stored: str) -> bool:
    """Verify *passphrase* against a ``base64(salt):base64(dk)`` hash."""
    try:
        salt_b64, dk_b64 = stored.split(":", 1)
        salt = base64.b64decode(salt_b64)
        expected_dk = base64.b64decode(dk_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk, expected_dk)


def _triangle_icon(pts: list, color: str, w: int = 10, h: int = 10) -> QIcon:
    """A small filled triangle from fractional (x, y) points in [0, 1],
    used for the calendar popup's prev/next-month and year-step arrows."""
    px = QPixmap(w, h)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([QPointF(x * w, y * h) for x, y in pts]))
    p.end()
    return QIcon(px)


def _calendar_glyph_icon(color: str = "#8E97AB", w: int = 16, h: int = 16) -> QIcon:
    """A simple hand-drawn calendar glyph (rounded body, header rule, two
    binder-ring ticks), matching this dialog's existing minimal line-art
    icon style rather than an emoji or bundled image asset."""
    px = QPixmap(w, h)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.2)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    body = QRectF(1.5, 3.5, w - 3.0, h - 5.0)
    p.drawRoundedRect(body, 1.5, 1.5)
    p.drawLine(QPointF(body.left(), body.top() + 3.2), QPointF(body.right(), body.top() + 3.2))
    p.drawLine(QPointF(w * 0.32, 1.0), QPointF(w * 0.32, 4.4))
    p.drawLine(QPointF(w * 0.68, 1.0), QPointF(w * 0.68, 4.4))
    p.end()
    return QIcon(px)


class LaunchDialog(QDialog):
    """
    Pre-launch configuration dialog.  Reads previous settings from
    config.toml and writes them back on confirmation so the next launch
    is pre-populated automatically.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM")
        self.setMinimumWidth(380)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_DIALOG_STYLE + combo_down_arrow_qss())

        s = QSettings()
        saved = {
            "vehicle_id":     s.value("launch/vehicle_id",     "",        type=str),
            "data_dir":       s.value("launch/data_dir",       "",        type=str),
            "gps_file_mode":  s.value("launch/gps_file_mode",  False,     type=bool),
            "mode":           s.value("launch/mode",           "vehicle", type=str),
            "vehicle_icon":   s.value("launch/vehicle_icon",   "car",     type=str),
            "auto_spc":       s.value("launch/auto_spc",       False,     type=bool),
            "auto_nws":       s.value("launch/auto_nws",       False,     type=bool),
            "auto_radar":     s.value("launch/auto_radar",     False,     type=bool),
            "auto_satellite": s.value("launch/auto_satellite", "",        type=str),
            "auto_obs_ok":       s.value("launch/auto_obs_ok",       False, type=bool),
            "auto_obs_wtm":      s.value("launch/auto_obs_wtm",      False, type=bool),
            "auto_obs_ks":       s.value("launch/auto_obs_ks",       False, type=bool),
            "auto_obs_co":       s.value("launch/auto_obs_co",       False, type=bool),
            "auto_obs_ne":       s.value("launch/auto_obs_ne",       False, type=bool),
            "radar_resolution":  s.value("launch/radar_resolution",  -1,    type=int),
        }
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._build_ui(saved)
        self._start_update_check()


    def _build_ui(self, saved: dict):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 28)
        root.setSpacing(0)

        # title
        title = QLabel("STORM")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        sub = QLabel("Severe Thunderstorm Observation and Reconnaissance Monitor")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(16)

        # divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #1E1E2E;")
        div.setFixedHeight(1)
        root.addWidget(div)
        root.addSpacing(16)

        self._vehicle_section = QWidget()
        vs = QVBoxLayout(self._vehicle_section)
        vs.setContentsMargins(0, 0, 0, 0)
        vs.setSpacing(0)

        # vehicle ID
        vid_label = QLabel("VEHICLE ID")
        vid_label.setObjectName("fieldLabel")
        vs.addWidget(vid_label)
        vs.addSpacing(6)

        vid_row = QHBoxLayout()
        vid_row.setSpacing(6)
        self._vid_input = QLineEdit(saved.get("vehicle_id", ""))
        self._vid_input.setPlaceholderText("e.g.  lid1")
        vid_row.addWidget(self._vid_input)

        self._lock_btn = QPushButton("🔒")
        self._lock_btn.setObjectName("lockBtn")
        self._lock_btn.setFixedWidth(36)
        self._lock_btn.setToolTip("Unlock both fields")
        self._lock_btn.clicked.connect(self._toggle_fields_lock)
        vid_row.addWidget(self._lock_btn)
        vs.addLayout(vid_row)
        vs.addSpacing(14)

        # data directory
        dir_label = QLabel("DATA DIRECTORY")
        dir_label.setObjectName("fieldLabel")
        vs.addWidget(dir_label)
        vs.addSpacing(6)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._dir_input = QLineEdit(saved.get("data_dir", ""))
        self._dir_input.setPlaceholderText("Leave blank for GPS puck")
        dir_row.addWidget(self._dir_input)

        self._browse_btn = QPushButton("…")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.setFixedWidth(36)
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)

        vs.addLayout(dir_row)
        vs.addSpacing(8)

        # gps data file checkbox
        self._gps_mode_check = QCheckBox("GPS data file (no met obs)")
        self._gps_mode_check.setChecked(saved.get("gps_file_mode", False))
        self._gps_mode_check.setStyleSheet(
            "QCheckBox { color: #8E97AB; font-size: 11px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #1E1E2E;"
            " border-radius: 3px; background: #1A1A2E; }"
            "QCheckBox::indicator:checked { background: #00CFFF; border-color: #00CFFF; }"
            "QCheckBox:disabled { color: #2A2A3E; }"
        )
        vs.addWidget(self._gps_mode_check)
        vs.addSpacing(14)

        # vehicle icon picker
        icon_label = QLabel("VEHICLE ICON")
        icon_label.setObjectName("fieldLabel")
        vs.addWidget(icon_label)
        vs.addSpacing(6)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(4)
        self._icon_btns: dict[str, QToolButton] = {}
        _icons = [
            ("car", "CAR"), ("drone", "DRONE"), ("mesonet", "MESO"),
            ("lidar", "LIDAR"), ("radar", "RADAR"), ("hailcam", "HAIL"),
        ]
        for key, label in _icons:
            btn = QToolButton()
            btn.setText(label)
            btn.setObjectName("iconBtn")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(22, 22))
            btn.setIcon(QIcon(_svg_pixmap(key, "#5A5B6A", 22)))
            btn.clicked.connect(lambda _checked, k=key: self._select_icon(k))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            icon_row.addWidget(btn)
            self._icon_btns[key] = btn
        vs.addLayout(icon_row)
        vs.addSpacing(12)

        root.addWidget(self._vehicle_section)

        self._set_icon_selected(saved.get("vehicle_icon", "car"))

        # single lock controls all vehicle config fields; lock when either value exists.
        self._set_fields_locked(bool(saved.get("vehicle_id") or saved.get("data_dir")))

        mode_label = QLabel("LAUNCH MODE")
        mode_label.setObjectName("fieldLabel")
        root.addWidget(mode_label)
        root.addSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, label in (("vehicle", "VEHICLE"), ("monitor", "MONITOR"), ("viewer", "VIEWER"), ("archive", "ARCHIVE")):
            btn = QPushButton(label)
            btn.setStyleSheet(_MODE_BTN_STYLE)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_mode(k))
            mode_row.addWidget(btn)
            self._mode_btns[key] = btn
        root.addLayout(mode_row)
        root.addSpacing(12)

        self._admin_check = QCheckBox("admin mode")
        self._admin_check.setChecked(False)
        self._admin_check.setStyleSheet(
            "QCheckBox { color: #C8D0DE; font-size: 11px; font-weight: 600; }"
            "QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #1E1E2E;"
            " border-radius: 3px; background: #1A1A2E; }"
            "QCheckBox::indicator:checked { background: #00CFFF; border-color: #00CFFF; }"
        )
        self._admin_check.toggled.connect(self._on_admin_toggled)
        root.addWidget(self._admin_check)
        root.addSpacing(8)

        self._admin_passphrase_row = QWidget()
        admin_pw_layout = QVBoxLayout(self._admin_passphrase_row)
        admin_pw_layout.setContentsMargins(0, 0, 0, 0)
        admin_pw_layout.setSpacing(6)
        admin_label = QLabel("ADMIN PASSPHRASE")
        admin_label.setObjectName("fieldLabel")
        admin_pw_layout.addWidget(admin_label)
        self._admin_passphrase_input = QLineEdit()
        self._admin_passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._admin_passphrase_input.setPlaceholderText("Enter admin passphrase")
        admin_pw_layout.addWidget(self._admin_passphrase_input)
        self._admin_passphrase_row.setVisible(False)
        root.addWidget(self._admin_passphrase_row)
        root.addSpacing(4)

        # passphrase row (hidden in viewer mode; shown for archive mode)
        self._passphrase_row = QWidget()
        pw_layout = QVBoxLayout(self._passphrase_row)
        pw_layout.setContentsMargins(0, 0, 0, 0)
        pw_layout.setSpacing(6)
        self._passphrase_label = QLabel("PASSPHRASE")
        self._passphrase_label.setObjectName("fieldLabel")
        pw_layout.addWidget(self._passphrase_label)
        self._passphrase_input = QLineEdit()
        self._passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._passphrase_input.setPlaceholderText("Enter passphrase")
        pw_layout.addWidget(self._passphrase_input)
        root.addWidget(self._passphrase_row)

        self._archive_section = QWidget()
        av_layout = QVBoxLayout(self._archive_section)
        av_layout.setContentsMargins(0, 10, 0, 0)
        av_layout.setSpacing(6)

        arc_lbl = QLabel("ARCHIVE START TIME (UTC)")
        arc_lbl.setObjectName("fieldLabel")
        av_layout.addWidget(arc_lbl)

        self._archive_dt_edit = QDateTimeEdit()
        self._archive_dt_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self._archive_dt_edit.setCalendarPopup(True)
        # default to yesterday at 20:00 UTC as a sensible starting point.
        now_utc = datetime.now(timezone.utc)
        yesterday = now_utc.replace(hour=20, minute=0, second=0, microsecond=0)
        self._archive_dt_edit.setDateTime(
            QDateTime(
                yesterday.year, yesterday.month, yesterday.day,
                yesterday.hour, yesterday.minute, yesterday.second,
            )
        )
        dt_row = QHBoxLayout()
        dt_row.setSpacing(6)
        dt_row.addWidget(self._archive_dt_edit, 1)
        dt_row.addWidget(self._build_calendar_button())
        av_layout.addLayout(dt_row)
        self._init_calendar_icons()

        arc_hint = QLabel("All data products will replay from this UTC time.")
        arc_hint.setObjectName("hint")
        arc_hint.setWordWrap(True)
        av_layout.addWidget(arc_hint)

        self._archive_section.setVisible(False)
        root.addWidget(self._archive_section)

        # apply saved mode
        self._select_mode(saved.get("mode", "vehicle"))

        root.addSpacing(14)
        div2 = QFrame()
        div2.setObjectName("divider")
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("background-color: #1E1E2E;")
        div2.setFixedHeight(1)
        root.addWidget(div2)
        root.addSpacing(10)

        self._data_toggle_btn = QPushButton("▸  DATA CONFIGURATION")
        self._data_toggle_btn.setObjectName("dataToggleBtn")
        self._data_toggle_btn.setFixedHeight(18)
        self._data_toggle_btn.clicked.connect(self._toggle_data_section)
        root.addWidget(self._data_toggle_btn)

        # collapsible container — hidden by default
        self._data_section = QWidget()
        ds = QVBoxLayout(self._data_section)
        ds.setContentsMargins(0, 8, 0, 0)
        ds.setSpacing(6)

        # row 1: SPC / NWS / RADAR as multi-select button strip
        self._layer_btns: dict[str, QPushButton] = {}
        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        for key, label in (("spc", "SPC"), ("nws", "NWS"), ("radar", "RADAR")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._toggle_layer(k))
            layer_row.addWidget(btn)
            self._layer_btns[key] = btn
        ds.addLayout(layer_row)

        # row 2: satellite selector (exclusive)
        sat_row = QHBoxLayout()
        sat_row.setSpacing(6)
        sat_lbl = QLabel("SAT")
        sat_lbl.setObjectName("fieldLabel")
        sat_lbl.setFixedWidth(28)
        sat_row.addWidget(sat_lbl)
        self._sat_btns: dict[str, QPushButton] = {}
        for key, label in (("", "OFF"), ("conus", "CONUS"), ("auto_meso", "AUTO-MESO")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_satellite(k))
            sat_row.addWidget(btn)
            self._sat_btns[key] = btn
        ds.addLayout(sat_row)

        # row 3: surface obs multi-select
        obs_row = QHBoxLayout()
        obs_row.setSpacing(6)
        obs_lbl = QLabel("OBS")
        obs_lbl.setObjectName("fieldLabel")
        obs_lbl.setFixedWidth(28)
        obs_row.addWidget(obs_lbl)
        self._obs_btns: dict[str, QPushButton] = {}
        for key, label in (
            ("ok", "OK MESO"),
            ("wtm", "WTM"),
            ("ks", "KS"),
            ("co", "CO"),
            ("ne", "NE"),
        ):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._toggle_obs(k))
            obs_row.addWidget(btn)
            self._obs_btns[key] = btn
        ds.addLayout(obs_row)

        # row 4: radar render resolution
        res_row = QHBoxLayout()
        res_row.setSpacing(6)
        res_lbl = QLabel("RES")
        res_lbl.setObjectName("fieldLabel")
        res_lbl.setFixedWidth(28)
        res_row.addWidget(res_lbl)
        self._res_combo = QComboBox()
        self._res_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _RES_OPTIONS = [
            ("Dynamic (auto-adjust)", -1),
            ("256 px  (fast)", 256),
            ("384 px", 384),
            ("512 px", 512),
            ("768 px  (sharp)", 768),
            ("1024 px  (ultra)", 1024),
            ("1536 px  (max)", 1536),
        ]
        for label, value in _RES_OPTIONS:
            self._res_combo.addItem(label, value)
        saved_res = int(saved.get("radar_resolution", -1))
        default_idx = 0
        for i, (_, v) in enumerate(_RES_OPTIONS):
            if v == saved_res:
                default_idx = i
                break
        self._res_combo.setCurrentIndex(default_idx)
        res_row.addWidget(self._res_combo)
        ds.addLayout(res_row)

        self._data_section.setVisible(False)
        root.addWidget(self._data_section)

        # initialise multi-select state from saved settings
        self._selected_layers: set[str] = set()
        for key, setting in (("spc", "auto_spc"), ("nws", "auto_nws"), ("radar", "auto_radar")):
            if saved.get(setting, False):
                self._selected_layers.add(key)
        self._refresh_layer_styles()

        self._select_satellite(saved.get("auto_satellite", ""))

        self._selected_obs: set[str] = set()
        for key in ("ok", "wtm", "ks", "co", "ne"):
            if saved.get(f"auto_obs_{key}", False):
                self._selected_obs.add(key)
        self._refresh_obs_styles()

        # auto-expand if any data pref is set
        any_data = (
            bool(self._selected_layers)
            or bool(self._selected_satellite)
            or bool(self._selected_obs)
        )
        if any_data:
            self._toggle_data_section()

        root.addSpacing(20)

        # launch button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        launch = QPushButton("LAUNCH STORM")
        launch.setObjectName("launchBtn")
        launch.clicked.connect(self._on_launch)
        launch.setDefault(True)
        self._launch_btn = launch
        btn_row.addWidget(launch)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # update button (below launch, centered, smaller)
        root.addSpacing(10)
        upd_row = QHBoxLayout()
        upd_row.addStretch()
        self._update_btn = QPushButton("CHECKING FOR UPDATES...")
        self._update_btn.setEnabled(False)
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._update_btn.clicked.connect(self._on_update_clicked)
        upd_row.addWidget(self._update_btn)
        upd_row.addStretch()
        root.addLayout(upd_row)

        # log viewer links (very subtle, bottom of dialog)
        root.addSpacing(4)
        log_row = QHBoxLayout()
        log_row.addStretch()
        self._crash_log_btn = QPushButton("VIEW CRASH LOG")
        self._crash_log_btn.setStyleSheet(_LOG_BTN_STYLE)
        self._crash_log_btn.clicked.connect(self._on_view_crash_log_clicked)
        log_row.addWidget(self._crash_log_btn)
        log_row.addStretch()
        root.addLayout(log_row)

        # defer adjustSize until the event loop starts so the full layout is
        QTimer.singleShot(0, self._post_layout_adjust)


    def _post_layout_adjust(self):
        """Resize to content then re-center within the available screen area."""
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + max(0, (screen.width()  - self.width())  // 2)
        y = screen.y() + max(0, (screen.height() - self.height()) // 2)
        self.move(x, y)


    def _build_calendar_button(self) -> QToolButton:
        """A small button next to the archive date field that opens a
        standard month/year calendar popup -- an explicit, always-visible
        alternative to typing the date by hand. Stored on self so it can
        be found/clicked directly (production code and tests alike)."""
        self._calendar_popup: "QCalendarWidget | None" = None
        btn = QToolButton()
        btn.setIcon(_calendar_glyph_icon())
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip("Pick a date from the calendar")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(32, 32)
        btn.setStyleSheet(
            "QToolButton {"
            "  background-color: #1A1A2E;"
            "  border: 1px solid #1E1E2E;"
            "  border-radius: 6px;"
            "}"
            "QToolButton:hover {"
            "  border-color: #00CFFF;"
            "  background-color: #0D1A2E;"
            "}"
        )
        btn.clicked.connect(self._open_date_picker)
        self._cal_btn = btn
        return btn

    def _open_date_picker(self):
        """Show a popup calendar anchored under the calendar button,
        pre-selected to the archive field's current date."""
        if self._calendar_popup is None:
            popup = QCalendarWidget(self)
            popup.setWindowFlags(Qt.WindowType.Popup)
            popup.setGridVisible(False)
            popup.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            popup.setStyleSheet(_DIALOG_STYLE)
            popup.clicked.connect(self._on_date_picked)
            self._calendar_popup = popup
            self._style_calendar_nav_icons(popup)

        self._calendar_popup.setSelectedDate(self._archive_dt_edit.date())
        anchor = self.sender()
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self._calendar_popup.move(pos)
        self._calendar_popup.show()

    def _on_date_picked(self, qdate):
        """Apply the calendar's chosen date to the archive field, keeping
        the currently-set time of day, then close the popup."""
        current_time = self._archive_dt_edit.time()
        self._archive_dt_edit.setDateTime(QDateTime(qdate, current_time))
        if self._calendar_popup is not None:
            self._calendar_popup.hide()

    def _style_calendar_nav_icons(self, calendar: "QCalendarWidget"):
        """Apply prev/next-month and year-spinbox triangle icons to a
        QCalendarWidget's built-in navigation controls, styled to match
        this dialog's dark theme. Used for both the archive date field's
        own calendar and the standalone popup from _build_calendar_button,
        so the two look identical."""
        _prev = calendar.findChild(QToolButton, "qt_calendar_prevmonth")
        _next = calendar.findChild(QToolButton, "qt_calendar_nextmonth")
        if _prev:
            _prev.setIcon(_triangle_icon([(0.85, 0.1), (0.85, 0.9), (0.15, 0.5)], "#FFFFFF"))
            _prev.setIconSize(QSize(10, 10))
        if _next:
            _next.setIcon(_triangle_icon([(0.15, 0.1), (0.15, 0.9), (0.85, 0.5)], "#FFFFFF"))
            _next.setIconSize(QSize(10, 10))
        _spin = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
        if _spin:
            _btns = _spin.findChildren(QAbstractButton)
            if len(_btns) >= 2:
                _btns[0].setIcon(_triangle_icon([(0.1, 0.85), (0.9, 0.85), (0.5, 0.15)], "#8E97AB", 10, 8))
                _btns[0].setIconSize(QSize(10, 8))
                _btns[1].setIcon(_triangle_icon([(0.1, 0.15), (0.9, 0.15), (0.5, 0.85)], "#8E97AB", 10, 8))
                _btns[1].setIconSize(QSize(10, 8))

    def _init_calendar_icons(self):
        """Style the archive date field's own built-in calendar popup
        (from setCalendarPopup(True)) to match the dark theme.

        Note: the QDateTimeEdit's calendar-popup *trigger* is drawn by
        the style as a `::drop-down`/`::down-arrow` sub-control (see
        styles.py), not a real child widget -- there is nothing for
        findChild(QToolButton) to locate or re-icon there, so it isn't
        attempted. The visible calendar button next to the date field is
        a separate, explicit QToolButton (see _build_calendar_button)
        with its own popup, precisely so its icon/size/click target are
        fully within our control."""
        self._style_calendar_nav_icons(self._archive_dt_edit.calendarWidget())

    def _set_fields_locked(self, locked: bool):
        self._vid_input.setReadOnly(locked)
        self._dir_input.setReadOnly(locked)
        self._browse_btn.setEnabled(not locked)
        self._gps_mode_check.setEnabled(not locked)
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip("Unlock all fields" if locked else "Lock all fields")
        for btn in self._icon_btns.values():
            btn.setEnabled(not locked)

    def _toggle_fields_lock(self):
        self._set_fields_locked(not self._vid_input.isReadOnly())

    def _select_mode(self, mode: str):
        self._selected_mode = mode
        for key, btn in self._mode_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if key == mode else _MODE_BTN_STYLE)
        vehicle_mode = (mode == "vehicle")
        viewer_mode  = (mode == "viewer")
        archive_mode = (mode == "archive")

        self._vehicle_section.setVisible(vehicle_mode)
        # archive: show passphrase (for server auth) but hide vehicle fields
        self._passphrase_row.setVisible(not viewer_mode)
        # archive-specific date/time picker; data-on-launch hidden in archive mode
        self._archive_section.setVisible(archive_mode)
        if hasattr(self, "_data_section"):
            self._data_toggle_btn.setVisible(not archive_mode)
            if archive_mode and self._data_section.isVisible():
                self._data_section.setVisible(False)
                self._data_toggle_btn.setText("▸  DATA CONFIGURATION")

        if not viewer_mode:
            if archive_mode:
                lbl = "ARCHIVE PASSPHRASE"
            elif vehicle_mode:
                lbl = "VEHICLE PASSPHRASE"
            else:
                lbl = "MONITOR PASSPHRASE"
            self._passphrase_label.setText(lbl)
        self._passphrase_input.clear()
        if self.isVisible():
            self._post_layout_adjust()

    def _on_admin_toggled(self, checked: bool):
        self._admin_passphrase_row.setVisible(checked)
        if not checked:
            self._admin_passphrase_input.clear()
        if self.isVisible():
            self._post_layout_adjust()

    def _toggle_data_section(self):
        visible = not self._data_section.isVisible()
        self._data_section.setVisible(visible)
        self._data_toggle_btn.setText("▾  DATA ON LAUNCH" if visible else "▸  DATA ON LAUNCH")
        QTimer.singleShot(0, self.adjustSize)

    def _toggle_layer(self, key: str):
        self._selected_layers.discard(key) if key in self._selected_layers else self._selected_layers.add(key)
        self._refresh_layer_styles()

    def _refresh_layer_styles(self):
        for k, btn in self._layer_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_layers else _MODE_BTN_STYLE)

    def _select_satellite(self, key: str):
        self._selected_satellite = key if key in self._sat_btns else ""
        for k, btn in self._sat_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k == self._selected_satellite else _MODE_BTN_STYLE)

    def _toggle_obs(self, key: str):
        self._selected_obs.discard(key) if key in self._selected_obs else self._selected_obs.add(key)
        self._refresh_obs_styles()

    def _refresh_obs_styles(self):
        for k, btn in self._obs_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_obs else _MODE_BTN_STYLE)

    def _select_icon(self, key: str):
        self._set_icon_selected(key)

    def _set_icon_selected(self, key: str):
        self._selected_icon = key if key in self._icon_btns else "car"
        for k, btn in self._icon_btns.items():
            selected = (k == self._selected_icon)
            btn.setStyleSheet(_ICON_SELECTED_STYLE if selected else "")
            btn.setIcon(QIcon(_svg_pixmap(k, "#00CFFF" if selected else "#5A5B6A", 22)))


    def _start_update_check(self):
        self._worker = UpdateWorker()
        self._worker.check_done.connect(self._on_check_done)
        self._worker.pull_done.connect(self._on_pull_done)
        self._worker.start_check()

    def _on_check_done(self, commits_behind: int):
        if commits_behind == -3:
            self._update_btn.setText("⚠   NOT A GIT INSTALL — CLONE REPO FOR IN-APP UPDATES")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == -2:
            self._update_btn.setText("DEV BUILD")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        elif commits_behind < 0:
            self._update_btn.setText("⚠   UPDATE CHECK FAILED — PROCEED AND TRY AGAIN LATER")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == 0:
            self._update_btn.setText("✓   UP TO DATE")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        else:
            n = commits_behind
            label = "UPDATE AVAILABLE — CLICK TO UPDATE"
            self._update_btn.setText(label)
            self._update_btn.setStyleSheet(_UPD_AVAILABLE)
            self._update_btn.setEnabled(True)

    def _on_update_clicked(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("UPDATING...")
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._worker.start_pull()

    def _on_pull_done(self, success: bool, deps_changed: bool):
        if success and deps_changed:
            # show dialog asking user if they want automatic update
            reply = QMessageBox.question(
                self,
                "Dependencies Changed",
                "The update includes dependency changes that require updating your conda environment.\n\n"
                "Would you like STORM to update the environment automatically?\n\n"
                "This will run: conda env update -f envs/storm.yml --prune",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._start_conda_update()
            else:
                # show manual instructions
                _cmd = "conda env update -f envs/storm.yml --prune"
                self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
                self._update_btn.setStyleSheet(_UPD_WARNING)
                self._update_btn.setWordWrap(True)
                QTimer.singleShot(0, self._post_layout_adjust)
        elif success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            QTimer.singleShot(800, self._restart_app)
        else:
            self._update_btn.setText("UPDATE FAILED")
            self._update_btn.setStyleSheet(_UPD_ERROR)

    def _start_conda_update(self):
        """Show progress dialog and start conda update in background."""
        # prevent multiple simultaneous updates
        if hasattr(self, '_conda_dialog') and self._conda_dialog:
            return
        
        self._conda_dialog = _CondaUpdateDialog(self)
        self._conda_dialog.rejected.connect(self._worker.cancel_conda_update)
        # use a unique-connection to avoid duplicate slots on repeated calls
        try:
            self._worker.conda_update_done.disconnect(self._on_conda_update_done)
        except TypeError:
            pass
        self._worker.conda_update_done.connect(self._on_conda_update_done)
        self._worker.start_conda_update()
        self._conda_dialog.exec()

    def _on_conda_update_done(self, success: bool, error_msg: str):
        """Handle conda update completion."""
        dialog = getattr(self, "_conda_dialog", None)
        if dialog:
            dialog.accept()
            self._conda_dialog = None
        else:
            # dialog already dismissed (user cancelled) — just show manual fallback
            if not success:
                _cmd = "conda env update -f envs/storm.yml --prune"
                self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
                self._update_btn.setStyleSheet(_UPD_WARNING)
                self._update_btn.setWordWrap(True)
                QTimer.singleShot(0, self._post_layout_adjust)
            return

        if success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            QTimer.singleShot(800, self._restart_app)
        else:
            # show error and fall back to manual instructions
            QMessageBox.warning(
                self,
                "Conda Update Failed",
                f"Automatic conda update failed:\n\n{error_msg}\n\n"
                "Please run the following command manually:\n"
                "conda env update -f envs/storm.yml --prune",
            )
            _cmd = "conda env update -f envs/storm.yml --prune"
            self._update_btn.setText(f"⚠   DEPS CHANGED — RUN:\n{_cmd}\nTHEN RESTART")
            self._update_btn.setStyleSheet(_UPD_WARNING)
            self._update_btn.setWordWrap(True)
            QTimer.singleShot(0, self._post_layout_adjust)

    def _restart_app(self):
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_view_crash_log_clicked(self):
        log_path = os.path.join(self._project_root, "storm_fault.log")
        dlg = _LogViewerDialog(log_path, parent=self)
        dlg.exec()


    def _browse_dir(self):
        current = self._dir_input.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select data directory", current
        )
        if chosen:
            self._dir_input.setText(chosen)

    def _on_launch(self):
        mode = getattr(self, "_selected_mode", "vehicle")

        # validate passphrase for vehicle, monitor, and archive modes
        if mode not in ("viewer",):
            passphrase = self._passphrase_input.text()
            if mode == "vehicle":
                stored = _config.VEHICLE_PASSPHRASE_HASH
            elif mode == "archive":
                stored = _config.ARCHIVE_PASSPHRASE_HASH
            else:
                stored = _config.MONITOR_PASSPHRASE_HASH
            if not _verify_pbkdf2(passphrase, stored):
                QMessageBox.warning(
                    self,
                    "Incorrect Passphrase",
                    "The passphrase you entered is incorrect.",
                )
                self._passphrase_input.clear()
                self._passphrase_input.setFocus()
                return

        if self._admin_check.isChecked():
            if not _config.ADMIN_PASSPHRASE_HASH:
                QMessageBox.warning(
                    self,
                    "Admin Passphrase Not Configured",
                    "Admin/Labs mode is not configured for this build.",
                )
                self._admin_check.setFocus()
                return
            if not _verify_pbkdf2(
                self._admin_passphrase_input.text(),
                _config.ADMIN_PASSPHRASE_HASH,
            ):
                QMessageBox.warning(
                    self,
                    "Incorrect Admin Passphrase",
                    "The admin passphrase you entered is incorrect.",
                )
                self._admin_passphrase_input.clear()
                self._admin_passphrase_input.setFocus()
                return

        # vehicle ID is required in vehicle mode
        if mode == "vehicle":
            vid = self._vid_input.text().strip()
            if not vid:
                QMessageBox.warning(
                    self,
                    "Vehicle ID Required",
                    "Please enter a vehicle ID before launching.",
                )
                self._vid_input.setFocus()
                return

        self._do_accept()

    def _do_accept(self):
        s = QSettings()
        s.setValue("launch/vehicle_id",     self._vid_input.text().strip())
        s.setValue("launch/data_dir",       self._dir_input.text().strip())
        s.setValue("launch/gps_file_mode",  self._gps_mode_check.isChecked())
        mode = getattr(self, "_selected_mode", "vehicle")
        # don't persist archive mode as the default (it needs explicit selection).
        s.setValue("launch/mode", mode if mode != "archive" else "viewer")
        s.setValue("launch/vehicle_icon",   getattr(self, "_selected_icon", "car"))
        layers = getattr(self, "_selected_layers", set())
        s.setValue("launch/auto_spc",   "spc"   in layers)
        s.setValue("launch/auto_nws",   "nws"   in layers)
        s.setValue("launch/auto_radar", "radar" in layers)
        s.setValue("launch/auto_satellite", getattr(self, "_selected_satellite", ""))
        obs = getattr(self, "_selected_obs", set())
        s.setValue("launch/auto_obs_ok",  "ok"  in obs)
        s.setValue("launch/auto_obs_wtm", "wtm" in obs)
        s.setValue("launch/auto_obs_ks",  "ks"  in obs)
        s.setValue("launch/auto_obs_co",  "co"  in obs)
        s.setValue("launch/auto_obs_ne",  "ne"  in obs)
        s.setValue("launch/radar_resolution", self._res_combo.currentData())
        self.accept()


    def vehicle_id(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._vid_input.text().strip()

    def data_dir(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._dir_input.text().strip()

    def gps_file_mode(self) -> bool:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return False
        return self._gps_mode_check.isChecked()

    def monitor(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "monitor"

    def viewer(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "viewer"

    def archive(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "archive"

    def admin_mode(self) -> bool:
        return self._admin_check.isChecked()

    def archive_start_time(self) -> "datetime | None":
        """UTC start time chosen in the archive date/time picker, or None."""
        if not self.archive():
            return None
        qt_dt = self._archive_dt_edit.dateTime()
        return datetime(
            qt_dt.date().year(),
            qt_dt.date().month(),
            qt_dt.date().day(),
            qt_dt.time().hour(),
            qt_dt.time().minute(),
            qt_dt.time().second(),
            tzinfo=timezone.utc,
        )

    def vehicle_icon(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return "car"
        return getattr(self, "_selected_icon", "car")

    def radar_resolution(self) -> int:
        """Return the chosen render grid size, or -1 for dynamic (adaptive) mode."""
        return self._res_combo.currentData()
