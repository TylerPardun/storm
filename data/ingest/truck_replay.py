
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.observation import Observation

log = logging.getLogger(__name__)


def load_truck_observations(path: str | Path) -> list[Observation]:
    """
    Load truck observations from CSV/TXT with columns like:
    logger_id,gps_dt,lon,lat,alt,windSpd_ms,windDir_Der,Utube_FastTemp,TdC,Derived_RH,Pressure
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"truck replay file not found: {p}")

    observations: list[Observation] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            try:
                obs = Observation(
                    vehicle_id=_str_or(row, "logger_id", "TRUCK"),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    timestamp=_parse_gps_dt(row.get("gps_dt", "")),
                    temperature_c=_float_or_none(row.get("Utube_FastTemp")),
                    dewpoint_c=_float_or_none(row.get("TdC")),
                    wind_speed_ms=_float_or_none(row.get("windSpd_ms")),
                    wind_dir_deg=_float_or_none(row.get("windDir_Der")),
                    pressure_mb=_float_or_none(row.get("Pressure")),
                )
            except Exception as exc:
                log.warning("truck replay row %d skipped: %s", i, exc)
                continue
            observations.append(obs)

    return observations


def _parse_gps_dt(raw: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        return datetime.now(timezone.utc)

    # common logger encodings encountered in field data.
    formats = [
        "%d%m%y%H%M%S",  # 060625010200 -> 2025-06-06 01:02:00
        "%y%m%d%H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _float_or_none(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _str_or(row: dict, key: str, default: str) -> str:
    text = str(row.get(key, "")).strip()
    return text or default
