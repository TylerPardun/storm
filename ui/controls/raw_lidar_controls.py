"""RAW LIDAR drawer, laid out like the RADAR drawer: one compact row to pick
the lidar, its scan file and field and to show it (MAP for plan-view scans,
VIEW for RHIs and stares), a scan row to step through the scans the file
holds (core/lidar_scans.py), and a status line saying where the lidar was.

Two kinds of instrument publish these files, and they are kept apart and
named plainly:
  * the lidar truck (DLTRUCK1) -- a Doppler lidar on a truck that moves; its
    files carry no position, so it is placed from the truck's GPS, and its
    heading is recorded or estimated (archive/fetchers/raw_lidar_archive_fetcher.py);
  * the CLAMPS1 / CLAMPS2 trailers -- each with its own Doppler lidar, parked
    at a site recorded in its files (often at home in Norman, not deployed).
KNOWN_RAW_LIDAR_SOURCES has one entry per (instrument, file type); grouping
by instrument keeps the picker at "which lidars", not "which files".
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


def instrument_name(instrument: str) -> str:
    return INSTRUMENT_NAMES.get(instrument, instrument)


class RawLidarControls(QWidget):
    """Signals
    -------
    source_selected(str)                 platform_id of the chosen (instrument, file)
    map_overlay_requested(str, object, bool)   platform_id, LidarAsset, on/off
    view_requested(str, object)          platform_id, LidarAsset -- open the viewer
    field_selected(str)                  field to draw
    scan_step_requested(int)             -1 / +1: previous / next scan
    radar_visible_toggled(bool)          radar under the lidar on/off
    locate_requested()                   center the map on the lidar
    """

    field_selected = pyqtSignal(str)
    locate_requested = pyqtSignal()
    source_selected = pyqtSignal(str)
    view_requested = pyqtSignal(str, object)
    map_overlay_requested = pyqtSignal(str, object, bool)
    scan_step_requested = pyqtSignal(int)
    radar_visible_toggled = pyqtSignal(bool)
    availability_changed = pyqtSignal(bool)     # any lidar data for this date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._availability_status = "Looking for lidar data for this date…"
        self._assets_by_source: dict[str, list] = {}
        self._sources_by_instrument: dict[str, list] = {}
        self._site_text: dict[str, str] = {}
        self._map_target = None   # (RawLidarSource, LidarAsset) MAP currently shows
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

        def button(text, tip, width=None, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            if width:
                b.setMinimumWidth(width)        # at least this; wider if the label needs it
            b.setCheckable(checkable)
            return b

        # ---- row 1: which lidar, which file, which field, show it --------
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)
        self._source_combo = combo("Which lidar: the lidar truck, or a CLAMPS trailer", 150)
        self._source_combo.currentIndexChanged.connect(self._on_instrument_changed)
        r1.addWidget(self._source_combo)
        self._asset_combo = combo("Which of its files for this date (each holds one kind of scanning)", 170)
        self._asset_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        r1.addWidget(self._asset_combo)
        self._field_combo = combo("Field to draw", 130)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        self._field_combo.setEnabled(False)
        r1.addWidget(self._field_combo)
        self._btn_map = button("MAP", "Draw the lidar's PPI/VAD scans on the map at the current time", 44, True)
        self._btn_map.setEnabled(False)
        self._btn_map.toggled.connect(self._on_map_toggled)
        r1.addWidget(self._btn_map)
        self._chk_radar = QCheckBox("Radar")
        self._chk_radar.setChecked(True)
        self._chk_radar.setFixedHeight(22)
        self._chk_radar.setToolTip("Show the radar under the lidar scans")
        self._chk_radar.toggled.connect(self.radar_visible_toggled.emit)
        self._chk_radar.setVisible(False)
        r1.addWidget(self._chk_radar)
        self._btn_view = button("VIEW", "Open the viewer: RHI cross-sections, or time-height for stares", 48)
        self._btn_view.setEnabled(False)
        self._btn_view.clicked.connect(self._on_view_clicked)
        r1.addWidget(self._btn_view)
        self._btn_locate = button("LOCATE", "Center the map on the lidar", 72)
        self._btn_locate.setEnabled(False)
        self._btn_locate.clicked.connect(self.locate_requested.emit)
        r1.addWidget(self._btn_locate)
        r1.addStretch()
        col.addWidget(row1)

        # ---- row 2: step through the file's scans ------------------------
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
        self._contents_label = QLabel("")
        self._contents_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        r2.addWidget(self._contents_label)
        self._scale_label = QLabel("")
        self._scale_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        r2.addWidget(self._scale_label)
        r2.addStretch()
        self._scan_row.setVisible(False)
        col.addWidget(self._scan_row)

        # ---- status: availability, where the lidar was, orientation ------
        self._status_label = QLabel(self._availability_status)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #8E97AB; font-size: 10px;")
        col.addWidget(self._status_label)
        # truck lidar orientation: shown when the file's heading was missing
        # (estimated) or unknown, so the data problem is visible and reportable
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

    # ---- what's available -----------------------------------------------
    def set_sources(self, sources) -> None:
        """sources: RawLidarSource entries (KNOWN_RAW_LIDAR_SOURCES)."""
        self._sources_by_instrument = {}
        for source in sources:
            self._sources_by_instrument.setdefault(source.instrument, []).append(source)

    def current_instrument(self) -> str | None:
        return self._source_combo.currentData()

    def current_source(self):
        entry = self._asset_combo.currentData()
        return entry[0] if entry else None

    def current_asset(self):
        entry = self._asset_combo.currentData()
        return entry[1] if entry else None

    def current_field(self) -> str | None:
        return self._field_combo.currentData()

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_assets_for_all_sources(self, assets_by_source: dict) -> None:
        """assets_by_source: platform_id -> list[LidarAsset] (None: couldn't check)."""
        self._assets_by_source = dict(assets_by_source)
        selected = self.current_instrument()
        with_data = [i for i, sources in self._sources_by_instrument.items()
                     if any(self._assets_by_source.get(s.platform_id) for s in sources)]
        unresolved = [i for i, sources in self._sources_by_instrument.items()
                      if any(s.platform_id in self._assets_by_source and self._assets_by_source[s.platform_id] is None
                             for s in sources)]
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        for instrument in with_data:
            self._source_combo.addItem(f"{instrument_name(instrument)} ({instrument})", instrument)
        index = self._source_combo.findData(selected)
        self._source_combo.setCurrentIndex(index if index >= 0 else 0)
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
        self._refresh_scan_mode_combo()
        self._show_status()
        self.availability_changed.emit(bool(with_data))

    def set_site_text(self, instrument: str, text: str) -> None:
        """Where an instrument was (e.g. 'at NWC Vehicle Bay (34.982, -97.520), 690 km from KOAX')."""
        self._site_text[instrument] = text
        self._show_status()

    def _show_status(self) -> None:
        instrument = self.current_instrument()
        where = self._site_text.get(instrument)
        text = self._availability_status
        if instrument and where:
            text = f"{instrument_name(instrument)} {where}  ·  {text}"
        self.set_status(text)

    def _refresh_scan_mode_combo(self) -> None:
        # any instrument switch invalidates whatever MAP was pointing at --
        # uncheck first (with the *old* selection still in place) so the
        # overlay being turned off is the one actually shown
        if self._btn_map.isChecked():
            self._btn_map.setChecked(False)
        sources = self._sources_by_instrument.get(self.current_instrument(), [])
        with_data = [s for s in sources if self._assets_by_source.get(s.platform_id)]
        self._asset_combo.blockSignals(True)
        self._asset_combo.clear()
        for source in with_data:
            assets = self._assets_by_source[source.platform_id]
            # one instrument can publish a scan mode under two stream names
            shared = sum(s.product == source.product for s in with_data) > 1
            for index, asset in enumerate(assets):
                label = FILE_NAMES.get(source.product, source.product.upper())
                if shared:
                    label += f" ({source.stream})"
                day = asset.filename.split(".")[2] if asset.filename.count(".") >= 3 else ""
                if len(day) == 8:
                    label += f" · {day[4:6]}/{day[6:]}"
                if len(assets) > 1:
                    label += f" · file {index + 1}/{len(assets)}"
                self._asset_combo.addItem(label, (source, asset))
        self._asset_combo.blockSignals(False)
        self._on_scan_mode_changed(self._asset_combo.currentIndex())

    def _on_instrument_changed(self, _index: int) -> None:
        self._refresh_scan_mode_combo()
        self._show_status()

    def _on_scan_mode_changed(self, _index: int) -> None:
        if self._btn_map.isChecked():
            self._btn_map.setChecked(False)
        self.set_orientation_notice(None)
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        self._field_combo.blockSignals(False)
        self._field_combo.setEnabled(False)
        self._scale_label.clear()
        self._scan_row.setVisible(False)
        self._btn_locate.setEnabled(False)
        source = self.current_source()
        has_file = source is not None
        # what a file holds is only known once it's loaded; until then MAP
        # and VIEW both load it (MAP for scan files, VIEW for any)
        self._btn_map.setEnabled(has_file and source.product in ("ppi", "csm", "other"))
        self._btn_view.setEnabled(has_file)
        self._chk_radar.setVisible(False)
        if has_file:
            self.source_selected.emit(source.platform_id)

    # ---- a file has loaded -------------------------------------------------
    def set_loaded_fields(self, rays, scans=None) -> bool:
        """Fill in the fields and scan summary once the selected file loads."""
        entry = self._asset_combo.currentData()
        if not entry or rays.provenance.get("url") != entry[1].url:
            return False
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name, field in rays.fields.items():
            label = name.replace("_", " ").capitalize()
            units = field.get("units", "")
            self._field_combo.addItem(f"{label} ({units})" if units else label, name)
        self._field_combo.setCurrentIndex(max(0, self._field_combo.findData("velocity")))
        self._field_combo.blockSignals(False)
        self._field_combo.setEnabled(bool(rays.fields))
        if scans is not None:
            from core.lidar_scans import counts
            mappable = any(s.mappable for s in scans)
            self._btn_map.setEnabled(mappable)
            self._btn_map.setToolTip("Draw the lidar's PPI/VAD scans on the map at the current time" if mappable
                                     else "This file has no PPI or VAD scans to put on a map -- use VIEW")
            self._contents_label.setText(f"File: {counts(scans) or 'no scans'}")
            self._scan_row.setVisible(bool(scans))
        self.set_orientation_notice(rays.provenance)
        self._on_field_changed()
        return True

    def set_scan_label(self, text: str, can_back: bool, can_forward: bool) -> None:
        self._scan_label.setText(text)
        self._btn_prev.setEnabled(can_back)
        self._btn_next.setEnabled(can_forward)

    def set_locate_available(self, available: bool) -> None:
        self._btn_locate.setEnabled(available)

    def set_orientation_notice(self, provenance: dict | None) -> None:
        """Amber when a missing truck heading was estimated, red when the
        scan couldn't be oriented at all; hidden when the file's own
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

    # ---- actions ------------------------------------------------------------
    def _on_map_toggled(self, checked: bool) -> None:
        self._chk_radar.setVisible(checked)
        if not checked and not self._chk_radar.isChecked():
            self._chk_radar.setChecked(True)          # radar back on when the lidar goes
        if checked:
            source, asset = self.current_source(), self.current_asset()
            if source is None or asset is None:
                return
            self._map_target = (source, asset)
            self.map_overlay_requested.emit(source.platform_id, asset, True)
            return
        if self._map_target is not None:
            source, asset = self._map_target
            self.map_overlay_requested.emit(source.platform_id, asset, False)
        self._map_target = None

    def map_is_on(self) -> bool:
        return self._btn_map.isChecked()

    def turn_map_off(self) -> None:
        if self._btn_map.isChecked():
            self._btn_map.setChecked(False)

    def _on_view_clicked(self) -> None:
        source, asset = self.current_source(), self.current_asset()
        if source is not None and asset is not None:
            self.view_requested.emit(source.platform_id, asset)

    def _on_field_changed(self, _index=None):
        self._scale_label.clear()
        name = self._field_combo.currentData()
        if name:
            self.field_selected.emit(name)

    def set_map_scale(self, metadata):
        if metadata['field'] == self._field_combo.currentData():
            self._scale_label.setText(f"{metadata['vmin']:.3g} … {metadata['vmax']:.3g} {metadata.get('units', '')}")
