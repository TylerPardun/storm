
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from PyQt6.QtCore import QObject, pyqtSignal

import config
from core.annotation import Annotation
from core.storm_cone import StormCone
from core.drawing import DrawingAnnotation
from core.scan_sector import ScanSector
from network.vehicle_sync import _observation_from_payload
from archive.vehicle_speed import VehicleSpeed, calculate_vehicle_speed

log = logging.getLogger(__name__)

_ANNOTATIONS_PATH = "annotations"
_TOPICS = ("vehicles", "annotations", "cones", "drawings", "scan_sectors")

# THREDDS mirror of the same storm.<topic>.<date> files as the API below --
# public, unauthenticated, and confirmed live 2026-09-08 to be the exact
# same backing data (its data/sonde/ start date matches what
# clamps_sonde_archive_fetcher.py independently found there) and format
# (a real scan_sectors record's fields match ScanSector.__init__ exactly,
# and one record's time+position matched the FOFS dltruck GPS track at
# the same instant to 5 decimal places). Tried first: at the time this was
# added, config.NSSL_API_ROOT was returning a 404 for every path tested,
# including endpoints unrelated to archive mode, suggesting that host/app
# was down entirely rather than these specific dates being unavailable.
# THREDDS's retention here is a rolling ~5 months, not permanent, so the
# API is still tried as a fallback -- for older dates, and in case it
# comes back for dates THREDDS doesn't have.
_THREDDS_ANNOTATIONS_ROOT = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/annotations"


