from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.observation import Observation
from ui.layers.station_plot_layer import _obs_fingerprint, _render

log = logging.getLogger(__name__)


def _surface_obs_fingerprint(obs: Observation) -> tuple:
    """Surface plots include age coloring, so timestamp changes must invalidate cache."""
    ts = obs.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return _obs_fingerprint(obs) + (int(ts.timestamp()),)


class SurfacePlotLayer:
    """Manages independently-toggleable surface station plots on the map."""

    def __init__(self, map_widget):
        self._map = map_widget
        self._cache: dict[str, tuple[tuple, bytes]] = {}

    def update(self, station_id: str, lat: float, lon: float, obs: Observation, name: str = "") -> None:
        fp = _surface_obs_fingerprint(obs)
        cached = self._cache.get(station_id)
        if cached and cached[0] == fp:
            return

        try:
            png_bytes = _render(obs, center_color=self._obs_age_color(obs, station_id))
        except Exception as exc:
            log.error("SurfacePlotLayer: render failed for %s: %s", station_id, exc, exc_info=True)
            return

        self._cache[station_id] = (fp, png_bytes)
        self._map.add_surface_station_plot(station_id, lat, lon, png_bytes, name=name)

    def remove(self, station_id: str) -> None:
        self._cache.pop(station_id, None)
        self._map.remove_surface_station_plot(station_id)

    def clear(self) -> None:
        for station_id in list(self._cache):
            self.remove(station_id)

    def set_visible(self, visible: bool) -> None:
        self._map.set_surface_station_plots_visible(visible)

    @staticmethod
    def _obs_age_color(obs: Observation, station_id: str = "", reference_time: datetime | None = None) -> str:
        """`reference_time` defaults to wall-clock now (live mode's original
        behavior). Archive mode must pass the archive clock's current time
        explicitly -- otherwise every archived observation, however "fresh"
        it was at the time, would compare against real now() and render as
        maximally stale."""
        ts = obs.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        now = reference_time or datetime.now(timezone.utc)
        age_min = max(0.0, (now - ts).total_seconds() / 60.0)
        if station_id.startswith("surface:asos:"):
            # asos reports hourly — use wider freshness thresholds
            if age_min <= 70.0:
                return "#39D98A"
            if age_min <= 90.0:
                return "#FFD166"
            return "#E53935"
        # ok Mesonet / WTM report every 5 min
        if age_min <= 5.0:
            return "#39D98A"
        if age_min <= 10.0:
            return "#FFD166"
        return "#E53935"
