
import os
import tempfile

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QFormLayout, QWidget
)
from PyQt6.QtCore import Qt

from ui.theme import ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED

# --- added: Generate temporary SVG files for the arrows to bypass QSS limitations ---
_UP_SVG = "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><path fill='none' stroke='#E8EAF0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' d='M4 10l4-4 4 4'/></svg>"
_DOWN_SVG = "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><path fill='none' stroke='#E8EAF0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' d='M4 6l4 4 4-4'/></svg>"

_tmp = tempfile.gettempdir()
# qt stylesheets strictly require forward slashes, even on Windows
_UP_PATH = os.path.join(_tmp, "storm_spin_up.svg").replace('\\', '/')
_DOWN_PATH = os.path.join(_tmp, "storm_spin_down.svg").replace('\\', '/')

try:
    with open(_UP_PATH, "w") as f: f.write(_UP_SVG)
    with open(_DOWN_PATH, "w") as f: f.write(_DOWN_SVG)
except Exception:
    pass  # fail silently if the OS strictly locks the temp dir

def _dialog_style() -> str:
    return f"""
        QDialog {{
            background-color: {BG_BASE};
        }}
        QLabel {{
            background-color: transparent;
        }}
        QDoubleSpinBox, QSpinBox {{
            background-color: {BG_ELEVATED};
            border: 1px solid #2E2E4E;
            border-radius: 6px;
            padding: 5px 8px;
            font-size: 12px;
            color: #E8EAF0;
            min-width: 100px;
        }}
        QDoubleSpinBox:focus, QSpinBox:focus {{
            border-color: {ACCENT};
        }}
        
        /* 1. Explicitly position the up button */
        QDoubleSpinBox::up-button, QSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 18px;
            border-left: 1px solid #2E2E4E;
            background-color: {BG_ELEVATED};
            border-top-right-radius: 5px;
        }}
        
        /* 2. Explicitly position the down button */
        QDoubleSpinBox::down-button, QSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 18px;
            border-left: 1px solid #2E2E4E;
            background-color: {BG_ELEVATED};
            border-bottom-right-radius: 5px;
        }}
        
        QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
        QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{
            background-color: #252540;
        }}
        
        /* 3. Point the arrows to the generated physical files */
        QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
            image: url("{_UP_PATH}");
            width: 10px;
            height: 10px;
        }}
        
        QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
            image: url("{_DOWN_PATH}");
            width: 10px;
            height: 10px;
        }}
        
        /* 4. Click effect */
        QDoubleSpinBox::up-button:pressed::up-arrow, QSpinBox::up-button:pressed::up-arrow,
        QDoubleSpinBox::down-button:pressed::down-arrow, QSpinBox::down-button:pressed::down-arrow {{
            margin: 1px 0 -1px 0;
        }}
    """


