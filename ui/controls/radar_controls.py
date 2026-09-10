
import math
import re
import threading
import xml.etree.ElementTree as ET
from urllib.request import urlopen

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QToolButton, QLabel,
    QCheckBox, QSlider, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, Qt


NEXRAD_SITES = [
    ("KTLX", "Oklahoma City, OK", 35.333, -97.278),
    ("KOUN", "Norman, OK (Research)", 35.236, -97.463),
    ("KVNX", "Enid/Vance, OK", 36.741, -98.127),
    ("KFDR", "Frederick, OK", 34.362, -98.976),
    ("KINX", "Tulsa, OK", 36.175, -95.565),
    ("KSRX", "Ft. Smith, AR", 35.291, -94.362),
    ("KDDC", "Dodge City, KS", 37.760, -99.969),
    ("KICT", "Wichita, KS", 37.655, -97.443),
    ("KTWX", "Topeka, KS", 38.996, -96.232),
    ("KEAX", "Kansas City, MO", 38.810, -94.264),
    ("KSGF", "Springfield, MO", 37.235, -93.401),
    ("KLSX", "St. Louis, MO", 38.699, -90.683),
    ("KLZK", "Little Rock, AR", 34.836, -92.262),
    ("KAMA", "Amarillo, TX", 35.233, -101.709),
    ("KFWS", "Dallas/Ft. Worth, TX", 32.573, -97.303),
    ("KDYX", "Dyess AFB, TX", 32.539, -99.254),
    ("KSHV", "Shreveport, LA", 32.451, -93.841),
    ("KLBB", "Lubbock, TX", 33.654, -101.814),
    ("KMAF", "Midland, TX", 31.943, -102.189),
    ("KABR", "Aberdeen, SD", 45.455, -98.413),
    ("KUDX", "Rapid City, SD", 44.125, -102.830),
    ("KLNX", "North Platte, NE", 41.958, -100.576),
    ("KOAX", "Omaha, NE", 41.320, -96.366),
    ("KUEX", "Grand Island, NE", 40.321, -98.442),
    ("KGLD", "Goodland, KS", 39.366, -101.700),
    ("KPUX", "Pueblo, CO", 38.460, -104.181),
    ("KFTG", "Denver, CO", 39.786, -104.545),
    ("KCYS", "Cheyenne, WY", 41.152, -104.806),
    ("KRIW", "Riverton, WY", 43.066, -108.477),
    ("KBIS", "Bismarck, ND", 46.771, -100.761),
    ("KMBX", "Minot, ND", 48.393, -100.864),
    ("KFSD", "Sioux Falls, SD", 43.588, -96.728),
    ("KDVN", "Davenport, IA", 41.611, -90.581),
    ("KDMX", "Des Moines, IA", 41.731, -93.723),
    # illinois
    ("KILX", "Lincoln/Central IL", 40.150, -89.336),
    ("KLOT", "Chicago/NE IL", 41.604, -88.085),
    # indiana
    ("KIND", "Indianapolis, IN", 39.707, -86.280),
    ("KIWX", "Northern Indiana", 41.359, -85.700),
    # tennessee
    ("KOHX", "Nashville, TN", 36.247, -86.563),
    ("KNQA", "Memphis, TN", 35.345, -89.874),
    ("KMRX", "Knoxville/E TN", 36.168, -83.402),
    # alabama
    ("KBMX", "Birmingham, AL", 33.172, -86.770),
    ("KHTX", "Huntsville/N AL", 34.930, -86.083),
    ("KMOB", "Mobile, AL", 30.679, -88.240),
    # mississippi
    ("KDGX", "Jackson/Central MS", 32.280, -89.984),
    ("KGWX", "Columbus/NE MS", 33.897, -88.329),
    # new Mexico
    ("KABX", "Albuquerque, NM", 35.150, -106.823),
    ("KFDX", "Clovis/E NM", 34.634, -103.629),
    ("KHDX", "Holloman AFB, NM", 33.076, -106.122),
    # minnesota
    ("KMPX", "Minneapolis-St. Paul, MN", 44.849, -93.565),
    ("KDLH", "Duluth, MN", 46.837, -92.210),
    # montana
    ("KBLX", "Billings, MT", 45.854, -108.607),
    ("KTFX", "Great Falls, MT", 47.460, -111.385),
    ("KMSX", "Missoula, MT", 47.041, -113.986),
]

