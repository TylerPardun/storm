"""Find the PPI scans in a raw-lidar file's rays (archive/fetchers/
raw_lidar_archive_fetcher.py RawLidarRays): azimuth sweeps at a low
elevation (under 45 deg), the only scans STORM shows -- on the map, like
radar volumes. They come from the lidars' PPI files and the sector sweeps in
their CSM files; anything else in those files (high-elevation VAD rings,
fixed pointing) is left out.

Rays belong to one scan while the scan number stays the same and no more
than MAX_GAP_S passes between rays. Whether rays sweep comes from how the
lidar itself was pointed (RawLidarRays.instrument_azimuth_deg, as stored):
the truck's true-north azimuths turn with the truck, which would make fixed
pointing look like a sweep whenever it turns. Reported azimuths are true
north.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

MAX_GAP_S = 10.0          # a longer pause between rays ends a scan
SWEEP_DEG = 20.0          # an azimuth span beyond this is a sweep
FLAT_DEG = 2.0            # a PPI's elevation varies less than this
MAX_ELEVATION_DEG = 45.0  # above this a sweep is a VAD ring, not a PPI


@dataclass(frozen=True, eq=False)       # scans compare by identity (they hold arrays)
class LidarScan:
    """One PPI sweep."""
    indices: np.ndarray       # ray indices into the file, in time order
    start: datetime
    end: datetime
    elevation_deg: float      # median
    north_referenced: bool = True         # False: azimuths are relative to the truck (heading unknown)
    source: object = None                 # the RawLidarRays (file) the scan's rays are in

    @property
    def rays(self) -> int:
        return int(self.indices.size)

    def describe(self) -> str:
        """e.g. 'PPI 18:49:29Z · 1.0° · 120 rays'"""
        return f"PPI {self.start:%H:%M:%SZ} · {self.elevation_deg:.1f}° · {self.rays} rays"


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _azimuth_span(azimuth: np.ndarray) -> float:
    if azimuth.size < 2:
        return 0.0
    unwrapped = np.rad2deg(np.unwrap(np.deg2rad(azimuth)))
    return float(np.ptp(unwrapped))


def _is_ppi(elevation: np.ndarray, pointing: np.ndarray) -> bool:
    if elevation.size < 3 or _azimuth_span(pointing) <= SWEEP_DEG:
        return False
    return float(np.ptp(elevation)) < FLAT_DEG and float(np.median(elevation)) < MAX_ELEVATION_DEG


def _make(idx, times, elevation, known) -> LidarScan:
    el = elevation[idx]
    finite_el = el[np.isfinite(el)]
    return LidarScan(
        indices=idx, start=_utc(times[idx[0]]), end=_utc(times[idx[-1]]),
        elevation_deg=float(np.median(finite_el)) if finite_el.size else float("nan"),
        north_referenced=bool(np.all(known[idx])),
    )


def classify_scans(rays) -> list[LidarScan]:
    """The file's PPI scans in time order (see module docstring)."""
    times = np.asarray(rays.time_epoch, dtype=float)
    if times.size == 0:
        return []
    elevation = np.asarray(rays.elevation_deg, dtype=float)
    azimuth = np.asarray(rays.azimuth_deg, dtype=float)
    pointing = getattr(rays, "instrument_azimuth_deg", None)
    pointing = azimuth if pointing is None else np.asarray(pointing, dtype=float)
    number = np.asarray(rays.scan_number, dtype=float)
    known = getattr(rays, "azimuth_known", None)
    if known is None:
        referenced = (getattr(rays, "provenance", None) or {}).get("north_referenced", True) is not False
        known = np.full(times.size, referenced)
    known = np.asarray(known, dtype=bool)
    new_number = np.zeros(times.size - 1, dtype=bool)
    if np.isfinite(number).any():
        new_number = np.nan_to_num(np.diff(number), nan=1.0) != 0
    breaks = np.flatnonzero((np.diff(times) > MAX_GAP_S) | new_number) + 1
    scans: list[LidarScan] = []
    for idx in np.split(np.arange(times.size), breaks):
        # a scan number spanning several elevations (e.g. files without
        # one): split wherever the elevation jumps, and test each part
        jumps = np.flatnonzero(np.abs(np.diff(elevation[idx])) > FLAT_DEG) + 1 if idx.size >= 2 else []
        for part in (np.split(idx, jumps) if len(jumps) else [idx]):
            if _is_ppi(elevation[part], pointing[part]):
                scans.append(_make(part, times, elevation, known))
    for scan in scans:
        object.__setattr__(scan, "source", rays)
    return scans


def scan_at(scans: list[LidarScan], when: datetime, hold_s: float = 600.0):
    """The latest scan begun at or before `when` and ended no more than
    hold_s before it (a scan stays on screen until the next one, like a
    radar volume, but not indefinitely)."""
    best = None
    for scan in scans:
        if scan.start <= when:
            best = scan
    if best is None or (when - best.end).total_seconds() > hold_s:
        return None
    return best


