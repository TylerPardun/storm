from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget, QVBoxLayout

_MAP_BUTTON_STYLE = """
QToolButton {
    background: transparent;
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}
QToolButton:hover {
    background: rgba(74, 158, 255, 0.08);
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}
QToolButton:checked,
QToolButton:pressed {
    background: rgba(74, 158, 255, 0.18);
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}
"""


class MapControls(QWidget):
    """Drawer for map-context tools: routing, measuring, and land cover."""

    def __init__(
        self,
        route_available: bool = True,
        landcover_available: bool = False,
        satellite_available: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._animation = None
        self._route_available = bool(route_available)
        self._landcover_available = bool(landcover_available)
        self._satellite_available = bool(satellite_available)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("mapDrawer")
        row = QHBoxLayout(self._drawer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.btn_route = self._btn("ROUTE", "Route directions")
        self.btn_route.setVisible(self._route_available)
        row.addWidget(self.btn_route)

        self.btn_measure = self._btn("MEASURE", "Measure distance between two points")
        row.addWidget(self.btn_measure)

        self.btn_landcover = self._btn(
            "LANDCOVER", "NLCD land cover"
        )
        self.btn_landcover.setVisible(self._landcover_available)
        row.addWidget(self.btn_landcover)

        self.btn_satellite_basemap = self._btn(
            "SAT", "Satellite basemap"
        )
        self.btn_satellite_basemap.setVisible(self._satellite_available)
        row.addWidget(self.btn_satellite_basemap)

        outer.addWidget(self._drawer)

    def _btn(self, label: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setProperty("mapSubButton", True)
        btn.setStyleSheet(_MAP_BUTTON_STYLE)
        return btn

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
