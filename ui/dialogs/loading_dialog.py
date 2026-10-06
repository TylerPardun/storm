
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QTimer

_LOADING_STYLE = """
QDialog {
    background-color: #0A0A0F;
}
QLabel#title {
    color: #00CFFF;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel#message {
    color: #8E97AB;
    font-size: 13px;
}
QLabel#dots {
    color: #00CFFF;
    font-size: 18px;
}
"""


class LoadingDialog(QDialog):
    """Minimal loading screen shown while the main window initializes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM")
        self.setFixedSize(360, 180)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setStyleSheet(_LOADING_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 40, 32, 40)
        layout.setSpacing(16)

        title = QLabel("STORM")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        message = QLabel("Loading map, please wait...")
        message.setObjectName("message")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message)

        self._dots_label = QLabel("●")
        self._dots_label.setObjectName("dots")
        self._dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._dots_label)

        layout.addStretch()

        self._dots_count = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_dots)
        self._timer.start(400)

    def _animate_dots(self):
        self._dots_count = (self._dots_count + 1) % 4
        dots = "●" * (self._dots_count + 1)
        self._dots_label.setText(dots)

    def closeEvent(self, event):
        """Stop animation timer when dialog closes."""
        self._timer.stop()
        super().closeEvent(event)