class StormConeInputDialog(QDialog):
    """
    Single dialog for both new cone placement and cone editing.

    Usage:
        dlg = StormConeInputDialog(parent=self)               # new
        dlg = StormConeInputDialog(edit_mode=True, speed_kts=40, heading=240, parent=self)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            action  = dlg.action()    # "ok" | "save" | "delete" | "cancel"
            speed   = dlg.speed_kts()
            heading = dlg.heading()
    """

    def __init__(self, edit_mode: bool = False,
                 speed_kts: float = 35.0, heading: int = 240,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Edit Storm Motion Cone" if edit_mode else "Storm Motion Cone")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        self._action = "cancel"
        self._edit_mode = edit_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QWidget()
        header.setStyleSheet(f"background-color: {BG_ELEVATED}; border-radius: 8px;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)

        icon_label = QLabel("⟶")
        icon_label.setStyleSheet(f"font-size: 22px; color: {ACCENT}; background: transparent;")
        h_layout.addWidget(icon_label)

        title_label = QLabel("Storm Motion Cone")
        title_label.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        h_layout.addWidget(title_label)
        h_layout.addStretch()
        layout.addWidget(header)

        hint = QLabel("Heading is the direction the storm is <b>coming from</b>"
                      "<br>(e.g. 206° = SSW → storm moves NNE).")
        hint.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0, 120)
        self._speed_spin.setSingleStep(5)
        self._speed_spin.setSuffix(" kts")
        self._speed_spin.setDecimals(0)
        self._speed_spin.setValue(speed_kts)

        self._heading_spin = QSpinBox()
        self._heading_spin.setRange(0, 359)
        self._heading_spin.setSingleStep(5)
        self._heading_spin.setSuffix("°")
        self._heading_spin.setWrapping(True)
        self._heading_spin.setValue(int(heading))

        speed_label = QLabel("Speed")
        speed_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        heading_label = QLabel("Heading")
        heading_label.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")

        form.addRow(speed_label, self._speed_spin)
        form.addRow(heading_label, self._heading_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if edit_mode:
            btn_delete = QPushButton("Delete")
            btn_delete.setObjectName("dangerButton")
            btn_delete.clicked.connect(self._on_delete)
            btn_row.addWidget(btn_delete)

            btn_move = QPushButton("Move")
            btn_move.setToolTip("Move cone")
            btn_move.clicked.connect(self._on_move)
            btn_row.addWidget(btn_move)

        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        if edit_mode:
            btn_ok = QPushButton("Save")
        else:
            btn_ok = QPushButton("OK")
        btn_ok.setObjectName("primaryButton")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_ok)

        layout.addLayout(btn_row)


    def _on_ok(self):
        self._action = "save" if self._edit_mode else "ok"
        self.accept()

    def _on_delete(self):
        self._action = "delete"
        self.accept()

    def _on_move(self):
        self._action = "move"
        self.accept()


    def action(self) -> str:
        """Returns 'ok', 'save', 'delete', or 'cancel'."""
        return self._action

    def speed_kts(self) -> float:
        return self._speed_spin.value()

    def heading(self) -> int:
        return self._heading_spin.value()


class StormConePlaceConfirmDialog(QDialog):
    """Shown after a storm cone placement drag, before the cone is created."""

    def __init__(self, lat: float, lon: float, speed_kts: float, heading: int, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Storm Cone")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title_label = QLabel("Storm Motion Cone")
        title_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ACCENT}; background: transparent;")
        layout.addWidget(title_label)

        info = QLabel(
            f"LAT {lat:.4f}   LON {lon:.4f}\n"
            f"Speed {speed_kts:.0f} kts   Heading {heading:d}°"
        )
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


class StormConeMotionConfirmDialog(QDialog):
    """Confirms an automatically calculated storm motion cone."""

    def __init__(
        self,
        origin_lat: float,
        origin_lon: float,
        speed_kts: float,
        heading: int,
        first_time: str,
        second_time: str,
        distance_nm: float,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Storm Motion")
        self.setModal(True)
        self.setMinimumWidth(330)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title_label = QLabel("Storm Motion Cone")
        title_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ACCENT}; background: transparent;")
        layout.addWidget(title_label)

        info = QLabel(
            f"Fixes {first_time} → {second_time}\n"
            f"Distance {distance_nm:.1f} nm\n"
            f"Speed {speed_kts:.0f} kts   Heading {heading:d}°\n"
            f"Origin {origin_lat:.4f}, {origin_lon:.4f}"
        )
        info.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED}; background: transparent;")
        layout.addWidget(info)

        if speed_kts > 120:
            warn = QLabel("Calculated speed is unusually high. Confirm only if the same feature was selected.")
            warn.setWordWrap(True)
            warn.setStyleSheet("font-size: 10px; color: #F5C542; background: transparent;")
            layout.addWidget(warn)

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


class StormConeMoveConfirmDialog(QDialog):
    """Shown after an existing storm cone has been dragged."""

    def __init__(self, old_lat: float, old_lon: float, new_lat: float, new_lon: float, parent=None):
        super().__init__(parent)
        self.setObjectName("annotationDialog")
        self.setWindowTitle("Confirm Move")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setStyleSheet(_dialog_style())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title_label = QLabel("Storm Motion Cone")
        title_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ACCENT}; background: transparent;")
        layout.addWidget(title_label)

        info = QLabel(
            f"Move from  {old_lat:.4f}, {old_lon:.4f}\n"
            f"       to  {new_lat:.4f}, {new_lon:.4f}"
        )
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
