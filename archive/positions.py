"""Timestamp-matched positions; never substitute a whole-day location for a scan."""
from bisect import bisect_left
from math import isfinite


class PositionTrack:
    def __init__(self, observations):
        self.observations = sorted(
            (o for o in observations if isfinite(o.lat) and isfinite(o.lon)
             and -90 <= o.lat <= 90 and -180 <= o.lon <= 180
             and (o.lat, o.lon) != (0, 0)),
            key=lambda o: o.timestamp,
        )
        self.times = [o.timestamp.timestamp() for o in self.observations]

    def nearest(self, time, max_gap_seconds=60):
        """Return an actual paired GPS fix within 60 s; ties prefer the older fix.

        No interpolation, extrapolation, or heading correction is implied.
        """
        epoch = time.timestamp() if hasattr(time, "timestamp") else float(time)
        if not isfinite(epoch):
            return None
        i = bisect_left(self.times, epoch)
        candidates = self.observations[max(0, i - 1):i + 1]
        if not candidates:
            return None
        nearest = min(candidates, key=lambda o: abs(o.timestamp.timestamp() - epoch))
        return nearest if abs(nearest.timestamp.timestamp() - epoch) <= max_gap_seconds else None
