
import csv
import json
import logging
import os
import threading
import time
import feature_flags
import runtime_flags
from collections import deque
from datetime import datetime, timezone, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QLabel, QDockWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QFrame, QCheckBox, QPushButton, QGridLayout,
    QFileDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, QObject, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QKeySequence, QShortcut, QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

from ui.theme import DARK_THEME, ACCENT
from ui.map.widget import MapWidget, TILES_PATH
from ui.controls.radar_controls import RadarControls, NEXRAD_SITES
from ui.controls.hazard_controls import HazardControls
from ui.controls.routing_controls import RoutingControls, _make_loc_icon
from ui.widgets.nav_pill import NavPill
from ui.controls.deploy_locs_controls import DeployLocsControls
from ui.controls.satellite_controls import SatelliteControls
from ui.controls.map_controls import MapControls
from ui.controls.landcover_controls import LandcoverControls
from ui.controls.mesoanalysis_controls import MesoanalysisControls
from ui.controls.sfcoa_controls import SfcoaControls
from ui.controls.surface_controls import SurfaceControls
from ui.controls.noxp_controls import NoxpControls
from ui.controls.raw_lidar_controls import RawLidarControls
from ui.widgets.outlook_panel import OutlookPanel
from ui.map.radar_overlay import RadarOverlay, render_scan_to_png as _render_scan_to_png
from ui.sounding.dialog import SoundingDialog
from ui.sounding.controls import SoundingControls
from ui.dialogs.vehicle_timeseries_dialog import VehicleTimeseriesDialog
from ui.dialogs.raw_lidar_quicklook_dialog import RawLidarQuicklookDialog
from archive.vehicle_speed import calculate_vehicle_speed, format_vehicle_speed
from ui.app.overlay_geometry import bottom_left_y_avoiding
from ui.widgets.annotation_tools import AnnotationTools
from ui.dialogs.annotation_dialog import AnnotationPlaceDialog, AnnotationEditDialog, AnnotationMoveConfirmDialog
from ui.dialogs.drawing_dialog import (
    DrawingTitleDialog, DrawingEditDialog, DrawingPlaceConfirmDialog,
    DrawingMoveConfirmDialog,
)
from ui.dialogs.storm_cone_dialog import (
    StormConeInputDialog, StormConePlaceConfirmDialog, StormConeMotionConfirmDialog,
    StormConeMoveConfirmDialog,
)
from ui.dialogs.scan_sector_dialog import ScanSectorDialog
from data.fetchers.radar_fetcher import RadarFetcher
from data.fetchers.sounding_fetcher import SoundingFetcher
from data.fetchers.obs_sounding_fetcher import ObsSoundingFetcher
from data.fetchers.clamps_sounding_fetcher import ClampsSoundingFetcher
from data.stations.sounding_stations import build_stations_geojson
from data.stations.sounding_utils import nearest_obs_station, nssl_within_radius_km
from data.fetchers.hazard_fetcher import HazardFetcher
from data.update_checker import UpdateWorker
from data.fetchers.satellite_fetcher import SatelliteFetcher
from data.fetchers.mesoanalysis_fetcher import MesoanalysisFetcher
from data.fetchers.sfcoa_overlay_fetcher import SfcoaOverlayFetcher
from data.fetchers.surface_fetcher import SurfaceFetcher
from data.radar.radar_decoder import decode_nexrad_l3
import config
from core.annotation import Annotation, ANNOTATION_TYPE_MAP
from core.storm_cone import StormCone, motion_from_fixes
from core.scan_sector import ScanSector, feature_collection
from core.drawing import DrawingAnnotation, DRAWING_TYPE_MAP, FRONT_TYPE_KEYS
from core.observation import Observation
from core.vehicle import Vehicle
from network.mqtt_client import MQTTClient
from network.annotation_sync import AnnotationSync
from network.storm_cone_sync import StormConeSync
from network.drawing_sync import DrawingSync
from network.scan_sector_sync import ScanSectorSync
from network.vehicle_sync import VehicleSync
from data.ingest.gps_reader import GPSReader
from data.ingest.obs_file_watcher import ObsFileWatcher, FieldMap
from ui.layers.station_plot_layer import StationPlotLayer
from ui.layers.surface_plot_layer import SurfacePlotLayer
from ui.widgets.layer_order_pill import LayerOrderPill
from ui.app.main_window_debug import MainWindowDebugMixin
from ui.app.main_window_map_helpers import MainWindowMapHelpersMixin

log = logging.getLogger(__name__)

SCAN_MOVE_THRESHOLD_KM = 0.1
SCAN_MOVE_FIXES_TO_STOP = 3
LIVE_RADAR_MAX_AGE_MINUTES = 45
VEHICLE_PANEL_WIDTH = 340
NLCD_TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm_nlcd.mbtiles")
)
SATELLITE_TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "satellite.mbtiles")
)


def _overlay_time_label(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).strftime("%H:%MZ")
    except ValueError:
        if "T" in text:
            return text.split("T", 1)[1].replace(":00Z", "Z")[:6]
    return text


def _make_camera_icon(size: int = 18, color: str = "#C8D0DE") -> QIcon:
    """Return a minimal camera QIcon rendered from inline SVG."""
    svg = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M4 8 H8 L9.5 6 H14.5 L16 8 H20 V18 H4 Z"'
        ' fill="none" stroke="{c}" stroke-width="1.8"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="12" cy="13" r="3.2"'
        ' fill="none" stroke="{c}" stroke-width="1.8"/>'
        '</svg>'
    ).format(c=color)
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    return QIcon(px)


