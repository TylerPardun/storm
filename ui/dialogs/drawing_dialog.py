
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox,
)
from PyQt6.QtCore import Qt

from core.drawing import DrawingAnnotation, DRAWING_TYPE_MAP, FRONT_TYPE_KEYS
from ui.theme import ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED

_COLOR_PRESETS = [
    ("White",  "#E8EAF0"),
    ("Red",    "#E53935"),
    ("Blue",   "#4A9EFF"),
    ("Pink",   "#FF69B4"),
    ("Green",  "#39D98A"),
]
_DEFAULT_COLOR = "#E8EAF0"


def _make_color_combo(current_hex: str = _DEFAULT_COLOR) -> QComboBox:
    combo = QComboBox()
    combo.setStyleSheet(
        "background-color: #1A1A2E; color: #E8EAF0; border: 1px solid #2E2E4E;"
        "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
    )
    selected = 0
    for i, (name, hex_val) in enumerate(_COLOR_PRESETS):
        combo.addItem(name, hex_val)
        if hex_val.lower() == current_hex.lower():
            selected = i
    combo.setCurrentIndex(selected)
    return combo


def _dialog_style() -> str:
    return f"""
        QDialog {{
            background-color: {BG_BASE};
        }}
        QLabel {{
            background-color: transparent;
        }}
        QLineEdit {{
            background-color: {BG_ELEVATED};
            border: 1px solid #2E2E4E;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 11px;
            color: #E8EAF0;
        }}
        QLineEdit:focus {{
            border-color: {ACCENT};
        }}
    """


def _centroid_latlon(coords: list[list[float]]) -> tuple[float, float]:
    if not coords:
        return 0.0, 0.0
    lat = sum(pt[0] for pt in coords) / len(coords)
    lon = sum(pt[1] for pt in coords) / len(coords)
    return lat, lon


