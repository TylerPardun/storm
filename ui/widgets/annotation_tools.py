
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QFrame
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, Qt, QPointF, QSize
from PyQt6.QtGui import QPixmap, QPainter, QPen, QBrush, QColor, QPainterPath, QIcon

from config import ACCENT_COLOR
from core.annotation import ANNOTATION_TYPES, ANNOTATION_TYPE_MAP
from core.drawing import FRONT_TYPES, CUSTOM_TYPES


def _make_front_icon(key: str, color_hex: str, w: int = 56, h: int = 20) -> QIcon:
    """Paint a meteorological front symbol as a small pixmap icon."""
    px = QPixmap(w, h)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    y = float(h - 6)   # baseline — leaves room for semicircles below on stationary
    lw = 1.5

    if key == "stationary_front":
        # when rendering the active (dark) icon, use color_hex for both halves
        _forced = (color_hex == "#0A0A0F")
        mid = w / 2.0
        p.setPen(QPen(QColor(color_hex if _forced else "#4A9EFF"), lw))
        p.drawLine(QPointF(2, y), QPointF(mid, y))
        p.setPen(QPen(QColor(color_hex if _forced else "#E53935"), lw))
        p.drawLine(QPointF(mid, y), QPointF(w - 2, y))
    else:
        p.setPen(QPen(QColor(color_hex), lw))
        p.drawLine(QPointF(2, y), QPointF(w - 2, y))

    p.setPen(Qt.PenStyle.NoPen)

    if key == "cold_front":
        p.setBrush(QBrush(QColor(color_hex)))
        for cx in (14.0, 38.0):
            path = QPainterPath()
            path.moveTo(cx - 7, y)
            path.lineTo(cx + 7, y)
            path.lineTo(cx, y - 10)
            path.closeSubpath()
            p.drawPath(path)

    elif key == "warm_front":
        p.setBrush(QBrush(QColor(color_hex)))
        r = 7.0
        for cx in (14.0, 38.0):
            path = QPainterPath()
            path.moveTo(cx + r, y)
            path.arcTo(cx - r, y - r, 2 * r, 2 * r, 0, 180)   # upper arc
            path.closeSubpath()
            p.drawPath(path)

    elif key == "stationary_front":
        _forced = (color_hex == "#0A0A0F")
        # one blue triangle on the left side of the line.
        p.setBrush(QBrush(QColor(color_hex if _forced else "#4A9EFF")))
        cx = 16.0
        path = QPainterPath()
        path.moveTo(cx - 6, y)
        path.lineTo(cx + 6, y)
        path.lineTo(cx, y - 9)
        path.closeSubpath()
        p.drawPath(path)
        # one red semicircle on the right side of the line.
        p.setBrush(QBrush(QColor(color_hex if _forced else "#E53935")))
        r = 6.0
        cx = 42.0
        path = QPainterPath()
        path.moveTo(cx + r, y)
        path.arcTo(cx - r, y - r, 2 * r, 2 * r, 0, -180)  # lower arc
        path.closeSubpath()
        p.drawPath(path)

    elif key == "occluded_front":
        p.setBrush(QBrush(QColor(color_hex)))
        r = 6.0
        # triangle → semicircle → triangle
        for cx, is_tri in ((11.0, True), (30.0, False), (48.0, True)):
            path = QPainterPath()
            if is_tri:
                path.moveTo(cx - 6, y)
                path.lineTo(cx + 6, y)
                path.lineTo(cx, y - 10)
            else:
                path.moveTo(cx + r, y)
                path.arcTo(cx - r, y - r, 2 * r, 2 * r, 0, 180)
            path.closeSubpath()
            p.drawPath(path)

    elif key == "dryline":
        # open scallops (unfilled arcs) above the line
        p.setPen(QPen(QColor(color_hex), lw))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = 8.0
        for cx in (10.0, 26.0, 42.0):
            path = QPainterPath()
            path.moveTo(cx - r, y)
            path.arcTo(cx - r, y - r, 2 * r, 2 * r, 180, -180)  # upper arc
            p.drawPath(path)

    p.end()
    return QIcon(px)


