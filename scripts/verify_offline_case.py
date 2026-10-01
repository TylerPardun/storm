"""Check that a case package opens with no network: build one from a live
archive session, then reopen it in a fresh process whose Python network
access is blocked (only loopback is allowed), and report what loaded.

Needs a display (windows are parked off-screen) and, for the build step,
network access. The map's web view (QtWebEngine) is not covered by the
block; everything STORM's Python fetchers download is.

    python scripts/verify_offline_case.py                      # build, then open offline
    python scripts/verify_offline_case.py --open PACKAGE.zip   # open an existing package offline
"""
import argparse
import faulthandler
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
UTC = timezone.utc


def _block_network(blocked: list) -> None:
    """Refuse every non-loopback connection made from Python."""
    import socket
    loopback = {"127.0.0.1", "::1", "localhost"}
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host not in loopback and host is not None:
            blocked.append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, f"offline check: {host} blocked")
        return real_getaddrinfo(host, *args, **kwargs)

    real_connect = socket.socket.connect

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in loopback and not host.startswith("127."):
            blocked.append(host)
            raise OSError(f"offline check: {host} blocked")
        return real_connect(sock, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect


def _app(scratch: Path):
    from PyQt6.QtWidgets import QApplication
    from PyQt6 import QtWebEngineWidgets  # noqa: F401 -- must precede QApplication
    import main as storm_main
    storm_main._register_storm_scheme()
    import net_compat
    net_compat.prefer_ipv4()
    app = QApplication(sys.argv)
    import core.workspace as workspace          # keep test tracks out of ~/STORM
    workspace.workspaces_root = lambda: scratch / "workspaces"
    from ui.app import main_window as mw
    from PyQt6.QtCore import QSettings

    class ScratchSettings(QSettings):            # don't touch the user's saved layout
        def __init__(self, *_args):
            super().__init__(str(scratch / "settings.ini"), QSettings.Format.IniFormat)
    mw.QSettings = ScratchSettings
    import ui.widgets.layer_order_pill as layer_order_pill
    layer_order_pill.QSettings = ScratchSettings
    mw.MainWindow._notify_saved = lambda *a, **k: None        # no modal "opened" box
    return app, mw


def _pump(app, seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
        if until is not None and until():
            return True
    return False


def _status(window) -> dict:
    c = window._archive_controls
    return {"radar": c._radar_status.text(), "obs": c._obs_status.text(),
            "radar_time": c._radar_time_label.text(),
            "clock": window._time_ctrl.current_time.strftime("%Y-%m-%d %H:%M:%SZ")}


def build(out: Path, when: datetime, station: str, frame_minutes: int) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="storm_offline_build_"))
    app, mw = _app(scratch)
    from core import case_package, provenance
    window = mw.MainWindow(archive_time=when)
    window.resize(1200, 800)
    window.move(-3000, 0)
    window.show()
    rendered = lambda: hasattr(window, "_archive_controls") and window._archive_controls._radar_status.text().startswith(("Radar: N0", "Radar: REF", "Radar: DBZ"))
    _pump(app, 30)
    if getattr(window, "_archive_radar", None) is None or window._archive_radar.station != station:
        window._on_radar_station_clicked(station)
    _pump(app, 240, rendered)
    obs_ready = lambda: window._archive_controls._obs_status.text().startswith("OBS: catalog") \
        or window._archive_controls._obs_status.text().startswith("OBS: 1-second")
    _pump(app, 240, obs_ready)
    # play the frame through so every scan in it is loaded
    for minutes in range(0, frame_minutes + 1, 10):
        window._time_ctrl.set_time(when + timedelta(minutes=minutes))
        _pump(app, 12)
    print("live session:", json.dumps(_status(window)), flush=True)
    sources = provenance.snapshot()
    frame = (when - timedelta(minutes=5), when + timedelta(minutes=frame_minutes))
    case = window._case_settings()
    case["clock_time"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    # as the export dialog does by default: add scans the catalogs list for the frame
    listed = case_package.listed_files(sources, provenance.kept_bytes)
    keep = case_package.in_time_frame(sources + listed, frame)
    added = [e for e in listed if e["url"] in keep]
    print(f"listed but not loaded, added for the frame: {len(added)} "
          f"({sum(e.get('bytes') or 0 for e in added) / 1e6:.0f} MB)", flush=True)
    sources = sources + added
    kinds = {s["kind"] for s in sources}
    case_package.build(out, case=case, tracks=[], sources=sources, include_kinds=kinds,
                       time_frame=frame, fetch=mw._package_fetch)
    manifest, _ = case_package.read(out)
    print(f"built {out} ({out.stat().st_size / 1e6:.1f} MB): {len(manifest['data'])} files packed, "
          f"{len(manifest['data_not_packed'])} not packed, kinds {sorted(kinds)}", flush=True)
    window.close()
    _pump(app, 2)
    os._exit(0)


def open_offline(package: Path, wait: int) -> int:
    blocked: list = []
    _block_network(blocked)
    warnings: list = []

    class Collect(logging.Handler):
        def emit(self, record):
            warnings.append(f"{record.levelname} {record.name}: {record.getMessage()[:200]}")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger().addHandler(Collect(level=logging.WARNING))

    scratch = Path(tempfile.mkdtemp(prefix="storm_offline_open_"))
    app, mw = _app(scratch)
    from core import case_package, package_sources
    manifest, _ = case_package.read(package)
    case = manifest["case"]
    when = datetime.fromisoformat(case["archive_time"].replace("Z", "+00:00"))
    window = mw.MainWindow(archive_time=when, case_package=str(package))
    window.resize(1200, 800)
    window.move(-3000, 0)
    window.show()
    ready = lambda: hasattr(window, "_archive_controls")      # built once the map is ready
    rendered = lambda: ready() and window._archive_controls._radar_time_label.text() not in ("", "--:--Z")
    radar_ok = _pump(app, wait, rendered)
    obs_ok = ready() and _pump(app, 60, lambda: window._archive_controls._obs_status.text().startswith(("OBS: catalog", "OBS: 1-second")))
    first = _status(window) if ready() else {"error": "archive startup never ran (map not ready)"}
    # step through the packed time frame
    frame = manifest.get("time_frame")
    later = None
    if frame and ready():
        end = datetime.fromisoformat(frame["end"].replace("Z", "+00:00"))
        target = end - timedelta(minutes=10)
        before = window._archive_controls._radar_time_label.text()
        window._time_ctrl.set_time(target)
        _pump(app, 60, lambda: window._archive_controls._radar_time_label.text() != before)
        later = _status(window)
    served = dict(package_sources.served())
    packed = {k: sum(1 for d in manifest["data"] if d["kind"] == k) for k in {d["kind"] for d in manifest["data"]}}
    report = {
        "package": package.name,
        "radar_rendered": radar_ok, "obs_loaded": obs_ok,
        "status_at_open": first, "status_after_seek": later,
        "packed_files_by_kind": packed, "served_from_package_by_kind": served,
        "blocked_hosts": sorted(set(blocked)), "blocked_attempts": len(blocked),
        "warnings": warnings[:40], "warning_count": len(warnings),
    }
    print(json.dumps(report, indent=2), flush=True)
    window.close()
    _pump(app, 2)
    os._exit(0 if radar_ok else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--open", type=Path, help="open this package offline (skip the build)")
    parser.add_argument("--build-only", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--when", default="2024-04-27T20:30:00Z")
    parser.add_argument("--station", default="KFDR")
    parser.add_argument("--minutes", type=int, default=40, help="length of the packed time frame")
    parser.add_argument("--wait", type=int, default=180)
    args = parser.parse_args()
    faulthandler.enable(all_threads=True)
    when = datetime.fromisoformat(args.when.replace("Z", "+00:00"))
    if args.build_only:
        build(args.build_only, when, args.station, args.minutes)
    if args.open:
        return open_offline(args.open, args.wait)
    package = Path(tempfile.mkdtemp(prefix="storm_offline_")) / f"offline_check_{when:%Y%m%d_%H%M}.zip"
    py = sys.executable
    step = [py, __file__, "--build-only", str(package), "--when", args.when,
            "--station", args.station, "--minutes", str(args.minutes)]
    if subprocess.run(step).returncode != 0 or not package.exists():
        print("build failed")
        return 1
    return subprocess.run([py, __file__, "--open", str(package), "--wait", str(args.wait)]).returncode


if __name__ == "__main__":
    sys.exit(main())
