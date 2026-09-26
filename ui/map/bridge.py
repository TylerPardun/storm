
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

log = logging.getLogger(__name__)


class MapBridge(QObject):
    map_clicked        = pyqtSignal(float, float)
    map_moved          = pyqtSignal(float, float, float)
    feature_clicked    = pyqtSignal(str)
    annotation_clicked = pyqtSignal(str)
    storm_cone_clicked = pyqtSignal(str)
    map_double_clicked    = pyqtSignal(float, float)
    drawing_clicked       = pyqtSignal(str)
    radar_station_clicked = pyqtSignal(str)
    platform_marker_clicked = pyqtSignal(str)
    sounding_clicked             = pyqtSignal(float, float)
    obs_sounding_station_clicked = pyqtSignal(str, str, float, float, float)  # id, name, lat, lon, elev
    asos_bbox_selected = pyqtSignal(float, float, float, float)
    user_dragged          = pyqtSignal()
    map_pick_for_route    = pyqtSignal(float, float)
    annotation_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    drawing_drag_ended    = pyqtSignal(str, str)           # id, coords json
    storm_cone_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    storm_cone_place_drag_ended = pyqtSignal(float, float)
    private_pin_route_requested = pyqtSignal(float, float, str)
    track_point_add_requested    = pyqtSignal(float, float)  # lat, lon
    track_point_selected         = pyqtSignal(int)           # point_id
    track_point_moved            = pyqtSignal(int, float, float, bool)  # point_id, lat, lon, keep_time

    @pyqtSlot(float, float)
    def on_map_click(self, lat: float, lon: float):
        self.map_clicked.emit(lat, lon)

    @pyqtSlot(float, float, float)
    def on_map_move(self, lat: float, lon: float, zoom: float):
        self.map_moved.emit(lat, lon, zoom)

    @pyqtSlot(str)
    def on_feature_click(self, feature_json: str):
        self.feature_clicked.emit(feature_json)

    @pyqtSlot(str)
    def on_annotation_click(self, annotation_id: str):
        self.annotation_clicked.emit(annotation_id)

    @pyqtSlot(str, float, float)
    def on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        self.annotation_drag_ended.emit(annotation_id, lat, lon)

    @pyqtSlot(str, str)
    def on_drawing_drag_end(self, drawing_id: str, coordinates_json: str):
        self.drawing_drag_ended.emit(drawing_id, coordinates_json)

    @pyqtSlot(str, float, float)
    def on_storm_cone_drag_end(self, cone_id: str, lat: float, lon: float):
        self.storm_cone_drag_ended.emit(cone_id, lat, lon)

    @pyqtSlot(float, float)
    def on_storm_cone_place_drag_end(self, lat: float, lon: float):
        self.storm_cone_place_drag_ended.emit(lat, lon)

    @pyqtSlot(float, float, str)
    def on_private_pin_route(self, lat: float, lon: float, label: str):
        self.private_pin_route_requested.emit(lat, lon, label)

    @pyqtSlot(str)
    def on_private_pin_copy(self, text: str):
        try:
            QApplication.clipboard().setText(text)
        except Exception as e:
            log.warning("private pin clipboard copy failed: %s", e)

    @pyqtSlot(str)
    def on_storm_cone_click(self, cone_id: str):
        self.storm_cone_clicked.emit(cone_id)

    @pyqtSlot(float, float)
    def on_track_point_add(self, lat: float, lon: float):
        self.track_point_add_requested.emit(lat, lon)

    @pyqtSlot(int)
    def on_track_point_select(self, point_id: int):
        self.track_point_selected.emit(point_id)

    @pyqtSlot(int, float, float, bool)
    def on_track_point_move(self, point_id: int, lat: float, lon: float, keep_time: bool):
        self.track_point_moved.emit(point_id, lat, lon, keep_time)

    @pyqtSlot(float, float)
    def on_map_dblclick(self, lat: float, lon: float):
        self.map_double_clicked.emit(lat, lon)

    @pyqtSlot(str)
    def on_drawing_click(self, drawing_id: str):
        self.drawing_clicked.emit(drawing_id)

    @pyqtSlot(str)
    def on_radar_station_click(self, site_id: str):
        self.radar_station_clicked.emit(site_id)

    @pyqtSlot(str)
    def on_platform_marker_click(self, platform_id: str):
        self.platform_marker_clicked.emit(platform_id)

    @pyqtSlot(str)
    def on_js_console(self, msg: str):
        """Receive forwarded JS console messages from the page via QWebChannel.

        Logged at INFO level and printed to stdout for easier capture when the
        application is run from a terminal.
        """
        try:
            # print to stdout so users running the app in a terminal see messages
            print(f"JS-FWD {msg}", flush=True)
        except Exception:
            pass
        try:
            log.info("JS-FWD %s", msg)
        except Exception:
            pass

    @pyqtSlot(float, float)
    def on_sounding_click(self, lat: float, lon: float):
        self.sounding_clicked.emit(lat, lon)

    @pyqtSlot(str, str, float, float, float)
    def on_obs_station_click(self, station_id: str, name: str, lat: float, lon: float, elev: float):
        self.obs_sounding_station_clicked.emit(station_id, name, lat, lon, elev)

    @pyqtSlot(float, float, float, float)
    def on_asos_bbox(self, west: float, south: float, east: float, north: float):
        """Called from JS when the user finishes drawing an ASOS bbox."""
        try:
            QTimer.singleShot(
                0,
                lambda: self.asos_bbox_selected.emit(west, south, east, north),
            )
        except Exception:
            pass

    @pyqtSlot()
    def on_user_drag(self):
        self.user_dragged.emit()

    map_loaded = pyqtSignal()

    @pyqtSlot()
    def on_map_loaded(self):
        self.map_loaded.emit()

    @pyqtSlot(float, float)
    def on_map_pick_for_route(self, lat: float, lon: float):
        self.map_pick_for_route.emit(lat, lon)