def _fetch_text(url: str, *, api_key: bool = False) -> Optional[str]:
    """Fetch an archive JSONL file as text; return None on 404, raise on
    other errors. api_key=True adds the NSSL API's auth header; THREDDS
    doesn't need or want it."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
        if api_key and config.NSSL_API_KEY:
            headers["X-API-Key"] = config.NSSL_API_KEY
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20, context=config.NSSL_SSL_CONTEXT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _fetch_topic_text(topic: str, date_str: str) -> "tuple[str | None, str]":
    """THREDDS first, API second. Returns (text_or_None, source_used) --
    source is reported purely for logging, callers don't need to branch
    on it. THREDDS failures (a genuinely missing date, or any network
    error) fall through to the API rather than only falling back on a
    clean 404, since a THREDDS hiccup shouldn't sink the whole lookup
    when the API might still answer."""
    thredds_url = f"{_THREDDS_ANNOTATIONS_ROOT}/storm.{topic}.{date_str}"
    thredds_error = None
    try:
        text = _fetch_text(thredds_url)
        if text is not None:
            return text, "THREDDS"
    except Exception as exc:
        thredds_error = exc
        log.debug("ArchiveMQTTReader: THREDDS fetch failed for %s %s, trying API: %s", topic, date_str, exc)

    api_url = f"{config.NSSL_API_ROOT}/{_ANNOTATIONS_PATH}/storm.{topic}.{date_str}"
    text = _fetch_text(api_url, api_key=True)
    if text is None and thredds_error is not None:
        raise RuntimeError(f"THREDDS availability unknown for {topic} {date_str}: {thredds_error}; API returned 404") from thredds_error
    return text, "API"


def _parse_timestamp(obj: dict) -> Optional[datetime]:
    """Extract a UTC datetime from a JSONL record."""
    # scan sectors
    scan_ts = obj.get("timestamp")
    if scan_ts:
        try:
            return datetime.fromisoformat(
                scan_ts.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass

    # annotations / cones / drawings
    ts_str = obj.get("created_at") or obj.get("deleted_at")
    if ts_str:
        try:
            return datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    # vehicles
    gps_date = obj.get("gps_date")
    gps_time = obj.get("gps_time")
    if gps_date and gps_time:
        try:
            return datetime.strptime(
                str(gps_date) + str(gps_time), "%d%m%y%H%M%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_jsonl(text: str) -> list[tuple[datetime, dict]]:
    """Parse JSONL into a sorted list of (timestamp, obj) tuples, skipping unparseable lines."""
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            ts = _parse_timestamp(obj)
            if ts is not None:
                results.append((ts, obj))
        except Exception:
            continue
    results.sort(key=lambda x: x[0])
    return results


class ArchiveMQTTReader(QObject):
    """
    Fetches STORM archive JSONL files from the NSSL API and replays
    vehicle positions, annotations, cones, drawings, and scan sectors in sync with
    the archive TimeController.

    Signals
    -------
    vehicle_position(str, float, float, float, float)
        vehicle_id, lat, lon, speed_ms, heading — emitted for each vehicle
        at the current archive time.
    vehicles_cleared()
        Emitted when the archive time jumps backward.
    annotation_received(Annotation)
    annotation_deleted(str, str)       annotation_id, deleted_at
    cone_received(StormCone)
    cone_deleted(str)                  cone_id
    drawing_received(DrawingAnnotation)
    drawing_deleted(str)               drawing_id
    scan_sector_received(ScanSector)
    scan_sectors_cleared()
    error(str)
    """

    vehicle_position    = pyqtSignal(object)  # Observation
    vehicles_cleared    = pyqtSignal()
    annotation_received = pyqtSignal(object)
    annotation_deleted  = pyqtSignal(str, str)
    cone_received       = pyqtSignal(object)
    cone_deleted        = pyqtSignal(str)
    drawing_received    = pyqtSignal(object)
    drawing_deleted     = pyqtSignal(str)
    scan_sector_received = pyqtSignal(object)
    scan_sectors_cleared = pyqtSignal()
    error               = pyqtSignal(str)
    _load_complete      = pyqtSignal()   # internal — fires on bg thread, triggers main-thread replay

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date_str = session_date.strftime("%Y%m%d")
        self._data: dict[str, list[tuple[datetime, dict]]] = {t: [] for t in _TOPICS}
        self._loaded = False
        self._vehicle_observations: dict[str, list] = {}
        self._pending_time: Optional[datetime] = None
        self._last_emit_time: Optional[datetime] = None
        # track which annotation/cone/drawing ids have been emitted so we can
        self._emitted_ids: dict[str, set[str]] = {
            "annotations": set(), "cones": set(), "drawings": set()
        }
        # _load_complete is queued automatically by Qt (bg thread → main thread)
        self._load_complete.connect(self._on_load_complete)


    def load(self) -> None:
        """Fetch all archive files for the session date (background thread)."""
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        if not self._loaded:
            self._pending_time = archive_time
            return

        # backward jump — remove anything placed after the new time, then re-emit from scratch.
        if self._last_emit_time is not None and archive_time < self._last_emit_time:
            self.vehicles_cleared.emit()
            self.scan_sectors_cleared.emit()
            # explicitly delete annotations/cones/drawings that are now in the future.
            for ann_id in list(self._emitted_ids["annotations"]):
                self.annotation_deleted.emit(ann_id, "")
            for cone_id in list(self._emitted_ids["cones"]):
                self.cone_deleted.emit(cone_id)
            for drawing_id in list(self._emitted_ids["drawings"]):
                self.drawing_deleted.emit(drawing_id)
            self._emitted_ids = {"annotations": set(), "cones": set(), "drawings": set()}

        self._last_emit_time = archive_time
        self._emit_vehicles(archive_time)
        self._emit_annotations(archive_time)
        self._emit_cones(archive_time)
        self._emit_drawings(archive_time)
        self._emit_scan_sectors(archive_time)

    def first_vehicle_positions(self) -> list[tuple[str, float, float]]:
        """Return (vehicle_id, lat, lon) tuples from the first vehicle timestamp."""
        msgs = self._data.get("vehicles", [])
        if not msgs:
            return []
        first_time = msgs[0][0]
        result = []
        for ts, obj in msgs:
            if ts > first_time:
                break
            try:
                obs = _observation_from_payload(obj)
                result.append((obs.vehicle_id, obs.lat, obs.lon))
            except Exception:
                continue
        return result

    def vehicle_metadata(self) -> dict[str, str | None]:
        """Return vehicle IDs and their first advertised icon type."""
        vehicles: dict[str, str | None] = {}
        for _, obj in self._data.get("vehicles", []):
            vehicle_id = obj.get("vehicle_id")
            if not vehicle_id:
                continue
            icon_type = (obj.get("icon_type") or "").strip() or None
            if vehicle_id not in vehicles or vehicles[vehicle_id] is None:
                vehicles[vehicle_id] = icon_type
        return vehicles

    def vehicle_speed(self, vehicle_id: str, archive_time: datetime) -> VehicleSpeed:
        """Return MQTT-cadence ground speeds for a vehicle."""
        return calculate_vehicle_speed(
            self._vehicle_observations.get(vehicle_id, []),
            archive_time,
            short_seconds=10,
            average_seconds=30,
        )

    def _on_load_complete(self) -> None:
        """Called on the main thread once background fetch finishes."""
        histories: dict[str, list] = {}
        for _, obj in self._data.get("vehicles", []):
            try:
                observation = _observation_from_payload(obj)
            except Exception:
                continue
            histories.setdefault(observation.vehicle_id, []).append(observation)
        self._vehicle_observations = histories
        self._loaded = True
        if self._pending_time is not None:
            self.on_time_changed(self._pending_time)
            self._pending_time = None


    def _fetch_all(self) -> None:
        # Each topic is an independent request, and a THREDDS-miss + API-fallback
        # round trip can take ~10s+ under NSSL's WAF (observed on both hosts, not
        # just the API) -- fetched sequentially that's 5x worst case (~60s for a
        # date with no data anywhere). Fetching all 5 topics concurrently instead
        # bounds the wait to roughly one topic's worst case.
        errors = []
        with ThreadPoolExecutor(max_workers=len(_TOPICS)) as pool:
            futures = {
                pool.submit(_fetch_topic_text, topic, self._date_str): topic
                for topic in _TOPICS
            }
            for future, topic in futures.items():
                try:
                    text, source = future.result()
                    if text:
                        self._data[topic] = _parse_jsonl(text)
                        log.info(
                            "ArchiveMQTTReader: loaded %d %s records (%s)",
                            len(self._data[topic]), topic, source,
                        )
                    else:
                        log.info("ArchiveMQTTReader: no %s file for %s", topic, self._date_str)
                except Exception as exc:
                    log.error("ArchiveMQTTReader: fetch failed for %s: %s", topic, exc)
                    errors.append(f"{topic}: {exc}")

        self._loaded = False  # set True on main thread via _load_complete signal
        if errors:
            self.error.emit(f"Archive MQTT load errors: {'; '.join(errors)}")
        self._load_complete.emit()


    def _emit_vehicles(self, t: datetime) -> None:
        """Emit the most recent position for each vehicle at or before t."""
        latest: dict[str, dict] = {}
        for ts, obj in self._data["vehicles"]:
            if ts > t:
                break
            vid = obj.get("vehicle_id")
            if vid:
                latest[vid] = obj
        for obj in latest.values():
            try:
                obs = _observation_from_payload(obj)
                self.vehicle_position.emit(obs)
            except Exception as exc:
                log.debug("ArchiveMQTTReader: vehicle parse error: %s", exc)

    def _emit_annotations(self, t: datetime) -> None:
        """Apply annotation creates/deletes up to time t."""
        for ts, obj in self._data["annotations"]:
            if ts > t:
                break
            ann_id = obj.get("id", "")
            if not ann_id:
                continue
            if obj.get("deleted"):
                if ann_id in self._emitted_ids["annotations"]:
                    self._emitted_ids["annotations"].discard(ann_id)
                    self.annotation_deleted.emit(ann_id, obj.get("deleted_at", ""))
            elif ann_id not in self._emitted_ids["annotations"]:
                try:
                    ann = Annotation.from_dict(obj)
                    self._emitted_ids["annotations"].add(ann_id)
                    self.annotation_received.emit(ann)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: annotation parse error: %s", exc)

    def _emit_cones(self, t: datetime) -> None:
        """Apply cone creates/deletes up to time t."""
        for ts, obj in self._data["cones"]:
            if ts > t:
                break
            cone_id = obj.get("id", "")
            if not cone_id:
                continue
            if obj.get("deleted"):
                if cone_id in self._emitted_ids["cones"]:
                    self._emitted_ids["cones"].discard(cone_id)
                    self.cone_deleted.emit(cone_id)
            elif cone_id not in self._emitted_ids["cones"]:
                try:
                    cone = StormCone.from_dict(obj)
                    self._emitted_ids["cones"].add(cone_id)
                    self.cone_received.emit(cone)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: cone parse error: %s", exc)

    def _emit_drawings(self, t: datetime) -> None:
        """Apply drawing creates/deletes up to time t."""
        for ts, obj in self._data["drawings"]:
            if ts > t:
                break
            drawing_id = obj.get("id", "")
            if not drawing_id:
                continue
            if obj.get("deleted"):
                if drawing_id in self._emitted_ids["drawings"]:
                    self._emitted_ids["drawings"].discard(drawing_id)
                    self.drawing_deleted.emit(drawing_id)
            elif drawing_id not in self._emitted_ids["drawings"]:
                try:
                    drawing = DrawingAnnotation.from_dict(obj)
                    self._emitted_ids["drawings"].add(drawing_id)
                    self.drawing_received.emit(drawing)
                except Exception as exc:
                    log.debug("ArchiveMQTTReader: drawing parse error: %s", exc)

    def _emit_scan_sectors(self, t: datetime) -> None:
        """Emit the most recent scan-sector state for each vehicle at or before t."""
        latest: dict[str, dict] = {}
        for ts, obj in self._data["scan_sectors"]:
            if ts > t:
                break
            vid = obj.get("vehicle_id")
            if vid:
                latest[vid] = obj
        for obj in latest.values():
            try:
                self.scan_sector_received.emit(ScanSector.from_dict(obj))
            except Exception as exc:
                log.debug("ArchiveMQTTReader: scan sector parse error: %s", exc)
