
from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

from data import endpoints
log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
POLL_INTERVAL_ACTIVE  = 120   # watches / MDs / NWS warnings — change throughout the day
POLL_INTERVAL_OUTLOOK = 900   # SPC categorical + probability — updates on a fixed schedule
SPC_CACHE_TTL         = 900   # in-memory cache TTL (aligned with outlook poll interval)

_SPC_WX_BASE = endpoints.NWS_SPC_OUTLOOK_LAYERS
_SPC_QUERY_SUFFIX = "where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326"
SPC_URLS = {
    "cat":  f"{_SPC_WX_BASE}/1/query?{_SPC_QUERY_SUFFIX}",
    "tor":  f"{_SPC_WX_BASE}/3/query?{_SPC_QUERY_SUFFIX}",
    "hail": f"{_SPC_WX_BASE}/5/query?{_SPC_QUERY_SUFFIX}",
    "wind": f"{_SPC_WX_BASE}/7/query?{_SPC_QUERY_SUFFIX}",
}

SPC_SIG_URLS = {
    "tor":  f"{_SPC_WX_BASE}/2/query?{_SPC_QUERY_SUFFIX}",
    "hail": f"{_SPC_WX_BASE}/4/query?{_SPC_QUERY_SUFFIX}",
    "wind": f"{_SPC_WX_BASE}/6/query?{_SPC_QUERY_SUFFIX}",
}

SPC_DAY_LAYER_IDS: dict[int, dict[str, int]] = {
    1: {"cat": 1, "tor": 3, "hail": 5, "wind": 7, "tor_sig": 2, "hail_sig": 4, "wind_sig": 6},
    2: {"cat": 9, "tor": 11, "hail": 13, "wind": 15, "tor_sig": 10, "hail_sig": 12, "wind_sig": 14},
    3: {"cat": 17, "prob": 19, "sig": 18},
    4: {"prob": 21},
    5: {"prob": 22},
    6: {"prob": 23},
    7: {"prob": 24},
    8: {"prob": 25},
}


def _spc_layer_url(layer_id: int) -> str:
    return f"{_SPC_WX_BASE}/{layer_id}/query?{_SPC_QUERY_SUFFIX}"


def _empty_spc_payload() -> tuple[str, str, str, str, str, str]:
    return (
        _EMPTY_FC_STR,
        _EMPTY_FC_STR,
        _EMPTY_FC_STR,
        _EMPTY_FC_STR,
        _EMPTY_FC_STR,
        _EMPTY_FC_STR,
    )

SPC_MD_URL = (
    f"{endpoints.NWS_SPC_MD_LAYERS}/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326"
)

# wwa MapServer — active NWS warnings (sig='W').  Layer 0 serves storm-based polygon geometry
WWA_WARNINGS_URL = (
    f"{endpoints.NWS_WWA_LAYERS}/0/query"
    "?where=sig%3D%27W%27"
    "&outFields=prod_type,phenom,event,wfo,onset,ends,expiration,url"
    "&returnGeometry=true&f=geojson&outSR=4326"
)

# wwa MapServer — county-level polygons for active SPC watches (TO=tornado, SV=severe tstorm).
WWA_WATCHES_URL = (
    f"{endpoints.NWS_WWA_LAYERS}/1/query"
    "?where=sig%3D%27A%27%20AND%20(phenom%3D%27TO%27%20OR%20phenom%3D%27SV%27)"
    "&outFields=prod_type,phenom,event,wfo,onset,ends,expiration,url"
    "&returnGeometry=true&f=geojson&outSR=4326"
)

_EMPTY_FC_STR = '{"type":"FeatureCollection","features":[]}'


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def _spc_cat_key(props: dict[str, Any]) -> str:
    txt = " ".join(_norm(v) for v in props.values())
    if "HIGH" in txt:
        return "HIGH"
    if "MDT" in txt or "MODERATE" in txt:
        return "MDT"
    if "ENH" in txt or "ENHANCED" in txt:
        return "ENH"
    if "SLGT" in txt or "SLIGHT" in txt:
        return "SLGHT"
    if "MRGL" in txt or "MARGINAL" in txt:
        return "MRGL"
    return ""


