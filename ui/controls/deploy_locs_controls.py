
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QLabel, QSlider,
)
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt
from PyQt6.QtGui import QFont


# best → worst color ramp (rank 1 = green, rank 5 = red)
_RANK_COLORS = ["#2DC653", "#A8C538", "#FFD166", "#FF8C42", "#EF233C"]
_NULL_COLOR  = "#888888"

METRICS = [
    ("rank_abi", "RANK ABI"),
    ("rank_aoi", "RANK AOI"),
    ("rqi",      "RQI"),
]

_METRIC_TOOLTIPS = {
    "rank_abi": "Annular Blockage Index · beam-blockage rank (1 best)",
    "rank_aoi": "Areal Occultation Index · terrain-blockage rank (1 best)",
    "rqi":      "Road Quality Index · 0–1 (1 best)",
}

_LEGENDS = {
    "rank_abi": [
        ("#2DC653", "1"), ("#A8C538", "2"), ("#FFD166", "3"),
        ("#FF8C42", "4"), ("#EF233C", "5"), ("#888888", "N/A"),
    ],
    "rank_aoi": [
        ("#2DC653", "1"), ("#A8C538", "2"), ("#FFD166", "3"),
        ("#FF8C42", "4"), ("#EF233C", "5"), ("#888888", "N/A"),
    ],
    "rqi": [
        ("#2DC653", "0.8–1.0"), ("#A8C538", "0.6–0.8"), ("#FFD166", "0.4–0.6"),
        ("#FF8C42", "0.2–0.4"), ("#EF233C", "0–0.2"),   ("#888888", "N/A"),
    ],
}

# (slider_min, slider_max, slider_default)
_SLIDER_CFG = {
    "rank_abi": (1, 5, 1),
    "rank_aoi": (1, 5, 1),
    "rqi":      (0, 10, 8),
}


def _slider_to_threshold(metric: str, slider_val: int) -> float:
    """Convert raw slider integer to the threshold value used for filtering."""
    if metric == "rqi":
        return slider_val / 10.0
    return float(slider_val)


def _slider_label_text(metric: str, slider_val: int) -> str:
    if metric in ("rank_abi", "rank_aoi"):
        if slider_val == 1:
            return "rank 1 only"
        return f"rank 1 – {slider_val}"
    else:
        return f"RQI ≥ {slider_val / 10:.1f}"


