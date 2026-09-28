"""LIDAR drawer. Lidar is chosen the way a radar is: on the map. While the
drawer is open, every place a lidar scanned from during the session is marked
(a CLAMPS trailer's site, or each stop of the lidar truck -- core/lidar_scans
.locations); clicking one moves the clock to its first scan, and its scans
show as the clock reaches them: PPI/VAD scans and fixed beams on the map,
RHIs and vertical stares in the vertical viewer. This drawer says which
location is chosen and what it holds, and has the field, the map/radar
switches, the viewer, and scan-by-scan stepping.

Two kinds of instrument, kept apart and named plainly:
  * the lidar truck (DLTRUCK1) -- a Doppler lidar on a truck that moves; its
    files carry no position, so it is placed from the truck's GPS, and its
    heading is recorded or estimated (archive/fetchers/raw_lidar_archive_fetcher.py);
  * the CLAMPS1 / CLAMPS2 trailers -- each with its own Doppler lidar, parked
    at a site recorded in its files (often at home in Norman, not deployed).
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
_PROMPT = "Click a lidar location on the map"


def instrument_name(instrument: str) -> str:
    return INSTRUMENT_NAMES.get(instrument, instrument)


class RawLidarControls(QWidget):
    """Signals
    -------
    field_selected(str)          field to show
    map_toggled(bool)            draw PPI/VAD scans on the map (on by default)
    radar_visible_toggled(bool)  radar under the lidar on/off
    viewer_requested()           open the vertical viewer (RHIs, stares)
    scan_step_requested(int)     -1 / +1: previous / next scan here (moves the clock)
    availability_changed(bool)   any lidar data for this date
    """

    field_selected = pyqtSignal(str)
    map_toggled = pyqtSignal(bool)
    radar_visible_toggled = pyqtSignal(bool)
    viewer_requested = pyqtSignal()
    scan_step_requested = pyqtSignal(int)
    availability_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._availability_status = "Looking for lidar data for this date…"
        self._assets_by_source: dict[str, list] = {}
        self._sources_by_instrument: dict[str, list] = {}
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

        def button(text, tip, width=None):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            if width:
                b.setMinimumWidth(width)
            return b

        def check(text, tip):
            c = QCheckBox(text)
            c.setChecked(True)
            c.setFixedHeight(22)
            c.setToolTip(tip)
            return c

        # ---- row 1: the chosen location, the field, how to show it ---------
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(6)
        self._location_label = QLabel(_PROMPT)
        self._location_label.setObjectName("lidarLocationLabel")
        self._location_label.setStyleSheet("color: #E3E8F2; font-size: 11px; font-weight: 600;")
        self._location_label.setMinimumWidth(200)
        r1.addWidget(self._location_label)
        self._field_combo = QComboBox()
        self._field_combo.setObjectName("lidarCombo")
        self._field_combo.setFixedHeight(22)
        self._field_combo.setMinimumWidth(130)
        self._field_combo.setToolTip("Field to show")
        self._field_combo.setEnabled(False)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        r1.addWidget(self._field_combo)
        self._chk_map = check("Map", "Draw the lidar's PPI/VAD scans and fixed beams on the map as the clock reaches them")
        self._chk_map.toggled.connect(self._on_map_toggled)
        r1.addWidget(self._chk_map)
        self._chk_radar = check("Radar", "Show the radar under the lidar scans")
        self._chk_radar.toggled.connect(self.radar_visible_toggled.emit)
        r1.addWidget(self._chk_radar)
        self._btn_viewer = button("VIEWER", "Open the vertical viewer: RHI cross-sections and vertical stares", 64)
        self._btn_viewer.setEnabled(False)
        self._btn_viewer.clicked.connect(self.viewer_requested.emit)
        r1.addWidget(self._btn_viewer)
        r1.addStretch()
        col.addWidget(row1)

        # ---- row 2: scan by scan at this location ----------------------------
        self._scan_row = QWidget()
        r2 = QHBoxLayout(self._scan_row)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(4)
        self._btn_prev = button("⏮", "Previous scan here (moves the clock to it)", 28)
        self._btn_prev.clicked.connect(lambda: self.scan_step_requested.emit(-1))
        r2.addWidget(self._btn_prev)
        self._scan_label = QLabel("")
        self._scan_label.setStyleSheet("color: #E3E8F2; font-size: 11px;")
        r2.addWidget(self._scan_label)
        self._btn_next = button("⏭", "Next scan here (moves the clock to it)", 28)
        self._btn_next.clicked.connect(lambda: self.scan_step_requested.emit(1))
        r2.addWidget(self._btn_next)
        r2.addSpacing(8)
        self._scale_label = QLabel("")
        self._scale_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        r2.addWidget(self._scale_label)
        r2.addStretch()
        self._scan_row.setVisible(False)
        col.addWidget(self._scan_row)

        self._coverage_label = QLabel("")
        self._coverage_label.setWordWrap(True)
        self._coverage_label.setStyleSheet("color: #00CFFF; font-size: 10px;")
        self._coverage_label.hide()
        col.addWidget(self._coverage_label)

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
        with_data = self.instruments()
        unresolved = [i for i, sources in self._sources_by_instrument.items()
                      if any(s.platform_id in self._assets_by_source and self._assets_by_source[s.platform_id] is None
                             for s in sources)]
        if with_data:
            message = "Lidar data this date: " + ", ".join(instrument_name(i) for i in with_data)
            if unresolved:
                message += " · some sources could not be checked"
        elif unresolved:
            message = "Could not check every lidar for this date."
        else:
            message = "No lidar data for this date."
        self._availability_status = message
        self.set_status(message)
        self.availability_changed.emit(bool(with_data))

    def instruments(self) -> list[str]:
        return [i for i, sources in self._sources_by_instrument.items()
                if any(self._assets_by_source.get(s.platform_id) for s in sources)]

    def assets_by_instrument(self) -> dict[str, list]:
        """Every lidar's files found for the session."""
        return {i: [a for s in self._sources_by_instrument[i] for a in (self._assets_by_source.get(s.platform_id) or [])]
                for i in self.instruments()}

    # ---- the chosen location ------------------------------------------------
    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_location(self, title: str | None) -> None:
        """What's chosen, e.g. 'Lidar truck · stop 2 of 4 · (41.438, -97.337)'; None: nothing."""
        self._location_label.setText(title or _PROMPT)
        if not title:
            self._field_combo.setEnabled(False)
            self._btn_viewer.setEnabled(False)
            self._scan_row.setVisible(False)
            self._coverage_label.hide()
            self._scale_label.clear()
            self.set_orientation_notice(None)

    def set_fields(self, fields: dict) -> None:
        """Fields across the chosen location's files (name -> units); keeps the choice."""
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
        """What the location holds, e.g. 'Scanning 18:49–19:23Z · 12 VAD · 1 vertical stare'."""
        self._coverage_label.setText(text)
        self._coverage_label.setVisible(bool(text))
        self._scan_row.setVisible(bool(text))
        self._btn_viewer.setEnabled(has_vertical)

    def set_scan_label(self, text: str, can_back: bool, can_forward: bool) -> None:
        self._scan_label.setText(text)
        self._btn_prev.setEnabled(can_back)
        self._btn_next.setEnabled(can_forward)

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
