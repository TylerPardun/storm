"""Lightweight, dependency-free process memory probe.

Added to diagnose the ~8 GB archive-mode memory growth reported on an 8 GB
machine (see radar_overlay.py's ARCHIVE_SUPERRES_GRID_SIZE and the NOXP
volume loader -- the two suspected sources). Stdlib-only (no psutil) and
never shells out to a subprocess, so it stays safe to call even while the
system is already under memory pressure.

Works on macOS, Linux and Windows: the `resource` module doesn't exist on
Windows, which uses the process and system memory counters through ctypes.
"""
import logging
import platform

log = logging.getLogger("storm.memprobe")

_SYSTEM = platform.system()
_IS_MACOS = _SYSTEM == "Darwin"
_IS_WINDOWS = _SYSTEM == "Windows"
_MB = 1024.0 * 1024.0


if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    # declared signatures: a HANDLE is pointer-sized on 64-bit Windows, and
    # ctypes would otherwise pass/return it as a 32-bit int
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]

    def _process_counters():
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return counters
else:
    import resource


def peak_rss_mb() -> float:
    """Process peak resident-set size (high-water mark since process start), in MB.

    A single cheap call, safe on every render even mid-crisis. macOS reports
    ru_maxrss in bytes, Linux in KB; Windows reports the peak working set.
    """
    try:
        if _IS_WINDOWS:
            return _process_counters().PeakWorkingSetSize / _MB
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return raw / _MB if _IS_MACOS else raw / 1024.0
    except Exception:
        return -1.0


def current_rss_mb() -> float:
    """Memory the process is using now, in MB (Windows working set, Linux
    resident set); on macOS, where the stdlib has no current figure, the peak."""
    try:
        if _IS_WINDOWS:
            return _process_counters().WorkingSetSize / _MB
        if _SYSTEM == "Linux":
            with open("/proc/self/statm") as statm:
                return int(statm.read().split()[1]) * resource.getpagesize() / _MB
        return peak_rss_mb()
    except Exception:
        return -1.0


def system_memory_mb() -> tuple[float, float]:
    """(total, available) physical memory in MB, or (-1, -1) if unknown."""
    try:
        if _IS_WINDOWS:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / _MB, status.ullAvailPhys / _MB
            return -1.0, -1.0
        if _SYSTEM == "Linux":
            info = {}
            with open("/proc/meminfo") as meminfo:
                for line in meminfo:
                    key, value = line.split(":", 1)
                    info[key] = int(value.split()[0]) / 1024.0
            return info.get("MemTotal", -1.0), info.get("MemAvailable", -1.0)
        import os
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / _MB
        return total, -1.0
    except Exception:
        return -1.0, -1.0


def log_delta(label: str, rss_before: float, rss_after: float, elapsed_ms: float) -> None:
    """Log how much a single operation pushed the process's peak RSS.

    A positive delta means the operation touched new pages the allocator
    never gave back to the OS -- that's the actual growth signal we're
    hunting for, not just "how much RAM is in use right now."
    """
    if rss_before < 0 or rss_after < 0:
        log.info("[memprobe] %s: rss unavailable in %.0f ms", label, elapsed_ms)
        return
    delta = rss_after - rss_before
    log.info(
        "[memprobe] %s: peak RSS %.0f -> %.0f MB (delta %+.0f) in %.0f ms",
        label, rss_before, rss_after, delta, elapsed_ms,
    )
