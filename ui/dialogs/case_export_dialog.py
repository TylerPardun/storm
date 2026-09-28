"""Choose what goes into a case package: storm tracks plus each type of
data the session loaded, all included by default (core/case_package.py)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QPushButton, QVBoxLayout,
)

from ui.theme import BG_BASE, TEXT_MUTED


def _size(num_bytes: int) -> str:
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.1f} GB"
    if num_bytes >= 1e6:
        return f"{num_bytes / 1e6:.1f} MB"
    return f"{max(num_bytes, 0) / 1e3:.0f} KB"


class CaseExportDialog(QDialog):
    """groups: [(kind, label, file_count, total_bytes)], one per data type
    the session loaded; tracks: how many storm tracks the date has."""

    def __init__(self, groups: list[tuple[str, str, int, int]], tracks: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Case Package")
        self.setStyleSheet(f"QDialog {{ background-color: {BG_BASE}; }} QLabel {{ background: transparent; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        intro = QLabel("Choose what to include. The case settings and the list of every source file "
                       "(with its checksum) are always included. Anything you leave out is downloaded "
                       "from its source when the package is opened.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        v.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        self._boxes: dict[str, QCheckBox] = {}
        self._sizes: dict[str, int] = {}
        rows = [("tracks", f"Storm tracks", tracks, 0)] + list(groups)
        for r, (kind, label, count, size) in enumerate(rows):
            box = QCheckBox(label)
            box.setChecked(count > 0)
            box.setEnabled(count > 0)
            box.toggled.connect(self._update_total)
            self._boxes[kind] = box
            self._sizes[kind] = size
            grid.addWidget(box, r, 0)
            detail = QLabel(f"{count} file{'s' if count != 1 else ''}" + (f" · {_size(size)}" if size else ""))
            detail.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
            detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(detail, r, 1)
        v.addLayout(grid)

        toggles = QDialogButtonBox()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Select none")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toggles.addButton(all_btn, QDialogButtonBox.ButtonRole.ActionRole)
        toggles.addButton(none_btn, QDialogButtonBox.ButtonRole.ActionRole)
        v.addWidget(toggles)

        self._total = QLabel()
        self._total.setStyleSheet("font-size: 11px;")
        v.addWidget(self._total)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        export = buttons.addButton("Export…", QDialogButtonBox.ButtonRole.AcceptRole)
        export.setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._update_total()

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            if box.isEnabled():
                box.setChecked(checked)

    def _update_total(self, *_args) -> None:
        total = sum(self._sizes[k] for k, box in self._boxes.items() if box.isChecked())
        self._total.setText(f"Package size about {_size(total)} (before compression)")

    def choices(self) -> tuple[bool, set[str]]:
        """(include storm tracks, data kinds to include)"""
        kinds = {k for k, box in self._boxes.items() if box.isChecked() and k != "tracks"}
        return self._boxes["tracks"].isChecked(), kinds
