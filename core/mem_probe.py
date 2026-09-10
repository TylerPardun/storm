"""Lightweight, dependency-free process memory probe.

Added to diagnose the ~8 GB archive-mode memory growth reported on an 8 GB
machine (see radar_overlay.py's ARCHIVE_SUPERRES_GRID_SIZE and the NOXP
volume loader -- the two suspected sources). Stdlib-only (no psutil) and
never shells out to a subprocess, so it stays safe to call even while the
system is already under memory pressure.
"""
import logging
import platform
import resource

log = logging.getLogger("storm.memprobe")

_IS_MACOS = platform.system() == "Darwin"


def peak_rss_mb() -> float:
    """Process peak resident-set size (high-water mark since process start), in MB.

    getrusage never touches the filesystem or spawns anything -- it's a
    single cheap syscall, safe to call on every render even mid-crisis.
    macOS reports ru_maxrss in bytes; Linux reports it in KB.
    """
    try:
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return raw / (1024.0 * 1024.0) if _IS_MACOS else raw / 1024.0
    except Exception:
        return -1.0


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
