"""One-second FOFS mobile-mesonet observations for admin archive playback."""

from __future__ import annotations

import bisect
import csv
import io
import logging
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from archive.vehicle_aliases import thredds_vehicle_id
from archive.vehicle_speed import VehicleSpeed, calculate_vehicle_speed
from core.observation import Observation

log = logging.getLogger(__name__)

_FILE_ROOT = (
    "https://data.nssl.noaa.gov/thredds/fileServer/"
    "FOFS/Mobile-Mesonet/data"
)
_USER_AGENT = "Mozilla/5.0 STORM/1.0"
_FRESH_SECONDS = 60


def _ssl_context() -> ssl.SSLContext:
    """Return the compatibility context required by data.nssl.noaa.gov."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _daily_url(vehicle_id: str, date_str: str) -> str:
    directory = thredds_vehicle_id(vehicle_id)
    return f"{_FILE_ROOT}/{directory}/raw/{date_str}.txt"


def _processed_netcdf_url(vehicle_id: str, date_str: str) -> str:
    directory = thredds_vehicle_id(vehicle_id)
    return f"{_FILE_ROOT}/{directory}/processed/{directory}.mesonet.{date_str}.nc"


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_gps_timestamp(
    date_field: str,
    time_field: str,
    expected_date: str | None,
) -> datetime | None:
    """Parse a FOFS gps_date/gps_time pair, tolerating the DDMMYY/MMDDYY
    format inconsistency seen across vehicles and campaign eras (e.g. one
    vehicle's file uses DDMMYY while another vehicle's file for the same
    day uses MMDDYY). When `expected_date` (YYYYMMDD) is given, only a
    parse that actually lands on that day is accepted — a format that
    parses without error but produces a different day (seen in real FOFS
    files, where a daily file's name and its logged dates disagreed) is
    rejected rather than silently kept.
    """
    stamp = f"{date_field.strip()}{time_field.strip().zfill(6)}"
    for fmt in ("%d%m%y%H%M%S", "%m%d%y%H%M%S"):
        try:
            candidate = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if expected_date is None:
            return candidate
        if candidate.strftime("%Y%m%d") == expected_date:
            return candidate
    return None


def parse_vehicle_csv(
    text: str,
    vehicle_id: str,
    icon_type: str | None = None,
    expected_date: str | None = None,
) -> list[Observation]:
    """Parse a FOFS daily CSV file into timestamp-sorted observations.

    `expected_date` (YYYYMMDD) is optional for backward compatibility, but
    should be passed whenever the caller knows which day it requested —
    see `_parse_gps_timestamp` for why.
    """
    observations: list[Observation] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            timestamp = _parse_gps_timestamp(row["gps_date"], row["gps_time"], expected_date)
            if timestamp is None:
                continue
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        observations.append(Observation(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=timestamp,
            icon_type=icon_type,
            temperature_c=_float_or_none(row.get("t_fast")),
            dewpoint_c=_float_or_none(row.get("dewpoint")),
            wind_speed_ms=_float_or_none(row.get("sfc_wspd")),
            wind_dir_deg=_float_or_none(row.get("sfc_wdir")),
            pressure_mb=_float_or_none(row.get("pressure")),
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations


def _nc_value(array, index: int) -> "float | None":
    import numpy as np
    if array is None:
        return None
    value = float(array[index])
    return value if np.isfinite(value) else None


def parse_vehicle_netcdf(data: bytes, vehicle_id: str, icon_type: str | None = None) -> list[Observation]:
    """Parse a FOFS processed netCDF file into timestamp-sorted observations.

    Preferred over parse_vehicle_csv: `epochtime` is unambiguous UTC
    seconds-since-epoch, unlike the raw CSV's gps_date/gps_time strings,
    which have been observed in both DDMMYY and MMDDYY forms depending on
    vehicle/era (see _parse_gps_timestamp). When a vehicle-day's source
    time data is unusable, the processed file reflects that by masking
    every epochtime value rather than a downstream parser guessing at a
    wrong-but-plausible date -- a row with no valid epochtime is dropped
    here rather than assigned any timestamp.
    """
    import numpy as np
    import xarray as xr

    # decode_times=False: epochtime's non-standard units ("s since ...")
    # trip xarray's CF time auto-decoding; read it as a plain float and
    # convert manually below instead.
    with xr.open_dataset(io.BytesIO(data), engine="h5netcdf", decode_times=False) as ds:
        epoch = np.asarray(ds["epochtime"].values, dtype="float64")
        lat = np.asarray(ds["lat"].values, dtype="float64")
        lon = np.asarray(ds["lon"].values, dtype="float64")
        t_fast = np.asarray(ds["t_fast"].values, dtype="float64") if "t_fast" in ds else None
        dewpoint = np.asarray(ds["dewpoint"].values, dtype="float64") if "dewpoint" in ds else None
        sfc_wspd = np.asarray(ds["sfc_wspd"].values, dtype="float64") if "sfc_wspd" in ds else None
        sfc_wdir = np.asarray(ds["sfc_wdir"].values, dtype="float64") if "sfc_wdir" in ds else None
        pressure = np.asarray(ds["pressure"].values, dtype="float64") if "pressure" in ds else None

    observations: list[Observation] = []
    for i in range(epoch.size):
        e = epoch[i]
        la, lo = lat[i], lon[i]
        if not (np.isfinite(e) and np.isfinite(la) and np.isfinite(lo)):
            continue

        observations.append(Observation(
            vehicle_id=vehicle_id,
            lat=float(la),
            lon=float(lo),
            timestamp=datetime.fromtimestamp(float(e), tz=timezone.utc),
            icon_type=icon_type,
            temperature_c=_nc_value(t_fast, i),
            dewpoint_c=_nc_value(dewpoint, i),
            wind_speed_ms=_nc_value(sfc_wspd, i),
            wind_dir_deg=_nc_value(sfc_wdir, i),
            pressure_mb=_nc_value(pressure, i),
        ))

    observations.sort(key=lambda obs: obs.timestamp)
    return observations


class ArchiveVehicleObsFetcher(QObject):
    """Loads and replays optional one-second observations for archive vehicles."""

    observation_ready = pyqtSignal(object)
    vehicles_cleared = pyqtSignal()
    load_finished = pyqtSignal(object)  # set[str] of MQTT vehicle IDs
    error = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date_str = session_date.strftime("%Y%m%d")
        self._observations: dict[str, list[Observation]] = {}
        self._timestamps: dict[str, list[datetime]] = {}
        self._last_indices: dict[str, int] = {}
        self._last_time: datetime | None = None
        self._loaded = False

    @property
    def available_vehicle_ids(self) -> set[str]:
        return set(self._observations)

    def load(self, vehicles: dict[str, str | None]) -> None:
        """Probe and load daily files for MQTT vehicle IDs in a background thread."""
        threading.Thread(
            target=self._load_all,
            args=(dict(vehicles),),
            daemon=True,
        ).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            return
        if self._last_time is not None and archive_time < self._last_time:
            self._last_indices.clear()
            self.vehicles_cleared.emit()
        self._last_time = archive_time

        for vehicle_id in self._observations:
            index = self._index_at(vehicle_id, archive_time)
            if index < 0 or self._last_indices.get(vehicle_id) == index:
                continue
            self._last_indices[vehicle_id] = index
            self.observation_ready.emit(self._observations[vehicle_id][index])

    def has_fresh_observation(self, vehicle_id: str, archive_time: datetime) -> bool:
        """Return whether one-second data should supersede MQTT at this time."""
        index = self._index_at(vehicle_id, archive_time)
        if index < 0:
            return False
        age = (archive_time - self._observations[vehicle_id][index].timestamp).total_seconds()
        return 0 <= age <= _FRESH_SECONDS

    def history(self, vehicle_id: str, through_time: datetime) -> list[Observation]:
        """Return one-second observations for a vehicle through the requested time."""
        index = self._index_at(vehicle_id, through_time)
        if index < 0:
            return []
        return self._observations[vehicle_id][:index + 1]

    def speed(self, vehicle_id: str, archive_time: datetime) -> VehicleSpeed:
        return calculate_vehicle_speed(
            self._observations.get(vehicle_id, []),
            archive_time,
            short_seconds=1,
            average_seconds=15,
        )

    def first_vehicle_positions(self) -> list[tuple[str, float, float]]:
        result = []
        for vehicle_id, observations in self._observations.items():
            if observations:
                first = observations[0]
                result.append((vehicle_id, first.lat, first.lon))
        return result

    def _index_at(self, vehicle_id: str, archive_time: datetime) -> int:
        timestamps = self._timestamps.get(vehicle_id)
        if not timestamps:
            return -1
        return bisect.bisect_right(timestamps, archive_time) - 1

    def _load_all(self, vehicles: dict[str, str | None]) -> None:
        loaded: dict[str, list[Observation]] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._fetch_vehicle, vehicle_id, icon_type): vehicle_id
                for vehicle_id, icon_type in vehicles.items()
            }
            for future in as_completed(futures):
                vehicle_id = futures[future]
                try:
                    observations = future.result()
                    if observations:
                        loaded[vehicle_id] = observations
                except Exception as exc:
                    log.warning("One-second archive load failed for %s: %s", vehicle_id, exc)
                    errors.append(f"{vehicle_id}: {exc}")

        self._observations = loaded
        self._timestamps = {
            vehicle_id: [obs.timestamp for obs in observations]
            for vehicle_id, observations in loaded.items()
        }
        self._loaded = True
        if errors and not loaded:
            self.error.emit("; ".join(errors))
        self.load_finished.emit(set(loaded))

    def _fetch_vehicle(
        self,
        vehicle_id: str,
        icon_type: str | None,
    ) -> list[Observation]:
        nc_observations = self._fetch_vehicle_netcdf(vehicle_id, icon_type)
        if nc_observations is not None:
            # A processed file was fetched and parsed -- trust it even if
            # empty (that means the source flagged this vehicle-day's time
            # data as unusable). Falling back to the raw CSV here would
            # reintroduce exactly the wrong-date risk the netCDF path
            # avoids, since the raw file can silently carry the wrong day.
            return nc_observations
        return self._fetch_vehicle_csv(vehicle_id, icon_type)

    def _fetch_vehicle_netcdf(
        self,
        vehicle_id: str,
        icon_type: str | None,
    ) -> "list[Observation] | None":
        """Try the processed netCDF source first. Returns None (not []) on
        a missing file or parse failure, signaling the caller to fall back
        to the raw CSV; returns a (possibly empty) list on a successful
        parse, which the caller must trust as-is."""
        url = _processed_netcdf_url(vehicle_id, self._date_str)
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(request, timeout=30, context=_ssl_context()) as response:
                data = response.read()
        except HTTPError as exc:
            if exc.code in (404, 410):
                return None
            log.warning("Processed netCDF fetch failed for %s: %s", vehicle_id, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - network/SSL errors, fall back
            log.warning("Processed netCDF fetch failed for %s: %s", vehicle_id, exc)
            return None

        try:
            observations = parse_vehicle_netcdf(data, vehicle_id, icon_type)
        except Exception as exc:  # noqa: BLE001 - malformed file, fall back
            log.warning("Processed netCDF parse failed for %s: %s", vehicle_id, exc)
            return None

        log.info(
            "One-second archive: loaded %d observations for %s from %s (processed netCDF)",
            len(observations), vehicle_id, url,
        )
        return observations

    def _fetch_vehicle_csv(
        self,
        vehicle_id: str,
        icon_type: str | None,
    ) -> list[Observation]:
        url = _daily_url(vehicle_id, self._date_str)
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(request, timeout=30, context=_ssl_context()) as response:
                text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code in (404, 410):
                return []
            raise
        observations = parse_vehicle_csv(text, vehicle_id, icon_type, expected_date=self._date_str)
        log.info(
            "One-second archive: loaded %d observations for %s from %s (raw CSV fallback)",
            len(observations),
            vehicle_id,
            url,
        )
        return observations
