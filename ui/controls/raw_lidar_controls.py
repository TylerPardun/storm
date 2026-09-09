"""Floating drawer for archive-mode CLAMPS raw-lidar browsing: pick a
known source (site + product), then a discovered file, then open its
quicklook. Mirrors MesoanalysisControls'/NoxpControls' collapsible-drawer
shell (ui/controls/mesoanalysis_controls.py, ui/controls/noxp_controls.py)."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
)


class RawLidarControls(QWidget):
    """Signals
    -------
    source_selected(str)       platform_id (a KNOWN_RAW_LIDAR_SOURCES entry)
    quicklook_requested(str, object)   platform_id, LidarAsset
    """

    source_selected = pyqtSignal(str)
    quicklook_requested = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._assets_by_source: dict[str, list] = {}
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
        source_row.addWidget(QLabel("Source"))
        self._source_combo = QComboBox()
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self._source_combo, stretch=1)
        col.addLayout(source_row)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("File"))
        self._asset_combo = QComboBox()
        asset_row.addWidget(self._asset_combo, stretch=1)
        self._btn_view = QPushButton("VIEW QUICKLOOK")
        self._btn_view.clicked.connect(self._on_view_clicked)
        self._btn_view.setEnabled(False)
        asset_row.addWidget(self._btn_view)
        col.addLayout(asset_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        col.addWidget(self._status_label)

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
        """sources: iterable of RawLidarSource (KNOWN_RAW_LIDAR_SOURCES)."""
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        for source in sources:
            self._source_combo.addItem(source.platform_id, source)
        self._source_combo.blockSignals(False)

    def current_source(self):
        return self._source_combo.currentData()

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text or ""))

    def set_assets_for_all_sources(self, assets_by_source: dict) -> None:
        """assets_by_source: platform_id -> list[LidarAsset], from one
        discovery pass across every known source for the archive date."""
        self._assets_by_source = dict(assets_by_source)
        self.set_status(
            f"{len(self._assets_by_source)} of {self._source_combo.count()} source(s) have data on this date"
        )
        self._refresh_asset_combo()

    def _refresh_asset_combo(self) -> None:
        source = self.current_source()
        self._asset_combo.blockSignals(True)
        self._asset_combo.clear()
        assets = self._assets_by_source.get(source.platform_id, []) if source is not None else []
        for asset in assets:
            self._asset_combo.addItem(asset.filename, asset)
        self._asset_combo.blockSignals(False)
        self._btn_view.setEnabled(self._asset_combo.count() > 0)

    def _on_source_changed(self, _index: int) -> None:
        self._refresh_asset_combo()
        source = self.current_source()
        if source is not None:
            self.source_selected.emit(source.platform_id)

    def _on_view_clicked(self) -> None:
        source = self.current_source()
        asset = self._asset_combo.currentData()
        if source is not None and asset is not None:
            self.quicklook_requested.emit(source.platform_id, asset)
