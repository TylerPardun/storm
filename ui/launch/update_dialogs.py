
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout



class _CondaUpdateDialog(QDialog):
    """Progress dialog shown while conda env update runs in the background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM — Updating Dependencies")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0F; }
            QLabel  { color: #E8EAF0; font-size: 12px; background: transparent; }
            QLabel#status { color: #00CFFF; font-size: 11px; font-weight: 600; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)

        title = QLabel("Updating conda environment...")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._status = QLabel("Running: conda env update -f envs/storm.yml --prune")
        self._status.setObjectName("status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        hint = QLabel("This may take 2-5 minutes. Please wait...")
        hint.setStyleSheet("color: #8E97AB; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        # animated dots to show progress
        self._dots_label = QLabel("●")
        self._dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots_label.setStyleSheet("color: #00CFFF; font-size: 18px;")
        layout.addWidget(self._dots_label)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #8E97AB; border: 1px solid #8E97AB;
                border-radius: 4px; padding: 4px 12px; font-size: 11px;
            }
            QPushButton:hover { color: #E8EAF0; border-color: #E8EAF0; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._dots_count = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_dots)
        self._timer.start(400)

    def _animate_dots(self):
        self._dots_count = (self._dots_count + 1) % 4
        dots = "●" * (self._dots_count + 1)
        self._dots_label.setText(dots)


class _LogViewerDialog(QDialog):
    """Shows the contents of storm_fault.log with a copy-to-clipboard button."""

    def __init__(self, log_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM — Crash Log")
        self.setMinimumSize(620, 400)
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0F; }
            QLabel  { color: #8E97AB; font-size: 11px; background: transparent; }
            QTextEdit {
                background-color: #050508;
                border: 1px solid #1A1A2E;
                border-radius: 6px;
                color: #39D98A;
                font-family: 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
            QPushButton {
                background-color: #1A1A2E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
                color: #8E97AB;
                font-size: 11px;
                padding: 6px 16px;
            }
            QPushButton:hover { border-color: #00CFFF; color: #00CFFF; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"Log file: {log_path}"))

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        layout.addWidget(self._text)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._load(log_path)

    def _load(self, path: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                self._text.setPlainText("(Crash log is empty — no faults recorded.)")
            else:
                self._text.setPlainText("".join(lines[-50:]))
                self._text.verticalScrollBar().setValue(
                    self._text.verticalScrollBar().maximum()
                )
        except FileNotFoundError:
            self._text.setPlainText("(No crash log found — storm_fault.log does not exist yet.)")
        except Exception as exc:
            self._text.setPlainText(f"(Could not read log: {exc})")

    def _copy(self):
        QApplication.clipboard().setText(self._text.toPlainText())
