
import json
import math
import logging
import sys
import runtime_flags

from PyQt6.QtCore import QUrl, QTimer, pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from config import ACCENT_COLOR
from ui.map.bridge import MapBridge
from ui.map.html import STATIC_PATH, TILES_PATH, build_map_html

# optional Windows fallback: disable WebGL map rendering only when explicitly
SAFE_MAP_MODE = (
    sys.platform == "win32"
    and runtime_flags.FLAGS.safe_map_mode
)

if not SAFE_MAP_MODE:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel

log = logging.getLogger(__name__)


def _ring_area(ring) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))


def _inside(ring, x, y) -> bool:
    hit = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def _edge_distance(ring, x, y, kx) -> float:
    """Distance from (x, y) to the ring's edges, longitude scaled by kx."""
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        ax, ay, bx, by, px = x0 * kx, y0, x1 * kx, y1, x * kx
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (ax + t * dx), y - (ay + t * dy)))
    return best


def _pole_candidates(ring, kx) -> list[tuple[float, float, float]]:
    """(distance, x, y) for points inside the ring, farthest from its edges
    first: a coarse grid, then a finer one around the best cell."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    found = []
    for _ in range(2):
        n = 12
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * j / n
                if _inside(ring, x, y):
                    found.append((_edge_distance(ring, x, y, kx), x, y))
        if not found:
            return []
        _, cx, cy = max(found)
        wx, wy = (x1 - x0) / 6, (y1 - y0) / 6
        x0, x1, y0, y1 = cx - wx, cx + wx, cy - wy, cy + wy
    return sorted(found, reverse=True)


def cwa_label_point(rings) -> list[float] | None:
    """[lon, lat] for a CWA's label: the point of its largest ring farthest
    from any edge, so the label sits well inside even an oddly shaped area.
    Worked out on the outline thinned to a few hundred points (outlines run
    to 250k vertices); the best candidate that is also inside the full
    outline wins, in case the thinning cut a corner."""
    rings = [[tuple(pt[:2]) for pt in r] for r in rings if len(r) >= 3]
    if not rings:
        return None
    ring = max(rings, key=lambda r: abs(_ring_area(r)))
    lats = [p[1] for p in ring[::max(1, len(ring) // 1000)]]
    kx = math.cos(math.radians((min(lats) + max(lats)) / 2))
    for keep in (300, 3000):
        thin = ring[::max(1, len(ring) // keep)]
        if len(thin) < 3:
            continue
        for _, x, y in _pole_candidates(thin, kx)[:20]:
            if _inside(ring, x, y):
                return [round(x, 4), round(y, 4)]
    return None


def cwa_label_points(features: list[dict], shp_path: str) -> list[list[float] | None]:
    """Label points for the CWA polygons, read from <shapefile>_labels.json
    when it matches this shapefile (the outlines never change between
    releases), else worked out (~10 s) and saved there for next time."""
    import os
    cache_path = shp_path[:-4] + "_labels.json"
    key = {"shapefile": os.path.basename(shp_path), "bytes": os.path.getsize(shp_path), "count": len(features)}
    try:
        with open(cache_path) as f:
            cached = json.load(f)
        if cached.get("key") == key:
            return cached["points"]
    except (OSError, ValueError, KeyError):
        pass
    points = [cwa_label_point(f["geometry"]["coordinates"]) for f in features]
    try:
        with open(cache_path, "w") as f:
            json.dump({"key": key, "points": points}, f)
    except OSError as exc:
        log.info("CWA labels not cached (%s)", exc)
    return points


class MapWidget(QWidget if SAFE_MAP_MODE else QWebEngineView):
    map_ready             = pyqtSignal()
    map_clicked           = pyqtSignal(float, float)
    map_moved             = pyqtSignal(float, float, float)
    feature_clicked       = pyqtSignal(str)
    annotation_clicked    = pyqtSignal(str)
    annotation_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    drawing_drag_ended    = pyqtSignal(str, str)           # id, coords json
    storm_cone_clicked    = pyqtSignal(str)
    storm_cone_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    storm_cone_place_drag_ended = pyqtSignal(float, float)
    private_pin_route_requested = pyqtSignal(float, float, str)
    map_double_clicked    = pyqtSignal(float, float)
    drawing_clicked       = pyqtSignal(str)
    radar_station_clicked = pyqtSignal(str)
    platform_marker_clicked = pyqtSignal(str)
    sounding_clicked             = pyqtSignal(float, float)
    obs_sounding_station_clicked = pyqtSignal(str, str, float, float, float)  # id, name, lat, lon, elev
    asos_bbox_selected    = pyqtSignal(float, float, float, float)  # west, south, east, north
    user_dragged          = pyqtSignal()
    map_pick_for_route    = pyqtSignal(float, float)
    cwa_loaded            = pyqtSignal()
    _cwa_parsed           = pyqtSignal(object)
    track_point_add_requested = pyqtSignal(float, float)  # lat, lon
    track_point_selected      = pyqtSignal(int)           # point_id
    track_point_moved         = pyqtSignal(int, float, float, bool)  # point_id, lat, lon, keep_time
    track_point_delete_requested = pyqtSignal(int)        # point_id
    track_marker_add_requested   = pyqtSignal(float, float)  # lat, lon
    track_marker_rename_requested = pyqtSignal()
    track_marker_remove_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        if SAFE_MAP_MODE:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            msg = QLabel(
                "Safe Map Mode: WebEngine disabled on this Windows device to avoid GPU crashes."
            )
            msg.setWordWrap(True)
            msg.setStyleSheet("color: #B5BDCC; font-size: 13px;")
            layout.addWidget(msg)
            self._map_ready = True
            self._js_queue = []
            QTimer.singleShot(0, self.map_ready.emit)
            return

        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from ui.map.tile_scheme_handler import StormSchemeHandler
        self._scheme_handler = StormSchemeHandler(
            TILES_PATH, STATIC_PATH, build_map_html()
        )
        # QWebEngineProfile.defaultProfile() is a process-wide singleton, not
        # per-window -- a second install for the same scheme without first
        # removing the previous MapWidget's handler is a silent no-op (Qt
        # just logs "URL scheme handler already installed for the scheme:
        # storm" and keeps the old one). That's exactly what happens on a
        # "change day" session restart: the new window's page then loads
        # storm:// content through the prior (now-closing) window's handler,
        # which is why the map goes blank. shutdown() below removes this
        # instance's handler on the way out so the next MapWidget can install
        # cleanly -- see MainWindow.closeEvent.
        QWebEngineProfile.defaultProfile().installUrlSchemeHandler(
            b"storm", self._scheme_handler
        )
        # public accessor so RadarOverlay can push PNG bytes for URL-based serving
        self.scheme_handler = self._scheme_handler

        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, not SAFE_MAP_MODE)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, not SAFE_MAP_MODE)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        self.bridge = MapBridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)

        self.bridge.map_clicked.connect(self.map_clicked)
        self.bridge.map_moved.connect(self.map_moved)
        self.bridge.feature_clicked.connect(self.feature_clicked)
        self.bridge.annotation_clicked.connect(self.annotation_clicked)
        self.bridge.annotation_drag_ended.connect(self.annotation_drag_ended)
        self.bridge.drawing_drag_ended.connect(self.drawing_drag_ended)
        self.bridge.storm_cone_clicked.connect(self.storm_cone_clicked)
        self.bridge.storm_cone_drag_ended.connect(self.storm_cone_drag_ended)
        self.bridge.storm_cone_place_drag_ended.connect(self.storm_cone_place_drag_ended)
        self.bridge.private_pin_route_requested.connect(self.private_pin_route_requested)
        self.bridge.map_double_clicked.connect(self.map_double_clicked)
        self.bridge.drawing_clicked.connect(self.drawing_clicked)
        self.bridge.radar_station_clicked.connect(self.radar_station_clicked)
        self.bridge.platform_marker_clicked.connect(self.platform_marker_clicked)
        self.bridge.sounding_clicked.connect(self.sounding_clicked)
        self.bridge.obs_sounding_station_clicked.connect(self.obs_sounding_station_clicked)
        self.bridge.asos_bbox_selected.connect(self.asos_bbox_selected)
        self.bridge.user_dragged.connect(self.user_dragged)
        self.bridge.map_pick_for_route.connect(self.map_pick_for_route)
        self.bridge.track_point_add_requested.connect(self.track_point_add_requested)
        self.bridge.track_point_selected.connect(self.track_point_selected)
        self.bridge.track_point_moved.connect(self.track_point_moved)
        self.bridge.track_point_delete_requested.connect(self.track_point_delete_requested)
        self.bridge.track_marker_add_requested.connect(self.track_marker_add_requested)
        self.bridge.track_marker_rename_requested.connect(self.track_marker_rename_requested)
        self.bridge.track_marker_remove_requested.connect(self.track_marker_remove_requested)

        # queue for JS calls that arrive before MapLibre has fully loaded.
        self._map_ready = False
        self._js_queue: list[str] = []
        self._cwa_parsed.connect(self._on_cwa_parsed)
        self.bridge.map_loaded.connect(self._on_map_loaded_from_js)
        self.loadFinished.connect(self._on_page_load_finished)

        QTimer.singleShot(0, self._load_map)

    def shutdown(self) -> None:
        """Release this instance's storm:// registration on the process-wide
        default profile. Must run before a replacement MapWidget is built in
        the same process (see the note in __init__) -- call from
        MainWindow.closeEvent, not __del__ (Qt teardown order there is not
        guaranteed to still have a usable profile)."""
        if SAFE_MAP_MODE or getattr(self, "_scheme_handler", None) is None:
            return
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        QWebEngineProfile.defaultProfile().removeUrlSchemeHandler(self._scheme_handler)

    def javaScriptConsoleMessage(self, level, message, line, source):
        # emit all JS console messages to stdout for debugging (includes errors/warnings/info)
        try:
            lvl_name = getattr(level, 'name', str(level))
        except Exception:
            lvl_name = str(level)
        print(f"JS [{lvl_name}] {message} ({source}:{line})", flush=True)
        from PyQt6.QtWebEngineCore import QWebEnginePage
        if level in (QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
                     QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel):
            log.warning("JS %s [%s:%s]: %s", lvl_name, source, line, message)

    def _load_map(self):
        self.load(QUrl("storm://app/"))

    def _on_page_load_finished(self, ok: bool):
        if not ok or self._map_ready:
            return
        QTimer.singleShot(8000, self._mark_map_ready_if_bridge_stalled)

    def _on_map_loaded_from_js(self):
        if self._map_ready:
            return
        self._map_ready = True
        for script in self._js_queue:
            self.page().runJavaScript(script)
        self._js_queue.clear()
        self.map_ready.emit()

    def _mark_map_ready_if_bridge_stalled(self):
        """Allow Python startup to continue if the JS map-ready callback stalls."""
        if self._map_ready:
            return
        log.warning("Map page loaded but JS map-ready callback did not fire; continuing startup")
        self._map_ready = True
        self._js_queue.clear()
        self.map_ready.emit()

    def run_js(self, script: str):
        if SAFE_MAP_MODE:
            return
        if self._map_ready:
            self.page().runJavaScript(script)
        else:
            self._js_queue.append(script)

    def add_vehicle(self, vehicle_id: str, lat: float, lon: float,
                    color: str = ACCENT_COLOR, icon_type: str = "car",
                    hover_text: str | None = None):
        tooltip = hover_text or vehicle_id
        self.run_js(
            "stormAddVehicle("
            f"{json.dumps(vehicle_id)}, {lat}, {lon}, {json.dumps(color)}, "
            f"{json.dumps(icon_type)}, {json.dumps(tooltip)});"
        )

    def remove_vehicle(self, vehicle_id: str):
        self.run_js(f"stormRemoveVehicle('{vehicle_id}');")

    def set_lidar_site(self, site: dict | None):
        """Selected raw-lidar origin, separate from surface-observation
        vehicles. Uses the same clickable icon marker as vehicles/NOXP
        (_vIcons["lidar"]) for visual consistency across instrument types."""
        if site is None:
            self.run_js("stormRemovePlatformMarker('raw-lidar-site');")
            return
        self.run_js(
            "stormAddPlatformMarker("
            f"{json.dumps('raw-lidar-site')}, {site['lat']}, {site['lon']}, "
            f"{json.dumps('#00CFFF')}, {json.dumps('lidar')}, {json.dumps(site['instrument'])});"
        )

    def set_noxp_site(self, site: dict | None):
        """Selected NOXP volume's origin -- separate marker id from
        set_lidar_site so both can be shown at once without clobbering
        each other. Clicking it emits platform_marker_clicked('noxp')."""
        if site is None:
            self.run_js("stormRemovePlatformMarker('noxp');")
            return
        self.run_js(
            "stormAddPlatformMarker("
            f"{json.dumps('noxp')}, {site['lat']}, {site['lon']}, "
            f"{json.dumps('#FFB347')}, {json.dumps('radar')}, {json.dumps(site['instrument'])});"
        )

    def set_satellite_frame(self, b64: str, west: float, south: float,
                            east: float, north: float):
        self.run_js(
            f"if(window.stormSetSatelliteFrame) "
            f"stormSetSatelliteFrame({repr(b64)},{west},{south},{east},{north});"
        )

    def set_satellite_time(self, time_iso: str):
        self.run_js(f"if(window.stormSetSatelliteTime) stormSetSatelliteTime('{time_iso}');")

    def set_satellite_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(f"if(window.stormSetSatelliteVisible) stormSetSatelliteVisible({flag});")

    def set_satellite_mode(self, mode: str):
        self.run_js(f"if(window.stormSetSatelliteMode) stormSetSatelliteMode('{mode}');")

    def set_satellite_opacity(self, opacity: float):
        self.run_js(f"if(window.stormSetSatelliteOpacity) stormSetSatelliteOpacity({opacity:.3f});")

    def clear_satellite_frame(self) -> None:
        self.run_js("if(window.stormClearSatelliteFrame) stormClearSatelliteFrame();")

    def set_mesoanalysis_overlay(
        self,
        product_id: str,
        tile_url: str,
        source_layer: str,
        west: float,
        south: float,
        east: float,
        north: float,
        minzoom: int = 0,
        maxzoom: int = 8,
        label_units: str = "",
    ) -> None:
        self.run_js(
            "if(window.stormSetMesoanalysisOverlay) "
            f"stormSetMesoanalysisOverlay({json.dumps(product_id)},"
            f"{json.dumps(tile_url)},"
            f"{json.dumps(source_layer)},{west},{south},{east},{north},"
            f"{int(minzoom)},{int(maxzoom)},"
            f"{json.dumps(str(label_units or ''))});"
        )

    def set_sfcoa_overlay(
        self,
        product_id: str,
        tile_url: str,
        source_layer: str,
        west: float,
        south: float,
        east: float,
        north: float,
        minzoom: int = 0,
        maxzoom: int = 8,
        label_units: str = "",
    ) -> None:
        self.run_js(
            "if(window.stormSetSfcoaOverlay) "
            f"stormSetSfcoaOverlay({json.dumps(product_id)},"
            f"{json.dumps(tile_url)},"
            f"{json.dumps(source_layer)},{west},{south},{east},{north},"
            f"{int(minzoom)},{int(maxzoom)},"
            f"{json.dumps(str(label_units or ''))});"
        )

    def register_sfcoa_mbtiles(self, tile_key: str, mbtiles_path: str) -> None:
        handler = getattr(self, "_scheme_handler", None)
        if handler is not None and hasattr(handler, "set_sfcoa_mbtiles"):
            handler.set_sfcoa_mbtiles(tile_key, mbtiles_path)

    def set_sfcoa_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSfcoaVisible) "
            f"stormSetSfcoaVisible({'true' if visible else 'false'});"
        )

    def set_sfcoa_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetSfcoaOpacity) "
            f"stormSetSfcoaOpacity({opacity:.3f});"
        )

    def clear_sfcoa_overlay(self, product_id: str = "") -> None:
        self.run_js(
            f"if(window.stormClearSfcoaOverlay) "
            f"stormClearSfcoaOverlay({json.dumps(str(product_id or ''))});"
        )

    def set_mesoanalysis_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetMesoanalysisVisible) "
            f"stormSetMesoanalysisVisible({'true' if visible else 'false'});"
        )

    def set_mesoanalysis_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetMesoanalysisOpacity) "
            f"stormSetMesoanalysisOpacity({opacity:.3f});"
        )

    def clear_mesoanalysis_overlay(self, product_id: str = "") -> None:
        self.run_js(
            f"if(window.stormClearMesoanalysisOverlay) "
            f"stormClearMesoanalysisOverlay({json.dumps(str(product_id or ''))});"
        )

    def set_nlcd_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetNlcdVisible) "
            f"stormSetNlcdVisible({'true' if visible else 'false'});"
        )

    def set_nlcd_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetNlcdOpacity) "
            f"stormSetNlcdOpacity({opacity:.3f});"
        )

    def set_satellite_basemap_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSatelliteBasemapVisible) "
            f"stormSetSatelliteBasemapVisible({'true' if visible else 'false'});"
        )

    def set_satellite_basemap_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetSatelliteBasemapOpacity) "
            f"stormSetSatelliteBasemapOpacity({opacity:.3f});"
        )

    def set_meso_sectors(self, sectors: dict):
        features = []
        for idx, bbox in sectors.items():
            if bbox:
                features.append({
                    "label": f"MESO-{idx}",
                    "west":  bbox["west"],
                    "south": bbox["south"],
                    "east":  bbox["east"],
                    "north": bbox["north"],
                })
        self.run_js(
            f"if(window.stormSetMesoSectors) stormSetMesoSectors({json.dumps(json.dumps(features))});"
        )

    def set_radar_stations(self, stations: list[dict]):
        features = []
        for station in stations:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [station["lon"], station["lat"]],
                },
                "properties": {
                    "site_id": station["site_id"],
                    "name": station.get("name", ""),
                },
            })
        geojson = {"type": "FeatureCollection", "features": features}
        self.run_js(
            f"if(window.stormSetRadarStations) stormSetRadarStations({json.dumps(json.dumps(geojson))});"
        )

    def set_radar_stations_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetRadarStationsVisible) stormSetRadarStationsVisible({flag});"
        )

    def set_cwa_geojson(self, geojson: dict):
        """Set the CWA GeoJSON on the map (expects a FeatureCollection dict)."""
        self.run_js(
            f"if(window.stormSetCwaGeoJSON) stormSetCwaGeoJSON({json.dumps(json.dumps(geojson))});"
        )

    def set_cwa_visible(self, visible: bool):
        """Toggle CWA overlay visibility."""
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetCwaVisible) stormSetCwaVisible({flag});"
        )

    def load_cwa_shapefile(self, shp_base: str | None = None):
        """Load a local CWA shapefile (shp + dbf) and push it to the map as GeoJSON.

        shp_base may be a basename (without extension) or a full .shp path. If
        omitted, defaults to the bundled cwa_shp/w_16ap26 shapefile.

        Parsing runs on a background thread; cwa_loaded is emitted on the main
        thread when the data is visible on the map.
        """
        import os, struct, threading as _threading

        if shp_base is None:
            # ui/map/widget.py -> ../.. lands at the STORM app root, where
            # cwa_shp/ actually lives (was one level short, silently
            # resolving to ui/cwa_shp/ which doesn't exist).
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'cwa_shp', 'w_16ap26'))
        else:
            base = shp_base
        shp_path = base if base.lower().endswith('.shp') else base + '.shp'
        dbf_path = shp_path[:-4] + '.dbf'

        def _worker():
            try:
                with open(shp_path, 'rb') as f:
                    shp_data = f.read()
                with open(dbf_path, 'rb') as f:
                    dbf_data = f.read()
            except Exception as exc:
                log.warning("load_cwa_shapefile: could not read %s / %s: %s", shp_path, dbf_path, exc)
                return

            # minimal SHP parser (Polygon type 5) — adapted from archive fetcher.
            def _parse_shp(data: bytes):
                if len(data) < 100:
                    return []
                pos = 100
                geometries = []
                while pos < len(data):
                    if pos + 12 > len(data):
                        break
                    _rec_num, content_words = struct.unpack_from('>ii', data, pos)
                    pos += 8
                    content_bytes = content_words * 2
                    if content_bytes < 4 or pos + content_bytes > len(data):
                        break
                    shape_type = struct.unpack_from('<i', data, pos)[0]
                    if shape_type == 0:
                        geometries.append(None)
                        pos += content_bytes
                        continue
                    if shape_type != 5:
                        geometries.append(None)
                        pos += content_bytes
                        continue
                    offset = pos + 4
                    if offset + 32 + 8 > len(data):
                        geometries.append(None)
                        pos += content_bytes
                        continue
                    offset += 32
                    num_parts, num_points = struct.unpack_from('<ii', data, offset)
                    offset += 8
                    if num_parts <= 0 or num_points <= 0:
                        geometries.append(None)
                        pos += content_bytes
                        continue
                    part_starts = list(struct.unpack_from(f'<{num_parts}i', data, offset))
                    offset += num_parts * 4
                    pts_raw = struct.unpack_from(f'<{num_points * 2}d', data, offset)
                    points = [(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]
                    rings = []
                    for idx_r, start in enumerate(part_starts):
                        end = part_starts[idx_r + 1] if idx_r + 1 < num_parts else num_points
                        ring = [list(pt) for pt in points[start:end]]
                        rings.append(ring)
                    geometries.append({'type': 'Polygon', 'coordinates': rings})
                    pos += content_bytes
                return geometries

            # minimal DBF parser — adapted from archive fetcher.
            def _parse_dbf(data: bytes):
                if len(data) < 32:
                    return []
                num_records = struct.unpack_from('<I', data, 4)[0]
                header_bytes = struct.unpack_from('<H', data, 8)[0]
                record_bytes = struct.unpack_from('<H', data, 10)[0]
                fields = []
                pos = 32
                while pos < header_bytes - 1 and data[pos] != 0x0D:
                    raw_name = data[pos:pos + 11]
                    name = raw_name.split(b"\x00")[0].decode('ascii', errors='replace').strip()
                    ftype = chr(data[pos + 11])
                    flen = data[pos + 16]
                    fields.append((name, ftype, flen))
                    pos += 32
                records = []
                rec_pos = header_bytes
                for _ in range(num_records):
                    if rec_pos + record_bytes > len(data):
                        break
                    deletion_flag = data[rec_pos]
                    if deletion_flag == 0x2A:  # '*' = deleted
                        rec_pos += record_bytes
                        continue
                    field_pos = rec_pos + 1
                    rec = {}
                    for name, ftype, flen in fields:
                        raw = data[field_pos:field_pos + flen].decode('ascii', errors='replace').strip()
                        if ftype == 'N':
                            try:
                                rec[name] = float(raw) if raw else None
                            except ValueError:
                                rec[name] = None
                        else:
                            rec[name] = raw
                        field_pos += flen
                    records.append(rec)
                    rec_pos += record_bytes
                return records

            geoms = _parse_shp(shp_data)
            recs = _parse_dbf(dbf_data)
            features = []
            for geom, rec in zip(geoms, recs):
                if geom is None:
                    continue
                features.append({'type': 'Feature', 'geometry': geom, 'properties': rec})
            # one label per CWA, well inside it (the map would otherwise
            # label each polygon once per tile: repeats, and none for a CWA
            # whose label spot is off screen)
            for feature, spot in zip(list(features), cwa_label_points(features, shp_path)):
                if spot is not None:
                    props = feature['properties']
                    features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': spot},
                                     'properties': {'WFO': props.get('WFO'), 'CWA': props.get('CWA')}})
            geojson = {'type': 'FeatureCollection', 'features': features}
            self._cwa_parsed.emit(geojson)

        _threading.Thread(target=_worker, daemon=True).start()

    def _on_cwa_parsed(self, geojson: dict):
        self.set_cwa_geojson(geojson)
        self.cwa_loaded.emit()

    def set_route(self, geojson_str: str, dest_lon: float, dest_lat: float):
        """Draw a route polyline on the map and place a destination marker."""
        self.run_js(
            f"if(window.stormSetRoute) stormSetRoute({json.dumps(geojson_str)});"
        )
        self.run_js(
            f"if(window.stormSetDestinationMarker) "
            f"stormSetDestinationMarker({dest_lon}, {dest_lat});"
        )

    def clear_route(self):
        """Remove the route line and destination marker."""
        self.run_js("if(window.stormClearRoute) stormClearRoute();")

    def set_damage_paths(self, geojson_str: str):
        """Show damage-survey path lines (DAT/NCEI Storm Events), colored by EF-scale rating."""
        self.run_js(
            f"if(window.stormSetDamagePaths) stormSetDamagePaths({json.dumps(geojson_str)});"
        )

    def clear_damage_paths(self):
        """Remove all damage-survey path lines from the map."""
        self.run_js("if(window.stormClearDamagePaths) stormClearDamagePaths();")

    def set_private_pin_own_location(self, lat: float, lon: float):
        """Expose the local vehicle fix to local-only private pin tools."""
        self.run_js(
            f"if(window.stormSetPrivatePinOwnLocation) "
            f"stormSetPrivatePinOwnLocation({lat}, {lon});"
        )

    def set_route_pick_mode(self, active: bool):
        """Toggle crosshair pick mode for destination selection."""
        flag = "true" if active else "false"
        self.run_js(
            f"if(window.stormSetRoutePickMode) stormSetRoutePickMode({flag});"
        )

    def set_sounding_mode(self, active: bool):
        """Toggle HRRR sounding-click mode: map clicks emit lat/lon instead of normal actions."""
        flag = "true" if active else "false"
        self.run_js(f"window._soundingModeActive = {flag};")
        cursor = Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    def set_obs_sounding_mode(self, active: bool):
        """Toggle OBS sounding mode: station dots become clickable."""
        flag = "true" if active else "false"
        self.run_js(f"window._soundingObsModeActive = {flag};")
        if not active:
            self.setCursor(Qt.CursorShape.ArrowCursor)


    def set_sounding_stations(self, geojson_str: str):
        """Inject sounding station GeoJSON and make the layer visible."""
        escaped = geojson_str.replace("\\", "\\\\").replace("`", "\\`")
        self.run_js(f"if(window.stormSetSoundingStations) stormSetSoundingStations(`{escaped}`);")

    def clear_sounding_stations(self):
        """Hide and clear the sounding station layer."""
        self.run_js("if(window.stormClearSoundingStations) stormClearSoundingStations();")

    def preview_meso_sector(self, idx: int | None):
        if idx in (1, 2):
            self.run_js(
                f"if(window.stormPreviewMesoSector) stormPreviewMesoSector('MESO-{idx}');"
            )
        else:
            self.run_js("if(window.stormClearMesoPreview) stormClearMesoPreview();")

    def fly_to(self, lat: float, lon: float, zoom: float = None):
        zoom_str = str(zoom) if zoom is not None else "undefined"
        self.run_js(f"stormFlyTo({lat}, {lon}, {zoom_str});")

    def set_follow(self, enabled: bool):
        self.run_js(f"stormSetFollow({'true' if enabled else 'false'});")

    def follow_move(self, lat: float, lon: float):
        self.run_js(f"stormFollowMove({lat}, {lon});")

    def set_annotation_mode(self, active: bool):
        if active:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.add('annotating');el.classList.remove('drawing','measuring');}})();"
            )
        else:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.remove('annotating');}})();"
            )

    def set_measure_mode(self, active: bool):
        if active:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.add('measuring');el.classList.remove('annotating','drawing');}})();"
                "if(window.stormMeasureActivate) stormMeasureActivate(true);"
            )
        else:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.remove('measuring');}})();"
                "if(window.stormMeasureActivate) stormMeasureActivate(false);"
            )

    def measure_click(self, lat: float, lon: float):
        self.run_js(f"if(window.stormMeasureClick) stormMeasureClick({lat},{lon});")

    def clear_measure(self):
        self.run_js("if(window.stormMeasureClear) stormMeasureClear();")

    def set_drawing_mode(self, active: bool, type_key: str = "") -> None:
        flag = "true" if active else "false"
        self.run_js(
            f"if(window.stormDrawingModeSet) stormDrawingModeSet({flag}, '{type_key}');"
        )

    def set_drawing_draggable(self, drawing_id: str, on: bool) -> None:
        self.run_js(f"if(window.stormSetDrawingDraggable) stormSetDrawingDraggable('{drawing_id}', {'true' if on else 'false'});")

    def drawing_update_preview(self, points: list) -> None:
        import json
        self.run_js(
            f"if(window.stormDrawingUpdatePreview) stormDrawingUpdatePreview({json.dumps(json.dumps(points))});"
        )

    def add_drawing(self, drawing) -> None:
        import json
        payload = json.dumps(drawing.to_dict())
        self.run_js(f"if(window.stormAddDrawing) stormAddDrawing('{drawing.id}', {json.dumps(payload)});")

    def remove_drawing(self, drawing_id: str) -> None:
        self.run_js(f"if(window.stormRemoveDrawing) stormRemoveDrawing('{drawing_id}');")

    def add_annotation(self, annotation) -> None:
        if getattr(annotation, "type_key", "") == "storm_motion":
            return
        label = annotation.label.replace("'", "\\'")
        self.run_js(
            f"stormAddAnnotation('{annotation.id}', {annotation.lat}, "
            f"{annotation.lon}, '{annotation.type_key}', '{label}');"
        )

    def remove_annotation(self, annotation_id: str) -> None:
        self.run_js(f"stormRemoveAnnotation('{annotation_id}');")

    def set_annotation_draggable(self, annotation_id: str, on: bool) -> None:
        self.run_js(f"stormSetAnnotationDraggable('{annotation_id}', {'true' if on else 'false'});")

    def move_annotation(self, annotation_id: str, lat: float, lon: float) -> None:
        self.run_js(f"stormMoveAnnotation('{annotation_id}', {lat}, {lon});")

    def add_storm_cone(self, cone) -> None:
        import json
        geojson_str = json.dumps(cone.build_geojson())
        self.run_js(f"stormAddStormCone('{cone.id}', {json.dumps(geojson_str)}, {cone.lat}, {cone.lon});")

    def remove_storm_cone(self, cone_id: str) -> None:
        self.run_js(f"stormRemoveStormCone('{cone_id}');")

    def set_storm_cone_draggable(self, cone_id: str, on: bool) -> None:
        self.run_js(f"if(window.stormSetStormConeDraggable) stormSetStormConeDraggable('{cone_id}', {'true' if on else 'false'});")

    def set_storm_cone_placement_mode(self, active: bool) -> None:
        self.run_js(f"if(window.stormSetStormConePlacementMode) stormSetStormConePlacementMode({'true' if active else 'false'});")

    def set_track_geojson(self, geojson_str: str) -> None:
        import json
        self.run_js(f"if(window.stormSetTrackGeoJSON) stormSetTrackGeoJSON({json.dumps(geojson_str)});")

    def set_trails(self, geojson: dict | None, stops: list | None = None, units: str = "") -> None:
        """Observation trails (core/trails.py); None clears them."""
        import json
        data = json.dumps(geojson or {"type": "FeatureCollection", "features": []})
        self.run_js(f"if(window.stormSetTrails) stormSetTrails({json.dumps(data)}, "
                    f"{json.dumps(stops or [])}, {json.dumps(units)});")

    def set_track_marker(self, marker: dict | None) -> None:
        """Show the track's reference marker ({lat, lon, label}), or none."""
        import json
        arg = json.dumps(json.dumps(marker)) if marker else "null"
        self.run_js(f"if(window.stormSetTrackMarker) stormSetTrackMarker({arg});")

    def set_track_edit_mode(self, active: bool) -> None:
        self.run_js(f"if(window.stormSetTrackEditMode) stormSetTrackEditMode({'true' if active else 'false'});")

    def set_track_layers_visible(self, line: bool, points: bool) -> None:
        self.run_js(
            f"if(window.stormSetTrackLayersVisible) stormSetTrackLayersVisible("
            f"{'true' if line else 'false'}, {'true' if points else 'false'});"
        )

    def set_asos_bbox_mode(self, active: bool) -> None:
        """Toggle JS rectangle-selection mode for ASOS bbox selection."""
        flag = 'true' if active else 'false'
        self.run_js(f"if(window.stormSetAsosBoxMode) stormSetAsosBoxMode({flag});")

    def fit_bounds(self, west: float, south: float, east: float, north: float, padding: int = 40) -> None:
        """Fly the map to fit the given bounding box with optional padding (px)."""
        self.run_js(
            f"map.fitBounds([[{west},{south}],[{east},{north}]], {{padding:{padding}}});"
        )

    def add_station_plot(self, vehicle_id: str, lat: float, lon: float, png_bytes: bytes) -> None:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self.run_js(f"stormAddStationPlot('{vehicle_id}', {lat}, {lon}, '{b64}');")

    def remove_station_plot(self, vehicle_id: str) -> None:
        self.run_js(f"stormRemoveStationPlot('{vehicle_id}');")

    def set_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetStationPlotsVisible({v});")

    def add_surface_station_plot(self, station_id: str, lat: float, lon: float, png_bytes: bytes, name: str = "") -> None:
        """Single-station add (used by SurfacePlotLayer directly)."""
        import json as _json
        self.scheme_handler.set_station_plots({station_id: png_bytes})
        self.run_js(
            f"stormAddSurfaceStationPlot({_json.dumps(station_id)}, {lat}, {lon}, {_json.dumps(name)});"
        )

    def add_surface_station_plots_batch(self, items: list) -> None:
        """Batch-add stations. items: list of (id, lat, lon, png_bytes, name).
        PNGs are stored in the scheme handler; JS payload contains only metadata."""
        import json as _json
        plots = {sid: png for sid, _, _, png, _ in items}
        self.scheme_handler.set_station_plots(plots)
        data = [{"id": sid, "lat": lat, "lon": lon, "name": name}
                for sid, lat, lon, _png, name in items]
        self.run_js(f"stormAddSurfaceStationPlotBatch({_json.dumps(data)});")

    def remove_surface_station_plot(self, station_id: str) -> None:
        self.scheme_handler.remove_station_plots([station_id])
        self.run_js(f"stormRemoveSurfaceStationPlot('{station_id}');")

    def remove_surface_station_plots_batch(self, station_ids) -> None:
        """Single JS call to remove many stations at once."""
        import json as _json
        ids = list(station_ids)
        self.scheme_handler.remove_station_plots(ids)
        self.run_js(f"stormRemoveSurfaceStationPlotBatch({_json.dumps(ids)});")

    def set_surface_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetSurfaceStationPlotsVisible({v});")

    def load_deploy_locs(self, points: list) -> None:
        import json
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
             "properties": {
                 "rank_abi": p.get("rank_abi"),
                 "rank_aoi": p.get("rank_aoi"),
                 "rqi":      p.get("rqi"),
             }}
            for p in points
        ]}
        self.run_js(f"stormLoadDeployLocs({json.dumps(json.dumps(fc))});")

    def set_deploy_locs_visible(self, visible: bool) -> None:
        self.run_js(f"stormSetDeployLocsVisible({'true' if visible else 'false'});")

    def set_deploy_locs_metric(self, metric: str) -> None:
        import json
        self.run_js(f"stormSetDeployLocsMetric({json.dumps(metric)});")

    def set_deploy_locs_filter(self, metric: str, threshold: float) -> None:
        import json
        self.run_js(f"stormSetDeployLocsFilter({json.dumps(metric)}, {threshold});")

    def set_deploy_locs_size(self, radius: int) -> None:
        self.run_js(f"stormSetDeployLocsSize({radius});")

    def set_scan_sectors_geojson(self, geojson: dict) -> None:
        import json
        self.run_js(
            f"if(window.stormSetScanSectors) stormSetScanSectors({json.dumps(json.dumps(geojson))});"
        )

    def set_annotations_visible(self, visible: bool) -> None:
        """Drawings (fronts, boundaries...), annotation markers and storm cones."""
        self.run_js(f"if(window.stormSetAnnotationsVisible) stormSetAnnotationsVisible({'true' if visible else 'false'});")

    def set_scan_sectors_visible(self, visible: bool) -> None:
        self.run_js(f"if(window.stormSetScanSectorsVisible) stormSetScanSectorsVisible({'true' if visible else 'false'});")

    def set_spc_geojson(
        self,
        cat_str: str,
        wind_str: str,
        hail_str: str,
        tor_str: str,
        prob_str: str = '{"type":"FeatureCollection","features":[]}',
        sig_str: str = '{"type":"FeatureCollection","features":[]}',
    ) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcGeoJSON) stormSetSpcGeoJSON("
            f"{json.dumps(cat_str)}, "
            f"{json.dumps(wind_str)}, "
            f"{json.dumps(hail_str)}, "
            f"{json.dumps(tor_str)}, "
            f"{json.dumps(prob_str)}, "
            f"{json.dumps(sig_str)}"
            ");"
        )

    def set_spc_category_visible(self, key: str, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcCategoryVisible) stormSetSpcCategoryVisible('{key}', {'true' if visible else 'false'});"
        )

    def set_spc_product_visible(self, key: str, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcProductVisible) stormSetSpcProductVisible('{key}', {'true' if visible else 'false'});"
        )

    def set_nws_warnings_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetNwsWarningsGeoJSON) stormSetNwsWarningsGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_nws_warnings_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetNwsWarningsVisible) stormSetNwsWarningsVisible({'true' if visible else 'false'});"
        )

    def set_spc_watches_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcWatchesGeoJSON) stormSetSpcWatchesGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_spc_watches_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcWatchesVisible) stormSetSpcWatchesVisible({'true' if visible else 'false'});"
        )

    def set_spc_mds_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcMdsGeoJSON) stormSetSpcMdsGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_spc_mds_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcMdsVisible) stormSetSpcMdsVisible({'true' if visible else 'false'});"
        )

    def move_layer_before(self, layer_id: str, before_layer_id: str | None) -> None:
        """Move a MapLibre layer before another layer (or to the top if before_layer_id is None)."""
        import json
        if before_layer_id is None:
            self.run_js(
                f"(function(){{ var lid={json.dumps(layer_id)}; "
                "function go(){ try { if(map.isStyleLoaded && !map.isStyleLoaded()) { setTimeout(go, 100); return; } "
                "if(map.getLayer(lid)) map.moveLayer(lid); } catch(e) { setTimeout(go, 100); } } go(); })();"
            )
        else:
            self.run_js(
                f"(function(){{ var lid={json.dumps(layer_id)}, before={json.dumps(before_layer_id)}; "
                "function go(){ try { if(map.isStyleLoaded && !map.isStyleLoaded()) { setTimeout(go, 100); return; } "
                "if(map.getLayer(lid) && map.getLayer(before)) map.moveLayer(lid, before); } "
                "catch(e) { setTimeout(go, 100); } } go(); })();"
            )
