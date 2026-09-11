"""Floating drawer for archive-mode NOXP mobile-radar browsing: pick a
campaign root, an available volume, a sweep and a field, then render.
Mirrors MesoanalysisControls'/SfcoaControls' collapsible-drawer shell
(ui/controls/mesoanalysis_controls.py) but for a discovery-then-render
flow instead of a product palette."""
from __future__ import annotations

from datetime import datetime

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
    map_toggled = pyqtSignal(bool)

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

        # Campaign/Volume are found automatically now (MainWindow's archive
        # startup sequence picks the case's own campaign by year and
        # searches it for this specific day -- see the "Mobile Radar"
        # loading-dialog task), so there's no manual search step in the
        # normal flow. Left in place (not deleted) as a hidden fallback --
        # everything below (Sweep/Field/MAP) still reads through
        # _platform_combo/_asset_combo either way -- but hidden, since nothing
        # should need to pick a campaign or volume by hand day to day.
        self._campaign_row = QWidget()
        platform_row = QHBoxLayout(self._campaign_row)
        platform_row.setContentsMargins(0, 0, 0, 0)
        platform_row.addWidget(QLabel("Campaign"))
        self._platform_combo = QComboBox()
        self._platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        platform_row.addWidget(self._platform_combo, stretch=1)
        self._campaign_row.hide()
        col.addWidget(self._campaign_row)

        self._volume_row = QWidget()
        asset_row = QHBoxLayout(self._volume_row)
        asset_row.setContentsMargins(0, 0, 0, 0)
        asset_row.addWidget(QLabel("Volume"))
        self._asset_combo = QComboBox()
        self._asset_combo.currentIndexChanged.connect(self._on_asset_changed)
        asset_row.addWidget(self._asset_combo, stretch=1)
        self._volume_row.hide()
        col.addWidget(self._volume_row)

        self._status_label = QLabel("Select a campaign to search")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #6E7A8F; font-size: 10px;")
        # A genuinely-empty word-wrapped QLabel's sizeHint is unstable
        # during the drawer's open/close animation (it can measure taller
        # than any real message ever needs); always keeping real text in
        # it, even as an initial placeholder, avoids that without capping
        # the height and risking a long message getting clipped.
        col.addWidget(self._status_label)

        render_row = QHBoxLayout()
        render_row.addWidget(QLabel("Sweep"))
        self._sweep_combo = QComboBox()
        render_row.addWidget(self._sweep_combo)
        render_row.addWidget(QLabel("Field"))
        self._field_combo = QComboBox()
        render_row.addWidget(self._field_combo)
        self._btn_render = QPushButton("MAP")
        self._btn_render.setCheckable(True)
        self._btn_render.toggled.connect(self._on_map_toggled)
        self._sweep_combo.currentIndexChanged.connect(self._on_selection_changed)
        self._field_combo.currentIndexChanged.connect(self._on_selection_changed)
        self._btn_render.setEnabled(False)
        self._btn_render.hide()
        render_row.addWidget(self._btn_render)
        col.addLayout(render_row)

        outer.addWidget(self._drawer)

    def toggle_drawer(self, checked: bool) -> None:
        if checked:
            if not self._assets:
                self._on_platform_changed(self._platform_combo.currentIndex())
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

    def select_campaign_for_year(self, year: int) -> bool:
        """Pre-select whichever registered campaign's label names this
        year (e.g. "2013", "VORTEX2 2009"), so opening the drawer searches
        the case's own year first instead of always starting from
        whatever combo index happens to be first ("2010", historically --
        see the search-every-year friction this was built to remove).
        Silent (no search triggered here); the existing drawer-open flow
        still does that once the right campaign is already selected.
        Returns whether a match was found -- campaigns without a year in
        their label (Colorado/Netcdf/Reeves) are left for manual pick
        rather than guessed at.
        """
        needle = str(year)
        for index in range(self._platform_combo.count()):
            platform = self._platform_combo.itemData(index)
            if platform is not None and needle in platform.display_name:
                self._platform_combo.blockSignals(True)
                self._platform_combo.setCurrentIndex(index)
                self._platform_combo.blockSignals(False)
                return True
        return False

    def asset_near(self, when: datetime):
        """Discovered asset nearest at/before `when`; falls back to the
        earliest discovered asset if every one is after `when`. None if
        nothing's been discovered yet (or none carry a usable timestamp)."""
        dated = [a for a in self._assets if a.nominal_time is not None]
        if not dated:
            return None
        before = [a for a in dated if a.nominal_time <= when]
        if before:
            return max(before, key=lambda a: a.nominal_time)
        return min(dated, key=lambda a: a.nominal_time)

    def set_current_asset_silently(self, asset) -> None:
        """Reflect an externally-driven asset switch (the clock-follow path
        in MainWindow) in the combo without re-emitting asset_selected --
        that selection already came from the caller, which is about to
        load it directly."""
        index = self._asset_combo.findData(asset)
        if index < 0:
            return
        self._asset_combo.blockSignals(True)
        self._asset_combo.setCurrentIndex(index)
        self._asset_combo.blockSignals(False)

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def _clear_assets(self) -> None:
        self._assets = []
        self._asset_combo.blockSignals(True)
        self._asset_combo.clear()
        self._asset_combo.blockSignals(False)
        self._btn_render.setEnabled(False)

    def set_assets(self, assets: list) -> None:
        """assets: list[RadarAsset] for the currently-selected platform/date.
        Only call this once a search has actually finished -- an in-progress
        search should show a "searching" status instead (see
        _on_platform_changed), not an empty list here, which would read as
        a completed "nothing found" rather than "still looking"."""
        self._clear_assets()
        self._assets = list(assets)
        self._asset_combo.blockSignals(True)
        for asset in self._assets:
            label = asset.nominal_time.strftime("%H:%M:%S UTC") if asset.nominal_time else asset.name
            self._asset_combo.addItem(f"{label} ({asset.format})", asset)
        self._asset_combo.blockSignals(False)
        self.set_status(f"{len(self._assets)} volume(s) found" if self._assets else "No volumes found for this date")
        if self._assets:
            self._on_asset_changed(0)

    def set_volume_summary(self, volume, *, keep_map_on: bool = False) -> None:
        """volume: a decoded RadarVolume (or None to clear). Populates the
        sweep/field selectors and shows the RHI/non-mappable state when the
        loaded volume's geometry hasn't been confirmed for map rendering.

        keep_map_on: when True (the clock-follow path in MainWindow), a
        volume switch keeps MAP checked and re-renders with it, preserving
        the previously-selected field/sweep where the new volume still has
        them. Default behavior (a manual asset pick) still always clears
        MAP -- unexpectedly leaving an old render up when you picked a
        different file yourself would be worse than requiring a re-check.
        """
        was_checked = self._btn_render.isChecked()
        prev_field = self._field_combo.currentText()
        prev_sweep = self._sweep_combo.currentData()
        if not keep_map_on:
            self._btn_render.setChecked(False)
        self._btn_render.hide()
        # Blocked during population: an empty combo's first addItem() sets
        # currentIndex 0, firing currentIndexChanged -- with MAP still
        # checked (keep_map_on path), that would trigger a premature render
        # on whatever field/sweep happens to land first, before the
        # explicit re-selection below ever runs.
        self._sweep_combo.blockSignals(True)
        self._field_combo.blockSignals(True)
        self._sweep_combo.clear()
        self._field_combo.clear()
        try:
            if volume is None:
                self._btn_render.setEnabled(False)
                return
            for name in sorted(volume.fields):
                self._field_combo.addItem(name)
            if volume.scan_type != "ppi":
                self._btn_render.setEnabled(False)
                self.set_status(
                    f"{volume.scan_type.upper()} volume"
                )
                return
            for i in range(int(volume.sweep_start.size)):
                self._sweep_combo.addItem(f"Sweep {i}", i)
            self._btn_render.setEnabled(self._sweep_combo.count() > 0 and self._field_combo.count() > 0)
            self._btn_render.setVisible(self._btn_render.isEnabled())
        finally:
            self._sweep_combo.blockSignals(False)
            self._field_combo.blockSignals(False)
        self.set_status(f"{self._sweep_combo.count()} sweep(s), {self._field_combo.count()} field(s) — ready to render")
        if keep_map_on and was_checked and self._btn_render.isEnabled():
            field_index = self._field_combo.findText(prev_field)
            self._field_combo.blockSignals(True)
            if field_index >= 0:
                self._field_combo.setCurrentIndex(field_index)
            self._field_combo.blockSignals(False)
            self._sweep_combo.blockSignals(True)
            if prev_sweep is not None and prev_sweep < self._sweep_combo.count():
                self._sweep_combo.setCurrentIndex(prev_sweep)
            self._sweep_combo.blockSignals(False)
            self._btn_render.setChecked(True)
            self._on_render_clicked()

    def _on_platform_changed(self, _index: int) -> None:
        self._clear_assets()
        self.set_volume_summary(None)
        platform = self.current_platform()
        if platform is not None:
            # NOXP discovery is a real bounded network crawl, not a lookup
            # -- it can take anywhere from a few seconds to over a minute
            # (see planning/archive-browse-backlog.md's GUI-wiring
            # writeup), so say so rather than leaving the volume list
            # looking like an already-completed "nothing here" result.
            self.set_status(f"Searching {platform.display_name}…")
            self.platform_selected.emit(platform.platform_id)

    def _on_asset_changed(self, _index: int) -> None:
        self.set_volume_summary(None)
        asset = self._asset_combo.currentData()
        if asset is not None:
            self.set_status(f"Loading {asset.name}…")
            self.asset_selected.emit(asset)

    def _on_render_clicked(self) -> None:
        sweep_index = self._sweep_combo.currentData()
        field_name = self._field_combo.currentText()
        if sweep_index is not None and field_name:
            self.render_requested.emit(int(sweep_index), field_name)

    def _on_map_toggled(self, checked):
        self.map_toggled.emit(checked)
        if checked:
            self._on_render_clicked()

    def _on_selection_changed(self, _index):
        if self._btn_render.isChecked():
            self._on_render_clicked()
