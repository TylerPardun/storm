from __future__ import annotations

import csv
import io
import json
import logging
import math
import pathlib
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import config
from core.observation import Observation

from data import endpoints
log = logging.getLogger(__name__)

OK_API_URL      = endpoints.nssl_api(endpoints.NSSL_MESONETS["ok"])
OK_META_URL     = endpoints.OK_MESONET_CURRENT
WTM_API_URL     = endpoints.nssl_api(endpoints.NSSL_MESONETS["wtx"])
WTM_SITES_URL   = endpoints.WTM_SITES
KS_API_URL      = endpoints.nssl_api(endpoints.NSSL_MESONETS["ks"])
CO_API_URL      = endpoints.nssl_api(endpoints.NSSL_MESONETS["co"])
CO_META_URL     = endpoints.nssl_api(endpoints.NSSL_CO_MESONET_METADATA)
NE_API_URL      = endpoints.nssl_api(endpoints.NSSL_MESONETS["ne"])
SD_API_URL      = endpoints.nssl_api(endpoints.NSSL_MESONETS["sd"])

# iem endpoints for ASOS
IEM_METAR_GEOJSON = endpoints.IEM_METAR_GEOJSON
IEM_CURRENTS_URL  = endpoints.IEM_CURRENTS
IEM_CURRENTS_BATCH = 100   # stations per IEM request
MAX_ASOS_STATIONS  = 400   # cap to keep map rendering fast

_ASOS_STATIONS_FILE = pathlib.Path(__file__).parents[1] / "asos_stations.json"


