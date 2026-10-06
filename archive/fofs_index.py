"""Index of every FOFS mobile-mesonet daily file THREDDS publishes.

The FOFS/Mobile-Mesonet tree is reorganized often (files are added daily,
folders move), so nothing here assumes a layout: the whole tree is crawled
through its THREDDS catalog.xml pages, and every .txt file whose name
carries a YYYYMMDD date is recorded against the vehicle its folder names.
The vehicle is the nearest enclosing folder that isn't a generic container
("raw", "processed", "_legacy_data", a year, ...), so data/probe1/raw/ and
data/_legacy_data/probe1/ both belong to probe1, and a new vehicle folder
shows up without any code change.

Not every indexed file is readable: _legacy_data holds the pre-conversion
originals of the published files, in each logger era's own format, with
exactly the same rows. The loader only uses files in the standard
published format (checked from the header), so those are indexed but
skipped -- see vehicle_obs_archive_fetcher.

The index is cached on disk for a few hours; a failed crawl never replaces
a good cached one.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from archive import thredds_paths as paths
log = logging.getLogger(__name__)

_CATALOG_BASE = paths.catalog_root()
_FILE_BASE = paths.file_root()
ROOT = paths.FOFS_MESONET
_NS = "{" + paths.THREDDS_NS + "}"
_DATE_IN_NAME = re.compile(r"(?<!\d)((?:19|20)\d{6})(?!\d)")
# Folder names that group files rather than name a vehicle.
_CONTAINERS = {"raw", "processed", "data", "_legacy_data", "legacy", "csv", "txt", "qc", "files",
               "mobile-mesonet", "fofs"}
_MAX_DEPTH = 8
_WORKERS = 6
CACHE_PATH = Path.home() / ".cache" / "storm" / "fofs_index.json"  # beside STORM's other caches
CACHE_TTL_S = 6 * 3600


@dataclass(frozen=True)
class FofsFile:
    vehicle: str
    date: date      # the date in the file name -- not necessarily the data's
    path: str       # THREDDS path below the catalog/fileServer roots
    modified: str

    @property
    def url(self) -> str:
        return f"{_FILE_BASE}/{self.path}"


def vehicle_for_path(path: str) -> str | None:
    """The vehicle a file belongs to: a folder on its path that matches a
    known vehicle apart from case and punctuation ("Probe_1" -> probe1),
    else the first folder below the tree's root that isn't a generic
    container or a year -- so data/probe1/raw/, data/_legacy_data/probe1/,
    data/probe1/qc_v2/ and data/2025/probe1/ all belong to probe1, and an
    unfamiliar vehicle folder still gets its own name."""
    relative = path[len(ROOT) + 1:] if path.startswith(ROOT + "/") else path
    folders = [f.strip().lower() for f in relative.split("/")[:-1] if f.strip()]
    for folder in folders:
        known = _known_vehicle(folder)
        if known:
            return known
    for folder in folders:
        if folder in _CONTAINERS or re.fullmatch(r"(19|20)\d{2}(\d{2}){0,2}", folder):
            continue
        cleaned = re.sub(r"[^a-z0-9_]+", "_", folder).strip("_")
        return cleaned or None
    return None


def _known_vehicle(folder: str) -> str | None:
    from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS
    bare = re.sub(r"[^a-z0-9]", "", folder)
    for known in KNOWN_FOFS_PLATFORMS:
        if re.sub(r"[^a-z0-9]", "", known) == bare:
            return known
    return None


def _date_from_name(name: str) -> date | None:
    match = _DATE_IN_NAME.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], "%Y%m%d").date()
    except ValueError:
        return None


def crawl(fetch_xml: Callable[[str], bytes | None], root: str = ROOT) -> list[FofsFile]:
    """Every dated .txt file below `root`. `fetch_xml(catalog_path)` returns
    a catalog.xml body, or None for a missing catalog; any other failure
    propagates, so a partial crawl is never mistaken for a complete one."""
    files: list[FofsFile] = []
    lock = threading.Lock()

    def visit(catalog_path: str) -> list[str]:
        body = fetch_xml(catalog_path)
        if body is None:
            return []
        base = catalog_path.rsplit("/", 1)[0]
        tree = ET.fromstring(body)
        children = []
        for ref in tree.iter(f"{_NS}catalogRef"):
            href = ref.get("{http://www.w3.org/1999/xlink}href", "")
            if href and not href.startswith(("http:", "https:", "/")):
                children.append(f"{base}/{href}")
        for dataset in tree.iter(f"{_NS}dataset"):
            url_path = dataset.get("urlPath")
            name = dataset.get("name", "")
            if not url_path or not name.lower().endswith(".txt"):
                continue
            day = _date_from_name(name)
            vehicle = vehicle_for_path(url_path)
            if day is None or vehicle is None:
                continue
            modified = dataset.find(f"{_NS}date[@type='modified']")
            with lock:
                files.append(FofsFile(vehicle, day, url_path, modified.text if modified is not None else ""))
        return children

    level = [f"{root}/catalog.xml"]
    seen = set(level)
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for _ in range(_MAX_DEPTH):
            if not level:
                break
            next_level = []
            for children in pool.map(visit, level):
                for child in children:
                    if child not in seen:
                        seen.add(child)
                        next_level.append(child)
            level = next_level
    files.sort(key=lambda f: (f.vehicle, f.date, f.path))
    return files


class FofsIndex:
    """The crawled file list, grouped for the loader and the calendar."""

    def __init__(self, files: list[FofsFile], built_at: float):
        self.files = files
        self.built_at = built_at
        self._by_vehicle_date: dict[tuple[str, date], list[FofsFile]] = {}
        for f in files:
            self._by_vehicle_date.setdefault((f.vehicle, f.date), []).append(f)

    def files_for(self, vehicle: str, day: date) -> list[FofsFile]:
        return self._by_vehicle_date.get((vehicle, day), [])

    def vehicles(self) -> set[str]:
        return {f.vehicle for f in self.files}

    def vehicles_with_files(self, days) -> set[str]:
        days = set(days)
        return {f.vehicle for f in self.files if f.date in days}

    def dates(self, vehicle: str | None = None) -> set[date]:
        return {f.date for f in self.files if vehicle is None or f.vehicle == vehicle}


_lock = threading.Lock()
_current: FofsIndex | None = None


def _read_cache(path: Path) -> FofsIndex | None:
    try:
        raw = json.loads(path.read_text())
        files = [FofsFile(f["vehicle"], date.fromisoformat(f["date"]), f["path"], f["modified"])
                 for f in raw["files"]]
        return FofsIndex(files, float(raw["built_at"]))
    except Exception:  # noqa: BLE001 - absent or unreadable cache
        return None


def _write_cache(path: Path, index: FofsIndex) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "built_at": index.built_at,
            "files": [{"vehicle": f.vehicle, "date": f.date.isoformat(), "path": f.path,
                       "modified": f.modified} for f in index.files],
        }))
        temporary.replace(path)
    except Exception as exc:  # noqa: BLE001 - a cache is an optimization only
        log.warning("FOFS index cache not written: %s", exc)


def get_index(
    fetch_xml: Callable[[str], bytes | None] | None = None,
    *,
    cache_path: Path | None = CACHE_PATH,
    max_age_s: float = CACHE_TTL_S,
    now: Callable[[], float] = time.time,
) -> FofsIndex | None:
    """The current index: from memory or disk when fresh enough, otherwise
    re-crawled. Returns a stale cache if a re-crawl fails, and None only
    when there is nothing at all to go on."""
    global _current
    with _lock:
        if _current is not None and now() - _current.built_at <= max_age_s:
            return _current
        cached = _read_cache(cache_path) if cache_path is not None else None
        if cached is not None and now() - cached.built_at <= max_age_s:
            _current = cached
            return cached
        try:
            started = time.monotonic()
            files = crawl(fetch_xml or _default_fetch_xml)
            if not files:
                # the tree always holds files; none means the crawl didn't
                # really happen (catalog down, blocked), not "no data"
                raise RuntimeError("crawl found no files")
            index = FofsIndex(files, now())
            log.info("FOFS index: %d files for %d vehicles in %.1fs",
                     len(files), len(index.vehicles()), time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - keep whatever we had
            log.warning("FOFS index crawl failed (%s); %s", exc,
                        "using the older cached index" if cached else "no index available")
            _current = cached
            return cached
        if cache_path is not None:
            _write_cache(cache_path, index)
        _current = index
        return index


def _default_fetch_xml(catalog_path: str) -> bytes | None:
    from urllib.error import HTTPError
    from urllib.request import Request
    from archive.fetchers.vehicle_obs_archive_fetcher import _USER_AGENT, _urlopen_with_retry
    request = Request(f"{_CATALOG_BASE}/{catalog_path}", headers={"User-Agent": _USER_AGENT})
    from core import package_sources
    try:
        return package_sources.read_url("mesonet", request, _urlopen_with_retry, timeout=60)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