PRODUCTS = [("N0B", "REFLECTIVITY (SR)"), ("N0U", "VELOCITY")]
OPTIONAL_PRODUCTS = [("N0C", "CORR COEFF"), ("N0K", "SPEC DIFF PHASE")]
ALL_PRODUCTS = PRODUCTS + OPTIONAL_PRODUCTS
THREDDS_CATALOG_ROOT = "https://thredds.ucar.edu/thredds/catalog/nexrad/level3"
class RadarControls(QWidget):
    """
    Toolbar widget containing:
      - RADAR toggle button — slides the two-row control drawer in/out
      - collapsible two-row drawer:
          Row 1: stations button | product selector | show data checkbox | stretch
          Row 2: ⏮ ⏪ ▶/⏸ ⏩ ⏭  +  expanding timeline slider  +  time label

    Signals:
        radar_toggled(bool)    — data fetch enabled/disabled (from show data checkbox)
        site_changed(str)      — new site ID selected
        stations_requested()   — toggle/open the map station picker overlay
        product_changed(str)   — selected product code (e.g. "N0Q")
        fetch_requested()      — trigger immediate fetch
        frame_requested(int)   — user selected a specific cache frame index
        loop_toggled(bool)     — loop playback started/stopped
    """

    radar_toggled   = pyqtSignal(bool)
    site_changed    = pyqtSignal(str)
    stations_requested = pyqtSignal()
    product_changed = pyqtSignal(str)
    tilt_changed    = pyqtSignal(int)
    fetch_requested = pyqtSignal()
    frame_requested = pyqtSignal(int)
    loop_toggled    = pyqtSignal(bool)
    speed_changed   = pyqtSignal(int)      # new interval in ms
    vad_requested   = pyqtSignal()         # VAD hodograph dialog requested

    # internal — emitted from the _refresh_product_availability background thread
    _products_refreshed = pyqtSignal(dict)  # {code: bool} availability map
    products_available_changed = pyqtSignal(list)
    product_availability_changed = pyqtSignal(dict)  # {code: bool} for current site

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radar_on           = False
        self._product            = "N0B"
        self._archive_mode       = False
        self._product_availability: dict[tuple[str, str], bool] = {}
        self._site               = "KTLX"
        self._all_sites          = list(NEXRAD_SITES)
        self._animation          = None   # hold ref to prevent GC during animation
        self._expanded_height    = 0
        self._setup_ui()
        self._products_refreshed.connect(self._apply_product_items)
        self.set_selected_site("KTLX")

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        # starts collapsed — height animates open as a dropdown pill below toolbar.
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("radarDrawer")
        drawer_layout = QVBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(4)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._stations_button = QToolButton()
        self._stations_button.setFixedHeight(22)
        self._stations_button.setMinimumWidth(140)
        self._stations_button.setObjectName("radarStationsButton")
        self._stations_button.setToolTip("Choose radar station")
        self._stations_button.clicked.connect(self.stations_requested.emit)
        r1.addWidget(self._stations_button)

        self._product_combo = QComboBox()
        self._product_combo.setFixedHeight(22)
        self._product_combo.setMinimumWidth(128)
        self._product_combo.setObjectName("radarProductCombo")
        # start with all 4 products; optional ones disabled until THREDDS probe confirms
        self._set_product_items(ALL_PRODUCTS, preserve_code=self._product)
        self._apply_availability_to_combo({"N0B": True, "N0U": True, "N0C": False, "N0K": False})
        self._product_combo.currentIndexChanged.connect(self._on_product_changed)
        r1.addWidget(self._product_combo)

        self._tilt_combo = QComboBox()
        self._tilt_combo.setFixedHeight(22)
        self._tilt_combo.setFixedWidth(74)
        self._tilt_combo.setObjectName("radarTiltCombo")
        self._tilt_combo.setToolTip("Radar tilt angle")
        self._tilt_combo.currentIndexChanged.connect(self._on_tilt_changed)
        self._tilt_combo.setVisible(False)
        r1.addWidget(self._tilt_combo)

        # checkbox sits immediately right of product selector
        self._chk_show_data = QCheckBox("show data")
        self._chk_show_data.setChecked(False)
        self._chk_show_data.setFixedHeight(22)
        self._chk_show_data.setToolTip("Load and show radar")
        self._chk_show_data.toggled.connect(self._on_data_enabled_toggled)
        r1.addWidget(self._chk_show_data)

        self._btn_vad = QToolButton()
        self._btn_vad.setText("VAD")
        self._btn_vad.setFixedHeight(22)
        self._btn_vad.setFixedWidth(48)
        self._btn_vad.setObjectName("radarVadButton")
        self._btn_vad.setToolTip("VAD wind profile")
        self._btn_vad.clicked.connect(self.vad_requested.emit)
        r1.addWidget(self._btn_vad)

        r1.addStretch()

        drawer_layout.addWidget(row1)

        row2 = QWidget()
        row2.setObjectName("radarPlaybackRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = QToolButton()
        self._btn_jump_start.setText("⏮")
        self._btn_jump_start.setFixedSize(32, 26)
        self._btn_jump_start.setEnabled(False)
        self._btn_jump_start.setToolTip("Oldest frame")
        self._btn_jump_start.clicked.connect(self._on_jump_start)
        r2.addWidget(self._btn_jump_start)

        self._btn_back = QToolButton()
        self._btn_back.setText("⏪")
        self._btn_back.setFixedSize(32, 26)
        self._btn_back.setEnabled(False)
        self._btn_back.setToolTip("Previous frame")
        self._btn_back.clicked.connect(self._on_step_back)
        r2.addWidget(self._btn_back)

        self._btn_play = QToolButton()
        self._btn_play.setText("▶")
        self._btn_play.setCheckable(True)
        self._btn_play.setFixedSize(32, 26)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip("Play / Pause loop")
        self._btn_play.toggled.connect(self._on_play_toggled)
        r2.addWidget(self._btn_play)

        self._btn_fwd = QToolButton()
        self._btn_fwd.setText("⏩")
        self._btn_fwd.setFixedSize(32, 26)
        self._btn_fwd.setEnabled(False)
        self._btn_fwd.setToolTip("Next frame")
        self._btn_fwd.clicked.connect(self._on_step_forward)
        r2.addWidget(self._btn_fwd)

        self._btn_jump_end = QToolButton()
        self._btn_jump_end.setText("⏭")
        self._btn_jump_end.setFixedSize(32, 26)
        self._btn_jump_end.setEnabled(False)
        self._btn_jump_end.setToolTip("Latest (live)")
        self._btn_jump_end.clicked.connect(self._on_jump_end)
        r2.addWidget(self._btn_jump_end)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.setFixedHeight(26)
        self._frame_slider.setMinimumWidth(80)   # expand to fill available space
        self._frame_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._frame_slider.setEnabled(False)
        # use sliderReleased instead of valueChanged — avoids re-render on every drag pixel
        self._frame_slider.sliderReleased.connect(self._on_slider_released)
        r2.addWidget(self._frame_slider)

        # time label sits right of slider so it reads: [slider] [23:45Z]
        self._frame_time_label = QLabel("--:--Z")
        self._frame_time_label.setObjectName("radarTimeLabel")
        self._frame_time_label.setFixedHeight(26)
        self._frame_time_label.setMinimumWidth(52)
        r2.addWidget(self._frame_time_label)

        # speed selector — 0.5×, 1×, 2×, 3×
        self._speed_combo = QComboBox()
        self._speed_combo.setFixedHeight(26)
        self._speed_combo.setMaximumWidth(5)
        self._speed_combo.setToolTip("Playback speed")
        for label, ms in [("0.5×", 1000), ("1×", 500), ("2×", 250), ("3×", 167)]:
            self._speed_combo.addItem(label, userData=ms)
        self._speed_combo.setCurrentIndex(1)   # default 1×
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_combo)

        drawer_layout.addWidget(row2)

        layout.addWidget(self._drawer)

        # measure natural height after layout settles, before first collapse
        QTimer.singleShot(0, self._measure_expanded_height)


    def current_site(self) -> str:
        return self._site

    def current_product(self) -> str:
        return self._product

    def set_selected_site(self, site_id: str, emit: bool = False):
        normalized = _normalize_site(site_id) or "KTLX"
        self._site = normalized
        self._stations_button.setText(f"Stations: {normalized}")
        # fetcher always fetches all 4; availability only drives combo disabled state
        self.products_available_changed.emit([code for code, _ in ALL_PRODUCTS])
        # apply any cached availability immediately so combo disables stale unavailable items
        cached = {
            code: self._product_availability.get((normalized, code), True)
            for code, _ in ALL_PRODUCTS
        }
        # n0B/N0U are always assumed available (never probed)
        cached["N0B"] = True
        cached["N0U"] = True
        self._apply_availability_to_combo(cached)
        self.product_availability_changed.emit(dict(cached))
        _site = normalized
        threading.Thread(
            target=self._refresh_product_availability,
            args=(_site,),
            daemon=True,
        ).start()
        if emit:
            self.site_changed.emit(normalized)
            if self.is_data_enabled():
                self.fetch_requested.emit()

    def set_scan_time(self, time_str: str):
        # update the time label next to the slider
        self._frame_time_label.setText(time_str)

    def is_data_enabled(self) -> bool:
        """Return whether radar data fetch/display is enabled."""
        return self._chk_show_data.isChecked()

    def _set_product_items(self, items: list[tuple[str, str]], preserve_code: str | None = None):
        self._product_combo.blockSignals(True)
        self._product_combo.clear()
        for code, label in items:
            self._product_combo.addItem(label, userData=code)
        if preserve_code:
            idx = self._product_combo.findData(preserve_code)
            if idx >= 0:
                self._product_combo.setCurrentIndex(idx)
            else:
                self._product_combo.setCurrentIndex(0)
        else:
            self._product_combo.setCurrentIndex(0)
        self._product_combo.blockSignals(False)
        # sync internal product state to the combo selection
        current_code = self._product_combo.currentData() or ""
        self._product = current_code

    def set_cache_size(self, n: int):
        """update slider range as scan cache grows; stay at live if already there."""
        was_live = self.is_at_latest_frame()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, n - 1))
        if was_live:
            self._frame_slider.setValue(n - 1)
        self._frame_slider.blockSignals(False)
        has_history = n > 1
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int):
        """move slider to index without emitting frame_requested."""
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

    def current_frame(self) -> int:
        return self._frame_slider.value()

    def is_at_latest_frame(self) -> bool:
        return self._frame_slider.value() >= self._frame_slider.maximum()

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def stop_loop(self):
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)   # triggers _on_play_toggled(False)

    def reset_cache_ui(self):
        """reset playback controls when site or product changes."""
        self.stop_loop()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(False)

    def configure_for_archive(self, enabled: bool) -> None:
        self._archive_mode = enabled
        self._chk_show_data.setVisible(not enabled)
        playback_row = self._drawer.layout().itemAt(1).widget()
        if playback_row is not None:
            playback_row.setVisible(not enabled)
        self._tilt_combo.setVisible(enabled and self._tilt_combo.count() > 0)

    def set_archive_products(self, products: list[tuple[str, str]]) -> None:
        if not products:
            return
        current = self._product if self._archive_mode else None
        self._set_product_items(products, preserve_code=current or products[0][0])

    def set_archive_tilts(self, tilts: list[float], current_index: int | None = None) -> None:
        self._tilt_combo.blockSignals(True)
        self._tilt_combo.clear()
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        if self._tilt_combo.count():
            idx = current_index if current_index is not None else 0
            idx = max(0, min(idx, self._tilt_combo.count() - 1))
            self._tilt_combo.setCurrentIndex(idx)
        self._tilt_combo.blockSignals(False)
        self._tilt_combo.setVisible(self._archive_mode and self._tilt_combo.count() > 0)


    def _measure_expanded_height(self):
        """store the natural height of RadarControls before it was ever collapsed."""
        self.setMaximumHeight(16777215)
        self._expanded_height = self.sizeHint().height()
        self.setMaximumHeight(0)

    def _refresh_product_availability(self, site_id: str):
        site = _normalize_site(site_id)
        if not site:
            return

        availability: dict[str, bool] = {"N0B": True, "N0U": True}
        for code, _label in OPTIONAL_PRODUCTS:
            avail = self._is_product_available(site, code)
            self._product_availability[(site, code)] = avail
            availability[code] = avail

        # only apply if the user hasn't switched sites since the probe started
        if site != self._site:
            return

        # emit signal instead of QTimer.singleShot — PyQt6 AutoConnection safely
        self._products_refreshed.emit(availability)

    def _apply_product_items(self, availability: dict):
        """Slot — always runs on the main thread via the _products_refreshed signal."""
        self._apply_availability_to_combo(availability)
        self.product_availability_changed.emit(dict(availability))

    def _apply_availability_to_combo(self, availability: dict):
        """Enable/disable combo items based on availability map."""
        model = self._product_combo.model()
        for i in range(self._product_combo.count()):
            code = self._product_combo.itemData(i)
            item = model.item(i)
            if item is None:
                continue
            avail = bool(availability.get(code, True))
            item.setEnabled(avail)
            if not avail:
                item.setToolTip(f"No {code} data at {self._site}")
            else:
                item.setToolTip("")

    def _is_product_available(self, site: str, product: str) -> bool:
        key = (site, product)
        if key in self._product_availability:
            return self._product_availability[key]

        site_token = _thredds_site_token(site)
        url = f"{THREDDS_CATALOG_ROOT}/{product}/{site_token}/catalog.xml"
        try:
            with urlopen(url, timeout=6) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            root = ET.fromstring(xml_text)
            ns = {"cat": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}
            # site catalogs typically contain day subcatalog refs, not dataset
            for ds in root.findall(".//cat:dataset", ns):
                if ds.attrib.get("urlPath"):
                    return True
            for ref in root.findall(".//cat:catalogRef", ns):
                if ref.attrib.get("{http://www.w3.org/1999/xlink}href"):
                    return True
        except Exception:
            return False
        return False


    def toggle_drawer(self, checked: bool):
        """animate RadarControls open or closed — called by the toolbar RADAR button."""
        self._radar_on = checked

        target = self._expanded_height if checked else 0
        if checked:
            current = self.maximumHeight()
            if target == 0:
                # expanded height wasn't measured yet — measure now
                self.setMaximumHeight(16777215)
                target = self.sizeHint().height()
                self.setMaximumHeight(current)
        else:
            # after opening, maximumHeight is 16777215 — animate from actual pixel
            current = self.height()
            self.setMaximumHeight(current)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(self._expanded_height))
        anim.start()
        self._animation = anim   # keep ref alive for duration of animation

    def _on_data_enabled_toggled(self, checked: bool):
        """show data checkbox — controls whether data is actually fetched."""
        self.radar_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_play_toggled(self, checked: bool):
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_product_changed(self, index: int):
        product = self._product_combo.itemData(index)
        if not product:
            return
        self._product = product
        self.product_changed.emit(product)

    def _on_tilt_changed(self, index: int):
        if index >= 0:
            self.tilt_changed.emit(index)

    def _on_slider_released(self):
        # emit frame_requested only when user releases the slider handle
        self.frame_requested.emit(self._frame_slider.value())

    def _on_jump_start(self):
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self):
        n = self._frame_slider.maximum()
        self.set_frame(n)
        self.frame_requested.emit(n)

    def _on_step_back(self):
        # use set_frame + manual emit so we bypass the sliderReleased path consistently
        new_val = max(0, self._frame_slider.value() - 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _on_step_forward(self):
        new_val = min(self._frame_slider.maximum(), self._frame_slider.value() + 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _on_speed_changed(self, index: int):
        ms = self._speed_combo.itemData(index)
        if ms is not None:
            self.speed_changed.emit(ms)


def _normalize_site(site_id: str) -> str:
    text = (site_id or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b[A-Z][A-Z0-9]{3}\b", text)
    return match.group(0) if match else ""


def _thredds_site_token(site: str) -> str:
    site = site.upper()
    if site.startswith("K") and len(site) == 4:
        return site[1:]
    return site


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """great-circle distance in km — used to sort radar sites by proximity."""
    r = 6371.0
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c