def load_asos_station_roster() -> dict[str, dict]:
    """Load the cached ASOS/METAR station roster (station_id -> {lat, lon, name}),
    building it from IEM's metar.geojson and caching to disk on first use.

    A module-level function (not a SurfaceFetcher method) so archive-mode
    fetchers can reuse the exact same roster/cache without depending on a
    live-only QObject -- see archive/fetchers/asos_archive_fetcher.py.
    """
    if _ASOS_STATIONS_FILE.exists():
        try:
            stations = json.loads(_ASOS_STATIONS_FILE.read_text("utf-8"))
            log.info("ASOS: loaded %d stations from %s", len(stations), _ASOS_STATIONS_FILE)
            return stations
        except Exception as exc:
            log.warning("ASOS: failed to read station file, re-fetching: %s", exc)

    log.info("ASOS: fetching station metadata from IEM metar.geojson …")
    req = Request(IEM_METAR_GEOJSON, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
    with urlopen(req, timeout=30, context=config.NSSL_SSL_CONTEXT) as resp:
        raw = resp.read()
    fc = json.loads(raw.decode("utf-8", errors="replace"))

    stations: dict[str, dict] = {}
    for feat in fc.get("features", []):
        props = feat.get("properties", {})
        stid  = (props.get("station") or props.get("stid") or "").strip().upper()
        if not stid:
            continue
        coords = (feat.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        name = (props.get("name") or stid).strip()
        stations[stid] = {"lat": lat, "lon": lon, "name": name}

    if not stations:
        raise RuntimeError("IEM metar.geojson returned no station features")

    try:
        _ASOS_STATIONS_FILE.write_text(json.dumps(stations, separators=(",", ":")), "utf-8")
        log.info("ASOS: saved %d stations to %s", len(stations), _ASOS_STATIONS_FILE)
    except Exception as exc:
        log.warning("ASOS: could not save station file: %s", exc)

    return stations


class SurfaceFetchError(RuntimeError):
    """Recoverable provider error with context useful for field debugging."""


class SurfaceFetcher(QObject):
    observations_updated = pyqtSignal(object)
    status_updated = pyqtSignal(str)
    diagnostics_updated = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(300_000)
        self._timer.timeout.connect(self.fetch_now)
        self._state_lock = threading.Lock()
        self._running = False
        self._pending_refresh = False
        self._ok_enabled  = False
        self._wtm_enabled = False
        self._ks_enabled  = False
        self._co_enabled  = False
        self._ne_enabled  = False
        self._sd_enabled  = False
        self._asos_enabled = False
        self._wtm_meta: dict[str, dict] | None = None
        self._co_meta:  dict[str, dict] | None = None
        self._asos_stations: dict[str, dict] | None = None   # stid → {lat, lon, name}
        self._asos_bbox: tuple[float, float, float, float] | None = None
        self._last_good: dict[str, tuple[list[dict], datetime]] = {}
        self._source_diag: dict[str, dict[str, Any]] = {}
        self._headers = {
            "User-Agent": "Mozilla/5.0 STORM/1.0",
            "Accept": "application/json, text/html, application/xml, text/xml",
        }

    def start(self):
        self._update_timer()

    def stop(self):
        self._timer.stop()

    def set_ok_enabled(self, enabled: bool):
        self._ok_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_wtm_enabled(self, enabled: bool):
        self._wtm_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_ks_enabled(self, enabled: bool):
        self._ks_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_co_enabled(self, enabled: bool):
        self._co_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_ne_enabled(self, enabled: bool):
        self._ne_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def set_sd_enabled(self, enabled: bool):
        self._sd_enabled = enabled
        self._update_timer()
        self.fetch_now()

    def _update_timer(self):
        if (
            self._ok_enabled
            or self._wtm_enabled
            or self._ks_enabled
            or self._co_enabled
            or self._ne_enabled
            or self._sd_enabled
            or (self._asos_enabled and self._asos_bbox is not None)
        ):
            self._timer.start()
        else:
            self._timer.stop()

    def fetch_now(self):
        with self._state_lock:
            if self._running:
                self._pending_refresh = True
                return
            self._running = True
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            payload: list[dict] = []
            parts: list[str] = []

            if self._ok_enabled:
                ok_attempt = datetime.now(timezone.utc)
                ok_obs, ok_stale, ok_note = self._fetch_source("OK", "ok", self._fetch_ok_mesonet)
                payload.extend(ok_obs)
                ok_time = max((i["obs"].timestamp for i in ok_obs), default=None)
                stamp = ok_time.strftime("%H:%MZ") if ok_time else "?"
                self._update_source_diag("ok", "OK", ok_attempt, ok_obs, ok_stale, ok_note)
                parts.append(self._format_status_part("OK", len(ok_obs), stamp, ok_stale, ok_note))

            if self._wtm_enabled:
                wtm_attempt = datetime.now(timezone.utc)
                wtm_obs, wtm_stale, wtm_note = self._fetch_source("WTM", "wtm", self._fetch_wtm)
                payload.extend(wtm_obs)
                wtm_time = max((i["obs"].timestamp for i in wtm_obs), default=None)
                stamp = wtm_time.strftime("%H:%MZ") if wtm_time else "?"
                self._update_source_diag("wtm", "WTM", wtm_attempt, wtm_obs, wtm_stale, wtm_note)
                parts.append(self._format_status_part("WTM", len(wtm_obs), stamp, wtm_stale, wtm_note))

            if self._ks_enabled:
                ks_attempt = datetime.now(timezone.utc)
                ks_obs, ks_stale, ks_note = self._fetch_source("KS", "ks", self._fetch_ks_mesonet)
                payload.extend(ks_obs)
                ks_time = max((i["obs"].timestamp for i in ks_obs), default=None)
                stamp = ks_time.strftime("%H:%MZ") if ks_time else "?"
                self._update_source_diag("ks", "KS", ks_attempt, ks_obs, ks_stale, ks_note)
                parts.append(self._format_status_part("KS", len(ks_obs), stamp, ks_stale, ks_note))

            if self._co_enabled:
                co_attempt = datetime.now(timezone.utc)
                co_obs, co_stale, co_note = self._fetch_source("CO", "co", self._fetch_co_mesonet)
                payload.extend(co_obs)
                co_time = max((i["obs"].timestamp for i in co_obs), default=None)
                stamp = co_time.strftime("%H:%MZ") if co_time else "?"
                self._update_source_diag("co", "CO", co_attempt, co_obs, co_stale, co_note)
                parts.append(self._format_status_part("CO", len(co_obs), stamp, co_stale, co_note))

            if self._ne_enabled:
                ne_attempt = datetime.now(timezone.utc)
                ne_obs, ne_stale, ne_note = self._fetch_source("NE", "ne", self._fetch_ne_mesonet)
                payload.extend(ne_obs)
                ne_time = max((i["obs"].timestamp for i in ne_obs), default=None)
                stamp = ne_time.strftime("%H:%MZ") if ne_time else "?"
                self._update_source_diag("ne", "NE", ne_attempt, ne_obs, ne_stale, ne_note)
                parts.append(self._format_status_part("NE", len(ne_obs), stamp, ne_stale, ne_note))

            if self._sd_enabled:
                sd_attempt = datetime.now(timezone.utc)
                sd_obs, sd_stale, sd_note = self._fetch_source("SD", "sd", self._fetch_sd_mesonet)
                payload.extend(sd_obs)
                sd_time = max((i["obs"].timestamp for i in sd_obs), default=None)
                stamp = sd_time.strftime("%H:%MZ") if sd_time else "?"
                self._update_source_diag("sd", "SD", sd_attempt, sd_obs, sd_stale, sd_note)
                parts.append(self._format_status_part("SD", len(sd_obs), stamp, sd_stale, sd_note))

            if self._asos_enabled and self._asos_bbox is not None:
                asos_attempt = datetime.now(timezone.utc)
                asos_obs, asos_stale, asos_note = self._fetch_source("ASOS", "asos", self._fetch_asos)
                payload.extend(asos_obs)
                asos_time = max((i["obs"].timestamp for i in asos_obs), default=None)
                stamp = asos_time.strftime("%H:%MZ") if asos_time else "?"
                self._update_source_diag("asos", "ASOS", asos_attempt, asos_obs, asos_stale, asos_note)
                parts.append(self._format_status_part("ASOS", len(asos_obs), stamp, asos_stale, asos_note))

            payload = [item for item in payload if self._source_enabled(item.get("source", ""))]

            if not parts:
                self.status_updated.emit("Surface obs idle")
                self.observations_updated.emit([])
            else:
                self.status_updated.emit("  |  ".join(parts))
                self.observations_updated.emit(payload)
            self.diagnostics_updated.emit(self._diagnostics_snapshot())
        except Exception as exc:
            log.error("surface fetch failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
        finally:
            rerun = False
            with self._state_lock:
                if self._pending_refresh:
                    self._pending_refresh = False
                    rerun = True
                else:
                    self._running = False
            if rerun:
                threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_ok_mesonet(self) -> list[dict]:
        # Refetch the CSV every cycle — it provides lat/lon/name AND current MSLP pressure
        meta, mslp_map = self._fetch_ok_metadata()

        raw  = self._http_get(OK_API_URL, headers=self._nssl_api_headers())
        data = self._json_from_bytes(raw, "OK Mesonet data", OK_API_URL)
        obs_time = self._parse_iso_utc(data.get("time"))
        obs_data = data.get("data", {})

        all_stids: set[str] = set()
        for var_vals in obs_data.values():
            all_stids.update(var_vals.keys())

        observations: list[dict] = []
        for stid in all_stids:
            meta_row = meta.get(stid)
            if not meta_row:
                continue
            obs = Observation(
                vehicle_id=f"surface:ok:{stid}",
                lat=meta_row["lat"],
                lon=meta_row["lon"],
                timestamp=obs_time,
                icon_type="mesonet",
                temperature_c=self._float_or_none(obs_data.get("tair", {}).get(stid)),
                dewpoint_c=self._float_or_none(obs_data.get("tdew", {}).get(stid)),
                wind_speed_ms=self._float_or_none(obs_data.get("wspd", {}).get(stid)),
                wind_dir_deg=self._float_or_none(obs_data.get("wdir", {}).get(stid)),
                pressure_mb=mslp_map.get(stid),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ok",
                "name": meta_row.get("name", stid.upper()),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("OK Mesonet data returned no station rows")

        return observations

    def _fetch_ok_metadata(self) -> tuple[dict[str, dict], dict[str, float]]:
        """Fetch OK Mesonet station metadata and current MSLP from the public CSV."""
        raw    = self._http_get(OK_META_URL).decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)
        meta: dict[str, dict] = {}
        mslp: dict[str, float] = {}
        for row in reader:
            stid = (row.get("STID") or "").strip().lower()
            lat  = self._float_or_none(row.get("LAT"))
            lon  = self._float_or_none(row.get("LON"))
            if not stid or lat is None or lon is None:
                continue
            meta[stid] = {
                "lat": lat,
                "lon": lon,
                "name": (row.get("NAME") or stid.upper()).strip(),
            }
            pres = self._valid_float(row.get("PRES"))
            if pres is not None and 850.0 <= pres <= 1100.0:
                mslp[stid] = pres
        return meta, mslp

    def _fetch_wtm(self) -> list[dict]:
        if self._wtm_meta is None:
            self._wtm_meta = self._fetch_wtm_metadata()

        raw  = self._http_get(WTM_API_URL, headers=self._nssl_api_headers())
        data = self._json_from_bytes(raw, "WTM data", WTM_API_URL)
        observations: list[dict] = []

        for row in data.get("results", []):
            mid = (row.get("mid") or "").strip().lower()
            if not mid:
                continue
            meta = (self._wtm_meta or {}).get(mid)
            if not meta:
                continue
            temp_c = self._float_or_none(row.get("temp1p5m"))
            station_pres = self._float_or_none(row.get("pres"))
            obs = Observation(
                vehicle_id=f"surface:wtm:{mid}",
                lat=meta["lat"],
                lon=meta["lon"],
                timestamp=self._parse_iso_utc(row.get("utc")),
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=self._float_or_none(row.get("dp1p5m")),
                wind_speed_ms=self._float_or_none(row.get("wspd10m")),
                wind_dir_deg=self._float_or_none(row.get("wdir10m")),
                pressure_mb=self._station_to_mslp(station_pres, meta.get("elevation_m"), temp_c),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "wtm",
                "name": meta.get("name", row.get("name", mid.upper())),
                "obs": obs,
            })

        return observations

    def _fetch_wtm_metadata(self) -> dict[str, dict]:
        raw  = self._http_get(WTM_SITES_URL)
        data = self._json_from_bytes(raw, "WTM station metadata", WTM_SITES_URL)
        result = {}
        for row in data.get("results", []):
            if not row.get("mesonet_id") or not row.get("latitude") or not row.get("longitude"):
                continue
            elev_ft = self._float_or_none(row.get("elevation"))
            result[row["mesonet_id"].lower()] = {
                "lat": float(row["latitude"]),
                "lon": float(row["longitude"]),
                "elevation_m": elev_ft / 3.28084 if elev_ft is not None else None,
                "name": row.get("name", row["mesonet_id"]),
            }
        return result

    def _fetch_ks_mesonet(self) -> list[dict]:
        raw  = self._http_get(KS_API_URL, headers=self._nssl_api_headers())
        data = self._json_from_bytes(raw, "KS Mesonet data", KS_API_URL)
        observations: list[dict] = []

        for row in data.get("results", []):
            station = (row.get("station") or "").strip()
            if not station:
                continue
            meta = row.get("meta") or {}
            lat = self._float_or_none(meta.get("lat"))
            lon = self._float_or_none(meta.get("lon"))
            if lat is None or lon is None:
                continue

            temp_c = self._float_or_none(row.get("TEMP2MAVG"))
            rh_pct = self._float_or_none(row.get("RELHUM2MAVG"))
            slp_kpa = self._float_or_none(row.get("SLPAVG"))

            obs = Observation(
                vehicle_id=f"surface:ks:{self._station_key(station)}",
                lat=lat,
                lon=lon,
                timestamp=self._parse_iso_utc(row.get("timestamp")),
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=self._dewpoint_c_from_rh(temp_c, rh_pct),
                wind_speed_ms=self._float_or_none(row.get("WSPD10MAVG")),
                wind_dir_deg=self._float_or_none(row.get("WDIR10M")),
                pressure_mb=slp_kpa * 10.0 if slp_kpa is not None else None,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ks",
                "name": meta.get("name") or station,
                "obs": obs,
            })

        return observations

    def _fetch_co_mesonet(self) -> list[dict]:
        if self._co_meta is None:
            self._co_meta = self._fetch_co_metadata()

        raw = self._http_get(CO_API_URL, headers=self._nssl_api_headers())
        data = self._json_from_bytes(raw, "CO Mesonet data", CO_API_URL)
        observations: list[dict] = []

        for stid in data.get("stations", []):
            station = str(stid).strip().lower()
            if not station:
                continue
            row = data.get(station)
            meta = (self._co_meta or {}).get(station)
            if not isinstance(row, dict) or not meta:
                continue

            temp_f = self._valid_float(row.get("t"))
            dew_f = self._valid_float(row.get("dewpt"))
            wind_mph = self._valid_float(row.get("windSpeed"))

            obs = Observation(
                vehicle_id=f"surface:co:{station}",
                lat=meta["lat"],
                lon=meta["lon"],
                timestamp=self._parse_co_time(row.get("time")),
                icon_type="mesonet",
                temperature_c=self._f_to_c(temp_f),
                dewpoint_c=self._f_to_c(dew_f),
                wind_speed_ms=wind_mph * 0.44704 if wind_mph is not None else None,
                wind_dir_deg=self._valid_float(row.get("windDir")),
                pressure_mb=None,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "co",
                "name": meta.get("name", station.upper()),
                "obs": obs,
            })

        return observations

    def _fetch_co_metadata(self) -> dict[str, dict]:
        raw = self._http_get(CO_META_URL, headers=self._nssl_api_headers())
        data = self._json_from_bytes(raw, "CO station metadata", CO_META_URL)
        meta: dict[str, dict] = {}
        for key, row in data.items():
            if not isinstance(row, dict):
                continue
            station = (row.get("station") or key or "").strip().lower()
            lat = self._float_or_none(row.get("lat"))
            lon = self._float_or_none(row.get("lon"))
            if not station or lat is None or lon is None:
                continue
            meta[station] = {
                "lat": lat,
                "lon": lon,
                "name": (row.get("name") or station.upper()).strip(),
            }
        return meta

    def _fetch_ne_mesonet(self) -> list[dict]:
        raw = self._http_get(NE_API_URL, headers=self._nssl_api_headers())
        payload = self._json_from_bytes(raw, "NE Mesonet data", NE_API_URL)
        observations: list[dict] = []

        for row in payload.get("data", []):
            station_id = str(row.get("id") or "").strip()
            lat = self._float_or_none(row.get("latitude"))
            lon = self._float_or_none(row.get("longitude"))
            if not station_id or lat is None or lon is None:
                continue

            data = row.get("data") or {}
            temperature = data.get("temperature") or {}
            dewpoint = data.get("dewPoint") or {}
            pressure = data.get("pressure") or {}
            wind = data.get("wind") or {}
            wind_level = wind.get("tenMeter") or {}
            wind_speed = wind_level.get("speed") or {}
            wind_direction = wind_level.get("direction") or {}
            if self._float_or_none(wind_speed.get("avg")) is None:
                wind_level = wind.get("threeMeter") or {}
                wind_speed = wind_level.get("speed") or {}
                wind_direction = wind_level.get("direction") or {}

            obs = Observation(
                vehicle_id=f"surface:ne:{station_id}",
                lat=lat,
                lon=lon,
                timestamp=self._parse_iso_utc(row.get("timestamp")),
                icon_type="mesonet",
                temperature_c=self._float_or_none(temperature.get("twoMeter")),
                dewpoint_c=self._float_or_none(dewpoint.get("twoMeter")),
                wind_speed_ms=self._float_or_none(wind_speed.get("avg")),
                wind_dir_deg=self._float_or_none(wind_direction.get("avg")),
                pressure_mb=self._float_or_none(pressure.get("seaLevel")),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "ne",
                "name": str(row.get("name") or f"NE Mesonet {station_id}").strip(),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("NE Mesonet data returned no station rows")

        return observations

    def _fetch_sd_mesonet(self) -> list[dict]:
        raw = self._http_get(SD_API_URL, headers=self._nssl_api_headers())
        payload = self._json_from_bytes(raw, "SD Mesonet data", SD_API_URL)
        observations: list[dict] = []

        for row in payload:
            stid = str(row.get("nwsid") or "").strip()
            lat  = self._float_or_none(row.get("lat"))
            lon  = self._float_or_none(row.get("lon"))
            if not stid or lat is None or lon is None:
                continue

            temp_c      = self._valid_float(row.get("TA [C]"))
            station_pres = self._valid_float(row.get("PA [mbar]"))
            elevation_m = self._float_or_none(row.get("elev [m]"))

            obs = Observation(
                vehicle_id=f"surface:sd:{stid.lower()}",
                lat=lat,
                lon=lon,
                timestamp=self._parse_iso_utc(row.get("date_time [ISO8601]")),
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=self._valid_float(row.get("TD [C]")),
                wind_speed_ms=self._valid_float(row.get("US [m/s]")),
                wind_dir_deg=self._valid_float(row.get("UD [deg]")),
                pressure_mb=self._station_to_mslp(station_pres, elevation_m, temp_c),
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "sd",
                "name": str(row.get("name") or stid).strip(),
                "obs": obs,
            })

        if not observations:
            raise RuntimeError("SD Mesonet data returned no station rows")

        return observations

    def set_asos_enabled(self, enabled: bool):
        self._asos_enabled = enabled
        if enabled:
            if self._asos_bbox is not None:
                # reuse the last drawn bbox immediately
                self._update_timer()
                self.fetch_now()
        else:
            # disable fetching but KEEP the bbox so re-enabling reuses it
            self._update_timer()
            self.fetch_now()

    def fetch_asos_bbox(self, west: float, south: float, east: float, north: float):
        """Store the bbox and kick off the first fetch. The timer handles subsequent refreshes."""
        self._asos_bbox = (west, south, east, north)
        self._update_timer()
        self.fetch_now()

    def clear_asos_bbox(self):
        """Clear the saved ASOS bbox so the next ASOS action asks for a new domain."""
        self._asos_bbox = None
        self._update_timer()

    def _ensure_asos_stations(self) -> dict[str, dict]:
        """Return in-memory station dict, building/loading it if needed."""
        if self._asos_stations is None:
            self._asos_stations = load_asos_station_roster()
        return self._asos_stations

    def _fetch_asos(self) -> list[dict]:
        """Fetch ASOS METARs via IEM currents API using a local station metadata file."""
        west, south, east, north = self._asos_bbox

        stations = self._ensure_asos_stations()

        # filter by bbox
        in_bbox = [
            stid for stid, meta in stations.items()
            if south <= meta["lat"] <= north and west <= meta["lon"] <= east
        ]

        if not in_bbox:
            log.info("ASOS: no stations found in bbox %s", self._asos_bbox)
            return []

        if len(in_bbox) > MAX_ASOS_STATIONS:
            log.warning(
                "ASOS: bbox contains %d stations; capping at %d — draw a smaller box",
                len(in_bbox), MAX_ASOS_STATIONS,
            )
            in_bbox = in_bbox[:MAX_ASOS_STATIONS]

        # batch-fetch from IEM (100 stations per request)
        observations: list[dict] = []
        for i in range(0, len(in_bbox), IEM_CURRENTS_BATCH):
            batch = in_bbox[i : i + IEM_CURRENTS_BATCH]
            observations.extend(self._fetch_iem_batch(batch, stations))

        log.info("ASOS: fetched %d obs for %d in-bbox stations", len(observations), len(in_bbox))
        return observations

    def _fetch_iem_batch(self, stids: list[str], stations: dict[str, dict]) -> list[dict]:
        """Fetch current obs from IEM for a batch of station IDs."""
        params = "&".join(f"station={s}" for s in stids)
        url    = f"{IEM_CURRENTS_URL}?{params}"

        raw  = self._http_get(url)
        data = self._json_from_bytes(raw, "ASOS current observations", url)

        observations: list[dict] = []
        for rec in data.get("data", []):
            stid = (rec.get("station") or "").strip().upper()
            if not stid:
                continue

            meta = stations.get(stid, {})
            lat  = self._float_or_none(rec.get("lat"))  or meta.get("lat")
            lon  = self._float_or_none(rec.get("lon"))  or meta.get("lon")
            if lat is None or lon is None:
                continue

            ts = self._parse_iso_utc(rec.get("utc_valid") or rec.get("valid"))

            # iem returns temperature/dewpoint in °F
            tmpf = self._float_or_none(rec.get("tmpf"))
            dwpf = self._float_or_none(rec.get("dwpf"))
            temp_c = (tmpf - 32.0) * 5.0 / 9.0 if tmpf is not None else None
            dew_c  = (dwpf - 32.0) * 5.0 / 9.0 if dwpf is not None else None

            # wind speed in knots → m/s
            sknt   = self._float_or_none(rec.get("sknt"))
            wspd_ms = sknt * 0.514444 if sknt is not None else None

            wdir = self._float_or_none(rec.get("drct"))

            # prefer mslp (already mb); fall back to altimeter (in-Hg)
            mslp = self._float_or_none(rec.get("mslp"))
            pres = mslp if (mslp is not None and 870.0 <= mslp <= 1090.0) else None
            if pres is None:
                alti = self._float_or_none(rec.get("alti"))
                if alti is not None and 27.0 <= alti <= 32.0:
                    pres = alti * 33.8639

            name = meta.get("name") or rec.get("name") or stid

            obs = Observation(
                vehicle_id=f"surface:asos:{stid}",
                lat=lat,
                lon=lon,
                timestamp=ts,
                icon_type="mesonet",
                temperature_c=temp_c,
                dewpoint_c=dew_c,
                wind_speed_ms=wspd_ms,
                wind_dir_deg=wdir,
                pressure_mb=pres,
            )
            observations.append({
                "id": obs.vehicle_id,
                "source": "asos",
                "name": name,
                "obs": obs,
            })

        return observations


    def _http_get(self, url: str, ssl_ctx=None, headers: dict[str, str] | None = None) -> bytes:
        req_headers = dict(self._headers)
        if headers:
            req_headers.update(headers)
        req = Request(url, headers=req_headers)
        try:
            with urlopen(req, timeout=30, context=ssl_ctx or config.NSSL_SSL_CONTEXT) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                body = resp.read()
                if status and status >= 400:
                    raise SurfaceFetchError(f"HTTP {status} from {url}")
                if not body:
                    raise SurfaceFetchError(f"empty response from {url}")
                return body
        except HTTPError as exc:
            preview = self._preview_bytes(exc.read())
            raise SurfaceFetchError(f"HTTP {exc.code} from {url}: {preview}") from exc
        except URLError as exc:
            raise SurfaceFetchError(f"network error from {url}: {exc.reason}") from exc

    def _fetch_source(self, label: str, source_key: str, fetch_fn) -> tuple[list[dict], bool, str | None]:
        try:
            observations = fetch_fn()
            self._last_good[source_key] = (list(observations), datetime.now(timezone.utc))
            return observations, False, None
        except Exception as exc:
            log.error("%s surface fetch failed: %s", label, exc, exc_info=True)
            self.error.emit(f"{label}: {exc}")
            cached = self._last_good.get(source_key)
            if cached:
                observations, cached_at = cached
                age_min = max(0, int((datetime.now(timezone.utc) - cached_at).total_seconds() // 60))
                log.warning("%s: using last good surface obs from %d min ago", label, age_min)
                return list(observations), True, f"{age_min}m"
            return [], False, "error"

    @staticmethod
    def _format_status_part(label: str, count: int, stamp: str, stale: bool, note: str | None) -> str:
        if stale:
            return f"{label} {count} {stamp} STALE {note or ''}".rstrip()
        if note == "error":
            return f"{label} error"
        return f"{label} {count} {stamp}"

    def _update_source_diag(
        self,
        source_key: str,
        label: str,
        attempted_at: datetime,
        observations: list[dict],
        stale: bool,
        note: str | None,
    ) -> None:
        valid_time = max((item["obs"].timestamp for item in observations), default=None)
        diag = self._source_diag.get(source_key, {"label": label})
        diag.update({
            "label": label,
            "last_attempt": attempted_at,
            "valid_time": valid_time,
            "count": len(observations),
            "stale": stale,
            "note": note,
        })
        if observations and not stale:
            diag["last_success"] = attempted_at
        self._source_diag[source_key] = diag

    def _diagnostics_snapshot(self) -> dict[str, dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for key, enabled in (
            ("ok", self._ok_enabled),
            ("wtm", self._wtm_enabled),
            ("ks", self._ks_enabled),
            ("co", self._co_enabled),
            ("ne", self._ne_enabled),
            ("sd", self._sd_enabled),
            ("asos", self._asos_enabled and self._asos_bbox is not None),
        ):
            if enabled:
                active[key] = dict(self._source_diag.get(key, {}))
        return active

    @staticmethod
    def _nssl_api_headers() -> dict[str, str]:
        return {"X-API-Key": config.NSSL_API_KEY} if config.NSSL_API_KEY else {}

    def _json_from_bytes(self, raw: bytes, label: str, url: str) -> Any:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            raise SurfaceFetchError(f"{label} returned an empty body from {url}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            preview = self._preview_text(text)
            raise SurfaceFetchError(
                f"{label} returned invalid JSON at char {exc.pos} from {url}: {preview}"
            ) from exc

    @staticmethod
    def _preview_bytes(raw: bytes, limit: int = 180) -> str:
        return SurfaceFetcher._preview_text(raw.decode("utf-8", errors="replace"), limit=limit)

    @staticmethod
    def _preview_text(text: str, limit: int = 180) -> str:
        compact = " ".join(str(text).split())
        if len(compact) > limit:
            compact = compact[:limit] + "..."
        return compact or "<empty>"

    def _source_enabled(self, source: str) -> bool:
        return (
            (source == "ok"  and self._ok_enabled)
            or (source == "wtm" and self._wtm_enabled)
            or (source == "ks" and self._ks_enabled)
            or (source == "co" and self._co_enabled)
            or (source == "ne" and self._ne_enabled)
            or (source == "sd" and self._sd_enabled)
            or (source == "asos" and self._asos_enabled)
        )

    @staticmethod
    def _float_or_none(value: str | None) -> float | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value or value in {"--", "M"}:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_iso_utc(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    @staticmethod
    def _parse_co_time(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        text = str(value).strip()
        if "T" in text and not text.endswith("Z") and "+" not in text[10:] and "-" not in text[10:]:
            text += "+00:00"
        return SurfaceFetcher._parse_iso_utc(text)

    @staticmethod
    def _valid_float(value: str | float | int | None) -> float | None:
        parsed = SurfaceFetcher._float_or_none(value)
        if parsed is None or parsed <= -900.0:
            return None
        return parsed

    @staticmethod
    def _f_to_c(value: float | None) -> float | None:
        return (value - 32.0) * 5.0 / 9.0 if value is not None else None

    @staticmethod
    def _dewpoint_c_from_rh(temp_c: float | None, rh_pct: float | None) -> float | None:
        if temp_c is None or rh_pct is None or rh_pct <= 0.0 or rh_pct > 100.0:
            return None
        a = 17.625
        b = 243.04
        gamma = math.log(rh_pct / 100.0) + (a * temp_c) / (b + temp_c)
        return (b * gamma) / (a - gamma)

    @staticmethod
    def _station_to_mslp(pressure_mb: float | None, elevation_m: float | None, temp_c: float | None) -> float | None:
        """Reduce station pressure to MSLP using the standard hypsometric formula."""
        if pressure_mb is None or elevation_m is None or elevation_m <= 0.0:
            return pressure_mb
        t_avg_k = (temp_c if temp_c is not None else 15.0) + 273.15 + 0.5 * 0.0065 * elevation_m
        return pressure_mb * math.exp(elevation_m / (29.271 * t_avg_k))

    @staticmethod
    def _station_key(station: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in station).strip("_")
