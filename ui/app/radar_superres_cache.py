
from collections import OrderedDict


# bounded LRU cache for rendered archive-mode super-res radar PNGs, keyed by
# (station, pyart_field, tilt_deg, scan_time_iso) so two stations/products/
# tilts/times never collide and a previously-rendered frame can be reused
# instantly instead of re-paying a ~4096px render.
class RadarSuperresCache:
    def __init__(self, capacity: int = 12):
        self._capacity = max(1, int(capacity))
        self._entries: "OrderedDict[tuple, tuple[bytes, list]]" = OrderedDict()

    @staticmethod
    def make_key(station: str, pyart_field: str, tilt_deg: float, scan_time) -> tuple:
        return (station, pyart_field, round(float(tilt_deg), 2), scan_time.isoformat())

    def get(self, key: tuple):
        entry = self._entries.get(key)
        if entry is None:
            return None
        # touch as most-recently-used
        self._entries.move_to_end(key)
        return entry

    def put(self, key: tuple, png_bytes: bytes, bounds: list) -> None:
        self._entries[key] = (png_bytes, bounds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
