"""RAW LIDAR drawer: choose a lidar the way RADAR chooses a station, and
STORM does the rest -- marks where it was when it started scanning, loads
its files for the session by itself, shows when it was scanning (a strip
under the time slider), and shows each scan as the clock reaches it: PPI/VAD
scans on the map, RHIs and stares in the vertical viewer.

Two kinds of instrument, kept apart and named plainly:
  * the lidar truck (DLTRUCK1) -- a Doppler lidar on a truck that moves; its
    files carry no position, so it is placed from the truck's GPS, and its
    heading is recorded or estimated (archive/fetchers/raw_lidar_archive_fetcher.py);
  * the CLAMPS1 / CLAMPS2 trailers -- each with its own Doppler lidar, parked
    at a site recorded in its files (often at home in Norman, not deployed).
KNOWN_RAW_LIDAR_SOURCES has one entry per (instrument, file type); the user
only ever picks the instrument -- which files hold what is STORM's business.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget,
)

INSTRUMENT_NAMES = {
    "DLTRUCK1": "Lidar truck",
    "CLAMPS1": "CLAMPS1 trailer",
    "CLAMPS2": "CLAMPS2 trailer",
}
FILE_NAMES = {
    "ppi": "Scans (PPI/VAD)",
    "csm": "Continuous scans (CSM)",
    "fp": "Fixed-point stares",
    "other": "Other scans (RHI…)",
}
_CHOOSE = "Choose a lidar…"


def instrument_name(instrument: str) -> str:
    return INSTRUMENT_NAMES.get(instrument, instrument)


class RawLidarControls(QWidget):
    """Signals
    -------
    instrument_selected(str)     the chosen lidar ("" = none)
    field_selected(str)          field to draw
    map_toggled(bool)            draw PPI/VAD scans on the map (on by default)
    radar_visible_toggled(bool)  radar under the lidar on/off
    viewer_requested()           open the vertical viewer (RHIs, stares)
    scan_step_requested(int)     -1 / +1: previous / next scan (moves the clock)
    load_large_requested()       load the big files held back (e.g. a day of stares)
    locate_requested()           center the map on the lidar
    availability_changed(bool)   any lidar data for this date
    """

    instrument_selected = pyqtSignal(str)
    field_selected = pyqtSignal(str)
    map_toggled = pyqtSignal(bool)
    radar_visible_toggled = pyqtSignal(bool)
    viewer_requested = pyqtSignal()
    scan_step_requested = pyqtSignal(int)
    load_large_requested = pyqtSignal()
    locate_requested = pyqtSignal()
    availability_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._availability_status = "Looking for lidar data for this date…"
        self._assets_by_source: dict[str, list] = {}
        self._sources_by_instrument: dict[str, list] = {}
        self._site_text: dict[str, str] = {}
        self._progress = ""
        self._setup_ui()

    # ---- layout -------------------------------------------------------
    def _setup_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("rawLidarDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        def combo(tip, width):
            c = QComboBox()
            c.setObjectName("lidarCombo")
            c.setFixedHeight(22)
            c.setMinimumWidth(width)
            c.setToolTip(tip)
            return c

        def button(text, tip, width=None):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            if width:
                b.setMinimumWidth(width)
            return b

        def check(text, tip, checked):
            c = QCheckBox(text)
            c.setChecked(checked)
            c.setFixedHeight(22)
            c.setToolTip(tip)
            return c

        # ---- row 1: which lidar, which field, how to show it --------------
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)
        self._source_combo = combo("Choose a lidar, like a radar station: STORM marks where it was, "
                                   "loads its scans and shows them as the clock reaches them", 190)
        self._source_combo.addItem(_CHOOSE, "")
        self._source_combo.currentIndexChanged.connect(self._on_instrument_changed)
        r1.addWidget(self._source_combo)
        self._field_combo = combo("Field to show", 130)
        self._field_combo.setEnabled(False)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        r1.addWidget(self._field_combo)
        self._chk_map = check("Map", "Draw the lidar's PPI/VAD scans on the map as the clock reaches them", True)
        self._chk_map.toggled.connect(self._on_map_toggled)
        r1.addWidget(self._chk_map)
        self._chk_radar = check("Radar", "Show the radar under the lidar scans", True)
        self._chk_radar.toggled.connect(self.radar_visible_toggled.emit)
        r1.addWidget(self._chk_radar)
        self._btn_viewer = button("VIEWER", "Open the vertical viewer: RHI cross-sections and stares", 64)
        self._btn_viewer.setEnabled(False)
        self._btn_viewer.clicked.connect(self.viewer_requested.emit)
        r1.addWidget(self._btn_viewer)
        self._btn_locate = button("LOCATE", "Center the map on the lidar", 72)
        self._btn_locate.setEnabled(False)
        self._btn_locate.clicked.connect(self.locate_requested.emit)
        r1.addWidget(self._btn_locate)
        r1.addStretch()
        col.addWidget(row1)

        # ---- row 2: scan by scan, and when it was scanning -----------------
        self._scan_row = QWidget()
        r2 = QHBoxLayout(self._scan_row)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(4)
        self._btn_prev = button("⏮", "Previous scan (moves the clock to it)", 28)
        self._btn_prev.clicked.connect(lambda: self.scan_step_requested.emit(-1))
        r2.addWidget(self._btn_prev)
        self._scan_label = QLabel("")
        self._scan_label.setStyleSheet("color: #E3E8F2; font-size: 11px;")
        r2.addWidget(self._scan_label)
        self._btn_next = button("⏭", "Next scan (moves the clock to it)", 28)
        self._btn_next.clicked.connect(lambda: self.scan_step_requested.emit(1))
        r2.addWidget(self._btn_next)
        r2.addSpacing(8)
        self._scale_label = QLabel("")
        self._scale_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        r2.addWidget(self._scale_label)
        self._btn_large = button("", "Load the lidar's large files too (they are not downloaded without asking)")
        self._btn_large.clicked.connect(self._on_large_clicked)
        self._btn_large.hide()
        r2.addWidget(self._btn_large)
        r2.addStretch()
        self._scan_row.setVisible(False)
        col.addWidget(self._scan_row)

        self._coverage_label = QLabel("")
        self._coverage_label.setWordWrap(True)
        self._coverage_label.setStyleSheet("color: #00CFFF; font-size: 10px;")
        self._coverage_label.hide()
        col.addWidget(self._coverage_label)

        # ---- status: availability, where the lidar was, orientation ------
        self._status_label = QLabel(self._availability_status)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        col.addWidget(self._status_label)
        self._orientation_label = QLabel()
        self._orientation_label.setWordWrap(True)
        self._orientation_label.hide()
        col.addWidget(self._orientation_label)

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

    # ---- which lidars have data ------------------------------------------
    def set_sources(self, sources) -> None:
        """sources: RawLidarSource entries (KNOWN_RAW_LIDAR_SOURCES)."""
        self._sources_by_instrument = {}
        for source in sources:
            self._sources_by_instrument.setdefault(source.instrument, []).append(source)

    def set_assets_for_all_sources(self, assets_by_source: dict) -> None:
        """assets_by_source: platform_id -> list[LidarAsset] (None: couldn't check)."""
        self._assets_by_source = dict(assets_by_source)
        with_data = [i for i, sources in self._sources_by_instrument.items()
                     if any(self._assets_by_source.get(s.platform_id) for s in sources)]
        unresolved = [i for i, sources in self._sources_by_instrument.items()
                      if any(s.platform_id in self._assets_by_source and self._assets_by_source[s.platform_id] is None
                             for s in sources)]
        selected = self.current_instrument()
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        self._source_combo.addItem(_CHOOSE, "")
        for instrument in with_data:
            self._source_combo.addItem(f"{instrument_name(instrument)} ({instrument})", instrument)
        index = self._source_combo.findData(selected) if selected else 0
        self._source_combo.setCurrentIndex(max(0, index))
        self._source_combo.blockSignals(False)
        if with_data:
            message = "Lidar data this date: " + ", ".join(instrument_name(i) for i in with_data)
            if unresolved:
                message += " · some sources could not be checked"
        elif unresolved:
            message = "Could not check every lidar for this date."
        else:
            message = "No lidar data for this date."
        self._availability_status = message
        self._show_status()
        self.availability_changed.emit(bool(with_data))

    def instruments(self) -> list[str]:
        return [self._source_combo.itemData(i) for i in range(1, self._source_combo.count())]

    def current_instrument(self) -> str:
        return self._source_combo.currentData() or ""

    def select_instrument(self, instrument: str) -> None:
        index = self._source_combo.findData(instrument)
        if index > 0:
            self._source_combo.setCurrentIndex(index)

    def assets_for(self, instrument: str) -> list:
        """Every file of this lidar found for the session."""
        return [asset for source in self._sources_by_instrument.get(instrument, [])
                for asset in (self._assets_by_source.get(source.platform_id) or [])]

    def _on_instrument_changed(self, _index: int) -> None:
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        self._field_combo.blockSignals(False)
        self._field_combo.setEnabled(False)
        self._scale_label.clear()
        self._scan_row.setVisible(False)
        self._coverage_label.hide()
        self._btn_large.hide()
        self._btn_viewer.setEnabled(False)
        self._btn_locate.setEnabled(False)
        self._progress = ""
        self.set_orientation_notice(None)
        self._show_status()
        self.instrument_selected.emit(self.current_instrument())

    # ---- status ---------------------------------------------------------------
    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_site_text(self, instrument: str, text: str) -> None:
        """Where an instrument was (e.g. 'at NWC Vehicle Bay (34.982, -97.520), 711 km from KOAX')."""
        self._site_text[instrument] = text
        self._show_status()

    def set_progress(self, text: str) -> None:
        """Loading progress for the chosen lidar ('' when done)."""
        self._progress = text
        self._show_status()

    def _show_status(self) -> None:
        instrument = self.current_instrument()
        if not instrument:
            self.set_status(self._availability_status)
            return
        parts = [f"{instrument_name(instrument)} {self._site_text.get(instrument, '')}".strip()]
        if self._progress:
            parts.append(self._progress)
        self.set_status("  ·  ".join(parts))

    # ---- the chosen lidar's scans --------------------------------------------
    def set_fields(self, fields: dict) -> None:
        """Fields across the lidar's loaded files (name -> units); keeps the choice."""
        current = self._field_combo.currentData()
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name, units in fields.items():
            label = name.replace("_", " ").capitalize()
            self._field_combo.addItem(f"{label} ({units})" if units else label, name)
        index = self._field_combo.findData(current) if current else -1
        if index < 0:
            index = max(0, self._field_combo.findData("velocity"))
        self._field_combo.setCurrentIndex(index)
        self._field_combo.blockSignals(False)
        self._field_combo.setEnabled(bool(fields))
        if current != self._field_combo.currentData():
            self._on_field_changed()

    def set_coverage(self, text: str, has_vertical: bool) -> None:
        """What the lidar did this session, e.g. 'Scanning 18:49–22:10Z · 55 VAD · 2 stares'."""
        self._coverage_label.setText(text)
        self._coverage_label.setVisible(bool(text))
        self._scan_row.setVisible(bool(text))
        self._btn_viewer.setEnabled(has_vertical)

    def set_large_files(self, total_bytes: int, what: str) -> None:
        """Files held back for their size; 0 hides the button."""
        if total_bytes <= 0:
            self._btn_large.hide()
            return
        self._btn_large.setText(f"LOAD {what.upper()} ({total_bytes / 1e6:.0f} MB)")
        self._btn_large.setEnabled(True)
        self._btn_large.show()
        self._scan_row.setVisible(True)

    def _on_large_clicked(self) -> None:
        self._btn_large.setEnabled(False)
        self.load_large_requested.emit()

    def set_scan_label(self, text: str, can_back: bool, can_forward: bool) -> None:
        self._scan_label.setText(text)
        self._btn_prev.setEnabled(can_back)
        self._btn_next.setEnabled(can_forward)

    def set_locate_available(self, available: bool) -> None:
        self._btn_locate.setEnabled(available)

    def set_orientation_notice(self, provenance: dict | None) -> None:
        """Amber when a missing truck heading was estimated, red when the
        scans couldn't be oriented at all; hidden when the file's own
        orientation is used."""
        provenance = provenance or {}
        if provenance.get("heading_missing_in_file"):
            color, text = "#F5B942", "⚠ " + provenance.get("azimuth_reference", "")
        elif provenance.get("north_referenced") is False:
            color, text = "#F87171", "⚠ " + provenance.get("azimuth_reference", "")
        else:
            self._orientation_label.hide()
            return
        self._orientation_label.setStyleSheet(f"color: {color}; font-size: 10px;")
        self._orientation_label.setText(text)
        self._orientation_label.show()

    # ---- display switches ---------------------------------------------------
    def current_field(self) -> str | None:
        return self._field_combo.currentData()

    def map_is_on(self) -> bool:
        return self._chk_map.isChecked()

    def radar_is_on(self) -> bool:
        return self._chk_radar.isChecked()

    def _on_map_toggled(self, checked: bool) -> None:
        self._chk_radar.setVisible(checked)
        if not checked and not self._chk_radar.isChecked():
            self._chk_radar.setChecked(True)          # radar back on when the lidar leaves the map
        self.map_toggled.emit(checked)

    def _on_field_changed(self, _index=None):
        self._scale_label.clear()
        name = self._field_combo.currentData()
        if name:
            self.field_selected.emit(name)

    def set_map_scale(self, metadata):
        if metadata['field'] == self._field_combo.currentData():
            self._scale_label.setText(f"{metadata['vmin']:.3g} … {metadata['vmax']:.3g} {metadata.get('units', '')}")
