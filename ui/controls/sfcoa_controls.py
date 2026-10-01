from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QToolButton,
    QPushButton, QSizePolicy, QFrame,
)

_PRODUCT_BUTTON_MIN_WIDTH = 64
_PRODUCT_BUTTON_MIN_HEIGHT = 22
_PLAYBACK_BUTTON_SIZE = 32
_REFRESH_BUTTON_WIDTH = 72
_STATUS_LABEL_WIDTH = 170

_GROUP_ORDER = ("thermo", "parcel", "lapse", "shear", "helicity", "composite", "upper", "kinematic")
_GROUP_LABELS = {
    "thermo": "SURFACE",
    "parcel": "PARCEL",
    "lapse": "LAPSE RATES",
    "shear": "SHEAR",
    "helicity": "SRH",
    "composite": "COMPOSITE",
    "upper": "UPPER AIR",
    "kinematic": "WINDS",
}

_COLUMN_GROUPS = (
    ("thermo",),
    ("parcel",),
    ("lapse", "composite"),
    ("helicity", "shear"),
    ("upper", "kinematic"),
)


class SfcoaControls(QWidget):
    """Adaptive SFCOA mesoanalysis product drawer."""

    product_toggled = pyqtSignal(str, bool)
    frame_requested = pyqtSignal(int)
    refresh_requested = pyqtSignal()
    visible_toggled = pyqtSignal(bool)
    content_resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._drawer_open_requested = False
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
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drawer = QWidget()
        self._drawer.setObjectName("sfcoaDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row = QWidget()
        row.setObjectName("sfcoaControlRow")
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(4)

        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setObjectName("sfcoaRefreshButton")
        self._refresh_btn.setFixedSize(_REFRESH_BUTTON_WIDTH, 22)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        r.addWidget(self._refresh_btn)
        r.addWidget(self._vdiv())

        self._btn_jump_start = self._pbtn("⏮", "First valid time")
        self._btn_back = self._pbtn("⏪", "")
        self._btn_fwd = self._pbtn("⏩", "")
        self._btn_jump_end = self._pbtn("⏭", "Latest valid time")
        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)
        for btn in (self._btn_jump_start, self._btn_back, self._btn_fwd, self._btn_jump_end):
            btn.setEnabled(False)
            r.addWidget(btn)

        self._status_label = QLabel("")
        self._status_label.setObjectName("sfcoaTimeLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._status_label.setFixedSize(_STATUS_LABEL_WIDTH, 22)
        r.addWidget(self._status_label)
        col.addWidget(row)

        self._product_grid_widget = QWidget()
        self._product_grid_widget.setObjectName("sfcoaProductGrid")
        self._product_grid = QGridLayout(self._product_grid_widget)
        self._product_grid.setContentsMargins(0, 0, 0, 0)
        self._product_grid.setHorizontalSpacing(0)
        self._product_grid.setVerticalSpacing(3)
        col.addWidget(self._product_grid_widget)
        outer.addWidget(self._drawer)

    def toggle_drawer(self, checked: bool):
        if self._animation:
            self._animation.stop()
            self._animation = None
        if checked:
            self._drawer_open_requested = True
            self.setMaximumHeight(0)
            QTimer.singleShot(0, self._animate_open)
            return
        self._drawer_open_requested = False
        current = self.height()
        self.setMaximumHeight(current)
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start()
        self._animation = anim

    def _animate_open(self) -> None:
        if not self._drawer_open_requested:
            return
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        target = max(1, self.sizeHint().height())
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(0)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.finished.connect(self._finish_open_animation)
        anim.start()
        self._animation = anim

    def _finish_open_animation(self) -> None:
        if self._drawer_open_requested:
            self.setMaximumHeight(16777215)

    def set_times(self, times) -> None:
        current = self.current_time()
        self._times = [item.time_id for item in times]
        self._time_labels = [item.label for item in times]
        idx = self._times.index(current) if current in self._times else max(0, len(self._times) - 1)
        self.set_cache_size(len(self._times))
        self.set_frame(idx)
        self._notify_content_resized()

    def set_products(self, products) -> None:
        previous_active = set(self._active_products)
        available = {product.variable_id for product in products}
        self._active_products &= available

        while self._product_grid.count():
            item = self._product_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._product_buttons.clear()

        grouped: dict[str, list] = {group: [] for group in _GROUP_ORDER}
        for product in products:
            grouped.setdefault(product.group, []).append(product)

        col_idx = 0
        for column_groups in _COLUMN_GROUPS:
            row_idx = 0
            rendered_any = False
            for group in column_groups:
                group_products = grouped.get(group) or []
                if not group_products:
                    continue
                rendered_any = True
                label = QLabel(_GROUP_LABELS.get(group, group.upper()))
                label.setObjectName("sfcoaGroupLabel")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._product_grid.addWidget(label, row_idx, col_idx)
                row_idx += 1
                for product in _sort_products(group, group_products):
                    btn = QToolButton()
                    btn.setText(product.label)
                    btn.setCheckable(True)
                    btn.setChecked(product.variable_id in self._active_products)
                    tip = product.long_name or product.label
                    if product.units:
                        tip = f"{tip} ({product.units})"
                    btn.setToolTip(tip)
                    btn.setMinimumSize(_PRODUCT_BUTTON_MIN_WIDTH, _PRODUCT_BUTTON_MIN_HEIGHT)
                    btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                    btn.setProperty("paletteButton", True)
                    btn.clicked.connect(
                        lambda checked=False, pid=product.variable_id: self._toggle_product(pid, checked)
                    )
                    self._product_buttons[product.variable_id] = btn
                    self._product_grid.addWidget(btn, row_idx, col_idx, Qt.AlignmentFlag.AlignHCenter)
                    row_idx += 1
            if rendered_any:
                col_idx += 1

        for product_id in previous_active - self._active_products:
            self.product_toggled.emit(product_id, False)
        self.visible_toggled.emit(bool(self._active_products))
        self._notify_content_resized()

    def set_status(self, text: str) -> None:
        text = str(text or "")
        self._status_label.setText(text)
        self._status_label.setToolTip(text)

    def current_time(self) -> str:
        if 0 <= self._current_frame < len(self._times):
            return self._times[self._current_frame]
        return ""

    def current_frame(self) -> int:
        return self._current_frame

    def active_products(self) -> set[str]:
        return set(self._active_products)

    def set_cache_size(self, n: int) -> None:
        n = max(0, int(n))
        if n == 0:
            self._current_frame = 0
        else:
            self._current_frame = min(self._current_frame, n - 1)
        has_history = n > 1
        for w in (self._btn_jump_start, self._btn_back, self._btn_fwd, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int) -> None:
        if not self._times:
            self._current_frame = 0
            self.set_status("No times")
            return
        self._current_frame = max(0, min(len(self._times) - 1, int(idx)))
        label = self._time_labels[self._current_frame] if self._current_frame < len(self._time_labels) else self.current_time()
        self.set_status(label)

    def stop_loop(self) -> None:
        return

    def _toggle_product(self, product_id: str, checked: bool) -> None:
        if self._updating:
            return
        if checked:
            self._active_products.add(product_id)
            self.visible_toggled.emit(True)
        else:
            self._active_products.discard(product_id)
            if not self._active_products:
                self.visible_toggled.emit(False)
        self.product_toggled.emit(product_id, checked)

    def _on_jump_start(self) -> None:
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self) -> None:
        idx = max(0, len(self._times) - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_back(self) -> None:
        idx = max(0, self._current_frame - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_forward(self) -> None:
        idx = min(max(0, len(self._times) - 1), self._current_frame + 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _pbtn(self, text: str, tip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(_PLAYBACK_BUTTON_SIZE, 22)
        btn.setToolTip(tip)
        return btn

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    def _notify_content_resized(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        if self._drawer_open_requested or self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
        self.updateGeometry()
        QTimer.singleShot(0, self.content_resized.emit)


def _sort_products(group: str, products: list) -> list:
    return sorted(products, key=lambda product: (_product_sort_key(group, product), product.label))


def _product_sort_key(group: str, product) -> int:
    var_id = str(product.variable_id).lower()
    explicit_order = {
        "thermo": ("tmpc", "dwpc", "sthe", "p", "swnd", "pvor", "vort"),
        "parcel": ("sbcp", "sbcn", "mucp", "mucn", "m1cp", "m1cn", "ml3k", "dncp"),
        "lapse": ("lllr", "lr36", "lr38"),
        "shear": ("s1mg", "s3mg", "s6mg", "s8mg", "bmag"),
        "helicity": ("srh1", "srh3"),
        "composite": ("sigt", "sccp"),
        "upper": ("h5ht", "wn30", "rh80", "rh70", "rh60"),
        "kinematic": (),
    }.get(group, ())
    for idx, token in enumerate(explicit_order):
        if var_id == token:
            return idx
    text = f"{product.variable_id} {product.label} {product.long_name}".lower()
    ordered_tokens = {
        "thermo": ("temp", "tmp", "dew", "theta", "pressure"),
        "parcel": ("cape", "cin", "dcape"),
        "lapse": ("lapse", "lr"),
        "shear": ("shear", "bulk"),
        "helicity": ("srh", "helicity"),
        "composite": ("sig", "supercell", "scp"),
        "upper": ("height", "vorticity", "rh", "500", "700", "600", "800"),
        "kinematic": ("wind", "storm"),
    }.get(group, ())
    for idx, token in enumerate(ordered_tokens):
        if token in text:
            return idx
    return len(ordered_tokens)
