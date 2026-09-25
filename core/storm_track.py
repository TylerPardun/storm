"""Subjective mesocyclone/storm-track points, placed by hand while reviewing
archive-mode radar -- ported from MESO-VIEW's inline track-editing feature
(mm_review/app.py), adapted to STORM's local-only archive sessions (no MQTT).

Unlike MESO-VIEW's own CSV schema (point_id, time, lat, lon, source, case_id,
track_file_kind, edited_at -- no radar/product columns at all), each point
here also records which radar and product were on screen when it was placed,
since that's useful context for later analysis and costs nothing to capture.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TrackPoint:
    point_id: int
    time: datetime  # UTC
    lat: float
    lon: float
    radar_site: str        # NEXRAD site id, or "NOXP"
    product: str            # native code shown when placed, e.g. "N0B"/"DBZ"
    product_label: str      # human-readable, e.g. "Reflectivity"
    tilt_deg: float | None  # elevation angle, if known


_COLUMNS = (
    "point_id", "time", "lat", "lon",
    "radar_site", "product", "product_label", "tilt_deg",
)


def track_filename(first_point_time: datetime, ext: str = "csv") -> str:
    """storm_YYYYMMDD_HHMM_track.<ext>, timestamped from the track's first
    point -- not the session start, not the export moment."""
    return f"storm_{first_point_time.strftime('%Y%m%d_%H%M')}_track.{ext}"


def default_track_dir() -> Path:
    return Path.home() / "Desktop"


def _rows(points: list[TrackPoint]) -> list[dict]:
    rows = []
    for p in points:
        t = p.time.astimezone(timezone.utc) if p.time.tzinfo else p.time.replace(tzinfo=timezone.utc)
        rows.append({
            "point_id": p.point_id,
            "time": t.strftime("%Y-%m-%d %H:%M:%S"),
            "lat": round(p.lat, 6),
            "lon": round(p.lon, 6),
            "radar_site": p.radar_site,
            "product": p.product,
            "product_label": p.product_label,
            "tilt_deg": p.tilt_deg,
        })
    return rows


def write_track_csv(points: list[TrackPoint], path: Path) -> None:
    import csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(_rows(points))


def write_track_excel(points: list[TrackPoint], path: Path) -> None:
    import pandas as pd
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(_rows(points), columns=list(_COLUMNS))
    df.to_excel(path, index=False, engine="openpyxl")
