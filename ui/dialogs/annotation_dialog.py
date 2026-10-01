
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QWidget, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import Qt

from core.annotation import Annotation, ANNOTATION_TYPE_MAP
from ui.theme import ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED


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


def _header_widget(meta: dict) -> QWidget:
    """Shared type-identity header block for both dialogs."""
    header = QWidget()
    header.setStyleSheet(f"background-color: {BG_ELEVATED}; border-radius: 8px;")
    h_layout = QHBoxLayout(header)
    h_layout.setContentsMargins(12, 10, 12, 10)
    h_layout.setSpacing(10)

    sym_label = QLabel(meta["symbol"])
    sym_label.setStyleSheet(
        f"font-size: 24px; color: {meta['color']}; background: transparent;"
    )
    h_layout.addWidget(sym_label)

    type_label = QLabel(meta["label"])
    type_label.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
    h_layout.addWidget(type_label)
    h_layout.addStretch()

    return header


class AnnotationPlaceDialog(QDialog):
    """
    Shown when the user clicks the map to place a new annotation.
    Shows type symbol/label, coordinates, and editable note field.
    """

    def __init__(self, type_key: str, lat: float, lon: float, viewer_mode: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Place Annotation")
        self.setModal(True)
        self.setMinimumWidth(320)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        self._result_label: str = ""
        self._result_type_key: str = type_key
        self._pressure_default_label = ""
        self._is_pressure_selector = type_key == "pressure_system"
        if self._is_pressure_selector:
            meta = {"symbol": "L", "label": "Pressure System", "color": "#E53935"}
            self._result_type_key = "low_pressure"
            self._pressure_default_label = ANNOTATION_TYPE_MAP["low_pressure"]["label"]
        else:
            meta = ANNOTATION_TYPE_MAP.get(
                type_key, {"symbol": "?", "label": type_key, "color": ACCENT}
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_header_widget(meta))

        if viewer_mode:
            notice = QLabel("Viewer mode — this annotation will only appear on your screen.")
            notice.setWordWrap(True)
            notice.setStyleSheet(
                "font-size: 10px; color: #E8A020; background: transparent;"
            )
            layout.addWidget(notice)

        coord_label = QLabel(f"LAT {lat:.4f}   LON {lon:.4f}")
        coord_label.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(coord_label)

        if self._is_pressure_selector:
            pressure_row = QWidget()
            pressure_layout = QHBoxLayout(pressure_row)
            pressure_layout.setContentsMargins(0, 0, 0, 0)
            pressure_layout.setSpacing(12)

            self._pressure_group = QButtonGroup(self)
            self._low_radio = QRadioButton("Low")
            self._high_radio = QRadioButton("High")
            self._low_radio.setChecked(True)
            self._low_radio.toggled.connect(self._on_pressure_changed)
            self._high_radio.toggled.connect(self._on_pressure_changed)
            self._pressure_group.addButton(self._low_radio)
            self._pressure_group.addButton(self._high_radio)

            for radio, color in ((self._low_radio, "#E53935"), (self._high_radio, "#4A9EFF")):
                radio.setStyleSheet(
                    f"font-size: 11px; font-weight: 700; color: {color}; background: transparent;"
                )
                pressure_layout.addWidget(radio)
            pressure_layout.addStretch()
            layout.addWidget(pressure_row)

        note_hint = QLabel("Note (optional)")
        note_hint.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(note_hint)

        self._note_edit = QLineEdit()
        default_label = self._pressure_default_label or meta["label"]
        self._note_edit.setPlaceholderText(default_label)
        self._note_edit.setText(default_label)
        self._note_edit.selectAll()
        layout.addWidget(self._note_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QPushButton("Confirm")
        btn_confirm.setObjectName("primaryButton")
        btn_confirm.setDefault(True)
        btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(btn_confirm)

        layout.addLayout(btn_row)

    def _on_confirm(self):
        self._result_label = self._note_edit.text().strip()
        self.accept()

    def _on_pressure_changed(self):
        if not self._is_pressure_selector:
            return
        next_type = "high_pressure" if self._high_radio.isChecked() else "low_pressure"
        previous_label = self._pressure_default_label
        next_label = ANNOTATION_TYPE_MAP[next_type]["label"]
        self._result_type_key = next_type
        self._pressure_default_label = next_label
        if self._note_edit.text().strip() in ("", previous_label):
            self._note_edit.setText(next_label)
            self._note_edit.selectAll()
        self._note_edit.setPlaceholderText(next_label)

    def result_type_key(self) -> str:
        return self._result_type_key

    def result_label(self) -> str:
        return self._result_label


class AnnotationEditDialog(QDialog):
    """
    Shown when the user clicks an existing annotation marker.
    Returns action='save', 'delete', or 'cancel'.
    """

    def __init__(self, annotation: Annotation, viewer_mode: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Edit Annotation")
        self.setModal(True)
        self.setMinimumWidth(320)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        self._action = "cancel"
        self._result_label = annotation.label
        meta = ANNOTATION_TYPE_MAP.get(
            annotation.type_key,
            {"symbol": "?", "label": annotation.type_key, "color": ACCENT}
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_header_widget(meta))

        if viewer_mode:
            notice = QLabel("Viewer mode — changes and deletions will only affect your screen.")
            notice.setWordWrap(True)
            notice.setStyleSheet(
                "font-size: 10px; color: #E8A020; background: transparent;"
            )
            layout.addWidget(notice)

        coord_label = QLabel(f"LAT {annotation.lat:.4f}   LON {annotation.lon:.4f}")
        coord_label.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(coord_label)

        note_hint = QLabel("Note")
        note_hint.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(note_hint)

        self._note_edit = QLineEdit()
        self._note_edit.setText(annotation.label)
        layout.addWidget(self._note_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_delete = QPushButton("Delete")
        btn_delete.setObjectName("dangerButton")
        btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_delete)

        if annotation.type_key != "fork":
            btn_move = QPushButton("Move")
            btn_move.clicked.connect(self._on_move)
            btn_row.addWidget(btn_move)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setObjectName("primaryButton")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _on_save(self):
        self._action = "save"
        self._result_label = self._note_edit.text().strip()
        self.accept()

    def _on_delete(self):
        self._action = "delete"
        self.accept()

    def _on_move(self):
        self._action = "move"
        self.accept()

    def action(self) -> str:
        return self._action

    def result_label(self) -> str:
        return self._result_label


class AnnotationMoveConfirmDialog(QDialog):
    """
    Shown after the user drags an annotation to a new location.
    Returns accepted (confirm) or rejected (cancel/revert).
    """

    def __init__(self, annotation: Annotation, new_lat: float, new_lon: float, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Move")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        meta = ANNOTATION_TYPE_MAP.get(
            annotation.type_key,
            {"symbol": "?", "label": annotation.type_key, "color": ACCENT}
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_header_widget(meta))

        info = QLabel(
            f"Move from  {annotation.lat:.4f}, {annotation.lon:.4f}\n"
            f"       to  {new_lat:.4f}, {new_lon:.4f}"
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
