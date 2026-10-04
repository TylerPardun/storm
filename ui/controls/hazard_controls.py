
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QLabel, QComboBox, QGridLayout
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt, QSize
from PyQt6.QtGui import QFont


class _SpcDayComboBox(QComboBox):
    """QComboBox with a fixed two-column popup for SPC days."""

    _ITEM_SIZE = QSize(76, 24)
    _POPUP_SIZE = QSize(_ITEM_SIZE.width() * 2 + 4, _ITEM_SIZE.height() * 4 + 4)
    _POPUP_STYLE = """
        QFrame#spcDayPopup {
            background-color: #1A1A2E;
            border: 1px solid #2E2E4E;
            border-radius: 6px;
        }
        QToolButton#spcDayPopupButton {
            background-color: transparent;
            border: none;
            border-radius: 4px;
            color: #E8EAF0;
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0px;
            padding: 0px;
        }
        QToolButton#spcDayPopupButton:hover {
            background-color: #20253A;
            color: #EFF3FF;
        }
        QToolButton#spcDayPopupButton:checked {
            background-color: rgba(74, 158, 255, 0.18);
            color: #4A9EFF;
            font-weight: 600;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup: QFrame | None = None
        self._day_buttons: dict[int, QToolButton] = {}

    def showPopup(self):
        self._ensure_popup()
        if self._popup is None:
            return
        current_day = int(self.currentData() or 1)
        for day, btn in self._day_buttons.items():
            btn.setChecked(day == current_day)
        self._popup.setFixedSize(self._POPUP_SIZE)
        self._popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        self._popup.show()
        self._popup.raise_()
        self._popup.activateWindow()

    def hidePopup(self):
        if self._popup is not None:
            self._popup.hide()
        super().hidePopup()

    def _ensure_popup(self):
        if self._popup is not None:
            return
        popup = QFrame(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setObjectName("spcDayPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.setStyleSheet(self._POPUP_STYLE)
        grid = QGridLayout(popup)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(0)
        for day in range(1, 9):
            btn = QToolButton(popup)
            btn.setObjectName("spcDayPopupButton")
            btn.setCheckable(True)
            btn.setText(f"Day {day}")
            btn.setFixedSize(self._ITEM_SIZE)
            btn.clicked.connect(lambda _checked=False, d=day: self._select_day(d))
            grid.addWidget(btn, (day - 1) // 2, (day - 1) % 2)
            self._day_buttons[day] = btn
        self._popup = popup

    def _select_day(self, day: int):
        idx = self.findData(day)
        if idx >= 0:
            self.setCurrentIndex(idx)
        self.hidePopup()


class HazardControls(QWidget):
    """
    Floating drawer for hazard layer toggles with an inline color legend.

    OUTLOOK / TOR / WIND / HAIL / PROB / SIG are mutually exclusive SPC overlay modes.
    WATCHES, MDs, and NWS WARNINGS are independent additive overlays.
    A compact legend row appears below the buttons when any product is active.
    """

    spc_day_changed      = pyqtSignal(int)
    spc_mode_changed     = pyqtSignal(str)   # "", "outlook", "tor", "wind", "hail", "prob", "sig"
    spc_watches_toggled  = pyqtSignal(bool)
    spc_mds_toggled      = pyqtSignal(bool)
    nws_warnings_toggled = pyqtSignal(bool)
    nws_filter_changed   = pyqtSignal(object)
    cwa_toggled          = pyqtSignal(bool)
    fetch_requested      = pyqtSignal()
    content_resized      = pyqtSignal()      # triggers layout pulse in main_window

    # colored-swatch entries per product layer.  Each entry is (hex_color, short_label).
    PRODUCT_LEGENDS: dict[str, list[tuple[str, str]]] = {
        "spc-cat": [
            ("#80C580", "MRGL"),
            ("#F6F67F", "SLGHT"),
            ("#E87038", "ENH"),
            ("#E84038", "MDT"),
            ("#930093", "HIGH"),
        ],
        "spc-tor": [
            ("#008B00", "2%"),
            ("#8B4726", "5%"),
            ("#FFA500", "10%"),
            ("#FF0000", "15%"),
            ("#FF00FF", "30%"),
            ("#912CEE", "45%"),
            ("#104E8B", "60%"),
        ],
        "spc-wind": [
            ("#C1A353", "5%"),
            ("#FFFF00", "15%"),
            ("#FF6600", "30%"),
            ("#FF0000", "45%"),
            ("#FF00FF", "60%"),
        ],
        "spc-hail": [
            ("#C1A353", "5%"),
            ("#FFFF00", "15%"),
            ("#FF6600", "30%"),
            ("#FF0000", "45%"),
            ("#FF00FF", "60%"),
        ],
        "spc-prob": [
            ("#C1A353", "5%"),
            ("#FFFF00", "15%"),
            ("#FF0000", "30%"),
            ("#FF00FF", "45%"),
            ("#104E8B", "60%"),
        ],
        "spc-sig": [("#FFFFFF", "SIG")],
        "spc-watches": [("#FF0000", "TOR Watch"), ("#4169E1", "SVR Tstm Watch")],
        "spc-mds":     [("#FF66CC", "MDs")],
    }

    DAY_PRODUCTS: dict[int, tuple[str, ...]] = {
        1: ("outlook", "tor", "wind", "hail"),
        2: ("outlook", "tor", "wind", "hail"),
        3: ("outlook", "prob"),
        4: ("prob",),
        5: ("prob",),
        6: ("prob",),
        7: ("prob",),
        8: ("prob",),
    }

    # per-phenom color + short label for NWS warnings legend.
    NWS_PHENOM_LEGEND: list[tuple[str, str, str]] = [
        # (phenom_code, hex_color, short_label)
        ("TO", "#FF0000",  "TOR Warn"),
        ("SV", "#FFD700",  "SVR Warn"),
        ("FF", "#00FF00",  "FFD Warn"),
        ("FL", "#00FF7F",  "FL Warn"),
        ("MA", "#87CEEB",  "Marine Warn"),
        ("WS", "#FF69B4",  "Win Storm"),
        ("BZ", "#FF4500",  "Blizzard"),
        ("WW", "#FF69B4",  "Win Wx"),
        ("HU", "#DA70D6",  "Hurricane"),
        ("TS", "#DA70D6",  "Trop Storm"),
        ("HF", "#DA70D6",  "HF Wind"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating_spc_mode = False
        self._current_spc_day = 1
        # new members for the toggleable legend (Step 1)
        self._nws_filter_set: set[str] | None = None      # None = show all phenoms; set = show only these
        self._last_nws_phenoms: set[str] = set()
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("hazardDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._day_combo = _SpcDayComboBox()
        self._day_combo.setObjectName("spcDayCombo")
        self._day_combo.setMinimumWidth(76)
        self._day_combo.setMaximumWidth(82)
        self._day_combo.setFixedHeight(24)
        for day in range(1, 9):
            self._day_combo.addItem(f"Day {day}", day)
        self._day_combo.currentIndexChanged.connect(self._on_day_changed)
        row.addWidget(self._day_combo)
        row.addWidget(self._vdiv())

        self._btn_outlook = self._btn("OUTLOOK")
        self._btn_tor     = self._btn("TOR")
        self._btn_wind    = self._btn("WIND")
        self._btn_hail    = self._btn("HAIL")
        self._btn_prob    = self._btn("PROB")
        self._btn_sig     = self._btn("SIG")

        self._btn_outlook.toggled.connect(lambda on: self._on_spc_mode_toggled("outlook", on))
        self._btn_tor.toggled.connect(    lambda on: self._on_spc_mode_toggled("tor",     on))
        self._btn_wind.toggled.connect(   lambda on: self._on_spc_mode_toggled("wind",    on))
        self._btn_hail.toggled.connect(   lambda on: self._on_spc_mode_toggled("hail",    on))
        self._btn_prob.toggled.connect(   lambda on: self._on_spc_mode_toggled("prob",    on))
        self._btn_sig.toggled.connect(    lambda on: self._on_spc_mode_toggled("sig",     on))

        self._spc_buttons = {
            "outlook": self._btn_outlook,
            "tor": self._btn_tor,
            "wind": self._btn_wind,
            "hail": self._btn_hail,
            "prob": self._btn_prob,
            "sig": self._btn_sig,
        }

        for b in self._spc_buttons.values():
            row.addWidget(b)

        row.addWidget(self._vdiv())

        self._btn_watches = self._btn("WATCHES")
        self._btn_mds     = self._btn("MDs")
        self._btn_watches.toggled.connect(self._on_spc_watches_toggled)
        self._btn_mds.toggled.connect(self._on_spc_mds_toggled)
        row.addWidget(self._btn_watches)
        row.addWidget(self._btn_mds)

        row.addWidget(self._vdiv())

        self._btn_nws_warnings = self._btn("NWS WARNINGS")
        self._btn_nws_warnings.toggled.connect(self._on_nws_warnings_toggled)
        row.addWidget(self._btn_nws_warnings)

        # nws County Warning Areas (CWA) overlay toggle
        self._btn_cwa = self._btn("NWS CWA")
        self._btn_cwa.toggled.connect(self._on_cwa_toggled)
        row.addWidget(self._btn_cwa)
        self._hazard_row = row

        row.addStretch(1)
        col.addWidget(btn_row)
        self._update_day_products()

        self._legend_widget = QWidget()
        self._legend_layout = QHBoxLayout(self._legend_widget)
        self._legend_layout.setContentsMargins(4, 1, 4, 2)
        self._legend_layout.setSpacing(6)
        self._legend_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._legend_widget.setVisible(False)
        col.addWidget(self._legend_widget)

        outer.addWidget(self._drawer)

    def add_damage_paths_button(self) -> QToolButton:
        """Archive mode: DAMAGE PATHS (NWS damage surveys) after the NWS
        buttons. Checking it starts drawing a box (MainWindow)."""
        self._hazard_row.insertWidget(self._hazard_row.count() - 1, self._vdiv())
        button = self._btn("DAMAGE PATHS")
        button.setToolTip("Draw a box for NWS damage-survey paths")
        self._hazard_row.insertWidget(self._hazard_row.count() - 1, button)
        return button

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setProperty("hazardButton", True)
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        b.setFixedHeight(24)
        b.setStyleSheet("""
            QToolButton {
                background: transparent;
                background-color: transparent;
                border: 1px solid #2E2E4E;
                border-radius: 6px;
                color: #B8BFCD;
                font-size: 11px;
                font-weight: 500;
                letter-spacing: 0px;
                padding: 3px 8px;
            }
            QToolButton:hover {
                background-color: rgba(74, 158, 255, 0.08);
                border-color: #4A9EFF;
                color: #EFF3FF;
            }
            QToolButton:checked,
            QToolButton:pressed {
                background-color: rgba(74, 158, 255, 0.18);
                border-color: #4A9EFF;
                color: #4A9EFF;
                font-weight: 600;
            }
        """)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div


    def toggle_drawer(self, checked: bool):
        if checked:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            current = 0
        else:
            current = self.height()
            self.setMaximumHeight(current)
            target = 0

        if self._animation:
            self._animation.stop()
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim

    def update_legend(self, active_products: list[str], nws_phenoms: set[str] | None = None):
        """Rebuild the legend. SPC entries are static; NWS warning entries are now toggleable buttons."""
        # clear all existing widgets
        while self._legend_layout.count():
            child = self._legend_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        phenom_set = nws_phenoms or set()
        self._last_nws_phenoms = phenom_set.copy()

        # 1. add static SPC entries
        for product in active_products:
            if product != "nws-warnings":
                for color, label in self.PRODUCT_LEGENDS.get(product, []):
                    lbl = QLabel()
                    lbl.setText(
                        f'<span style="color:{color}; font-size:11px;">■</span>'
                        f'&thinsp;<span style="font-size:9px;">{label}</span>'
                    )
                    lbl.setFont(QFont("Helvetica Neue", 9))
                    lbl.setStyleSheet(
                        "color: #B5BDCC; background: transparent; padding: 1px 4px 2px 4px;"
                    )
                    self._legend_layout.addWidget(lbl)

        # 2. add toggleable NWS entries
        if "nws-warnings" in active_products and phenom_set:
            if self._legend_layout.count() > 0:
                # add subtle separator only if something was already added (SPC)
                sep = QLabel("│")
                sep.setStyleSheet("color: #4A4A5E; padding: 0 6px;")
                self._legend_layout.addWidget(sep)

            for code, color, label in self.NWS_PHENOM_LEGEND:
                if code in phenom_set:
                    btn = self._create_nws_legend_item(code, color, label)
                    self._legend_layout.addWidget(btn)
        
        self._legend_layout.addStretch(1)
        # show legend only if we added at least one item
        self._legend_widget.setVisible(self._legend_layout.count() > 0)

        # auto-resize animation if the drawer is open
        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            current = self.height()
            if target != current:
                if self._animation:
                    self._animation.stop()
                anim = QPropertyAnimation(self, b"maximumHeight")
                anim.setDuration(120)
                anim.setStartValue(current)
                anim.setEndValue(target)
                anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
                anim.finished.connect(lambda: self.setMaximumHeight(16777215))
                anim.start()
                self._animation = anim
                self.content_resized.emit()


    def _set_spc_mode(self, mode: str):
        self._updating_spc_mode = True
        try:
            for key, btn in self._spc_buttons.items():
                btn.setChecked(mode == key)
        finally:
            self._updating_spc_mode = False

    def current_spc_day(self) -> int:
        return self._current_spc_day

    def _active_spc_mode(self) -> str:
        for key, btn in self._spc_buttons.items():
            if btn.isChecked():
                return key
        return ""

    def _update_day_products(self):
        allowed = set(self.DAY_PRODUCTS.get(self._current_spc_day, ()))
        for key, btn in self._spc_buttons.items():
            btn.setVisible(key in allowed)
            btn.setEnabled(key in allowed)
        active = self._active_spc_mode()
        if active and active not in allowed:
            self._set_spc_mode("")
            self.spc_mode_changed.emit("")
        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            self.content_resized.emit()

    def _on_day_changed(self, *_args):
        day = int(self._day_combo.currentData() or 1)
        if day == self._current_spc_day:
            return
        self._current_spc_day = day
        self._update_day_products()
        self.spc_day_changed.emit(day)
        if self._active_spc_mode():
            self.fetch_requested.emit()

    def _on_spc_mode_toggled(self, mode: str, checked: bool):
        if self._updating_spc_mode:
            return
        if checked:
            self._set_spc_mode(mode)
            self.spc_mode_changed.emit(mode)
            self.fetch_requested.emit()
        else:
            if not any((
                btn.isChecked() for btn in self._spc_buttons.values()
            )):
                self.spc_mode_changed.emit("")

    def _on_spc_watches_toggled(self, checked: bool):
        self.spc_watches_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_spc_mds_toggled(self, checked: bool):
        self.spc_mds_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_nws_warnings_toggled(self, checked: bool):
        self.nws_warnings_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_cwa_toggled(self, checked: bool):
        """Emit cwa_toggled when the CWA button changes. No fetch required."""
        self.cwa_toggled.emit(checked)

    def _create_nws_legend_item(self, code: str, color: str, label: str) -> QToolButton:
        """Create a toggleable legend entry for one NWS phenom type."""
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setText(f"■ {label}")
        btn.setStyleSheet(f"""
            QToolButton {{
                color: {color};
                font-size: 9px;
                padding: 2px 6px;
                background: transparent;
                border: none;
                border-radius: 3px;
            }}
            QToolButton:checked {{
                background: rgba(255, 255, 255, 0.12);
                font-weight: 600;
            }}
            QToolButton:hover {{
                background: rgba(255, 255, 255, 0.06);
            }}
        """)

        # set initial state from current filter (no signal emitted)
        is_checked = (self._nws_filter_set is None) or (code in self._nws_filter_set)
        btn.blockSignals(True)
        btn.setChecked(is_checked)
        btn.blockSignals(False)

        btn.toggled.connect(lambda checked, c=code: self._on_nws_legend_toggled(c, checked))
        return btn

    def _on_nws_legend_toggled(self, code: str, checked: bool):
        # initialize the set if it's currently 'Show All'
        if self._nws_filter_set is None:
            self._nws_filter_set = self._last_nws_phenoms.copy()

        if checked:
            self._nws_filter_set.add(code)
        else:
            self._nws_filter_set.discard(code)

        # only reset to None if it actually matches the full set again
        if self._nws_filter_set == self._last_nws_phenoms:
            self._nws_filter_set = None

        self.nws_filter_changed.emit(self._nws_filter_set)

    def set_nws_filter_selected(self, codes: set[str] | None, emit: bool = True):
        """Programmatically set which NWS phenom codes are enabled in the legend.

        This is called from outside (e.g. main window restoring state).
        The legend buttons will reflect the new state the next time update_legend() runs.
        """
        # normalize input
        if codes:
            self._nws_filter_set = {str(c).strip().upper() for c in codes}
        else:
            self._nws_filter_set = None   # None means "show all"

        if emit:
            self.nws_filter_changed.emit(self._nws_filter_set)