def _coords_close(a, b, tol: float = 1e-4) -> bool:
    """Return True if two [lat, lon] points are within ~10 m of each other."""
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _clear_layout(layout):
    """Remove and schedule deletion of all widgets in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


class _NetChecker(QObject):
    """Worker that checks internet connectivity from a background thread."""
    result_ready = pyqtSignal(str)   # "ok", "slow", or "none"

    def check(self):
        import socket, time
        try:
            t0 = time.monotonic()
            s  = socket.create_connection(("1.1.1.1", 53), timeout=2)
            s.close()
            self.result_ready.emit("slow" if time.monotonic() - t0 > 1.0 else "ok")
        except OSError:
            self.result_ready.emit("none")


class MainWindow(MainWindowMapHelpersMixin, MainWindowDebugMixin, QMainWindow):
    # emitted from background threads to update the discussion text panel safely.
    _panel_text_ready = pyqtSignal(int, str, str)
    # emitted from the decode thread when a scan has been decoded — carries
    _scan_decoded = pyqtSignal(int, str, str, object)
    # emitted from the decode thread on a decode failure — site, product.
    _radar_decode_failed = pyqtSignal(str, str)
    _render_ready = pyqtSignal(object)
    # emitted from the archive-render thread when an archive scan PNG is ready.
    _archive_render_ready = pyqtSignal(object)
    # emitted from the NOXP render thread when a rendered sweep PNG is ready.
    _noxp_render_ready = pyqtSignal(object)
    # emitted when the user aborts an archive loading session.
    session_aborted = pyqtSignal()

    def __init__(
        self,
        debug: bool = False,
        monitor: bool = False,
        viewer: bool = False,
        archive_time=None,    # datetime | None
    ):
        super().__init__()
        self._debug = debug
        self._monitor = monitor
        self._viewer = viewer
        self._archive_time = archive_time    # None = live mode
        self._archive = archive_time is not None
        self._current_radar_scan = None
        self._nws_active_phenoms: set[str] = set()  # phenom codes present in last NWS fetch
        self._mesoanalysis_fetcher = None
        self._mesoanalysis_current_metadata: dict | None = None
        self._mesoanalysis_visible = False
        self._mesoanalysis_active_products: set[str] = set()
        self._mesoanalysis_times_by_product: dict[str, list] = {}
        self._mesoanalysis_times = []
        self._mesoanalysis_overlay_cache: dict[tuple[str, str], dict] = {}
        self._sfcoa_fetcher = None
        self._sfcoa_current_metadata: dict | None = None
        self._sfcoa_visible = False
        self._sfcoa_active_products: set[str] = set()
        self._sfcoa_times = []
        self._sfcoa_variables_by_time: dict[str, list] = {}
        self._sfcoa_overlay_cache: dict[tuple[str, str], dict] = {}
        self._sfcoa_status_text = ""

        self.setWindowTitle(
            f"STORM  v{config.VERSION}"
            + (f"  [ARCHIVE {archive_time.strftime('%Y-%m-%d %H:%MZ')}]"
               if self._archive else "")
        )
        self.setMinimumSize(1024, 680)
        self.resize(1280, 800)

        # global dark theme applied once here — all children inherit via QSS cascade
        self.setStyleSheet(DARK_THEME)
        # build UI in dependency order
        self._runtime_safe = runtime_flags.FLAGS.runtime_safe

        self._init_map()
        self._init_toolbar()
        self._init_statusbar()
        self._init_vehicle_panel()
        self._radar_station_sites = self._load_radar_station_sites()
        self._radar_station_picker_visible = False
        self._radar_auto_site_pending = not monitor and not self._archive
        self._startup_sequence_started = False
        self._startup_local_pending = False
        self._startup_mqtt_pending = False
        self._post_startup_fetchers_started = False
        # track whether CWA shapefile has been loaded into the map
        self._cwa_loaded = False
        self._local_startup_timer = QTimer(self)
        self._local_startup_timer.setSingleShot(True)
        self._local_startup_timer.timeout.connect(self._complete_local_startup_phase)
        self._mqtt_startup_timer = QTimer(self)
        self._mqtt_startup_timer.setSingleShot(True)
        self._mqtt_startup_timer.timeout.connect(self._complete_mqtt_startup_phase)

        if self._archive:
            self.map_widget.map_ready.connect(self._begin_archive_startup)
        else:
            self.map_widget.map_ready.connect(self._begin_startup_sequence)

        # fine-grained startup toggles are for crash-isolation only.
        self._disable_radar = runtime_flags.FLAGS.disable_radar
        self._disable_mqtt = runtime_flags.FLAGS.disable_mqtt
        self._disable_annotations = runtime_flags.FLAGS.disable_annotations
        self._disable_deploy_locs = runtime_flags.FLAGS.disable_deploy_locs
        self._disable_data_inputs = runtime_flags.FLAGS.disable_data_inputs

        # features that require MQTT should be disabled when MQTT is disabled.
        if self._disable_mqtt:
            self._disable_annotations = True
            self._disable_data_inputs = True

        if self._runtime_safe:
            log.warning("Running in safe runtime mode (radar/MQTT/data inputs disabled)")
            self._init_measure()
            self._init_stations()
            self.status_msg_label.setText("Safe runtime mode - background services disabled")
            self.status_msg_label.setStyleSheet(
                "color: #FFD166; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
            )
        else:
            log.warning(
                "Startup toggles: radar=%s mqtt=%s annotations=%s deploy_locs=%s data_inputs=%s",
                "off" if self._disable_radar else "on",
                "off" if self._disable_mqtt else "on",
                "off" if self._disable_annotations else "on",
                "off" if self._disable_deploy_locs else "on",
                "off" if self._disable_data_inputs else "on",
            )

            self._init_measure()
            self._init_stations()

            if not self._disable_deploy_locs:
                self._init_deploy_locs()

        # wire map mousemove → status bar coordinate and zoom display
        self.map_widget.map_moved.connect(self._on_map_moved)

        # clock ticks every second
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._clock_layout_synced = False
        self._update_clock()

        # internet connectivity indicator — checks every 30 seconds
        self._start_net_check()

        # gps fix age indicator — polls every 5 seconds (vehicle mode only)
        self._last_local_obs_ts: float = 0.0
        if not (self._monitor or self._viewer):
            self._gps_indicator_timer = QTimer(self)
            self._gps_indicator_timer.timeout.connect(self._update_gps_indicator)
            self._gps_indicator_timer.start(5_000)
            self._update_gps_indicator()

        # in-ops update checker — background, non-blocking, no dialogs
        self._start_update_check()

        # restore window geometry and dock layout from last session.
        _s = QSettings("NSSL", "STORM")
        if _s.contains("geometry"):
            self.restoreGeometry(_s.value("geometry"))
        if _s.contains("windowState"):
            self.restoreState(_s.value("windowState"))
            # keep toolbar button in sync with current vehicle panel visibility
            self.btn_vehicles.setChecked(self.vehicle_panel.isVisible())

        # screenshot button — persistent, bottom-right above zoom controls
        self._init_screenshot_button()
        self._init_scan_button()

        # layer order pill — floats above the bottom-left status pill
        self._layer_pill = LayerOrderPill(self._map_container)
        self._layer_pill.order_changed.connect(self._apply_layer_order)
        self._layer_pill.order_changed.connect(lambda _: self._layout_overlays())
        self._layer_pill.size_changed.connect(self._layout_overlays)
        self._layer_pill.show()

        # extra startup layout passes avoid first-paint clipping in floating pills.
        QTimer.singleShot(0, self._layout_overlays)
        QTimer.singleShot(220, self._layout_overlays)

        # ctrl+d toggles debug panel even outside --debug mode (emergency diagnostic)
        self._debug_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self._debug_shortcut.activated.connect(self._toggle_debug_panel)
        # ctrl+E toggles error log panel
        self._error_log_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        self._error_log_shortcut.activated.connect(self._toggle_error_log_panel)
        # esc cancels in-progress line/polygon/front drawing.
        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self._on_escape_pressed)

        # auto-init debug panel when launched with --debug flag
        if debug:
            self._init_debug_panel()


    def _init_map(self):
        # container fills the QMainWindow central area; map + overlays are
        self._map_container = QWidget()
        self.setCentralWidget(self._map_container)

        self.map_widget = MapWidget()
        self.map_widget.setParent(self._map_container)

        # defer initial geometry until after all overlay widgets exist
        QTimer.singleShot(0, self._layout_overlays)


    def _begin_archive_startup(self):
        """Called once the map is ready in archive mode."""
        from archive.session import ArchiveSession
        from archive.time_controller import TimeController
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher
        from archive.fetchers.satellite_archive_fetcher import ArchiveSatelliteFetcher
        from archive.fetchers.hazard_archive_fetcher import ArchiveHazardFetcher
        from archive.fetchers.sounding_archive_fetcher import ArchiveSoundingFetcher
        from archive.fetchers.clamps_wind_archive_fetcher import ArchiveClampsWindFetcher
        from archive.fetchers.mqtt_reader import ArchiveMQTTReader
        from ui.controls.archive_controls import ArchiveControls
        from ui.dialogs.archive_loading_dialog import ArchiveLoadingDialog

        self._archive_session = ArchiveSession(start_time=self._archive_time)

        # time controller — central archive clock.
        self._time_ctrl = TimeController(start_time=self._archive_time, parent=self)

        # archive controls bar at bottom of screen.
        self._archive_controls = ArchiveControls(self._time_ctrl, self._map_container)
        self._archive_controls.setObjectName("archiveControls")
        self._archive_controls.set_radar_status("Radar: waiting")
        self._archive_controls.set_satellite_status("Sat: waiting")
        self._archive_controls.set_obs_status(
            "OBS: probing" if runtime_flags.FLAGS.admin_mode else "OBS: MQTT"
        )
        self._archive_controls.show()

        # mqtt reader — vehicles, annotations, cones, drawings.
        self._annotations: dict = {}
        self._drawings: dict = {}
        self._storm_cones: dict = {}

        self._archive_mqtt = ArchiveMQTTReader(
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_mqtt.vehicle_position.connect(self._on_archive_vehicle_position)
        self._archive_mqtt.vehicles_cleared.connect(self._on_archive_vehicles_cleared)
        self._archive_mqtt.annotation_received.connect(self._recv_remote_annotation)
        self._archive_mqtt.annotation_deleted.connect(self._recv_remote_annotation_deleted)
        self._archive_mqtt.cone_received.connect(self._recv_remote_storm_cone)
        self._archive_mqtt.cone_deleted.connect(self._recv_remote_storm_cone_deleted)
        self._archive_mqtt.drawing_received.connect(self._recv_remote_drawing)
        self._archive_mqtt.drawing_deleted.connect(self._recv_remote_drawing_deleted)
        self._archive_mqtt.scan_sector_received.connect(self._recv_remote_scan_sector)
        self._archive_mqtt.scan_sectors_cleared.connect(self._clear_archive_scan_sectors)
        self._archive_vehicle_obs = None
        self._archive_vehicle_obs_started = False
        self._archive_vehicle_obs_from_catalog = False
        self._archive_vehicle_obs_roster_size = 0

        # hazard fetcher.
        self._archive_hazard = ArchiveHazardFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_hazard.spc_received.connect(self._on_spc_received)
        self._archive_hazard.nws_received.connect(self._on_nws_received)
        self._archive_hazard.watches_received.connect(self._on_spc_watches_received)
        self._archive_hazard.spc_mds_received.connect(self._on_spc_mds_received)

        # satellite fetcher.
        self._archive_satellite = ArchiveSatelliteFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_satellite.frame_ready.connect(self._on_archive_satellite_frame)
        self._archive_satellite.meso_sectors_updated.connect(self._on_meso_sectors_updated)

        # sounding dialog and archive fetcher (on-demand).
        self._sounding_dialog = SoundingDialog(self)
        self._archive_sounding = ArchiveSoundingFetcher(parent=self)
        self._archive_sounding.sounding_ready.connect(self._on_sounding_ready)
        self._archive_sounding.fetch_error.connect(
            lambda msg: self.status_msg_label.setText(f"Sounding: {msg}")
        )
        # also initialise the sounding-station layer so the map shows clickable sites.
        self._sounding_stations_geojson = build_stations_geojson()

        # CLAMPS wind profiles (VAD dialog reuse; on-demand, like soundings).
        self._archive_clamps_wind = ArchiveClampsWindFetcher(parent=self)
        self._archive_clamps_wind.sets_ready.connect(self._on_archive_clamps_wind_ready)
        self._archive_clamps_wind.error.connect(
            lambda msg: self.status_msg_label.setText(f"CLAMPS wind: {msg}")
        )

        # NOXP mobile radar (on-demand, platform picked in noxp_controls;
        # noxp_controls itself, and its platform list, are set up in
        # _init_toolbar -- that always runs before this).
        self._archive_noxp = None
        if feature_flags.is_enabled("noxp_radar"):
            from archive.fetchers.noxp_radar_archive_fetcher import ArchiveNoxpRadarFetcher
            self._noxp_current_volume = None
            self._archive_noxp = ArchiveNoxpRadarFetcher(parent=self)
            self._archive_noxp.assets_ready.connect(self._on_archive_noxp_assets_ready)
            self._archive_noxp.volume_loaded.connect(self._on_archive_noxp_volume_loaded)
            self._archive_noxp.error.connect(
                lambda msg: self.status_msg_label.setText(f"NOXP: {msg}")
            )

        # CLAMPS raw lidar quicklook (discovers every known source once per
        # archive date; on-demand load of one selected file).
        self._archive_raw_lidar = None
        self._raw_lidar_dialog = None
        if feature_flags.is_enabled("raw_lidar_quicklook"):
            from archive.fetchers.raw_lidar_quicklook_fetcher import ArchiveRawLidarQuicklookFetcher
            self._archive_raw_lidar = ArchiveRawLidarQuicklookFetcher(parent=self)
            self._archive_raw_lidar.assets_ready.connect(self._on_archive_raw_lidar_assets_ready)
            self._archive_raw_lidar.rays_ready.connect(self._on_archive_raw_lidar_rays_ready)
            self._archive_raw_lidar.error.connect(
                lambda msg: self.status_msg_label.setText(f"Raw lidar: {msg}")
            )
            self._archive_raw_lidar.fetch(self._archive_time)

        # radar overlay (reuses existing renderer).
        self._radar_overlay = RadarOverlay(self.map_widget)

        # radar fetcher — created once the station is known.
        self._archive_radar: "ArchiveRadarFetcher | None" = None

        # single-worker pool for archive-mode renders — keeps the PNG render
        # (numpy/scipy work) off the UI thread during archive playback. Set
        # up here (not in _init_radar, which is skipped when radar is
        # disabled) since archive mode doesn't depend on the live radar path.
        from concurrent.futures import ThreadPoolExecutor
        self._archive_render_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="archive-radar-render"
        )
        # incremented on station change / new scan so stale in-flight renders are discarded
        self._archive_render_generation = 0
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = False
        self._archive_render_ready.connect(self._on_archive_render_ready)
        self._noxp_render_ready.connect(self._on_noxp_render_ready)

        # wire time controller to archive fetchers.
        self._time_ctrl.time_changed.connect(self._archive_mqtt.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_hazard.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_satellite.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_sounding.on_time_changed)

        # show loading dialog while initial data fetches run.
        loading_tasks = ["Vehicle tracks", "Radar index", "Hazard data", "Satellite index"]
        self._archive_loading = ArchiveLoadingDialog(
            session_label=self._archive_time.strftime("%Y-%m-%d  %H:%M UTC"),
            tasks=loading_tasks,
            parent=self,
        )

        # start background fetches; mark tasks done via callbacks.
        self._archive_mqtt.load()
        self._archive_hazard.load_day_data()
        self._archive_satellite.load_capabilities()

        # track completion of loading tasks.
        def _check_mqtt_loaded():
            if self._archive_mqtt._loaded:
                self._archive_loading.set_task_done("Vehicle tracks")
                self._start_archive_vehicle_obs()
                self._try_auto_select_radar_station()
            else:
                QTimer.singleShot(500, _check_mqtt_loaded)

        def _check_hazard_loaded():
            if self._archive_hazard._watches_loaded:
                self._archive_loading.set_task_done("Hazard data")
            else:
                QTimer.singleShot(500, _check_hazard_loaded)

        self._archive_satellite.error.connect(
            lambda _: self._archive_loading.set_task_error("Satellite index")
            if self._archive_loading.isVisible() else None
        )
        self._archive_satellite.error.connect(self._on_archive_satellite_error)

        def _check_satellite_indexed():
            if not self._archive_loading.isVisible():
                return
            if "conus" in self._archive_satellite._indexed_modes:
                self._archive_loading.set_task_done("Satellite index")
            else:
                QTimer.singleShot(1000, _check_satellite_indexed)

        QTimer.singleShot(500, _check_satellite_indexed)

        QTimer.singleShot(400, _check_mqtt_loaded)
        QTimer.singleShot(400, _check_hazard_loaded)

        self._archive_loading.show()
        # trigger an initial data load for all fetchers once loading completes.
        self._archive_loading.accepted.connect(
            lambda: self._time_ctrl.set_time(self._time_ctrl.current_time)
        )
        self._archive_loading.rejected.connect(self._on_archive_loading_aborted)

        self.hazard_controls.spc_day_changed.connect(self._on_archive_spc_day_changed)
        self.hazard_controls.spc_mode_changed.connect(self._on_archive_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_archive_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_archive_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_archive_nws_toggled)
        self.hazard_controls.fetch_requested.connect(self._archive_hazard.refresh_now)
        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self.satellite_controls.configure_for_archive(True)
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        self.map_widget.sounding_clicked.connect(self._on_archive_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_archive_obs_station_click)
        # mode_changed is normally connected in _init_soundings (not called in archive).
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        self.radar_controls.configure_for_archive(True)
        self.radar_controls.product_changed.connect(self._on_archive_product_changed)
        self.radar_controls.tilt_changed.connect(self._on_archive_tilt_changed)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)

        QTimer.singleShot(200, lambda: self._time_ctrl.set_time(self._archive_time))

        # lay out the archive controls bar at the bottom of the screen.
        QTimer.singleShot(0, self._layout_overlays)

    def _start_archive_vehicle_obs(self) -> None:
        """Start the optional admin one-second observation source."""
        if (
            not runtime_flags.FLAGS.admin_mode
            or self._archive_vehicle_obs_started
            or not hasattr(self, "_archive_mqtt")
        ):
            return
        self._archive_vehicle_obs_started = True

        vehicles = self._archive_mqtt.vehicle_metadata()
        used_catalog_roster = False
        if not vehicles:
            # No recorded MQTT history for this date (expected for dates
            # STORM wasn't deployed/connected for -- confirmed common for
            # older campaigns). Fall back to probing the full known FOFS
            # platform roster directly; ArchiveVehicleObsFetcher already
            # drops platforms with no file for this day, so this is safe
            # even though not every platform was active on every date.
            from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS
            vehicles = {platform: None for platform in KNOWN_FOFS_PLATFORMS}
            used_catalog_roster = True
        self._archive_vehicle_obs_from_catalog = used_catalog_roster
        self._archive_vehicle_obs_roster_size = len(vehicles)

        from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher

        self._archive_vehicle_obs = ArchiveVehicleObsFetcher(
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_vehicle_obs.observation_ready.connect(
            self._on_archive_dense_vehicle_position
        )
        self._archive_vehicle_obs.load_finished.connect(
            self._on_archive_vehicle_obs_loaded
        )
        self._archive_vehicle_obs.error.connect(
            lambda msg: log.warning("One-second archive unavailable: %s", msg)
        )
        # MQTT is connected first so its rewind clear/fallback pass happens before
        # dense observations overlay the vehicles they cover.
        self._time_ctrl.time_changed.connect(
            self._archive_vehicle_obs.on_time_changed
        )
        self._archive_vehicle_obs.load(vehicles)

    def _on_archive_vehicle_obs_loaded(self, vehicle_ids: set[str]) -> None:
        total = self._archive_vehicle_obs_roster_size
        if not vehicle_ids:
            status = (
                "OBS: no catalog data for this date"
                if self._archive_vehicle_obs_from_catalog
                else "OBS: MQTT fallback"
            )
            self._archive_controls.set_obs_status(status)
            return

        self._archive_controls.set_precision_mode(True)
        source_label = "catalog" if self._archive_vehicle_obs_from_catalog else "1-second"
        if len(vehicle_ids) == total:
            status = f"OBS: {source_label} ({len(vehicle_ids)})"
        else:
            status = f"OBS: {source_label} partial {len(vehicle_ids)}/{total}"
        self._archive_controls.set_obs_status(status, active=True)
        self._archive_vehicle_obs.on_time_changed(self._time_ctrl.current_time)
        self._layout_overlays()

    def _try_auto_select_radar_station(self) -> None:
        """Pick the nearest NEXRAD station from vehicle positions, or fall back
        to the home location when no MQTT data is available."""
        if not hasattr(self, "_archive_mqtt"):
            return
        positions = self._archive_mqtt.first_vehicle_positions()
        if positions:
            _, lat, lon = positions[0]
            site = self._nearest_nexrad(lat, lon, archive=True)
        else:
            log.info(
                "Archive: no vehicle positions — falling back to home location "
                "(%.3f, %.3f) for radar station selection",
                config.HOME_LAT, config.HOME_LON,
            )
            site = self._nearest_nexrad(config.HOME_LAT, config.HOME_LON, archive=True)

        if not site:
            self.status_msg_label.setText(
                "Could not determine radar station — select one on the map"
            )
            return

        self._archive_session.radar_station = site
        self._start_archive_radar(site)

    def _nearest_nexrad(self, lat: float, lon: float, archive: bool = False) -> "str | None":
        """Return the 4-letter NEXRAD ID closest to lat/lon.

        When archive=True, stations known to be absent from the public Level-2
        archive are excluded from consideration.
        """
        from archive.fetchers.radar_archive_fetcher import ARCHIVE_UNAVAILABLE_STATIONS
        best_site = None
        best_dist = float("inf")
        for info in (self._radar_station_sites or []):
            site_id = info.get("site_id", "")
            if archive and site_id in ARCHIVE_UNAVAILABLE_STATIONS:
                continue
            slat = info.get("lat", 0)
            slon = info.get("lon", 0)
            d = (lat - slat) ** 2 + (lon - slon) ** 2
            if d < best_dist:
                best_dist = d
                best_site = site_id
        return best_site

    def _start_archive_radar(self, station: str) -> None:
        """Instantiate and wire an ArchiveRadarFetcher for the given station."""
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher

        # discard any in-flight/pending render for the old station
        self._archive_render_generation += 1
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = False

        if self._archive_radar is not None:
            for sig in (
                self._archive_radar.scan_ready,
                self._archive_radar.loading_changed,
                self._archive_radar.error,
            ):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            try:
                self._time_ctrl.time_changed.disconnect(self._archive_radar.on_time_changed)
            except Exception:
                pass

        self._archive_radar = ArchiveRadarFetcher(
            station=station,
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_radar.scan_ready.connect(self._on_archive_radar_scan)
        self._archive_radar.loading_changed.connect(
            lambda loading: (
                self.status_msg_label.setText(f"Radar: loading {station}…" if loading else ""),
                self._archive_controls.set_radar_status(f"Radar: loading {station}…")
                if loading and hasattr(self, "_archive_controls") else None
            )
        )
        self._archive_radar.error.connect(self._on_archive_radar_error)
        self._time_ctrl.time_changed.connect(self._archive_radar.on_time_changed)

        loading = getattr(self, "_archive_loading", None)
        if loading is not None and loading.isVisible():
            self._archive_radar.index_loaded.connect(
                lambda _: loading.set_status("Fetching first radar scan…")
            )
            self._archive_radar.scan_ready.connect(
                lambda _: loading.set_task_done("Radar index")
            )
            self._archive_radar.error.connect(
                lambda _: loading.set_task_error("Radar index")
            )
            _radar_timeout = QTimer(self)
            _radar_timeout.setSingleShot(True)
            _radar_timeout.setInterval(45_000)
            _radar_timeout.timeout.connect(
                lambda: loading.set_task_done("Radar index")
                if loading.isVisible() else None
            )
            _radar_timeout.start()

        if hasattr(self, "radar_controls"):
            self.radar_controls.set_selected_site(station, emit=False)

        self._archive_radar.load_index()
        self._archive_radar.on_time_changed(self._time_ctrl.current_time)


    def _on_archive_radar_scan(self, scan) -> None:
        """Handle a newly decoded Level-2 scan from the archive fetcher.

        Updates tilt/product selectors immediately (cheap, UI-thread) and
        hands the PNG render (numpy/scipy work) off to a background thread
        so archive playback doesn't stall the UI on every frame.
        """
        self._current_radar_scan = scan

        # add tilt/product selectors to archive controls the first time;
        if hasattr(scan, "available_products"):
            from core.level2_radar_scan import L2_PRODUCTS
            products = [
                (f, L2_PRODUCTS[f]["label"]) for f in scan.available_products
                if f in L2_PRODUCTS
            ]
            self.radar_controls.set_archive_products(products)
            current_tilt_idx = getattr(self._archive_radar, "_tilt_idx", 0)
            self.radar_controls.set_archive_tilts(scan.available_tilts, current_tilt_idx)

        self._archive_pending_render_scan = scan
        self._submit_pending_archive_render()

    def _submit_pending_archive_render(self) -> None:
        """Submit a background render for the latest pending archive scan.
        No-op if a render is already in flight — _on_archive_render_ready
        will submit the newest pending scan when the current render completes."""
        scan = self._archive_pending_render_scan
        if scan is None or self._archive_render_in_flight:
            return
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = True
        gen = self._archive_render_generation
        from ui.map.radar_overlay import RENDER_GRID_SIZE
        archive_grid = max(RENDER_GRID_SIZE, 768)
        self._archive_render_executor.submit(self._bg_render_archive, gen, scan, archive_grid)

    def _bg_render_archive(self, gen: int, scan, grid_size: int) -> None:
        """Runs in the archive-render thread pool — NOT on the main thread.
        Renders scan to PNG then emits _archive_render_ready (auto-queued to main thread)."""
        if gen != self._archive_render_generation:
            return
        from ui.map.radar_overlay import render_scan_to_png
        try:
            png, bounds, _ = render_scan_to_png(scan, grid_size)
        except Exception as exc:
            log.error("Archive radar render failed: %s", exc)
            self._archive_render_ready.emit({"gen": gen, "scan": scan, "error": str(exc)})
            return
        if gen != self._archive_render_generation:
            return
        self._archive_render_ready.emit({
            "gen": gen, "scan": scan, "png": png, "bounds": bounds,
        })

    def _on_archive_render_ready(self, result: dict) -> None:
        """Runs on the main thread — injects the pre-rendered archive PNG into the map."""
        self._archive_render_in_flight = False
        if result["gen"] == self._archive_render_generation:
            scan = result["scan"]
            if "error" in result:
                if hasattr(self, "_archive_controls"):
                    self._archive_controls.set_radar_status("Radar: render error", error=True)
            else:
                self._radar_overlay.inject(result["png"], result["bounds"])
                if hasattr(self, "radar_controls"):
                    self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
                if hasattr(self, "_archive_controls"):
                    self._archive_controls.set_radar_status(
                        f"Radar: {scan.pyart_field} {scan.tilt_deg:.1f}deg {scan.scan_time.strftime('%H:%MZ')}"
                    )
        if self._archive_pending_render_scan is not None:
            self._submit_pending_archive_render()

    def _on_archive_satellite_frame(self, frame) -> None:
        """Update the archive satellite frame; only show if the user has toggled it on."""
        w, s, e, n = frame.bbox
        self.map_widget.set_satellite_frame(frame.b64, w, s, e, n)
        self._archive_sat_has_data = True
        self.satellite_controls.set_scan_time(frame.time_str)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status(
                f"Sat: AWS {frame.mode.upper()} {frame.time_str}"
            )
        self._layout_overlays()
        if self.btn_satellite.isChecked():
            self.map_widget.set_satellite_visible(True)

    def _on_archive_vehicle_position(self, obs) -> None:
        """Update a vehicle marker from the MQTT archive."""
        dense_obs = getattr(self, "_archive_vehicle_obs", None)
        if (
            dense_obs is not None
            and dense_obs.has_fresh_observation(
                obs.vehicle_id,
                self._time_ctrl.current_time,
            )
        ):
            return
        self.update_vehicle_obs(obs)
        
        # also update timeseries dialog if open
        if (self._vehicle_timeseries_dlg is not None
                and self._vehicle_timeseries_dlg.isVisible()):
            observations = self._get_archive_vehicle_history(obs.vehicle_id)
            if observations:
                self._vehicle_timeseries_dlg.update_vehicle(
                    obs.vehicle_id, observations)

    def _on_archive_dense_vehicle_position(self, obs) -> None:
        """Update a vehicle from the optional one-second archive."""
        if not self._archive_vehicle_obs.has_fresh_observation(
            obs.vehicle_id,
            self._time_ctrl.current_time,
        ):
            return
        self.update_vehicle_obs(obs)
        if (
            self._vehicle_timeseries_dlg is not None
            and self._vehicle_timeseries_dlg.isVisible()
        ):
            observations = self._get_archive_vehicle_history(obs.vehicle_id)
            if observations:
                self._vehicle_timeseries_dlg.update_vehicle(
                    obs.vehicle_id,
                    observations,
                )

    def _on_archive_vehicles_cleared(self) -> None:
        """Remove all vehicle markers when time jumps backward."""
        for vid in list(self._vehicles.keys()):
            self.map_widget.remove_vehicle(vid)
        self._vehicles.clear()
        self.update_vehicle_count(0)
        self._sync_routing_vehicle_snapshot()

    def _clear_archive_scan_sectors(self) -> None:
        """Remove scan-sector overlays when archive time jumps backward."""
        self._scan_sectors.clear()
        self._refresh_scan_sectors()

    def _on_archive_tilt_changed(self, tilt_idx: int) -> None:
        if self._archive_radar:
            self._archive_radar.set_tilt_index(tilt_idx)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(f"Radar: loading tilt {tilt_idx}")

    def _on_archive_product_changed(self, pyart_field: str) -> None:
        if self._archive_radar:
            self._archive_radar.set_product(pyart_field)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(f"Radar: loading {pyart_field}")

    def _on_archive_radar_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status("Radar: error", error=True)
        self.status_msg_label.setText(msg)
        self._layout_overlays()

    def _on_archive_loading_aborted(self) -> None:
        """User clicked Abort on the loading dialog — quit the application."""
        self.session_aborted.emit()
        QApplication.quit()

    def _on_archive_satellite_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status("Sat: error", error=True)
        self.status_msg_label.setText(f"Satellite: {msg}")
        self._layout_overlays()


    def _on_archive_spc_mode_changed(self, mode: str) -> None:
        """Toggle SPC outlook/product layers in archive mode (no live fetcher)."""
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self.map_widget.set_spc_category_visible(key, outlook_on)
        for key in ("tor", "wind", "hail", "prob", "sig"):
            on = mode == key
            self.map_widget.set_spc_product_visible(key, on)
        self._update_hazard_legend()

    def _on_archive_spc_day_changed(self, day: int) -> None:
        self._archive_hazard.set_spc_day(day)
        empty = '{"type":"FeatureCollection","features":[]}'
        self.map_widget.set_spc_geojson(empty, empty, empty, empty, empty, empty)
        self._on_archive_spc_mode_changed(self.hazard_controls._active_spc_mode())

    def _on_archive_watches_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_watches_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_nws_toggled(self, enabled: bool) -> None:
        self.map_widget.set_nws_warnings_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_mds_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_mds_visible(enabled)
        self._update_hazard_legend()


    def _on_archive_sounding_map_click(self, lat: float, lon: float) -> None:
        self.status_msg_label.setText("Fetching archive sounding…")
        self._archive_sounding.fetch_model_sounding(lat, lon)

    def _on_archive_obs_station_click(
        self, station_id: str, name: str, lat: float, lon: float, elev: float
    ) -> None:
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._archive_sounding.fetch_obs_sounding(station_id, lat, lon, elev)


    def _begin_startup_sequence(self):
        if self._runtime_safe or self._startup_sequence_started:
            return
        self._startup_sequence_started = True
        self._begin_local_data_phase()

    def _begin_local_data_phase(self):
        if self._disable_data_inputs or self._monitor:
            self._complete_local_startup_phase()
            return
        self._startup_local_pending = True
        self._init_data_inputs()
        self._local_startup_timer.start(3000)

    def _complete_local_startup_phase(self):
        if self._startup_local_pending:
            self._startup_local_pending = False
            self._local_startup_timer.stop()
        self._begin_mqtt_phase()

    def _begin_mqtt_phase(self):
        if self._startup_mqtt_pending or hasattr(self, "_mqtt_client") or self._disable_mqtt:
            if self._disable_mqtt:
                self._complete_mqtt_startup_phase()
            return
        self._startup_mqtt_pending = True
        self._init_mqtt()
        if not self._disable_annotations:
            self._init_annotations()
            self._init_storm_cone()
            threading.Thread(target=self._fetch_current_json, daemon=True).start()
        if not config.MQTT_HOST:
            self._complete_mqtt_startup_phase()
        else:
            self._mqtt_startup_timer.start(4000)

    def _fetch_current_json(self):
        """One-shot background fetch of current.json to pre-populate annotations on launch."""
        from urllib.request import urlopen, Request

        url = f"{config.NSSL_API_ROOT}/current.json"
        try:
            headers = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
            if config.NSSL_API_KEY:
                headers["X-API-Key"] = config.NSSL_API_KEY
            req = Request(url, headers=headers)
            with urlopen(req, timeout=10, context=config.NSSL_SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            log.warning("current.json fetch failed: %s", e)
            return

        n_ann = n_cone = n_drawing = 0
        for item in data.values():
            if item.get("deleted"):
                continue
            if "type_key" in item:
                try:
                    ann = Annotation.from_dict(item)
                    self._annotation_sync.annotation_received.emit(ann)
                    n_ann += 1
                except Exception as e:
                    log.warning("current.json annotation parse error: %s", e)
            elif "drawing_type" in item:
                try:
                    drawing = DrawingAnnotation.from_dict(item)
                    self._drawing_sync.drawing_received.emit(drawing)
                    n_drawing += 1
                except Exception as e:
                    log.warning("current.json drawing parse error: %s", e)
            elif "speed_kts" in item or "heading" in item:
                try:
                    cone = StormCone.from_dict(item)
                    self._storm_cone_sync.cone_received.emit(cone)
                    n_cone += 1
                except Exception as e:
                    log.warning("current.json cone parse error: %s", e)

        log.info("current.json loaded: %d annotations, %d cones, %d drawings", n_ann, n_cone, n_drawing)

    def _on_refresh_current_json(self):
        """Triggered by the Refresh button in the annotation toolbar."""
        threading.Thread(target=self._fetch_current_json, daemon=True).start()

    def _complete_mqtt_startup_phase(self):
        if self._startup_mqtt_pending:
            self._startup_mqtt_pending = False
            self._mqtt_startup_timer.stop()
        self._start_post_startup_fetchers()

    def _start_post_startup_fetchers(self):
        if self._post_startup_fetchers_started:
            return
        self._post_startup_fetchers_started = True

        s = QSettings()
        self._launch_auto_spc        = s.value("launch/auto_spc",        False, type=bool)
        self._launch_auto_nws        = s.value("launch/auto_nws",        False, type=bool)
        self._launch_auto_radar      = s.value("launch/auto_radar",      False, type=bool)
        self._launch_auto_satellite  = s.value("launch/auto_satellite",  "",    type=str)
        self._launch_auto_obs_ok     = s.value("launch/auto_obs_ok",     False, type=bool)
        self._launch_auto_obs_wtm    = s.value("launch/auto_obs_wtm",    False, type=bool)
        self._launch_auto_obs_ks     = s.value("launch/auto_obs_ks",     False, type=bool)
        self._launch_auto_obs_co     = s.value("launch/auto_obs_co",     False, type=bool)
        self._launch_auto_obs_ne     = s.value("launch/auto_obs_ne",     False, type=bool)

        if not self._disable_radar:
            self._init_radar()
        self._init_hazards()

        self._init_satellite()
        self._init_surface_obs()
        self._apply_launch_prefs()

    def _apply_launch_prefs(self):
        """Auto-enable map layers based on launch dialog preferences."""
        if self._launch_auto_spc:
            self.hazard_controls._btn_outlook.setChecked(True)
        if self._launch_auto_nws:
            self.hazard_controls._btn_nws_warnings.setChecked(True)
        if not self._disable_radar and hasattr(self, "_radar_fetcher") and self._launch_auto_radar:
            self.radar_controls._chk_show_data.blockSignals(True)
            self.radar_controls._chk_show_data.setChecked(True)
            self.radar_controls._chk_show_data.blockSignals(False)
            self._auto_start_radar()
            self.btn_radar.setChecked(True)
        if self._launch_auto_obs_ok:
            self.surface_controls._btn_ok.setChecked(True)
        if self._launch_auto_obs_wtm:
            self.surface_controls._btn_wtm.setChecked(True)
        if self._launch_auto_obs_ks:
            self.surface_controls._btn_ks.setChecked(True)
        if self._launch_auto_obs_co:
            self.surface_controls._btn_co.setChecked(True)
        if self._launch_auto_obs_ne:
            self.surface_controls._btn_ne.setChecked(True)
        sat = self._launch_auto_satellite
        if sat == "conus":
            self.satellite_controls._btn_conus.setChecked(True)
        elif sat == "auto_meso":
            self._auto_meso_pending = True
            self._satellite_fetcher.meso_sectors_updated.connect(self._on_auto_meso_caps_ready)

    def _on_auto_meso_caps_ready(self, sectors: dict):
        """One-shot: pick the closer meso sector once caps have been fetched."""
        if not getattr(self, "_auto_meso_pending", False):
            return
        self._auto_meso_pending = False
        try:
            self._satellite_fetcher.meso_sectors_updated.disconnect(self._on_auto_meso_caps_ready)
        except Exception:
            pass

        vehicle = self._vehicles.get(config.VEHICLE_ID)
        lat = vehicle.lat if vehicle else config.HOME_LAT
        lon = vehicle.lon if vehicle else config.HOME_LON

        best_idx = None
        best_dist = float("inf")
        for idx in (1, 2):
            bbox = sectors.get(idx)
            if not bbox:
                continue
            clat = (bbox["north"] + bbox["south"]) / 2
            clon = (bbox["east"]  + bbox["west"])  / 2
            dist = (lat - clat) ** 2 + (lon - clon) ** 2
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx == 1:
            self.satellite_controls._btn_meso1.setChecked(True)
        elif best_idx == 2:
            self.satellite_controls._btn_meso2.setChecked(True)
        else:
            self.satellite_controls._btn_conus.setChecked(True)


    def _init_toolbar(self):
        self._floating_toolbar = QWidget(self._map_container)
        self._floating_toolbar.setObjectName("floatingToolbar")

        tb = QHBoxLayout(self._floating_toolbar)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(4)

        self.btn_radar = self._toolbar_toggle("RADAR", "Show/hide radar controls", tb)
        # radar controls drop down below the toolbar as a separate floating pill
        self.radar_controls = RadarControls(self._map_container)
        self.radar_controls.setObjectName("floatingToolbar")
        self.btn_radar.toggled.connect(self.radar_controls.toggle_drawer)
        self.btn_radar.toggled.connect(
            lambda on: self._set_radar_station_picker_visible(False) if not on else None
        )
        # pulse layout updates for the duration of the open/close animation
        self.btn_radar.toggled.connect(self._start_layout_pulse)

        self._add_separator(tb)

        self.btn_vehicles = self._toolbar_toggle("VEHICLES", "Toggle vehicle panel", tb)

        self.btn_prev_locs = self._toolbar_toggle(
            "PREV LOCS", "Show previous truck deployment locations", tb
        )
        self.deploy_locs_controls = DeployLocsControls(self._map_container)
        self.deploy_locs_controls.setObjectName("floatingToolbar")
        self.btn_prev_locs.toggled.connect(self.deploy_locs_controls.toggle_drawer)
        self.btn_prev_locs.toggled.connect(self._start_layout_pulse)
        self.deploy_locs_controls.content_resized.connect(self._start_layout_pulse)

        self._add_separator(tb)

        self.btn_hazards = self._toolbar_toggle(
            "HAZARDS", "Show/hide SPC and NWS hazard overlays", tb
        )
        self.hazard_controls = HazardControls(self._map_container)
        self.hazard_controls.setObjectName("floatingToolbar")
        self.btn_hazards.toggled.connect(self.hazard_controls.toggle_drawer)
        self.btn_hazards.toggled.connect(self._start_layout_pulse)
        self.hazard_controls.content_resized.connect(self._start_layout_pulse)

        self.outlook_panel = OutlookPanel(self._map_container)
        self.outlook_panel.closed.connect(self._layout_overlays)
        self._fetch_generation = 0
        self._panel_text_ready.connect(self._on_panel_text_ready)

        self._add_separator(tb)

        self.btn_satellite = self._toolbar_toggle(
            "SATELLITE", "Show/hide GOES satellite imagery overlay", tb
        )
        self.satellite_controls = SatelliteControls(self._map_container)
        self.satellite_controls.setObjectName("floatingToolbar")
        self.btn_satellite.toggled.connect(self.satellite_controls.toggle_drawer)
        self.btn_satellite.toggled.connect(self._start_layout_pulse)
        self.btn_satellite.toggled.connect(self._on_satellite_toggled)

        if feature_flags.is_enabled("mesoanalysis") or feature_flags.is_enabled("sfcoa"):
            self._add_separator(tb)

        if feature_flags.is_enabled("mesoanalysis"):
            self.btn_mesoanalysis = self._toolbar_toggle(
                "MESO", "Show/hide Satsquatch mesoanalysis contours", tb
            )
            self.mesoanalysis_controls = MesoanalysisControls(self._map_container)
            self.mesoanalysis_controls.setObjectName("floatingToolbar")
            self.btn_mesoanalysis.toggled.connect(self.mesoanalysis_controls.toggle_drawer)
            self.btn_mesoanalysis.toggled.connect(self._start_layout_pulse)
            self.btn_mesoanalysis.toggled.connect(self._on_mesoanalysis_drawer_toggled)

        if feature_flags.is_enabled("sfcoa"):
            self.btn_sfcoa = self._toolbar_toggle(
                "SFCOA", "Show/hide SFCOA mesoanalysis contours", tb
            )
            self.sfcoa_controls = SfcoaControls(self._map_container)
            self.sfcoa_controls.setObjectName("floatingToolbar")
            self.btn_sfcoa.toggled.connect(self.sfcoa_controls.toggle_drawer)
            self.btn_sfcoa.toggled.connect(self._start_layout_pulse)
            self.btn_sfcoa.toggled.connect(self._on_sfcoa_drawer_toggled)
            self.sfcoa_controls.content_resized.connect(self._start_layout_pulse)

        if self._archive and (feature_flags.is_enabled("noxp_radar") or feature_flags.is_enabled("raw_lidar_quicklook")):
            self._add_separator(tb)

        if self._archive and feature_flags.is_enabled("noxp_radar"):
            from archive.catalog import ALL_PLATFORMS as _ALL_PLATFORMS
            self.btn_noxp = self._toolbar_toggle(
                "NOXP", "Show/hide NOXP mobile radar controls (archive)", tb
            )
            self.noxp_controls = NoxpControls(self._map_container)
            self.noxp_controls.setObjectName("floatingToolbar")
            self.noxp_controls.set_platforms(
                [p for p in _ALL_PLATFORMS if p.family == "NOXP Radar"]
            )
            self.btn_noxp.toggled.connect(self.noxp_controls.toggle_drawer)
            self.btn_noxp.toggled.connect(self._start_layout_pulse)
            self.noxp_controls.platform_selected.connect(self._on_noxp_platform_selected)
            self.noxp_controls.asset_selected.connect(self._on_noxp_asset_selected)
            self.noxp_controls.render_requested.connect(self._on_noxp_render_requested)

        if self._archive and feature_flags.is_enabled("raw_lidar_quicklook"):
            self.btn_raw_lidar = self._toolbar_toggle(
                "RAW LIDAR", "Show/hide CLAMPS raw lidar quicklook controls (archive)", tb
            )
            self.raw_lidar_controls = RawLidarControls(self._map_container)
            self.raw_lidar_controls.setObjectName("floatingToolbar")
            from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES
            self.raw_lidar_controls.set_sources(KNOWN_RAW_LIDAR_SOURCES)
            self.btn_raw_lidar.toggled.connect(self.raw_lidar_controls.toggle_drawer)
            self.btn_raw_lidar.toggled.connect(self._start_layout_pulse)
            self.raw_lidar_controls.quicklook_requested.connect(self._on_raw_lidar_quicklook_requested)

        self.btn_surface = self._toolbar_toggle(
            "SURFACE", "Show/hide surface observation controls", tb
        )
        self.surface_controls = SurfaceControls(self._map_container)
        self.surface_controls.setObjectName("floatingToolbar")
        self.btn_surface.toggled.connect(self.surface_controls.toggle_drawer)
        self.btn_surface.toggled.connect(self._start_layout_pulse)
        self.surface_controls.content_resized.connect(self._start_layout_pulse)

        self._add_separator(tb)

        self.btn_sounding = self._toolbar_toggle(
            "SOUNDING", "Click map for HRRR point sounding or observed radiosonde data", tb
        )
        self.sounding_controls = SoundingControls(self._map_container)
        self.sounding_controls.setObjectName("floatingToolbar")
        self.btn_sounding.toggled.connect(self.sounding_controls.toggle_drawer)
        self.btn_sounding.toggled.connect(self._start_layout_pulse)
        self.btn_sounding.toggled.connect(self._on_sounding_mode_toggled)

        self._add_separator(tb)

        self.btn_annotate = self._toolbar_toggle(
            "ANNOTATE", "Place road annotations and storm motion cone", tb
        )
        # annotation tools drop down below the toolbar as a separate floating pill
        self.annotation_tools = AnnotationTools(self._map_container)
        self.annotation_tools.setObjectName("floatingToolbar")
        self.btn_annotate.toggled.connect(self.annotation_tools.toggle_drawer)
        self.btn_annotate.toggled.connect(self._start_layout_pulse)

        self._add_separator(tb)

        self.btn_map = self._toolbar_toggle("MAP", "Map tools and base layers", tb)
        nlcd_available = feature_flags.is_enabled("nlcd") and os.path.isfile(NLCD_TILES_PATH)
        satellite_available = (
            feature_flags.is_enabled("satellite_basemap")
            and os.path.isfile(SATELLITE_TILES_PATH)
        )
        self.map_controls = MapControls(
            route_available=not self._viewer,
            landcover_available=nlcd_available,
            satellite_available=satellite_available,
            parent=self._map_container,
        )
        self.map_controls.setObjectName("floatingToolbar")
        self.btn_map.toggled.connect(self.map_controls.toggle_drawer)
        self.btn_map.toggled.connect(self._start_layout_pulse)

        self.btn_measure = self.map_controls.btn_measure
        self.btn_route = self.map_controls.btn_route
        self.btn_nlcd = self.map_controls.btn_landcover
        self.btn_satellite_basemap = self.map_controls.btn_satellite_basemap

        self.routing_controls = RoutingControls(self._map_container)
        self.routing_controls.setObjectName("floatingToolbar")
        self.btn_route.toggled.connect(self.routing_controls.toggle_drawer)
        self.btn_route.toggled.connect(self._start_layout_pulse)
        self.routing_controls.enter_pick_mode.connect(
            lambda: self.map_widget.set_route_pick_mode(True)
        )
        self.routing_controls.enter_pick_mode.connect(
            lambda: self.btn_sounding.setChecked(False) if self.btn_sounding.isChecked() else None
        )
        self.routing_controls.enter_pick_mode.connect(
            lambda: self.btn_measure.setChecked(False) if self.btn_measure.isChecked() else None
        )
        self.routing_controls.cancel_pick_mode.connect(
            lambda: self.map_widget.set_route_pick_mode(False)
        )
        self.routing_controls.route_calculated.connect(self._on_route_calculated)
        self.routing_controls.route_cleared.connect(self._on_route_cleared)
        self.routing_controls.content_resized.connect(self._start_layout_pulse)
        self.map_widget.map_pick_for_route.connect(
            self.routing_controls.on_map_pick
        )
        self.map_widget.private_pin_route_requested.connect(
            self._on_private_pin_route_requested
        )
        if nlcd_available:
            self.landcover_controls = LandcoverControls(self._map_container)
            self.landcover_controls.setObjectName("floatingToolbar")
            self.btn_nlcd.toggled.connect(self.landcover_controls.toggle_drawer)
            self.btn_nlcd.toggled.connect(self._start_layout_pulse)
            self.btn_nlcd.toggled.connect(self._on_nlcd_toggled)
            self.landcover_controls.opacity_changed.connect(self._on_nlcd_opacity_changed)
        if satellite_available:
            self.btn_satellite_basemap.toggled.connect(self._on_satellite_basemap_toggled)
        if self._archive:
            self.btn_prev_locs.hide()
            # no archive data sources for these — hide to prevent crashes.
            self.btn_surface.hide()     # _surface_fetcher / _surface_layer absent
            self.btn_annotate.hide()    # _mqtt_client / _annotation_sync / _drawing_sync absent
            if hasattr(self, "btn_mesoanalysis"):
                self.btn_mesoanalysis.hide()   # Satsquatch mesoanalysis is not archived
            if hasattr(self, "btn_sfcoa"):
                self.btn_sfcoa.hide()          # SFCOA mesoanalysis is not archived

        self.nav_pill = NavPill(self._map_container)
        self._pill_route_expanded = False
        self.routing_controls.nav_updated.connect(self._on_nav_updated)
        self.routing_controls.route_cleared.connect(self.nav_pill.nav_clear)
        self.routing_controls.route_cleared.connect(self._layout_overlays)
        self.routing_controls.route_cleared.connect(self._on_pill_route_cleared)
        self.nav_pill.open_drawer_requested.connect(self._on_pill_expand_requested)
        self.nav_pill.clear_requested.connect(self.routing_controls._on_clear)
        self.btn_route.toggled.connect(self._on_route_drawer_toggled)
        self.btn_route.toggled.connect(
            lambda on: self.btn_measure.setChecked(False) if on and self.btn_measure.isChecked() else None
        )

    def _on_nav_updated(self, step_text: str, summary: str):
        """Show/refresh the nav pill — but only when the toolbar drawer is open."""
        self.nav_pill.update_nav(step_text, summary)
        if self.btn_route.isChecked():
            self.nav_pill.hide()
        self._layout_overlays()

    def _on_pill_expand_requested(self):
        """Toggle the floating routing panel below the nav pill."""
        self._pill_route_expanded = not self._pill_route_expanded
        self.nav_pill.set_expanded(self._pill_route_expanded)
        self.routing_controls.toggle_drawer(self._pill_route_expanded)
        self._start_layout_pulse()

    def _on_pill_route_cleared(self):
        """Collapse the pill-expanded routing panel when the route is cleared."""
        if self._pill_route_expanded:
            self._pill_route_expanded = False
            self.nav_pill.set_expanded(False)
            self.routing_controls.toggle_drawer(False)

    def _on_route_drawer_toggled(self, checked: bool):
        """Hide pill while toolbar drawer is open; restore it when drawer closes."""
        if checked:
            # if pill-expand was open, close it — toolbar takes over
            if self._pill_route_expanded:
                self._pill_route_expanded = False
                self.nav_pill.set_expanded(False)
            self.nav_pill.hide()
        elif self.routing_controls._last_result is not None:
            self.nav_pill.show()
        self._layout_overlays()

    def _on_route_calculated(self, result):
        self._set_layer_active("route", True)
        import json as _json
        geojson_str = _json.dumps(result.geometry)
        dlat, dlon  = result.dest_latlon
        self.map_widget.set_route(geojson_str, dlon, dlat)

    def _on_route_cleared(self):
        self._set_layer_active("route", False)
        self.map_widget.clear_route()
        self.map_widget.set_route_pick_mode(False)

    def _on_private_pin_route_requested(self, lat: float, lon: float, label: str):
        if self._viewer or self._monitor or self._archive:
            return
        if not hasattr(self, "routing_controls"):
            return
        self.routing_controls.route_to_pin(lat, lon, label)
        self.btn_route.setChecked(True)
        self._layout_overlays()

    def _toolbar_toggle(self, label: str, tooltip: str, layout: QHBoxLayout) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setChecked(False)
        layout.addWidget(btn)
        return btn

    def _add_separator(self, layout: QHBoxLayout):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #394056; margin: 6px 2px;")
        layout.addWidget(sep)


    def _init_statusbar(self):
        # row 0: version anchor | [update indicator] | status message
        self._status_left = QWidget(self._map_container)
        self._status_left.setObjectName("statusOverlayLeft")
        pill = QVBoxLayout(self._status_left)
        pill.setContentsMargins(10, 5, 10, 5)
        pill.setSpacing(3)

        row0 = QHBoxLayout()
        row0.setContentsMargins(0, 0, 0, 0)
        row0.setSpacing(8)

        version_label = QLabel(f"STORM v{config.VERSION}")
        version_label.setStyleSheet(
            "color: #4A5268; font-size: 10px; font-weight: 600; letter-spacing: 0.8px;"
        )
        row0.addWidget(version_label)

        # in-ops update indicator — hidden until an update is detected
        self.update_indicator = QPushButton("↑ UPDATE AVAILABLE")
        self.update_indicator.setFlat(True)
        self.update_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            "color: #00CFFF; background: transparent; border: none; padding: 0;"
        )
        self.update_indicator.setToolTip("Click to apply update and restart STORM")
        self.update_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_indicator.setVisible(False)
        self.update_indicator.clicked.connect(self._on_update_indicator_clicked)
        row0.addWidget(self.update_indicator)

        self.status_msg_label = QLabel("")
        self.status_msg_label.setStyleSheet(
            "color: #9BA3B2; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row0.addWidget(self.status_msg_label)
        row0.addStretch()

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        self.coord_label = QLabel("LAT: ---.---- LON: ---.----")
        coord_probe = "LAT: -180.0000  LON: -180.0000"
        self.coord_label.setMinimumWidth(self.coord_label.fontMetrics().horizontalAdvance(coord_probe) + 8)
        self.vehicle_count_label = QLabel("VEHICLES: 0")

        for lbl in [self.coord_label, self.vehicle_count_label]:
            lbl.setStyleSheet("color: #C8D0DE; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;")

        # mode badge: VEHICLE / MONITOR / VIEWER / ARCHIVE — always leftmost in row 1
        if self._archive:
            mode_badge = QLabel("● ARCHIVE")
            mode_badge.setStyleSheet(
                "color: #FF9F1C; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        elif self._monitor:
            mode_badge = QLabel("● MONITOR")
            mode_badge.setStyleSheet(
                "color: #FFD166; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        elif self._viewer:
            mode_badge = QLabel("● VIEWER")
            mode_badge.setStyleSheet(
                "color: #9B8FFF; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        elif runtime_flags.FLAGS.admin_mode:
            mode_badge = QLabel("● ADMIN")
            mode_badge.setStyleSheet(
                "color: #00CFFF; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        else:
            mode_badge = QLabel("● VEHICLE")
            mode_badge.setStyleSheet(
                "color: #39D98A; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        row1.addWidget(mode_badge)

        row1.addWidget(self._status_divider())
        row1.addWidget(self.coord_label)
        row1.addWidget(self._status_divider())
        row1.addWidget(self.vehicle_count_label)
        row1.addStretch()

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)

        # gps fix status — vehicle mode only (hidden in monitor/viewer)
        self.gps_indicator = QLabel("● NO GPS FIX")
        self.gps_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        self.gps_indicator.setVisible(not (self._monitor or self._viewer))
        row2.addWidget(self.gps_indicator)

        if not (self._monitor or self._viewer):
            row2.addWidget(self._status_divider())

        self.conn_indicator = QLabel("● AWS OFFLINE")
        self.conn_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        row2.addWidget(self.conn_indicator)
        row2.addWidget(self._status_divider())

        self.net_indicator = QLabel("")
        self.net_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #3A3B4A;"
        )
        self.net_indicator.setVisible(False)
        row2.addWidget(self.net_indicator)

        row2.addStretch()

        self.date_label = QLabel("-- --- ----")
        self.date_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
        )
        row2.addWidget(self.date_label)

        row2.addWidget(self._status_divider())

        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
        )
        row2.addWidget(self.clock_label)

        # keep hazard_indicator as a hidden member so existing callers don't break
        self.hazard_indicator = QLabel("● DATA OFFLINE")
        self.hazard_indicator.setVisible(False)

        pill.addLayout(row0)
        pill.addLayout(row1)
        pill.addLayout(row2)


    def _status_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #394056; margin: 4px 0;")
        return div

    def closeEvent(self, event):
        _s = QSettings("NSSL", "STORM")
        _s.setValue("geometry", self.saveGeometry())
        _s.setValue("windowState", self.saveState())
        from ui.widgets.layer_order_pill import save_layer_order
        save_layer_order(self._layer_pill.current_order())

        # stop all background workers before closing so threads don't outlive
        self._clock_timer.stop()
        if hasattr(self, "_update_check_timer"):
            self._update_check_timer.stop()
        if hasattr(self, "_time_ctrl"):
            self._time_ctrl.pause()
        if hasattr(self, "_radar_fetcher"):
            self._radar_fetcher.stop()
        if hasattr(self, "_hazard_fetcher"):
            self._hazard_fetcher.stop()
        if hasattr(self, "_satellite_fetcher"):
            self._satellite_fetcher.stop()
        if hasattr(self, "_surface_fetcher"):
            self._surface_fetcher.stop()
        if hasattr(self, "_gps_reader") and self._gps_reader is not None:
            self._gps_reader.stop()
        if hasattr(self, "_obs_watcher") and self._obs_watcher is not None:
            self._obs_watcher.stop()
        if hasattr(self, "_mqtt_client"):
            self._mqtt_client.disconnect()
        self._cleanup_debug_panel()

        super().closeEvent(event)
        QApplication.quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlays()

    def _layout_overlays(self):
        """Position map + floating overlays within the container after any resize."""
        r = self._map_container.rect()

        MARGIN = 8

        # map always fills the full container (overlays float on top)
        self.map_widget.setGeometry(r)

        # toolbar: shrink-wrap to content, center horizontally, float with margin
        if hasattr(self, "_floating_toolbar"):
            # wide-mode scales toolbar controls slightly on larger windows
            wide_mode = r.width() >= 1500
            if self._floating_toolbar.property("wide") != wide_mode:
                self._floating_toolbar.setProperty("wide", wide_mode)
                self._floating_toolbar.style().unpolish(self._floating_toolbar)
                self._floating_toolbar.style().polish(self._floating_toolbar)
            self._floating_toolbar.adjustSize()
            tb_w = self._floating_toolbar.width()
            tb_h = self._floating_toolbar.height()
            tb_x = max(0, (r.width() - tb_w) // 2)
            self._floating_toolbar.setGeometry(tb_x, MARGIN, tb_w, tb_h)
            self._floating_toolbar.raise_()

            # stack open pills below the toolbar to avoid overlap
            _drop_y = MARGIN + tb_h + 4
            _stack_y = _drop_y

            def _stack(widget):
                nonlocal _stack_y
                if widget is None:
                    return
                layout = widget.layout()
                if layout is not None:
                    layout.activate()
                widget.adjustSize()
                w = widget.width()
                h = widget.height()
                x = max(0, (r.width() - w) // 2)
                widget.setGeometry(x, _stack_y, w, h)
                widget.raise_()
                _stack_y += h + 6

            if hasattr(self, "radar_controls") and self.btn_radar.isChecked():
                _stack(self.radar_controls)
            if hasattr(self, "vehicle_panel") and self.vehicle_panel.isVisible():
                # apply a session-scoped size watermark so the panel only ever
                # grows while open — eliminates the constant resize churn when
                # vehicles stream in/out. resets when the toolbar toggle hides it.
                vp = self.vehicle_panel
                if vp.layout() is not None:
                    vp.layout().activate()
                vp.adjustSize()
                wm_w, wm_h = self._vehicle_panel_size_watermark
                w = max(vp.width(), wm_w)
                h = max(vp.height(), wm_h)
                self._vehicle_panel_size_watermark = (w, h)
                x = max(0, (r.width() - w) // 2)
                vp.setGeometry(x, _stack_y, w, h)
                vp.raise_()
                _stack_y += h + 6
            if hasattr(self, "vehicle_detail_panel") and self.vehicle_detail_panel.isVisible():
                _stack(self.vehicle_detail_panel)
            if hasattr(self, "deploy_locs_controls") and self.btn_prev_locs.isChecked():
                _stack(self.deploy_locs_controls)
            if hasattr(self, "hazard_controls") and self.btn_hazards.isChecked():
                _stack(self.hazard_controls)
            if hasattr(self, "satellite_controls") and self.btn_satellite.isChecked():
                _stack(self.satellite_controls)
            if hasattr(self, "mesoanalysis_controls") and self.btn_mesoanalysis.isChecked():
                _stack(self.mesoanalysis_controls)
            if hasattr(self, "sfcoa_controls") and self.btn_sfcoa.isChecked():
                _stack(self.sfcoa_controls)
            if hasattr(self, "noxp_controls") and self.btn_noxp.isChecked():
                _stack(self.noxp_controls)
            if hasattr(self, "raw_lidar_controls") and self.btn_raw_lidar.isChecked():
                _stack(self.raw_lidar_controls)
            if hasattr(self, "surface_controls") and self.btn_surface.isChecked():
                _stack(self.surface_controls)
            if hasattr(self, "sounding_controls") and self.btn_sounding.isChecked():
                _stack(self.sounding_controls)
            if hasattr(self, "annotation_tools") and self.btn_annotate.isChecked():
                _stack(self.annotation_tools)
            if hasattr(self, "map_controls") and self.btn_map.isChecked():
                _stack(self.map_controls)
            if hasattr(self, "landcover_controls") and self.btn_nlcd.isChecked():
                _stack(self.landcover_controls)
            if hasattr(self, "routing_controls") and self.btn_route.isChecked():
                _stack(self.routing_controls)

        # nav pill — upper-right, below toolbar (hidden while toolbar drawer is open)
        _np_w = min(340, r.width() - 2 * MARGIN)
        _nav_y = MARGIN + (tb_h + 4 if hasattr(self, "_floating_toolbar") else 0)
        _np_h = 0
        if hasattr(self, "nav_pill") and self.nav_pill.isVisible():
            self.nav_pill.setFixedWidth(_np_w)
            self.nav_pill.layout().activate()
            _np_h = self.nav_pill.sizeHint().height()
            self.nav_pill.setGeometry(r.width() - _np_w - MARGIN, _nav_y, _np_w, _np_h)
            self.nav_pill.raise_()

        # routing panel — floats below the nav pill when opened via ▾ button
        if (hasattr(self, "routing_controls") and self._pill_route_expanded
                and not self.btn_route.isChecked()):
            rc = self.routing_controls
            rc_y = _nav_y + _np_h + 4
            rc.adjustSize()
            rc.setGeometry(r.width() - _np_w - MARGIN, rc_y, _np_w, rc.height())
            rc.raise_()

        # outlook panel — right side, below toolbar, above status pill
        if hasattr(self, "outlook_panel"):
            op = self.outlook_panel
            top = _drop_y if hasattr(self, "_floating_toolbar") else MARGIN
            bottom_pad = 40  # clear status pills
            panel_h = max(100, r.height() - top - MARGIN - bottom_pad)
            op.setGeometry(r.width() - OutlookPanel.PANEL_WIDTH - MARGIN, top,
                           OutlookPanel.PANEL_WIDTH, panel_h)
            op.raise_()

        # archive controls bar — centered, pinned to bottom
        arc_bar_h = 0
        archive_rect = None
        if hasattr(self, "_archive_controls"):
            ac = self._archive_controls
            ac_w = min(r.width() - 2 * MARGIN, 680)
            ac.setFixedWidth(ac_w)
            ac.adjustSize()
            ac_h = ac.sizeHint().height()
            arc_bar_h = ac_h + MARGIN
            ac_x = max(MARGIN, (r.width() - ac_w) // 2)
            ac.setGeometry(ac_x, r.height() - arc_bar_h, ac_w, ac_h)
            archive_rect = (ac_x, r.height() - arc_bar_h, ac_w, ac_h)
            ac.raise_()

        # debug pill — bottom-center, sits above archive controls (or above bottom margin)
        if hasattr(self, "_debug_pill"):
            dp = self._debug_pill
            dp_w = dp.width()
            dp_h = dp.height()
            dp_x = max(MARGIN, (r.width() - dp_w) // 2)
            dp_bottom = r.height() - arc_bar_h - MARGIN if arc_bar_h else r.height() - MARGIN
            dp.move(dp_x, dp_bottom - dp_h)
            dp.raise_()

        # Left status pill stays bottom-left when there is room.  On narrower
        # archive layouts it moves above the playback bar instead of covering it.
        if hasattr(self, "_status_left"):
            self._status_left.adjustSize()
            sl = self._status_left.size()
            _status_y = bottom_left_y_avoiding(
                r.height(),
                MARGIN,
                sl.width(),
                sl.height(),
                archive_rect,
            )
            self._status_left.setGeometry(
                MARGIN, _status_y,
                sl.width(), sl.height()
            )
            self._status_left.raise_()

        # layer order pill — sits directly above the status pill
        if hasattr(self, "_layer_pill"):
            lp_w = self._layer_pill.width()
            lp_h = self._layer_pill.height()
            # anchor bottom of pill to just above the status pill
            lp_bottom = _status_y - 4
            lp_y = lp_bottom - lp_h
            self._layer_pill.move(MARGIN, lp_y)
            self._layer_pill._relayout()
            self._layer_pill.raise_()

        # re-center button — bottom-right, above MapLibre zoom controls (~70px tall)
        _ZOOM_CTRL_H = 70   # approximate height of MapLibre NavigationControl
        _GAP = 6
        _recenter_top = r.height() - _ZOOM_CTRL_H - _GAP
        if hasattr(self, "btn_recenter"):
            btn_w = self.btn_recenter.width()
            btn_h = self.btn_recenter.height()
            self.btn_recenter.move(
                r.width() - btn_w - MARGIN,
                _recenter_top - btn_h,
            )
            self.btn_recenter.raise_()
            if self.btn_recenter.isVisible():
                _recenter_top = _recenter_top - btn_h - _GAP

        # screenshot button — stacks above re-center button (or above zoom ctrls)
        if hasattr(self, "btn_screenshot"):
            sb_w = self.btn_screenshot.width()
            sb_h = self.btn_screenshot.height()
            self.btn_screenshot.move(
                r.width() - sb_w - MARGIN,
                _recenter_top - sb_h,
            )
            self.btn_screenshot.raise_()
            if self.btn_screenshot.isVisible():
                _recenter_top = _recenter_top - sb_h - _GAP

        # scan button — vehicle mode only, near the other bottom map controls
        if hasattr(self, "btn_scan"):
            sc_w = self.btn_scan.width()
            sc_h = self.btn_scan.height()
            self.btn_scan.move(
                r.width() - sc_w - MARGIN,
                _recenter_top - sc_h,
            )
            self.btn_scan.raise_()


    def _start_layout_pulse(self):
        """Re-layout at ~60 fps for 220 ms to track drawer open/close animations."""
        if not hasattr(self, "_pulse_timer"):
            self._pulse_timer = QTimer()
            self._pulse_timer.setInterval(16)
            self._pulse_timer.timeout.connect(self._layout_overlays)
        self._pulse_timer.start()
        QTimer.singleShot(220, self._pulse_timer.stop)


    def update_coordinates(self, lat: float, lon: float):
        self.coord_label.setText(f"LAT: {lat:>9.4f}  LON: {lon:>10.4f}")

    def _on_map_moved(self, lat: float, lon: float, _zoom: float):
        self.update_coordinates(lat, lon)

    def update_vehicle_count(self, count: int):
        self.vehicle_count_label.setText(f"VEHICLES: {count}")
        if hasattr(self, "_vehicle_count_badge"):
            self._vehicle_count_badge.setText(str(count))

    def _start_net_check(self):
        """Start periodic internet connectivity check (TCP to 1.1.1.1:53 every 30s)."""
        self._net_check_timer = QTimer(self)
        self._net_check_timer.timeout.connect(self._run_net_check)
        self._net_check_timer.start(30_000)
        self._run_net_check()  # immediate first check

    def _run_net_check(self):
        checker = _NetChecker()
        checker.result_ready.connect(self._on_net_result)
        threading.Thread(target=checker.check, daemon=True).start()

    def _on_net_result(self, state: str):
        if state == "ok":
            self.net_indicator.setText("● NET OK")
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        elif state == "slow":
            self.net_indicator.setText("● NET SLOW")
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #FFD166;"
            )
        else:
            self.net_indicator.setText("● NO INTERNET")
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )
        self.net_indicator.setVisible(True)
        self._layout_overlays()

    def _update_gps_indicator(self):
        """Refresh GPS fix status label based on age of last local vehicle observation."""
        age = time.monotonic() - self._last_local_obs_ts
        if self._last_local_obs_ts == 0.0 or age > 30:
            self.gps_indicator.setText("● NO GPS FIX")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )
        elif age > 5:
            self.gps_indicator.setText("● GPS STALE")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #FFD166;"
            )
        else:
            self.gps_indicator.setText("● GPS OK")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        self._layout_overlays()


    def _start_update_check(self):
        """Check for updates at startup and then every 30 minutes."""
        self._update_worker = UpdateWorker()
        self._update_worker.check_done.connect(self._on_update_check_done)
        self._update_worker.pull_done.connect(self._on_update_pull_done)
        self._update_worker.start_check()

        self._update_check_timer = QTimer(self)
        self._update_check_timer.timeout.connect(self._update_worker.start_check)
        self._update_check_timer.start(10 * 60 * 1000)   # 10 min

    def _on_update_check_done(self, commits_behind: int):
        if commits_behind > 0:
            self.update_indicator.setText("↑ UPDATE AVAILABLE")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #00CFFF; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(True)
        elif commits_behind == -2:
            self.update_indicator.setText("DEV BUILD")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; "
                "color: #3A3B4A; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(False)
        else:
            # -1 (error) or 0 (current) — stay silent
            self.update_indicator.setVisible(False)
            self._layout_overlays()
            return
        self.update_indicator.setVisible(True)
        self._layout_overlays()

    def _on_update_indicator_clicked(self):
        self.update_indicator.setEnabled(False)
        self.update_indicator.setText("↑ UPDATING...")
        self.update_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            "color: #5A5B6A; background: transparent; border: none; padding: 0;"
        )
        self._update_worker.start_pull()

    def _on_update_pull_done(self, success: bool, deps_changed: bool):
        import os, sys
        if success and deps_changed:
            # can't auto-restart safely if conda env changed — tell the user
            _cmd = "conda env update -f envs/storm.yml --prune"
            self.update_indicator.setText(f"↑ DEPS CHANGED — RUN: {_cmd}  THEN RESTART")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #FFB800; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(False)
            self.update_indicator.setVisible(True)
            self._layout_overlays()
        elif success:
            self.update_indicator.setText("↑ RESTARTING...")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #39D98A; background: transparent; border: none; padding: 0;"
            )
            self._layout_overlays()
            QTimer.singleShot(600, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))
        else:
            self.update_indicator.setText("↑ UPDATE FAILED — RETRY")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #E53935; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(True)
            self.update_indicator.setVisible(True)
            self._layout_overlays()

    def set_connection_status(self, connected: bool):
        if connected:
            self.conn_indicator.setText("● AWS OK")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        else:
            self.conn_indicator.setText("● AWS OFFLINE")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )


    def _init_vehicle_panel(self):
        self.vehicle_panel = QWidget(self._map_container)
        self.vehicle_panel.setObjectName("vehiclePill")
        self.vehicle_panel.setMinimumWidth(VEHICLE_PANEL_WIDTH)
        self.vehicle_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        # session-scoped size watermark — panel never shrinks while open;
        # resets to (0, 0) when the toolbar toggle hides it.
        self._vehicle_panel_size_watermark = (0, 0)
        layout = QVBoxLayout(self.vehicle_panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # header row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        header = QLabel("VEHICLES")
        header.setObjectName("vehiclePillTitle")
        header_row.addWidget(header)

        self._vehicle_count_badge = QLabel("0")
        self._vehicle_count_badge.setObjectName("vehiclePillCount")
        header_row.addWidget(self._vehicle_count_badge)

        header_row.addStretch()

        self._chk_station_plots = QCheckBox("station plots")
        self._chk_station_plots.setChecked(True)
        self._chk_station_plots.setObjectName("vehiclePillToggle")
        header_row.addWidget(self._chk_station_plots)

        layout.addLayout(header_row)

        # placeholder until vehicle list is populated
        placeholder = QLabel("AWAITING VEHICLES...")
        placeholder.setObjectName("vehiclePillEmpty")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #3A3B5A; font-size: 10px; font-weight: 500; letter-spacing: 0.5px; padding: 6px 0;")
        self._vehicle_placeholder = placeholder
        layout.addWidget(placeholder)

        self._vehicle_rows_widget = QWidget()
        self._vehicle_rows_widget.setObjectName("vehicleRowsContainer")
        # icon groups are columns laid out horizontally
        self._vehicle_rows_layout = QHBoxLayout(self._vehicle_rows_widget)
        self._vehicle_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._vehicle_rows_layout.setSpacing(14)
        self._vehicle_rows_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._vehicle_rows_widget.setVisible(False)
        layout.addWidget(self._vehicle_rows_widget)

        # no stretch so the pill shrink-wraps to content

        # start hidden — opened via toolbar toggle
        self.vehicle_panel.hide()
        self.btn_vehicles.toggled.connect(self.vehicle_panel.setVisible)
        self.btn_vehicles.toggled.connect(self._on_vehicle_panel_toggled)
        self.btn_vehicles.toggled.connect(self._start_layout_pulse)
        self.btn_prev_locs.toggled.connect(self.map_widget.set_deploy_locs_visible)
        self.btn_prev_locs.toggled.connect(self._apply_deploy_locs_filter_on_show)
        self.deploy_locs_controls.metric_changed.connect(self.map_widget.set_deploy_locs_metric)
        self.deploy_locs_controls.filter_changed.connect(self.map_widget.set_deploy_locs_filter)
        self.deploy_locs_controls.size_changed.connect(self.map_widget.set_deploy_locs_size)

        # detail pill (hidden until a vehicle is selected)
        self._selected_vehicle_ids = []
        self._vehicle_age_display_state: dict[str, tuple[str, str]] = {}
        self._vehicle_row_age_labels: dict[str, QLabel] = {}
        self.vehicle_detail_panel = QWidget(self._map_container)
        self.vehicle_detail_panel.setObjectName("vehicleDetailPill")
        detail_layout = QVBoxLayout(self.vehicle_detail_panel)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setSpacing(6)

        self._vehicle_detail_title = QLabel("VEHICLE")
        self._vehicle_detail_title.setObjectName("vehicleDetailTitle")
        detail_layout.addWidget(self._vehicle_detail_title)

        self._vehicle_detail_body_widget = QWidget()
        self._vehicle_detail_body_layout = QVBoxLayout(self._vehicle_detail_body_widget)
        self._vehicle_detail_body_layout.setContentsMargins(0, 0, 0, 0)
        self._vehicle_detail_body_layout.setSpacing(0)
        detail_layout.addWidget(self._vehicle_detail_body_widget)

        self.vehicle_detail_panel.hide()
        self.btn_vehicles.toggled.connect(self._sync_vehicle_detail_visibility)


    def _init_radar(self):
        self._radar_overlay = RadarOverlay(self.map_widget)
        self._radar_fetcher = RadarFetcher()
        self._scan_cache: dict[str, list] = {}   # key: "site/product" → list of RadarScan

        # background thread pool for NEXRAD decode+render — keeps heavy MetPy/numpy/scipy
        from concurrent.futures import ThreadPoolExecutor
        self._render_scan_to_png = _render_scan_to_png
        self._decode_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="radar-decode"
        )
        self._render_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="radar-render"
        )
        # incremented on site change so in-flight decodes/renders for old site are discarded
        self._render_generation = 0

        # wire decoded-scan and render-ready signals (emitted from bg threads → main thread)
        self._scan_decoded.connect(self._on_scan_decoded)
        self._render_ready.connect(self._on_render_ready)
        self._radar_decode_failed.connect(
            lambda site, prod: self.status_msg_label.setText(
                f"Radar decode failed: {site}/{prod}"
            )
        )

        # 500 ms between loop frames — fast enough to feel animated, slow enough to read
        self._loop_timer = QTimer()
        self._loop_timer.setInterval(500)
        self._loop_timer.timeout.connect(self._advance_loop_frame)

        # pending render scan — latest scan waiting to be rendered.
        self._pending_render_scan = None

        # guard: only one background render in flight at a time.
        self._render_in_flight = False

        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer = QTimer()
        self._inject_throttle_timer.setSingleShot(True)
        self._inject_throttle_timer.timeout.connect(self._flush_deferred_inject)

        self.radar_controls.radar_toggled.connect(self._on_radar_toggled)
        self.radar_controls.site_changed.connect(self._on_radar_site_changed)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)
        self.radar_controls.product_changed.connect(self._on_radar_product_changed)
        self.radar_controls.products_available_changed.connect(
            self._on_radar_products_available
        )
        self.radar_controls.product_availability_changed.connect(
            self._on_radar_product_availability_changed
        )
        self._radar_product_availability: dict[str, bool] = {
            "N0B": True, "N0U": True, "N0C": True, "N0K": True
        }
        self.radar_controls.fetch_requested.connect(self._on_radar_fetch_requested)
        self.radar_controls.frame_requested.connect(self._display_cached_frame)
        self.radar_controls.loop_toggled.connect(self._on_loop_toggled)
        self.radar_controls.speed_changed.connect(self._on_radar_speed_changed)
        self.radar_controls.vad_requested.connect(self._on_vad_requested)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)

        self._radar_fetcher.new_data.connect(self._on_radar_data)
        self._radar_fetcher.fetch_error.connect(self._on_radar_error)
        self._radar_error_clear_timer = QTimer()
        self._radar_error_clear_timer.setSingleShot(True)
        self._radar_error_clear_timer.timeout.connect(self._clear_radar_error)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.radar_controls.set_selected_site("KTLX")

        initial_site = self.radar_controls.current_site()
        self._radar_fetcher.set_site(initial_site)
        self._on_radar_products_available(["N0B", "N0U", "N0C", "N0K"])
        
        # track initial backfill completion
        self._radar_initial_backfill_complete = False
        self._radar_backfill_products_received = set()

        self._sounding_fetcher        = SoundingFetcher(self)
        self._obs_sounding_fetcher    = ObsSoundingFetcher(self)
        self._clamps_sounding_fetcher = ClampsSoundingFetcher(self)
        self._sounding_dialog         = SoundingDialog(self)
        self._nssl_sounding_refresh_timer = QTimer(self)
        self._nssl_sounding_refresh_timer.setInterval(5 * 60 * 1000)
        self._nssl_sounding_refresh_timer.timeout.connect(
            self._refresh_nssl_sounding_if_visible
        )

        # dedicated comparison fetchers — their sounding_ready goes to add_source
        self._comp_hrrr_fetcher = SoundingFetcher(self)
        self._comp_obs_fetcher  = ObsSoundingFetcher(self)
        self._comp_nssl_fetcher = ClampsSoundingFetcher(self)

        self._last_nssl_sset = None   # cached for NSSL proximity checks

        self._sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._obs_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._obs_sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._clamps_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._clamps_sounding_fetcher.fetch_error.connect(self._on_sounding_error)

        self._comp_hrrr_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_hrrr_fetcher.fetch_error.connect(self._on_sounding_error)
        self._comp_obs_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_obs_fetcher.fetch_error.connect(self._on_sounding_error)
        self._comp_nssl_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_nssl_fetcher.fetch_error.connect(self._on_sounding_error)

        self._sounding_dialog.source_requested.connect(self._on_comparison_source_requested)

        self.map_widget.sounding_clicked.connect(self._on_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_obs_station_click)
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        # pre-build stations GeoJSON once (static asset, ~15 KB)
        self._sounding_stations_geojson = build_stations_geojson()

    def _init_hazards(self):
        self._hazard_fetcher = HazardFetcher(parent=self)
        self.hazard_controls.spc_day_changed.connect(self._on_spc_day_changed)
        self.hazard_controls.spc_mode_changed.connect(self._on_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_spc_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_spc_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_nws_warnings_toggled)
        self.hazard_controls.nws_filter_changed.connect(self._on_nws_filter_changed)
        self.hazard_controls.cwa_toggled.connect(self._on_cwa_toggled)
        self.hazard_controls.fetch_requested.connect(self._on_hazard_fetch_requested)

        self._hazard_fetcher.spc_received.connect(self._on_spc_received)
        self._hazard_fetcher.nws_received.connect(self._on_nws_received)
        self._hazard_fetcher.nws_raw_phenoms_received.connect(self._on_nws_raw_phenoms)
        self._hazard_fetcher.spc_watches_received.connect(self._on_spc_watches_received)
        self._hazard_fetcher.spc_mds_received.connect(self._on_spc_mds_received)
        self._hazard_fetcher.fetch_error.connect(self._on_hazard_error)
        self._hazard_fetcher.connectivity_changed.connect(self._on_hazard_connectivity)

        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self._hazard_error_clear_timer = QTimer()
        self._hazard_error_clear_timer.setSingleShot(True)
        self._hazard_error_clear_timer.timeout.connect(self._clear_hazard_error)

        # seed NWS bbox from MBTiles domain extent so warnings are filtered
        try:
            import sqlite3 as _sqlite3
            from ui.map.widget import TILES_PATH as _TILES_PATH
            _conn = _sqlite3.connect(_TILES_PATH)
            _row = _conn.execute(
                "SELECT value FROM metadata WHERE name='bounds'"
            ).fetchone()
            _conn.close()
            if _row:
                _lon_min, _lat_min, _lon_max, _lat_max = (
                    float(x) for x in _row[0].split(",")
                )
                self._hazard_fetcher.set_nws_bbox(
                    _lon_min, _lat_min, _lon_max, _lat_max
                )
        except Exception:
            pass

        # load persisted NWS filter selections from QSettings and apply.
        try:
            s = QSettings("NSSL", "STORM")
            stored = s.value("nws/filters", None, type=str)
            # stored is comma-separated string or None
            if stored:
                codes = {c.strip().upper() for c in stored.split(",") if c.strip()}
            else:
                codes = None
            # programmatically set controls and emit handler so fetcher/font updates.
            self.hazard_controls.set_nws_filter_selected(codes, emit=True)
        except Exception:
            pass

        self._hazard_fetcher.start()

        # keep top drawers mutually exclusive for clean placement
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_radar.setChecked(False) if on else None
        )
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_radar.toggled.connect(
            lambda on: self.btn_hazards.setChecked(False) if on else None
        )

    def _init_satellite(self):
        self._satellite_fetcher  = SatelliteFetcher(parent=self)
        self._satellite_cache: dict[str, list] = {"conus": [], "meso1": [], "meso2": []}
        self._satellite_loop_timer = QTimer(self)
        self._satellite_loop_timer.setInterval(600)   # ms per frame during loop
        self._satellite_loop_timer.timeout.connect(self._satellite_loop_tick)

        # control signals → handlers
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.frame_requested.connect(self._on_satellite_frame_requested)
        self.satellite_controls.loop_toggled.connect(self._on_satellite_loop_toggled)
        self.satellite_controls.speed_changed.connect(self._on_satellite_speed_changed)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        # fetcher signals → handlers
        self._satellite_fetcher.meso_sectors_updated.connect(self._on_meso_sectors_updated)
        self._satellite_fetcher.frames_updated.connect(self._on_satellite_frames_updated)
        self._satellite_fetcher.fetch_error.connect(self._on_satellite_error)

        self._satellite_error_clear_timer = QTimer(self)
        self._satellite_error_clear_timer.setSingleShot(True)
        self._satellite_error_clear_timer.timeout.connect(self._clear_satellite_error)

        self._satellite_fetcher.start()

        # drawer mutually exclusive with radar/hazards/annotate
        for btn, other in [
            (self.btn_satellite, self.btn_radar),
            (self.btn_satellite, self.btn_hazards),
            (self.btn_satellite, self.btn_surface),
            (self.btn_satellite, self.btn_annotate),
            (self.btn_radar,     self.btn_satellite),
            (self.btn_radar,     self.btn_surface),
            (self.btn_hazards,   self.btn_satellite),
            (self.btn_hazards,   self.btn_surface),
            (self.btn_surface,   self.btn_satellite),
            (self.btn_surface,   self.btn_radar),
            (self.btn_surface,   self.btn_hazards),
            (self.btn_surface,   self.btn_annotate),
            (self.btn_annotate,  self.btn_satellite),
            (self.btn_annotate,  self.btn_surface),
        ]:
            btn.toggled.connect(
                lambda on, o=other: o.setChecked(False) if on else None
            )

    def _init_mesoanalysis(self):
        if self._mesoanalysis_fetcher is not None:
            return
        self._mesoanalysis_fetcher = MesoanalysisFetcher(parent=self)

        self.mesoanalysis_controls.product_toggled.connect(self._on_mesoanalysis_product_toggled)
        self.mesoanalysis_controls.frame_requested.connect(self._on_mesoanalysis_frame_requested)
        self.mesoanalysis_controls.refresh_requested.connect(self._on_mesoanalysis_refresh_requested)
        self.mesoanalysis_controls.visible_toggled.connect(self._on_mesoanalysis_visible_toggled)

        self._mesoanalysis_fetcher.products_ready.connect(self._on_mesoanalysis_products_ready)
        self._mesoanalysis_fetcher.times_ready.connect(self._on_mesoanalysis_times_ready)
        self._mesoanalysis_fetcher.overlay_ready.connect(self._on_mesoanalysis_overlay_ready)
        self._mesoanalysis_fetcher.fetch_error.connect(self._on_mesoanalysis_error)

        for btn, other in [
            (self.btn_mesoanalysis, self.btn_satellite),
            (self.btn_mesoanalysis, self.btn_radar),
            (self.btn_mesoanalysis, self.btn_hazards),
            (self.btn_mesoanalysis, self.btn_surface),
            (self.btn_mesoanalysis, self.btn_annotate),
            (self.btn_satellite,    self.btn_mesoanalysis),
            (self.btn_radar,        self.btn_mesoanalysis),
            (self.btn_hazards,      self.btn_mesoanalysis),
            (self.btn_surface,      self.btn_mesoanalysis),
            (self.btn_annotate,     self.btn_mesoanalysis),
        ]:
            btn.toggled.connect(
                lambda on, o=other: o.setChecked(False) if on else None
            )

        self._mesoanalysis_fetcher.refresh_products()
        self.mesoanalysis_controls.set_status("Meso: products")

    def _init_sfcoa(self):
        if self._sfcoa_fetcher is not None:
            return
        self._sfcoa_fetcher = SfcoaOverlayFetcher(config.SFCOA_BASE_URL, parent=self)

        self.sfcoa_controls.product_toggled.connect(self._on_sfcoa_product_toggled)
        self.sfcoa_controls.frame_requested.connect(self._on_sfcoa_frame_requested)
        self.sfcoa_controls.refresh_requested.connect(self._on_sfcoa_refresh_requested)
        self.sfcoa_controls.visible_toggled.connect(self._on_sfcoa_visible_toggled)

        self._sfcoa_fetcher.times_ready.connect(self._on_sfcoa_times_ready)
        self._sfcoa_fetcher.variables_ready.connect(self._on_sfcoa_variables_ready)
        self._sfcoa_fetcher.overlay_ready.connect(self._on_sfcoa_overlay_ready)
        self._sfcoa_fetcher.fetch_error.connect(self._on_sfcoa_error)

        pairs = [
            (self.btn_sfcoa, self.btn_satellite),
            (self.btn_sfcoa, self.btn_radar),
            (self.btn_sfcoa, self.btn_hazards),
            (self.btn_sfcoa, self.btn_surface),
            (self.btn_sfcoa, self.btn_annotate),
            (self.btn_satellite, self.btn_sfcoa),
            (self.btn_radar, self.btn_sfcoa),
            (self.btn_hazards, self.btn_sfcoa),
            (self.btn_surface, self.btn_sfcoa),
            (self.btn_annotate, self.btn_sfcoa),
        ]
        if hasattr(self, "btn_mesoanalysis"):
            pairs.extend([
                (self.btn_sfcoa, self.btn_mesoanalysis),
                (self.btn_mesoanalysis, self.btn_sfcoa),
            ])
        for btn, other in pairs:
            btn.toggled.connect(
                lambda on, o=other: o.setChecked(False) if on else None
            )

        self._sfcoa_fetcher.refresh_catalog()
        self.sfcoa_controls.set_status("SFCOA: times")

    def _init_surface_obs(self):
        self._surface_fetcher = SurfaceFetcher(parent=self)
        self._surface_layer = SurfacePlotLayer(self.map_widget)
        self._surface_station_ids: set[str] = set()
        self._surface_render_generation = 0

        self.surface_controls.ok_toggled.connect(
            lambda v: self._on_surface_source_toggled("ok", v)
        )
        self.surface_controls.wtm_toggled.connect(
            lambda v: self._on_surface_source_toggled("wtm", v)
        )
        self.surface_controls.ks_toggled.connect(
            lambda v: self._on_surface_source_toggled("ks", v)
        )
        self.surface_controls.co_toggled.connect(
            lambda v: self._on_surface_source_toggled("co", v)
        )
        self.surface_controls.ne_toggled.connect(
            lambda v: self._on_surface_source_toggled("ne", v)
        )
        self.surface_controls.sd_toggled.connect(
            lambda v: self._on_surface_source_toggled("sd", v)
        )
        self.surface_controls.asos_toggled.connect(self._on_asos_toggled)
        self.surface_controls.asos_bbox_requested.connect(self._start_new_asos_bbox)
        self.surface_controls.plots_toggled.connect(self._surface_layer.set_visible)

        self.map_widget.asos_bbox_selected.connect(self._on_asos_bbox_selected)
        self._surface_fetcher.observations_updated.connect(self._on_surface_observations_updated)
        self._surface_fetcher.status_updated.connect(self.surface_controls.set_status)
        self._surface_fetcher.diagnostics_updated.connect(self.surface_controls.set_diagnostics)
        self._surface_fetcher.status_updated.connect(self.status_msg_label.setText)
        self._surface_fetcher.error.connect(self._on_surface_error)

        self._surface_fetcher.start()
        QTimer.singleShot(1200, lambda: self._surface_layer.set_visible(
            self.surface_controls.plots_visible()
        ))

    def _on_surface_observations_updated(self, items: list[dict]):
        items = [
            item for item in items
            if self._surface_source_enabled(item.get("source", ""))
        ]
        self._surface_render_generation += 1
        render_generation = self._surface_render_generation
        incoming: set[str] = {item["id"] for item in items}
        gone = self._surface_station_ids - incoming
        self._surface_station_ids = incoming

        layer      = self._surface_layer
        map_widget = self.map_widget

        class RenderSignals(QObject):
            ready = pyqtSignal(list, set)   # (rendered_rows, ids_to_remove)
            status = pyqtSignal(str)

        signals = RenderSignals()

        def _push(rendered_data, ids_to_remove):
            if render_generation != self._surface_render_generation:
                return
            # remove stale stations in one JS call
            if ids_to_remove:
                for sid in ids_to_remove:
                    layer._cache.pop(sid, None)
                map_widget.remove_surface_station_plots_batch(ids_to_remove)

            if not rendered_data:
                return

            # store PNG bytes in scheme handler + send tiny metadata-only JS payload
            batch = []
            for sid, lat, lon, fp, png, name in rendered_data:
                layer._cache[sid] = (fp, png)
                batch.append((sid, lat, lon, png, name))
            map_widget.add_surface_station_plots_batch(batch)

        # critical: Ensure the signal is routed to the main thread
        signals.ready.connect(_push, Qt.ConnectionType.QueuedConnection)
        signals.status.connect(self.status_msg_label.setText, Qt.ConnectionType.QueuedConnection)

        def _render_worker():
            from ui.layers.station_plot_layer import _render
            from ui.layers.surface_plot_layer import _surface_obs_fingerprint
            import time

            _gone_to_emit = gone
            has_asos = any(item.get("source") == "asos" for item in items)

            # if there's nothing to render but stations to remove, clear them out
            if not items:
                if _gone_to_emit and render_generation == self._surface_render_generation:
                    signals.ready.emit([], _gone_to_emit)
                return

            # snapshot the cache once to avoid live dict mutation from the main thread
            cache_snapshot = dict(layer._cache)

            to_render = []
            for item in items:
                if render_generation != self._surface_render_generation:
                    return
                obs  = item["obs"]
                sid  = item["id"]
                name = item.get("name", sid)
                fp   = _surface_obs_fingerprint(obs)
                cached = cache_snapshot.get(sid)
                if cached and cached[0] == fp:
                    continue
                to_render.append((item, obs, sid, name, fp))

            if has_asos and to_render:
                signals.status.emit(f"ASOS: rendering {len(to_render)} station plots…")

            rendered_chunk = []
            chunk_size = min(24, max(1, len(to_render))) if has_asos else max(1, len(to_render))

            for i, (item, obs, sid, name, fp) in enumerate(to_render):
                if render_generation != self._surface_render_generation:
                    return
                try:
                    color = layer._obs_age_color(obs, sid)
                    png   = _render(obs, center_color=color)
                    rendered_chunk.append((sid, obs.lat, obs.lon, fp, png, name))
                except Exception as exc:
                    log.error("surface render failed for %s: %s", sid, exc)
                
                if len(rendered_chunk) >= chunk_size or i == len(to_render) - 1:
                    if render_generation != self._surface_render_generation:
                        return
                    signals.ready.emit(rendered_chunk, _gone_to_emit)
                    rendered_chunk = []
                    _gone_to_emit = set()  # Only emit the 'gone' list on the very first chunk
                    
                    if has_asos:
                        time.sleep(0.015)

            if has_asos and render_generation == self._surface_render_generation:
                signals.status.emit(f"ASOS: rendered {len(to_render)} station plots")

        threading.Thread(target=_render_worker, daemon=True).start()

    def _on_surface_source_toggled(self, source: str, enabled: bool) -> None:
        if source == "ok":
            self._surface_fetcher.set_ok_enabled(enabled)
            self._set_layer_active("ok_mesonet", enabled)
        elif source == "wtm":
            self._surface_fetcher.set_wtm_enabled(enabled)
            self._set_layer_active("wtm", enabled)
        elif source == "ks":
            self._surface_fetcher.set_ks_enabled(enabled)
            self._set_layer_active("ks_mesonet", enabled)
        elif source == "co":
            self._surface_fetcher.set_co_enabled(enabled)
            self._set_layer_active("co_mesonet", enabled)
        elif source == "ne":
            self._surface_fetcher.set_ne_enabled(enabled)
            self._set_layer_active("ne_mesonet", enabled)
        elif source == "sd":
            self._surface_fetcher.set_sd_enabled(enabled)
            self._set_layer_active("sd_mesonet", enabled)
        else:
            return

        if not enabled:
            self._remove_surface_source_plots(source)

    def _surface_source_enabled(self, source: str) -> bool:
        if source == "ok":
            return bool(getattr(self._surface_fetcher, "_ok_enabled", False))
        if source == "wtm":
            return bool(getattr(self._surface_fetcher, "_wtm_enabled", False))
        if source == "ks":
            return bool(getattr(self._surface_fetcher, "_ks_enabled", False))
        if source == "co":
            return bool(getattr(self._surface_fetcher, "_co_enabled", False))
        if source == "ne":
            return bool(getattr(self._surface_fetcher, "_ne_enabled", False))
        if source == "sd":
            return bool(getattr(self._surface_fetcher, "_sd_enabled", False))
        if source == "asos":
            return bool(
                getattr(self._surface_fetcher, "_asos_enabled", False)
                and getattr(self._surface_fetcher, "_asos_bbox", None) is not None
            )
        return False

    def _remove_surface_source_plots(self, source: str) -> None:
        self._surface_render_generation += 1
        prefix = f"surface:{source}:"
        ids = [
            sid for sid in self._surface_station_ids
            if sid.startswith(prefix)
        ]
        if not ids:
            return

        self._surface_station_ids = {
            sid for sid in self._surface_station_ids
            if not sid.startswith(prefix)
        }
        for sid in ids:
            self._surface_layer._cache.pop(sid, None)
        self.map_widget.remove_surface_station_plots_batch(ids)

    def _on_surface_error(self, msg: str):
        self.status_msg_label.setText(f"Surface: {msg}")
        if str(msg).startswith("ASOS:"):
            self.map_widget.set_asos_bbox_mode(False)
            self.map_widget.run_js(
                "if(window.stormRestoreAsosMapInteractions) stormRestoreAsosMapInteractions();"
            )
        self._layout_overlays()

    def _on_satellite_toggled(self, checked: bool):
        self._set_layer_active("satellite", checked)
        if self._archive:
            # archive mode: simply show/hide the overlay the archive fetcher is updating.
            has_data = getattr(self, "_archive_sat_has_data", False)
            self.map_widget.set_satellite_visible(checked and has_data)
            return
        if not checked:
            # closing the drawer stops playback but leaves the overlay visible.
            self._satellite_loop_timer.stop()
            self.satellite_controls.stop_loop()
        else:
            mode = self.satellite_controls.current_mode()
            if not mode:
                return
            self.map_widget.set_satellite_mode(mode)
            frames = self._satellite_cache.get(mode, [])
            if frames:
                self._render_satellite_frame(frames[-1])
                self.map_widget.set_satellite_visible(True)

    def _on_nlcd_toggled(self, checked: bool):
        self._set_layer_active("nlcd", checked)
        if hasattr(self, "landcover_controls"):
            self.map_widget.set_nlcd_opacity(self.landcover_controls.opacity())
        self.map_widget.set_nlcd_visible(checked)

    def _on_nlcd_opacity_changed(self, opacity: float):
        self.map_widget.set_nlcd_opacity(opacity)

    def _on_satellite_basemap_toggled(self, checked: bool):
        self._set_layer_active("satellite_basemap", checked)
        self.map_widget.set_satellite_basemap_visible(checked)

    def _on_mesoanalysis_drawer_toggled(self, checked: bool):
        if checked and self._mesoanalysis_fetcher is None:
            self._init_mesoanalysis()
            return
        if checked and not self._mesoanalysis_current_metadata:
            self._mesoanalysis_fetcher.refresh_products()

    def _on_sfcoa_drawer_toggled(self, checked: bool):
        if checked and self._sfcoa_fetcher is None:
            self._init_sfcoa()
            return
        if checked and not self._sfcoa_times and self._sfcoa_fetcher:
            self._sfcoa_fetcher.refresh_catalog()
        if checked:
            self._sfcoa_visible = bool(self._sfcoa_active_products)
            self._set_layer_active("sfcoa", self._sfcoa_visible)
            if self._sfcoa_current_metadata and self._sfcoa_visible:
                self._display_sfcoa_metadata(self._sfcoa_current_metadata)
            self.map_widget.set_sfcoa_visible(self._sfcoa_visible)

    def _on_mesoanalysis_products_ready(self, products):
        self.mesoanalysis_controls.set_products(products)
        self.mesoanalysis_controls.set_status(f"Meso: {len(products)} products")

    def _on_mesoanalysis_product_toggled(self, product_id: str, enabled: bool):
        if not product_id or self._mesoanalysis_fetcher is None:
            return
        self.mesoanalysis_controls.stop_loop()
        if enabled:
            self._mesoanalysis_active_products.add(product_id)
            self._mesoanalysis_visible = True
            self._set_layer_active("mesoanalysis", True)
            self.map_widget.set_mesoanalysis_opacity(1.0)
            self.mesoanalysis_controls.set_status(f"Meso: loading {product_id}")
            self._mesoanalysis_fetcher.fetch_times(product_id)
        else:
            self._mesoanalysis_active_products.discard(product_id)
            self.map_widget.clear_mesoanalysis_overlay(product_id)
            if not self._mesoanalysis_active_products:
                self._mesoanalysis_visible = False
                self._set_layer_active("mesoanalysis", False)
                self.map_widget.set_mesoanalysis_visible(False)

    def _on_mesoanalysis_times_ready(self, product_id: str, times):
        if product_id not in self._mesoanalysis_active_products:
            return
        self._mesoanalysis_times_by_product[product_id] = list(times)
        if len(times) > len(self._mesoanalysis_times):
            self._mesoanalysis_times = list(times)
        self.mesoanalysis_controls.set_times(self._mesoanalysis_times or times)
        self.mesoanalysis_controls.set_status(f"Meso: {len(self._mesoanalysis_active_products)} products")
        self._fetch_mesoanalysis_product_frame(product_id, self.mesoanalysis_controls.current_frame())

    def _on_mesoanalysis_frame_requested(self, idx: int):
        self.mesoanalysis_controls.set_frame(idx)
        self._fetch_current_mesoanalysis_frame()

    def _fetch_current_mesoanalysis_frame(self):
        for product_id in sorted(self._mesoanalysis_active_products):
            self._fetch_mesoanalysis_product_frame(product_id, self.mesoanalysis_controls.current_frame())

    def _fetch_mesoanalysis_product_frame(self, product_id: str, idx: int):
        if not product_id or self._mesoanalysis_fetcher is None:
            return
        times = self._mesoanalysis_times_by_product.get(product_id) or self._mesoanalysis_times
        if not times:
            return
        idx = max(0, min(len(times) - 1, int(idx)))
        time_id = times[idx].time_id
        cached = self._mesoanalysis_overlay_cache.get((product_id, time_id))
        if cached is not None:
            self._on_mesoanalysis_overlay_ready(cached)
            return
        self.mesoanalysis_controls.set_status(f"Meso: loading {product_id} {time_id[-6:-4]}Z")
        self._mesoanalysis_fetcher.fetch_overlay(product_id, time_id)

    def _on_mesoanalysis_refresh_requested(self):
        if self._mesoanalysis_fetcher is None:
            return
        self.mesoanalysis_controls.set_status("Meso: refreshing")
        if self._mesoanalysis_active_products:
            for product_id in sorted(self._mesoanalysis_active_products):
                self._mesoanalysis_fetcher.fetch_times(product_id)
        else:
            self._mesoanalysis_fetcher.refresh_products()

    def _on_mesoanalysis_visible_toggled(self, visible: bool):
        self._mesoanalysis_visible = bool(visible)
        self._set_layer_active("mesoanalysis", self._mesoanalysis_visible)
        if self._mesoanalysis_visible:
            self.map_widget.set_mesoanalysis_opacity(1.0)
        self.map_widget.set_mesoanalysis_visible(
            self._mesoanalysis_visible and bool(self._mesoanalysis_active_products)
        )

    def _on_mesoanalysis_overlay_ready(self, metadata: dict):
        self._mesoanalysis_current_metadata = metadata
        product_id = str(metadata.get("product") or self.mesoanalysis_controls.current_product())
        time_id = str(metadata.get("time") or self.mesoanalysis_controls.current_time())
        if product_id and time_id:
            self._mesoanalysis_overlay_cache[(product_id, time_id)] = metadata
        self.mesoanalysis_controls.set_status(f"Meso: {len(self._mesoanalysis_active_products)} products")
        if self._mesoanalysis_visible:
            self._display_mesoanalysis_metadata(metadata)

    def _display_mesoanalysis_metadata(self, metadata: dict):
        bounds = metadata.get("bounds") or []
        if len(bounds) != 4:
            self._on_mesoanalysis_error("Mesoanalysis metadata missing bounds")
            return
        tile_url = metadata.get("tile_url") or ""
        source_layer = metadata.get("source_layer") or ""
        if not tile_url or not source_layer:
            self._on_mesoanalysis_error("Mesoanalysis metadata missing tile source")
            return
        west, south, east, north = self._mesoanalysis_display_bounds(
            [float(v) for v in bounds]
        )
        self.map_widget.set_mesoanalysis_overlay(
            str(metadata.get("product") or ""),
            tile_url,
            source_layer,
            west,
            south,
            east,
            north,
            int(metadata.get("minzoom") or 0),
            int(metadata.get("maxzoom") or 8),
            str(metadata.get("label_units") or ""),
        )
        self.map_widget.set_mesoanalysis_visible(True)

    def _mesoanalysis_display_bounds(self, source_bounds: list[float]) -> list[float]:
        mbtiles_bounds = self._load_mbtiles_bounds()
        if not mbtiles_bounds or len(source_bounds) != 4:
            return source_bounds
        west = max(source_bounds[0], mbtiles_bounds[0])
        south = max(source_bounds[1], mbtiles_bounds[1])
        east = min(source_bounds[2], mbtiles_bounds[2])
        north = min(source_bounds[3], mbtiles_bounds[3])
        if west >= east or south >= north:
            return source_bounds
        return [west, south, east, north]

    def _on_mesoanalysis_error(self, msg: str):
        self.mesoanalysis_controls.set_status(str(msg))
        self.status_msg_label.setText(str(msg))

    def _on_sfcoa_times_ready(self, times):
        self._sfcoa_times = list(times)
        self.sfcoa_controls.set_times(self._sfcoa_times)
        self.sfcoa_controls.set_status(f"SFCOA: {len(self._sfcoa_times)} times")
        if self._sfcoa_times and self._sfcoa_fetcher:
            self._sfcoa_fetcher.fetch_variables(self.sfcoa_controls.current_time())

    def _on_sfcoa_variables_ready(self, time_id: str, variables):
        self._sfcoa_variables_by_time[time_id] = list(variables)
        if time_id != self.sfcoa_controls.current_time():
            return
        self.sfcoa_controls.set_products(variables)
        self.sfcoa_controls.set_status(f"SFCOA {time_id[-2:]}Z: {len(variables)} vars")
        if self._sfcoa_active_products:
            self._fetch_current_sfcoa_frame()

    def _on_sfcoa_product_toggled(self, product_id: str, enabled: bool):
        if not product_id or self._sfcoa_fetcher is None:
            return
        time_id = self.sfcoa_controls.current_time()
        if enabled:
            self._sfcoa_active_products.add(product_id)
            self._sfcoa_visible = True
            self._set_layer_active("sfcoa", True)
            self.map_widget.set_sfcoa_opacity(1.0)
            if time_id:
                self.sfcoa_controls.set_status(f"SFCOA: loading {product_id}")
                self._fetch_sfcoa_product_time(product_id, time_id)
        else:
            self._sfcoa_active_products.discard(product_id)
            self.map_widget.clear_sfcoa_overlay(product_id)
            if not self._sfcoa_active_products:
                self._sfcoa_visible = False
                self._set_layer_active("sfcoa", False)
                self.map_widget.set_sfcoa_visible(False)

    def _on_sfcoa_frame_requested(self, idx: int):
        self.sfcoa_controls.set_frame(idx)
        time_id = self.sfcoa_controls.current_time()
        if not time_id:
            return
        if time_id not in self._sfcoa_variables_by_time and self._sfcoa_fetcher:
            self.sfcoa_controls.set_status(f"SFCOA: loading {time_id[-2:]}Z")
            self._sfcoa_fetcher.fetch_variables(time_id)
            return
        variables = self._sfcoa_variables_by_time.get(time_id, [])
        self.sfcoa_controls.set_products(variables)
        self._fetch_current_sfcoa_frame()

    def _fetch_current_sfcoa_frame(self):
        time_id = self.sfcoa_controls.current_time()
        if not time_id:
            return
        available = {
            getattr(var, "variable_id", "")
            for var in self._sfcoa_variables_by_time.get(time_id, [])
        }
        for product_id in sorted(self._sfcoa_active_products):
            if available and product_id not in available:
                continue
            self._fetch_sfcoa_product_time(product_id, time_id)

    def _fetch_sfcoa_product_time(self, product_id: str, time_id: str):
        cached = self._sfcoa_overlay_cache.get((product_id, time_id))
        if cached is not None:
            self._on_sfcoa_overlay_ready(cached)
            return
        self.sfcoa_controls.set_status(f"SFCOA: loading {product_id} {time_id[-2:]}Z")
        self._sfcoa_fetcher.fetch_overlay(product_id, time_id)

    def _on_sfcoa_refresh_requested(self):
        if self._sfcoa_fetcher is None:
            return
        self.sfcoa_controls.set_status("SFCOA: refreshing")
        self._sfcoa_variables_by_time.clear()
        self._sfcoa_fetcher.refresh_catalog()

    def _on_sfcoa_visible_toggled(self, visible: bool):
        self._sfcoa_visible = bool(visible)
        self._set_layer_active("sfcoa", self._sfcoa_visible)
        if self._sfcoa_visible:
            self.map_widget.set_sfcoa_opacity(1.0)
        self.map_widget.set_sfcoa_visible(
            self._sfcoa_visible and bool(self._sfcoa_active_products)
        )

    def _on_sfcoa_overlay_ready(self, metadata: dict):
        self._sfcoa_current_metadata = metadata
        product_id = str(metadata.get("product") or metadata.get("variable") or "")
        time_id = str(metadata.get("time") or self.sfcoa_controls.current_time())
        if product_id and time_id:
            self._sfcoa_overlay_cache[(product_id, time_id)] = metadata
        summary = self._sfcoa_status_summary(metadata)
        self._sfcoa_status_text = summary
        self.sfcoa_controls.set_status(summary.replace("SFCOA ", "", 1))
        self.status_msg_label.setText(summary)
        if self._sfcoa_visible:
            self._display_sfcoa_metadata(metadata)

    def _sfcoa_status_summary(self, metadata: dict) -> str:
        label = str(metadata.get("label") or metadata.get("product") or "SFCOA")
        valid = _overlay_time_label(metadata.get("valid_time"))
        if valid:
            return f"SFCOA {label} valid {valid}"
        time_id = str(metadata.get("time") or "")
        if time_id:
            return f"SFCOA {label} {time_id[-2:]}Z"
        return f"SFCOA {label}"

    def _display_sfcoa_metadata(self, metadata: dict):
        bounds = metadata.get("bounds") or metadata.get("bbox") or []
        if len(bounds) != 4:
            self._on_sfcoa_error("SFCOA metadata missing bounds")
            return
        tile_url = str(metadata.get("tile_url") or "")
        source_layer = str(metadata.get("source_layer") or "")
        if not tile_url or not source_layer:
            self._on_sfcoa_error("SFCOA metadata missing tile source")
            return
        tile_key = str(metadata.get("tile_key") or "")
        mbtiles_path = str(metadata.get("mbtiles_path") or "")
        if tile_key and mbtiles_path and hasattr(self.map_widget, "register_sfcoa_mbtiles"):
            self.map_widget.register_sfcoa_mbtiles(tile_key, mbtiles_path)
        west, south, east, north = self._mesoanalysis_display_bounds(
            [float(v) for v in bounds]
        )
        self.map_widget.set_sfcoa_overlay(
            str(metadata.get("product") or metadata.get("variable") or ""),
            tile_url,
            source_layer,
            west,
            south,
            east,
            north,
            int(metadata.get("minzoom") or 0),
            int(metadata.get("maxzoom") or 8),
            str(metadata.get("label_units") or metadata.get("units") or ""),
        )
        self.map_widget.set_sfcoa_visible(True)

    def _on_sfcoa_error(self, msg: str):
        text = f"SFCOA: {msg}"
        self.sfcoa_controls.set_status(text)
        self.status_msg_label.setText(text)

    def _on_satellite_mode_changed(self, mode: str):
        if self._archive:
            self.satellite_controls.stop_loop()
            self.satellite_controls.reset_cache_ui()
            self._archive_sat_has_data = False

            if not mode:
                self.map_widget.clear_satellite_frame()
                self.map_widget.set_satellite_visible(False)
                return

            self.map_widget.set_satellite_mode(mode)
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            self._archive_satellite.set_mode(mode)
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()
            return

        self._satellite_loop_timer.stop()
        self.satellite_controls.stop_loop()
        self.satellite_controls.reset_cache_ui()

        if not mode:
            self.map_widget.set_satellite_visible(False)
            return

        self.map_widget.set_satellite_mode(mode)
        frames = self._satellite_cache.get(mode, [])
        if frames:
            self.satellite_controls.set_cache_size(len(frames))
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str)
            self.map_widget.set_satellite_visible(True)
            self.status_msg_label.setText(f"GOES {mode.upper()} {frames[-1].time_str}")
            self._layout_overlays()
        else:
            # clear the previous mode's frame so CONUS doesn't linger
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            # backfill recent frames on first select so loop playback works immediately.
            self._satellite_fetcher.fetch_history(mode, 10)
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()

    def _on_satellite_frames_updated(self, mode: str, frames: list):
        self._satellite_cache[mode] = frames
        active_mode = self.satellite_controls.current_mode()
        if mode != active_mode:
            return
        was_live = self.satellite_controls.is_at_latest_frame()
        self.satellite_controls.set_cache_size(len(frames))
        if was_live:
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str)
            self.status_msg_label.setText(f"GOES {mode.upper()} {frames[-1].time_str}")
            self._layout_overlays()
            if not self.satellite_controls.is_looping():
                self.map_widget.set_satellite_visible(True)

    def _on_satellite_frame_requested(self, idx: int):
        mode   = self.satellite_controls.current_mode()
        frames = self._satellite_cache.get(mode, [])
        if not frames or idx >= len(frames):
            return
        frame = frames[idx]
        self._render_satellite_frame(frame)
        self.satellite_controls.set_scan_time(frame.time_str)

    def _on_satellite_speed_changed(self, ms: int):
        self._satellite_loop_timer.setInterval(ms)

    def _on_satellite_loop_toggled(self, looping: bool):
        if looping:
            self._satellite_loop_timer.start()
        else:
            self._satellite_loop_timer.stop()

    def _satellite_loop_tick(self):
        mode   = self.satellite_controls.current_mode()
        frames = self._satellite_cache.get(mode, [])
        if not frames:
            return
        current = self.satellite_controls.current_frame()
        nxt     = (current + 1) % len(frames)
        self.satellite_controls.set_frame(nxt)
        self._render_satellite_frame(frames[nxt])
        self.satellite_controls.set_scan_time(frames[nxt].time_str)

    def _render_satellite_frame(self, frame):
        if frame.b64:
            w, s, e, n = frame.bbox
            self.map_widget.set_satellite_frame(frame.b64, w, s, e, n)
        else:
            self.map_widget.set_satellite_time(frame.time_iso)

    def _on_meso_sectors_updated(self, sectors: dict):
        for idx in (1, 2):
            bbox = sectors.get(idx)
            # in archive mode buttons stay enabled even before bbox is known;
            available = bbox is not None or self._archive
            self.satellite_controls.set_meso_available(idx, available, bbox)
        self.map_widget.set_meso_sectors(sectors)

    def _on_meso_preview(self, idx: int, active: bool):
        self.map_widget.preview_meso_sector(idx if active else None)

    def _auto_start_radar(self):
        self._radar_fetcher.start()
        self._radar_fetcher.fetch_now()
        # show fetching status
        site = self.radar_controls.current_site()
        self.status_msg_label.setText(f"Fetching {site} radar data…")
        self._layout_overlays()

    def _radar_data_enabled(self) -> bool:
        return (
            hasattr(self, "radar_controls")
            and self.radar_controls.is_data_enabled()
        )

    def _on_radar_fetch_requested(self):
        if not self._radar_data_enabled():
            return
        self._radar_fetcher.fetch_now()

    def _on_radar_error(self, msg: str):
        if not self._radar_data_enabled():
            return
        self.status_msg_label.setText(f"Radar: {msg}")
        self._layout_overlays()
        self._radar_error_clear_timer.start(10_000)

    def _clear_radar_error(self):
        if self.status_msg_label.text().startswith("Radar:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _clear_hazard_error(self):
        if self.status_msg_label.text().startswith("Hazards:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_satellite_error(self, msg: str):
        self.status_msg_label.setText(f"Satellite: {msg}")
        self._layout_overlays()
        self._satellite_error_clear_timer.start(10_000)

    def _clear_satellite_error(self):
        if self.status_msg_label.text().startswith("Satellite:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_radar_toggled(self, enabled: bool):
        self._set_layer_active("radar", enabled)
        if enabled:
            # bump generation so any renders queued before this toggle-on are
            self._render_generation += 1
            self._radar_fetcher.start()
            self._radar_fetcher.fetch_now()
            site = self.radar_controls.current_site()
            self.status_msg_label.setText(f"Fetching {site} radar data…")
            self._layout_overlays()
        else:
            # stop everything and clear all state when disabled
            self._set_radar_station_picker_visible(False)
            self._loop_timer.stop()
            self.radar_controls.reset_cache_ui()
            self._scan_cache.clear()
            self._current_radar_scan = None
            # bump render generation so any in-flight decode/render and any
            self._render_generation += 1
            self._pending_render_scan = None
            self._render_in_flight = False
            self._deferred_inject_result = None
            self._inject_throttle_timer.stop()
            self._last_inject_time = 0.0
            self._radar_fetcher.reset_history()
            self._radar_fetcher.stop()
            self._radar_overlay.hide(transient=False)
            self.status_msg_label.setText("")
            self._layout_overlays()
            # defer the actual removeLayer + removeSource until the renderer has
            _guard_gen = self._render_generation
            QTimer.singleShot(400, lambda: self._deferred_radar_clear(_guard_gen))

    def _deferred_radar_clear(self, guard_gen: int):
        """Remove the MapLibre radar source/layer after the deferred delay.

        Skipped if the render generation changed since scheduling — meaning the
        user toggled radar back on (which bumps the generation) before the timer
        fired.
        """
        if self._render_generation == guard_gen:
            self._radar_overlay.clear()

    def _on_hazard_error(self, msg: str):
        self.status_msg_label.setText(f"Hazards: {msg}")
        self._layout_overlays()
        self._hazard_error_clear_timer.start(10_000)

    def _on_hazard_connectivity(self, online: bool):
        self.hazard_indicator.setVisible(not online)
        self._layout_overlays()

    def _on_spc_received(
        self,
        cat_str: str,
        wind_str: str,
        hail_str: str,
        tor_str: str,
        prob_str: str = '{"type":"FeatureCollection","features":[]}',
        sig_str: str = '{"type":"FeatureCollection","features":[]}',
    ):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_geojson(cat_str, wind_str, hail_str, tor_str, prob_str, sig_str)

    def _on_nws_raw_phenoms(self, phenoms: set):
        """Update the legend phenom set from the raw (unfiltered) NWS data.

        Keeping this separate from _on_nws_received ensures that toggling a
        phenom off in the legend doesn't remove its button — the user needs
        the button to be able to re-enable it.
        """
        self._nws_active_phenoms = phenoms or set()
        self._update_hazard_legend()

    def _on_nws_received(self, warnings_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_nws_warnings_geojson(warnings_str)
        # _nws_active_phenoms is updated by _on_nws_raw_phenoms (raw signal),

    def _on_spc_watches_received(self, watches_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_watches_geojson(watches_str)

    def _on_spc_mds_received(self, mds_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_mds_geojson(mds_str)

    def _clear_hazard_fetch_msg(self):
        txt = self.status_msg_label.text()
        if txt.startswith("Fetching ") or txt.startswith("Refreshing "):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_hazard_fetch_requested(self):
        self.status_msg_label.setText("Refreshing hazards…")
        self._layout_overlays()
        self._hazard_fetcher.fetch_now()

    def _on_nws_filter_changed(self, codes: set[str] | None):
        """Apply user-selected NWS phenom filter (codes: set of uppercase phenom strings).

        Also persist selection to QSettings so it survives restarts.
        """
        if not hasattr(self, "_hazard_fetcher"):
            return
        # none or empty set => no filter (show all)
        self._hazard_fetcher.set_nws_filter(codes if codes else None)
        # persist to settings as comma-separated string (or empty to clear)
        try:
            s = QSettings("NSSL", "STORM")
            if codes:
                s.setValue("nws/filters", ",".join(sorted(codes)))
            else:
                s.remove("nws/filters")
        except Exception:
            pass
        # re-emit or fetch to update the map immediately.
        if self._hazard_fetcher.is_nws_fresh():
            self._hazard_fetcher.emit_cached_nws()
        else:
            self._hazard_fetcher.fetch_now()

    def _on_spc_mds_toggled(self, enabled: bool):
        self._set_layer_active("spc_mds", enabled)
        self._hazard_fetcher.set_spc_mds_enabled(enabled)
        self.map_widget.set_spc_mds_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_mds_fresh():
                self._hazard_fetcher.emit_cached_mds()
            else:
                self.status_msg_label.setText("Fetching SPC MDs…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _update_hazard_legend(self):
        """Recompute which hazard layers are active and update the pill legend."""
        if not hasattr(self, "_hazard_fetcher"):
            # archive mode: derive active layers from hazard_controls button states.
            hc = self.hazard_controls
            active = []
            if hc._btn_outlook.isChecked():
                active.append("spc-cat")
            for k in ("tor", "wind", "hail", "prob", "sig"):
                btn = getattr(hc, f"_btn_{k}")
                if btn.isChecked():
                    active.append(f"spc-{k}")
            if hc._btn_watches.isChecked():
                active.append("spc-watches")
            if hc._btn_nws_warnings.isChecked():
                active.append("nws-warnings")
            hc.update_legend(active, nws_phenoms=self._nws_active_phenoms)
            self._start_layout_pulse()
            return
        fc = self._hazard_fetcher
        active = []
        if any(fc._spc_categories.values()):
            active.append("spc-cat")
        for k in ("tor", "wind", "hail", "prob", "sig"):
            if fc._spc_products.get(k):
                active.append(f"spc-{k}")
        if fc._spc_watches_enabled:
            active.append("spc-watches")
        if fc._spc_mds_enabled:
            active.append("spc-mds")
        if fc._nws_enabled:
            active.append("nws-warnings")
        self.hazard_controls.update_legend(active, nws_phenoms=self._nws_active_phenoms)
        self._start_layout_pulse()

    def _on_spc_feature_clicked(self, payload: str):
        """Handle a click on one or more overlapping hazard polygons."""
        import re as _re
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        # js sends a list of all unique features under the click point.
        items = raw if isinstance(raw, list) else [raw]

        fetch_targets: list[tuple[str, str, str | None]] = []  # (title, kind, identifier)
        for data in items:
            source = data.get("source", "")
            props = data.get("properties", {})

            # for archive mode, encode the current archive time into identifiers
            _archive_ts = (
                self._time_ctrl.current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self._archive and hasattr(self, "_time_ctrl") else None
            )

            if source in ("spc-cat", "spc-tor", "spc-wind", "spc-hail", "spc-prob", "spc-sig"):
                day = int(props.get("spc_day") or getattr(self.hazard_controls, "current_spc_day", lambda: 1)())
                title = "DAY 4-8 CONVECTIVE OUTLOOK" if day >= 4 else f"DAY {day} CONVECTIVE OUTLOOK"
                ident = f"{day}|{_archive_ts}" if _archive_ts else str(day)
                fetch_targets.append((title, "swo", ident))
            elif source == "spc-mds":
                name = str(props.get("name", "")).strip()
                _m = _re.search(r'\d+', name)
                num = _m.group().zfill(4) if _m else "0000"
                fetch_targets.append((f"MESOSCALE DISCUSSION {num}", "mcd", num))
            elif source == "spc-watches":
                watch_num = str(props.get("watch_num", "")).strip()
                if not watch_num:
                    continue
                event_label = str(props.get("event", "Watch")).upper()
                # append archive timestamp so text fetch targets the correct date.
                ident = f"{watch_num}|{_archive_ts}" if _archive_ts else watch_num
                fetch_targets.append((f"{event_label} {watch_num}", "watch", ident))
            elif source == "nws-warnings":
                warning_url = str(props.get("warning_url", "")).strip()
                if not warning_url:
                    continue
                prod_type = str(props.get("prod_type", "Warning")).title()
                wfo = str(props.get("wfo", "")).strip()
                title = f"{prod_type} — {wfo}" if wfo else prod_type
                fetch_targets.append((title, "warning", warning_url))

        if not fetch_targets:
            return

        self._fetch_generation += 1
        gen = self._fetch_generation
        self.outlook_panel.show_loading([t[0] for t in fetch_targets])
        self._layout_overlays()
        for title, kind, identifier in fetch_targets:
            threading.Thread(
                target=self._fetch_outlook_text,
                args=(gen, title, kind, identifier),
                daemon=True,
            ).start()

    def _fetch_outlook_text(self, generation: int, title: str, kind: str, identifier: str | None):
        """Fetch SPC discussion text in a background thread.

        Sources:
          SPC Outlooks: IEM Mesonet AFOS API (PIL: SWODY1/SWODY2/SWODY3/SWOD48)
          MDs:           SPC direct .txt URL    (https://www.spc.noaa.gov/products/md/md{nnnn}.txt)

        IEM rejects the SPCMCD{nnnn} PIL as too long, so MDs are fetched
        directly from SPC's own text product archive instead.
        """
        from urllib.request import Request, urlopen

        HEADERS = {
            "User-Agent": "STORM/1.0 (contact: support)",
            "Accept": "application/geo+json, application/ld+json, application/json, text/plain",
        }

        def _fetch(url: str) -> str:
            import time as _time
            last_exc = None
            for attempt in range(3):
                if attempt:
                    _time.sleep(0.8)
                try:
                    req = Request(url, headers=HEADERS)
                    with urlopen(req, timeout=15) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    return raw.strip("\x01\x02\x03\r\n").strip()
                except Exception as _e:
                    last_exc = _e
            raise last_exc

        try:
            if kind == "swo":
                day = 1
                archive_identifier = None
                if identifier:
                    parts = str(identifier).split("|", 1)
                    try:
                        day = int(parts[0])
                    except (TypeError, ValueError):
                        day = 1
                    archive_identifier = parts[1] if len(parts) > 1 else None
                pil = "SWOD48" if day >= 4 else f"SWODY{max(1, min(3, day))}"
                # archive_identifier is an ISO archive timestamp in archive mode.
                if archive_identifier:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(archive_identifier, "%Y-%m-%dT%H:%M:%SZ")
                    # iem AFOS ignores after/before in ISO format; sdate/edate (date-only)
                    sdate = ts.strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil={pil}&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil={pil}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "mcd":
                url = f"https://www.spc.noaa.gov/products/md/md{identifier}.txt"
                text = _fetch(url)
            elif kind == "watch":
                # identifier is "NNNN" in live mode or "NNNN|ISO_TS" in archive mode.
                watch_num = identifier
                archive_ts = None
                if identifier and "|" in identifier:
                    watch_num, archive_ts = identifier.split("|", 1)
                sel_digit = str(int(watch_num) % 10)
                if archive_ts:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(archive_ts, "%Y-%m-%dT%H:%M:%SZ")
                    sdate = (ts - _td2(days=1)).strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil=SEL{sel_digit}&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SEL{sel_digit}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "warning":
                if not identifier:
                    text = "(No warning URL available)"
                elif self._archive:
                    # archive mode: identifier is the IEM VTEC viewer URL with query params:
                    from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
                    import json as _json
                    _p = _urlparse(identifier)
                    _q = _parse_qs(_p.query)
                    year     = (_q.get("year",         [None])[0])
                    wfo      = (_q.get("wfo",           [None])[0])
                    phenom   = (_q.get("phenomena",     [None])[0])
                    sig      = (_q.get("significance",  [None])[0])
                    etn_raw  = (_q.get("eventid",       [None])[0])
                    if year and wfo and phenom and sig and etn_raw:
                        etn_int = int(etn_raw.lstrip("0") or "0")
                        api_url = (
                            "https://mesonet.agron.iastate.edu/json/vtec_event.py"
                            f"?wfo={wfo}&year={year}&phenomena={phenom}"
                            f"&significance={sig}&etn={etn_int}"
                        )
                        raw = _fetch(api_url)
                        data = _json.loads(raw)
                        if data.get("event_exists"):
                            text = (data.get("report") or {}).get("text", "") or "(No text in archive)"
                        else:
                            text = "(Warning event not found in IEM archive)"
                    else:
                        text = "(Could not parse VTEC parameters from warning URL)"
                else:
                    import json as _json
                    raw = _fetch(identifier)
                    try:
                        raw_json = _json.loads(raw)
                        wp = raw_json.get("properties", {})
                        headline = wp.get("headline", "")
                        description = wp.get("description", "")
                        instruction = wp.get("instruction", "")
                        text = "\n\n".join(x for x in [headline, description, instruction] if x)
                    except _json.JSONDecodeError:
                        text = "(Warning text unavailable)"
            else:
                text = ""
            if not text:
                text = "(No discussion text found)"
        except Exception as exc:
            text = f"Failed to load discussion:\n{exc}"

        self._panel_text_ready.emit(generation, title, text)

    def _on_panel_text_ready(self, generation: int, title: str, text: str):
        if generation == self._fetch_generation:
            self.outlook_panel.show_text(title, text)

    def _on_spc_day_changed(self, day: int):
        self._hazard_fetcher.set_spc_day(day)

    def _on_spc_mode_changed(self, mode: str):
        self._set_layer_active("spc_outlook", mode == "outlook")
        self._set_layer_active("spc_tor",     mode == "tor")
        self._set_layer_active("spc_wind",    mode == "wind")
        self._set_layer_active("spc_hail",    mode == "hail")
        self._set_layer_active("spc_prob",    mode == "prob")
        self._set_layer_active("spc_sig",     mode == "sig")
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self._hazard_fetcher.set_spc_category_enabled(key, outlook_on)
            self.map_widget.set_spc_category_visible(key, outlook_on)

        for key in ("tor", "wind", "hail", "prob", "sig"):
            on = mode == key
            self._hazard_fetcher.set_spc_product_enabled(key, on)
            self.map_widget.set_spc_product_visible(key, on)

        if mode:
            _spc_labels = {
                "outlook": "SPC outlook", "tor": "SPC tornado",
                "wind": "SPC wind", "hail": "SPC hail",
                "prob": "SPC probability", "sig": "SPC significant",
            }
            _fetch_label = f"Fetching {_spc_labels.get(mode, 'SPC')}…"
            needs_refresh = False
            if mode == "outlook":
                needs_refresh = not self._hazard_fetcher.spc_category_cached()
            elif mode in ("tor", "wind", "hail", "prob", "sig"):
                needs_refresh = not self._hazard_fetcher.spc_product_cached(mode)
            if needs_refresh:
                self._hazard_fetcher.force_spc_refresh()
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
            elif self._hazard_fetcher.is_spc_fresh():
                self._hazard_fetcher.emit_cached_spc()
            else:
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_spc_watches_toggled(self, enabled: bool):
        self._set_layer_active("spc_watches", enabled)
        self._hazard_fetcher.set_spc_watches_enabled(enabled)
        self.map_widget.set_spc_watches_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_watches_fresh():
                self._hazard_fetcher.emit_cached_watches()
            else:
                self.status_msg_label.setText("Fetching SPC watches…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_nws_warnings_toggled(self, enabled: bool):
        self._set_layer_active("nws_warnings", enabled)
        self._hazard_fetcher.set_nws_enabled(enabled)
        self.map_widget.set_nws_warnings_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_nws_fresh():
                self._hazard_fetcher.emit_cached_nws()
            else:
                self.status_msg_label.setText("Fetching NWS warnings…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_cwa_toggled(self, enabled: bool):
        """Toggle the CWA overlay visibility and mark the layer active.

        If the shapefile hasn't been loaded yet, kick off the background load
        and show a status message until cwa_loaded fires.
        """
        self._set_layer_active("cwa", enabled)
        if enabled and not self._cwa_loaded:
            self.status_msg_label.setText("Loading CWA boundaries…")
            self.map_widget.cwa_loaded.connect(self._on_cwa_load_complete)
            self.map_widget.load_cwa_shapefile()
        else:
            self.map_widget.set_cwa_visible(enabled)

    def _on_cwa_load_complete(self):
        try:
            self.map_widget.cwa_loaded.disconnect(self._on_cwa_load_complete)
        except Exception:
            pass
        self._cwa_loaded = True
        self.map_widget.set_cwa_visible(True)
        self.status_msg_label.setText("")

    def _on_radar_site_changed(self, site: str):
        # increment generation so any in-flight decodes/renders for old site are discarded
        self._render_generation += 1
        self._pending_render_scan = None
        self._render_in_flight = False   # previous render's result will be discarded by gen check
        # clear cache when site changes — old data belongs to a different location
        self._radar_fetcher.set_site(site)
        self._loop_timer.stop()
        self.radar_controls.reset_cache_ui()
        self._scan_cache.clear()
        self._current_radar_scan = None
        if getattr(self, '_site_change_from_map_click', False):
            # js click handler already set raster-opacity to 0 — just sync
            self._radar_overlay._hidden = True
            self._radar_overlay._current_scan = None
        else:
            # non-click path (auto-site, etc.) — safe to send JS
            self._radar_overlay.hide()
        # reset inject throttle so the first frame for the new site appears immediately
        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer.stop()
        if not self._radar_data_enabled():
            self.status_msg_label.setText("")
            self._layout_overlays()
            return
        # show fetching status
        self._radar_initial_backfill_complete = False
        self._radar_backfill_products_received = set()
        self.status_msg_label.setText(f"Fetching {site} radar data…")
        self._layout_overlays()

    def _on_radar_products_available(self, products: list[str]):
        if not hasattr(self, "_radar_fetcher"):
            return
        # always fetch all 4 products regardless of THREDDS availability probe —
        self._radar_fetcher.set_products(["N0B", "N0U", "N0C", "N0K"])

    def _on_radar_product_availability_changed(self, availability: dict):
        self._radar_product_availability = dict(availability)
        self._update_radar_unavailable_status()

    def _update_radar_unavailable_status(self):
        """If the currently selected radar product is marked unavailable for
        the current site, surface that in the status pill."""
        if not hasattr(self, "radar_controls"):
            return
        product = self.radar_controls.current_product()
        site = self.radar_controls.current_site()
        avail = self._radar_product_availability.get(product, True)
        cur = self.status_msg_label.text()
        prefix = "Radar:"
        if not avail:
            name_map = {"N0C": "Correlation Coefficient", "N0K": "Specific Diff. Phase"}
            name = name_map.get(product, product)
            self.status_msg_label.setText(f"Radar: no {name} data at {site}")
            self._layout_overlays()
        elif cur.startswith("Radar: no "):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_sounding_mode_toggled(self, active: bool):
        if not active:
            # deactivate both sub-modes and clear station layer
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            if hasattr(self, "sounding_controls"):
                self.sounding_controls.reset_to_hrrr()
            return
        # activate the currently selected sub-mode
        mode = self.sounding_controls.active_mode if hasattr(self, "sounding_controls") else "hrrr"
        self._on_sounding_mode_changed(mode)
        # untoggle other exclusive map-click modes
        if self.btn_measure.isChecked():
            self.btn_measure.setChecked(False)
        if hasattr(self, "btn_annotate") and self.btn_annotate.isChecked():
            self.btn_annotate.setChecked(False)
        # cancel active annotation sub-tool even if annotate drawer was already closed
        if getattr(self, "_active_annotation_type", "") or getattr(self, "_active_drawing_type", ""):
            self._on_annotation_tool_selected("")
        # cancel route pick mode
        if hasattr(self, "map_widget"):
            self.map_widget.set_route_pick_mode(False)

    def _on_sounding_mode_changed(self, mode: str):
        """Called when the user switches between HRRR, OBS, and NSSL in the sub-bar."""
        if not self.btn_sounding.isChecked():
            return
        if self._archive:
            # archive mode: HRRR → model sounding, OBS → radiosonde,
            # NSSL → CLAMPS radiosonde launch, falling back to a CLAMPS
            # TROPoe thermodynamic retrieval if no launch exists for the day.
            if mode == "hrrr":
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.map_widget.set_sounding_mode(True)
            elif mode == "obs":
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
                self.map_widget.set_obs_sounding_mode(True)
            else:  # nssl
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.status_msg_label.setText("Fetching NSSL soundings…")
                self._archive_sounding.fetch_nssl_sounding()
            return
        if not hasattr(self, "_sounding_fetcher"):
            return
        if mode == "hrrr":
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.map_widget.set_sounding_mode(True)
        elif mode == "obs":
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
            self.map_widget.set_obs_sounding_mode(True)
        else:  # nssl
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.status_msg_label.setText("Fetching NSSL soundings…")
            self._clamps_sounding_fetcher.fetch()

    def _on_sounding_map_click(self, lat: float, lon: float):
        self.status_msg_label.setText("Fetching HRRR sounding…")
        self._sounding_fetcher.fetch(lat, lon)

    def _on_asos_toggled(self, enabled: bool):
        """Enable/disable ASOS; only enter draw mode if no bbox has been set yet."""
        self._surface_fetcher.set_asos_enabled(enabled)
        self._set_layer_active("asos", enabled)
        if enabled and self._surface_fetcher._asos_bbox is None:
            self.map_widget.set_asos_bbox_mode(True)
        elif not enabled:
            self.map_widget.set_asos_bbox_mode(False)
            self._remove_surface_source_plots("asos")

    def _start_new_asos_bbox(self):
        """Let the user replace the saved ASOS bbox with a freshly drawn one."""
        self._surface_render_generation += 1
        self._surface_fetcher.clear_asos_bbox()
        asos_ids = [
            sid for sid in self._surface_station_ids
            if sid.startswith("surface:asos:")
        ]
        self._surface_station_ids = {
            sid for sid in self._surface_station_ids
            if not sid.startswith("surface:asos:")
        }
        if asos_ids:
            for sid in asos_ids:
                self._surface_layer._cache.pop(sid, None)
            self.map_widget.remove_surface_station_plots_batch(asos_ids)
        if not self.surface_controls.asos_enabled():
            self.surface_controls.set_asos_enabled(True)
        else:
            self._surface_fetcher.set_asos_enabled(True)
            self._set_layer_active("asos", True)
        self.status_msg_label.setText("ASOS: draw a new bounding box")
        self.map_widget.set_asos_bbox_mode(True)

    def _on_asos_bbox_selected(self, west: float, south: float, east: float, north: float):
        """Triggered when user finishes an ASOS bbox selection on the map."""
        self.status_msg_label.setText("Fetching ASOS observations…")
        # exit drawing mode but keep the ASOS button checked (auto-refresh stays active)
        self.map_widget.set_asos_bbox_mode(False)
        self.map_widget.run_js(
            "if(window.stormRestoreAsosMapInteractions) stormRestoreAsosMapInteractions();"
        )

        def _start_asos_fetch():
            try:
                self._surface_fetcher.fetch_asos_bbox(west, south, east, north)
            except Exception as exc:
                log.error("ASOS fetch failed to start: %s", exc, exc_info=True)
                self.status_msg_label.setText(f"ASOS fetch error: {exc}")

        QTimer.singleShot(50, _start_asos_fetch)

    def _on_obs_station_click(self, station_id: str, name: str, lat: float, lon: float, elev: float):
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._obs_sounding_fetcher.fetch(station_id, name, lat, lon, elev)

    def _on_sounding_ready(self, sset):
        self.status_msg_label.setText("")
        if sset.is_nssl:
            self._last_nssl_sset = sset
        self._sounding_dialog.load(sset)

        available = self._compute_available_sources(sset)
        self._sounding_dialog.set_available_sources(available)

        if sset.is_nssl and not self._archive:
            self._nssl_sounding_refresh_timer.start()
        elif hasattr(self, "_nssl_sounding_refresh_timer"):
            self._nssl_sounding_refresh_timer.stop()

    def _compute_available_sources(self, sset) -> dict:
        """Determine which comparison source pills to show for a given primary SoundingSet."""
        available = {}
        nssl = getattr(self, "_last_nssl_sset", None)

        if sset.source == "hrrr":
            obs = nearest_obs_station(sset.lat, sset.lon)
            if obs:
                available["obs"] = obs
            if nssl and nssl_within_radius_km(nssl.lat, nssl.lon, sset.lat, sset.lon):
                available["nssl"] = {}

        elif sset.source == "obs":
            available["hrrr"] = {"lat": sset.lat, "lon": sset.lon}
            if nssl and nssl_within_radius_km(nssl.lat, nssl.lon, sset.lat, sset.lon):
                available["nssl"] = {}

        elif sset.source == "nssl":
            if sset.lat != 0.0 or sset.lon != 0.0:
                available["hrrr"] = {"lat": sset.lat, "lon": sset.lon}
                obs = nearest_obs_station(sset.lat, sset.lon)
                if obs:
                    available["obs"] = obs

        return available

    def _on_comp_sounding_ready(self, sset):
        """Called when a comparison (secondary) sounding finishes fetching."""
        self.status_msg_label.setText("")
        if sset.is_nssl:
            self._last_nssl_sset = sset
        self._sounding_dialog.add_source(sset)

    def _on_comparison_source_requested(self, source: str, meta: dict):
        """Triggered when the user clicks an inactive pill in the sounding dialog."""
        if source == "hrrr":
            lat = meta.get("lat", 0.0)
            lon = meta.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                return
            self.status_msg_label.setText(f"Fetching HRRR comparison sounding…")
            self._comp_hrrr_fetcher.fetch(lat, lon)
        elif source == "obs":
            sid  = meta.get("station_id", "")
            name = meta.get("name", "")
            lat  = meta.get("lat", 0.0)
            lon  = meta.get("lon", 0.0)
            elev = meta.get("elev", 0.0)
            if not sid:
                return
            self.status_msg_label.setText(f"Fetching OBS comparison sounding {sid}…")
            self._comp_obs_fetcher.fetch(sid, name, lat, lon, elev)
        elif source == "nssl":
            self.status_msg_label.setText("Fetching NSSL comparison sounding…")
            self._comp_nssl_fetcher.fetch()

    def _on_sounding_error(self, msg: str):
        self.status_msg_label.setText(f"Sounding error: {msg}")

    def _refresh_nssl_sounding_if_visible(self):
        if self._archive:
            self._nssl_sounding_refresh_timer.stop()
            return
        sset = getattr(self._sounding_dialog, "_sset", None)
        if (
            not self._sounding_dialog.isVisible()
            or sset is None
            or not sset.is_nssl
        ):
            self._nssl_sounding_refresh_timer.stop()
            return
        self._clamps_sounding_fetcher.fetch()

    def _on_vad_requested(self):
        """Open the VAD wind-profile hodograph dialog: live NEXRAD VAD for
        the current radar site, or CLAMPS wind profiles for the archive
        date/known platforms in archive mode (fetched on the archive
        clock, not "now" -- live NEXRAD semantics don't apply here)."""
        if self._archive:
            self.status_msg_label.setText("Fetching CLAMPS wind profiles…")
            if not self._archive_clamps_wind.fetch(self._archive_time):
                self.status_msg_label.setText("CLAMPS wind: fetch already in progress")
            return

        site = self.radar_controls.current_site()
        from ui.dialogs.vad_dialog import VADDialog
        dlg = VADDialog(site, parent=self)
        dlg.exec()

    def _on_archive_clamps_wind_ready(self, sets: dict) -> None:
        if not sets:
            self.status_msg_label.setText("CLAMPS wind: no data for this date")
            return
        # More than one platform can have data on a given date; show
        # whichever sorts first for now. Picking among several is a later
        # UI improvement once that's a common case rather than the
        # exception it is today.
        platform_id = sorted(sets)[0]
        note = f" ({len(sets)} platforms available)" if len(sets) > 1 else ""
        self.status_msg_label.setText(f"CLAMPS wind: showing {platform_id}{note}")
        from ui.dialogs.vad_dialog import VADDialog
        dlg = VADDialog(platform_id, parent=self, preloaded_set=sets[platform_id])
        dlg.exec()

    # -- NOXP mobile radar (archive) ------------------------------------

    def _on_noxp_platform_selected(self, platform_id: str) -> None:
        from archive.catalog import catalogs_for_platform
        platform = next((p for p in self._archive_noxp_platforms() if p.platform_id == platform_id), None)
        if platform is None:
            return
        catalog_root = catalogs_for_platform(platform)[0].url
        self.status_msg_label.setText(f"NOXP: searching {platform.display_name}…")
        if not self._archive_noxp.discover(platform_id, catalog_root, self._archive_time):
            self.status_msg_label.setText("NOXP: search already in progress")

    def _archive_noxp_platforms(self):
        from archive.catalog import ALL_PLATFORMS
        return [p for p in ALL_PLATFORMS if p.family == "NOXP Radar"]

    def _on_archive_noxp_assets_ready(self, platform_id: str, assets: list) -> None:
        if self.noxp_controls.current_platform() is None or self.noxp_controls.current_platform().platform_id != platform_id:
            return  # a later platform selection has already superseded this result
        self.noxp_controls.set_assets(assets)
        self.status_msg_label.setText(f"NOXP: {len(assets)} volume(s) found")

    def _on_noxp_asset_selected(self, asset) -> None:
        platform = self.noxp_controls.current_platform()
        if platform is None:
            return
        self.status_msg_label.setText(f"NOXP: loading {asset.name}…")
        if not self._archive_noxp.load_volume(platform.platform_id, asset):
            self.status_msg_label.setText("NOXP: load already in progress")

    def _on_archive_noxp_volume_loaded(self, platform_id: str, volume) -> None:
        self._noxp_current_volume = volume
        self.noxp_controls.set_volume_summary(volume)
        self.status_msg_label.setText(f"NOXP: {volume.scan_type} volume loaded ({volume.provenance.get('format', '?')})")

    def _on_noxp_render_requested(self, sweep_index: int, field_name: str) -> None:
        if self._noxp_current_volume is None:
            return
        from archive.fetchers.noxp_radar_archive_fetcher import noxp_volume_to_scan
        try:
            scan = noxp_volume_to_scan(self._noxp_current_volume, sweep_index, field_name)
        except ValueError as exc:
            self.status_msg_label.setText(f"NOXP: {exc}")
            return
        self.status_msg_label.setText(f"NOXP: rendering sweep {sweep_index} ({field_name})…")
        from ui.map.radar_overlay import RENDER_GRID_SIZE
        threading.Thread(
            target=self._bg_render_noxp, args=(scan, max(RENDER_GRID_SIZE, 768)), daemon=True,
        ).start()

    def _bg_render_noxp(self, scan, grid_size: int) -> None:
        """A NOXP render is a discrete, user-triggered one-shot -- unlike the
        continuously-advancing NEXRAD archive playback, there's no in-flight
        render to preempt, so this doesn't need that pipeline's
        generation-tracking, just the same render + inject calls."""
        try:
            png, bounds, _ = _render_scan_to_png(scan, grid_size)
        except Exception as exc:
            log.error("NOXP render failed: %s", exc)
            self._noxp_render_ready.emit({"error": str(exc)})
            return
        self._noxp_render_ready.emit({"png": png, "bounds": bounds, "scan": scan})

    def _on_noxp_render_ready(self, result: dict) -> None:
        if "error" in result:
            self.status_msg_label.setText(f"NOXP: render error — {result['error']}")
            return
        self._radar_overlay.inject(result["png"], result["bounds"])
        scan = result["scan"]
        self.status_msg_label.setText(
            f"NOXP: {scan.native_field} {scan.elevation_deg:.1f}° {scan.scan_time.strftime('%H:%MZ')}"
        )

    # -- CLAMPS raw lidar quicklook (archive) ---------------------------

    def _on_archive_raw_lidar_assets_ready(self, assets_by_source: dict) -> None:
        self.raw_lidar_controls.set_assets_for_all_sources(assets_by_source)

    def _on_raw_lidar_quicklook_requested(self, platform_id: str, asset) -> None:
        self.status_msg_label.setText(f"Raw lidar: loading {asset.filename}…")
        if not self._archive_raw_lidar.load(platform_id, asset):
            self.status_msg_label.setText("Raw lidar: load already in progress")

    def _on_archive_raw_lidar_rays_ready(self, platform_id: str, rays) -> None:
        self.status_msg_label.setText(f"Raw lidar: {platform_id} loaded")
        if self._raw_lidar_dialog is not None and self._raw_lidar_dialog.platform_id == platform_id:
            self._raw_lidar_dialog.set_rays(rays)
            self._raw_lidar_dialog.raise_()
            self._raw_lidar_dialog.activateWindow()
            return
        self._raw_lidar_dialog = RawLidarQuicklookDialog(platform_id, parent=self, preloaded_rays=rays)
        self._raw_lidar_dialog.show()

    def _toggle_radar_station_picker(self):
        self._set_radar_station_picker_visible(not self._radar_station_picker_visible)

    def _set_radar_station_picker_visible(self, visible: bool):
        self._radar_station_picker_visible = visible
        self.map_widget.set_radar_stations_visible(visible)

    def _on_radar_station_clicked(self, site: str):
        # js already called stormSetRadarStationsVisible(false) and set
        self._radar_station_picker_visible = False
        if self._archive:
            # in archive mode, start fetching from the selected station.
            self._archive_session.radar_station = site
            self._start_archive_radar(site)
            return
        self._site_change_from_map_click = True
        self._select_radar_site(site, user_selected=True)
        self._site_change_from_map_click = False

    def _select_radar_site(self, site: str, user_selected: bool):
        if user_selected:
            self._radar_auto_site_pending = False
        if site == self.radar_controls.current_site():
            self.radar_controls.set_selected_site(site, emit=False)
            return
        self.radar_controls.set_selected_site(site, emit=True)

    def _on_radar_product_changed(self, product: str):
        # both products are always cached — just switch what's displayed
        self._loop_timer.stop()
        if not self._radar_data_enabled():
            self._current_radar_scan = None
            self._radar_overlay.hide()
            self.status_msg_label.setText("")
            self._layout_overlays()
            return
        key = f"{self.radar_controls.current_site()}/{product}"
        cache = self._scan_cache.get(key, [])
        self.radar_controls.reset_cache_ui()
        if cache:
            self.radar_controls.set_cache_size(len(cache))
            self._show_scan(cache[-1])
        else:
            # hide instead of clear to avoid forcing MapLibre to teardown and rebuild
            self._current_radar_scan = None
            self._radar_overlay.hide()
            if not self._radar_product_availability.get(product, True):
                self._update_radar_unavailable_status()
            else:
                self.status_msg_label.setText(f"Fetching radar product {product}…")
                self._layout_overlays()
                self._radar_fetcher.fetch_now()

    def _on_radar_data(self, site: str, product: str, raw_bytes: bytes):
        """Called on the main thread by the fetcher signal.  Returns immediately —
        the actual decode is submitted to a background thread so the UI is never
        blocked by MetPy/numpy work."""
        if not self._radar_data_enabled():
            return
        log.debug("radar data received: %s/%s (%d bytes)", site, product, len(raw_bytes))
        gen = self._render_generation
        self._decode_executor.submit(self._bg_decode, gen, site, product, raw_bytes)

    def _bg_decode(self, gen: int, site: str, product: str, raw_bytes: bytes):
        """Runs in the decode thread pool — NOT on the main thread.
        Decodes raw NEXRAD bytes and emits _scan_decoded (auto-queued to main thread)."""
        # bail early if the site was changed while we were queued
        if gen != self._render_generation:
            log.debug("bg_decode: discarding stale decode gen=%d (current=%d)", gen, self._render_generation)
            return
        scan = decode_nexrad_l3(site, product, raw_bytes)
        if scan is None:
            self._radar_decode_failed.emit(site, product)
            return
        # check again after decode in case site changed mid-decode
        if gen != self._render_generation:
            log.debug("bg_decode: discarding post-decode stale result gen=%d", gen)
            return
        self._scan_decoded.emit(gen, site, product, scan)

    def _on_scan_decoded(self, gen: int, site: str, product: str, scan):
        """Runs on the main thread (PyQt queues the signal from the decode thread).
        Updates the scan cache and submits a background render for the latest frame."""
        if gen != self._render_generation:
            return
        if not self._radar_data_enabled():
            return

        scan_time = scan.scan_time
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)
        else:
            scan_time = scan_time.astimezone(timezone.utc)
        if datetime.now(timezone.utc) - scan_time > timedelta(minutes=LIVE_RADAR_MAX_AGE_MINUTES):
            log.warning(
                "discarding stale live radar scan: %s/%s %s",
                site,
                product,
                scan_time.strftime("%Y-%m-%d %H:%M:%SZ"),
            )
            return
        scan.scan_time = scan_time

        key = f"{site}/{product}"
        cache = self._scan_cache.setdefault(key, [])

        # skip duplicate scan times — THREDDS sometimes returns the same file twice
        if cache and cache[-1].scan_time == scan.scan_time:
            return

        cache.append(scan)

        # trim to 35-minute rolling window, hard cap at 6 scans per product (12 total)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        while cache and cache[0].scan_time < cutoff:
            cache.pop(0)
        while len(cache) > 6:
            cache.pop(0)

        if scan not in cache:
            log.debug("discarding live radar scan trimmed from cache: %s/%s", site, product)
            return

        log.debug("cache updated: key=%s, n=%d frames", key, len(cache))
        
        # track initial backfill completion
        if not self._radar_initial_backfill_complete:
            self._radar_backfill_products_received.add(product)
            # consider backfill complete when we have at least one scan for each product
            expected_products = {"N0B", "N0U", "N0C", "N0K"}
            if expected_products.issubset(self._radar_backfill_products_received):
                self._radar_initial_backfill_complete = True
                # clear the fetching message
                if self.status_msg_label.text().startswith("Fetching"):
                    self.status_msg_label.setText("")
                    self._layout_overlays()

        # only update display for the currently visible product;
        if product != self.radar_controls.current_product():
            return

        was_live = self.radar_controls.is_at_latest_frame()
        self.radar_controls.set_cache_size(len(cache))

        if not self.radar_controls.is_looping() and was_live:
            self._pending_render_scan = scan
            self._submit_pending_render()

    def _submit_pending_render(self):
        """Submit a background render for the latest pending scan.
        No-op if a render is already in flight — the pending scan will be
        submitted by _on_render_ready when the current render completes."""
        scan = self._pending_render_scan
        if scan is None:
            return
        if not self._radar_data_enabled():
            self._pending_render_scan = None
            return
        if self._render_in_flight:
            # don't queue a second render — _on_render_ready will re-check pending
            return
        self._pending_render_scan = None
        self._render_in_flight = True
        gen = self._render_generation
        grid_size = self._radar_overlay._grid_size
        self._render_executor.submit(self._bg_render, gen, scan, grid_size)

    def _bg_render(self, gen: int, scan, grid_size: int):
        """Runs in the render thread pool — NOT on the main thread.
        Renders scan to PNG then emits _render_ready (auto-queued to main thread)."""
        if gen != self._render_generation:
            log.debug("bg_render: discarding stale render gen=%d (current=%d)", gen, self._render_generation)
            return
        try:
            mask_scan = self._velocity_mask_scan(scan)
            png_bytes, bounds, elapsed_ms = self._render_scan_to_png(
                scan, grid_size, mask_scan=mask_scan
            )
        except Exception as e:
            log.error("bg_render: render failed: %s", e, exc_info=True)
            return
        if gen != self._render_generation:
            log.debug("bg_render: discarding post-render stale result gen=%d", gen)
            return
        self._render_ready.emit({
            "gen":        gen,
            "scan":       scan,
            "png_bytes":  png_bytes,
            "bounds":     bounds,
            "elapsed_ms": elapsed_ms,
        })

    _INJECT_COOLDOWN_MS = 600

    def _on_render_ready(self, result: dict):
        """Runs on the main thread — injects the pre-rendered PNG into the map."""
        import time as _time
        self._render_in_flight = False
        if result["gen"] != self._render_generation:
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return
        if not self._radar_data_enabled():
            self._pending_render_scan = None
            return
        scan = result["scan"]
        if scan.product != self.radar_controls.current_product():
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return

        now = _time.monotonic()
        elapsed_since_inject = (now - self._last_inject_time) * 1000

        if elapsed_since_inject >= self._INJECT_COOLDOWN_MS:
            # enough time has passed — inject immediately
            self._do_inject(result)
        else:
            # too soon — defer this result; timer will flush it
            self._deferred_inject_result = result
            if not self._inject_throttle_timer.isActive():
                remaining = self._INJECT_COOLDOWN_MS - int(elapsed_since_inject)
                self._inject_throttle_timer.start(max(20, remaining))

        # keep the render pipeline moving regardless of inject throttle
        if self._pending_render_scan is not None:
            self._submit_pending_render()

    def _flush_deferred_inject(self):
        """Timer callback — inject the most recent deferred render result."""
        result = self._deferred_inject_result
        self._deferred_inject_result = None
        if result is None:
            return
        if result["gen"] != self._render_generation:
            return
        if not self._radar_data_enabled():
            return
        self._do_inject(result)

    def _do_inject(self, result: dict):
        """Actually inject the rendered PNG into the map."""
        if not self._radar_data_enabled():
            return
        import time as _time
        scan = result["scan"]
        self._current_radar_scan = scan
        self._radar_overlay.inject(result["png_bytes"], result["bounds"])
        self._last_inject_time = _time.monotonic()
        self._radar_overlay._maybe_adjust_grid(result["elapsed_ms"])
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
        self._radar_error_clear_timer.stop()
        if not self._pending_cone_motion_fix:
            self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _show_scan(self, scan):
        if not self._radar_data_enabled():
            return
        self._current_radar_scan = scan
        self._radar_overlay.update(scan, mask_scan=self._velocity_mask_scan(scan))
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
        self._radar_error_clear_timer.stop()
        if not self._pending_cone_motion_fix:
            self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _velocity_mask_scan(self, scan):
        if getattr(scan, "colormap", "") not in ("nws_vel", "nws_cc", "nws_kdp"):
            return None
        site = getattr(scan, "site", "")
        ref_cache = self._scan_cache.get(f"{site}/N0B", [])
        if not ref_cache:
            return None
        return min(
            ref_cache,
            key=lambda ref: abs((ref.scan_time - scan.scan_time).total_seconds()),
        )

    def _display_cached_frame(self, idx: int):
        if not self._radar_data_enabled():
            return
        log.debug("displaying cached frame %d of %d",
                  idx, len(self._scan_cache.get(
                      f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}", []
                  )))
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if 0 <= idx < len(cache):
            self._show_scan(cache[idx])

    def _on_radar_speed_changed(self, ms: int):
        self._loop_timer.setInterval(ms)

    def _on_loop_toggled(self, looping: bool):
        if looping and not self._radar_data_enabled():
            self._loop_timer.stop()
            return
        if looping:
            self._loop_timer.start()
        else:
            self._loop_timer.stop()
            # snap back to the latest (live) frame when loop stops
            key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
            cache = self._scan_cache.get(key, [])
            if cache:
                self.radar_controls.set_frame(len(cache) - 1)
                self._show_scan(cache[-1])

    def _advance_loop_frame(self):
        if not self._radar_data_enabled():
            self._loop_timer.stop()
            return
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if not cache:
            return
        # wrap around so loop plays continuously
        next_frame = (self.radar_controls.current_frame() + 1) % len(cache)
        self.radar_controls.set_frame(next_frame)
        self._show_scan(cache[next_frame])


    def _init_mqtt(self):
        self._mqtt_client = MQTTClient(client_id=config.VEHICLE_ID, parent=self)
        self._mqtt_client.connected.connect(self._on_mqtt_connected)
        self._mqtt_client.disconnected.connect(self._on_mqtt_disconnected)

        self._vehicle_sync = VehicleSync(self._mqtt_client, parent=self)
        self._vehicle_sync.vehicle_received.connect(self._on_remote_vehicle_obs)
        self._storm_cone_sync = StormConeSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync = ScanSectorSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync.scan_received.connect(self._recv_remote_scan_sector)

        # connect after a short delay so the window is fully painted first
        if config.MQTT_HOST:
            QTimer.singleShot(500, self._mqtt_connect)
        else:
            log.info("MQTT host not configured — running offline")

    def _on_remote_vehicle_obs(self, obs):
        # if this machine is producing local data (not in monitor mode),
        if not self._monitor and obs.vehicle_id == config.VEHICLE_ID:
            return
        self.update_vehicle_obs(obs)

    def _on_local_vehicle_obs(self, obs: Observation):
        if self._startup_local_pending and obs.vehicle_id == config.VEHICLE_ID:
            self._complete_local_startup_phase()
        # track GPS fix age for the local vehicle status indicator
        if obs.vehicle_id == config.VEHICLE_ID and obs.lat is not None:
            self._last_local_obs_ts = time.monotonic()
        # always update local GUI at the full poll rate (1 Hz)
        self.update_vehicle_obs(obs)
        # throttle MQTT publishes independently — other vehicles don't need 1 Hz
        vehicle_sync = getattr(self, "_vehicle_sync", None)
        if vehicle_sync is not None:
            now = time.monotonic()
            if now - self._last_mqtt_publish >= config.OBS_MQTT_PUBLISH_S:
                vehicle_sync.publish_obs(obs)
                self._last_mqtt_publish = now

    def _mqtt_connect(self):
        use_tls = config.MQTT_USE_TLS and not runtime_flags.FLAGS.mqtt_no_tls
        if not use_tls:
            log.warning("MQTT TLS disabled via --mqtt-no-tls (diagnostic mode)")
        self._mqtt_client.connect_to_broker(
            host=config.MQTT_HOST,
            port=config.MQTT_PORT,
            use_tls=use_tls,
            ca_cert=config.MQTT_CA_CERT,
            cert_file=config.MQTT_CERT_FILE,
            key_file=config.MQTT_KEY_FILE,
        )

    def _on_mqtt_connected(self):
        self.set_connection_status(True)
        if self._startup_mqtt_pending:
            self._mqtt_startup_timer.start(1500)
        if self.status_msg_label.text().startswith("MQTT:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_mqtt_disconnected(self, code: int):
        self.set_connection_status(False)
        if self._startup_mqtt_pending:
            self._complete_mqtt_startup_phase()
        code_map = {
            -1: "setup error (cert/key/path)",
            7: "connection lost",
            128: "unspecified error",
            129: "malformed packet",
            130: "protocol error",
            131: "implementation-specific error",
            132: "unsupported protocol version",
            133: "client ID invalid",
            134: "bad username/password",
            135: "not authorized",
            136: "server unavailable",
            137: "server busy",
            138: "banned",
            140: "bad auth method",
            149: "packet too large",
            151: "quota exceeded",
            153: "payload format invalid",
        }
        reason = code_map.get(code, "connection/auth error")
        self.status_msg_label.setText(f"MQTT: offline ({code}) {reason}")
        self._layout_overlays()


    def _init_annotations(self):
        self._annotations: dict[str, Annotation] = {}
        self._active_annotation_type: str = ""
        self._annotation_sync = AnnotationSync(self._mqtt_client, read_only=self._viewer, parent=self)

        # mutual exclusion: opening one drawer closes the other
        self.btn_radar.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_radar.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_hazards.setChecked(False) if on else None
        )

        # tool selection → set cursor mode
        self.annotation_tools.tool_selected.connect(self._on_annotation_tool_selected)
        self.annotation_tools.refresh_requested.connect(self._on_refresh_current_json)

        # map click → place annotation (if tool is active)
        self.map_widget.map_clicked.connect(self._on_map_click)

        # annotation marker click → edit/delete dialog
        self.map_widget.annotation_clicked.connect(self._on_annotation_clicked)
        self.map_widget.annotation_drag_ended.connect(self._on_annotation_drag_end)
        self._moving_annotation_id = None

        # remote annotations arriving over MQTT — update map without re-publishing
        self._annotation_sync.annotation_received.connect(self._recv_remote_annotation)
        self._annotation_sync.annotation_deleted.connect(self._recv_remote_annotation_deleted)

        self._init_drawings()

    def _init_drawings(self):
        self._drawings: dict[str, DrawingAnnotation] = {}
        self._active_drawing_type: str = ""
        self._drawing_points: list = []
        self._moving_drawing_id: str | None = None
        self._moving_drawing_original_coordinates: list | None = None
        self._drawing_sync = DrawingSync(self._mqtt_client, read_only=self._viewer, parent=self)

        self.map_widget.map_double_clicked.connect(self._on_map_dblclick)
        self.map_widget.drawing_clicked.connect(self._on_drawing_clicked)
        self.map_widget.drawing_drag_ended.connect(self._on_drawing_drag_end)
        self._drawing_sync.drawing_received.connect(self._recv_remote_drawing)
        self._drawing_sync.drawing_deleted.connect(self._recv_remote_drawing_deleted)

    def _set_placement_prompt(self, msg: str, needs_click: bool = True):
        """Show an accent-colored status prompt."""
        suffix = "  —  click map to place" if needs_click else ""
        self.status_msg_label.setText(f"  ▶  {msg}{suffix}")
        self.status_msg_label.setStyleSheet(
            f"color: {ACCENT}; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        self._layout_overlays()

    def _clear_placement_prompt(self):
        self.status_msg_label.setText("")
        self.status_msg_label.setStyleSheet("")
        self._layout_overlays()

    def _on_annotation_tool_selected(self, type_key: str):
        # selecting any tool deactivates sounding mode
        if type_key and hasattr(self, "btn_sounding") and self.btn_sounding.isChecked():
            self.btn_sounding.setChecked(False)
        # cancel any in-progress drawing when tool switches
        if getattr(self, "_active_drawing_type", ""):
            self._cancel_drawing()

        # if the drawer is closing (type_key=="") but a storm motion fix is
        if (
            not type_key
            and self._active_annotation_type == "storm_motion"
            and self._pending_cone_motion_fix is not None
        ):
            return

        self._pending_cone_params = None
        self._pending_cone_motion_fix = None
        self._active_annotation_type = ""
        self._active_drawing_type = ""
        self.map_widget.set_storm_cone_placement_mode(False)

        if type_key in DRAWING_TYPE_MAP:
            # drawing tool (front or custom shape)
            self._active_drawing_type = type_key
            self.map_widget.set_annotation_mode(False)
            self.map_widget.set_drawing_mode(True, type_key)
            meta = DRAWING_TYPE_MAP[type_key]
            self._set_placement_prompt(
                f"{meta['label']} — click to add points, double-click to finish",
                needs_click=False,
            )
        elif type_key == "storm_motion":
            self._active_annotation_type = type_key
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(True)
            self._set_placement_prompt(
                "storm cone — select first radar feature position",
                needs_click=False,
            )
        elif type_key == "pressure_system":
            self._active_annotation_type = type_key
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(True)
            self._set_placement_prompt("pressure system")
        elif type_key:
            self._active_annotation_type = type_key
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(True)
            label = ANNOTATION_TYPE_MAP.get(type_key, {}).get("label", "annotation")
            self._set_placement_prompt(label)
        else:
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(False)
            self._clear_placement_prompt()

    def _on_map_click(self, lat: float, lon: float):
        if getattr(self, "_measure_active", False):
            self._on_measure_click(lat, lon)
            return
        if getattr(self, "_active_drawing_type", ""):
            self._on_drawing_click(lat, lon)
            return
        if self._active_annotation_type == "storm_motion":
            self._on_storm_motion_fix_click(lat, lon)
            return
        if self._active_annotation_type == "fork":
            # remove any existing fork annotations before placing new one
            existing_forks = [aid for aid, a in self._annotations.items() if a.type_key == "fork"]
            for fid in existing_forks:
                self._delete_annotation(fid)
            annotation = Annotation.new(type_key="fork", lat=lat, lon=lon)
            self._active_annotation_type = ""
            self.map_widget.set_annotation_mode(False)
            self.annotation_tools.deactivate_tool()
            self._clear_placement_prompt()
            self._place_annotation(annotation)
        elif self._active_annotation_type:
            dlg = AnnotationPlaceDialog(self._active_annotation_type, lat, lon, viewer_mode=self._viewer, parent=self)
            if dlg.exec() == AnnotationPlaceDialog.DialogCode.Accepted:
                annotation = Annotation.new(
                    type_key=dlg.result_type_key(),
                    lat=lat,
                    lon=lon,
                    label=dlg.result_label(),
                )
                self._place_annotation(annotation)

    def _on_annotation_clicked(self, annotation_id: str):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        dlg = AnnotationEditDialog(annotation, viewer_mode=self._viewer, parent=self)
        if dlg.exec() == AnnotationEditDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_annotation(annotation_id)
            elif dlg.action() == "save":
                annotation.label = dlg.result_label()
                self._update_annotation(annotation)
            elif dlg.action() == "move":
                self._moving_annotation_id = annotation_id
                self.map_widget.set_annotation_draggable(annotation_id, True)
                self.status_msg_label.setText("  ▶  Drag the annotation to its new location")
                self.status_msg_label.setStyleSheet(
                    f"color: {ACCENT}; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
                )

    def _on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        self.map_widget.set_annotation_draggable(annotation_id, False)
        self._moving_annotation_id = None
        self.status_msg_label.setText("")

        dlg = AnnotationMoveConfirmDialog(annotation, lat, lon, parent=self)
        if dlg.exec() == AnnotationMoveConfirmDialog.DialogCode.Accepted:
            annotation.lat = lat
            annotation.lon = lon
            self._update_annotation(annotation)
        else:
            # revert to original position
            self.map_widget.move_annotation(annotation_id, annotation.lat, annotation.lon)

    def _place_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_create(annotation)
        self._refresh_annotation_layer()
        log.info("annotation placed: %s at (%.4f, %.4f)", annotation.type_key, annotation.lat, annotation.lon)

    def _delete_annotation(self, annotation_id: str):
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._annotation_sync.publish_delete(annotation_id)
        self._refresh_annotation_layer()
        log.info("annotation deleted: %s", annotation_id)

    def _update_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        # re-add marker so label tooltip reflects new text
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_update(annotation)
        log.info("annotation updated: %s label=%s", annotation.id, annotation.label)

    def _recv_remote_annotation(self, annotation: Annotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._refresh_annotation_layer()
        log.info("remote annotation received: %s (%s)", annotation.id, annotation.type_key)

    def _recv_remote_annotation_deleted(self, annotation_id: str, deleted_at: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._refresh_annotation_layer()
        log.info("remote annotation deleted: %s at %s", annotation_id, deleted_at or "unknown")


    def _on_drawing_click(self, lat: float, lon: float):
        """Add a point to the in-progress drawing."""
        self._drawing_points.append([lat, lon])
        self.map_widget.drawing_update_preview(self._drawing_points)
        n = len(self._drawing_points)
        meta = DRAWING_TYPE_MAP.get(self._active_drawing_type, {})
        self._set_placement_prompt(
            f"{meta.get('label', '')} — {n} point{'s' if n != 1 else ''} — double-click to finish",
            needs_click=False,
        )

    def _on_map_dblclick(self, lat: float, lon: float):
        if not getattr(self, "_active_drawing_type", ""):
            return
        self._finalize_drawing(lat, lon)

    def _finalize_drawing(self, lat: float, lon: float):
        pts = self._drawing_points[:]
        while pts and _coords_close(pts[-1], (lat, lon)):
            pts.pop()
        pts.append([lat, lon])

        drawing_type = self._active_drawing_type

        if len(pts) < 2:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            meta = DRAWING_TYPE_MAP.get(drawing_type, {})
            self._set_placement_prompt(
                f"{meta.get('label', 'Drawing')} needs at least 2 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return
        if drawing_type == "polygon" and len(pts) < 3:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            self._set_placement_prompt(
                "Polygon needs at least 3 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return

        self._cancel_drawing()   # clear state + preview before showing dialog

        if drawing_type in ("polyline", "polygon"):
            dlg = DrawingTitleDialog(drawing_type, parent=self)
            if dlg.exec() != DrawingTitleDialog.DialogCode.Accepted:
                return
            title = dlg.title()
            color = dlg.color()
            line_style = dlg.line_style()
        else:
            title = DRAWING_TYPE_MAP.get(drawing_type, {}).get("label", drawing_type)
            color = None
            line_style = "solid"

        drawing = DrawingAnnotation.new(
            drawing_type=drawing_type,
            coordinates=pts,
            title=title,
            color=color,
            line_style=line_style,
        )
        dlg = DrawingPlaceConfirmDialog(drawing_type, len(pts), parent=self)
        if dlg.exec() != DrawingPlaceConfirmDialog.DialogCode.Accepted:
            return
        self._place_drawing(drawing)
        if drawing_type in FRONT_TYPE_KEYS:
            self.btn_annotate.setChecked(False)

    def _cancel_drawing(self):
        self._drawing_points.clear()
        self._active_drawing_type = ""
        self.map_widget.set_drawing_mode(False)
        self._clear_placement_prompt()

    def _on_escape_pressed(self):
        if not getattr(self, "_active_drawing_type", ""):
            return
        self._cancel_drawing()
        if hasattr(self, "annotation_tools"):
            self.annotation_tools.deactivate_tool()

    def _on_drawing_clicked(self, drawing_id: str):
        # ignore if a tool is currently active
        if self._active_annotation_type or getattr(self, "_active_drawing_type", ""):
            return
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        dlg = DrawingEditDialog(drawing, parent=self)
        if dlg.exec() != DrawingEditDialog.DialogCode.Accepted:
            return
        action = dlg.action()
        if action == "delete":
            self._delete_drawing(drawing_id)
        elif action == "flip":
            drawing.flipped = not drawing.flipped
            self._update_drawing(drawing)
        elif action == "move":
            self._moving_drawing_id = drawing_id
            self._moving_drawing_original_coordinates = [pt[:] for pt in drawing.coordinates]
            self.map_widget.set_drawing_draggable(drawing_id, True)
            self._set_placement_prompt("drag the drawing to its new location", needs_click=False)
        elif action == "save":
            drawing.title = dlg.result_title()
            drawing.color = dlg.result_color()
            drawing.line_style = dlg.result_line_style()
            self._update_drawing(drawing)

    def _on_drawing_drag_end(self, drawing_id: str, coordinates_json: str):
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        self.map_widget.set_drawing_draggable(drawing_id, False)
        self._moving_drawing_id = None
        try:
            new_coordinates = json.loads(coordinates_json)
        except json.JSONDecodeError:
            log.warning("drawing drag end parse failed for %s", drawing_id)
            self._clear_placement_prompt()
            self._update_drawing(drawing)
            return
        dlg = DrawingMoveConfirmDialog(drawing, new_coordinates, parent=self)
        if dlg.exec() == DrawingMoveConfirmDialog.DialogCode.Accepted:
            drawing.coordinates = new_coordinates
        elif self._moving_drawing_original_coordinates is not None:
            drawing.coordinates = [pt[:] for pt in self._moving_drawing_original_coordinates]
        self._moving_drawing_original_coordinates = None
        self._clear_placement_prompt()
        self._update_drawing(drawing)

    def _place_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_create(drawing)
        self._refresh_annotation_layer()
        log.info("drawing placed: %s at %d points", drawing.drawing_type, len(drawing.coordinates))

    def _delete_drawing(self, drawing_id: str):
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._drawing_sync.publish_delete(drawing_id)
        self._refresh_annotation_layer()
        log.info("drawing deleted: %s", drawing_id)

    def _update_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.remove_drawing(drawing.id)
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_update(drawing)
        log.info("drawing updated: %s", drawing.id)

    def _recv_remote_drawing(self, drawing: DrawingAnnotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._refresh_annotation_layer()
        log.info("remote drawing received: %s (%s)", drawing.id, drawing.drawing_type)

    def _recv_remote_drawing_deleted(self, drawing_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._refresh_annotation_layer()
        log.info("remote drawing deleted: %s", drawing_id)


    def _init_storm_cone(self):
        self._storm_cones: dict[str, StormCone] = {}
        self._pending_cone_params: dict | None = None
        self._pending_cone_motion_fix: dict | None = None
        self._moving_cone_id: str | None = None
        self._moving_cone_original_location: tuple[float, float] | None = None

        # cone placed via ANNOTATE drawer — map cone-click → edit dialog
        self.map_widget.storm_cone_clicked.connect(self._on_storm_cone_clicked)
        self.map_widget.storm_cone_drag_ended.connect(self._on_storm_cone_drag_end)
        self.map_widget.storm_cone_place_drag_ended.connect(self._on_storm_cone_place_drag_end)

        # remote cones arriving over MQTT — update map without re-publishing
        self._storm_cone_sync.cone_received.connect(self._recv_remote_storm_cone)
        self._storm_cone_sync.cone_deleted.connect(self._recv_remote_storm_cone_deleted)

        # auto-expire cones older than 1 hour (checked every 60 seconds).
        self._cone_expire_timer = QTimer(self)
        self._cone_expire_timer.setInterval(60_000)
        self._cone_expire_timer.timeout.connect(self._expire_storm_cones)
        self._cone_expire_timer.start()

    def _current_displayed_radar_scan(self):
        return self._current_radar_scan

    def _on_storm_motion_fix_click(self, lat: float, lon: float):
        scan = self._current_displayed_radar_scan()
        if scan is None:
            self._set_placement_prompt(
                "storm cone — show a radar frame before selecting motion",
                needs_click=False,
            )
            return

        scan_time = scan.scan_time
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)
        time_label = scan_time.strftime("%H:%MZ")

        if self._pending_cone_motion_fix is None:
            self._pending_cone_motion_fix = {
                "lat": lat,
                "lon": lon,
                "scan_time": scan_time,
                "site": getattr(scan, "site", ""),
                "product": getattr(scan, "product", ""),
                "time_label": time_label,
            }
            self._set_placement_prompt(
                f"storm cone — first fix {time_label}; move to a later radar frame and select the same feature",
                needs_click=False,
            )
            return

        first = self._pending_cone_motion_fix
        if getattr(scan, "site", "") != first.get("site", ""):
            self._set_placement_prompt(
                "storm cone — radar site changed; select the first fix again",
                needs_click=False,
            )
            self._pending_cone_motion_fix = None
            return

        try:
            motion = motion_from_fixes(
                first["lat"], first["lon"], first["scan_time"],
                lat, lon, scan_time,
            )
        except ValueError:
            self._set_placement_prompt(
                "storm cone — choose a radar frame later than the first fix",
                needs_click=False,
            )
            return

        if motion["distance_nm"] < 0.05:
            self._set_placement_prompt(
                "storm cone — second fix is too close; choose the same feature on a later frame",
                needs_click=False,
            )
            return

        speed_kts = motion["speed_kts"]
        heading = int(round(motion["heading"])) % 360
        dlg = StormConeMotionConfirmDialog(
            lat,
            lon,
            speed_kts,
            heading,
            first["time_label"],
            time_label,
            motion["distance_nm"],
            parent=self,
        )
        if dlg.exec() != StormConeMotionConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt(
                f"storm cone — first fix {first['time_label']}; select a second point",
                needs_click=False,
            )
            return

        cone = StormCone.new(
            lat,
            lon,
            heading=heading,
            speed_kts=speed_kts,
            valid_at=scan_time,
        )
        self._pending_cone_motion_fix = None
        self._pending_cone_params = None
        self._active_annotation_type = ""
        self.map_widget.set_annotation_mode(False)
        self.map_widget.set_storm_cone_placement_mode(False)
        self.annotation_tools.deactivate_tool()
        self._clear_placement_prompt()
        self._place_storm_cone(cone)

    def _on_storm_cone_clicked(self, cone_id: str):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        dlg = StormConeInputDialog(
            edit_mode=True,
            speed_kts=cone.speed_kts,
            heading=int(cone.heading),
            parent=self,
        )
        if dlg.exec() == StormConeInputDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_storm_cone(cone_id)
            elif dlg.action() == "save":
                cone.speed_kts = dlg.speed_kts()
                cone.heading = dlg.heading()
                cone.created_at = datetime.now(timezone.utc)
                self._update_storm_cone(cone)
            elif dlg.action() == "move":
                self._moving_cone_id = cone_id
                self._moving_cone_original_location = (cone.lat, cone.lon)
                self.map_widget.set_storm_cone_draggable(cone_id, True)
                self._set_placement_prompt("drag the storm cone to its new location", needs_click=False)

    def _on_storm_cone_drag_end(self, cone_id: str, lat: float, lon: float):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        self.map_widget.set_storm_cone_draggable(cone_id, False)
        self._moving_cone_id = None
        dlg = StormConeMoveConfirmDialog(
            self._moving_cone_original_location[0] if self._moving_cone_original_location else cone.lat,
            self._moving_cone_original_location[1] if self._moving_cone_original_location else cone.lon,
            lat,
            lon,
            parent=self,
        )
        if dlg.exec() == StormConeMoveConfirmDialog.DialogCode.Accepted:
            cone.lat = lat
            cone.lon = lon
            cone.created_at = datetime.now(timezone.utc)
        elif self._moving_cone_original_location is not None:
            cone.lat, cone.lon = self._moving_cone_original_location
        self._moving_cone_original_location = None
        self._clear_placement_prompt()
        self._update_storm_cone(cone)

    def _on_storm_cone_place_drag_end(self, lat: float, lon: float):
        if self._pending_cone_params is None:
            return
        dlg = StormConePlaceConfirmDialog(
            lat,
            lon,
            self._pending_cone_params["speed_kts"],
            self._pending_cone_params["heading"],
            parent=self,
        )
        if dlg.exec() != StormConePlaceConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt("storm cone — click and drag to place", needs_click=False)
            return
        cone = StormCone.new(lat, lon, **self._pending_cone_params)
        self._pending_cone_params = None
        self._active_annotation_type = ""
        self.map_widget.set_storm_cone_placement_mode(False)
        self.annotation_tools.deactivate_tool()
        self._clear_placement_prompt()
        self._place_storm_cone(cone)

    def _place_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._storm_cone_sync.publish_create(cone)
        self._refresh_storm_cone_layer()
        log.info("storm cone placed: id=%s lat=%.4f lon=%.4f hdg=%.0f spd=%.0f",
                 cone.id, cone.lat, cone.lon, cone.heading, cone.speed_kts)

    def _delete_storm_cone(self, cone_id: str):
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._storm_cone_sync.publish_delete(cone_id)
        self._refresh_storm_cone_layer()
        log.info("storm cone deleted: %s", cone_id)

    def _update_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)   # re-add rebuilds geometry
        self._storm_cone_sync.publish_update(cone)
        log.info("storm cone updated: id=%s hdg=%.0f spd=%.0f",
                 cone.id, cone.heading, cone.speed_kts)

    def _recv_remote_storm_cone(self, cone: StormCone):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone received: %s", cone.id)

    def _recv_remote_storm_cone_deleted(self, cone_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone deleted: %s", cone_id)

    def _expire_storm_cones(self):
        """Remove any storm cone whose created_at is more than 1 hour ago.

        Called every 60 s by _cone_expire_timer.  Expired cones are silently
        removed from the map and dict — no MQTT delete is published, because
        the broker's own 1-hour message-expiry will have already purged the
        retained message on the broker side.
        """
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        expired = [
            cone_id
            for cone_id, cone in list(self._storm_cones.items())
            if cone.created_at < cutoff
        ]
        for cone_id in expired:
            self._storm_cones.pop(cone_id, None)
            self.map_widget.remove_storm_cone(cone_id)
            log.info("storm cone auto-expired (>1 h): %s", cone_id)
        if expired:
            self._refresh_storm_cone_layer()


    def _init_measure(self):
        self._measure_active = False
        self._measure_has_anchor = False
        self._measure_complete = False

        # mutual exclusion: MEASURE, ANNOTATE, and SOUNDING all consume map clicks
        self.btn_measure.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_measure.toggled.connect(
            lambda on: self.btn_sounding.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_measure.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_sounding.setChecked(False) if on else None
        )
        self.btn_measure.toggled.connect(self._on_measure_toggled)

    def _on_measure_toggled(self, active: bool):
        if active:
            self._measure_active = True
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(True)
            self._set_placement_prompt("measure — click first point")
        else:
            # if user exits mid-measure after first point, clear partial artifacts.
            if self._measure_has_anchor or self._measure_complete:
                self.map_widget.clear_measure()
            self._measure_active = False
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(False)
            self._clear_placement_prompt()

    def _on_measure_click(self, lat: float, lon: float):
        if self._measure_complete:
            self._set_placement_prompt("measure complete — toggle off to clear", needs_click=False)
            return

        self.map_widget.measure_click(lat, lon)
        if not self._measure_has_anchor:
            self._measure_has_anchor = True
            self._set_placement_prompt("measure — click second point")
        else:
            # second point placed — keep tool selected so user can toggle off to clear.
            self._measure_has_anchor = False
            self._measure_complete = True
            self.map_widget.set_measure_mode(False)   # reset cursor while keeping line visible
            self._set_placement_prompt("measure complete — toggle off to clear", needs_click=False)


    def _init_stations(self):
        self._vehicles: dict[str, Vehicle] = {}
        self._vehicle_history: dict[str, deque] = {}  # vehicle_id → deque[Observation]
        self._vehicle_timeseries_dlg: "VehicleTimeseriesDialog | None" = None
        self._scan_sectors: dict[str, ScanSector] = {}
        self._scan_move_count = 0
        self._last_scan_publish = 0.0
        self._follow_mode = False
        self._station_layer = StationPlotLayer(self.map_widget)
        self._chk_station_plots.toggled.connect(self._station_layer.set_visible)
        self.map_widget.user_dragged.connect(self._on_user_dragged)
        # station plots on by default — delayed until map is ready
        QTimer.singleShot(1200, lambda: self._station_layer.set_visible(
            self._chk_station_plots.isChecked()
        ))

        self.btn_recenter = QToolButton(self._map_container)
        self.btn_recenter.setIcon(_make_loc_icon(18))
        self.btn_recenter.setIconSize(QSize(18, 18))
        self.btn_recenter.setToolTip("Re-center on vehicle")
        self.btn_recenter.setFixedSize(32, 32)
        self.btn_recenter.setStyleSheet("""
            QToolButton {
                background: #1E2433;
                border: 1px solid #2A3045;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: #252D42;
                border-color: #4A9EFF;
            }
            QToolButton:pressed {
                background: #1A1F30;
            }
        """)
        self.btn_recenter.clicked.connect(self._on_recenter_clicked)
        self.btn_recenter.hide()


    def _apply_deploy_locs_filter_on_show(self, visible: bool):
        """Apply the current threshold filter whenever the layer is toggled on."""
        if visible:
            self.map_widget.set_deploy_locs_filter(
                self.deploy_locs_controls.current_metric(),
                self.deploy_locs_controls.current_threshold(),
            )

    def _init_deploy_locs(self):
        if config.DEPLOY_LOCS_FILE:
            QTimer.singleShot(1200, self._load_deploy_locs)

    def _load_deploy_locs(self):
        try:
            with open(config.DEPLOY_LOCS_FILE, newline='') as f:
                reader = csv.DictReader(f)
                points = [
                    {
                        "lat": float(r["lat"]),
                        "lon": float(r["lon"]),
                        "rank_abi": int(r["rank_abi"]) if r["rank_abi"] else None,
                        "rank_aoi": int(r["rank_aoi"]) if r["rank_aoi"] else None,
                        "rqi": float(r["rqi"]) if r["rqi"] else None,
                    }
                    for r in reader
                ]
            self.map_widget.load_deploy_locs(points)
            log.info("deploy locs: loaded %d points from %s", len(points), config.DEPLOY_LOCS_FILE)
        except Exception as e:
            log.warning("deploy locs: could not load %s: %s", config.DEPLOY_LOCS_FILE, e)

    def update_vehicle_obs(self, obs: Observation) -> None:
        """Public entry point for all vehicle observation updates (MQTT, file watcher, GPS)."""
        self._maybe_seed_initial_radar_site(obs)
        if not self._should_display_vehicle_obs(obs):
            self._hide_vehicle(obs.vehicle_id)
            return

        existing = self._vehicles.get(obs.vehicle_id)
        if obs.vehicle_id == config.VEHICLE_ID:
            icon_type = config.VEHICLE_ICON
        else:
            icon_type = getattr(obs, "icon_type", None) or (existing.icon_type if existing else "car")
        v = self._vehicles.setdefault(
            obs.vehicle_id,
            Vehicle(id=obs.vehicle_id, lat=obs.lat, lon=obs.lon, icon_type=icon_type),
        )
        v.icon_type = icon_type
        v.lat, v.lon, v.latest_obs = obs.lat, obs.lon, obs
        
        # append to vehicle history if this is NOT the local vehicle and has met data
        if obs.vehicle_id != config.VEHICLE_ID:
            has_met_data = any([
                obs.temperature_c is not None,
                obs.dewpoint_c is not None,
                obs.wind_speed_ms is not None,
                obs.pressure_mb is not None,
            ])
            if has_met_data:
                if obs.vehicle_id not in self._vehicle_history:
                    self._vehicle_history[obs.vehicle_id] = deque()
                self._vehicle_history[obs.vehicle_id].append(obs)
                # live-update timeseries dialog if open
                if (self._vehicle_timeseries_dlg is not None
                        and self._vehicle_timeseries_dlg.isVisible()):
                    self._vehicle_timeseries_dlg.update_vehicle(
                        obs.vehicle_id,
                        list(self._vehicle_history[obs.vehicle_id]))

        marker_color = self._obs_age_color(obs)
        age_label = self._obs_age_label(obs)
        self._vehicle_age_display_state[obs.vehicle_id] = (marker_color, age_label)
        hover_text = self._archive_vehicle_hover_text(obs.vehicle_id)
        self.map_widget.add_vehicle(
            obs.vehicle_id,
            obs.lat,
            obs.lon,
            marker_color,
            v.icon_type,
            hover_text=hover_text,
        )
        if obs.vehicle_id == config.VEHICLE_ID and hasattr(self, "routing_controls"):
            self.routing_controls.update_own_position(obs.lat, obs.lon)
            if not self._viewer and not self._monitor and not self._archive and obs.lat is not None and obs.lon is not None:
                self.map_widget.set_private_pin_own_location(obs.lat, obs.lon)
        self._sync_routing_vehicle_snapshot()
        count = len(self._vehicles)
        self.update_vehicle_count(count)
        if hasattr(self, "_vehicle_placeholder"):
            self._vehicle_placeholder.setVisible(False)
        self._refresh_vehicle_panel()
        self._station_layer.update(obs.vehicle_id, obs.lat, obs.lon, obs)
        self._refresh_vehicle_detail()
        self._update_recenter_btn_visibility()
        if self._follow_mode and obs.vehicle_id == self._follow_target_id():
            self.map_widget.follow_move(obs.lat, obs.lon)
        if obs.vehicle_id == config.VEHICLE_ID:
            self._update_local_scan_from_vehicle(obs)

    def _archive_vehicle_hover_text(self, vehicle_id: str) -> str:
        """Build the admin vehicle name and ground-speed tooltip (archive or live)."""
        if not runtime_flags.FLAGS.admin_mode:
            return vehicle_id

        if self._archive:
            if not hasattr(self, "_time_ctrl"):
                return vehicle_id
            archive_time = self._time_ctrl.current_time
            dense_obs = getattr(self, "_archive_vehicle_obs", None)
            if (
                dense_obs is not None
                and dense_obs.has_fresh_observation(vehicle_id, archive_time)
            ):
                speed = dense_obs.speed(vehicle_id, archive_time)
            elif hasattr(self, "_archive_mqtt"):
                speed = self._archive_mqtt.vehicle_speed(vehicle_id, archive_time)
            else:
                return vehicle_id
        else:
            history = self._vehicle_history.get(vehicle_id)
            if not history:
                return vehicle_id
            now = datetime.now(tz=timezone.utc)
            speed = calculate_vehicle_speed(
                list(history), now, short_seconds=10, average_seconds=30
            )

        return f"{vehicle_id}\n{format_vehicle_speed(speed)}"

    def _follow_target_id(self) -> str | None:
        """Return the vehicle ID to follow: local vehicle, first selected, or sole vehicle."""
        if config.VEHICLE_ID in self._vehicles:
            return config.VEHICLE_ID
        if self._selected_vehicle_ids:
            return self._selected_vehicle_ids[0]
        if len(self._vehicles) == 1:
            return next(iter(self._vehicles))
        return None

    def _set_follow_mode(self, enabled: bool) -> None:
        self._follow_mode = enabled
        self.map_widget.set_follow(enabled)
        self._update_recenter_btn_visibility()

    def _on_recenter_clicked(self) -> None:
        """Re-engage follow mode from the floating re-center button."""
        self._set_follow_mode(True)
        if self._follow_target_id():
            target = self._vehicles.get(self._follow_target_id())
            if target:
                self.map_widget.follow_move(target.lat, target.lon)

    def _init_screenshot_button(self) -> None:
        self.btn_screenshot = QToolButton(self._map_container)
        self.btn_screenshot.setIcon(_make_camera_icon(18))
        self.btn_screenshot.setIconSize(QSize(18, 18))
        self.btn_screenshot.setToolTip("Save a screenshot of the map")
        self.btn_screenshot.setFixedSize(32, 32)
        self.btn_screenshot.setStyleSheet("""
            QToolButton {
                background: #1E2433;
                border: 1px solid #2A3045;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: #252D42;
                border-color: #4A9EFF;
            }
            QToolButton:pressed {
                background: #1A1F30;
            }
        """)
        self.btn_screenshot.clicked.connect(self._on_screenshot_clicked)
        self.btn_screenshot.show()

    def _init_scan_button(self) -> None:
        self.btn_scan = QToolButton(self._map_container)
        self.btn_scan.setText("SCAN")
        self.btn_scan.setToolTip("Start or stop current data collection footprint")
        self.btn_scan.setFixedSize(58, 32)
        self.btn_scan.setStyleSheet("""
            QToolButton {
                background: #1E2433;
                border: 1px solid #2A3045;
                border-radius: 4px;
                color: #C8D0DE;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }
            QToolButton:hover {
                background: #252D42;
                border-color: #00CFFF;
                color: #00CFFF;
            }
            QToolButton[active="true"] {
                background: rgba(0, 207, 255, 0.18);
                border-color: #00CFFF;
                color: #00CFFF;
            }
        """)
        self.btn_scan.clicked.connect(self._on_scan_button_clicked)
        self.btn_scan.setVisible(not (self._monitor or self._viewer or self._archive))

    def _on_scan_button_clicked(self) -> None:
        vehicle = self._vehicles.get(config.VEHICLE_ID)
        current = self._scan_sectors.get(config.VEHICLE_ID)
        if current and current.active:
            lat, lon = current.lat, current.lon
        elif vehicle is not None:
            lat, lon = vehicle.lat, vehicle.lon
        else:
            lat, lon = config.HOME_LAT, config.HOME_LON
            self.status_msg_label.setText("Scan: no local GPS yet; using configured home location")
            self._layout_overlays()

        dlg = ScanSectorDialog(
            vehicle_id=config.VEHICLE_ID,
            lat=lat,
            lon=lon,
            active_scan=current,
            parent=self,
        )
        if not dlg.exec():
            return

        if dlg.action == "stop":
            self._deactivate_local_scan()
        elif dlg.action == "apply":
            self._activate_local_scan(dlg.scan_sector())

    def _activate_local_scan(self, scan: ScanSector) -> None:
        self._scan_sectors[scan.vehicle_id] = scan
        self._scan_move_count = 0
        self._last_scan_publish = time.monotonic()
        self._refresh_scan_sectors()
        self._publish_scan(scan)
        self._update_scan_button()

    def _deactivate_local_scan(self, reason: str = "") -> None:
        existing = self._scan_sectors.pop(config.VEHICLE_ID, None)
        if existing is None:
            self._refresh_scan_sectors()
            self._update_scan_button()
            return
        inactive = ScanSector(
            vehicle_id=config.VEHICLE_ID,
            active=False,
            mode=existing.mode,
            lat=existing.lat,
            lon=existing.lon,
            range_m=existing.range_m,
            inner_range_m=existing.inner_range_m,
            azimuth_deg=existing.azimuth_deg,
            beam_width_deg=existing.beam_width_deg,
            follow_vehicle=existing.follow_vehicle,
        )
        self._publish_scan(inactive)
        self._scan_move_count = 0
        self._refresh_scan_sectors()
        self._update_scan_button()
        if reason:
            self.status_msg_label.setText(reason)
            self._layout_overlays()

    def _recv_remote_scan_sector(self, scan: ScanSector) -> None:
        if not (self._monitor or self._viewer or self._archive) and scan.vehicle_id == config.VEHICLE_ID:
            return
        if scan.active:
            self._scan_sectors[scan.vehicle_id] = scan
        else:
            self._scan_sectors.pop(scan.vehicle_id, None)
        self._refresh_scan_sectors()

    def _publish_scan(self, scan: ScanSector) -> None:
        sync = getattr(self, "_scan_sector_sync", None)
        if sync is not None:
            sync.publish(scan)

    def _refresh_scan_sectors(self) -> None:
        self.map_widget.set_scan_sectors_geojson(
            feature_collection(list(self._scan_sectors.values()))
        )
        self._set_layer_active("scan_sectors", bool(self._scan_sectors))

    def _update_scan_button(self) -> None:
        if not hasattr(self, "btn_scan"):
            return
        active = config.VEHICLE_ID in self._scan_sectors
        self.btn_scan.setProperty("active", "true" if active else "false")
        self.btn_scan.setText("SCAN ON" if active else "SCAN")
        scan = self._scan_sectors.get(config.VEHICLE_ID)
        if scan and scan.active:
            desc = scan.mode.upper()
            if scan.range_m:
                desc += f" {scan.range_m / 1000:.1f} km"
            self.btn_scan.setToolTip(f"Current sampling: {desc}")
        else:
            self.btn_scan.setToolTip("Start or stop current data collection footprint")
        self.btn_scan.style().unpolish(self.btn_scan)
        self.btn_scan.style().polish(self.btn_scan)
        self.btn_scan.adjustSize()
        self.btn_scan.setFixedSize(max(58, self.btn_scan.sizeHint().width() + 10), 32)
        self._layout_overlays()

    def _update_local_scan_from_vehicle(self, obs: Observation) -> None:
        scan = self._scan_sectors.get(config.VEHICLE_ID)
        if scan is None or not scan.active:
            return
        if scan.follow_vehicle:
            scan.lat = obs.lat
            scan.lon = obs.lon
            scan.timestamp = datetime.now(timezone.utc)
            self._refresh_scan_sectors()
            now = time.monotonic()
            if now - self._last_scan_publish >= config.OBS_MQTT_PUBLISH_S:
                self._publish_scan(scan)
                self._last_scan_publish = now
            return

        dist_km = self._haversine_km(scan.lat, scan.lon, obs.lat, obs.lon)
        if dist_km > SCAN_MOVE_THRESHOLD_KM:
            self._scan_move_count += 1
        else:
            self._scan_move_count = 0
        if self._scan_move_count >= SCAN_MOVE_FIXES_TO_STOP:
            self._deactivate_local_scan("Scan stopped: vehicle moved away from the collection point")

    def _on_screenshot_clicked(self) -> None:
        """Hide the floating toolbar, capture the window, then prompt to save."""
        to_hide = [self.btn_screenshot]
        if hasattr(self, "_floating_toolbar"):
            to_hide.append(self._floating_toolbar)
        prev_visible = [(w, w.isVisible()) for w in to_hide]
        for w, _ in prev_visible:
            w.hide()
        # let Qt repaint without the hidden widgets before grabbing.
        QApplication.processEvents()

        try:
            screen = self.windowHandle().screen() if self.windowHandle() else QApplication.primaryScreen()
            if screen is None:
                log.warning("Screenshot: no screen available")
                return
            # grab the main window rect in screen coordinates.
            top_left = self.mapToGlobal(self.rect().topLeft())
            dpr = screen.devicePixelRatio()
            pix = screen.grabWindow(
                0,
                int(top_left.x()),
                int(top_left.y()),
                int(self.width()),
                int(self.height()),
            )
            if not pix.isNull():
                pix.setDevicePixelRatio(dpr)
        finally:
            for w, was_visible in prev_visible:
                if was_visible:
                    w.show()
            self._layout_overlays()

        if pix.isNull():
            log.warning("Screenshot: grabbed pixmap was null")
            return

        from pathlib import Path
        default_dir = Path.home() / "Pictures"
        if not default_dir.exists():
            default_dir = Path.home()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = str(default_dir / f"storm_{ts}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", default_path, "PNG Image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not pix.save(path, "PNG"):
            log.warning("Screenshot: failed to save to %s", path)
        else:
            log.info("Screenshot saved to %s", path)

    def _update_recenter_btn_visibility(self) -> None:
        if not hasattr(self, "btn_recenter"):
            return
        visible = not self._follow_mode and self._follow_target_id() is not None
        self.btn_recenter.setVisible(visible)

    def _on_user_dragged(self) -> None:
        """Called when JS detects a map drag — disengage follow mode."""
        if self._follow_mode:
            self._follow_mode = False
            self.map_widget.set_follow(False)
            self._update_recenter_btn_visibility()

    def _maybe_seed_initial_radar_site(self, obs: Observation) -> None:
        if not self._radar_auto_site_pending or self._monitor:
            return
        if obs.vehicle_id != config.VEHICLE_ID:
            return
        if not hasattr(self, "radar_controls"):
            return
        nearest = self._nearest_radar_site(obs.lat, obs.lon)
        self._radar_auto_site_pending = False
        if nearest != self.radar_controls.current_site():
            self._select_radar_site(nearest, user_selected=False)

    def _should_display_vehicle_obs(self, obs: Observation) -> bool:
        return self._obs_age_minutes(obs) <= 10.0 * 60.0

    def _sync_routing_vehicle_snapshot(self) -> None:
        if hasattr(self, "routing_controls"):
            self.routing_controls.update_vehicles(self._vehicles)

    def _hide_vehicle(self, vehicle_id: str) -> None:
        removed = self._vehicles.pop(vehicle_id, None)
        self._vehicle_age_display_state.pop(vehicle_id, None)
        if removed is None:
            return

        self.map_widget.remove_vehicle(vehicle_id)
        self._station_layer.remove(vehicle_id)
        self._sync_routing_vehicle_snapshot()
        if vehicle_id in self._scan_sectors:
            self._scan_sectors.pop(vehicle_id, None)
            self._refresh_scan_sectors()
            self._update_scan_button()
        if vehicle_id in self._selected_vehicle_ids:
            self._selected_vehicle_ids = [vid for vid in self._selected_vehicle_ids if vid != vehicle_id]
        self.update_vehicle_count(len(self._vehicles))
        self._refresh_vehicle_panel()
        self._refresh_vehicle_detail()
        self._sync_vehicle_detail_visibility()
        self._update_recenter_btn_visibility()

    def _obs_age_minutes(self, obs: Observation) -> float:
        ref = (
            self._time_ctrl.current_time
            if self._archive and hasattr(self, "_time_ctrl")
            else datetime.now(timezone.utc)
        )
        age = ref - obs.timestamp
        return max(0.0, age.total_seconds() / 60.0)

    def _obs_age_color(self, obs: Observation) -> str:
        age_min = self._obs_age_minutes(obs)
        if age_min <= 1.0:
            return "#39D98A"  # fresh
        if age_min <= 3.0:
            return "#FFD166"  # caution
        if age_min <= 5.0:
            return "#FF9F43"  # aging
        return "#E53935"      # stale

    def _obs_age_label(self, obs: Observation) -> str:
        age_min = self._obs_age_minutes(obs)
        if age_min < 1.0:
            return "<1m"
        if age_min < 60.0:
            return f"{age_min:.0f}m"
        hours = age_min / 60.0
        return f"{hours:.1f}h"

    def _obs_status_sort_key(self, v) -> int:
        """Return a sort key based on ob status color: 0=green, 1=yellow, 2=orange, 3=red, 4=no obs."""
        if not v.latest_obs:
            return 4
        age_min = self._obs_age_minutes(v.latest_obs)
        if age_min <= 1.0:
            return 0
        if age_min <= 3.0:
            return 1
        if age_min <= 5.0:
            return 2
        return 3

    def _on_vehicle_panel_toggled(self, checked: bool) -> None:
        """Reset the size watermark when the panel is hidden so the next
        open starts fresh and re-grows from current content."""
        if not checked:
            self._vehicle_panel_size_watermark = (0, 0)

    def _refresh_vehicle_panel(self):
        if not hasattr(self, "_vehicle_rows_layout"):
            return

        rebuilt_rows = False
        self.vehicle_panel.setUpdatesEnabled(False)
        try:
            self._vehicle_row_age_labels.clear()
            _clear_layout(self._vehicle_rows_layout)

            if not self._vehicles:
                self._vehicle_rows_widget.setVisible(False)
                if hasattr(self, "_vehicle_placeholder"):
                    self._vehicle_placeholder.setVisible(True)
                self._vehicle_rows_layout.invalidate()
                self.vehicle_panel.layout().invalidate()
                rebuilt_rows = True
                return

            self._vehicle_rows_widget.setVisible(True)
            if hasattr(self, "_vehicle_placeholder"):
                self._vehicle_placeholder.setVisible(False)

            # 1. group vehicles by their icon_type
            grouped_vehicles = {}
            for v in self._vehicles.values():
                icon = v.icon_type if v.icon_type else "unknown"
                grouped_vehicles.setdefault(icon, []).append(v)

            # 2. sort vehicles within each group by ob status color
            for icon in grouped_vehicles:
                grouped_vehicles[icon].sort(key=self._obs_status_sort_key)

            # 3. lay icon groups out as side-by-side columns
            for icon, vehicles in sorted(grouped_vehicles.items()):
                col_widget = QWidget()
                col_widget.setMinimumWidth(180)
                col_layout = QVBoxLayout(col_widget)
                col_layout.setContentsMargins(0, 0, 0, 0)
                col_layout.setSpacing(0)
                col_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

                header = QLabel(icon.upper())
                header.setStyleSheet("color: #6E7A8F; font-size: 10px; font-weight: bold; padding-bottom: 4px; padding-left: 4px;")
                col_layout.addWidget(header)

                for v in vehicles:
                    col_layout.addWidget(self._make_vehicle_row(v))

                self._vehicle_rows_layout.addWidget(col_widget)

            # invalidate cached size hints so the panel reflects the rebuild
            self._vehicle_rows_layout.invalidate()
            self.vehicle_panel.layout().invalidate()
            self._vehicle_rows_widget.updateGeometry()
            self.vehicle_panel.updateGeometry()
            rebuilt_rows = True
        finally:
            # re-enable updates BEFORE _layout_overlays so adjustSize() reads
            # fresh size hints, not the stale ones cached during the rebuild.
            self.vehicle_panel.setUpdatesEnabled(True)
            if rebuilt_rows:
                self._layout_overlays()

    def _make_vehicle_row(self, v) -> QWidget:
        obs = v.latest_obs
        selected = v.id in self._selected_vehicle_ids

        row = QFrame()
        row.setStyleSheet(
            "QFrame { background-color: rgba(74,158,255,0.08); border-bottom: 1px solid #1E2434; }"
            if selected else
            "QFrame { background: transparent; border-bottom: 1px solid #1E2434; }"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 5, 0, 5)
        rl.setSpacing(6)

        badge_color = self._obs_age_color(obs) if obs else "#6E7A8F"
        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        badge.setFixedWidth(12)
        rl.addWidget(badge)

        name_btn = QPushButton(v.id)
        name_btn.setFlat(True)
        name_btn.setToolTip(v.id)
        name_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        name_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #E8EAF0; "
            "font-weight: 600; font-size: 10px; padding: 0; text-align: left; }"
            "QPushButton:hover { color: #4A9EFF; }"
        )
        name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        name_btn.clicked.connect(lambda checked=False, vid=v.id: self._on_vehicle_row_clicked(vid))
        rl.addWidget(name_btn)

        if obs is None:
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet("color: #9DA6B8; font-size: 10px; background: transparent; border: none;")
            no_obs.setFixedWidth(78)
            rl.addWidget(no_obs)
        else:
            sep = QLabel("·")
            sep.setStyleSheet("color: #6E7A8F; background: transparent; border: none;")
            sep.setFixedWidth(6)
            rl.addWidget(sep)
            age = QLabel(f"{self._obs_age_label(obs)} old")
            age.setStyleSheet("color: #C8D0DE; font-size: 10px; background: transparent; border: none;")
            age.setFixedWidth(42)
            rl.addWidget(age)
            self._vehicle_row_age_labels[v.id] = age

        rl.addStretch()
        return row

    def _on_vehicle_row_clicked(self, vid: str):
        if vid in self._selected_vehicle_ids:
            self._selected_vehicle_ids.remove(vid)
        else:
            self._selected_vehicle_ids.append(vid)
        v = self._vehicles.get(vid)
        if v is not None:
            self.map_widget.fly_to(v.lat, v.lon, zoom=13)
        self._refresh_vehicle_panel()
        self._refresh_vehicle_detail()
        self._sync_vehicle_detail_visibility()
        self._layout_overlays()

    def _on_timeseries_button_clicked(self, vehicle_id: str):
        """Open (or re-load) the shared timeseries dialog for the given vehicle."""
        # collect all vehicles that have met history
        if self._archive:
            if not hasattr(self, "_archive_mqtt"):
                return
            all_vehicles = {
                vid: self._get_archive_vehicle_history(vid)
                for vid in self._vehicles
            }
        else:
            all_vehicles = {
                vid: list(hist)
                for vid, hist in self._vehicle_history.items()
                if hist
            }

        # drop vehicles with no usable observations
        all_vehicles = {v: obs for v, obs in all_vehicles.items() if obs}

        if vehicle_id not in all_vehicles:
            return

        if self._vehicle_timeseries_dlg is None:
            self._vehicle_timeseries_dlg = VehicleTimeseriesDialog(self)

        self._vehicle_timeseries_dlg.load(vehicle_id, all_vehicles)
    
    def _get_archive_vehicle_history(self, vehicle_id: str) -> list:
        """Extract vehicle observation history from archive MQTT data."""
        if not hasattr(self, "_archive_mqtt") or not hasattr(self, "_time_ctrl"):
            return []

        dense_obs = getattr(self, "_archive_vehicle_obs", None)
        if (
            dense_obs is not None
            and vehicle_id in dense_obs.available_vehicle_ids
        ):
            return dense_obs.history(vehicle_id, self._time_ctrl.current_time)
        
        from network import vehicle_sync
        
        current_time = self._time_ctrl.current_time
        observations = []
        
        # archive MQTT records are stored as list of (timestamp, dict) tuples
        vehicle_records = self._archive_mqtt._data.get("vehicles", [])
        for ts, record in vehicle_records:
            # only include records up to current archive time
            if ts > current_time:
                break
            
            if record.get("vehicle_id") != vehicle_id:
                continue
            
            # convert to Observation
            try:
                obs = vehicle_sync._observation_from_payload(record)
                # only include if has met data
                if (obs.temperature_c is not None or obs.dewpoint_c is not None or 
                    obs.wind_speed_ms is not None or obs.pressure_mb is not None):
                    observations.append(obs)
            except Exception:
                continue
        
        return observations

    def _sync_vehicle_detail_visibility(self):
        if not hasattr(self, "vehicle_detail_panel"):
            return
        if not self.btn_vehicles.isChecked():
            self.vehicle_detail_panel.hide()
            return
        if not self._selected_vehicle_ids:
            self.vehicle_detail_panel.hide()
            return
        self.vehicle_detail_panel.show()

    def _refresh_vehicle_detail(self):
        if not hasattr(self, "vehicle_detail_panel"):
            return
        if not self._selected_vehicle_ids:
            self._vehicle_detail_title.setText("VEHICLE")
            _clear_layout(self._vehicle_detail_body_layout)
            return
        self._vehicle_detail_title.setText(
            f"DETAILS ({len(self._selected_vehicle_ids)})"
        )
        _clear_layout(self._vehicle_detail_body_layout)
        for vid in self._selected_vehicle_ids:
            vehicle = self._vehicles.get(vid)
            section = self._make_vehicle_detail_section(vid, vehicle)
            self._vehicle_detail_body_layout.addWidget(section)
        self._layout_overlays()

    def _make_vehicle_detail_section(self, vid: str, vehicle) -> QWidget:
        obs = vehicle.latest_obs if vehicle else None

        section = QFrame()
        section.setStyleSheet("QFrame { background: transparent; border-bottom: 1px solid #1E2434; }")
        sl = QVBoxLayout(section)
        sl.setContentsMargins(0, 6, 0, 6)
        sl.setSpacing(4)

        if obs is None:
            top = QHBoxLayout()
            name = QLabel(vid)
            name.setStyleSheet("color: #E8EAF0; font-weight: 600; background: transparent; border: none;")
            top.addWidget(name)
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet("color: #9DA6B8; margin-left: 6px; background: transparent; border: none;")
            top.addWidget(no_obs)
            top.addStretch()
            sl.addLayout(top)
            return section

        badge_color = self._obs_age_color(obs)

        # top row: badge · name · age · TIMESERIES button · lat/lon
        top = QHBoxLayout()
        top.setSpacing(8)

        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        top.addWidget(badge)

        name_lbl = QLabel(vid)
        name_lbl.setStyleSheet("color: #E8EAF0; font-weight: 600; background: transparent; border: none;")
        top.addWidget(name_lbl)

        age_lbl = QLabel(f"{self._obs_age_label(obs)} old")
        age_lbl.setStyleSheet("color: #C8D0DE; background: transparent; border: none;")
        top.addWidget(age_lbl)
        
        # timeseries button — only shown if vehicle has met data history
        has_history = vid in self._vehicle_history and len(self._vehicle_history[vid]) > 0
        if has_history:
            ts_btn = QPushButton("TIMESERIES")
            ts_btn.setFlat(True)
            ts_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #4A9EFF; "
                "color: #4A9EFF; font-size: 8px; font-weight: 600; padding: 2px 6px; "
                "border-radius: 3px; }"
                "QPushButton:hover { background: rgba(74,158,255,0.1); }"
            )
            ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ts_btn.clicked.connect(lambda checked=False, v=vid: self._on_timeseries_button_clicked(v))
            top.addWidget(ts_btn)

        top.addStretch()

        for key, val in [("lat", f"{obs.lat:.4f}"), ("lon", f"{obs.lon:.4f}")]:
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet("color: #C8D0DE; background: transparent; border: none;")
            top.addWidget(k_lbl)
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet("color: #E8EAF0; background: transparent; border: none;")
            top.addWidget(v_lbl)

        sl.addLayout(top)

        # timestamp
        ts = obs.timestamp.astimezone(timezone.utc).strftime("%d %b %Y %H%M UTC")
        ts_lbl = QLabel(ts)
        ts_lbl.setStyleSheet("color: #C8D0DE; font-size: 10px; background: transparent; border: none;")
        sl.addWidget(ts_lbl)

        # obs values grid
        temp_txt = f"{obs.temperature_c * 9/5 + 32:.0f}°F" if obs.temperature_c is not None else "--"
        dew_txt  = f"{obs.dewpoint_c   * 9/5 + 32:.0f}°F" if obs.dewpoint_c   is not None else "--"
        wind_txt = (
            f"{obs.wind_speed_ms * 1.94384:.0f}kt @ {obs.wind_dir_deg:.0f}°"
            if obs.wind_speed_ms is not None and obs.wind_dir_deg is not None else "--"
        )
        pres_txt = f"{obs.pressure_mb:.1f}mb" if obs.pressure_mb is not None else "--"

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        for row_idx, (lbl1, val1, lbl2, val2) in enumerate([
            ("Temp", temp_txt, "Dew",  dew_txt),
            ("Wind", wind_txt, "Pres", pres_txt),
        ]):
            for col, (text, is_val) in enumerate([(lbl1, False), (val1, True), (lbl2, False), (val2, True)]):
                cell = QLabel(text)
                cell.setStyleSheet(
                    "color: #E8EAF0; background: transparent; border: none;"
                    if is_val else
                    "color: #C8D0DE; background: transparent; border: none;"
                )
                grid.addWidget(cell, row_idx, col)

        sl.addLayout(grid)
        return section


    def _init_data_inputs(self):
        """Start Track A (file watcher) and/or Track B (GPS) if configured."""
        self._gps_reader: GPSReader | None = None
        self._obs_watcher: ObsFileWatcher | None = None
        self._last_mqtt_publish = 0.0   # monotonic timestamp of last MQTT vehicle publish

        if self._monitor:
            log.info("Monitor mode — no local data inputs started")
            QTimer.singleShot(1500, self._show_monitor_mode_status)
            return

        # track B — GPS puck auto-detect (used when Track A file watcher is not configured)
        if not config.OBS_FILE_DIR:
            self._gps_reader = GPSReader(
                vehicle_id=config.VEHICLE_ID,
                port="",
                baud=config.GPS_BAUD,
                parent=self,
            )
            self._gps_reader.obs_ready.connect(self._on_local_vehicle_obs)
            self._gps_reader.start()
            log.info("GPS reader started in auto-detect mode")

        # track A — instrument file watcher (surface obs vehicles)
        if config.OBS_FILE_DIR:
            if config.OBS_FILE_GPS_MODE:
                field_map = FieldMap(
                    lat="Latitude",
                    lon="Longitude",
                    date_col="ddmmyy",
                    time_col="hhmmss[UTC]",
                    temperature_c="",
                    dewpoint_c="",
                    wind_speed_ms="",
                    wind_dir_deg="",
                    pressure_mb="",
                )
            else:
                field_map = FieldMap(
                    lat=config.OBS_FILE_COL_LAT,
                    lon=config.OBS_FILE_COL_LON,
                    date_col=config.OBS_FILE_COL_DATE,
                    time_col=config.OBS_FILE_COL_TIME,
                    temperature_c=config.OBS_FILE_COL_TEMP,
                    dewpoint_c=config.OBS_FILE_COL_DEWP,
                    wind_speed_ms=config.OBS_FILE_COL_WSPD,
                    wind_dir_deg=config.OBS_FILE_COL_WDIR,
                    pressure_mb=config.OBS_FILE_COL_PRES,
                )
            self._obs_watcher = ObsFileWatcher(
                data_dir=config.OBS_FILE_DIR,
                vehicle_id=config.VEHICLE_ID,
                field_map=field_map,
                poll_interval_s=config.OBS_FILE_POLL_S,
                gps_mode=config.OBS_FILE_GPS_MODE,
                parent=self,
            )
            self._obs_watcher.obs_ready.connect(self._on_local_vehicle_obs)
            self._obs_watcher.start()
            log.info("Obs file watcher started: dir=%s gps_mode=%s",
                     config.OBS_FILE_DIR, config.OBS_FILE_GPS_MODE)
        else:
            log.info("Obs file dir not configured (obs_file.data_dir empty) — Track A disabled")
