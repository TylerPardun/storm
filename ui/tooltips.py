"""Readable button descriptions: Qt shows a plain-text tooltip on one line
however long it is, so a sentence-long description ran across half the
screen in small type. Long ones are shown wrapped to a comfortable width
instead; short ones and ones already written as rich text are left alone.
The look (size, colors, padding) matches the map's hover readouts and is set
by QToolTip in ui/theme.py."""
from __future__ import annotations

import html

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QToolTip, QWidget

WRAP_OVER_CHARS = 60      # longer plain-text tooltips are wrapped...
WRAP_WIDTH_PX = 320       # ...to this width


def wrapped(text: str) -> str:
    """`text` as rich text that wraps at WRAP_WIDTH_PX, line breaks kept."""
    body = "<br>".join(html.escape(line) for line in text.split("\n"))
    return f'<table width="{WRAP_WIDTH_PX}" cellspacing="0" cellpadding="0"><tr><td>{body}</td></tr></table>'


def needs_wrapping(text: str) -> bool:
    return bool(text) and len(text) > WRAP_OVER_CHARS and not Qt.mightBeRichText(text)


class ReadableToolTips(QObject):
    """Install on the QApplication: every widget's long tooltip wraps."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            text = obj.toolTip()
            if needs_wrapping(text):
                QToolTip.showText(event.globalPos(), wrapped(text), obj)
                return True
        return False