class DrawingTitleDialog(QDialog):
    """
    Shown after finishing a polyline or polygon.
    Requires a title and lets the user choose color and line style.
    """

    def __init__(self, drawing_type: str, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Name This Shape")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        self._title = ""
        self._color = _DEFAULT_COLOR
        self._line_style = "solid"
        meta = DRAWING_TYPE_MAP.get(drawing_type, {"label": drawing_type, "color": ACCENT})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_lbl = QLabel(meta["label"])
        header_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {meta['color']}; background: transparent;"
        )
        layout.addWidget(header_lbl)

        hint = QLabel("Title (shown on map at centroid)")
        hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        layout.addWidget(hint)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Enter a label…")
        layout.addWidget(self._title_edit)

        # color + line style row
        style_row = QHBoxLayout()
        style_row.setSpacing(8)

        color_lbl = QLabel("Color")
        color_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        style_row.addWidget(color_lbl)

        self._color_combo = _make_color_combo(self._color)
        style_row.addWidget(self._color_combo)

        style_row.addSpacing(12)

        style_lbl = QLabel("Style")
        style_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        style_row.addWidget(style_lbl)

        self._style_combo = QComboBox()
        self._style_combo.addItems(["Solid", "Dashed", "Dotted"])
        self._style_combo.setStyleSheet(
            "background-color: #1A1A2E; color: #E8EAF0; border: 1px solid #2E2E4E;"
            "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        style_row.addWidget(self._style_combo)
        style_row.addStretch()

        layout.addLayout(style_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_ok = QPushButton("Add to Map")
        btn_ok.setObjectName("primaryButton")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_ok)

        layout.addLayout(btn_row)
        self._title_edit.setFocus()

    def _on_ok(self):
        text = self._title_edit.text().strip()
        if not text:
            self._title_edit.setPlaceholderText("Title is required")
            self._title_edit.setStyleSheet(
                f"background-color: {BG_ELEVATED}; border: 1px solid #E53935;"
                "border-radius: 6px; padding: 6px 10px; font-size: 11px; color: #E8EAF0;"
            )
            return
        self._title = text
        self._color = self._color_combo.currentData()
        self._line_style = ["solid", "dashed", "dotted"][self._style_combo.currentIndex()]
        self.accept()

    def title(self) -> str:
        return self._title

    def color(self) -> str:
        return self._color

    def line_style(self) -> str:
        return self._line_style


class DrawingPlaceConfirmDialog(QDialog):
    """Shown after finishing a drawing, before it is added to the map."""

    def __init__(self, drawing_type: str, point_count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Drawing")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        meta = DRAWING_TYPE_MAP.get(drawing_type, {"label": drawing_type, "color": ACCENT})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_lbl = QLabel(meta["label"])
        header_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {meta['color']}; background: transparent;"
        )
        layout.addWidget(header_lbl)

        info = QLabel(f"{point_count} point{'s' if point_count != 1 else ''}")
        info.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QPushButton("Confirm")
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.setDefault(True)
        btn_confirm.clicked.connect(self.accept)
        btn_row.addWidget(btn_confirm)

        layout.addLayout(btn_row)


class DrawingEditDialog(QDialog):
    """
    Shown when the user clicks an existing drawing.
    - Fronts: type header, Flip Sides button, Delete button.
    - Custom shapes: editable title, Save button, Delete button.
    Returns action: 'save', 'delete', 'flip', 'move', or 'cancel'.
    """

    def __init__(self, drawing: DrawingAnnotation, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Edit Drawing")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        self._action = "cancel"
        self._result_title = drawing.title
        self._result_color = getattr(drawing, "color", _DEFAULT_COLOR)
        self._result_line_style = getattr(drawing, "line_style", "solid")
        meta = DRAWING_TYPE_MAP.get(
            drawing.drawing_type,
            {"label": drawing.drawing_type, "color": ACCENT}
        )
        is_front = drawing.drawing_type in FRONT_TYPE_KEYS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_lbl = QLabel(meta["label"])
        header_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {meta['color']}; background: transparent;"
        )
        layout.addWidget(header_lbl)

        if is_front:
            flip_hint = QLabel(
                "Symbols appear on the right side of the drawing direction.\n"
                "Click Flip Sides to move them to the opposite side."
            )
            flip_hint.setWordWrap(True)
            flip_hint.setStyleSheet(
                f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
            )
            layout.addWidget(flip_hint)
        else:
            hint = QLabel("Title (shown on map)")
            hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
            layout.addWidget(hint)

            self._title_edit = QLineEdit()
            self._title_edit.setText(drawing.title)
            layout.addWidget(self._title_edit)

            # color + line style row
            style_row = QHBoxLayout()
            style_row.setSpacing(8)

            color_lbl = QLabel("Color")
            color_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
            style_row.addWidget(color_lbl)

            self._color_combo = _make_color_combo(self._result_color)
            style_row.addWidget(self._color_combo)

            style_row.addSpacing(12)

            style_lbl = QLabel("Style")
            style_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
            style_row.addWidget(style_lbl)

            self._style_combo = QComboBox()
            self._style_combo.addItems(["Solid", "Dashed", "Dotted"])
            self._style_combo.setCurrentIndex(
                {"solid": 0, "dashed": 1, "dotted": 2}.get(self._result_line_style, 0)
            )
            self._style_combo.setStyleSheet(
                "background-color: #1A1A2E; color: #E8EAF0; border: 1px solid #2E2E4E;"
                "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
            )
            style_row.addWidget(self._style_combo)
            style_row.addStretch()

            layout.addLayout(style_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_delete = QPushButton("Delete")
        btn_delete.setObjectName("dangerButton")
        btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_delete)

        btn_move = QPushButton("Move")
        btn_move.clicked.connect(self._on_move)
        btn_row.addWidget(btn_move)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        if is_front:
            btn_flip = QPushButton("Flip Sides")
            btn_flip.setObjectName("primaryButton")
            btn_flip.setDefault(True)
            btn_flip.clicked.connect(self._on_flip)
            btn_row.addWidget(btn_flip)
        else:
            btn_save = QPushButton("Save")
            btn_save.setObjectName("primaryButton")
            btn_save.setDefault(True)
            btn_save.clicked.connect(self._on_save)
            btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _on_save(self):
        self._action = "save"
        self._result_title = self._title_edit.text().strip() or self._result_title
        self._result_color = self._color_combo.currentData()
        self._result_line_style = ["solid", "dashed", "dotted"][self._style_combo.currentIndex()]
        self.accept()

    def _on_delete(self):
        self._action = "delete"
        self.accept()

    def _on_flip(self):
        self._action = "flip"
        self.accept()

    def _on_move(self):
        self._action = "move"
        self.accept()

    def action(self) -> str:
        return self._action

    def result_title(self) -> str:
        return self._result_title

    def result_color(self) -> str:
        return self._result_color

    def result_line_style(self) -> str:
        return self._result_line_style


class DrawingMoveConfirmDialog(QDialog):
    """Shown after a drawing has been dragged to a new location."""

    def __init__(self, drawing: DrawingAnnotation, new_coordinates: list[list[float]], parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Move")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        meta = DRAWING_TYPE_MAP.get(
            drawing.drawing_type,
            {"label": drawing.drawing_type, "color": ACCENT}
        )
        old_lat, old_lon = _centroid_latlon(drawing.coordinates)
        new_lat, new_lon = _centroid_latlon(new_coordinates)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_lbl = QLabel(meta["label"])
        header_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {meta['color']}; background: transparent;"
        )
        layout.addWidget(header_lbl)

        info = QLabel(
            f"Move center from  {old_lat:.4f}, {old_lon:.4f}\n"
            f"          to  {new_lat:.4f}, {new_lon:.4f}"
        )
        info.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QPushButton("Confirm")
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.setDefault(True)
        btn_confirm.clicked.connect(self.accept)
        btn_row.addWidget(btn_confirm)

        layout.addLayout(btn_row)