# ---- one lidar's scans across its files, like a radar's volumes ------------

PERIOD_JOIN_S = 900.0     # scans closer than this are one scanning period


def timeline(files, start: datetime | None = None, end: datetime | None = None) -> list[LidarScan]:
    """Every scan from a lidar's files (RawLidarRays with .scans), within
    [start, end] when given, in time order -- what the lidar did during the
    session, whichever file each scan was published in."""
    scans = []
    for rays in files:
        for scan in getattr(rays, "scans", None) or classify_scans(rays):
            if (start is None or scan.end >= start) and (end is None or scan.start <= end):
                scans.append(_trim(scan, start, end))
    return sorted(scans, key=lambda s: (s.start, s.end))


def _trim(scan: LidarScan, start, end) -> LidarScan:
    """A scan that runs past the session's edges, cut to the rays inside it."""
    if (start is None or scan.start >= start) and (end is None or scan.end <= end):
        return scan
    import dataclasses
    times = np.asarray(scan.source.time_epoch, dtype=float)[scan.indices]
    keep = np.ones(times.size, dtype=bool)
    if start is not None:
        keep &= times >= start.timestamp()
    if end is not None:
        keep &= times <= end.timestamp()
    idx = scan.indices[keep]
    return dataclasses.replace(scan, indices=idx, start=_utc(times[keep][0]), end=_utc(times[keep][-1]))


def periods(scans: list[LidarScan], join_s: float = PERIOD_JOIN_S) -> list[tuple[datetime, datetime]]:
    """When the lidar was scanning: runs of scans no more than join_s apart."""
    out: list[list[datetime]] = []
    for scan in sorted(scans, key=lambda s: s.start):
        if out and (scan.start - out[-1][1]).total_seconds() <= join_s:
            out[-1][1] = max(out[-1][1], scan.end)
        else:
            out.append([scan.start, scan.end])
    return [(a, b) for a, b in out]


def describe_periods(spans: list[tuple[datetime, datetime]]) -> str:
    """e.g. 'Scanning 18:49–19:23Z, 20:07–22:10Z'; a span that crosses into
    another UTC day names the days: 'Scanning 12:03Z May 17 – 02:15Z May 18'."""
    if not spans:
        return "No scans this session"
    first_day = spans[0][0].date()

    def span(a, b):
        if a.date() == b.date() == first_day:
            return f"{a:%H:%M}–{b:%H:%M}Z"
        if a.date() == b.date():
            return f"{a:%H:%M}–{b:%H:%M}Z {b:%b} {b.day}"
        return f"{a:%H:%M}Z {a:%b} {a.day} – {b:%H:%M}Z {b:%b} {b.day}"
    return "Scanning " + ", ".join(span(a, b) for a, b in spans)


# ---- where each lidar scanned from: the locations to click, like radar sites --

STOP_RADIUS_M = 250.0     # scans within this of a stop's first scan are that stop


@dataclass(eq=False)
class LidarLocation:
    """One place a lidar scanned from during the session: a trailer's site,
    or one of the truck's stops. Its position is where its first scan was
    taken; `scans` are every scan made there, in time order."""
    instrument: str
    number: int               # 1-based, in time order among the instrument's locations
    lat: float
    lon: float
    scans: list
    place: str = ""           # a trailer's site description

    @property
    def start(self) -> datetime:
        return self.scans[0].start

    @property
    def end(self) -> datetime:
        return max(s.end for s in self.scans)

    @property
    def key(self) -> str:
        return f"{self.instrument}-{self.number}"


def _scan_position(scan) -> tuple[float, float] | None:
    rays = scan.source
    idx = scan.indices
    lat = np.asarray(rays.latitude, dtype=float)[idx]
    lon = np.asarray(rays.longitude, dtype=float)[idx]
    good = np.isfinite(lat) & np.isfinite(lon)
    if not good.any():
        return None
    i = int(np.flatnonzero(good)[0])
    return float(lat[i]), float(lon[i])


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return 6371000.0 * math.hypot(dlat, dlon)


def locations(instrument: str, scans: list[LidarScan], site: dict | None = None,
              radius_m: float = STOP_RADIUS_M) -> tuple[list[LidarLocation], int]:
    """(the instrument's locations, scans that had no position). A trailer
    (`site` given) is one location at its site; the truck gets one per stop:
    consecutive scans within radius_m of the stop's first scan."""
    ordered = sorted(scans, key=lambda s: (s.start, s.end))
    if site is not None:
        found = [LidarLocation(instrument, 1, site["lat"], site["lon"], ordered, site.get("description", ""))]
        return (found if ordered else []), 0
    found: list[LidarLocation] = []
    unplaced = 0
    for scan in ordered:
        position = _scan_position(scan)
        if position is None:
            unplaced += 1
            continue
        if found and _metres((found[-1].lat, found[-1].lon), position) <= radius_m:
            found[-1].scans.append(scan)
        else:
            found.append(LidarLocation(instrument, len(found) + 1, position[0], position[1], [scan]))
    return found, unplaced
