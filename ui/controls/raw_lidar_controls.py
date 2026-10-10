"""LIDAR drawer, laid out like the radar's: one row with the chosen lidar,
the field and the radar switch. A lidar is chosen the way a radar station
is, on the map: while the drawer is open, every place a lidar ran PPI scans
from during the session is marked (a CLAMPS trailer's site, or each stop of
the lidar truck -- core/lidar_scans.locations). Clicking one moves the clock
to its first scan; its PPI and CSM sector sweeps are then drawn on the map as
the clock reaches them, and when it scanned shows under the time slider.
Only PPI scans exist in STORM (core/lidar_scans.py), from the lidars' PPI
and CSM files (archive/fetchers/raw_lidar_archive_fetcher.PRODUCTS).

Two kinds of instrument, named plainly:
  * the lidar truck (DLTRUCK1) -- a Doppler lidar on a truck that moves; its
    files carry no position, so it is placed from the truck's GPS, and its
    heading is recorded or estimated (a small amber warning says when);
  * the CLAMPS1 / CLAMPS2 trailers -- each with its own Doppler lidar, parked
    at a site recorded in its files.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

INSTRUMENT_NAMES = {
    "DLTRUCK1": "Lidar truck",
    "CLAMPS1": "CLAMPS1 trailer",
    "CLAMPS2": "CLAMPS2 trailer",
}


def instrument_name(instrument: str) -> str:
    return INSTRUMENT_NAMES.get(instrument, instrument)


class RawLidarControls(QWidget):
    """Signals
    -------
    field_selected(str)          field to show
    data_toggled(bool)           show the lidar's scans on the map (like the radar's "show data")
    radar_visible_toggled(bool)  radar under the lidar on/off
    location_requested()         the chosen lidar's button: center the map on it
    availability_changed(bool)   any lidar data for this date
    min_intensity_changed(object) hide cells below this intensity (SNR + 1); None: show all
    winds_requested()            open the lidars' wind profiles (VAD / CSM winds)
    """

    field_selected = pyqtSignal(str)
    data_toggled = pyqtSignal(bool)
    radar_visible_toggled = pyqtSignal(bool)
    location_requested = pyqtSignal()
    availability_changed = pyqtSignal(bool)
    min_intensity_changed = pyqtSignal(object)
    winds_requested = pyqtSignal()

    # intensity filter: slider steps of 0.001 above 1.000 (noise sits near
    # 1.000 +- 0.005; returns are above ~1.01). 0 = off.
    INTENSITY_STEP = 0.001
    INTENSITY_MAX_STEPS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._status = "Looking for lidar data…"
        self._chosen = False
        self._assets_by_source: dict[str, list] = {}
        self._sources_by_instrument: dict[str, list] = {}
        self._setup_ui()

    # ---- layout: one row, like the radar's ---------------------------------
    def _setup_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("rawLidarDrawer")
        row = QHBoxLayout(self._drawer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._location_button = QToolButton()
        self._location_button.setObjectName("lidarLocationButton")
        self._location_button.setFixedHeight(22)
        self._location_button.setMinimumWidth(140)
        self._location_button.setText(self._status)
        self._location_button.clicked.connect(self._on_location_clicked)
        row.addWidget(self._location_button)

        self._heading_warning = QLabel("⚠")
        self._heading_warning.setFixedHeight(22)
        self._heading_warning.hide()
        row.addWidget(self._heading_warning)

        self._field_combo = QComboBox()
        self._field_combo.setObjectName("lidarCombo")
        self._field_combo.setFixedHeight(22)
        self._field_combo.setMinimumWidth(128)
        self._field_combo.setEnabled(False)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        row.addWidget(self._field_combo)

        self._chk_data = QCheckBox("show data")
        self._chk_data.setChecked(True)
        self._chk_data.setFixedHeight(22)
        self._chk_data.toggled.connect(self._on_data_toggled)
        row.addWidget(self._chk_data)

        self._chk_radar = QCheckBox("radar")
        self._chk_radar.setChecked(True)
        self._chk_radar.setFixedHeight(22)
        self._chk_radar.setToolTip("Show the radar under the lidar")
        self._chk_radar.toggled.connect(self.radar_visible_toggled.emit)
        row.addWidget(self._chk_radar)

        self._winds_button = QToolButton()
        self._winds_button.setObjectName("lidarLocationButton")
        self._winds_button.setText("WINDS")
        self._winds_button.setFixedHeight(22)
        self._winds_button.setToolTip("Wind profiles from the lidars' VAD and CSM retrievals: "
                                      "hodograph and time-height, for this date")
        self._winds_button.clicked.connect(self.winds_requested.emit)
        row.addSpacing(6)
        row.addWidget(self._winds_button)

        row.addSpacing(10)
        self._intensity_title = QLabel("Min intensity")
        self._intensity_title.setObjectName("deployLocsSliderLabel")
        row.addWidget(self._intensity_title)
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._intensity_slider.setObjectName("deployLocsSlider")
        self._intensity_slider.setRange(0, self.INTENSITY_MAX_STEPS)
        self._intensity_slider.setFixedWidth(130)
        self._intensity_slider.setToolTip(
            "Hide cells whose intensity (the file's SNR + 1) is below this value -- the noise beyond the "
            "aerosol layer. Noise sits near 1.000; about 1.005-1.02 usually clears it. Off (all the way "
            "left) shows every cell, as recorded.")
        self._intensity_slider.valueChanged.connect(self._on_intensity_moved)
        row.addWidget(self._intensity_slider)
        self._intensity_value = QLabel()
        self._intensity_value.setObjectName("deployLocsSliderLabel")
        self._intensity_value.setMinimumWidth(36)
        row.addWidget(self._intensity_value)
        # re-render once the slider settles, not on every step of a drag
        self._intensity_timer = QTimer(self)
        self._intensity_timer.setSingleShot(True)
        self._intensity_timer.setInterval(250)
        self._intensity_timer.timeout.connect(lambda: self.min_intensity_changed.emit(self.min_intensity()))
        self._on_intensity_moved(0, emit=False)
        self._set_intensity_available(False)
        row.addStretch()

        outer.addWidget(self._drawer)

    # ---- intensity filter -------------------------------------------------
    def min_intensity(self) -> float | None:
        steps = self._intensity_slider.value()
        return round(1.0 + steps * self.INTENSITY_STEP, 3) if steps and self._intensity_slider.isEnabled() else None

    def _on_intensity_moved(self, steps: int, emit: bool = True) -> None:
        self._intensity_value.setText(f"{1.0 + steps * self.INTENSITY_STEP:.3f}" if steps else "off")
        if emit:
            self._intensity_timer.start()

    def _set_intensity_available(self, available: bool) -> None:
        """Only lidars whose files carry an intensity field can be filtered."""
        for widget in (self._intensity_title, self._intensity_slider, self._intensity_value):
            widget.setEnabled(available)
        self._intensity_title.setToolTip("" if available else "This lidar's files have no intensity field")

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
            message = "Finding lidar scans…"
        elif unresolved:
            message = "Couldn't check the lidars"
        else:
            message = "No lidar data this date"
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
        """Shown on the lidar button while no lidar is chosen."""
        self._status = str(text or "")
        if not self._chosen:
            self._location_button.setText(self._status)

    def set_location(self, title: str | None, details: str = "") -> None:
        """The chosen lidar, e.g. 'Lidar truck · stop 2 of 4', with its
        position and scanning times on hover; None: nothing chosen."""
        self._chosen = bool(title)
        self._location_button.setText(title or self._status)
        self._location_button.setToolTip(details if title else "")
        if not title:
            self._field_combo.setEnabled(False)
            self.set_heading_notice(None)

    def _on_location_clicked(self) -> None:
        if self._chosen:
            self.location_requested.emit()

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
        self._set_intensity_available("intensity" in fields)
        if current != self._field_combo.currentData():
            self._on_field_changed()

    def set_heading_notice(self, provenance: dict | None) -> None:
        """A small amber ⚠ when the truck's missing heading was estimated, red
        when the scans couldn't be oriented; the explanation is on hover."""
        provenance = provenance or {}
        if provenance.get("heading_missing_in_file"):
            color = "#F5B942"
        elif provenance.get("north_referenced") is False:
            color = "#F87171"
        else:
            self._heading_warning.hide()
            return
        self._heading_warning.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
        self._heading_warning.setToolTip(provenance.get("azimuth_reference", ""))
        self._heading_warning.show()

    # ---- display switches ---------------------------------------------------
    def current_field(self) -> str | None:
        return self._field_combo.currentData()

    def radar_is_on(self) -> bool:
        return self._chk_radar.isChecked()

    def data_is_on(self) -> bool:
        return self._chk_data.isChecked()

    def set_data_on(self, on: bool) -> None:
        self._chk_data.setChecked(on)

    def _on_data_toggled(self, checked: bool) -> None:
        self._chk_radar.setVisible(checked)       # "radar under the lidar" only means something with it on
        self.data_toggled.emit(checked)

    def _on_field_changed(self, _index=None):
        name = self._field_combo.currentData()
        if name:
            self.field_selected.emit(name)
