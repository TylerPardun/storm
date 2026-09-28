"""Split a raw-lidar file's rays (archive/fetchers/raw_lidar_archive_fetcher.py
RawLidarRays) into the scans the lidar actually ran, and name each by its
geometry rather than by the file it was published in -- a "ppi" file can hold
VAD rings and stares, an "other" file RHIs:

    PPI    azimuth sweep at a low elevation (under 45 deg)  -> map
    VAD    azimuth ring at a high elevation (45 deg and up) -> map
    Beam   fixed pointing at a low elevation (under 45 deg) -> map, as a line
    RHI    elevation sweep at one azimuth                   -> cross-section viewer
    Stare  fixed pointing at 45 deg and up (e.g. vertical)  -> time-height viewer

Rays belong to one scan while the scan number stays the same and no more than
MAX_GAP_S passes between rays. Consecutive stares at the same pointing are
joined into one stare period. The kind comes from how the lidar itself was
pointed (RawLidarRays.instrument_azimuth_deg, as stored): the truck's true-
north azimuths turn with the truck, which would make a stare look like a sweep
whenever it turns. Reported azimuths are true north.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

MAX_GAP_S = 10.0          # a longer pause between rays ends a scan
SWEEP_DEG = 20.0          # an azimuth span beyond this is a sweep
FIXED_DEG = 2.0           # pointing within this counts as fixed
STARE_JOIN_S = 120.0      # stares at the same pointing this close join
MAP_KINDS = ("PPI", "VAD", "Beam")
VERTICAL_KINDS = ("RHI", "Stare")


@dataclass(frozen=True, eq=False)       # scans compare by identity (they hold arrays)
class LidarScan:
    kind: str                 # "PPI", "VAD", "RHI" or "Stare"
    indices: np.ndarray       # ray indices into the file, in time order
    start: datetime
    end: datetime
    elevation_deg: float      # median
    azimuth_deg: float        # median (for an RHI, its azimuth)
    elevation_span: tuple[float, float]
    azimuth_span_deg: float
    pointing_deg: float = float("nan")    # median azimuth as the lidar was pointed (stored)
    north_referenced: bool = True         # False: azimuths are relative to the truck (heading unknown)
    source: object = None                 # the RawLidarRays (file) the scan's rays are in

    @property
    def rays(self) -> int:
        return int(self.indices.size)

    @property
    def mappable(self) -> bool:
        return self.kind in MAP_KINDS

    def azimuth_text(self) -> str:
        """'az 320.6°', or '... relative to the truck' when its heading is unknown."""
        return (f"az {self.azimuth_deg:.1f}°" if self.north_referenced
                else f"az {self.azimuth_deg:.1f}° relative to the truck (heading unknown)")

    def describe(self) -> str:
        """e.g. 'VAD 18:49:29Z · 60.0° · 8 rays'"""
        when = self.start.strftime("%H:%M:%SZ")
        if self.kind == "RHI":
            lo, hi = self.elevation_span
            return f"RHI {when} · {self.azimuth_text()} · {lo:.0f}–{hi:.0f}° · {self.rays} rays"
        if self.kind in ("Stare", "Beam"):
            span = f"–{self.end:%H:%M:%SZ}" if self.end > self.start else ""
            where = f" · {self.azimuth_text()}" if self.kind == "Beam" else ""
            return f"{self.kind} {when}{span} · el {self.elevation_deg:.0f}°{where} · {self.rays} rays"
        return f"{self.kind} {when} · {self.elevation_deg:.1f}° · {self.rays} rays"


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _azimuth_span(azimuth: np.ndarray) -> float:
    if azimuth.size < 2:
        return 0.0
    unwrapped = np.rad2deg(np.unwrap(np.deg2rad(azimuth)))
    return float(np.ptp(unwrapped))


def _kind(elevation: np.ndarray, azimuth: np.ndarray) -> str:
    az_span = _azimuth_span(azimuth)
    el_span = float(np.ptp(elevation)) if elevation.size > 1 else 0.0
    if elevation.size >= 3 and el_span > 5 and az_span < FIXED_DEG:
        return "RHI"
    if elevation.size >= 3 and az_span > SWEEP_DEG and el_span < FIXED_DEG:
        return "VAD" if float(np.median(elevation)) >= 45 else "PPI"
    if az_span < FIXED_DEG and el_span < FIXED_DEG:
        return "Stare" if float(np.median(elevation)) >= 45 else "Beam"
    return "Other"


def _make(kind, idx, times, elevation, azimuth, pointing=None, known=None) -> LidarScan:
    el, az = elevation[idx], azimuth[idx]
    point = (azimuth if pointing is None else pointing)[idx]
    finite_el = el[np.isfinite(el)]
    return LidarScan(
        kind=kind, indices=idx, start=_utc(times[idx[0]]), end=_utc(times[idx[-1]]),
        elevation_deg=float(np.median(finite_el)) if finite_el.size else float("nan"),
        azimuth_deg=float(np.median(az[np.isfinite(az)])) % 360 if np.isfinite(az).any() else float("nan"),
        elevation_span=(float(np.nanmin(el)), float(np.nanmax(el))) if finite_el.size else (float("nan"),) * 2,
        azimuth_span_deg=_azimuth_span(point[np.isfinite(point)]),
        pointing_deg=float(np.median(point[np.isfinite(point)])) % 360 if np.isfinite(point).any() else float("nan"),
        north_referenced=True if known is None else bool(np.all(known[idx])),
    )


def classify_scans(rays) -> list[LidarScan]:
    """The file's scans in time order (see module docstring)."""
    times = np.asarray(rays.time_epoch, dtype=float)
    if times.size == 0:
        return []
    elevation = np.asarray(rays.elevation_deg, dtype=float)
    azimuth = np.asarray(rays.azimuth_deg, dtype=float)                 # true north, for reporting
    pointing = getattr(rays, "instrument_azimuth_deg", None)
    pointing = azimuth if pointing is None else np.asarray(pointing, dtype=float)   # for classifying
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
        kind = _kind(elevation[idx], pointing[idx])
        if kind == "Other" and idx.size >= 2:
            # a scan number spanning several pointings (e.g. files without
            # one): split wherever the pointing jumps, and classify each part
            jumps = np.flatnonzero((np.abs(np.diff(elevation[idx])) > FIXED_DEG)
                                   & (np.abs(np.diff(pointing[idx])) > FIXED_DEG)) + 1
            parts = np.split(idx, jumps) if jumps.size else [idx]
        else:
            parts = [idx]
        for part in parts:
            scans.append(_make(_kind(elevation[part], pointing[part]), part, times, elevation, azimuth, pointing, known))
    scans = _join_stares(scans, times, elevation, azimuth, pointing, known)
    for scan in scans:
        object.__setattr__(scan, "source", rays)
    return scans


