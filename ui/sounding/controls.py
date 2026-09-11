
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QComboBox
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt


class SoundingControls(QWidget):
    """Floating drawer that appears when the SOUNDING toolbar button is active.

    Presents two mutually-exclusive source buttons:
        [HRRR]  — click anywhere on map to fetch HRRR point sounding (existing)
        [OBS]   — radiosonde station dots appear; click one to fetch observed data
    """

    coptersonde_selected = pyqtSignal(object)
    mode_changed   = pyqtSignal(str)   # "hrrr", "obs", or "nssl"
    content_resized = pyqtSignal()     # triggers layout pulse in main_window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation   = None
        self._active_mode = "hrrr"
        self._setup_ui()


    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("soundingDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._btn_hrrr = self._btn("HRRR")
        self._btn_obs  = self._btn("OBS")
        self._btn_nssl = self._btn("NSSL")
        self._btn_copter = self._btn("COPTERSONDE")
        self._btn_copter.hide()
        self._btn_copter.toggled.connect(lambda on: self._on_mode_toggled("coptersonde", on))
        self._copter_combo = QComboBox()
        self._copter_combo.hide()
        self._copter_combo.currentIndexChanged.connect(self._on_copter_selected)

        self._btn_hrrr.setChecked(True)   # default source

        self._btn_hrrr.toggled.connect(lambda on: self._on_mode_toggled("hrrr", on))
        self._btn_obs.toggled.connect( lambda on: self._on_mode_toggled("obs",  on))
        self._btn_nssl.toggled.connect(lambda on: self._on_mode_toggled("nssl", on))

        row.addWidget(self._btn_hrrr)
        row.addWidget(self._vdiv())
        row.addWidget(self._btn_obs)
        row.addWidget(self._vdiv())
        row.addWidget(self._btn_nssl)
        row.addWidget(self._btn_copter)
        row.addWidget(self._copter_combo)
        row.addStretch(1)

        col.addWidget(btn_row)
        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div


    def _on_mode_toggled(self, mode: str, checked: bool):
        if not checked:
            # prevent both buttons from being unchecked simultaneously
            if self._active_mode == mode:
                # re-check the button that was just unchecked
                btn = {"hrrr": self._btn_hrrr, "obs": self._btn_obs, "nssl": self._btn_nssl, "coptersonde": self._btn_copter}[mode]
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
            return

        self._active_mode = mode

        # enforce mutual exclusivity
        for btn, btn_mode in (
            (self._btn_hrrr, "hrrr"),
            (self._btn_obs,  "obs"),
            (self._btn_nssl, "nssl"),
            (self._btn_copter, "coptersonde"),
        ):
            if btn_mode != mode:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)

        self._copter_combo.setVisible(mode == "coptersonde" and self._copter_combo.count() > 0)
        self.mode_changed.emit(mode)


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


    def reset_to_hrrr(self):
        """Silently reset to HRRR mode (called when SOUNDING button is deactivated)."""
        self._active_mode = "hrrr"
        for btn in (self._btn_hrrr, self._btn_obs, self._btn_nssl, self._btn_copter):
            btn.blockSignals(True)
        self._btn_hrrr.setChecked(True)
        self._btn_obs.setChecked(False)
        self._btn_nssl.setChecked(False)
        self._btn_copter.setChecked(False)
        self._copter_combo.hide()
        for btn in (self._btn_hrrr, self._btn_obs, self._btn_nssl, self._btn_copter):
            btn.blockSignals(False)

    @property
    def active_mode(self) -> str:
        return self._active_mode

    def set_coptersondes(self, sets):
        self._copter_combo.blockSignals(True)
        self._copter_combo.clear()
        for key, profiles in sorted(sets.items()):
            self._copter_combo.addItem(key, profiles)
        self._copter_combo.blockSignals(False)
        self._copter_combo.setVisible(self.active_mode == 'coptersonde' and bool(sets))
        self.content_resized.emit()
        if sets:
            self._on_copter_selected()

    def _on_copter_selected(self, _index=None):
        profiles = self._copter_combo.currentData()
        if profiles is not None and self.active_mode == 'coptersonde':
            self.coptersonde_selected.emit(profiles)
