"""Subjective mesocyclone/storm-track points, placed by hand while reviewing
archive-mode radar -- ported from MESO-VIEW's inline track-editing feature
(mm_review/app.py), adapted to STORM's local-only archive sessions (no MQTT).

Files are interchangeable with MESO-VIEW's: they're written with MESO-VIEW's
own columns (point_id, time, lat, lon, source, case_id, track_file_kind,
edited_at) -- which its reader needs -- plus, per point, the radar and
product that were on screen when it was placed. Any track file either app
wrote (or a hand-made one with time and lat/lon columns) can be loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TrackPoint:
    point_id: int
    time: datetime  # UTC
    lat: float
    lon: float
    radar_site: str = ""          # NEXRAD site id, or "NOXP"; "" when not recorded
    product: str = ""             # native code shown when placed, e.g. "N0B"/"DBZ"
    product_label: str = ""       # human-readable, e.g. "Reflectivity"
    tilt_deg: float | None = None  # elevation angle, if known
    source: str = "manual"        # MESO-VIEW's provenance tag, e.g. "moved_retimed"


_COLUMNS = (
    "point_id", "time", "lat", "lon", "source", "case_id", "track_file_kind", "edited_at",
    "radar_site", "product", "product_label", "tilt_deg",
)
# MESO-VIEW's case IDs: T/N (tornadic/non-tornadic) and a number, the prefix
# of its track file names, e.g. T10_20190528_2200_autosave_edited.csv.
_CASE_ID = re.compile(r"^([TN]\d+)_")


def track_filename(first_point_time: datetime, ext: str = "csv") -> str:
    """storm_YYYYMMDD_HHMM_track.<ext>, timestamped from the track's first
    point -- not the session start, not the export moment."""
    return f"storm_{first_point_time.strftime('%Y%m%d_%H%M')}_track.{ext}"


def default_track_dir() -> Path:
    """Where new tracks are saved: the Desktop where there is one, else home."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def new_track_path(first_point_time: datetime, directory: Path | None = None) -> Path:
    """A path for a new track that doesn't overwrite an existing file --
    two tracks started in the same minute get _2, _3, ..."""
    directory = Path(directory) if directory is not None else default_track_dir()
    path = directory / track_filename(first_point_time)
    n = 2
    while path.exists():
        path = directory / f"{path.stem.split('_track')[0]}_track_{n}.csv"
        n += 1
    return path


def case_id_for(path: Path | None) -> str:
    """MESO-VIEW case ID from a track file's name, or "" if it has none."""
    if path is None:
        return ""
    match = _CASE_ID.match(Path(path).name)
    return match[1] if match else ""


def read_track_file(path: Path) -> list[TrackPoint]:
    """Load a track from a CSV or Excel file written by STORM or MESO-VIEW
    (or any table with a time column and lat/lon). Column names are matched
    the way MESO-VIEW matches them; rows without a usable time or position
    are skipped; points come back in time order. Raises ValueError when the
    file has no time or lat/lon columns."""
    import pandas as pd

    path = Path(path)
    df = pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    for alias in ("datetime", "date", "time_utc", "utc"):
        if alias in df.columns and "time" not in df.columns:
            df = df.rename(columns={alias: "time"})
    df = df.rename(columns={k: v for k, v in (("latitude", "lat"), ("longitude", "lon"))
                            if k in df.columns and v not in df.columns})
    missing = [c for c in ("time", "lat", "lon") if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} has no {', '.join(missing)} column")

    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["time", "lat", "lon"]).sort_values("time", kind="mergesort")

    def text(row, column):
        value = row.get(column)
        return "" if value is None or pd.isna(value) else str(value)

    points, used = [], set()
    for row in df.to_dict("records"):
        point_id = pd.to_numeric(row.get("point_id"), errors="coerce")
        point_id = int(point_id) if not pd.isna(point_id) and int(point_id) not in used else None
        if point_id is None:
            point_id = max(used, default=0) + 1
        used.add(point_id)
        tilt = pd.to_numeric(row.get("tilt_deg"), errors="coerce")
        points.append(TrackPoint(
            point_id=point_id,
            time=row["time"].to_pydatetime(),
            lat=float(row["lat"]), lon=float(row["lon"]),
            radar_site=text(row, "radar_site"), product=text(row, "product"),
            product_label=text(row, "product_label"),
            tilt_deg=None if pd.isna(tilt) else float(tilt),
            source=text(row, "source") or "original",
        ))
    return points


def _rows(points: list[TrackPoint], case_id: str) -> list[dict]:
    edited_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for p in sorted(points, key=lambda p: p.time):
        t = p.time.astimezone(timezone.utc) if p.time.tzinfo else p.time.replace(tzinfo=timezone.utc)
        rows.append({
            "point_id": p.point_id,
            "time": t.strftime("%Y-%m-%d %H:%M:%S"),
            "lat": round(p.lat, 6),
            "lon": round(p.lon, 6),
            "source": p.source,
            "case_id": case_id,
            "track_file_kind": "complete",
            "edited_at": edited_at,
            "radar_site": p.radar_site,
            "product": p.product,
            "product_label": p.product_label,
            "tilt_deg": p.tilt_deg,
        })
    return rows


def write_track_csv(points: list[TrackPoint], path: Path, case_id: str = "") -> None:
    import csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(_rows(points, case_id))


def write_track_excel(points: list[TrackPoint], path: Path, case_id: str = "") -> None:
    import pandas as pd
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(_rows(points, case_id), columns=list(_COLUMNS))
    df.to_excel(path, index=False, engine="openpyxl")