def _join_stares(scans, times, elevation, azimuth, pointing, known) -> list[LidarScan]:
    joined: list[LidarScan] = []
    for scan in scans:
        last = joined[-1] if joined else None
        if (last is not None and last.kind == scan.kind and scan.kind in ("Stare", "Beam")
                and (scan.start - last.end).total_seconds() <= STARE_JOIN_S
                and abs(scan.elevation_deg - last.elevation_deg) < FIXED_DEG
                and abs(((scan.pointing_deg - last.pointing_deg + 180) % 360) - 180) < FIXED_DEG):
            joined[-1] = _make(scan.kind, np.concatenate([last.indices, scan.indices]), times, elevation, azimuth, pointing, known)
        else:
            joined.append(scan)
    return joined


def counts(scans: list[LidarScan]) -> str:
    """e.g. '55 VAD · 21 stares'"""
    from collections import Counter
    c = Counter(s.kind for s in scans)
    order = ("PPI", "VAD", "Beam", "RHI", "Stare", "Other")
    words = {"Stare": ("vertical stare", "vertical stares"), "Beam": ("fixed beam", "fixed beams"),
             "Other": ("other scan", "other scans")}
    parts = []
    for kind in order:
        if c[kind]:
            one, many = words.get(kind, (kind, kind))
            parts.append(f"{c[kind]} {one if c[kind] == 1 else many}")
    return " · ".join(parts)


def scan_at(scans: list[LidarScan], when: datetime, kinds=MAP_KINDS, hold_s: float = 600.0):
    """The latest scan of `kinds` begun at or before `when` and ended no more
    than hold_s before it (a scan stays on screen until the next one, like a
    radar volume, but not indefinitely)."""
    best = None
    for scan in scans:
        if scan.kind in kinds and scan.start <= when:
            best = scan
    if best is None or (when - best.end).total_seconds() > hold_s:
        return None
    return best


def nearest_scan_time(scans: list[LidarScan], when: datetime, kinds=MAP_KINDS):
    """Where to move the clock to show a scan of `kinds`: the end of the
    first one at or after `when` (so the whole scan is in), else the end of
    the last one before it; None when there are none."""
    chosen = [s for s in scans if s.kind in kinds]
    if not chosen:
        return None
    after = [s for s in chosen if s.end >= when]
    return (after[0] if after else chosen[-1]).end


def step(scans: list[LidarScan], when: datetime, direction: int, kinds=None):
    """The scan before (-1) or after (+1) the one on screen at `when`."""
    chosen = [s for s in scans if kinds is None or s.kind in kinds]
    if not chosen:
        return None
    if direction > 0:
        return next((s for s in chosen if s.start > when), None)
    earlier = [s for s in chosen if s.end < when and s.start < when]
    current = [s for s in chosen if s.start <= when <= s.end]
    if current:
        earlier = [s for s in chosen if s.end < current[0].start]
    return earlier[-1] if earlier else None


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
                scans.append(scan)
    return sorted(scans, key=lambda s: (s.start, s.end))


def periods(scans: list[LidarScan], join_s: float = PERIOD_JOIN_S) -> list[tuple[datetime, datetime]]:
    """When the lidar was scanning: runs of scans no more than join_s apart."""
    out: list[list[datetime]] = []
    for scan in sorted(scans, key=lambda s: s.start):
        if out and (scan.start - out[-1][1]).total_seconds() <= join_s:
            out[-1][1] = max(out[-1][1], scan.end)
        else:
            out.append([scan.start, scan.end])
    return [(a, b) for a, b in out]


def scanning_at(scans: list[LidarScan], when: datetime, join_s: float = PERIOD_JOIN_S) -> bool:
    return any(a <= when <= b for a, b in periods(scans, join_s))


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
