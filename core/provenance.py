"""Which source files this session actually loaded, for case packages
(core/case_package.py): URL, SHA-256 of the bytes received, size, and when.
The hash lets someone reopening a case tell whether the upstream file has
since changed -- THREDDS collections are reorganized and reprocessed often.

Fetchers call record() after a successful download; MainWindow resets the
record when a session starts. Thread-safe (fetchers run on worker threads).
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_records: dict[str, dict] = {}
# Small files are kept as loaded so a case package holds exactly what was
# seen; larger ones (radar volumes, big CSVs) are re-downloaded at export and
# checked against the recorded hash rather than held in memory.
KEEP_BYTES_UP_TO = 2 * 1024 * 1024
_kept: dict[str, bytes] = {}


def record(kind: str, url: str, data: bytes | None = None, *, sha256: str | None = None,
           size: int | None = None, source: str = "network") -> None:
    """Note a loaded file. Pass the bytes, or a hash already computed."""
    if data is not None:
        sha256 = hashlib.sha256(data).hexdigest()
        size = len(data)
    entry = {"kind": kind, "url": url, "sha256": sha256, "bytes": size, "source": source,
             "loaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with _lock:
        _records.setdefault(url, entry)
        if data is not None and len(data) <= KEEP_BYTES_UP_TO:
            _kept.setdefault(url, data)


def kept_bytes(url: str) -> bytes | None:
    """The bytes as loaded, for files small enough to keep."""
    with _lock:
        return _kept.get(url)


def snapshot() -> list[dict]:
    with _lock:
        return sorted(_records.values(), key=lambda e: (e["kind"], e["url"]))


def reset() -> None:
    with _lock:
        _records.clear()
        _kept.clear()