class DeployLocsControls(QWidget):
    """
    Floating drawer for previous deployment location color-coding + threshold
    filtering.

    Signals:
        metric_changed(key)             — active metric switched
        filter_changed(metric, threshold) — slider or metric changed; apply
                                           a MapLibre filter at this threshold
    """

    metric_changed   = pyqtSignal(str)          # "rank_abi" | "rank_aoi" | "rqi"
    filter_changed   = pyqtSignal(str, float)   # (metric, threshold)
    size_changed     = pyqtSignal(int)          # circle radius in pixels
    content_resized  = pyqtSignal()             # triggers layout pulse in main_window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation  = None
        self._updating   = False
        self._setup_ui()


    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("deployLocsDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._btns: dict[str, QToolButton] = {}
        for key, label in METRICS:
            b = self._btn(label)
            b.setToolTip(_METRIC_TOOLTIPS.get(key, ""))
            b.toggled.connect(lambda on, k=key: self._on_metric_toggled(k, on))
            row.addWidget(b)
            self._btns[key] = b

        row.addStretch(1)
        col.addWidget(btn_row)

        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setFont(QFont("Helvetica Neue", 9))
        self._legend.setStyleSheet(
            "color: #B5BDCC; background: transparent; padding: 1px 4px 2px 4px;"
        )
        self._legend.setVisible(False)
        col.addWidget(self._legend)

        self._filter_row = QWidget()
        self._filter_row.setObjectName("deployLocsFilterRow")
        fr = QHBoxLayout(self._filter_row)
        fr.setContentsMargins(4, 2, 4, 0)
        fr.setSpacing(8)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("deployLocsSlider")
        self._slider.setMinimumWidth(90)
        self._slider.valueChanged.connect(self._on_slider_changed)

        self._slider_label = QLabel()
        self._slider_label.setObjectName("deployLocsSliderLabel")
        self._slider_label.setFont(QFont("Helvetica Neue", 9))
        self._slider_label.setStyleSheet(
            "color: #B5BDCC; background: transparent;"
        )
        self._slider_label.setMinimumWidth(80)

        fr.addWidget(self._slider, 1)
        fr.addWidget(self._slider_label)
        self._filter_row.setVisible(False)
        col.addWidget(self._filter_row)

        self._size_row = QWidget()
        self._size_row.setObjectName("deployLocsSizeRow")
        sr = QHBoxLayout(self._size_row)
        sr.setContentsMargins(4, 2, 4, 0)
        sr.setSpacing(8)

        size_lbl = QLabel("Size")
        size_lbl.setFont(QFont("Helvetica Neue", 9))
        size_lbl.setStyleSheet("color: #B5BDCC; background: transparent;")

        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setObjectName("deployLocsSizeSlider")
        self._size_slider.setMinimum(2)
        self._size_slider.setMaximum(20)
        self._size_slider.setValue(6)
        self._size_slider.setMinimumWidth(90)
        self._size_slider.valueChanged.connect(self._on_size_changed)

        self._size_val_label = QLabel("6 px")
        self._size_val_label.setFont(QFont("Helvetica Neue", 9))
        self._size_val_label.setStyleSheet("color: #B5BDCC; background: transparent;")
        self._size_val_label.setMinimumWidth(36)

        sr.addWidget(size_lbl)
        sr.addWidget(self._size_slider, 1)
        sr.addWidget(self._size_val_label)
        self._size_row.setVisible(False)
        col.addWidget(self._size_row)

        outer.addWidget(self._drawer)

        # default to rank_abi without firing the signal yet
        self._updating = True
        self._btns["rank_abi"].setChecked(True)
        self._updating = False

        # initialize slider config for the default metric (no emit)
        self._init_slider("rank_abi")

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b


    def toggle_drawer(self, checked: bool):
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


    def current_metric(self) -> str:
        for key, b in self._btns.items():
            if b.isChecked():
                return key
        return "rank_abi"

    def current_threshold(self) -> float:
        return _slider_to_threshold(self.current_metric(), self._slider.value())


    def _init_slider(self, metric: str):
        """Set slider range/default for metric without emitting filter_changed."""
        mn, mx, default = _SLIDER_CFG[metric]
        self._slider.blockSignals(True)
        self._slider.setMinimum(mn)
        self._slider.setMaximum(mx)
        self._slider.setValue(default)
        self._slider.blockSignals(False)
        self._slider_label.setText(_slider_label_text(metric, default))
        self._filter_row.setVisible(True)
        self._size_row.setVisible(True)

    def _on_metric_toggled(self, key: str, checked: bool):
        if self._updating:
            return
        if checked:
            # deselect siblings
            self._updating = True
            try:
                for k, b in self._btns.items():
                    if k != key:
                        b.setChecked(False)
            finally:
                self._updating = False
            self.metric_changed.emit(key)
            self._update_legend(key)
            self._init_slider(key)
            self.filter_changed.emit(key, _slider_to_threshold(key, _SLIDER_CFG[key][2]))
        else:
            # prevent deselecting the last active button
            if not any(b.isChecked() for b in self._btns.values()):
                self._updating = True
                try:
                    self._btns[key].setChecked(True)
                finally:
                    self._updating = False

    def _on_slider_changed(self, value: int):
        metric = self.current_metric()
        self._slider_label.setText(_slider_label_text(metric, value))
        self.filter_changed.emit(metric, _slider_to_threshold(metric, value))

    def _on_size_changed(self, value: int):
        self._size_val_label.setText(f"{value} px")
        self.size_changed.emit(value)

    def _update_legend(self, metric: str):
        entries = _LEGENDS.get(metric, [])
        parts = [
            f'<span style="color:{c}; font-size:11px;">■</span>'
            f'&thinsp;<span style="font-size:9px;">{lbl}</span>'
            for c, lbl in entries
        ]
        self._legend.setText("&nbsp;&nbsp;".join(parts))
        self._legend.setVisible(True)

        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            target  = self.sizeHint().height()
            current = self.height()
            if target == current:
                return
            if self._animation:
                self._animation.stop()
            anim = QPropertyAnimation(self, b"maximumHeight")
            anim.setDuration(120)
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
            anim.start()
            self._animation = anim
            self.content_resized.emit()
