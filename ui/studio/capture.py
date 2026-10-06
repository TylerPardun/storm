"""Capture finished map frames for movies and screenshots.

For an export size other than the map's own, the map's page is moved into an
invisible view of exactly that size, so MapLibre renders it natively there
(crisp at 4K, not scaled up); the zoom is raised by log2(width ratio) so the
picture shows the same area as on screen. end() moves the page back and
restores the view.

A frame is captured only once it's complete: STORM has drawn everything for
the clock's time (MainWindow.studio_frame_pending) and MapLibre reports
"idle" (rendered, every tile loaded) for a repaint asked for after that. Waiting has a limit (FRAME_TIMEOUT_S) so a source
that never answers can't stall an export; such frames are counted.
"""
from __future__ import annotations

import json
import math
import time

from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath

from core.studio import Overlays, View

FRAME_TIMEOUT_S = 25.0
POLL_MS = 40                 # STORM still loading data for this moment
READY_POLL_MS = 8            # waiting for the map to say it's done


class MapCapture(QObject):
    frame_ready = pyqtSignal(QImage)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._w = window
        self._map = window.map_widget
        self._view = None                    # the off-screen view while exporting at a set size
        self._zoom_offset = 0.0
        self._saved = None
        self._legend_was_open = None
        self.size = None
        self.slow_frames = 0
        self._waiting = None
        self._armed = False

    # ---- the page and the camera ---------------------------------------
    def js(self, code: str, callback) -> None:
        self._map.map_page().runJavaScript(code, callback)

    def read_view(self, callback) -> None:
        """callback(View) with the map's current camera."""
        self.js("JSON.stringify({lon: map.getCenter().lng, lat: map.getCenter().lat, zoom: map.getZoom(),"
                " bearing: map.getBearing(), pitch: map.getPitch()})",
                lambda r: callback(View(**json.loads(r))) if r else None)

    def begin(self, size: tuple[int, int] | None, overlays: Overlays, done) -> None:
        """Prepare to capture at `size` (None: the map's size); done() when ready."""
        self.slow_frames = 0
        self._overlays = overlays

        def saved(view):
            self._saved = view
            self.js("(function(){var b=document.getElementById('legend-body');"
                    "return !!(b && b.offsetParent);})()", got_legend)

        def got_legend(is_open):
            self._legend_was_open = bool(is_open)
            if overlays.legend != self._legend_was_open:
                self._toggle_legend()
            if size is None:
                self.size = (self._map.width(), self._map.height())
                self._zoom_offset = 0.0
                QTimer.singleShot(200, done)
                return
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self.size = size
            self._zoom_offset = math.log2(size[0] / max(1, self._map.width()))
            self._view = QWebEngineView()
            self._view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            self._view.resize(*size)
            self._view.show()
            self._view.setPage(self._map.map_page())
            QTimer.singleShot(300, lambda: self.js("map.resize(); 0", lambda _r: done()))
        self.read_view(saved)

    def _toggle_legend(self) -> None:
        self.js("(function(){var t=document.getElementById('legend-toggle'); if (t) t.click(); return 0;})()",
                lambda _r: None)

    def end(self) -> None:
        """Put the page back in the window and the view, legend and clock as they were."""
        if self._view is not None:
            self._map.setPage(self._map.map_page())
            self._view.deleteLater()
            self._view = None
            self.js("map.resize(); 0", lambda _r: None)
        if self._saved is not None:
            self.apply_view(self._saved, offset=False)
        if self._legend_was_open is not None and self._overlays.legend != self._legend_was_open:
            self._toggle_legend()
        self._zoom_offset = 0.0

    def apply_view(self, v: View, offset: bool = True) -> None:
        z = v.zoom + (self._zoom_offset if offset else 0.0)
        self.js(f"map.jumpTo({{center: [{v.lon}, {v.lat}], zoom: {z}, bearing: {v.bearing}, "
                f"pitch: {v.pitch}}}); 0", lambda _r: None)

    def apply(self, when, view: View) -> None:
        tc = self._w._time_ctrl
        tc.pause()
        if tc.current_time != when:
            tc.set_time(when)
        self.apply_view(view)

    # ---- waiting for a finished frame, then grabbing it ------------------------
    def capture_when_ready(self) -> None:
        """Emit frame_ready once the frame for the current time and view is complete."""
        self._waiting = time.monotonic()
        self._armed = False
        self._poll()

    # Once STORM has handed the map everything for this moment, the page
    # watches, each animation frame after a repaint, for every source and
    # tile to be loaded with the camera still, then lets two more frames pass
    # for that picture to reach the view. (MapLibre's "idle" would also wait
    # out its 300 ms label fades after every zoom change.)
    _ARM_JS = ("(function(){window.__stormFrameReady=false;"
               "function done(){window.__stormFrameReady=true;}"
               "function check(){if(map.loaded()&&map.areTilesLoaded()&&!map.isMoving())"
               "{requestAnimationFrame(function(){requestAnimationFrame(done);});}"
               "else{requestAnimationFrame(check);}}"
               "map.triggerRepaint();requestAnimationFrame(check);return 0;})()")

    def _poll(self) -> None:
        if self._waiting is None:
            return
        if time.monotonic() - self._waiting > FRAME_TIMEOUT_S:
            self.slow_frames += 1
            self._grab()
            return
        if self._w.studio_frame_pending() is not None:
            QTimer.singleShot(POLL_MS, self._poll)
            return
        if not self._armed:
            self._armed = True
            self.js(self._ARM_JS, lambda _r: QTimer.singleShot(READY_POLL_MS, self._poll))
            return
        self.js("window.__stormFrameReady === true", self._after_map_check)

    def _after_map_check(self, done) -> None:
        if self._waiting is None:
            return
        if done:
            self._grab()
        else:
            QTimer.singleShot(READY_POLL_MS, self._poll)

    def cancel(self) -> None:
        self._waiting = None

    def _grab(self) -> None:
        if self._waiting is None:
            return
        self._waiting = None
        view = self._view or self._map
        image = view.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        w, h = self.size
        if (image.width(), image.height()) != (w, h):
            image = image.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.frame_ready.emit(self.decorate(image))

    # ---- burned-in captions ---------------------------------------------------
    def caption_lines(self) -> list[str]:
        lines = []
        if self._overlays.time_stamp:
            t = self._w._time_ctrl.current_time
            lines.append(f"{t:%Y-%m-%d %H:%M:%S} UTC")
        if self._overlays.status_line:
            ac = self._w._archive_controls
            radar = " ".join(x for x in (ac._radar_time_label.text(), ac._radar_status.text()) if x and x != "Radar —")
            lidar = ""
            loc = getattr(self._w, "_lidar_location", None)
            if loc is not None and self._w.raw_lidar_controls.data_is_on():
                lidar = self._w.raw_lidar_controls._location_button.text()
            parts = [p for p in (f"Radar {radar}" if radar else "", lidar) if p]
            if parts:
                lines.append("   ·   ".join(parts))
        return lines

    def decorate(self, image: QImage) -> QImage:
        lines = self.caption_lines()
        if not lines:
            return image
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Helvetica Neue")
        font.setPixelSize(max(12, round(image.height() / 48)))
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        fm = p.fontMetrics()
        pad = round(font.pixelSize() * 0.6)
        width = max(fm.horizontalAdvance(l) for l in lines) + 2 * pad
        height = fm.height() * len(lines) + 2 * pad
        margin = round(font.pixelSize() * 0.8)
        box = QRectF(margin, margin, width, height)
        path = QPainterPath()
        path.addRoundedRect(box, pad * 0.8, pad * 0.8)
        p.fillPath(path, QColor(15, 15, 26, 220))
        p.setPen(QColor(73, 83, 111))
        p.drawPath(path)
        for i, line in enumerate(lines):
            p.setPen(QColor("#F2F5FA") if i == 0 else QColor("#C8D0DE"))
            p.drawText(int(margin + pad), int(margin + pad + fm.ascent() + i * fm.height()), line)
        p.end()
        return image
