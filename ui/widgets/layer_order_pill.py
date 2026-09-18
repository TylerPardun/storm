
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, pyqtSignal


DEFAULT_LAYERS: list[str] = [
    "nlcd",
    "satellite",
    "mesoanalysis",
    "sfcoa",
    "radar",
    "cwa",
    "spc_outlook",
    "spc_tor",
    "spc_wind",
    "spc_hail",
    "spc_prob",
    "spc_sig",
    "spc_watches",
    "spc_mds",
    "nws_warnings",
    "scan_sectors",
    "annotations",
    "storm_cones",
    "route",
    "damage_paths",
    "ok_mesonet",
    "wtm",
    "ks_mesonet",
    "co_mesonet",
    "ne_mesonet",
    "asos",
]

LAYER_LABELS: dict[str, str] = {
    "satellite":    "Satellite",
    "nlcd":         "NLCD",
    "mesoanalysis": "Mesoanalysis",
    "sfcoa":       "SFCOA",
    "radar":        "Radar",
    "cwa":          "CWA",
    "spc_outlook":  "SPC Outlook",
    "spc_tor":      "SPC Tornado",
    "spc_wind":     "SPC Wind",
    "spc_hail":     "SPC Hail",
    "spc_prob":     "SPC Prob",
    "spc_sig":      "SPC Sig",
    "spc_watches":  "SPC Watches",
    "spc_mds":      "SPC MDs",
    "nws_warnings": "NWS Warnings",
    "scan_sectors": "Scan Sectors",
    "annotations":  "Annotations",
    "storm_cones":  "Storm Cones",
    "route":        "Route",
    "damage_paths": "Damage Paths",
    "ok_mesonet":   "OK Mesonet",
    "wtm":          "WTM",
    "ks_mesonet":   "KS Mesonet",
    "co_mesonet":   "CO Mesonet",
    "ne_mesonet":   "NE Mesonet",
    "asos":         "ASOS",
}

MAPLIBRE_LAYERS: dict[str, list[str]] = {
    "satellite":    ["sat-layer", "sat-wms-layer"],
    "nlcd":         ["nlcd-landcover"],
    "mesoanalysis": ["mesoanalysis-line", "mesoanalysis-label"],
    "sfcoa":       ["sfcoa-fill", "sfcoa-line", "sfcoa-label"],
    "radar":        ["radar-overlay", "noxp-overlay"],
    "cwa":          ["cwa-fill", "cwa-line", "cwa-label"],
    "spc_outlook":  ["spc-cat-fill", "spc-cat-line"],
    "spc_tor":      ["spc-tor-fill", "spc-tor-line", "spc-tor-sig-base",
                     "spc-tor-sign", "spc-tor-cig1", "spc-tor-cig2",
                     "spc-tor-cig3", "spc-tor-sig-line"],
    "spc_wind":     ["spc-wind-fill", "spc-wind-line", "spc-wind-sig-base",
                     "spc-wind-sign", "spc-wind-cig1", "spc-wind-cig2",
                     "spc-wind-cig3", "spc-wind-sig-line"],
    "spc_hail":     ["spc-hail-fill", "spc-hail-line", "spc-hail-sig-base",
                     "spc-hail-sign", "spc-hail-cig1", "spc-hail-cig2",
                     "spc-hail-cig3", "spc-hail-sig-line"],
    "spc_prob":     ["spc-prob-fill", "spc-prob-line", "spc-prob-sig-base",
                     "spc-prob-sign", "spc-prob-cig1", "spc-prob-cig2",
                     "spc-prob-cig3", "spc-prob-sig-line"],
    "spc_sig":      ["spc-sig-fill", "spc-sig-line", "spc-sig-sig-base",
                     "spc-sig-sign", "spc-sig-cig1", "spc-sig-cig2",
                     "spc-sig-cig3", "spc-sig-sig-line"],
    "spc_watches":  ["spc-watches-fill", "spc-watches-line"],
    "spc_mds":      ["spc-mds-fill", "spc-mds-line"],
    "nws_warnings": ["nws-warnings-fill", "nws-warnings-line"],
    "scan_sectors": ["scan-sector-fill", "scan-sector-line", "scan-sector-point", "scan-sector-label"],
    "annotations":  [],
    "storm_cones":  [],
    "route":        ["route-casing", "route-line"],
    "damage_paths": ["damage-paths-casing", "damage-paths-line", "damage-paths-start", "damage-paths-end"],
    "ok_mesonet":   [],
    "wtm":          [],
    "ks_mesonet":   [],
    "co_mesonet":   [],
    "ne_mesonet":   [],
    "asos":         ["asos-plots"],
}

