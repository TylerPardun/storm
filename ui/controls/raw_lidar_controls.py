"""Floating drawer for archive-mode CLAMPS raw-lidar browsing: pick a
known instrument (physical lidar unit), then a scan mode it actually has
data for, then open its quicklook. Mirrors MesoanalysisControls'/
NoxpControls' collapsible-drawer shell (ui/controls/mesoanalysis_controls.py,
ui/controls/noxp_controls.py).

KNOWN_RAW_LIDAR_SOURCES registers up to 4 scan-mode variants (csm/ppi/fp/
other) per physical instrument, so a single deployed lidar can produce
several RawLidarSource entries for one day -- grouping by instrument here
keeps the top-level picker at "how many lidars were actually out," not
"how many scan-mode files exist," which is what it looked like before."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
)


class RawLidarControls(QWidget):
    """Signals
    -------
    source_selected(str)       platform_id of the selected (instrument, scan
                                mode) pair, once it has data for this date
    quicklook_requested(str, object)   platform_id, LidarAsset
    map_overlay_requested(str, object, bool)
        platform_id, LidarAsset, enabled -- user (un)checked MAP. Only
        offered for PPI/CSM sources. The renderer validates coordinates and
        the mobile azimuth reference before plotting measured gates.
    """

    field_selected = pyqtSignal(str)
    locate_requested = pyqtSignal()
    source_selected = pyqtSignal(str)
    quicklook_requested = pyqtSignal(str, object)
    map_overlay_requested = pyqtSignal(str, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._availability_status = "Searching known instruments for this date…"
        self._assets_by_source: dict[str, list] = {}
        self._sources_by_instrument: dict[str, list] = {}
        self._map_target = None   # (RawLidarSource, LidarAsset) the MAP button currently represents
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("rawLidarDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Lidar"))
        self._source_combo = QComboBox()
        self._source_combo.currentIndexChanged.connect(self._on_instrument_changed)
        source_row.addWidget(self._source_combo, stretch=1)
        self._btn_locate = QPushButton("LOCATE")
        self._btn_locate.setToolTip("Center on lidar")
        self._btn_locate.hide()
        self._btn_locate.clicked.connect(self.locate_requested.emit)
        source_row.addWidget(self._btn_locate)
        col.addLayout(source_row)

        self._field_row = QWidget()
        field_layout = QHBoxLayout(self._field_row)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.addWidget(QLabel("Map field"))
        self._field_combo = QComboBox()
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        field_layout.addWidget(self._field_combo, stretch=1)
        self._scale_label = QLabel()
        field_layout.addWidget(self._scale_label)
        self._field_row.hide()
        col.addWidget(self._field_row)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("Scan Mode"))
        self._asset_combo = QComboBox()
        self._asset_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        asset_row.addWidget(self._asset_combo, stretch=1)
        self._btn_view = QPushButton("VIEW QUICKLOOK")
        self._btn_view.clicked.connect(self._on_view_clicked)
        self._btn_view.setEnabled(False)
        asset_row.addWidget(self._btn_view)

        self._btn_map = QPushButton("MAP")
        self._btn_map.setCheckable(True)
        self._btn_map.setEnabled(False)
        self._btn_map.hide()
        self._btn_map.setToolTip(
            "Show lidar scan on map"
        )
        self._btn_map.toggled.connect(self._on_map_toggled)
        asset_row.addWidget(self._btn_map)
        col.addLayout(asset_row)

        self._status_label = QLabel("Searching known instruments for this date…")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        # A genuinely-empty word-wrapped QLabel's sizeHint is unstable
        # during the drawer's open/close animation (it can measure taller
        # than any real message ever needs); always keeping real text in
        # it, even as an initial placeholder, avoids that without capping
        # the height and risking a long message getting clipped. Accurate
        # as a placeholder since discovery runs automatically once archive
        # mode starts (main_window._begin_archive_startup), not on open.
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

    def set_sources(self, sources) -> None:
        """sources: iterable of RawLidarSource (KNOWN_RAW_LIDAR_SOURCES),
        grouped here by instrument -- see module docstring."""
        self._sources_by_instrument = {}
        for source in sources:
            self._sources_by_instrument.setdefault(source.instrument, []).append(source)

        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        for instrument in self._sources_by_instrument:
            self._source_combo.addItem(instrument, instrument)
        self._source_combo.blockSignals(False)

    def current_instrument(self) -> str | None:
        return self._source_combo.currentData()

    def current_source(self):
        """The currently selected (instrument, scan mode) RawLidarSource,
        or None if the current instrument has no data on this date."""
        entry = self._asset_combo.currentData()
        return entry[0] if entry else None

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_assets_for_all_sources(self, assets_by_source: dict) -> None:
        """assets_by_source: platform_id -> list[LidarAsset], from one
        discovery pass across every known (instrument, scan mode) source
        for the archive date."""
        self._assets_by_source = dict(assets_by_source)
        selected = self.current_instrument()
        instruments_with_data = {
            instrument for instrument, sources in self._sources_by_instrument.items()
            if any(self._assets_by_source.get(source.platform_id) for source in sources)
        }
        unresolved = {
            instrument for instrument, sources in self._sources_by_instrument.items()
            if any(source.platform_id in self._assets_by_source and self._assets_by_source[source.platform_id] is None for source in sources)
        }
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        for instrument in self._sources_by_instrument:
            if instrument in instruments_with_data or instrument in unresolved:
                suffix = " (unavailable)" if instrument not in instruments_with_data else ""
                self._source_combo.addItem(instrument + suffix, instrument)
        index = self._source_combo.findData(selected)
        if index >= 0:
            self._source_combo.setCurrentIndex(index)
        elif instruments_with_data:
            first = next(i for i in self._sources_by_instrument if i in instruments_with_data)
            self._source_combo.setCurrentIndex(self._source_combo.findData(first))
        self._source_combo.blockSignals(False)
        if instruments_with_data:
            message = f"Available this date: {', '.join(sorted(instruments_with_data))}"
            if unresolved:
                message += " · some sources unavailable"
            self.set_status(message)
        elif unresolved:
            self.set_status("Availability incomplete. Some sources could not be checked.")
        else:
            self.set_status("No raw lidar data found for this date.")
        self._availability_status = self._status_label.text()
        self._refresh_scan_mode_combo()

    def _refresh_scan_mode_combo(self) -> None:
        # any instrument switch invalidates whatever MAP was pointing at --
        # uncheck first (with the *old* combo selection still in place) so
        # _on_map_toggled reports the source actually being turned off,
        # not whatever the combo happens to land on after repopulating.
        if self._btn_map.isChecked():
            self._btn_map.setChecked(False)

        instrument = self.current_instrument()
        self._asset_combo.blockSignals(True)
        self._asset_combo.clear()
        sources = self._sources_by_instrument.get(instrument, [])
        with_data = [s for s in sources if self._assets_by_source.get(s.platform_id)]
        for source in sources:
            assets = self._assets_by_source.get(source.platform_id)
            if assets:
                # one instrument can publish a scan mode under two stream names
                shared = sum(s.product == source.product for s in with_data) > 1
                for index, asset in enumerate(assets):
                    label = source.product.upper()
                    if shared:
                        label += f" ({source.stream} stream)"
                    if len(assets) > 1:
                        label += f" · file {index + 1}/{len(assets)}"
                    self._asset_combo.addItem(label, (source, asset))
        self._asset_combo.blockSignals(False)
        self._btn_view.setEnabled(self._asset_combo.count() > 0)
        self._on_scan_mode_changed(self._asset_combo.currentIndex())

    def _on_instrument_changed(self, _index: int) -> None:
        self._refresh_scan_mode_combo()

    def _on_scan_mode_changed(self, _index: int) -> None:
        # same reasoning as _refresh_scan_mode_combo: drop any active
        # overlay target before re-deriving state for the new selection.
        if self._btn_map.isChecked():
            self._btn_map.setChecked(False)
        self.set_status(self._availability_status)
        self.set_orientation_notice(None)
        self._field_row.hide()
        self._btn_locate.hide()
        self._scale_label.clear()
        source = self.current_source()
        can_map = source is not None and source.product in ("ppi", "csm")
        self._btn_map.setEnabled(can_map)
        self._btn_map.setVisible(can_map)
        if source is not None:
            self.source_selected.emit(source.platform_id)

    def _on_map_toggled(self, checked: bool) -> None:
        if checked:
            source = self.current_source()
            entry = self._asset_combo.currentData()
            if source is None or entry is None:
                return
            self._map_target = (source, entry[1])
            self.map_overlay_requested.emit(source.platform_id, entry[1], True)
            return
        if self._map_target is not None:
            source, asset = self._map_target
            self.map_overlay_requested.emit(source.platform_id, asset, False)
        self._map_target = None

    def _on_view_clicked(self) -> None:
        source = self.current_source()
        entry = self._asset_combo.currentData()
        if source is not None and entry is not None:
            self.quicklook_requested.emit(source.platform_id, entry[1])

    def set_loaded_fields(self, rays) -> bool:
        entry = self._asset_combo.currentData()
        if not entry or rays.provenance.get("url") != entry[1].url:
            return False
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name, field in rays.fields.items():
            label = name.replace("_", " ").capitalize()
            units = field.get("units", "")
            self._field_combo.addItem(f"{label} ({units})" if units else label, name)
        index = self._field_combo.findData("velocity")
        self._field_combo.setCurrentIndex(max(0, index))
        self._field_combo.blockSignals(False)
        self._field_row.setVisible(bool(rays.fields) and entry[0].product in ("ppi", "csm"))
        self._on_field_changed()
        site_name = rays.provenance.get("metadata", {}).get("Site_description")
        if site_name:
            self.set_status(str(site_name))
        self.set_orientation_notice(rays.provenance)
        return True

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

    def _on_field_changed(self, _index=None):
        self._scale_label.clear()
        name = self._field_combo.currentData()
        if name:
            self.field_selected.emit(name)

    def set_map_scale(self, metadata):
        if metadata['field'] == self._field_combo.currentData():
            self._scale_label.setText(f"{metadata['vmin']:.3g} … {metadata['vmax']:.3g}")
