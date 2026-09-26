
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel

from config import ACCENT_COLOR as _STORM_CYAN


_FIG_BG    = "#0a0a0f"
_AX_BG     = "#0f0f1a"
_BORDER    = "#2a2a40"
_ACCENT    = "#ff6b35"
_TEXT      = "#e8eaf0"
_MUTED     = "#666688"
_HDR_CLR   = "#b8b8d8"   # bright table row/column headers

_TEMP_CLR   = "#ff6b6b"
_DEWP_CLR   = "#3ddc84"
_BARB_CLR   = "#aaaacc"
_PARCEL_CLR  = "#ffffff"
_VTEMP_CLR   = "#ffaa88"
_VPARCEL_CLR = "#ff3366"    # virtual-temperature parcel trace
_EIL_CLR    = "#00e676"
_RM_CLR     = "#ff6b6b"
_LM_CLR     = "#4fc3f7"
_OBS_MOTION_CLR = "#FFD400"   # observed storm motion (archive storm track)

# per-source identity for comparison overlay
_SOURCE_COLORS = {"obs": "#ff6b6b", "hrrr": "#4fc3f7", "nssl": "#81c784"}
_SOURCE_LABELS = {"obs": "OBS",     "hrrr": "HRRR",    "nssl": "NSSL"}
_SOURCE_LS     = {"obs": "-",       "hrrr": "--",       "nssl": ":"}
_SOURCE_GLYPHS = {"obs": "━━",      "hrrr": "╌╌",       "nssl": "┈┈"}

_HODO_LAYERS = [
    ("#ff8585", 0,  3),
    ("#ffe45c", 3,  6),
    ("#70d7ff", 6,  9),
    ("#c0c0d6", 9, 99),
]

_SLIDER_QSS = f"""
QSlider {{ background-color: transparent; }}
QSlider::groove:horizontal {{
    background-color: #1a1a2e; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::sub-page:horizontal {{
    background-color: {_STORM_CYAN}; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::handle:horizontal {{
    background-color: {_STORM_CYAN}; border: 2px solid {_FIG_BG};
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
"""

_EXPORT_BTN_QSS = f"""
QToolButton {{
    background-color: #151522;
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QToolButton:hover {{
    border-color: {_STORM_CYAN};
    color: {_STORM_CYAN};
}}
QToolButton:pressed {{
    background-color: #202033;
}}
"""


def _force_bg(widget):
    bg  = QColor(_FIG_BG)
    txt = QColor(_TEXT)
    pal = widget.palette()
    for role in (
        QPalette.ColorRole.Window,    QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
        QPalette.ColorRole.Midlight,  QPalette.ColorRole.Light,
        QPalette.ColorRole.Mid,       QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
    ):
        pal.setColor(role, bg)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Text,       QPalette.ColorRole.BrightText,
    ):
        pal.setColor(role, txt)
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


def _lbl(text, color=_TEXT, size=10, bold=False,
         align=Qt.AlignmentFlag.AlignLeft):
    w = QLabel(text)
    weight = "bold" if bold else "normal"
    w.setStyleSheet(
        f"background-color: transparent; color: {color}; "
        f"font-size: {size}px; font-weight: {weight};"
    )
    w.setAlignment(align)
    return w
