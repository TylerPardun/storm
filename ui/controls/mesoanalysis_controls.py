from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QToolButton, QPushButton,
)

_PRODUCT_BUTTON_WIDTH = 72
_PRODUCT_COLUMN_WIDTH = 118
_PLAYBACK_BUTTON_SIZE = 17
_TIME_LABEL_WIDTH = 36
_CONTROL_HEIGHT = 22


MESO_PRODUCT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Thermo", (
        ("temp", "Temp"),
        ("dwpt", "Dewpoint"),
        ("mslp", "MSLP"),
        ("cape03", "0-3 CAPE"),
    )),
    ("Surface Parcel", (
        ("sfccape", "SFC CAPE"),
        ("sfccinh", "SFC CINH"),
        ("sfclcl", "SFC LCL"),
        ("sfclfc", "SFC LFC"),
    )),
    ("Mixed Layer Parcel", (
        ("mlcape", "ML CAPE"),
        ("mlcinh", "ML CINH"),
        ("mllcl", "ML LCL"),
        ("mllfc", "ML LFC"),
    )),
    ("Most Unstable Parcel", (
        ("mucape", "MU CAPE"),
        ("mucinh", "MU CINH"),
        ("mulcl", "MU LCL"),
        ("mulfc", "MU LFC"),
    )),
    ("Kinematic", (
        ("srh01", "0-1 SRH"),
        ("srh03", "0-3 SRH"),
    )),
    ("Composite", (
        ("scp", "SCP"),
        ("stpc", "STP"),
    )),
)


class MesoanalysisControls(QWidget):
    """Grouped product palette for Satsquatch mesoanalysis contour overlays."""

    product_toggled = pyqtSignal(str, bool)
    frame_requested = pyqtSignal(int)
    refresh_requested = pyqtSignal()
    visible_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating = False
        self._times: list[str] = []
        self._time_labels: list[str] = []
        self._current_frame = 0
        self._active_products: set[str] = set()
        self._product_buttons: dict[str, QToolButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drawer = QWidget()
        self._drawer.setObjectName("mesoanalysisDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        groups = QWidget()
        groups.setObjectName("mesoanalysisProductGrid")
        grid = QGridLayout(groups)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        for col_idx in range(len(MESO_PRODUCT_GROUPS)):
            grid.setColumnMinimumWidth(col_idx, _PRODUCT_COLUMN_WIDTH)

        for col_idx, (group_name, products) in enumerate(MESO_PRODUCT_GROUPS):
            label = QLabel(group_name.upper())
            label.setObjectName("mesoanalysisGroupLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(_PRODUCT_COLUMN_WIDTH)
            grid.addWidget(label, 0, col_idx)

            for row_idx, (product_id, text) in enumerate(products, start=1):
                btn = QToolButton()
                btn.setText(text)
                btn.setCheckable(True)
                btn.setFixedWidth(_PRODUCT_BUTTON_WIDTH)
                btn.setProperty("paletteButton", True)
                btn.clicked.connect(
                    lambda checked=False, pid=product_id: self._toggle_product(pid, checked)
                )
                self._product_buttons[product_id] = btn
                grid.addWidget(btn, row_idx, col_idx, Qt.AlignmentFlag.AlignHCenter)

        self._btn_back = self._pbtn("◀", "")
        self._btn_fwd = self._pbtn("▶", "")
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        for btn in (self._btn_back, self._btn_fwd):
            btn.setEnabled(False)

        time_wrap = QWidget()
        time_wrap.setObjectName("mesoanalysisPlaybackCell")
        time_grid = QGridLayout(time_wrap)
        time_grid.setContentsMargins(0, 0, 0, 0)
        time_grid.setHorizontalSpacing(1)
        time_grid.setVerticalSpacing(0)
        time_grid.addWidget(self._btn_back, 0, 0)
        self._time_label = QLabel("--:--Z")
        self._time_label.setObjectName("mesoanalysisTimeLabel")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setFixedSize(_TIME_LABEL_WIDTH, _CONTROL_HEIGHT)
        time_grid.addWidget(self._time_label, 0, 1)
        time_grid.addWidget(self._btn_fwd, 0, 2)
        grid.addWidget(time_wrap, 4, 4, Qt.AlignmentFlag.AlignHCenter)

        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setFixedSize(_PRODUCT_BUTTON_WIDTH, _CONTROL_HEIGHT)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        grid.addWidget(self._refresh_btn, 4, 5, Qt.AlignmentFlag.AlignHCenter)

        col.addWidget(groups)
        outer.addWidget(self._drawer)

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

    def set_products(self, products) -> None:
        available = {product.product_id for product in products}
        for product_id, btn in self._product_buttons.items():
            btn.setEnabled(product_id in available)

    def set_times(self, times) -> None:
        current = self.current_time()
        self._times = [item.time_id for item in times]
        self._time_labels = [item.label for item in times]
        idx = self._times.index(current) if current in self._times else max(0, len(self._times) - 1)
        self.set_cache_size(len(self._times))
        self.set_frame(idx)

    def set_status(self, text: str) -> None:
        self.setToolTip(str(text or ""))

    def current_product(self) -> str:
        return sorted(self._active_products)[0] if self._active_products else ""

    def active_products(self) -> set[str]:
        return set(self._active_products)

    def current_time(self) -> str:
        if 0 <= self._current_frame < len(self._times):
            return self._times[self._current_frame]
        return ""

    def clear_selection(self) -> None:
        self.stop_loop()
        active = set(self._active_products)
        self._active_products.clear()
        self._times = []
        self._time_labels = []
        self.set_cache_size(0)
        for btn in self._product_buttons.values():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        for product_id in active:
            self.product_toggled.emit(product_id, False)
        self.visible_toggled.emit(False)
        self.set_status("Meso hidden")

    def _on_refresh_clicked(self) -> None:
        self.clear_selection()
        self.refresh_requested.emit()

    def set_cache_size(self, n: int) -> None:
        n = max(0, int(n))
        if n == 0:
            self._current_frame = 0
        else:
            self._current_frame = min(self._current_frame, n - 1)
        has_history = n > 1
        for w in (self._btn_back, self._btn_fwd):
            w.setEnabled(has_history)
        if n == 0:
            self._time_label.setText("--:--Z")

    def set_frame(self, idx: int) -> None:
        if not self._times:
            self._current_frame = 0
            self._time_label.setText("--:--Z")
            return
        self._current_frame = max(0, min(len(self._times) - 1, int(idx)))
        if 0 <= self._current_frame < len(self._time_labels):
            self._time_label.setText(self._time_labels[self._current_frame])

    def current_frame(self) -> int:
        return self._current_frame

    def stop_loop(self) -> None:
        return

    def _toggle_product(self, product_id: str, checked: bool) -> None:
        if self._updating:
            return
        self.stop_loop()
        if checked:
            self._active_products.add(product_id)
            self.visible_toggled.emit(True)
        else:
            self._active_products.discard(product_id)
            if not self._active_products:
                self.visible_toggled.emit(False)
        self.product_toggled.emit(product_id, checked)

    def _on_step_back(self) -> None:
        idx = max(0, self._current_frame - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_forward(self) -> None:
        idx = min(max(0, len(self._times) - 1), self._current_frame + 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _pbtn(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(_PLAYBACK_BUTTON_SIZE, _CONTROL_HEIGHT)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        return btn
