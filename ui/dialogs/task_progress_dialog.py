"""Progress for a long background job (packing a case package): what it is
doing, a thin bar with the count and percent beside it, and Cancel.
Replaces QProgressDialog, whose centered label, in-bar percent and loose
button row didn't line up with each other."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from ui.theme import BG_BASE, TEXT_MUTED


class TaskProgressDialog(QDialog):
    canceled = pyqtSignal()

    def __init__(self, title: str, heading: str, parent=None):
        super().__init__(parent)
        self._finished = False
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(
            f"QDialog {{ background-color: {BG_BASE}; }}"
            "QLabel { background: transparent; }"
            "QProgressBar { background-color: #1A1F2E; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #00CFFF; border-radius: 3px; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)

        self._heading = QLabel(heading)
        self._heading.setStyleSheet("font-size: 12px; font-weight: 600;")
        v.addWidget(self._heading)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        row.addWidget(self._bar, 1)
        self._count = QLabel("")
        self._count.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count.setMinimumWidth(110)
        row.addWidget(self._count)
        v.addLayout(row)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self._detail = QLabel("")
        self._detail.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        bottom.addWidget(self._detail, 1)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._on_cancel)
        bottom.addWidget(self._cancel)
        v.addLayout(bottom)

    def set_progress(self, done: int, total: int, detail: str = "") -> None:
        total = max(total, 1)
        pct = int(100 * done / total)
        self._bar.setValue(pct)
        self._count.setText(f"{done} of {total} · {pct}%")
        # one line however long the file name is
        width = max(60, self._detail.width())
        self._detail.setText(self._detail.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideMiddle, width))

    def finish(self) -> None:
        """The job is over (done, failed or canceled): close without canceling.
        (close() would go through reject(), which means Cancel here.)"""
        self._finished = True
        super().reject()

    def _on_cancel(self) -> None:
        if self._finished or not self._cancel.isEnabled():
            return
        self._cancel.setEnabled(False)
        self._heading.setText("Canceling…")
        self.canceled.emit()

    def reject(self) -> None:          # Esc / the close button cancel too
        if self._finished:
            super().reject()
        else:
            self._on_cancel()
