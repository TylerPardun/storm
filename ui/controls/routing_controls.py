
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QToolButton,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
    QApplication, QMenu,
)
from math import radians, sin, cos, sqrt, atan2

from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt, QTimer, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

from data.fetchers.routing_fetcher import RoutingFetcher, RouteResult, fmt_distance, fmt_duration

# pass-through detection thresholds
_CLOSE_M        = 40.0    # must come within this to be "at" the maneuver
_PASSED_M       = 20.0    # distance must grow by this after being close → passed through
# off-route re-routing thresholds
_OFF_ROUTE_M    = 100.0   # metres from route geometry → considered off-route
_OFF_ROUTE_COUNT = 3      # consecutive off-route fixes before recalculating
# arrival
_ARRIVE_M       = 30.0    # metres to destination → trigger arrival


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate great-circle distance in metres."""
    R = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

# text-color styles applied inline on the QLineEdit fields
_STYLE_GPS    = "color: #39D98A;"   # green  — set via GPS loc button
_STYLE_MANUAL = "color: #EFF3FF;"   # white  — manually entered / map-picked


def _make_vehicle_icon(size: int = 15) -> QIcon:
    """Return a QIcon with a simple car silhouette for the vehicle-picker button."""
    _SVG = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M19 17H5a2 2 0 0 1-2-2v-4l2-5h14l2 5v4a2 2 0 0 1-2 2z"'
        ' fill="none" stroke="{color}" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="7.5" cy="17" r="1.5" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<circle cx="16.5" cy="17" r="1.5" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '</svg>'
    )

    def _pixmap(color: str) -> QPixmap:
        svg_bytes = _SVG.format(color=color).encode()
        renderer = QSvgRenderer(svg_bytes)
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        renderer.render(painter)
        painter.end()
        return px

    icon = QIcon()
    icon.addPixmap(_pixmap("#4A9EFF"), QIcon.Mode.Normal,   QIcon.State.Off)
    icon.addPixmap(_pixmap("#3A3F52"), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


_VEHICLE_ICON = None


def _vehicle_icon() -> QIcon:
    global _VEHICLE_ICON
    if _VEHICLE_ICON is None:
        _VEHICLE_ICON = _make_vehicle_icon()
    return _VEHICLE_ICON


def _make_loc_icon(size: int = 15) -> QIcon:
    """Return a QIcon with the hollow navigation-arrow cursor (maps-style).

    Uses two pixmaps: Normal (blue) and Disabled (dim gray) so Qt swaps
    automatically when the button is enabled/disabled.
    """
    # feather-style hollow navigation arrow — matches the Google/Apple Maps icon
    _SVG = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="3 11 22 2 13 21 11 13"'
        ' fill="none" stroke="{color}" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )

    def _pixmap(color: str) -> QPixmap:
        svg_bytes = _SVG.format(color=color).encode()
        renderer = QSvgRenderer(svg_bytes)
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        renderer.render(painter)
        painter.end()
        return px

    icon = QIcon()
    icon.addPixmap(_pixmap("#4A9EFF"), QIcon.Mode.Normal,   QIcon.State.Off)
    icon.addPixmap(_pixmap("#3A3F52"), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


_LOC_ICON = None   # built once on first use to avoid constructing before QApp


def _loc_icon() -> QIcon:
    global _LOC_ICON
    if _LOC_ICON is None:
        _LOC_ICON = _make_loc_icon()
    return _LOC_ICON


class RoutingControls(QWidget):
    """Floating drawer for route calculation and turn-by-turn display.

    Signals:
        enter_pick_mode()              — either pick button was activated
        cancel_pick_mode()             — pick mode was canceled
        route_calculated(RouteResult)  — a route was successfully fetched
        route_cleared()                — user cleared the route
    """

    enter_pick_mode   = pyqtSignal()
    cancel_pick_mode  = pyqtSignal()
    route_calculated  = pyqtSignal(object)   # RouteResult
    route_cleared     = pyqtSignal()
    content_resized   = pyqtSignal()         # triggers layout pulse in main_window
    nav_updated       = pyqtSignal(str, str) # (step_text, summary_text) → NavPill

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation         = None
        self._fetcher           = RoutingFetcher()

        # known vehicles: {vehicle_id: (lat, lon)}
        self._vehicles: dict[str, tuple[float, float]] = {}

        # last GPS fix received — None until a fix arrives
        self._gps_lat: float | None = None
        self._gps_lon: float | None = None

        # resolved coordinates used for routing
        self._own_lat: float | None = None
        self._own_lon: float | None = None
        self._dest_lat: float | None = None
        self._dest_lon: float | None = None

        self._geocoding_origin  = False
        self._geocoding_dest    = False
        self._last_result: RouteResult | None = None
        self._route_steps: list = []      # full step list with location data
        self._first_unfinished  = 0       # index of the step at top of list

        # pass-through tracking
        self._min_maneuver_dist: float = float("inf")

        # off-route tracking
        self._off_route_count: int = 0
        self._rerouting: bool = False

        # route fetch in-flight guard (prevents duplicate concurrent requests)
        self._fetching_route: bool = False

        # "origin" while picking start, "dest" while picking destination, "" = idle
        self._pick_mode: str = ""

        self._setup_ui()
        self._connect_fetcher()


    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("routingDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        origin_row = QWidget()
        ori = QHBoxLayout(origin_row)
        ori.setContentsMargins(0, 0, 0, 0)
        ori.setSpacing(4)

        ori_lbl = QLabel("FROM")
        ori_lbl.setFixedWidth(36)
        ori_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        ori_lbl.setStyleSheet("color: #6E7A8F; font-size: 9px; font-weight: 700; "
                              "letter-spacing: 0.8px; background: transparent;")

        self._origin_input = QLineEdit()
        self._origin_input.setPlaceholderText("Address, lat,lon, or pick on map…")
        self._origin_input.returnPressed.connect(self._on_origin_enter)
        self._origin_input.textEdited.connect(self._on_origin_text_edited)
        self._origin_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        # map-pick button for origin
        self._btn_origin_pick = QToolButton()
        self._btn_origin_pick.setText("⊕")
        self._btn_origin_pick.setToolTip("Pick start on map")
        self._btn_origin_pick.setCheckable(True)
        self._btn_origin_pick.setFixedSize(24, 24)
        self._btn_origin_pick.toggled.connect(self._on_origin_pick_toggled)

        # current-location button for origin — enabled only when a GPS fix exists
        self._btn_origin_loc = QToolButton()
        self._btn_origin_loc.setIcon(_loc_icon())
        self._btn_origin_loc.setIconSize(QSize(14, 14))
        self._btn_origin_loc.setToolTip("Start at GPS location")
        self._btn_origin_loc.setObjectName("locBtn")
        self._btn_origin_loc.setFixedSize(24, 24)
        self._btn_origin_loc.setEnabled(False)
        self._btn_origin_loc.clicked.connect(self._set_origin_to_gps)

        # vehicle-picker button for origin — enabled when vehicles are known
        self._btn_origin_vehicle = QToolButton()
        self._btn_origin_vehicle.setIcon(_vehicle_icon())
        self._btn_origin_vehicle.setIconSize(QSize(14, 14))
        self._btn_origin_vehicle.setToolTip("Start at vehicle")
        self._btn_origin_vehicle.setObjectName("locBtn")
        self._btn_origin_vehicle.setFixedSize(24, 24)
        self._btn_origin_vehicle.setEnabled(False)
        self._btn_origin_vehicle.clicked.connect(self._show_vehicle_menu_origin)

        ori.addWidget(ori_lbl)
        ori.addWidget(self._origin_input)
        ori.addWidget(self._btn_origin_pick)
        ori.addWidget(self._btn_origin_loc)
        ori.addWidget(self._btn_origin_vehicle)
        col.addWidget(origin_row)

        dest_row = QWidget()
        dst = QHBoxLayout(dest_row)
        dst.setContentsMargins(0, 0, 0, 0)
        dst.setSpacing(4)

        dest_lbl = QLabel("TO")
        dest_lbl.setFixedWidth(36)
        dest_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        dest_lbl.setStyleSheet("color: #6E7A8F; font-size: 9px; font-weight: 700; "
                               "letter-spacing: 0.8px; background: transparent;")

        self._dest_input = QLineEdit()
        self._dest_input.setPlaceholderText("Address or city…")
        self._dest_input.returnPressed.connect(self._on_dest_enter)
        self._dest_input.textEdited.connect(self._on_dest_text_edited)
        self._dest_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._btn_dest_pick = QToolButton()
        self._btn_dest_pick.setText("⊕")
        self._btn_dest_pick.setToolTip("Pick destination on map")
        self._btn_dest_pick.setCheckable(True)
        self._btn_dest_pick.setFixedSize(24, 24)
        self._btn_dest_pick.toggled.connect(self._on_dest_pick_toggled)

        # current-location button for destination — enabled only when a GPS fix exists
        self._btn_dest_loc = QToolButton()
        self._btn_dest_loc.setIcon(_loc_icon())
        self._btn_dest_loc.setIconSize(QSize(14, 14))
        self._btn_dest_loc.setToolTip("Destination: GPS location")
        self._btn_dest_loc.setObjectName("locBtn")
        self._btn_dest_loc.setFixedSize(24, 24)
        self._btn_dest_loc.setEnabled(False)
        self._btn_dest_loc.clicked.connect(self._set_dest_to_gps)

        # vehicle-picker button for destination — enabled when vehicles are known
        self._btn_dest_vehicle = QToolButton()
        self._btn_dest_vehicle.setIcon(_vehicle_icon())
        self._btn_dest_vehicle.setIconSize(QSize(14, 14))
        self._btn_dest_vehicle.setToolTip("Destination: vehicle")
        self._btn_dest_vehicle.setObjectName("locBtn")
        self._btn_dest_vehicle.setFixedSize(24, 24)
        self._btn_dest_vehicle.setEnabled(False)
        self._btn_dest_vehicle.clicked.connect(self._show_vehicle_menu_dest)

        dst.addWidget(dest_lbl)
        dst.addWidget(self._dest_input)
        dst.addWidget(self._btn_dest_pick)
        dst.addWidget(self._btn_dest_loc)
        dst.addWidget(self._btn_dest_vehicle)
        col.addWidget(dest_row)

        action_row = QWidget()
        act = QHBoxLayout(action_row)
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(4)

        self._btn_go = QToolButton()
        self._btn_go.setText("GET DIRECTIONS")
        self._btn_go.setEnabled(False)
        self._btn_go.clicked.connect(self._on_get_directions)

        self._btn_clear = QToolButton()
        self._btn_clear.setText("CLEAR")
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._on_clear)

        # copy ETA — useful for monitor operators to paste into comms
        self._btn_copy_eta = QToolButton()
        self._btn_copy_eta.setText("COPY ETA")
        self._btn_copy_eta.setToolTip("Copy route summary")
        self._btn_copy_eta.setVisible(False)
        self._btn_copy_eta.clicked.connect(self._on_copy_eta)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "color: #9DA6B8; font-size: 10px; background: transparent; padding: 0 4px;"
        )
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        act.addWidget(self._btn_go)
        act.addWidget(self._btn_clear)
        act.addWidget(self._btn_copy_eta)
        act.addWidget(self._status_label)
        act.addStretch(1)
        col.addWidget(action_row)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "color: #4A9EFF; font-size: 11px; font-weight: 600; "
            "background: transparent; padding: 1px 40px 1px 40px;"
        )
        self._summary_label.setVisible(False)
        col.addWidget(self._summary_label)

        self._steps_list = QListWidget()
        self._steps_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; "
            "  padding: 0 0 0 36px; }"
            "QListWidget::item { color: #C8D0DE; font-size: 11px; "
            "  padding: 2px 2px 2px 0; background: transparent; border: none; }"
            "QListWidget::item:selected { background: rgba(74,158,255,0.12); "
            "  border-radius: 4px; }"
        )
        self._steps_list.setMaximumHeight(180)
        self._steps_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._steps_list.setVisible(False)
        col.addWidget(self._steps_list)

        outer.addWidget(self._drawer)

    def _connect_fetcher(self):
        self._fetcher.route_ready.connect(self._on_route_ready)
        self._fetcher.geocode_ready.connect(self._on_geocode_ready)
        self._fetcher.fetch_error.connect(self._on_fetch_error)


    def toggle_drawer(self, checked: bool):
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


    def update_own_position(self, lat: float, lon: float):
        """Called by MainWindow whenever the own vehicle's GPS position updates.

        Stores the fix silently and enables the ⌖ buttons — neither field is
        auto-populated so the user stays in full control.
        """
        self._gps_lat = lat
        self._gps_lon = lon
        self._btn_origin_loc.setEnabled(True)
        self._btn_dest_loc.setEnabled(True)
        self._check_step_advance(lat, lon)

    def update_vehicles(self, vehicles: dict):
        """Called by MainWindow whenever the tracked-vehicle dict changes.

        Stores a snapshot of {id: (lat, lon)} and enables/disables the
        vehicle-picker buttons accordingly.
        """
        self._vehicles = {vid: (v.lat, v.lon) for vid, v in vehicles.items()
                         if v.lat is not None and v.lon is not None}
        has = bool(self._vehicles)
        self._btn_origin_vehicle.setEnabled(has)
        self._btn_dest_vehicle.setEnabled(has)

    def _show_vehicle_menu_origin(self):
        """Pop up a menu of known vehicles and set the FROM field on selection."""
        if not self._vehicles:
            return
        menu = QMenu(self)
        for vid, (lat, lon) in sorted(self._vehicles.items()):
            action = menu.addAction(f"{vid}  ({lat:.4f}, {lon:.4f})")
            action.setData((vid, lat, lon))
        chosen = menu.exec(self._btn_origin_vehicle.mapToGlobal(
            self._btn_origin_vehicle.rect().bottomLeft()
        ))
        if chosen is not None:
            vid, lat, lon = chosen.data()
            self._own_lat = lat
            self._own_lon = lon
            self._origin_input.blockSignals(True)
            self._origin_input.setText(vid)
            self._origin_input.setStyleSheet(_STYLE_GPS)
            self._origin_input.blockSignals(False)
            self._update_go_enabled()

    def _show_vehicle_menu_dest(self):
        """Pop up a menu of known vehicles and set the TO field on selection."""
        if not self._vehicles:
            return
        menu = QMenu(self)
        for vid, (lat, lon) in sorted(self._vehicles.items()):
            action = menu.addAction(f"{vid}  ({lat:.4f}, {lon:.4f})")
            action.setData((vid, lat, lon))
        chosen = menu.exec(self._btn_dest_vehicle.mapToGlobal(
            self._btn_dest_vehicle.rect().bottomLeft()
        ))
        if chosen is not None:
            vid, lat, lon = chosen.data()
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.blockSignals(True)
            self._dest_input.setText(vid)
            self._dest_input.setStyleSheet(_STYLE_GPS)
            self._dest_input.blockSignals(False)
            self._update_go_enabled()
            if self._have_origin():
                self._on_get_directions()

    def on_map_pick(self, lat: float, lon: float):
        """Called by MainWindow when the user clicks the map in pick mode."""
        if self._pick_mode == "origin":
            self._pick_mode = ""
            self._own_lat = lat
            self._own_lon = lon
            self._origin_input.setText(f"{lat:.5f}, {lon:.5f}")
            self._origin_input.setStyleSheet(_STYLE_MANUAL)
            self._btn_origin_pick.blockSignals(True)
            self._btn_origin_pick.setChecked(False)
            self._btn_origin_pick.blockSignals(False)
            self._update_go_enabled()
            if self._have_dest():
                self._on_get_directions()

        elif self._pick_mode == "dest":
            self._pick_mode = ""
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.setText(f"{lat:.5f}, {lon:.5f}")
            self._dest_input.setStyleSheet(_STYLE_MANUAL)
            self._btn_dest_pick.blockSignals(True)
            self._btn_dest_pick.setChecked(False)
            self._btn_dest_pick.blockSignals(False)
            self._update_go_enabled()
            if self._have_origin_text():
                self._on_get_directions()

    def route_to_pin(self, lat: float, lon: float, label: str = ""):
        """Route from the current GPS fix to a private pin."""
        if self._gps_lat is None or self._gps_lon is None:
            self._set_status("GPS fix required")
            return
        self._own_lat = self._gps_lat
        self._own_lon = self._gps_lon
        self._dest_lat = lat
        self._dest_lon = lon

        dest_text = (label or "").strip()
        if not dest_text or dest_text == "Private pin":
            dest_text = f"{lat:.5f}, {lon:.5f}"

        self._origin_input.blockSignals(True)
        self._origin_input.setText(f"{self._gps_lat:.5f}, {self._gps_lon:.5f}")
        self._origin_input.setStyleSheet(_STYLE_GPS)
        self._origin_input.blockSignals(False)

        self._dest_input.blockSignals(True)
        self._dest_input.setText(dest_text[:60] + ("..." if len(dest_text) > 60 else ""))
        self._dest_input.setStyleSheet(_STYLE_MANUAL)
        self._dest_input.blockSignals(False)

        self._update_go_enabled()
        self._on_get_directions()


    def _on_origin_text_edited(self):
        """User manually typed in the origin field — clear resolved coords."""
        self._own_lat = None
        self._own_lon = None
        self._origin_input.setStyleSheet(_STYLE_MANUAL)
        self._update_go_enabled()

    def _on_origin_enter(self):
        text = self._origin_input.text().strip()
        if not text:
            return
        # try "lat, lon" parse first
        try:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) == 2:
                lat, lon = float(parts[0]), float(parts[1])
                self._apply_resolved_origin(text, lat, lon)
                return
        except ValueError:
            pass
        # otherwise geocode
        self._set_status("Geocoding start…")
        self._geocoding_origin = True
        self._fetcher.geocode(text, is_origin=True)

    def _apply_resolved_origin(self, display: str, lat: float, lon: float):
        self._own_lat = lat
        self._own_lon = lon
        short = display[:50] + ("…" if len(display) > 50 else "")
        self._origin_input.blockSignals(True)
        self._origin_input.setText(short)
        self._origin_input.blockSignals(False)
        self._set_status("")
        self._update_go_enabled()

    def _set_origin_to_gps(self):
        """Fill the FROM field with the current GPS fix."""
        if self._gps_lat is None:
            return
        self._own_lat = self._gps_lat
        self._own_lon = self._gps_lon
        self._origin_input.blockSignals(True)
        self._origin_input.setText(f"{self._gps_lat:.5f}, {self._gps_lon:.5f}")
        self._origin_input.setStyleSheet(_STYLE_GPS)
        self._origin_input.blockSignals(False)
        self._update_go_enabled()


    def _on_dest_text_edited(self):
        """User manually typed in the destination field — clear resolved coords."""
        self._dest_lat = None
        self._dest_lon = None
        self._update_go_enabled()

    def _on_dest_enter(self):
        self._dest_lat = None
        self._dest_lon = None
        self._update_go_enabled()
        if self._dest_input.text().strip():
            self._on_get_directions()

    def _set_dest_to_gps(self):
        """Fill the TO field with the current GPS fix."""
        if self._gps_lat is None:
            return
        self._dest_lat = self._gps_lat
        self._dest_lon = self._gps_lon
        self._dest_input.blockSignals(True)
        self._dest_input.setText(f"{self._gps_lat:.5f}, {self._gps_lon:.5f}")
        self._dest_input.setStyleSheet(_STYLE_GPS)
        self._dest_input.blockSignals(False)
        self._update_go_enabled()


    def _on_origin_pick_toggled(self, checked: bool):
        if checked:
            # cancel dest pick if active
            if self._pick_mode == "dest":
                self._btn_dest_pick.blockSignals(True)
                self._btn_dest_pick.setChecked(False)
                self._btn_dest_pick.blockSignals(False)
            self._pick_mode = "origin"
            self.enter_pick_mode.emit()
        else:
            if self._pick_mode == "origin":
                self._pick_mode = ""
                self.cancel_pick_mode.emit()

    def _on_dest_pick_toggled(self, checked: bool):
        if checked:
            # cancel origin pick if active
            if self._pick_mode == "origin":
                self._btn_origin_pick.blockSignals(True)
                self._btn_origin_pick.setChecked(False)
                self._btn_origin_pick.blockSignals(False)
            self._pick_mode = "dest"
            self.enter_pick_mode.emit()
        else:
            if self._pick_mode == "dest":
                self._pick_mode = ""
                self.cancel_pick_mode.emit()


    def _have_origin_text(self) -> bool:
        return bool(self._origin_input.text().strip())

    def _have_origin(self) -> bool:
        return self._own_lat is not None and self._own_lon is not None

    def _have_dest(self) -> bool:
        return (self._dest_lat is not None) or bool(self._dest_input.text().strip())

    def _update_go_enabled(self):
        has_origin = self._have_origin() or self._have_origin_text()
        has_dest   = self._have_dest()
        self._btn_go.setEnabled(has_origin and has_dest)

    def _on_get_directions(self):
        if not self._have_origin():
            origin_text = self._origin_input.text().strip()
            if not origin_text:
                self._set_status("Enter a starting point")
                return
            # try lat,lon parse
            try:
                parts = [p.strip() for p in origin_text.split(",")]
                if len(parts) == 2:
                    lat, lon = float(parts[0]), float(parts[1])
                    self._apply_resolved_origin(origin_text, lat, lon)
                    self._on_get_directions()   # recurse now that origin is resolved
                    return
            except ValueError:
                pass
            # geocode origin; route will continue in _on_geocode_ready
            self._set_status("Geocoding start…")
            self._btn_go.setEnabled(False)
            self._geocoding_origin = True
            self._fetcher.geocode(origin_text, is_origin=True)
            return

        address = self._dest_input.text().strip()
        if not address and self._dest_lat is None:
            self._set_status("Enter a destination")
            return

        if self._dest_lat is not None:
            self._start_route_fetch()
        else:
            self._set_status("Geocoding destination…")
            self._btn_go.setEnabled(False)
            self._geocoding_dest = True
            self._fetcher.geocode(address, is_origin=False)

    def _start_route_fetch(self):
        if self._fetching_route:
            return
        self._fetching_route = True
        self._set_status("Fetching route…")
        self._btn_go.setEnabled(False)
        self._fetcher.fetch_route(
            self._own_lat, self._own_lon,
            self._dest_lat, self._dest_lon,
        )


    def _on_geocode_ready(self, name: str, lat: float, lon: float, is_origin: bool):
        if is_origin and self._geocoding_origin:
            self._geocoding_origin = False
            self._apply_resolved_origin(name, lat, lon)
            # continue: resolve destination then fetch
            if self._dest_lat is not None:
                self._start_route_fetch()
            else:
                dest_text = self._dest_input.text().strip()
                if dest_text:
                    self._set_status("Geocoding destination…")
                    self._geocoding_dest = True
                    self._fetcher.geocode(dest_text, is_origin=False)
        elif not is_origin and self._geocoding_dest:
            self._geocoding_dest = False
            self._dest_lat = lat
            self._dest_lon = lon
            self._dest_input.blockSignals(True)
            self._dest_input.setText(name[:60] + ("…" if len(name) > 60 else ""))
            self._dest_input.blockSignals(False)
            self._start_route_fetch()

    def _on_route_ready(self, result: RouteResult):
        self._last_result = result
        self._rerouting = False
        self._fetching_route = False
        self._btn_go.setEnabled(True)
        self._set_status("")
        self._btn_clear.setEnabled(True)
        self._btn_copy_eta.setVisible(True)
        self._populate_steps(result)
        self.route_calculated.emit(result)
        self._emit_nav_state()
        self._resize_for_content()

    def _on_fetch_error(self, msg: str):
        self._geocoding_origin = False
        self._geocoding_dest   = False
        self._fetching_route   = False
        self._rerouting        = False
        self._update_go_enabled()
        self._set_status(msg[:60])

    def _on_clear(self):
        self._dest_lat          = None
        self._dest_lon          = None
        self._last_result       = None
        self._route_steps       = []
        self._first_unfinished  = 0
        self._min_maneuver_dist = float("inf")
        self._off_route_count   = 0
        self._rerouting         = False
        self._geocoding_origin  = False
        self._geocoding_dest    = False
        self._fetching_route    = False
        self._dest_input.clear()
        self._dest_input.setStyleSheet(_STYLE_MANUAL)
        self._summary_label.setVisible(False)
        self._steps_list.clear()
        self._steps_list.setVisible(False)
        self._btn_clear.setEnabled(False)
        self._btn_copy_eta.setVisible(False)
        # cancel any active pick
        for btn in (self._btn_origin_pick, self._btn_dest_pick):
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._pick_mode = ""
        self._set_status("")
        self._update_go_enabled()
        self.route_cleared.emit()
        # only animate the height change when the drawer is fully open.
        if self.maximumHeight() == 16777215:
            self._resize_for_content()
        else:
            self.content_resized.emit()


    def _populate_steps(self, result: RouteResult):
        self._route_steps      = list(result.steps)
        self._first_unfinished = 0
        self._min_maneuver_dist = float("inf")
        self._off_route_count  = 0
        self._steps_list.clear()
        for step in result.steps:
            dist_str = fmt_distance(step.distance_m) if step.distance_m > 0 else ""
            text     = f"{step.icon}  {step.instruction}"
            if dist_str:
                text += f"  —  {dist_str}"
            self._steps_list.addItem(QListWidgetItem(text))

        total_dist = fmt_distance(result.distance_m)
        total_time = fmt_duration(result.duration_s)
        self._summary_label.setText(f"{total_dist}  ·  {total_time}")
        self._summary_label.setVisible(True)
        self._steps_list.setVisible(True)

    def _dist_to_route(self, lat: float, lon: float) -> float:
        """Minimum distance in metres from (lat, lon) to any coordinate in the route geometry."""
        if self._last_result is None:
            return 0.0
        coords = self._last_result.geometry.get("coordinates", [])
        if not coords:
            return 0.0
        return min(_haversine_m(lat, lon, c[1], c[0]) for c in coords)

    def _check_step_advance(self, lat: float, lon: float):
        """Advance steps and handle off-route / arrival on every GPS fix."""
        if not self._route_steps:
            return

        if self._steps_list.count() == 1:
            arrive_loc = self._route_steps[-1].location
            dist_to_dest = _haversine_m(lat, lon, arrive_loc[0], arrive_loc[1])
            step = self._route_steps[self._first_unfinished]
            item = self._steps_list.item(0)
            if item:
                item.setText(f"{step.icon}  {step.instruction}  —  {fmt_distance(dist_to_dest)}")
            self._emit_nav_state()
            if dist_to_dest < _ARRIVE_M:
                self._steps_list.takeItem(0)
                self.nav_updated.emit("You have arrived.", "")
                self._set_status("Arrived!")
                QTimer.singleShot(5000, self._on_clear)
            return

        next_idx = self._first_unfinished + 1
        if next_idx >= len(self._route_steps):
            return
        next_loc = self._route_steps[next_idx].location
        dist = _haversine_m(lat, lon, next_loc[0], next_loc[1])

        # update live distance in the first list item and pill
        step = self._route_steps[self._first_unfinished]
        item = self._steps_list.item(0)
        if item:
            item.setText(f"{step.icon}  {step.instruction}  —  {fmt_distance(dist)}")
        self._emit_nav_state()

        if not self._rerouting and self._last_result is not None:
            if self._dist_to_route(lat, lon) > _OFF_ROUTE_M:
                self._off_route_count += 1
                if self._off_route_count >= _OFF_ROUTE_COUNT:
                    self._off_route_count = 0
                    self._rerouting = True
                    self._own_lat = lat
                    self._own_lon = lon
                    self._origin_input.blockSignals(True)
                    self._origin_input.setText(f"{lat:.5f}, {lon:.5f}")
                    self._origin_input.setStyleSheet(_STYLE_GPS)
                    self._origin_input.blockSignals(False)
                    self._set_status("Off route — recalculating…")
                    self._start_route_fetch()
                    return
            else:
                self._off_route_count = 0

        if dist < self._min_maneuver_dist:
            self._min_maneuver_dist = dist

        if self._min_maneuver_dist < _CLOSE_M and dist > self._min_maneuver_dist + _PASSED_M:
            self._steps_list.takeItem(0)
            self._first_unfinished += 1
            self._min_maneuver_dist = float("inf")
            self._off_route_count   = 0
            self._emit_nav_state()
            self._resize_for_content()

    def _on_copy_eta(self):
        if self._last_result is None:
            return
        dest = self._dest_input.text().strip()
        if not dest and self._dest_lat is not None:
            dest = f"{self._dest_lat:.4f}, {self._dest_lon:.4f}"
        summary  = self._summary_label.text()
        clip_text = f"Route to {dest}: {summary}" if dest else summary
        QApplication.clipboard().setText(clip_text)
        self._set_status("Copied!")
        QTimer.singleShot(2000, lambda: self._set_status(""))

    def _set_status(self, msg: str):
        self._status_label.setText(msg)

    def _emit_nav_state(self):
        """Push current step + summary to the NavPill via nav_updated signal."""
        if self._steps_list.count() == 0 or not self._summary_label.isVisible():
            return
        item = self._steps_list.item(0)
        step_text = item.text() if item else "Navigating…"
        self.nav_updated.emit(step_text, self._summary_label.text())

    def _resize_for_content(self):
        """Animate to new natural height after content changes."""
        if self.maximumHeight() <= 0:
            return
        self.setMaximumHeight(16777215)
        target  = self.sizeHint().height()
        current = self.height()
        if target != current:
            if self._animation:
                self._animation.stop()
            anim = QPropertyAnimation(self, b"maximumHeight")
            anim.setDuration(150)
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
            anim.start()
            self._animation = anim
        # always notify so _start_layout_pulse runs and corrects geometry.
        self.content_resized.emit()
