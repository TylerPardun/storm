"""Date-driven CLAMPS surface playback, independent of MQTT participation."""
from bisect import bisect_right
from threading import Thread, Event
from PyQt6.QtCore import QObject, pyqtSignal


class ClampsSurfacePlayback(QObject):
    loaded = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = Event()
        self.rows = {}
        self.times = {}

    def load(self, day):
        Thread(target=self._load, args=(day,), daemon=True).start()

    def _load(self, day):
        from archive.fetchers.clamps_surface_archive_fetcher import fetch_clamps_surface_observations
        try:
            rows = fetch_clamps_surface_observations(day) or []
            if not self._closed.is_set():
                self.loaded.emit(rows)
        except Exception as exc:
            if not self._closed.is_set():
                self.error.emit(str(exc))

    def install(self, rows):
        self.rows = {}
        for obs in rows:
            obs.icon_type = 'lidar'
            self.rows.setdefault(obs.vehicle_id, []).append(obs)
        for rows in self.rows.values():
            rows.sort(key=lambda obs: obs.timestamp)
        self.times = {key: [obs.timestamp for obs in rows] for key, rows in self.rows.items()}

    def at(self, key, when):
        idx = bisect_right(self.times.get(key, []), when) - 1
        if idx < 0:
            return None
        obs = self.rows[key][idx]
        return obs if (when - obs.timestamp).total_seconds() <= 300 else None

    def history(self, key, when):
        return self.rows.get(key, [])[:bisect_right(self.times.get(key, []), when)]

    def shutdown(self):
        self._closed.set()
