
from config import ACCENT_COLOR

DARK_THEME = """
/* ── Base ─────────────────────────────────────────────── */
* {
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    color: #E8EAF0;
    selection-background-color: #FF6B35;
    selection-color: #0A0A0F;
}

QMainWindow, QWidget {
    background-color: #0A0A0F;
    border: none;
}

/* ── Floating Toolbar ─────────────────────────────────── */
#floatingToolbar {
    background-color: rgba(15, 15, 26, 0.95);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.55);
}

#floatingToolbar QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    color: #C8D0DE;
}

#floatingToolbar QToolButton:hover {
    background-color: #20253A;
    border-color: #3C4664;
    color: #EFF3FF;
}

#floatingToolbar QToolButton:checked, #floatingToolbar QToolButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

/* Reusable transparent outlined buttons for product palettes. */
#floatingToolbar QToolButton[paletteButton="true"] {
    background: transparent;
    background-color: transparent;
    border: 1px solid rgba(74, 83, 108, 0.62);
    border-radius: 6px;
    color: #C8D0DE;
}

#floatingToolbar QToolButton[paletteButton="true"]:hover {
    background: rgba(74, 158, 255, 0.08);
    border-color: rgba(74, 158, 255, 0.55);
    color: #EFF3FF;
}

#floatingToolbar QToolButton[paletteButton="true"]:checked,
#floatingToolbar QToolButton[paletteButton="true"]:pressed {
    background: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

/* Slightly upscale controls when the window is wide enough. */
#floatingToolbar[wide="true"] QToolButton {
    padding: 4px 10px;
    font-size: 12px;
}

#floatingToolbar[wide="true"] QCheckBox {
    font-size: 12px;
}

#floatingToolbar[wide="true"] QComboBox#radarSiteCombo,
#floatingToolbar[wide="true"] QComboBox#radarProductCombo,
#floatingToolbar[wide="true"] QToolButton#radarStationsButton {
    min-height: 24px;
}

#floatingToolbar QWidget#radarDrawer,
#floatingToolbar QWidget#hazardDrawer,
#floatingToolbar QWidget#satelliteDrawer,
#floatingToolbar QWidget#mapDrawer,
#floatingToolbar QWidget#landcoverDrawer,
#floatingToolbar QWidget#mesoanalysisDrawer,
#floatingToolbar QWidget#deployLocsDrawer,
#floatingToolbar QWidget#routingDrawer,
#floatingToolbar QWidget#soundingDrawer,
#floatingToolbar QWidget#surfaceDrawer,
#floatingToolbar QWidget#rawLidarDrawer {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}

#floatingToolbar QWidget#radarDrawer > QWidget,
#floatingToolbar QWidget#hazardDrawer > QWidget,
#floatingToolbar QWidget#satelliteDrawer > QWidget,
#floatingToolbar QWidget#mapDrawer > QWidget,
#floatingToolbar QWidget#landcoverDrawer > QWidget,
#floatingToolbar QWidget#mesoanalysisDrawer > QWidget,
#floatingToolbar QWidget#deployLocsDrawer > QWidget,
#floatingToolbar QWidget#routingDrawer > QWidget,
#floatingToolbar QWidget#soundingDrawer > QWidget,
#floatingToolbar QWidget#surfaceDrawer > QWidget,
#floatingToolbar QWidget#rawLidarDrawer > QWidget {
    background: transparent;
}

#floatingToolbar QComboBox#radarSiteCombo,
#floatingToolbar QComboBox#radarProductCombo,
#floatingToolbar QToolButton#radarStationsButton {
    background-color: rgba(32, 37, 58, 0.62);
    border: 1px solid rgba(74, 83, 108, 0.48);
}

#floatingToolbar QComboBox#radarSiteCombo:hover,
#floatingToolbar QComboBox#radarProductCombo:hover,
#floatingToolbar QToolButton#radarStationsButton:hover {
    border-color: rgba(120, 138, 178, 0.72);
}

#floatingToolbar QCheckBox::indicator {
    background: transparent;
    border: none;
}

/* ── Vehicle Pill ─────────────────────────────────────── */
#vehiclePill {
    background-color: rgba(12, 14, 22, 0.98);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.6);
    min-width: 280px;
}

#vehiclePill QWidget, #vehiclePill QFrame {
    background: transparent;
}

#vehiclePill QLabel {
    background: transparent;
    color: #C8D0DE;
}

#vehiclePill QLabel#vehiclePillTitle {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.3px;
    color: #4A9EFF;
}

#vehiclePill QLabel#vehiclePillCount {
    background-color: rgba(74, 158, 255, 0.16);
    color: #4A9EFF;
    border-radius: 8px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
}

#vehiclePill QCheckBox#vehiclePillToggle {
    font-size: 10px;
    color: #9DA6B8;
    background: transparent;
}

#vehiclePill QLabel#vehiclePillEmpty {
    color: #9DA6B8;
    font-size: 10px;
    padding: 6px 0;
}

#vehiclePill QWidget#vehicleRowsContainer {
    background: transparent;
}

/* ── Vehicle Detail Pill ─────────────────────────────── */
#vehicleDetailPill QWidget, #vehicleDetailPill QFrame {
    background: transparent;
}

#vehicleDetailPill {
    background-color: rgba(10, 12, 20, 0.98);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.6);
    min-width: 280px;
}

#vehicleDetailPill QLabel#vehicleDetailTitle {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: #E8EAF0;
}

/* ── Hazard Drawer ────────────────────────────────────── */
/* Overrides the global floatingToolbar QToolButton:checked orange rule so  *
 * hazard mode buttons always show blue and stay readable over the map.     */
#floatingToolbar QWidget#hazardDrawer QToolButton {
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:checked {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo {
    background-color: rgba(32, 37, 58, 0.62);
    border: 1px solid rgba(74, 83, 108, 0.48);
    border-radius: 6px;
    color: #E8EAF0;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0px;
    padding: 2px 18px 2px 8px;
    min-height: 18px;
    max-height: 24px;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo:hover {
    background-color: rgba(32, 37, 58, 0.78);
    border-color: rgba(120, 138, 178, 0.72);
    color: #EFF3FF;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo::drop-down {
    width: 18px;
    border: none;
    background: transparent;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo QAbstractItemView {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    color: #E8EAF0;
    selection-background-color: #FF6B35;
    selection-color: #0A0A0F;
    outline: none;
    padding: 0px;
    min-width: 156px;
    min-height: 100px;
}

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

/* ── Map Drawer ───────────────────────────────────────── */
#floatingToolbar QWidget#mapDrawer QToolButton,
#floatingToolbar QToolButton[mapSubButton="true"],
#floatingToolbar QWidget#satelliteDrawer QToolButton {
    background: transparent;
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#mapDrawer QToolButton:hover,
#floatingToolbar QToolButton[mapSubButton="true"]:hover,
#floatingToolbar QWidget#satelliteDrawer QToolButton:hover {
    background: rgba(74, 158, 255, 0.08);
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#mapDrawer QToolButton:checked,
#floatingToolbar QWidget#mapDrawer QToolButton:pressed,
#floatingToolbar QToolButton[mapSubButton="true"]:checked,
#floatingToolbar QToolButton[mapSubButton="true"]:pressed,
#floatingToolbar QWidget#satelliteDrawer QToolButton:checked,
#floatingToolbar QWidget#satelliteDrawer QToolButton:pressed {
    background: rgba(74, 158, 255, 0.18);
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}

/* ── Deploy Locs Drawer ───────────────────────────────── */
#floatingToolbar QWidget#deployLocsDrawer QToolButton {
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#deployLocsDrawer QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#deployLocsDrawer QToolButton:checked {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}

#floatingToolbar QWidget#deployLocsFilterRow {
    background: transparent;
}

#floatingToolbar QSlider#deployLocsSlider {
    height: 18px;
}

#floatingToolbar QSlider#deployLocsSlider::groove:horizontal {
    height: 4px;
    background: #2E2E4E;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSlider::sub-page:horizontal {
    background: #4A9EFF;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: #EFF3FF;
    border-radius: 6px;
}

#floatingToolbar QSlider#deployLocsSlider::handle:horizontal:hover {
    background: #4A9EFF;
}

#floatingToolbar QLabel#deployLocsSliderLabel {
    background: transparent;
    color: #B5BDCC;
    font-size: 10px;
}

#floatingToolbar QWidget#deployLocsSizeRow {
    background: transparent;
}

#floatingToolbar QSlider#deployLocsSizeSlider {
    height: 18px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::groove:horizontal {
    height: 4px;
    background: #2E2E4E;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::sub-page:horizontal {
    background: #4A9EFF;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: #EFF3FF;
    border-radius: 6px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::handle:horizontal:hover {
    background: #4A9EFF;
}

/* ── Routing Drawer ───────────────────────────────────── */
#floatingToolbar QWidget#routingDrawer QLineEdit {
    background-color: rgba(26, 26, 46, 0.85);
    border: 1px solid #2E2E4E;
    border-radius: 5px;
    padding: 3px 7px;
    font-size: 11px;
    color: #E8EAF0;
    min-height: 20px;
}

#floatingToolbar QWidget#routingDrawer QLineEdit:focus {
    border-color: #4A9EFF;
}

#floatingToolbar QWidget#routingDrawer QToolButton {
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#routingDrawer QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#routingDrawer QToolButton:checked,
#floatingToolbar QWidget#routingDrawer QToolButton:pressed {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}

/* Small fixed-size buttons in origin / dest rows (⊕ pick, nav-arrow loc) */
#floatingToolbar QWidget#routingDrawer QToolButton[text="⊕"],
#floatingToolbar QWidget#routingDrawer QToolButton#locBtn {
    padding: 1px 2px;
    font-size: 13px;
    border-radius: 4px;
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:enabled {
    border-color: rgba(74, 158, 255, 0.4);
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:enabled:hover {
    background-color: rgba(74, 158, 255, 0.15);
    border-color: #4A9EFF;
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:disabled {
    border-color: #252535;
}

/* ── Outlook Panel ────────────────────────────────────── */
#outlookPanel {
    background-color: rgba(15, 15, 26, 0.95);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.55);
}

#outlookPanelTitle {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: #4A9EFF;
    background: transparent;
}

#outlookPanelClose {
    background: transparent;
    border: none;
    color: #5A5B6A;
    font-size: 16px;
    padding: 0 2px;
}

#outlookPanelClose:hover {
    color: #E8EAF0;
}

#outlookPanelText {
    background-color: transparent;
    border: none;
    color: #C1C9D8;
    font-size: 11px;
    selection-background-color: #FF6B35;
}

QWidget#outlookTabRow {
    background: transparent;
}

#outlookTabBtn {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(74, 83, 108, 0.5);
    border-radius: 4px;
    color: #8A92A8;
    font-size: 10px;
    font-weight: 600;
    padding: 2px 7px;
}

#outlookTabBtn:hover {
    background: rgba(74, 158, 255, 0.12);
    border-color: rgba(74, 158, 255, 0.4);
    color: #C1C9D8;
}

#outlookTabBtn:checked {
    background: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
}

/* ── Status Overlays ──────────────────────────────────── */
QWidget#statusOverlayLeft {
    background-color: rgba(15, 15, 26, 0.88);
    border-radius: 8px;
    border: 1px solid rgba(84, 94, 122, 0.5);
}

QWidget#statusOverlayLeft QLabel {
    background: transparent;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 0;
    color: #C8D0DE;
}

/* ── Navigation Pill ──────────────────────────────────── */
QWidget#navPill {
    background-color: rgba(15, 15, 26, 0.93);
    border-radius: 10px;
    border: 1px solid rgba(74, 158, 255, 0.4);
}

QWidget#navPill QLabel#navPillStep {
    background: transparent;
    color: #EFF3FF;
    font-size: 15px;
    font-weight: 600;
    padding: 0;
}

QWidget#navPill QLabel#navPillSummary {
    background: transparent;
    color: #4A9EFF;
    font-size: 13px;
    font-weight: 500;
    padding: 0;
}

QWidget#navPill QToolButton {
    background: transparent;
    border: none;
    color: #9DA6B8;
    font-size: 16px;
    padding: 0;
}

QWidget#navPill QToolButton:hover {
    color: #EFF3FF;
}

/* ── Side Panel ───────────────────────────────────────── */
QDockWidget {
    background-color: #0F0F1A;
    border: none;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background-color: #0F0F1A;
    border-bottom: 1px solid #1E1E2E;
    padding: 8px 12px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #6E7A8F;
}

QDockWidget QWidget {
    background-color: #0F0F1A;
}

/* ── Scroll Bars ──────────────────────────────────────── */
QScrollBar:vertical {
    background-color: transparent;
    width: 6px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #2E2E4E;
    border-radius: 3px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #FF6B35;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 6px;
}

QScrollBar::handle:horizontal {
    background-color: #2E2E4E;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #FF6B35;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Labels ───────────────────────────────────────────── */
QLabel {
    background-color: transparent;
    color: #E8EAF0;
}

QLabel#sectionHeader {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    color: #6E7A8F;
    padding: 4px 0px;
}

QLabel#vehicleCallsign {
    font-size: 13px;
    font-weight: 600;
    color: #E8EAF0;
}

QLabel#vehicleMeta {
    font-size: 10px;
    color: #6E7A8F;
}

QLabel#obsValue {
    font-size: 12px;
    font-weight: 500;
    color: #FF6B35;
}

/* ── Buttons ──────────────────────────────────────────── */
QPushButton {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 11px;
    font-weight: 500;
    color: #E8EAF0;
}

QPushButton:hover {
    background-color: #1E1E3E;
    border-color: #FF6B35;
    color: #FF6B35;
}

QPushButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

QPushButton#primaryButton {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: #FF8555;
    border-color: #FF8555;
}

QPushButton#dangerButton {
    border-color: #E53935;
    color: #E53935;
}

QPushButton#dangerButton:hover {
    background-color: #E53935;
    color: #0A0A0F;
}

QComboBox#radarProductCombo, QComboBox#radarSiteCombo, QToolButton#radarStationsButton {
    padding: 4px 8px;
}

/* ── ComboBox ─────────────────────────────────────────── */
QComboBox {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11px;
    color: #E8EAF0;
    min-width: 120px;
}

QComboBox:hover {
    border-color: #FF6B35;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    width: 8px;
    height: 8px;
    border-left: 2px solid #5A5B6A;
    border-bottom: 2px solid #5A5B6A;
    margin-right: 6px;
}

QComboBox QAbstractItemView {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    selection-background-color: #FF6B35;
    selection-color: #0A0A0F;
    padding: 4px;
}

/* ── Slider ───────────────────────────────────────────── */
QSlider::groove:horizontal {
    background-color: #1E1E2E;
    height: 3px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background-color: #FF6B35;
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -6px 0;
}

QSlider::sub-page:horizontal {
    background-color: #FF6B35;
    border-radius: 2px;
}

#floatingToolbar QWidget#radarDrawer QSlider,
#floatingToolbar QWidget#satelliteDrawer QSlider,
#floatingToolbar QWidget#landcoverDrawer QSlider {
    background: transparent;
}

#floatingToolbar QWidget#radarDrawer QSlider::groove:horizontal,
#floatingToolbar QWidget#satelliteDrawer QSlider::groove:horizontal,
#floatingToolbar QWidget#landcoverDrawer QSlider::groove:horizontal {
    background-color: rgba(184, 191, 205, 0.28);
    height: 2px;
    border-radius: 1px;
}

/* ── Mesoanalysis / SFCOA drawers ─────────────────────── */
#floatingToolbar QWidget#mesoanalysisDrawer,
#floatingToolbar QWidget#sfcoaDrawer,
#floatingToolbar QWidget#mesoanalysisControlRow,
#floatingToolbar QWidget#sfcoaControlRow,
#floatingToolbar QWidget#sfcoaProductGrid,
#floatingToolbar QWidget#mesoanalysisStatusRow {
    background: transparent;
    border: none;
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton,
#floatingToolbar QWidget#sfcoaDrawer QToolButton {
    background-color: transparent;
    border: 1px solid rgba(74, 83, 108, 0.42);
    border-radius: 6px;
    color: #C8D0DE;
    padding: 3px 8px;
}

#floatingToolbar QWidget#mesoanalysisDrawer QLabel#mesoanalysisGroupLabel,
#floatingToolbar QWidget#sfcoaDrawer QLabel#sfcoaGroupLabel {
    color: #6E7A8F;
    font-size: 9px;
    font-weight: 700;
    min-width: 72px;
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton {
    background-color: transparent;
    background: transparent;
    min-width: 62px;
    padding: 2px 6px;
    font-size: 10px;
}

#floatingToolbar QWidget#mesoanalysisProductGrid,
#floatingToolbar QWidget#mesoanalysisProductGrid QWidget,
#floatingToolbar QWidget#mesoanalysisPlaybackCell,
#floatingToolbar QWidget#sfcoaProductGrid,
#floatingToolbar QWidget#sfcoaProductGrid QWidget {
    background: transparent;
    background-color: transparent;
}

#floatingToolbar QWidget#mesoanalysisDrawer QLabel#mesoanalysisTimeLabel,
#floatingToolbar QWidget#sfcoaDrawer QLabel#sfcoaTimeLabel {
    color: #C8D0DE;
    font-size: 10px;
    font-weight: 600;
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton:hover,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: rgba(74, 158, 255, 0.35);
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton:checked,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton:checked {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton:hover,
#floatingToolbar QWidget#sfcoaDrawer QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: rgba(74, 158, 255, 0.55);
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton:checked,
#floatingToolbar QWidget#sfcoaDrawer QToolButton:checked {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#mesoanalysisDrawer QComboBox,
#floatingToolbar QWidget#sfcoaDrawer QComboBox {
    background-color: rgba(32, 37, 58, 0.62);
    border: 1px solid rgba(74, 83, 108, 0.48);
    border-radius: 6px;
    color: #D9E0EC;
    padding: 2px 7px;
    min-width: 0;
}

#floatingToolbar QWidget#mesoanalysisDrawer QComboBox:hover,
#floatingToolbar QWidget#sfcoaDrawer QComboBox:hover {
    border-color: rgba(120, 138, 178, 0.72);
}

#floatingToolbar QWidget#mesoanalysisDrawer QPushButton,
#floatingToolbar QWidget#sfcoaDrawer QPushButton {
    background: transparent;
    border: 1px solid rgba(74, 83, 108, 0.48);
    border-radius: 6px;
    color: #C8D0DE;
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
}

#floatingToolbar QWidget#mesoanalysisDrawer QPushButton:hover,
#floatingToolbar QWidget#sfcoaDrawer QPushButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: rgba(74, 158, 255, 0.55);
    color: #EFF3FF;
}

#floatingToolbar QWidget#mesoanalysisDrawer QSlider {
    background: transparent;
}

#floatingToolbar QWidget#mesoanalysisDrawer QSlider::groove:horizontal {
    background-color: rgba(184, 191, 205, 0.28);
    height: 2px;
    border-radius: 1px;
}

/* ── Radar / Satellite playback rows (small icon buttons, minimal padding) ── */
QWidget#radarPlaybackRow QToolButton,
QWidget#satPlaybackRow QToolButton {
    padding: 1px 3px;
    font-size: 13px;
}

QWidget#radarPlaybackRow QToolButton:hover,
QWidget#satPlaybackRow QToolButton:hover {
    background-color: #1A1A2E;
    border-color: #2E2E4E;
    color: #E8EAF0;
}

QWidget#radarPlaybackRow QToolButton:checked,
QWidget#radarPlaybackRow QToolButton:pressed,
QWidget#satPlaybackRow QToolButton:checked,
QWidget#satPlaybackRow QToolButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

/* ── Tooltips ─────────────────────────────────────────── */
/* Button descriptions: the same look as the map's hover readouts
   (ui/map/map_template.html); long ones wrap (ui/tooltips.py). */
QToolTip {
    background-color: #0F0F1A;
    border: 1px solid #49536F;
    border-radius: 6px;
    color: #E8EDF5;
    font-size: 12px;
    padding: 5px 9px;
}

/* ── Toolbar checkboxes ──────────────────────────────── */
#floatingToolbar QCheckBox {
    background: transparent;
    border: none;
    color: #B8BFCD;
    font-size: 11px;
    spacing: 5px;
    padding: 0;
}

#floatingToolbar QWidget#radarDrawer QCheckBox,
#floatingToolbar QWidget#hazardDrawer QCheckBox,
#floatingToolbar QWidget#satelliteDrawer QCheckBox,
#floatingToolbar QWidget#deployLocsDrawer QCheckBox,
#floatingToolbar QWidget#routingDrawer QCheckBox,
#floatingToolbar QWidget#soundingDrawer QCheckBox,
#floatingToolbar QWidget#surfaceDrawer QCheckBox {
    background: transparent;
}
#floatingToolbar QCheckBox::indicator {
    width: 12px;
    height: 12px;
    border: none;
    background: transparent;
    image: url(static/indicator_off.svg);
}
#floatingToolbar QCheckBox::indicator:checked {
    image: url(static/indicator_on.svg);
}

/* ── Separators ───────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    color: #1E1E2E;
}

/* ── Annotation Dialog ────────────────────────────────── */
QDialog#annotationDialog {
    background-color: #0A0A0F;
}

QDialog#annotationDialog QLabel {
    background-color: transparent;
}

QDialog#annotationDialog QLineEdit {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    color: #E8EAF0;
}

QDialog#annotationDialog QLineEdit:focus {
    border-color: #FF6B35;
}

/* ── Layer Order Pill ────────────────────────────────── */
#layerOrderPill {
    background: transparent;
}

#layerOrderPanel {
    background-color: rgba(15, 15, 26, 0.95);
    border-radius: 10px;
    border: 1px solid rgba(74, 83, 108, 0.55);
    min-width: 200px;
}

#layerOrderPanel QWidget {
    background: transparent;
}

/* ── Archive Controls Bar ─────────────────────────────── */
#archiveControls {
    background-color: rgba(15, 15, 26, 0.88);
    border: 1px solid rgba(84, 94, 122, 0.5);
    border-radius: 8px;
}

#archiveControls QLabel {
    background: transparent;
    padding: 0;
}

#archiveControls QToolButton {
    background-color: rgba(26, 26, 46, 0.34);
    border: 1px solid transparent;
    border-radius: 5px;
    color: #C8D0DE;
    font-size: 12px;
    font-weight: 600;
    padding: 0;
}

#archiveControls QToolButton:hover {
    background-color: rgba(32, 37, 58, 0.85);
    border-color: rgba(74, 83, 108, 0.75);
    color: #EFF3FF;
}

#archiveControls QToolButton:checked {
    background-color: #FF9F1C;
    border-color: #FF9F1C;
    color: #0A0A0F;
}

#archiveControls QSlider::groove:horizontal {
    height: 4px;
    background: #1E1E2E;
    border-radius: 2px;
}

#archiveControls QSlider::handle:horizontal {
    background: #00CFFF;
    border: none;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

#archiveControls QSlider::sub-page:horizontal {
    background: #00CFFF;
    border-radius: 2px;
}

#archiveControls QComboBox {
    background-color: rgba(26, 26, 46, 0.55);
    border: 1px solid rgba(46, 46, 78, 0.85);
    border-radius: 5px;
    color: #C8D0DE;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
}

#archiveControls QComboBox:hover {
    border-color: #3C4664;
}

#archiveControls QComboBox QAbstractItemView {
    background-color: #1A1A2E;
    border: 1px solid #3C4664;
    color: #E8EAF0;
    selection-background-color: #FF9F1C;
    selection-color: #0A0A0F;
}
""".replace("#FF6B35", ACCENT_COLOR)

# accent color for use in Python code — reads from config.toml [ui] accent_color
ACCENT = ACCENT_COLOR

BG_BASE       = "#0A0A0F"
BG_ELEVATED   = "#1A1A2E"
TEXT_MUTED    = "#6E7A8F"
