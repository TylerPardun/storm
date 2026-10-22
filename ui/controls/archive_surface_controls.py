"""Archive SURFACE drawer: historical ASOS stations, loaded by drawing a box
on the map. The ASOS button starts the box; the line under it says so."""
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from ui.controls.drawer_hint import drawer_hint


class ArchiveSurfaceControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(520)                  # one line of hint, not a tall narrow column
        drawer = QWidget()
        drawer.setObjectName("hazardDrawer")       # the same outlined, blue-when-on buttons as HAZARDS
        col = QVBoxLayout(drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addStretch(1)
        self.asos_button = QToolButton()
        self.asos_button.setText("ASOS")
        self.asos_button.setCheckable(True)
        self.asos_button.setFixedHeight(24)
        self.asos_button.setToolTip("Historical ASOS observations for the stations inside a box you draw")
        row.addWidget(self.asos_button)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(drawer_hint("ASOS: click ASOS, then drag a box on the map to load the stations inside it."))
        outer.addWidget(drawer)

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