class AnnotationButton(QToolButton):
    """Single annotation-type icon button with active-state highlight."""

    def __init__(self, type_key: str, symbol: str, label: str, color: str, parent=None):
        super().__init__(parent)
        self._type_key = type_key
        self._color = color
        # two-line text: big symbol on top, short label below
        self.setText(f"{symbol}\n{label}")
        self.setToolTip(label)
        self.setCheckable(False)   # active state managed manually
        self.setFixedHeight(38)
        self.setMinimumWidth(64)
        self._set_style(False)

    @property
    def type_key(self) -> str:
        return self._type_key

    def set_active(self, active: bool):
        self._set_style(active)

    def _set_style(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: {self._color};
                    border: 1px solid {self._color};
                    border-radius: 6px;
                    color: #0A0A0F;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 2px 6px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: transparent;
                    border: 1px solid #2E2E4E;
                    border-radius: 6px;
                    color: {self._color};
                    font-size: 9px;
                    font-weight: 600;
                    padding: 2px 6px;
                }}
                QToolButton:hover {{
                    background-color: #1A1A2E;
                    border-color: {self._color};
                }}
            """)


class FrontAnnotationButton(AnnotationButton):
    """AnnotationButton variant that shows a painted front-symbol icon."""

    _ICON_SIZE = QSize(56, 20)

    def __init__(self, type_key: str, label: str, color: str, parent=None):
        super().__init__(type_key, "", label, color, parent)
        self._icon_off = _make_front_icon(type_key, color)
        self._icon_on  = _make_front_icon(type_key, "#0A0A0F")  # dark for active bg
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(self._ICON_SIZE)
        self.setIcon(self._icon_off)
        self.setText(label)  # override the symbol+label text set in super().__init__

    def set_active(self, active: bool):
        super().set_active(active)
        self.setIcon(self._icon_on if active else self._icon_off)


class AnnotationTools(QWidget):
    """
    Collapsible toolbar drawer for annotation type selection.

    Signals:
        tool_selected(str)  — type_key when a button is activated;
                              empty string when deactivated.
        refresh_requested() — user pressed the Refresh button to re-fetch current.json.
    """

    tool_selected    = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_type: str = ""
        self._buttons: list[AnnotationButton] = []
        self._animation = None
        self._expanded_height = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        self.setMaximumHeight(0)  # starts collapsed (dropdown opens downward)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("radarDrawer")
        drawer_layout = QVBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(3)

        row1 = QWidget()
        row1_layout = QHBoxLayout(row1)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(4)

        for type_def in ANNOTATION_TYPES:
            if type_def["key"] in ("fork", "high_pressure", "low_pressure"):
                continue  # fork and pressure are placed separately
            btn = AnnotationButton(
                type_key=type_def["key"],
                symbol=type_def["symbol"],
                label=type_def.get("short_label", type_def["label"]),
                color=type_def["color"],
            )
            btn.setToolTip(type_def["label"])
            btn.clicked.connect(lambda checked, b=btn: self._on_button_clicked(b))
            self._buttons.append(btn)
            row1_layout.addWidget(btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        row1_layout.addWidget(sep)

        btn_storm = AnnotationButton(
            type_key="storm_motion",
            symbol="⟶",
            label="Storm Motion",
            color=ACCENT_COLOR,
        )
        btn_storm.clicked.connect(lambda checked, b=btn_storm: self._on_button_clicked(b))
        self._buttons.append(btn_storm)
        row1_layout.addWidget(btn_storm)

        sep_fork = QFrame()
        sep_fork.setFrameShape(QFrame.Shape.VLine)
        sep_fork.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        row1_layout.addWidget(sep_fork)

        fork_def = ANNOTATION_TYPE_MAP["fork"]
        btn_fork = AnnotationButton(
            type_key="fork",
            symbol=fork_def["symbol"],
            label=fork_def["label"],
            color=fork_def["color"],
        )
        btn_fork.clicked.connect(lambda checked, b=btn_fork: self._on_button_clicked(b))
        self._buttons.append(btn_fork)
        row1_layout.addWidget(btn_fork)

        drawer_layout.addWidget(row1)

        hdiv = QFrame()
        hdiv.setFrameShape(QFrame.Shape.HLine)
        hdiv.setFixedHeight(1)
        hdiv.setStyleSheet("background-color: #2E2E4E;")
        drawer_layout.addWidget(hdiv)

        row2 = QWidget()
        row2_layout = QHBoxLayout(row2)
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(4)

        for ftype in FRONT_TYPES:
            if ftype["key"] == "stationary_front":
                short = "Stationary"
            elif ftype["key"] == "occluded_front":
                short = "Occluded"
            elif ftype["key"] == "dryline":
                short = "Dry Line"
            else:
                short = ftype["label"].split()[0][:4]
            btn = FrontAnnotationButton(
                type_key=ftype["key"],
                label=short,
                color=ftype["color"],
            )
            btn.setToolTip(ftype["label"])
            btn.clicked.connect(lambda checked, b=btn: self._on_button_clicked(b))
            self._buttons.append(btn)
            row2_layout.addWidget(btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        row2_layout.addWidget(sep2)

        for ctype in CUSTOM_TYPES:
            btn = AnnotationButton(
                type_key=ctype["key"],
                symbol=ctype["symbol"],
                label=ctype["label"],
                color=ctype["color"],
            )
            btn.setToolTip(ctype["label"])
            btn.clicked.connect(lambda checked, b=btn: self._on_button_clicked(b))
            self._buttons.append(btn)
            row2_layout.addWidget(btn)

        btn_pressure = AnnotationButton(
            type_key="pressure_system",
            symbol="H/L",
            label="Pressure",
            color="#E8EAF0",
        )
        btn_pressure.setToolTip("High / Low Pressure")
        btn_pressure.clicked.connect(lambda checked, b=btn_pressure: self._on_button_clicked(b))
        self._buttons.append(btn_pressure)
        row2_layout.addWidget(btn_pressure)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        row2_layout.addWidget(sep3)

        self._btn_refresh = QToolButton()
        self._btn_refresh.setText("↻\nRefresh")
        self._btn_refresh.setToolTip("Refresh annotations")
        self._btn_refresh.setFixedHeight(38)
        self._btn_refresh.setMinimumWidth(64)
        self._btn_refresh.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: 1px solid #2E2E4E;
                border-radius: 6px;
                color: #8888AA;
                font-size: 9px;
                font-weight: 600;
                padding: 2px 6px;
            }
            QToolButton:hover {
                background-color: #1A1A2E;
                border-color: #8888AA;
                color: #CCCCEE;
            }
            QToolButton:pressed {
                background-color: #2E2E4E;
            }
        """)
        self._btn_refresh.clicked.connect(self.refresh_requested.emit)
        row2_layout.addWidget(self._btn_refresh)

        drawer_layout.addWidget(row2)

        layout.addWidget(self._drawer)
        QTimer.singleShot(0, self._measure_expanded_height)

    def _measure_expanded_height(self):
        self.setMaximumHeight(16777215)
        self._expanded_height = self.sizeHint().height()
        self.setMaximumHeight(0)


    def deactivate_tool(self):
        """Clear the active tool without emitting tool_selected."""
        self._active_type = ""
        for btn in self._buttons:
            btn.set_active(False)


    def toggle_drawer(self, checked: bool):
        """Animate open or closed — called by the toolbar ANNOTATE button."""
        if not checked:
            self.deactivate_tool()
            self.tool_selected.emit("")

        target = self._expanded_height if checked else 0
        if checked:
            current = self.maximumHeight()
            if target == 0:
                self.setMaximumHeight(16777215)
                target = self.sizeHint().height()
                self.setMaximumHeight(current)
        else:
            current = self.height()
            self.setMaximumHeight(current)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim   # keep ref alive for animation duration

    def _on_button_clicked(self, btn: AnnotationButton):
        if self._active_type == btn.type_key:
            # clicking the active tool deactivates it
            self._active_type = ""
            btn.set_active(False)
            self.tool_selected.emit("")
        else:
            for b in self._buttons:
                b.set_active(False)
            self._active_type = btn.type_key
            btn.set_active(True)
            self.tool_selected.emit(btn.type_key)
