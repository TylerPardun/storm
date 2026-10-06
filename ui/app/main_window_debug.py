

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QDockWidget, QVBoxLayout, QWidget

import config
from ui.widgets.debug_pill import DebugPill


class MainWindowDebugMixin:

    def _init_debug_panel(self):
        """Create the floating debug pill and wire up the status refresh timer."""
        self._debug_pill = DebugPill(self._map_container)
        self._debug_pill.size_changed.connect(self._layout_overlays)
        self._debug_pill.show()
        self._layout_overlays()

        self._debug_timer = QTimer()
        self._debug_timer.timeout.connect(self._refresh_debug_panel)
        self._debug_timer.start(1000)

    def _refresh_debug_panel(self):
        if not hasattr(self, "_debug_pill"):
            return

        lines = ["─── RADAR ───────────────────────────────"]
        fetcher = getattr(self, "_radar_fetcher", None)
        if fetcher is None:
            lines.append("radar disabled or not initialized")
        else:
            loop_timer = getattr(self, "_loop_timer", None)
            scan_cache = getattr(self, "_scan_cache", {})
            lines.append(
                f"fetcher running: {fetcher._running}  "
                f"site: {fetcher._site}  products: {fetcher._products}"
            )
            lines.append(
                f"map ready: {getattr(self.map_widget, '_map_ready', '?')}  "
                f"loop active: {loop_timer.isActive() if loop_timer else False}  "
                f"interval: {loop_timer.interval() if loop_timer else 0}ms"
            )
            lines.append(f"cache keys: {list(scan_cache.keys())}")
            for key, scans in scan_cache.items():
                if scans:
                    ages = [f"{s.age_seconds:.0f}s" for s in scans]
                    lines.append(f"  {key}: {len(scans)} frames  ages=[{', '.join(ages)}]")

        lines.append("─── MQTT ────────────────────────────────")
        mqtt = getattr(self, "_mqtt_client", None)
        if mqtt:
            connected = self.conn_indicator.text() == "● AWS OK"
            lines.append(
                f"host: {config.MQTT_HOST or '(not configured)'}:{config.MQTT_PORT}  "
                f"connected: {connected}"
            )
        else:
            lines.append("mqtt client not initialized")

        lines.append("─── DATA INPUTS ─────────────────────────")
        gps = getattr(self, "_gps_reader", None)
        if gps:
            alive = gps._thread is not None and gps._thread.is_alive()
            lines.append(f"GPS reader: port={gps._port}  thread alive: {alive}")
        else:
            lines.append("GPS reader: not configured")

        watcher = getattr(self, "_obs_watcher", None)
        if watcher:
            lines.append(
                f"obs watcher: dir={watcher._data_dir}  "
                f"file={watcher._current_path.name if watcher._current_path else '?'}  "
                f"pos={watcher._last_pos}  timer active: {watcher._timer.isActive()}"
            )
        else:
            lines.append("obs watcher: not configured")

        lines.append("─── VEHICLES ────────────────────────────")
        lines.append(f"tracked vehicles: {list(self._vehicles.keys())}")
        spl = getattr(self, "_station_layer", None)
        if spl:
            lines.append(f"station plot cache: {list(spl._cache.keys())}")

        self._debug_pill.set_status("\n".join(lines))

    def _toggle_debug_panel(self):
        if not hasattr(self, "_debug_pill"):
            self._init_debug_panel()
        else:
            self._debug_pill.toggle()

    def _cleanup_debug_panel(self):
        if hasattr(self, "_debug_pill"):
            self._debug_pill.cleanup()


    def _init_error_log_panel(self):
        import os
        self._error_log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "storm_errors.log"
        )

        self._error_log_dock = QDockWidget("ERROR LOG  (Ctrl+E to close)", self)
        self._error_log_dock.setObjectName("errorLogDock")
        self._error_log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._error_log_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._error_log_text = QLabel("no errors logged")
        self._error_log_text.setFont(QFont("Courier New", 9))
        self._error_log_text.setStyleSheet(
            "color: #FFD166; background: #050508; padding: 6px; border-radius: 4px;"
        )
        self._error_log_text.setWordWrap(True)
        self._error_log_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._error_log_text)

        self._error_log_dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._error_log_dock)

        self._error_log_timer = QTimer()
        self._error_log_timer.timeout.connect(self._refresh_error_log_panel)
        self._error_log_timer.start(3000)
        self._refresh_error_log_panel()

    def _refresh_error_log_panel(self):
        if not hasattr(self, "_error_log_text"):
            return
        import os
        path = self._error_log_path
        if not os.path.exists(path):
            self._error_log_text.setText("no errors logged yet")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = lines[-50:] if len(lines) > 50 else lines
            self._error_log_text.setText("".join(tail).rstrip() or "no errors logged yet")
        except Exception as exc:
            self._error_log_text.setText(f"(could not read log: {exc})")

    def _toggle_error_log_panel(self):
        if not hasattr(self, "_error_log_dock"):
            self._init_error_log_panel()
        else:
            self._error_log_dock.setVisible(not self._error_log_dock.isVisible())
