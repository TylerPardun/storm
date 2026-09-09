"""Floating drawer for archive-mode NOXP mobile-radar browsing: pick a
campaign root, an available volume, a sweep and a field, then render.
Mirrors MesoanalysisControls'/SfcoaControls' collapsible-drawer shell
(ui/controls/mesoanalysis_controls.py) but for a discovery-then-render
flow instead of a product palette."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
)


class NoxpControls(QWidget):
    """Signals
    -------
    platform_selected(str)          platform_id -- user picked a campaign root
    asset_selected(object)          RadarAsset -- user picked a discovered volume
    render_requested(int, str)      sweep_index, native_field_name
    """

    platform_selected = pyqtSignal(str)
    asset_selected = pyqtSignal(object)
    render_requested = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._assets: list = []
        self._available_sweeps: list[tuple[int, float]] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("noxpDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        platform_row = QHBoxLayout()
        platform_row.addWidget(QLabel("Campaign"))
        self._platform_combo = QComboBox()
        self._platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        platform_row.addWidget(self._platform_combo, stretch=1)
        col.addLayout(platform_row)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("Volume"))
        self._asset_combo = QComboBox()
        self._asset_combo.currentIndexChanged.connect(self._on_asset_changed)
        asset_row.addWidget(self._asset_combo, stretch=1)
        col.addLayout(asset_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        col.addWidget(self._status_label)

        render_row = QHBoxLayout()
        render_row.addWidget(QLabel("Sweep"))
        self._sweep_combo = QComboBox()
        render_row.addWidget(self._sweep_combo)
        render_row.addWidget(QLabel("Field"))
        self._field_combo = QComboBox()
        render_row.addWidget(self._field_combo)
        self._btn_render = QPushButton("RENDER")
        self._btn_render.clicked.connect(self._on_render_clicked)
        self._btn_render.setEnabled(False)
        render_row.addWidget(self._btn_render)
        col.addLayout(render_row)

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

    def set_platforms(self, platforms) -> None:
        """platforms: iterable of KnownPlatform (family == 'NOXP Radar')."""
        self._platform_combo.blockSignals(True)
        self._platform_combo.clear()
        for platform in platforms:
            self._platform_combo.addItem(platform.display_name, platform)
        self._platform_combo.blockSignals(False)

    def current_platform(self):
        return self._platform_combo.currentData()

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_assets(self, assets: list) -> None:
        """assets: list[RadarAsset] for the currently-selected platform/date."""
        self._assets = list(assets)
        self._asset_combo.blockSignals(True)
        self._asset_combo.clear()
        for asset in self._assets:
            label = asset.nominal_time.strftime("%H:%M:%S UTC") if asset.nominal_time else asset.name
            self._asset_combo.addItem(f"{label} ({asset.format})", asset)
        self._asset_combo.blockSignals(False)
        self._btn_render.setEnabled(False)
        self.set_status(f"{len(self._assets)} volume(s) found" if self._assets else "No volumes found for this date")

    def set_volume_summary(self, volume) -> None:
        """volume: a decoded RadarVolume (or None to clear). Populates the
        sweep/field selectors and shows the RHI/non-mappable state when the
        loaded volume's geometry hasn't been confirmed for map rendering."""
        self._sweep_combo.clear()
        self._field_combo.clear()
        if volume is None:
            self._btn_render.setEnabled(False)
            return
        for name in sorted(volume.fields):
            self._field_combo.addItem(name)
        if volume.scan_type != "ppi":
            self._btn_render.setEnabled(False)
            self.set_status(
                f"Volume is {volume.scan_type} — not confirmed-PPI geometry, "
                "so it can't be map-rendered. Inspection only."
            )
            return
        for i in range(int(volume.sweep_start.size)):
            self._sweep_combo.addItem(f"Sweep {i}", i)
        self._btn_render.setEnabled(self._sweep_combo.count() > 0 and self._field_combo.count() > 0)
        self.set_status(f"{self._sweep_combo.count()} sweep(s), {self._field_combo.count()} field(s) — ready to render")

    def _on_platform_changed(self, _index: int) -> None:
        self.set_assets([])
        self.set_volume_summary(None)
        platform = self.current_platform()
        if platform is not None:
            self.platform_selected.emit(platform.platform_id)

    def _on_asset_changed(self, _index: int) -> None:
        self.set_volume_summary(None)
        asset = self._asset_combo.currentData()
        if asset is not None:
            self.asset_selected.emit(asset)

    def _on_render_clicked(self) -> None:
        sweep_index = self._sweep_combo.currentData()
        field_name = self._field_combo.currentText()
        if sweep_index is not None and field_name:
            self.render_requested.emit(int(sweep_index), field_name)