def _spc_prob_label(props: dict[str, Any]) -> str | None:
    """Return a normalized probability label string for SPC probabilistic layers."""
    lbl2 = (props.get("label2") or props.get("LABEL2") or props.get("Label2"))
    if lbl2:
        s2 = str(lbl2).strip().upper()
        if s2 in ("SIGN", "CIG1", "CIG2", "CIG3"):
            return s2
    lbl_raw = props.get("LABEL") or props.get("label") or props.get("Label")
    if lbl_raw:
        s = str(lbl_raw).strip().upper()
        if s in ("SIGN", "CIG1", "CIG2", "CIG3"):
            return s
    dn = props.get("dn")
    if dn is None:
        dn = props.get("DN")
    if dn is not None:
        try:
            return str(int(dn))
        except (TypeError, ValueError):
            pass
    if lbl_raw is None:
        return None
    s = str(lbl_raw).strip().replace("%", "")
    if not s:
        return None
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        return s


def _fc_has_features(fc_str: str) -> bool:
    return bool(fc_str and fc_str != _EMPTY_FC_STR)


class HazardFetcher(QObject):
    """
    Polls SPC + NWS hazard feeds in the background.

    Signals emit pre-serialized JSON strings (not dicts) to avoid redundant
    serialization when pushing data to the MapLibre JS layer.

    Signals:
      spc_received(str, str, str, str, str, str): cat, wind, hail, tor, prob, sig GeoJSON strings
      nws_received(str): warnings GeoJSON string
      spc_watches_received(str): watch polygons GeoJSON string
      spc_mds_received(str): MD polygons GeoJSON string
      fetch_error(str): recoverable error text
    """

    spc_received             = pyqtSignal(object, object, object, object, object, object)
    nws_received             = pyqtSignal(object)
    nws_raw_phenoms_received = pyqtSignal(object)   # set[str] of raw (unfiltered) phenom codes
    spc_watches_received     = pyqtSignal(object)
    spc_mds_received         = pyqtSignal(object)
    fetch_error              = pyqtSignal(str)
    connectivity_changed     = pyqtSignal(bool)   # True = back online, False = offline

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._fetch_lock = threading.Lock()

        self._spc_categories = {"MRGL": False, "SLGHT": False, "ENH": False, "MDT": False, "HIGH": False}
        self._spc_products    = {"wind": False, "hail": False, "tor": False, "prob": False, "sig": False}
        self._spc_day = 1
        self._spc_watches_enabled = False
        self._spc_mds_enabled     = False
        self._nws_enabled         = False
        # optional user-selected set of allowed NWS phenom codes (e.g. {'TO','SV'}).
        self._nws_filter: set[str] | None = None

        # nws warnings bbox — set from MBTiles domain at startup
        self._nws_bbox = (-116.0, 28.0, -82.0, 49.0)

        # in-memory cache — stores pre-serialized JSON strings
        self._spc_cache_by_day: dict[int, tuple[str, str, str, str, str, str]] = {}
        self._spc_cache_time_by_day: dict[int, float] = {}
        self._spc_last_poll_by_day: dict[int, float] = {}   # controls POLL_INTERVAL_OUTLOOK gating

        self._watches_cache: str | None = None
        self._watches_cache_time: float = 0.0

        self._mds_cache: str | None = None
        self._mds_cache_time: float = 0.0

        self._nws_cache: str | None = None
        self._nws_cache_time: float = 0.0

        # per-URL SHA-256 hashes for change detection — skip map push when unchanged
        self._response_hashes: dict[str, str] = {}

        # etag conditional-request support — store server ETag + last raw bytes per URL
        self._etags: dict[str, str] = {}
        self._raw_cache: dict[str, bytes] = {}

        # connectivity tracking — go offline after 2 consecutive failed poll cycles
        self._consecutive_failures = 0
        self._is_offline = False


    def set_spc_category_enabled(self, key: str, enabled: bool):
        k = _norm(key)
        if k in self._spc_categories:
            self._spc_categories[k] = bool(enabled)

    def set_spc_product_enabled(self, key: str, enabled: bool):
        k = key.strip().lower()
        if k in self._spc_products:
            self._spc_products[k] = bool(enabled)

    def set_spc_day(self, day: int):
        d = max(1, min(8, int(day or 1)))
        if d != self._spc_day:
            self._spc_day = d
            self.force_spc_refresh()

    def set_nws_enabled(self, enabled: bool):
        self._nws_enabled = bool(enabled)

    def set_nws_filter(self, allowed: set[str] | None):
        """Set an optional set of allowed VTEC phenom codes (uppercase). None = no filter (show all)."""
        if allowed is None:
            self._nws_filter = None
        else:
            self._nws_filter = {str(x).strip().upper() for x in allowed}

    def set_spc_watches_enabled(self, enabled: bool):
        self._spc_watches_enabled = bool(enabled)

    def set_spc_mds_enabled(self, enabled: bool):
        self._spc_mds_enabled = bool(enabled)

    def set_nws_bbox(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float):
        self._nws_bbox = (lon_min, lat_min, lon_max, lat_max)


    def is_spc_fresh(self) -> bool:
        return (
            self._spc_day in self._spc_cache_by_day
            and time.time() - self._spc_cache_time_by_day.get(self._spc_day, 0.0) < SPC_CACHE_TTL
        )

    def is_watches_fresh(self) -> bool:
        return self._watches_cache is not None and time.time() - self._watches_cache_time < SPC_CACHE_TTL

    def is_mds_fresh(self) -> bool:
        return self._mds_cache is not None and time.time() - self._mds_cache_time < SPC_CACHE_TTL

    def is_nws_fresh(self) -> bool:
        return self._nws_cache is not None and time.time() - self._nws_cache_time < SPC_CACHE_TTL

    def emit_cached_spc(self):
        cached = self._spc_cache_by_day.get(self._spc_day)
        if cached is not None:
            self.spc_received.emit(*cached)

    def spc_category_cached(self) -> bool:
        cached = self._spc_cache_by_day.get(self._spc_day)
        if not cached:
            return False
        cat_str, _, _, _, _, _ = cached
        return _fc_has_features(cat_str)

    def spc_product_cached(self, key: str) -> bool:
        cached = self._spc_cache_by_day.get(self._spc_day)
        if not cached:
            return False
        _, wind_str, hail_str, tor_str, prob_str, sig_str = cached
        k = key.strip().lower()
        if k == "wind":
            return _fc_has_features(wind_str)
        if k == "hail":
            return _fc_has_features(hail_str)
        if k == "tor":
            return _fc_has_features(tor_str)
        if k == "prob":
            return _fc_has_features(prob_str)
        if k == "sig":
            return _fc_has_features(sig_str)
        return False

    def force_spc_refresh(self):
        self._spc_last_poll_by_day[self._spc_day] = 0

    def emit_cached_watches(self):
        if self._watches_cache is not None:
            self.spc_watches_received.emit(self._watches_cache)

    def emit_cached_mds(self):
        if self._mds_cache is not None:
            self.spc_mds_received.emit(self._mds_cache)

    def emit_cached_nws(self):
        if self._nws_cache is None:
            return
        # _nws_cache always stores raw (unfiltered) data; apply filter at emit time.
        _filt = getattr(self, "_nws_filter", None)
        if _filt:
            try:
                data = json.loads(self._nws_cache)
                feats = [
                    f for f in (data.get("features") or [])
                    if str((f.get("properties") or {}).get("phenom", "")).upper() in _filt
                ]
                self.nws_received.emit(json.dumps({"type": "FeatureCollection", "features": feats}))
                return
            except Exception:
                pass
        self.nws_received.emit(self._nws_cache)


    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()

    def fetch_now(self):
        if not self._fetch_lock.acquire(blocking=False):
            return

        def _run():
            try:
                self._fetch_cycle()
            finally:
                self._fetch_lock.release()

        threading.Thread(target=_run, daemon=True).start()

    def _record_success(self):
        self._consecutive_failures = 0
        if self._is_offline:
            self._is_offline = False
            self.connectivity_changed.emit(True)

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= 2 and not self._is_offline:
            self._is_offline = True
            self.connectivity_changed.emit(False)

    def _poll_loop(self):
        while self._running:
            if self._fetch_lock.acquire(blocking=False):
                try:
                    self._fetch_cycle()
                finally:
                    self._fetch_lock.release()
            self._stop_event.wait(POLL_INTERVAL_ACTIVE)
            self._stop_event.clear()

    def _fetch_cycle(self):
        try:
            now = time.time()
            # spc outlook re-fetched on its own longer interval; active hazards every poll.
            spc_due   = (
                (any(self._spc_categories.values()) or any(self._spc_products.values()))
                and (
                    (now - self._spc_last_poll_by_day.get(self._spc_day, 0.0) >= POLL_INTERVAL_OUTLOOK)
                    or self._spc_day not in self._spc_cache_by_day
                )
            )
            need_watches = self._spc_watches_enabled
            need_mds     = self._spc_mds_enabled
            need_nws     = self._nws_enabled

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                spc_f     = pool.submit(self._fetch_spc)            if spc_due      else None
                mds_f     = pool.submit(self._fetch_spc_mds)        if need_mds     else None
                watches_f = pool.submit(self._fetch_spc_watches)    if need_watches else None
                nws_f     = pool.submit(self._fetch_nws_warnings)   if need_nws     else None

                futures = [
                    (lbl, f) for lbl, f in [
                        ("spc", spc_f), ("mds", mds_f),
                        ("watches", watches_f), ("nws", nws_f),
                    ] if f is not None
                ]
                any_success = False
                for label, f in futures:
                    try:
                        f.result(timeout=REQUEST_TIMEOUT_SECONDS + 5)
                        any_success = True
                    except concurrent.futures.TimeoutError:
                        log.warning("Hazard fetch timed out for %s", label)
                        self.fetch_error.emit(f"Hazard fetch timed out ({label})")
                    except Exception as exc:
                        log.exception("Hazard fetch failed for %s", label)
                        self.fetch_error.emit(f"Hazard fetch failed ({label}): {exc}")

                if futures:
                    if any_success:
                        self._record_success()
                    else:
                        self._record_failure()

        except Exception as exc:
            log.exception("Hazard fetch cycle failed")
            self.fetch_error.emit(f"Hazard fetch failed: {exc}")


    def _get_raw(self, url: str) -> tuple[bytes, bool]:
        """Fetch URL with gzip + ETag conditional-request support.

        Returns (bytes, changed).  changed=False means the response is
        byte-for-byte identical to the previous fetch (HTTP 304 from the server
        or a local SHA-256 match); callers can skip parsing entirely.
        """
        headers = {
            "User-Agent": "STORM/1.0 (contact: support)",
            "Accept": "application/geo+json, application/json",
            "Accept-Encoding": "gzip",
        }
        if url in self._etags:
            headers["If-None-Match"] = self._etags[url]

        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self._etags[url] = new_etag
                self._raw_cache[url] = raw
        except HTTPError as exc:
            if exc.code == 304:
                # server confirms nothing changed — return cached bytes, unchanged.
                return self._raw_cache.get(url, b""), False
            raise

        h = hashlib.sha256(raw).hexdigest()
        changed = self._response_hashes.get(url) != h
        if changed:
            self._response_hashes[url] = h
        return raw, changed


    @staticmethod
    def _esri_ts_to_epoch(val) -> float | None:
        """Convert an ESRI millisecond timestamp to Unix seconds, or None if unparseable."""
        if val is None:
            return None
        try:
            return float(val) / 1000.0
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _feature_active(feat: dict, now: float) -> bool:
        """Return True if the feature has not yet expired per its ESRI timestamp fields."""
        props = feat.get("properties") or {}
        for key in ("expiration", "ends"):
            ts = HazardFetcher._esri_ts_to_epoch(props.get(key))
            if ts is not None:
                return ts > now
        return True  # no expiration info — assume still active

    def _expire_nws_cache(self, now: float):
        """Prune locally-expired NWS features from the raw cache, then re-emit with phenom filter."""
        if not self._nws_cache:
            return
        try:
            data = json.loads(self._nws_cache)
            feats = data.get("features", [])
            active = [f for f in feats if self._feature_active(f, now)]
            if len(active) == len(feats):
                return  # nothing expired
            self._nws_cache = json.dumps({"type": "FeatureCollection", "features": active})
            self._nws_cache_time = now
            # emit updated raw phenoms
            raw_phenoms = {
                str((f.get("properties") or {}).get("phenom", "")).upper()
                for f in active if (f.get("properties") or {}).get("phenom")
            }
            self.nws_raw_phenoms_received.emit(raw_phenoms)
            # emit with phenom filter applied
            self.emit_cached_nws()
        except Exception as exc:
            log.warning("_expire_nws_cache failed: %s", exc)

    def _expire_cached_fc(self, cache_attr: str, cache_time_attr: str, signal, now: float):
        """Prune expired features from a cached FeatureCollection and re-emit if any were removed.

        Called on unchanged poll cycles so that locally-expired features are
        removed from the map without waiting for the server feed to update.
        """
        cached: str | None = getattr(self, cache_attr)
        if not cached:
            return
        try:
            data = json.loads(cached)
            feats = data.get("features", [])
            active = [f for f in feats if self._feature_active(f, now)]
            if len(active) == len(feats):
                return  # nothing expired yet
            filtered = json.dumps({"type": "FeatureCollection", "features": active})
            setattr(self, cache_attr, filtered)
            setattr(self, cache_time_attr, now)
            signal.emit(filtered)
        except Exception as exc:
            log.warning("_filter_expired failed for %s: %s", cache_attr, exc)


    def _fetch_spc(self):
        now = time.time()
        day = self._spc_day
        layers = SPC_DAY_LAYER_IDS.get(day, SPC_DAY_LAYER_IDS[1])
        self._spc_last_poll_by_day[day] = now  # mark polled immediately — prevents retry spam on failure

        # seed from existing cache strings; non-enabled products keep their cached value.
        cat_str, wind_str, hail_str, tor_str, prob_str, sig_str = self._spc_cache_by_day.get(
            day,
            _empty_spc_payload(),
        )
        any_changed = False

        prev_prob_strings = {
            "wind": wind_str,
            "hail": hail_str,
            "tor": tor_str,
            "prob": prob_str,
        }

        # categorical outlook
        if any(self._spc_categories.values()) and "cat" in layers:
            try:
                raw, changed = self._get_raw(_spc_layer_url(layers["cat"]))
                if changed:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    feats = []
                    for f in data.get("features", []):
                        props = dict(f.get("properties") or {})
                        cat = _spc_cat_key(props)
                        if not cat:
                            continue
                        props["cat"] = cat
                        props["spc_day"] = day
                        props["spc_product"] = "outlook"
                        feats.append({
                            "type": "Feature",
                            "geometry": f.get("geometry"),
                            "properties": props,
                        })
                    cat_str = json.dumps({"type": "FeatureCollection", "features": feats})
                    any_changed = True
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                log.warning("SPC categorical fetch failed: %s", exc)
                self.fetch_error.emit(f"SPC categorical fetch failed: {exc}")

        # probability products — only enabled ones; disabled keep their cached string
        for key in ("wind", "hail", "tor", "prob"):
            if not self._spc_products.get(key, False) or key not in layers:
                continue
            try:
                raw, changed = self._get_raw(_spc_layer_url(layers[key]))
                if changed:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    feats = []
                    for f in data.get("features", []):
                        props = dict(f.get("properties") or {})
                        label = _spc_prob_label(props)
                        if label is not None:
                            props["LABEL"] = label
                        props["spc_day"] = day
                        props["spc_product"] = key
                        feats.append({
                            "type": "Feature",
                            "geometry": f.get("geometry"),
                            "properties": props,
                        })
                    s = json.dumps({"type": "FeatureCollection", "features": feats})
                    if key == "wind":
                        wind_str = s
                    elif key == "hail":
                        hail_str = s
                    elif key == "tor":
                        tor_str = s
                    else:
                        prob_str = s
                    any_changed = True
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                log.warning("SPC %s fetch failed: %s", key, exc)
                self.fetch_error.emit(f"SPC {key} fetch failed: {exc}")

            # significant layer: merge into the same product as LABEL="SIGN"
            sig_key = f"{key}_sig"
            sig_layer_id = layers.get(sig_key)
            if key == "prob" and sig_layer_id is None:
                sig_layer_id = layers.get("sig")
            if self._spc_products.get(key, False) and sig_layer_id is not None:
                try:
                    raw, changed = self._get_raw(_spc_layer_url(sig_layer_id))
                    if changed:
                        data = json.loads(raw.decode("utf-8", errors="replace"))
                        sig_feats = []
                        for f in data.get("features", []):
                            props = dict(f.get("properties") or {})
                            props["LABEL"] = _spc_prob_label(props) or "SIGN"
                            props["spc_day"] = day
                            props["spc_product"] = key
                            sig_feats.append({
                                "type": "Feature",
                                "geometry": f.get("geometry"),
                                "properties": props,
                            })

                        current_str = (
                            wind_str if key == "wind"
                            else hail_str if key == "hail"
                            else tor_str if key == "tor"
                            else prob_str
                        )
                        base = json.loads(current_str or _EMPTY_FC_STR)
                        base_feats = list(base.get("features") or [])
                        # replace any existing SIGN features to avoid duplicates or stale sig areas.
                        base_feats = [
                            bf for bf in base_feats
                            if (bf.get("properties") or {}).get("LABEL") != "SIGN"
                        ]
                        base_feats.extend(sig_feats)
                        merged = json.dumps({"type": "FeatureCollection", "features": base_feats})
                        if key == "wind":
                            wind_str = merged
                        elif key == "hail":
                            hail_str = merged
                        elif key == "tor":
                            tor_str = merged
                        else:
                            prob_str = merged
                        any_changed = True
                    elif prev_prob_strings[key] != (
                        wind_str if key == "wind"
                        else hail_str if key == "hail"
                        else tor_str if key == "tor"
                        else prob_str
                    ):
                        base = json.loads(
                            (
                                wind_str if key == "wind"
                                else hail_str if key == "hail"
                                else tor_str if key == "tor"
                                else prob_str
                            )
                            or _EMPTY_FC_STR
                        )
                        prev_base = json.loads(prev_prob_strings[key] or _EMPTY_FC_STR)
                        sig_feats = [
                            bf for bf in (prev_base.get("features") or [])
                            if (bf.get("properties") or {}).get("LABEL") == "SIGN"
                        ]
                        if sig_feats:
                            base_feats = [
                                bf for bf in (base.get("features") or [])
                                if (bf.get("properties") or {}).get("LABEL") != "SIGN"
                            ]
                            base_feats.extend(sig_feats)
                            merged = json.dumps({"type": "FeatureCollection", "features": base_feats})
                            if key == "wind":
                                wind_str = merged
                            elif key == "hail":
                                hail_str = merged
                            elif key == "tor":
                                tor_str = merged
                            else:
                                prob_str = merged
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                    log.warning("SPC %s significant fetch failed: %s", key, exc)
                    self.fetch_error.emit(f"SPC {key} significant fetch failed: {exc}")

        if self._spc_products.get("sig", False) and "sig" in layers:
            try:
                raw, changed = self._get_raw(_spc_layer_url(layers["sig"]))
                if changed:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    sig_feats = []
                    for f in data.get("features", []):
                        props = dict(f.get("properties") or {})
                        props["LABEL"] = _spc_prob_label(props) or "SIGN"
                        props["spc_day"] = day
                        props["spc_product"] = "sig"
                        sig_feats.append({
                            "type": "Feature",
                            "geometry": f.get("geometry"),
                            "properties": props,
                        })
                    sig_str = json.dumps({"type": "FeatureCollection", "features": sig_feats})
                    any_changed = True
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                log.warning("SPC day %s significant fetch failed: %s", day, exc)
                self.fetch_error.emit(f"SPC significant fetch failed: {exc}")

        # always update cache (preserves cached strings for non-enabled products)
        self._spc_cache_by_day[day] = (cat_str, wind_str, hail_str, tor_str, prob_str, sig_str)
        self._spc_cache_time_by_day[day] = now
        if any_changed:
            self.spc_received.emit(cat_str, wind_str, hail_str, tor_str, prob_str, sig_str)

    def _fetch_nws_warnings(self):
        """Fetch active NWS warnings from the NOAA WWA MapServer (Layer 0).

        The NWS Active Alerts API returns geometry:null for county-based products,
        silently dropping many warnings.  The WWA MapServer always provides actual
        polygon geometry for every active warning.
        """
        now = time.time()
        try:
            raw, changed = self._get_raw(WWA_WARNINGS_URL)
            if not changed:
                self._nws_cache_time = now  # refresh TTL even when data is stable
                # server confirmed no new warnings — but some cached ones may have
                self._expire_nws_cache(now)
                return
            data = json.loads(raw.decode("utf-8", errors="replace"))
            feats = []
            for f in data.get("features", []):
                props = dict(f.get("properties") or {})
                geom = f.get("geometry")
                if not geom:
                    continue
                phenom = str(props.get("phenom", "")).upper()
                props["nws_color"]   = _nws_color_for_phenom(phenom)
                props["warning_url"] = str(props.get("url", "")).strip()
                feats.append({"type": "Feature", "geometry": geom, "properties": props})
            raw_str = json.dumps({"type": "FeatureCollection", "features": feats})
        except Exception as exc:
            log.warning("NWS warnings fetch failed: %s", exc)
            self.fetch_error.emit(f"NWS warnings fetch failed: {exc}")
            return
        # store raw (unfiltered) data in cache so that clearing the filter later
        self._nws_cache      = raw_str
        self._nws_cache_time = now
        # emit raw phenoms for legend button tracking (always unfiltered)
        raw_phenoms = {
            str((f.get("properties") or {}).get("phenom", "")).upper()
            for f in feats
            if (f.get("properties") or {}).get("phenom")
        }
        self.nws_raw_phenoms_received.emit(raw_phenoms)
        # apply filter at emit time (not at cache time)
        _filt = getattr(self, "_nws_filter", None)
        if _filt:
            emit_feats = [
                f for f in feats
                if str((f.get("properties") or {}).get("phenom", "")).upper() in _filt
            ]
            self.nws_received.emit(json.dumps({"type": "FeatureCollection", "features": emit_feats}))
        else:
            self.nws_received.emit(raw_str)

    def _fetch_spc_watches(self):
        """Fetch active SPC tornado/severe-thunderstorm watch polygons from the
        NOAA WWA MapServer.  The NWS Active Alerts API omits geometry for SPC
        watches, so we use the dedicated WWA layer which serves county-level
        polygons for every active watch.
        """
        now = time.time()
        try:
            raw, changed = self._get_raw(WWA_WATCHES_URL)
            if not changed:
                self._watches_cache_time = now
                self._expire_cached_fc("_watches_cache", "_watches_cache_time", self.spc_watches_received, now)
                return
            data = json.loads(raw.decode("utf-8", errors="replace"))
            feats = []
            for f in data.get("features", []):
                props = dict(f.get("properties") or {})
                geom = f.get("geometry")
                if not geom:
                    continue
                phenom = str(props.get("phenom", "")).upper()
                prod   = str(props.get("prod_type", "")).lower()
                # "event" field = zero-padded watch number (e.g. "0029").
                raw_event = str(props.get("event", "")).strip()
                try:
                    props["watch_num"] = str(int(raw_event)).zfill(4)
                except (TypeError, ValueError):
                    props["watch_num"] = raw_event
                props["watch_url"] = str(props.get("url", "")).strip()
                if phenom == "TO" or "tornado" in prod:
                    props["watch_color"] = "#FF0000"
                    props["event"]       = "Tornado Watch"
                else:
                    props["watch_color"] = "#4169E1"
                    props["event"]       = "Severe Thunderstorm Watch"
                feats.append({"type": "Feature", "geometry": geom, "properties": props})
            out_str = json.dumps({"type": "FeatureCollection", "features": feats})
        except Exception as exc:
            log.warning("SPC watches fetch failed: %s", exc)
            self.fetch_error.emit(f"SPC watches fetch failed: {exc}")
            return
        self._watches_cache      = out_str
        self._watches_cache_time = now
        self.spc_watches_received.emit(out_str)

    def _fetch_spc_mds(self):
        now = time.time()
        try:
            raw, changed = self._get_raw(SPC_MD_URL)
            if not changed:
                self._mds_cache_time = now
                return
            data = json.loads(raw.decode("utf-8", errors="replace"))
            feats = []
            for f in data.get("features", []):
                props = dict(f.get("properties") or {})
                # the endpoint returns a tiny "NoArea" placeholder when no MDs are active.
                if str(props.get("name", "")).strip().lower() in ("noarea", "no area", ""):
                    continue
                feats.append({
                    "type": "Feature",
                    "geometry": f.get("geometry"),
                    "properties": props,
                })
            out_str = json.dumps({"type": "FeatureCollection", "features": feats})
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("SPC MD fetch failed: %s", exc)
            self.fetch_error.emit(f"SPC MD fetch failed: {exc}")
            return
        self._mds_cache      = out_str
        self._mds_cache_time = now
        self.spc_mds_received.emit(out_str)


def _nws_color_for_phenom(phenom: str) -> str:
    """Map a VTEC phenom code to a display color for NWS warnings."""
    return {
        "TO": "#FF0000",   # Tornado Warning – red
        "SV": "#FFD700",   # Severe Thunderstorm Warning – yellow
        "FF": "#00FF00",   # Flash Flood Warning – green
        "FA": "#00FF00",   # Flood Advisory – green
        "FL": "#00FF7F",   # Flood Warning – spring green
        "WS": "#FF69B4",   # Winter Storm Warning – pink
        "WW": "#FF69B4",   # Winter Weather Advisory – pink
        "BZ": "#FF4500",   # Blizzard Warning – orange-red
        "MA": "#87CEEB",   # Marine Warning – sky blue
        "HF": "#DA70D6",   # Hurricane Force Wind Warning – orchid
        "HU": "#DA70D6",   # Hurricane Warning – orchid
        "TS": "#DA70D6",   # Tropical Storm Warning – orchid
    }.get(phenom, "#FFD700")
