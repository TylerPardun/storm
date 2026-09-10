
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QWidget, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer

_STYLE = """
QDialog {
    background-color: #0A0A0F;
    border: 1px solid rgba(74, 83, 108, 0.55);
    border-radius: 12px;
}
QLabel#title {
    color: #00CFFF;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel#subtitle {
    color: #8E97AB;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}
QLabel#status {
    color: #8E97AB;
    font-size: 11px;
}
QLabel#check {
    color: #39D98A;
    font-size: 12px;
    font-weight: 700;
}
QLabel#pending {
    color: #3A3B4A;
    font-size: 12px;
}
QProgressBar {
    background-color: #1A1A2E;
    border: none;
    border-radius: 3px;
    height: 4px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #00CFFF;
    border-radius: 3px;
}
QPushButton#abort {
    background-color: transparent;
    color: #F87171;
    border: 1px solid rgba(248, 113, 113, 0.4);
    border-radius: 6px;
    font-size: 11px;
    padding: 5px 16px;
}
QPushButton#abort:hover {
    background-color: rgba(248, 113, 113, 0.12);
}
"""


class ArchiveLoadingDialog(QDialog):
    """
    Shown while the archive fetchers pre-load data for the session date.

    Usage
    -----
    dlg = ArchiveLoadingDialog(parent)
    dlg.show()                       # non-blocking
    dlg.set_task_done("Radar index") # mark a task complete
    dlg.set_status("Downloading…")   # update status line
    # when all tasks are done, call dlg.accept() to dismiss.
    """

    def __init__(self, session_label: str, tasks: list[str], parent=None):
        """
        Parameters
        ----------
        session_label : str
            Human-readable session description, e.g. "2023-05-15 20:00 UTC".
        tasks : list[str]
            Names of tasks that must complete before the dialog auto-dismisses.
        """
        super().__init__(parent)
        self.setWindowTitle("STORM — Loading Archive")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setFixedWidth(380)
        self.setStyleSheet(_STYLE)

        self._tasks        = list(tasks)
        self._done: set[str] = set()

        self._build_ui(session_label, tasks)


    def _build_ui(self, session_label: str, tasks: list[str]):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(0)

        title = QLabel("LOADING ARCHIVE")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        root.addSpacing(4)

        sub = QLabel(session_label)
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(sub)

        root.addSpacing(20)

        self._task_labels: dict[str, QLabel] = {}
        for task in tasks:
            row = QHBoxLayout()
            row.setSpacing(10)
            indicator = QLabel("◌")
            indicator.setObjectName("pending")
            indicator.setFixedWidth(16)
            row.addWidget(indicator)
            lbl = QLabel(task)
            lbl.setObjectName("status")
            row.addWidget(lbl)
            row.addStretch()
            root.addLayout(row)
            root.addSpacing(6)
            self._task_labels[task] = indicator

        root.addSpacing(16)

        self._progress = QProgressBar()
        self._progress.setRange(0, max(1, len(tasks)))
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        root.addWidget(self._progress)

        root.addSpacing(14)

        self._status_label = QLabel("Preparing archive session…")
        self._status_label.setObjectName("status")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_label)

        root.addSpacing(16)

        abort_btn = QPushButton("Abort")
        abort_btn.setObjectName("abort")
        abort_btn.clicked.connect(self.reject)
        abort_row = QHBoxLayout()
        abort_row.addStretch()
        abort_row.addWidget(abort_btn)
        abort_row.addStretch()
        root.addLayout(abort_row)


    def set_task_done(self, task_name: str) -> None:
        """Mark a task as complete and update the progress bar."""
        self._done.add(task_name)
        indicator = self._task_labels.get(task_name)
        if indicator:
            indicator.setObjectName("check")
            indicator.setText("✓")
            indicator.setStyleSheet("color: #39D98A; font-size: 12px; font-weight: 700;")
        self._progress.setValue(len(self._done))
        if len(self._done) >= len(self._tasks):
            QTimer.singleShot(400, self.accept)

    def set_status(self, message: str) -> None:
        """Update the bottom status line."""
        self._status_label.setText(message)

    def set_task_error(self, task_name: str) -> None:
        """Mark a task as failed (shown in red; still counts toward completion)."""
        self._done.add(task_name)
        indicator = self._task_labels.get(task_name)
        if indicator:
            indicator.setObjectName("")
            indicator.setText("✗")
            indicator.setStyleSheet("color: #F87171; font-size: 12px; font-weight: 700;")
        self._progress.setValue(len(self._done))
        if len(self._done) >= len(self._tasks):
            QTimer.singleShot(600, self.accept)
