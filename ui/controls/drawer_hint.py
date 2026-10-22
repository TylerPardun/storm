"""A quiet line of instructions at the bottom of a toolbar drawer, centered
under its controls -- for features that need a step the buttons alone don't
explain (drawing a box for damage paths or ASOS)."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

HINT_STYLE = "color: #6E7A8F; font-size: 10px; background: transparent; padding: 1px 0 0 0;"


def drawer_hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("drawerHint")
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    label.setWordWrap(True)
    label.setStyleSheet(HINT_STYLE)
    return label
