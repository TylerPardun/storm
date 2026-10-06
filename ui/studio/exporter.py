"""Render a Video Studio project to a file, frame by frame (core/studio.py
plan -> ui/studio/capture.py -> ui/studio/writer.py).

Each frame sets the clock and the camera, waits for the map to finish
drawing that moment, captures it and hands it to the writer. A frame identical
to the one before (a hold, or a camera standing still while the clock stays
put) reuses the last image instead of waiting again.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.studio import RESOLUTIONS, Project
from ui.studio.capture import MapCapture
from ui.studio.writer import FrameWriter


class StudioExporter(QObject):
    progress = pyqtSignal(int, int, str)     # done, total, detail
    finished = pyqtSignal(bool, str)         # ok, message

    def __init__(self, window, project: Project, path: Path, parent=None):
        super().__init__(parent)
        self._w, self._p, self._path = window, project, Path(path)
        ac = getattr(window, "_archive_controls", None)
        self._frames = project.plan(getattr(ac, "_scan_times", []) if ac is not None else [])
        self._radars = [project.radar_at(i / project.fps) for i in range(len(self._frames))]
        self._radar_was = window.studio_radar_state()
        self._i = 0
        self._last_key = None
        self._last_image = None
        self._pending_image = None
        self._canceled = False
        self._done = False
        self._t0 = time.monotonic()
        self._clock_was = window._time_ctrl.current_time
        self.capture = MapCapture(window, self)
        self.capture.frame_ready.connect(self._on_frame)
        self.writer = None

    def start(self) -> None:
        if not self._frames:
            self.finished.emit(False, "No keyframes")
            return
        self.capture.begin(RESOLUTIONS.get(self._p.resolution), self._p.overlays, self._ready_to_render)

    def _ready_to_render(self) -> None:
        w, h = self.capture.size
        w -= w % 2                       # H.264 needs even dimensions
        h -= h % 2
        self.capture.size = (w, h)
        self.writer = FrameWriter(self._path, self._p.format, (w, h), self._p.fps, self)
        self.writer.ready.connect(self._flush)
        self.writer.finished.connect(self._on_written)
        QTimer.singleShot(0, self._next)

    def cancel(self) -> None:
        if self._canceled or self._done:
            return
        self._canceled = True
        self.capture.cancel()
        if self.writer is not None:
            self.writer.cancel()
        self._restore()
        self.finished.emit(False, "Export canceled")

    # ---- the loop ---------------------------------------------------------
    def _next(self) -> None:
        if self._canceled:
            return
        if self._i >= len(self._frames):
            n = len(self._frames)
            self.progress.emit(n, n, "Writing the file…")
            self.writer.finish()
            return
        when, view = self._frames[self._i]
        radar = self._radars[self._i]
        key = (when, json.dumps(radar, sort_keys=True), round(view.lon, 7), round(view.lat, 7), round(view.zoom, 5),
               round(view.bearing, 4), round(view.pitch, 4))
        if key == self._last_key and self._last_image is not None:
            self._on_frame(self._last_image)
            return
        clock_moved = self._last_key is None or key[:2] != self._last_key[:2]
        self._last_key = key
        self._w.studio_apply_radar(radar)
        self.capture.apply(when, view)
        # a new time sets data loading going; a camera move alone needs no settling
        QTimer.singleShot(30 if clock_moved else 0, self.capture.capture_when_ready)

    def _on_frame(self, image) -> None:
        if self._canceled:
            return
        self._last_image = image
        self._pending_image = image
        self._flush()

    def _flush(self) -> None:
        if self._pending_image is None or self._canceled:
            return
        if not self.writer.write(self._pending_image):
            return                                   # the encoder will say when it's ready
        self._pending_image = None
        self._i += 1
        elapsed = time.monotonic() - self._t0
        left = elapsed / self._i * (len(self._frames) - self._i)
        when = self._frames[min(self._i, len(self._frames) - 1)][0]
        self.progress.emit(self._i, len(self._frames),
                           f"{when:%H:%M:%S} UTC · about {left/60:.0f} min left" if left >= 90
                           else f"{when:%H:%M:%S} UTC · about {left:.0f} s left")
        QTimer.singleShot(0, self._next)

    def _on_written(self, ok: bool, message: str) -> None:
        if self._canceled:
            return
        self._done = True
        self._restore()
        if ok and self.capture.slow_frames:
            message += (f" ({self.capture.slow_frames} frame{'s' if self.capture.slow_frames != 1 else ''} "
                        "captured before all data had arrived)")
        self.finished.emit(ok, message)

    def _restore(self) -> None:
        self.capture.end()
        self._w.studio_apply_radar(self._radar_was)
        self._w._time_ctrl.set_time(self._clock_was)
