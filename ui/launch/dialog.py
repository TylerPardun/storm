
import base64
import hashlib
import hmac
import logging
import os
import random
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QToolButton, QFileDialog, QFrame,
    QApplication, QMessageBox, QSizePolicy, QWidget,
    QDateTimeEdit, QSpinBox, QComboBox, QCheckBox,
    QCalendarWidget, QListWidget, QListWidgetItem, QTabWidget, QScrollArea,
)
from PyQt6.QtCore import (
    Qt, QSettings, QTimer, QSize, QDate, QDateTime, QPointF, QRectF, QObject, QEvent, pyqtSignal,
)
from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPolygonF, QPen, QTextCharFormat

import config as _config
from ui.launch.icons import combo_down_arrow_qss, _svg_pixmap
from ui.launch.styles import (
    _DIALOG_STYLE, _ICON_SELECTED_STYLE, _LOG_BTN_STYLE,
    _MODE_BTN_SELECTED_STYLE, _MODE_BTN_STYLE, _UPD_AVAILABLE, _UPD_CHECKING,
    _UPD_CURRENT, _UPD_ERROR, _UPD_SUCCESS, _UPD_WARNING, _YEAR_GRID_STYLE,
)
from ui.launch.availability import AvailabilityWorker
from archive.catalog import ALL_PLATFORMS, CAMPAIGN_YEARS
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


class _CircularProgress(QWidget):
    """Small circular progress ring for the archive catalog scan.

    Originally added alongside a linear bar (_browse_coverage_progress)
    inside the collapsible "browse" drawer, which starts collapsed -- easy
    to never see, which reads as "nothing is happening" even though
    AvailabilityIndex.scan() (archive/catalog.py) is genuinely progressive,
    most-recent-year first. That bar has since been removed as redundant;
    this wheel (next to the date picker, visible without expanding
    anything) is the only progress indicator now.
    """

    def __init__(self, parent=None, diameter: int = 20):
        super().__init__(parent)
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self._value = 0
        self._maximum = 0

    def setRange(self, minimum: int, maximum: int) -> None:
        self._maximum = max(0, maximum)
        self.update()

    def setValue(self, value: int) -> None:
        self._value = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        track = QPen(QColor("#2A2A3E"))
        track.setWidth(3)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)

        fraction = (self._value / self._maximum) if self._maximum else 0.0
        arc = QPen(QColor("#00CFFF"))
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        # 12 o'clock start, clockwise sweep (Qt angles are counterclockwise
        # from 3 o'clock in 1/16ths of a degree, hence the negative span).
        painter.drawArc(rect, 90 * 16, -round(360 * 16 * fraction))
        painter.end()


