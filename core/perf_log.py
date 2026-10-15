"""Opt-in performance log for testing STORM on field laptops.

Off unless STORM is started with --perf-log (or STORM_PERF_LOG=1). Then each
session writes perf_logs/perf-<computer>-<local time>.jsonl next to main.py:
one JSON object per line, flushed as written, so a crash still leaves a
usable log. scripts/collect_perf_logs.py zips the logs for upload and
scripts/summarize_perf_log.py turns one into a readable report.

Lines (every one has "t", seconds since the log started, and "kind"):
  system      the computer, OS, Python/Qt/library versions, STORM commit
  screen      size and scaling of the main screen
  gpu         the map's WebGL renderer (what the web view draws with)
  sample      every SAMPLE_S: memory (process, peak, system free), CPU use,
              threads, and the longest UI freeze since the last sample
  ui_stall    the window didn't respond for at least STALL_MS
  <step>      a timed step (radar.decode, radar.render, lidar.render, ...)
              with "ms" and what was done
  note        anything else worth knowing (e.g. the radar render stepping
              down a size)

Nothing here may raise into STORM: logging failures are swallowed.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SAMPLE_S = 5.0
STALL_MS = 300.0

_lock = threading.Lock()
_stream = None
_path: Path | None = None
_t0 = time.perf_counter()
_ui_lag_max_ms = 0.0
_ui_stalls = 0


def enabled() -> bool:
    return _stream is not None


def path() -> Path | None:
    return _path


def _write(record: dict) -> None:
    if _stream is None:
        return
    try:
        record = {"t": round(time.perf_counter() - _t0, 3), **record}
        line = json.dumps(record, default=str, separators=(",", ":"))
        with _lock:
            _stream.write(line + "\n")
            _stream.flush()
    except Exception:  # noqa: BLE001 - never break STORM for a log line
        pass


def event(kind: str, **fields) -> None:
    """One line: something that happened, with whatever describes it."""
    if _stream is not None:
        _write({"kind": kind, "thread": threading.current_thread().name, **fields})


@contextmanager
def timed(kind: str, **fields):
    """Time a step: `with timed("lidar.render", rays=n): ...`. The block can
    add fields to the dict it gets back; failures are logged as ok=False."""
    if _stream is None:
        yield fields
        return
    started = time.perf_counter()
    ok = True
    try:
        yield fields
    except BaseException:
        ok = False
        raise
    finally:
        event(kind, ms=round((time.perf_counter() - started) * 1000.0, 1), ok=ok, **fields)


def note_ui_lag(lag_ms: float) -> None:
    """From the UI heartbeat: how late the main thread ran a timer."""
    global _ui_lag_max_ms, _ui_stalls
    if _stream is None:
        return
    _ui_lag_max_ms = max(_ui_lag_max_ms, lag_ms)
    if lag_ms >= STALL_MS:
        _ui_stalls += 1
        event("ui_stall", ms=round(lag_ms))


# ---- the computer --------------------------------------------------------------

def _cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        if sys.platform == "darwin":
            import subprocess
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        with open("/proc/cpuinfo") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or "unknown"


def _storm_commit(root: Path) -> str:
    """The checked-out commit, read from .git without needing git installed."""
    try:
        head = (root / ".git" / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head[:12]
        ref = head.split(" ", 1)[1]
        ref_file = root / ".git" / ref
        if ref_file.exists():
            return f"{ref.rsplit('/', 1)[-1]}@{ref_file.read_text().strip()[:12]}"
        for line in (root / ".git" / "packed-refs").read_text().splitlines():
            if line.endswith(ref):
                return f"{ref.rsplit('/', 1)[-1]}@{line[:12]}"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _versions() -> dict:
    found = {}
    for name in ("numpy", "PyQt6.QtCore", "matplotlib", "metpy", "pyart", "xarray", "pyproj", "PIL"):
        try:
            module = __import__(name, fromlist=["_"])
            found[name] = getattr(module, "__version__", None) or getattr(module, "PYQT_VERSION_STR", None) \
                or getattr(module, "QT_VERSION_STR", None)
        except Exception:  # noqa: BLE001
            continue
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        found["Qt"], found["PyQt6"] = QT_VERSION_STR, PYQT_VERSION_STR
        found.pop("PyQt6.QtCore", None)
    except Exception:  # noqa: BLE001
        pass
    return found


def _system_record(root: Path, argv) -> dict:
    from core.mem_probe import system_memory_mb
    total_mb, available_mb = system_memory_mb()
    return {
        "kind": "system",
        "computer": socket.gethostname(),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu_name(),
        "logical_cpus": os.cpu_count(),
        "ram_total_mb": round(total_mb),
        "ram_available_mb": round(available_mb),
        "python": platform.python_version(),
        "storm": _storm_commit(root),
        "argv": list(argv),
        "versions": _versions(),
    }


# ---- sampling ----------------------------------------------------------------------

def _sampler() -> None:
    global _ui_lag_max_ms, _ui_stalls
    from core.mem_probe import current_rss_mb, peak_rss_mb, system_memory_mb
    last_wall, last_cpu = time.perf_counter(), _cpu_seconds()
    while _stream is not None:
        time.sleep(SAMPLE_S)
        wall, cpu = time.perf_counter(), _cpu_seconds()
        _, available = system_memory_mb()
        lag, stalls = _ui_lag_max_ms, _ui_stalls
        _ui_lag_max_ms, _ui_stalls = 0.0, 0
        event("sample",
              rss_mb=round(current_rss_mb()), peak_mb=round(peak_rss_mb()),
              system_available_mb=round(available),
              # % of one core; 100 = one core flat out
              cpu_pct=round(100.0 * (cpu - last_cpu) / max(wall - last_wall, 1e-6)),
              threads=threading.active_count(),
              ui_lag_max_ms=round(lag), ui_stalls=stalls)
        last_wall, last_cpu = wall, cpu


def _cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system


# ---- start / stop -------------------------------------------------------------------

def requested(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    return "--perf-log" in argv or os.environ.get("STORM_PERF_LOG", "").strip() not in ("", "0")


def start(root: Path | None = None, argv=None) -> Path | None:
    """Open this session's log and start sampling. Returns the file, or None."""
    global _stream, _path, _t0
    if _stream is not None:
        return _path
    root = Path(root or Path(__file__).resolve().parents[1])
    try:
        folder = root / "perf_logs"
        folder.mkdir(exist_ok=True)
        host = "".join(c for c in socket.gethostname() if c.isalnum() or c in "-_")[:32] or "computer"
        _path = folder / f"perf-{host}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        _stream = open(_path, "a", encoding="utf-8", buffering=1)
        _t0 = time.perf_counter()
    except Exception:  # noqa: BLE001
        _stream, _path = None, None
        return None
    _write(_system_record(root, argv if argv is not None else sys.argv))
    threading.Thread(target=_sampler, name="perf-log-sampler", daemon=True).start()
    return _path


def stop() -> None:
    global _stream
    stream, _stream = _stream, None
    if stream is not None:
        try:
            stream.write(json.dumps({"t": round(time.perf_counter() - _t0, 3), "kind": "end"}) + "\n")
            stream.close()
        except Exception:  # noqa: BLE001
            pass
