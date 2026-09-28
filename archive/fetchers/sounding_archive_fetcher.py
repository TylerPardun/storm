
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet, PRESSURE_LEVELS
from data.fetchers.obs_sounding_fetcher import (
    _fetch_profiles_for_timestamp,
    _format_label,
    _parse_profile,
)

log = logging.getLogger(__name__)

_OPEN_METEO_ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
_IEM_RAOB_URL     = "https://mesonet.agron.iastate.edu/json/raob.py"
_ARCHIVE_MODEL = "ncep_hrrr_conus"
_REQUEST_TIMEOUT = 30
_LEVEL_VARS = (
    "temperature",
    "dew_point",
    "wind_u_component",
    "wind_v_component",
    "geopotential_height",
)


class ArchiveSoundingFetcher(QObject):
    """
    On-demand sounding fetcher for archive mode.

    Unlike the live sounding fetcher, this is triggered by a user map click
    (lat/lon) combined with the current archive time rather than "now".

    Signals
    -------
    sounding_ready(SoundingSet)
    fetch_error(str)
    """

    coptersondes_ready = pyqtSignal(object)
    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_archive_time: Optional[datetime] = None


    def on_time_changed(self, archive_time: datetime) -> None:
        self._current_archive_time = archive_time

    def fetch_model_sounding(self, lat: float, lon: float) -> None:
        """Fetch RUC/RAP analysis sounding at lat/lon for the current archive time."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_model,
            args=(lat, lon, t),
            daemon=True,
        ).start()

    def fetch_obs_sounding(self, station_id: str, lat: float, lon: float, elev: float) -> None:
        """Fetch observed radiosonde sounding from IEM RAOB archive."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_obs,
            args=(station_id, lat, lon, elev, t),
            daemon=True,
        ).start()

    def fetch_nssl_sounding(self) -> None:
        """Fetch the NSSL mobile sounding nearest to the current archive time."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_nssl,
            args=(t,),
            daemon=True,
        ).start()


    def fetch_coptersondes(self):
        t = self._current_archive_time
        if t is not None:
            threading.Thread(target=self._do_fetch_coptersondes, args=(t,), daemon=True).start()

    def _do_fetch_coptersondes(self, t):
        from archive.fetchers.coptersonde_archive_fetcher import fetch_coptersonde_soundings
        try:
            sets = fetch_coptersonde_soundings(t)
            if self._current_archive_time is not None and self._current_archive_time.date() == t.date():
                self.coptersondes_ready.emit(sets)
        except Exception as exc:
            self.fetch_error.emit(f"CopterSonde: {exc}")

    def _do_fetch_model(self, lat: float, lon: float, t: datetime) -> None:
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "models": _ARCHIVE_MODEL,
                "start_date": t.strftime("%Y-%m-%d"),
                "end_date": t.strftime("%Y-%m-%d"),
                "hourly": _build_hourly_params(),
                "wind_speed_unit": "ms",
                "timezone": "UTC",
            }
            resp = requests.get(_OPEN_METEO_ARCHIVE_URL, params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            sounding, elevation = _parse_open_meteo_archive(resp.json(), lat, lon, t)
            if sounding is None:
                self.fetch_error.emit("No model sounding data returned")
                return
            sset = SoundingSet(
                lat=lat, lon=lon, elevation=elevation,
                fetch_time=t,
                soundings=[sounding],
                source="hrrr",
            )
            self.sounding_ready.emit(sset)
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: model sounding failed: %s", exc)
            self.fetch_error.emit(f"Model sounding error: {exc}")


    def _do_fetch_obs(
        self, station_id: str, lat: float, lon: float, elev: float, t: datetime
    ) -> None:
        try:
            raw_profiles = []
            for ts in _archive_timestamps_to_query(t):
                try:
                    raw_profiles.extend(_fetch_profiles_for_timestamp(station_id, ts))
                except Exception as exc:
                    log.warning(
                        "ArchiveSoundingFetcher: failed RAOB fetch for %s at %s: %s",
                        station_id, ts, exc,
                    )

            if not raw_profiles:
                self.fetch_error.emit(f"No observed sounding data returned for {station_id}")
                return

            soundings = []
            for idx, profile in enumerate(raw_profiles):
                valid_str = profile.get("valid", "")
                try:
                    valid_time = datetime.fromisoformat(valid_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    log.warning(
                        "ArchiveSoundingFetcher: unparseable valid time %r for %s",
                        valid_str, station_id,
                    )
                    continue
                if valid_time > t:
                    continue
                sounding = _parse_profile(
                    lat, lon, elev, profile.get("profile", []), valid_time, idx
                )
                if sounding is not None:
                    soundings.append(sounding)

            if not soundings:
                self.fetch_error.emit(f"No valid sounding time slots found for {station_id}")
                return

            deduped: dict[datetime, Sounding] = {}
            for sounding in soundings:
                deduped[sounding.valid_time] = sounding

            ordered = list(deduped.values())
            ordered.sort(key=lambda s: s.valid_time)
            for i, sounding in enumerate(ordered):
                sounding.slot_offset = i
                sounding.label = _format_label(sounding.valid_time)

            sset = SoundingSet(
                lat=lat, lon=lon, elevation=elev,
                fetch_time=t,
                soundings=ordered,
                station_id=station_id,
                station_name=station_id,
                source="obs",
            )
            self.sounding_ready.emit(sset)
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: obs sounding failed: %s", exc)
            self.fetch_error.emit(f"Observed sounding error: {exc}")


    def _do_fetch_nssl(self, t: datetime) -> None:
        """Try archived launches, the API, then clearly labeled TROPoe retrievals.

        Provider failures cannot short-circuit another provider. Every result
        is limited to the selected UTC day and archive time, including TROPoe.
        """
        from archive.fetchers.clamps_sonde_archive_fetcher import fetch_clamps_sonde_soundings
        from archive.fetchers.clamps_tropoe_archive_fetcher import fetch_clamps_tropoe_soundings
        from data.fetchers.clamps_sounding_fetcher import (
            _api_sonde_entries, _fetch_and_parse_url, _soundings_to_set,
        )

        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)

        def api_soundings():
            soundings = []
            for idx, entry in enumerate(sorted(_api_sonde_entries(), key=lambda e: e.file_time)):
                if not day_start <= entry.file_time <= t:
                    continue
                try:
                    snd = _fetch_and_parse_url(entry.skewt_url, entry.file_time, idx, entry.raw_url)
                    if snd is not None:
                        soundings.append(snd)
                except Exception as exc:
                    log.warning("Archive sonde file failed: %s: %s", entry.skewt_url, exc)
            return _soundings_to_set(soundings, t) if soundings else None

        errors = []
        for name, fetch in (
            ("THREDDS sonde", lambda: fetch_clamps_sonde_soundings(t)),
            ("NSSL API", api_soundings),
            ("CLAMPS TROPoe", lambda: fetch_clamps_tropoe_soundings(t)),
        ):
            try:
                sset = fetch()
                if sset is None:
                    continue
                sset.soundings = sorted(
                    (s for s in sset.soundings if day_start <= s.valid_time <= t),
                    key=lambda s: s.valid_time,
                )
                if not sset.soundings:
                    continue
                latest = sset.soundings[-1]
                if latest.location_source is not None or (latest.lat, latest.lon) != (0, 0):
                    sset.lat, sset.lon = latest.lat, latest.lon
                if latest.height.size:
                    sset.elevation = float(latest.height[0])
                self.sounding_ready.emit(sset)
                return
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                log.warning("Archive sounding provider failed: %s: %s", name, exc)
        detail = "; ".join(errors) if errors else "No profiles at or before the selected archive time"
        self.fetch_error.emit(f"NSSL/CLAMPS sounding unavailable: {detail}")



def _build_hourly_params() -> str:
    parts = []
    for level in PRESSURE_LEVELS:
        for var in _LEVEL_VARS:
            parts.append(f"{var}_{level}hPa")
    return ",".join(parts)


def _parse_open_meteo_archive(
    data: dict, lat: float, lon: float, valid_time: datetime
) -> tuple[Optional[Sounding], float]:
    import numpy as np

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    valid_time = valid_time.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    target_key = valid_time.strftime("%Y-%m-%dT%H:00")
    if target_key not in times:
        return None, float(data.get("elevation", 0.0))
    t_idx = times.index(target_key)
    site_elevation = float(data.get("elevation", 0.0))

    pressures = []
    temps = []
    dewpts = []
    u_winds = []
    v_winds = []
    heights = []

    for level in PRESSURE_LEVELS:
        values = {
            "t": hourly.get(f"temperature_{level}hPa"),
            "td": hourly.get(f"dew_point_{level}hPa"),
            "u": hourly.get(f"wind_u_component_{level}hPa"),
            "v": hourly.get(f"wind_v_component_{level}hPa"),
            "z": hourly.get(f"geopotential_height_{level}hPa"),
        }
        if any(arr is None or t_idx >= len(arr) for arr in values.values()):
            continue
        t_val = values["t"][t_idx]
        td_val = values["td"][t_idx]
        u_val = values["u"][t_idx]
        v_val = values["v"][t_idx]
        z_val = values["z"][t_idx]
        if any(v is None or not np.isfinite(v) for v in (t_val, td_val, u_val, v_val, z_val)):
            continue
        if z_val < site_elevation - 10:
            continue

        pressures.append(float(level))
        temps.append(float(t_val))
        dewpts.append(float(td_val))
        u_winds.append(float(u_val))
        v_winds.append(float(v_val))
        heights.append(float(z_val))

    if len(pressures) < 5:
        return None, site_elevation

    sounding = Sounding(
        lat=lat,
        lon=lon,
        valid_time=valid_time,
        slot_offset=0,
        label=f"HRRR {valid_time:%H:%M} UTC",
        pressure=np.array(pressures, dtype=np.float32),
        temperature=np.array(temps, dtype=np.float32),
        dewpoint=np.array(dewpts, dtype=np.float32),
        u_wind=np.array(u_winds, dtype=np.float32),
        v_wind=np.array(v_winds, dtype=np.float32),
        height=np.array(heights, dtype=np.float32),
        provenance={
            "provider": "Open-Meteo historical forecast", "model": _ARCHIVE_MODEL,
            "source_url": _OPEN_METEO_ARCHIVE_URL,
            "model_run_time": None, "forecast_lead_hours": None,
            "time_semantics": "Continuous series stitched from early forecast hours; run and lead are not returned",
            "grid_latitude": data.get("latitude"), "grid_longitude": data.get("longitude"),
        },
    )
    return sounding, site_elevation



def _parse_iem_raob(data: dict, station_id: str, lat: float, lon: float, elev: float) -> list:
    """Parse IEM RAOB JSON response into a list of Sounding objects."""
    import numpy as np

    soundings = []
    profiles = data.get("profiles", [data]) if "profiles" in data else [data]

    for profile in profiles:
        valid_str = profile.get("valid_at") or profile.get("valid")
        valid_time = _parse_iso(valid_str)
        if valid_time is None:
            continue

        levels = profile.get("levels") or profile.get("profile") or []
        if not levels:
            continue

        pressures, heights, temps, dewpts, wdir, wspd = [], [], [], [], [], []
        for lvl in levels:
            try:
                p  = float(lvl.get("pressure", lvl.get("pres", 0)))
                z  = float(lvl.get("height", lvl.get("hght", -9999)))
                tc = float(lvl.get("tmpc", lvl.get("temperature", lvl.get("tmpf", -9999))))
                td = float(lvl.get("dwpc", lvl.get("dewpoint", lvl.get("dwpf", -9999))))
                wd_v = lvl.get("drct") or lvl.get("wind_dir")
                ws_v = lvl.get("sknt") or lvl.get("wind_speed")
                wd = float(wd_v) if wd_v is not None else 0.0
                ws = float(ws_v) if ws_v is not None else 0.0
                if p > 0 and tc > -999:
                    pressures.append(p)
                    heights.append(z if z > -9990 else 0.0)
                    temps.append(tc)
                    dewpts.append(td if td > -999 else float("nan"))
                    wdir.append(wd)
                    wspd.append(ws)
            except (TypeError, ValueError):
                continue

        if len(pressures) < 5:
            continue

        p  = np.array(pressures, dtype=np.float32)
        z  = np.array(heights,   dtype=np.float32)
        tc = np.array(temps,     dtype=np.float32)
        td = np.array(dewpts,    dtype=np.float32)
        wd = np.array(wdir,      dtype=np.float32)
        ws = np.array(wspd,      dtype=np.float32)

        u = -ws * np.sin(np.radians(wd))
        v = -ws * np.cos(np.radians(wd))

        soundings.append(Sounding(
            lat=lat,
            lon=lon,
            valid_time=valid_time,
            slot_offset=0,
            label=valid_time.strftime("%HZ %d %b"),
            pressure=p,
            temperature=tc,
            dewpoint=td,
            u_wind=u,
            v_wind=v,
            height=z,
        ))

    return soundings



def _nearest_synoptic_time(t: datetime) -> datetime:
    """Return the most recent synoptic time (00Z, 06Z, 12Z, 18Z) at or before t."""
    hour = (t.hour // 6) * 6
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def _archive_timestamps_to_query(t: datetime) -> list[str]:
    """Match the live obs-sounding query logic, but against the archive clock."""
    archive_time = t.astimezone(timezone.utc)
    dates = [archive_time.date()]
    if archive_time.hour < 12:
        dates.append((archive_time - timedelta(days=1)).date())
    timestamps: list[str] = []
    for date_obj in dates:
        for hour in (0, 6, 12, 18):
            ts = datetime(
                date_obj.year, date_obj.month, date_obj.day, hour, 0, 0,
                tzinfo=timezone.utc,
            )
            timestamps.append(ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return timestamps


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None