class _AvailabilityCalendar(QCalendarWidget):
    """Paint availability beneath readable dates, with a distinct selection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._known_dates = frozenset()
        self.setMinimumSize(322, 260)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        # Field deployments only ever produce past data -- no case can exist
        # for a day that hasn't happened yet.
        self.setMaximumDate(QDate.currentDate())
        weekday = QTextCharFormat()
        weekday.setForeground(QColor("#8E97AB"))
        for day in Qt.DayOfWeek:
            self.setWeekdayTextFormat(day, weekday)

    def set_known_dates(self, dates):
        self._known_dates = frozenset(dates)
        self.updateCells()

    def paintCell(self, painter, rect, date):
        # Draw each cell once: tinting after Qt's native paintCell washes
        # out both the numeral and the selection. Inset tiles also keep
        # consecutive available days from merging into one solid block.
        current_month = date.year() == self.yearShown() and date.month() == self.monthShown()
        enabled = self.isEnabled() and self.minimumDate() <= date <= self.maximumDate()
        available = date.toPyDate() in self._known_dates
        selected = date == self.selectedDate() and enabled
        background = "#0D0D1A"
        foreground = "#E8EAF0" if current_month else "#8E97AB"
        border = None
        if not enabled:
            foreground = "#606879"
        elif selected:
            background, foreground = "#00CFFF", "#0A0A0F"
        elif available:
            background = "#123C50" if current_month else "#142936"
            foreground = "#F0F8FF" if current_month else "#A6B6C8"
            border = "#28627A" if current_month else "#24404F"
        if enabled and not selected and date == QDate.currentDate():
            border = "#8E97AB"

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor("#0D0D1A"))
        tile = QRectF(rect).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(border), 1) if border else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(tile, 4, 4)
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(selected or (available and enabled))
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))
        painter.restore()


class _ClickConsumer(QObject):
    """Swallows mouse-press events on a widget and calls `on_click`
    instead of letting them reach that widget's own handling. Used on
    the calendar's qt_calendar_yearbutton (see _style_calendar_nav_icons)
    to replace its built-in "reveal an editable year spinbox" behavior
    with opening the year-grid popup -- consuming the event means Qt's
    own internal click handling (and the slot it's wired to) never
    runs, rather than running alongside a separately-connected handler."""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click

    def eventFilter(self, obj, event):
        mouse_activate = event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
        key_activate = event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if mouse_activate or key_activate:
            self._on_click()
            return True
        return False


class _YearGridPopup(QWidget):
    """A small popup grid of years so the calendar's year field can jump
    straight to a year instead of stepping it one click at a time --
    the same idea QCalendarWidget already applies to months (a grid you
    pick from), just for years, since Qt doesn't ship one itself."""

    yearPicked = pyqtSignal(int)

    _COLUMNS = 4
    _ROWS = 3
    _COUNT = _COLUMNS * _ROWS

    def __init__(self, current_year: int, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("yearGridPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_YEAR_GRID_STYLE)
        self._known_years = frozenset()
        self._current_year = current_year
        # No field deployment has data past today, so no page should ever
        # scroll in a year beyond this one.
        self._max_year = QDate.currentDate().year()
        self._start_year = self._clamp_start(current_year - 5)
        self._buttons: list[QPushButton] = []
        self._build_ui()
        self._refresh()

    def _clamp_start(self, start: int) -> int:
        return min(start, self._max_year - self._COUNT + 1)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        prev_btn = QToolButton()
        prev_btn.setObjectName("yearNavBtn")
        prev_btn.setIcon(_triangle_icon([(0.85, 0.1), (0.85, 0.9), (0.15, 0.5)], "#8E97AB"))
        prev_btn.setIconSize(QSize(9, 9))
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.setToolTip("Previous years")
        prev_btn.clicked.connect(lambda: self._shift(-self._COUNT))
        nav.addWidget(prev_btn)

        self._range_lbl = QLabel()
        self._range_lbl.setObjectName("yearRangeLbl")
        self._range_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._range_lbl, 1)

        next_btn = QToolButton()
        next_btn.setObjectName("yearNavBtn")
        next_btn.setIcon(_triangle_icon([(0.15, 0.1), (0.15, 0.9), (0.85, 0.5)], "#8E97AB"))
        next_btn.setIconSize(QSize(9, 9))
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.setToolTip("Next years")
        next_btn.clicked.connect(lambda: self._shift(self._COUNT))
        nav.addWidget(next_btn)
        self._next_btn = next_btn
        outer.addLayout(nav)

        grid = QGridLayout()
        grid.setSpacing(5)
        for i in range(self._COUNT):
            btn = QPushButton()
            btn.setObjectName("yearCell")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: self._pick(idx))
            grid.addWidget(btn, i // self._COLUMNS, i % self._COLUMNS)
            self._buttons.append(btn)
        outer.addLayout(grid)

    def _shift(self, delta: int):
        self._start_year = self._clamp_start(self._start_year + delta)
        self._refresh()

    def _pick(self, idx: int):
        self.yearPicked.emit(self._start_year + idx)
        self.hide()

    def set_known_years(self, years):
        self._known_years = frozenset(years)
        self._refresh()

    def _refresh(self):
        self._range_lbl.setText(f"{self._start_year} – {self._start_year + self._COUNT - 1}")
        self._next_btn.setEnabled(self._start_year + self._COUNT - 1 < self._max_year)
        for i, btn in enumerate(self._buttons):
            year = self._start_year + i
            btn.setText(str(year))
            btn.setProperty("available", year in self._known_years)
            btn.setToolTip("Dates with data" if year in self._known_years else "No dates found yet")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            selected = year == self._current_year
            if btn.property("selected") != selected:
                btn.setProperty("selected", selected)
                btn.style().unpolish(btn)
                btn.style().polish(btn)


class LaunchDialog(QDialog):
    """
    Pre-launch configuration dialog.  Reads previous settings from
    config.toml and writes them back on confirmation so the next launch
    is pre-populated automatically.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # _post_layout_adjust() re-centers after every content-size change
        # (switching modes, browse results changing height, ...), which
        # otherwise silently discards a window position the user dragged
        # themselves -- these two flags make _recenter() a one-time "center
        # on first show" instead of "re-center on every layout pass."
        self._user_moved_window = False
        self._recentering_window = False
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
        self._project_root = str(Path(__file__).resolve().parents[2])
        self._year_click_filters: list = []
        self._year_grid_popup: "_YearGridPopup | None" = None
        self._availability = AvailabilityWorker()
        self._availability.updated.connect(self._on_availability_updated)
        self.destroyed.connect(self._availability.close)
        self._availability_generation = 0
        self._availability_snapshot = None
        self._availability_refresh = False
        self._availability_timer = QTimer(self)
        self._availability_timer.setSingleShot(True)
        self._availability_timer.setInterval(250)
        self._availability_timer.timeout.connect(self._request_availability)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._post_layout_adjust)
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._restart_app)
        self._build_ui(saved)
        self._select_mode(saved.get("mode", "vehicle"))
        self._archive_dt_edit.dateChanged.connect(self._schedule_availability)
        self._start_update_check()


    def _build_ui(self, saved: dict):
        frame = QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self._form_scroll = QScrollArea()
        self._form_scroll.setObjectName("launchFormScroll")
        self._form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form = QWidget()
        self._form.setObjectName("launchForm")
        self._form.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._form_scroll.setWidget(self._form)
        frame.addWidget(self._form_scroll)
        root = QVBoxLayout(self._form)
        root.setContentsMargins(32, 32, 32, 12)
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
        self._archive_dt_edit.setCalendarWidget(_AvailabilityCalendar(self._archive_dt_edit))
        # default to yesterday at 20:00 UTC as a sensible starting point.
        now_utc = datetime.now(timezone.utc)
        yesterday = (now_utc - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
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
        self._archive_scan_progress = _CircularProgress(self)
        self._archive_scan_progress.setToolTip("Finding dates…")
        self._archive_scan_progress.hide()
        dt_row.addWidget(self._archive_scan_progress)
        av_layout.addLayout(dt_row)
        self._init_calendar_icons()

        arc_hint = QLabel("All data products will replay from this UTC time.")
        arc_hint.setObjectName("hint")
        arc_hint.setWordWrap(True)
        av_layout.addWidget(arc_hint)

        self._surprise_btn = QPushButton("SURPRISE ME")
        self._surprise_btn.setToolTip(
            "Choose a random date with data"
        )
        self._surprise_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._surprise_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #1A1A2E; color: #8E97AB;"
            "  border: 1px solid #1E1E2E; border-radius: 6px;"
            "  font-size: 11px; font-weight: 700; padding: 6px 10px;"
            "}"
            "QPushButton:hover { border-color: #00CFFF; color: #00CFFF; background-color: #0D1A2E; }"
        )
        self._surprise_btn.clicked.connect(self._on_surprise_me_clicked)
        av_layout.addWidget(self._surprise_btn)

        av_layout.addSpacing(8)
        self._build_browse_section(av_layout)

        self._archive_section.setVisible(False)
        root.addWidget(self._archive_section)

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

        root.addSpacing(4)

        # Keep launch/update controls reachable while a long form scrolls.
        self._footer = QWidget()
        root = QVBoxLayout(self._footer)
        root.setContentsMargins(32, 14, 32, 20)
        root.setSpacing(0)
        frame.addWidget(self._footer)

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
        self._layout_timer.start(0)


    def _post_layout_adjust(self):
        """Honor wrapped content height; scroll the form on smaller screens.

        QWidget.adjustSize() caps top-level windows at a fraction of the screen
        and can squeeze nested layouts below their height-for-width. Compute the
        form's height explicitly instead of pumping a nested event loop.
        """
        self._browse_coverage_lbl.setMinimumHeight(0)
        self._form.setMinimumHeight(0)
        layout = self._form.layout()
        layout.invalidate()
        layout.activate()
        width = max(self.minimumWidth(), self._form.minimumSizeHint().width())
        height = layout.heightForWidth(width)
        footer_height = self._footer.sizeHint().height()
        screen = self.screen().availableGeometry()
        max_height = max(240, screen.height() - 60)
        scrollbar = self._form_scroll.verticalScrollBar().sizeHint().width() if height + footer_height > max_height else 0
        self._form.setMinimumHeight(height)
        self.resize(width + scrollbar, min(height + footer_height, max_height))
        self.layout().activate()
        self._form_scroll.widget().layout().activate()
        lbl = self._browse_coverage_lbl
        lbl.setMinimumHeight(max(0, lbl.heightForWidth(lbl.width())) if lbl.text() else 0)
        if not self._user_moved_window:
            self._recenter()

    def _recenter(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + max(0, (screen.width()  - self.width())  // 2)
        y = screen.y() + max(0, (screen.height() - self.height()) // 2)
        self._recentering_window = True
        try:
            self.move(x, y)
        finally:
            self._recentering_window = False

    def moveEvent(self, event):
        super().moveEvent(event)
        if not self._recentering_window:
            self._user_moved_window = True


    def _build_calendar_button(self) -> QToolButton:
        """A small button next to the archive date field that opens a
        standard month/year calendar popup -- an explicit, always-visible
        alternative to typing the date by hand. Stored on self so it can
        be found/clicked directly (production code and tests alike)."""
        self._calendar_popup: "QCalendarWidget | None" = None
        btn = QToolButton()
        btn.setIcon(_calendar_glyph_icon())
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip("Choose date")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(32, 32)
        btn.setStyleSheet(
            "QToolButton {"
            "  background-color: #1A1A2E;"
            "  color: #8E97AB;"
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
            popup = _AvailabilityCalendar(self)
            popup.setWindowFlags(Qt.WindowType.Popup)
            popup.setGridVisible(False)
            popup.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            popup.setStyleSheet(_DIALOG_STYLE)
            popup.clicked.connect(self._on_date_picked)
            self._calendar_popup = popup
            self._style_calendar_nav_icons(popup)

        self._calendar_popup.setSelectedDate(self._archive_dt_edit.date())
        self._calendar_popup.set_known_dates(self._filtered_dates())
        anchor = self._cal_btn
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

    def _on_surprise_me_clicked(self):
        """Jump to a random date that actually has catalog data -- the same
        pool the calendar shading draws from (AvailabilitySnapshot.dates,
        unfiltered by the browse section's platform/year/campaign pickers,
        since "surprise me" should draw from everything, not whatever
        narrow filter happens to be set)."""
        snapshot = self._availability_snapshot
        dates = snapshot.dates if snapshot is not None else frozenset()
        if not dates:
            QMessageBox.information(
                self, "Still Looking",
                "Still discovering available dates in the background -- give it "
                "a few seconds and try again.",
            )
            return
        chosen = random.choice(sorted(dates))
        current_time = self._archive_dt_edit.time()
        self._archive_dt_edit.setDateTime(
            QDateTime(QDate(chosen.year, chosen.month, chosen.day), current_time)
        )

    def _style_calendar_nav_icons(self, calendar: "QCalendarWidget"):
        """Style existing controls; intercept year activation before Qt reveals
        its editable spinbox. Never insert widgets in Qt's private nav layout.
        """
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
            # Defensive: if Qt's swap-to-spinbox behavior still runs for
            # any reason, make sure what it reveals is never a real text
            # input either (no caret, no keyboard editing).
            _spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            _spin.setReadOnly(True)
            _spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            _spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

        _year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")
        if _year_btn is not None:
            _consumer = _ClickConsumer(lambda c=calendar, b=_year_btn: self._open_year_grid(c, b), calendar)
            _year_btn.installEventFilter(_consumer)
            self._year_click_filters.append(_consumer)

    def _open_year_grid(self, calendar: "QCalendarWidget", anchor: QToolButton):
        """Show the year-grid popup anchored under the clicked year
        button, seeded from the calendar's own currently-shown year --
        not the (possibly still-hidden, out-of-sync) year spinbox."""
        if self._year_grid_popup is not None:
            self._year_grid_popup.hide()
            self._year_grid_popup.deleteLater()
        popup = _YearGridPopup(calendar.yearShown(), parent=self)
        popup.set_known_years(d.year for d in self._filtered_dates())
        popup.yearPicked.connect(lambda year, c=calendar: self._on_year_picked(c, year))
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popup.move(pos)
        popup.show()
        self._year_grid_popup = popup  # keep a reference so it isn't gc'd while open

    def _on_year_picked(self, calendar: "QCalendarWidget", year: int):
        calendar.setCurrentPage(year, calendar.monthShown())

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

    # -- Archive availability: progressive session metadata, automatic date queries --

    def _build_browse_section(self, parent_layout: QVBoxLayout):
        summary_row = QHBoxLayout()
        self._browse_coverage_lbl = QLabel("Availability will be checked when archive mode opens.")
        self._browse_coverage_lbl.setObjectName("hint")
        self._browse_coverage_lbl.setWordWrap(True)
        summary_row.addWidget(self._browse_coverage_lbl, 1)
        self._availability_refresh_btn = QToolButton()
        self._availability_refresh_btn.setText("↻")
        self._availability_refresh_btn.setStyleSheet(self._cal_btn.styleSheet())
        self._availability_refresh_btn.setFixedSize(28, 28)
        self._availability_refresh_btn.setAccessibleName("Refresh archive availability")
        self._availability_refresh_btn.setToolTip("Refresh available dates")
        self._availability_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._availability_refresh_btn.clicked.connect(self._refresh_availability)
        summary_row.addWidget(self._availability_refresh_btn)
        parent_layout.addLayout(summary_row)

        self._browse_toggle_btn = QPushButton("▸  BROWSE AVAILABLE CASES")
        self._browse_toggle_btn.setObjectName("dataToggleBtn")
        self._browse_toggle_btn.setFixedHeight(18)
        self._browse_toggle_btn.clicked.connect(self._toggle_browse_section)
        parent_layout.addWidget(self._browse_toggle_btn)
        self._browse_section = QWidget()
        self._browse_section.setMinimumWidth(360)
        bs = QVBoxLayout(self._browse_section)
        bs.setContentsMargins(0, 8, 0, 0)
        bs.setSpacing(6)

        filter_row = QHBoxLayout()
        self._browse_campaign_combo = QComboBox()
        self._browse_campaign_combo.addItem("Any campaign years", None)
        self._browse_campaign_combo.setToolTip("Filter by campaign year; other campaigns may appear.")
        for name in CAMPAIGN_YEARS:
            self._browse_campaign_combo.addItem(name, name)
        self._browse_year_combo = QComboBox()
        self._populate_browse_year_combo(None)
        for combo in (self._browse_campaign_combo, self._browse_year_combo):
            combo.setCursor(Qt.CursorShape.PointingHandCursor)
            filter_row.addWidget(combo, 1)
        bs.addLayout(filter_row)
        self._browse_platform_combo = QComboBox()
        self._browse_platform_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_platform_combo.setMinimumContentsLength(24)
        self._browse_platform_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._browse_platform_combo.addItem("All instruments", None)
        for p in sorted(ALL_PLATFORMS, key=lambda p: (p.family, p.display_name)):
            self._browse_platform_combo.addItem(f"{p.family} — {p.display_name}", p)
        bs.addWidget(self._browse_platform_combo)
        self._browse_status_lbl = QLabel()
        self._browse_status_lbl.setObjectName("hint")
        self._browse_status_lbl.setWordWrap(True)
        bs.addWidget(self._browse_status_lbl)
        self._browse_dates_list = QListWidget()
        self._browse_dates_list.setObjectName("browseDatesList")
        self._browse_results = QTabWidget()
        self._browse_results.setObjectName("browseResults")
        self._browse_results.setFixedHeight(140)
        self._browse_dates_list.itemClicked.connect(self._on_browse_date_chosen)
        self._browse_dates_list.itemActivated.connect(self._on_browse_date_chosen)
        self._browse_results.addTab(self._browse_dates_list, "Dates")
        self._browse_sources_list = QListWidget()
        self._browse_sources_list.setObjectName("browseDatesList")
        self._browse_results.addTab(self._browse_sources_list, "Selected date · instruments")
        bs.addWidget(self._browse_results)
        note = QLabel("Shaded calendar dates and cyan years mark catalog-listed data matching these filters. "
                      "Unmarked dates may be unchecked. NOXP startup discovery is limited; use archive radar controls for more files.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        bs.addWidget(note)
        self._browse_section.hide()
        parent_layout.addWidget(self._browse_section)
        self._browse_campaign_combo.currentIndexChanged.connect(self._on_browse_campaign_changed)
        self._browse_year_combo.currentIndexChanged.connect(self._render_availability)
        self._browse_platform_combo.currentIndexChanged.connect(self._render_availability)

    def _toggle_browse_section(self):
        visible = self._browse_section.isHidden()
        self._browse_section.setVisible(visible)
        self._browse_toggle_btn.setText("▾  BROWSE AVAILABLE CASES" if visible else "▸  BROWSE AVAILABLE CASES")
        self._layout_timer.start(0)

    def _populate_browse_year_combo(self, campaign):
        self._browse_year_combo.blockSignals(True)
        self._browse_year_combo.clear()
        self._browse_year_combo.addItem("Any year", None)
        years = CAMPAIGN_YEARS.get(campaign, ()) if campaign else range(1999, datetime.now().year + 2)
        for year in years:
            self._browse_year_combo.addItem(str(year), year)
        self._browse_year_combo.blockSignals(False)

    def _on_browse_campaign_changed(self):
        self._populate_browse_year_combo(self._browse_campaign_combo.currentData())
        self._render_availability()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_availability()

    def hideEvent(self, event):
        self._availability_generation += 1
        self._availability_timer.stop()
        self._layout_timer.stop()
        self._availability.cancel()
        super().hideEvent(event)

    def _schedule_availability(self):
        self._availability_generation += 1
        self._availability.cancel()
        self._availability_timer.stop()
        if self._selected_mode == "archive":
            self._render_availability()
            if self.isVisible():
                self._availability_timer.start()

    def _refresh_availability(self):
        self._availability_refresh = True
        self._availability_snapshot = None
        self._schedule_availability()

    def _request_availability(self):
        if self.isVisible() and self._selected_mode == "archive":
            self._availability.request(self._availability_generation, self._availability_refresh)
            self._availability_refresh = False

    def _on_availability_updated(self, generation, snapshot):
        if generation != self._availability_generation or self._selected_mode != "archive":
            return
        # Keep diagnostics in the log rather than expanding hover labels.
        previous = self._availability_snapshot
        previous_errors = {
            (pid, error)
            for pid, result in previous.platforms.items()
            for error in result.errors
        } if previous else set()
        for pid, result in snapshot.platforms.items():
            for error in result.errors:
                if (pid, error) not in previous_errors:
                    logging.getLogger(__name__).warning("Archive availability [%s]: %s", pid, error)
        self._availability_snapshot = snapshot
        self._render_availability()

    def _filtered_dates(self):
        snapshot = self._availability_snapshot
        if snapshot is None:
            return frozenset()
        platform = self._browse_platform_combo.currentData()
        result = snapshot.platforms.get(platform.platform_id) if platform else None
        dates = result.dates if result else (frozenset() if platform else snapshot.dates)
        year = self._browse_year_combo.currentData()
        campaign = self._browse_campaign_combo.currentData()
        return frozenset(d for d in dates if (year is None or d.year == year)
                         and (campaign is None or d.year in CAMPAIGN_YEARS[campaign]))

    def _render_availability(self):
        snapshot = self._availability_snapshot
        target = self._archive_dt_edit.date().toPyDate()
        dates = self._filtered_dates()
        self._archive_dt_edit.calendarWidget().set_known_dates(dates)
        if self._calendar_popup is not None:
            self._calendar_popup.set_known_dates(dates)
        years = {d.year for d in dates}
        if self._year_grid_popup is not None:
            self._year_grid_popup.set_known_years(years)
        for i in range(1, self._browse_year_combo.count()):
            year = self._browse_year_combo.itemData(i)
            self._browse_year_combo.setItemData(i, QColor("#00CFFF" if year in years else "#E8EAF0"), Qt.ItemDataRole.ForegroundRole)
        self._browse_platform_combo.setToolTip(self._browse_platform_combo.currentText())
        # Preserve scroll position and selection when only coverage progress changed.
        listed = tuple(self._browse_dates_list.item(i).data(Qt.ItemDataRole.UserRole)
                       for i in range(self._browse_dates_list.count()))
        ordered = tuple(sorted(dates, reverse=True))
        if listed != ordered:
            scroll = self._browse_dates_list.verticalScrollBar().value()
            self._browse_dates_list.clear()
            for d in ordered:
                item = QListWidgetItem(d.isoformat())
                item.setData(Qt.ItemDataRole.UserRole, d)
                self._browse_dates_list.addItem(item)
                if d == target:
                    self._browse_dates_list.setCurrentItem(item)
            self._browse_dates_list.verticalScrollBar().setValue(scroll)

        target_row = ordered.index(target) if target in dates else -1
        if self._browse_dates_list.currentRow() != target_row:
            self._browse_dates_list.setCurrentRow(target_row)
        self._browse_status_lbl.setText(f"{len(dates)} known dates in {len(years)} years. Select a date to explore.")
        self._browse_sources_list.clear()
        present, unknown = [], []
        for p in ALL_PLATFORMS:
            result = snapshot.platforms.get(p.platform_id) if snapshot else None
            if result and target in result.dates:
                present.append(p)
                item = QListWidgetItem(f"{p.family} — {p.display_name}" + (" (partial)" if not result.complete else ""))
                item.setToolTip("Files listed; search incomplete." if not result.complete else "Files listed; data not yet checked.")
                self._browse_sources_list.addItem(item)
            if result is None or not result.complete:
                unknown.append(p)
        for p in unknown:
            if p in present:
                continue
            result = snapshot.platforms.get(p.platform_id) if snapshot else None
            item = QListWidgetItem(f"{'Incomplete' if result and result.errors else 'Checking'} — {p.family} — {p.display_name}")
            item.setForeground(QColor("#8E97AB"))
            item.setToolTip("Search incomplete. Refresh to retry." if result and result.errors else "Still checking this instrument…")
            self._browse_sources_list.addItem(item)
        self._browse_results.setTabText(1, "Selected date")
        running = snapshot is None or snapshot.checked < snapshot.total
        self._archive_scan_progress.setVisible(running)
        self._archive_scan_progress.setRange(0, snapshot.total if snapshot else 0)
        self._archive_scan_progress.setValue(snapshot.checked if snapshot else 0)
        self._archive_scan_progress.setToolTip(
            f"Finding dates · {snapshot.checked}/{snapshot.total} catalogs" if snapshot else "Finding dates…"
        )
        text = f"{target.isoformat()} · " + ("Data listed" if present else "No data found yet" if running or unknown else "No files listed in checked catalogs")
        if snapshot and snapshot.cached:
            text += " · cached listings"
        if running:
            text += f"\nIndexing catalogs{f' · {snapshot.checked}/{snapshot.total}' if snapshot else '…'}"
        elif unknown:
            text += "\nAvailability incomplete: some sources remain unchecked or partial. Use ↻ to refresh."
        else:
            text += "\nCatalog check complete. Calendar shading is ready."
        self._browse_coverage_lbl.setText(text)
        self._browse_coverage_lbl.setStyleSheet("color: #4ADE80;" if present else "")
        self._browse_coverage_lbl.setToolTip(
            "Some sources are unchecked or partial." if unknown
            else "Files listed; coverage may vary within each day." if present
            else "No files listed for this date."
        )
        self._layout_timer.start(0)

    def _on_browse_date_chosen(self, item):
        picked = item.data(Qt.ItemDataRole.UserRole)
        self._archive_dt_edit.setDate(QDate(picked.year, picked.month, picked.day))

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
            if archive_mode and not self._data_section.isHidden():
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
            self._layout_timer.start(0)

        self._schedule_availability()

    def _on_admin_toggled(self, checked: bool):
        self._admin_passphrase_row.setVisible(checked)
        if not checked:
            self._admin_passphrase_input.clear()
        if self.isVisible():
            self._layout_timer.start(0)

    def _toggle_data_section(self):
        visible = self._data_section.isHidden()
        self._data_section.setVisible(visible)
        self._data_toggle_btn.setText("▾  DATA CONFIGURATION" if visible else "▸  DATA CONFIGURATION")
        self._layout_timer.start(0)

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
                self._layout_timer.start(0)
        elif success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            self._restart_timer.start(800)
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
                self._layout_timer.start(0)
            return

        if success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            self._restart_timer.start(800)
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
            self._layout_timer.start(0)

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