_SETTINGS_KEY = "layers/order"


def load_layer_order() -> list[str]:
    s = QSettings("NSSL", "STORM")
    saved = s.value(_SETTINGS_KEY, None)
    if isinstance(saved, list):
        merged = [k for k in saved if k in LAYER_LABELS]
        for k in DEFAULT_LAYERS:
            if k not in merged:
                merged.append(k)
        return merged
    return list(DEFAULT_LAYERS)


def save_layer_order(order: list[str]) -> None:
    QSettings("NSSL", "STORM").setValue(_SETTINGS_KEY, order)


class LayerOrderPill(QWidget):
    """Floating layer-order pill.  Expands upward from the toggle button.

    Signals:
        order_changed(list[str]) — emitted on confirm; payload is bottom→top.
    """

    order_changed = pyqtSignal(list)
    size_changed  = pyqtSignal()       # emitted whenever the pill resizes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("layerOrderPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._expanded = False
        self._order: list[str] = load_layer_order()
        self._saved_order: list[str] = list(self._order)
        self._active: set[str] = set()
        self._in_relayout = False

        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setObjectName("layerOrderToggle")
        self._toggle_btn.setStyleSheet(
            "QToolButton { background: rgba(15,15,26,0.95); "
            "border: 1px solid rgba(74,83,108,0.55); border-radius: 8px; "
            "color: #C8D0DE; font-size: 10px; font-weight: 600; "
            "letter-spacing: 0.5px; padding: 5px 10px; }"
            "QToolButton:hover { border-color: #4A9EFF; color: #4A9EFF; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_expanded)

        self._panel = QWidget(self)
        self._panel.setObjectName("layerOrderPanel")
        self._panel.setMinimumWidth(248)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        title = QLabel("LAYER ORDER")
        title.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1.3px; color: #4A9EFF;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self._btn_reset = QPushButton("RESET")
        self._btn_reset.setFixedHeight(20)
        self._btn_reset.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2E2E4E; "
            "border-radius: 4px; color: #6E7A8F; font-size: 9px; "
            "font-weight: 600; letter-spacing: 0.8px; padding: 0 6px; }"
            "QPushButton:hover { border-color: #4A9EFF; color: #4A9EFF; }"
        )
        self._btn_reset.clicked.connect(self._on_reset)
        hdr.addWidget(self._btn_reset)
        panel_layout.addLayout(hdr)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #1E1E2E; margin: 2px 0;")
        panel_layout.addWidget(div)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        panel_layout.addWidget(self._rows_widget)

        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("color: #1E1E2E; margin: 2px 0;")
        panel_layout.addWidget(div2)

        self._btn_confirm = QPushButton("APPLY ORDER")
        self._btn_confirm.setStyleSheet(
            "QPushButton { background: rgba(0,207,255,0.12); border: 1px solid #00CFFF; "
            "border-radius: 5px; color: #00CFFF; font-size: 10px; "
            "font-weight: 700; letter-spacing: 0.8px; padding: 4px 0; }"
            "QPushButton:hover { background: rgba(0,207,255,0.22); }"
            "QPushButton:pressed { background: rgba(0,207,255,0.35); }"
        )
        self._btn_confirm.clicked.connect(self._on_confirm)
        panel_layout.addWidget(self._btn_confirm)

        self._panel.hide()

        self._refresh_label()
        self._relayout()


    def _relayout(self):
        """Size and position children; resize the pill itself.

        The toggle button is always at the BOTTOM of the pill (y = pill_h - btn_h).
        The panel, when visible, sits above it.  _layout_overlays in MainWindow
        positions the pill by its bottom-left corner, so growing upward is free.
        """
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            self._do_relayout()
        finally:
            self._in_relayout = False

    def _do_relayout(self):
        self._toggle_btn.adjustSize()
        btn_w = self._toggle_btn.sizeHint().width()
        btn_h = self._toggle_btn.sizeHint().height()

        if self._expanded and not self._panel.isHidden():
            self._rows_widget.layout().activate()
            self._panel.layout().activate()
            self._rows_widget.adjustSize()
            self._panel.adjustSize()
            panel_w = max(self._panel.sizeHint().width(), btn_w, self._panel.minimumWidth())
            panel_h = self._panel.sizeHint().height()
            GAP = 4
            total_w = panel_w
            total_h = panel_h + GAP + btn_h
            self._panel.setGeometry(0, 0, panel_w, panel_h)
            self._toggle_btn.setGeometry(0, panel_h + GAP, total_w, btn_h)
        else:
            total_w = btn_w
            total_h = btn_h
            self._toggle_btn.setGeometry(0, 0, btn_w, btn_h)

        prev = self.size()
        self.setFixedSize(total_w, total_h)
        if self.width() != prev.width() or self.height() != prev.height():
            self.size_changed.emit()


    def _build_rows(self):
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()

        display_order = self._display_order()
        for i, key in enumerate(display_order):
            self._rows_layout.addWidget(self._make_row(key, i, len(display_order)))
        self._rows_layout.addStretch(1)

    def _make_row(self, key: str, display_idx: int, total: int) -> QWidget:
        active = key in self._active
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        lbl = QLabel(LAYER_LABELS.get(key, key))
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 500; "
            f"color: {'#C8D0DE' if active else '#3A3B5A'};"
        )
        rl.addWidget(lbl, 1)

        dot = QLabel("●")
        dot.setStyleSheet(f"font-size: 8px; color: {'#39D98A' if active else '#1E2434'};")
        rl.addWidget(dot)

        btn_up = QToolButton()
        btn_up.setText("↑")
        btn_up.setFixedSize(20, 20)
        btn_up.setEnabled(display_idx > 0)
        btn_up.setStyleSheet(self._arrow_style(display_idx > 0))
        btn_up.clicked.connect(lambda _, k=key: self._move_layer(k, -1))
        rl.addWidget(btn_up)

        btn_dn = QToolButton()
        btn_dn.setText("↓")
        btn_dn.setFixedSize(20, 20)
        btn_dn.setEnabled(display_idx < total - 1)
        btn_dn.setStyleSheet(self._arrow_style(display_idx < total - 1))
        btn_dn.clicked.connect(lambda _, k=key: self._move_layer(k, +1))
        rl.addWidget(btn_dn)

        return row

    @staticmethod
    def _arrow_style(enabled: bool) -> str:
        color = "#C8D0DE" if enabled else "#2A2B3A"
        return (
            f"QToolButton {{ background: transparent; border: 1px solid #1E1E2E; "
            f"border-radius: 3px; color: {color}; font-size: 11px; }}"
            f"QToolButton:hover {{ border-color: #4A9EFF; color: #4A9EFF; }}"
        )


    def _toggle_expanded(self):
        self._expanded = not self._expanded
        self._refresh_label()
        if self._expanded:
            self._build_rows()
            self._panel.show()
        else:
            self._panel.hide()
        self._relayout()

    def _move_layer(self, key: str, direction: int):
        visible = self._visible_order()
        if key not in visible:
            return
        stack_idx = visible.index(key)
        new_idx = max(0, min(len(visible) - 1, stack_idx + (-direction)))
        if new_idx == stack_idx:
            return
        visible.insert(new_idx, visible.pop(stack_idx))
        self._order = self._merge_order(visible)
        QTimer.singleShot(0, self._rebuild_and_relayout)

    def _rebuild_and_relayout(self):
        if not self._expanded:
            return
        self._build_rows()
        self._relayout()

    def _on_confirm(self):
        self._saved_order = list(self._order)
        save_layer_order(self._saved_order)
        self._refresh_label()
        self.order_changed.emit(list(self._order))

    def _on_reset(self):
        self._order = list(self._saved_order)
        self._build_rows()
        self._relayout()


    def set_layer_active(self, key: str, active: bool):
        if active:
            self._active.add(key)
        else:
            self._active.discard(key)
        self._refresh_label()
        if self._expanded:
            self._build_rows()
        self._relayout()

    def current_order(self) -> list[str]:
        return list(self._saved_order)


    def _refresh_label(self):
        arrow = "▲" if self._expanded else "▼"
        visible = len(self._active)
        self._toggle_btn.setText(
            f"LAYERS  {visible}  {arrow}"
        )
        self._toggle_btn.adjustSize()

    def _visible_order(self) -> list[str]:
        return [key for key in self._order if key in self._active]

    def _display_order(self) -> list[str]:
        visible = list(reversed(self._visible_order()))
        if visible:
            return visible
        return []

    def _merge_order(self, visible: list[str]) -> list[str]:
        merged: list[str] = []
        visible_iter = iter(visible)
        active_set = set(self._active)
        for key in self._order:
            if key in active_set:
                merged.append(next(visible_iter))
            else:
                merged.append(key)
        return merged
