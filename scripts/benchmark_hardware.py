"""Time STORM's heavy steps on this computer, on fixed inputs, so field
laptops can be compared with the development Mac.

    python scripts/benchmark_hardware.py

About 1-3 minutes; uses about 1 GB of memory. Downloads one radar volume from the
public NEXRAD archive on AWS once (KTLX 2013-05-31 23:23, 10.6 MB; not
THREDDS) and draws a generated dense lidar sweep, so no other data is
needed. Writes perf_logs/benchmark-<computer>-<time>.json and prints each
step next to the development Mac's time. Close STORM first.
"""
from __future__ import annotations

import gc
import json
import os
import platform
import socket
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_app = None
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from data import endpoints  # noqa: E402 - after sys.path

SCAN_URL = f"{endpoints.NEXRAD_LEVEL2_BUCKET}/2013/05/31/KTLX/KTLX20130531_232345_V06.gz"
SCAN_TIME = datetime(2013, 5, 31, 23, 23, 45, tzinfo=timezone.utc)

# The same steps on the development Mac (Apple M2, 8 GB, macOS 26), seconds,
# best of 3 (2 for the big renders), measured with this script 2026-10-15.
REFERENCE_M2 = {
    "import_radar_libraries": 3.17,
    "radar_decode_reflectivity": 0.59,
    "radar_decode_velocity": 0.59,
    "radar_dealias_velocity": 1.03,
    "radar_render_768_full": 0.10,
    "radar_render_2048_cropped": 0.50,
    "radar_render_3072_cropped": 1.12,
    "radar_render_4096_cropped": 1.98,
    "lidar_render_dense_768": 3.06,
}


def best_of(fn, repeats=3):
    times = []
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return min(times), statistics.median(times)


def dense_lidar_sweep():
    """3852 rays x 400 gates (30 m) at 0.5 deg elevation over 360 deg --
    the size of the dense sweep measured in planning/performance-on-older-hardware.md."""
    import numpy as np
    from archive.fetchers.raw_lidar_archive_fetcher import RawLidarRays, RawLidarSource
    n, g = 3852, 400
    rng = np.random.default_rng(7)
    times = SCAN_TIME.timestamp() - (n - 1 - np.arange(n)) * 0.02
    source = RawLidarSource(platform_id="BENCH-PPI", instrument="BENCH", platform_dir="bench",
                            datastream="bench.b1", product="ppi", mobile=False)
    rays = RawLidarRays(
        source=source, time_epoch=times, distance_m=15.0 + 30.0 * np.arange(g),
        azimuth_deg=np.linspace(0, 359.9, n), elevation_deg=np.full(n, 0.5),
        latitude=np.full(n, 35.0), longitude=np.full(n, -97.0), altitude_m=np.full(n, 350.0),
        heading_deg=np.full(n, np.nan), scan_number=np.ones(n),
        fields={"velocity": {"data": np.ma.masked_invalid(rng.normal(0, 8, (n, g)).astype(np.float32)), "units": "m/s"}},
        housekeeping={}, coordinate_source=np.full(n, "file site attributes"), position_time_epoch=times,
        provenance={"azimuth_metadata": {"comment": "0 degrees is north"}}, warnings=[])
    return rays, np.arange(n)


def main() -> int:
    from core.mem_probe import peak_rss_mb, system_memory_mb
    from core.perf_log import _cpu_name, _storm_commit
    results, notes = {}, {}
    total_mb, available_mb = system_memory_mb()
    info = {"computer": socket.gethostname(), "cpu": _cpu_name(), "logical_cpus": os.cpu_count(),
            "ram_total_mb": round(total_mb), "ram_available_mb": round(available_mb), "os": platform.platform(),
            "python": platform.python_version(), "storm": _storm_commit(ROOT),
            "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print(f"{info['computer']}: {info['cpu']}, {info['logical_cpus']} cores, {info['ram_total_mb']} MB RAM")

    cache = ROOT / "perf_logs" / "bench_cache"
    cache.mkdir(parents=True, exist_ok=True)
    scan_file = cache / SCAN_URL.rsplit("/", 1)[-1]
    if not scan_file.exists():
        import requests
        print("Downloading the test radar volume (10.6 MB, AWS)…", flush=True)
        started = time.perf_counter()
        response = requests.get(SCAN_URL, timeout=120)
        response.raise_for_status()
        scan_file.write_bytes(response.content)
        seconds = time.perf_counter() - started
        notes["download_mbit_s"] = round(len(response.content) * 8 / 1e6 / seconds, 1)
        results["download_radar_volume"] = (round(seconds, 2), round(seconds, 2))
    raw = scan_file.read_bytes()

    print("Radar…", flush=True)
    started = time.perf_counter()
    import importlib
    from PyQt6.QtCore import QCoreApplication
    global _app                                   # the fetcher is a QObject: keep an application alive
    _app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    importlib.import_module("metpy.io")
    from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher, VelocityOptions
    from ui.map import radar_overlay as ro
    results["import_radar_libraries"] = (round(time.perf_counter() - started, 2),) * 2
    fetcher = ArchiveRadarFetcher("KTLX", SCAN_TIME)
    fetcher._decode(SCAN_TIME, raw, product="reflectivity", tilt_idx=0)          # warm up
    os.environ.setdefault("PYART_QUIET", "1")
    importlib.import_module("pyart")              # imported once, not timed with dealiasing
    for name, kwargs in (("radar_decode_reflectivity", dict(product="reflectivity")),
                         ("radar_decode_velocity", dict(product="velocity")),
                         ("radar_dealias_velocity", dict(product="velocity", velocity=VelocityOptions(dealias=True)))):
        results[name] = best_of(lambda: fetcher._decode(SCAN_TIME, raw, tilt_idx=0, **kwargs))
    scan = fetcher._decode(SCAN_TIME, raw, product="reflectivity", tilt_idx=0)
    ro.render_scan_to_png(scan, grid_size=256)
    results["radar_render_768_full"] = best_of(lambda: ro.render_scan_to_png(scan, grid_size=768))
    for size in (2048, 3072, 4096):
        results[f"radar_render_{size}_cropped"] = best_of(
            lambda: ro.render_scan_to_png(scan, grid_size=size, crop_radius_m=ro.ARCHIVE_SUPERRES_CROP_RADIUS_M), 2)

    print("Lidar…", flush=True)
    from ui.map.lidar_overlay import render_lidar_to_png
    rays, indices = dense_lidar_sweep()
    results["lidar_render_dense_768"] = best_of(
        lambda: render_lidar_to_png(rays, SCAN_TIME + timedelta(seconds=1), "velocity", 768, ray_indices=indices), 2)
    notes["peak_memory_mb"] = round(peak_rss_mb())

    print()
    print(f"{'Step':32s} {'this computer':>14s} {'dev Mac (M2)':>13s} {'ratio':>7s}")
    for name, (best, _median) in results.items():
        ref = REFERENCE_M2.get(name)
        ratio = f"{best / ref:5.1f}×" if ref else "   —"
        print(f"{name:32s} {best:12.2f} s {(f'{ref:.2f} s' if ref else '—'):>13s} {ratio:>7s}")
    for key, value in notes.items():
        print(f"{key}: {value}")

    out = ROOT / "perf_logs" / f"benchmark-{''.join(c for c in info['computer'] if c.isalnum() or c in '-_')[:32]}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"info": info, "seconds_best_median": results, "notes": notes,
                               "reference_m2_best": REFERENCE_M2}, indent=1))
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
