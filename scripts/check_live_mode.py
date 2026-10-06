"""Open STORM in a live mode and report what each live source delivers.

    python scripts/check_live_mode.py viewer            # read-only: connects MQTT
    python scripts/check_live_mode.py monitor           # MQTT off (it can publish)
    python scripts/check_live_mode.py vehicle           # MQTT off (it publishes a position)

Counts what arrives in the first few minutes -- radar (NEXRAD Level 3, after
RADAR > show data), SPC and NWS hazards (after turning them on in HAZARDS),
satellite (after picking CONUS), surface observations, MQTT -- with screenshots and every warning STORM logged, and
writes case_data/live_checks/<mode>-<time>/report.json. Monitor and vehicle
modes run with MQTT off so a check never shows up on the field team's live
map. A memory watchdog stops it above --mem-limit-mb (the Mac has 8 GB).
Don't run it while STORM is open.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("viewer", "monitor", "vehicle"))
    ap.add_argument("--seconds", type=int, default=150, help="how long to watch the live feeds")
    ap.add_argument("--mem-limit-mb", type=float, default=3000)
    a = ap.parse_args()

    from check_archive_days import storm_running, tree_rss_mb
    if storm_running():
        print("STORM is open: close it first.")
        return 2
    out = ROOT.parent / "case_data" / "live_checks" / f"{a.mode}-{datetime.now():%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    report = {"mode": a.mode, "started": datetime.now(timezone.utc).isoformat(), "counts": {}, "notes": {},
              "logs": [], "problems": []}
    t0 = time.monotonic()

    def save():
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str))

    class Collect(logging.Handler):
        def emit(self, r):
            if len(report["logs"]) < 1000:
                report["logs"].append({"level": r.levelname, "logger": r.name, "msg": r.getMessage()[:300],
                                       "at_s": round(time.monotonic() - t0, 1)})
    logging.getLogger().addHandler(Collect(level=logging.WARNING))
    logging.getLogger().setLevel(logging.INFO)

    peak = [0.0]

    def watchdog():
        while True:
            mb = tree_rss_mb(os.getpid())
            peak[0] = max(peak[0], mb)
            if mb > a.mem_limit_mb:
                report["problems"].append(f"memory passed {a.mem_limit_mb:.0f} MB; stopped")
                save()
                os._exit(3)
            time.sleep(1)
    threading.Thread(target=watchdog, daemon=True).start()

    import runtime_flags
    if a.mode != "viewer":
        runtime_flags.FLAGS.enable_startup_toggles = True
        runtime_flags.FLAGS.disable_mqtt = True              # never publish from a check
    from verify_offline_case import _app, _pump
    app, mw = _app(Path(tempfile.mkdtemp(prefix="storm_livecheck_")))
    import config
    import main as storm_main
    config.VEHICLE_ID = storm_main._normalize_vehicle_id(config.VEHICLE_ID)   # as main.py does: this machine's ID
    report["notes"]["client_id"] = config.VEHICLE_ID
    w = mw.MainWindow(monitor=a.mode == "monitor", viewer=a.mode == "viewer")
    w.resize(1400, 900)
    w.move(40, 40)
    w.show()

    counts = report["counts"]

    def counter(name):
        counts.setdefault(name, 0)
        return lambda *_a: counts.__setitem__(name, counts[name] + 1)

    errors = []

    def error(source):
        return lambda msg: errors.append(f"{source}: {str(msg)[:160]}")
    # live mode builds its fetchers once the map is ready
    _pump(app, 90, lambda: all(hasattr(w, x) for x in ("_radar_fetcher", "_hazard_fetcher", "_satellite_fetcher")))
    report["notes"]["map_ready_s"] = round(time.monotonic() - t0, 1)
    for attr, signals in (("_radar_fetcher", ("new_data",)),
                          ("_hazard_fetcher", ("spc_received", "nws_received", "spc_watches_received", "spc_mds_received")),
                          ("_satellite_fetcher", ("frames_updated",)),
                          ("_surface_fetcher", ("observations_updated",)),
                          ("_mqtt_client", ("connected", "message_received"))):
        obj = getattr(w, attr, None)
        if obj is None:
            report["notes"][attr] = "not created in this mode"
            continue
        for sig in signals:
            getattr(obj, sig).connect(counter(f"{attr.strip('_')}.{sig}"))
        for err in ("fetch_error", "error"):
            if hasattr(obj, err):
                getattr(obj, err).connect(error(attr.strip("_")))

    def shot(name):
        _pump(app, 0.5)
        w.grab().scaledToWidth(1000).save(str(out / f"{name}.png"))

    if hasattr(w, "radar_controls"):
        w.radar_controls._chk_show_data.setChecked(True)          # RADAR > show data, as a user would
    _pump(app, 90, lambda: counts.get("radar_fetcher.new_data", 0) > 0)
    report["notes"]["radar_first_s"] = round(time.monotonic() - t0, 1)
    shot("01_start")

    # hazards: open HAZARDS and turn on the outlook, watches and NWS warnings
    try:
        if hasattr(w, "btn_hazards"):
            w.btn_hazards.setChecked(True)
            hc = w.hazard_controls
            for b in (hc._btn_outlook, hc._btn_watches, hc._btn_nws_warnings):
                b.setChecked(True)
            ok = _pump(app, 60, lambda: counts.get("hazard_fetcher.spc_received", 0) > 0
                       and counts.get("hazard_fetcher.nws_received", 0) > 0)
            report["notes"]["hazards"] = "outlook and warnings arrived" if ok else "not both in 60 s"
            shot("02_hazards")
            w.btn_hazards.setChecked(False)
    except Exception as exc:                                          # noqa: BLE001
        report["problems"].append(f"hazards: {exc!r}")

    # satellite: open SATELLITE and pick CONUS, as a user would
    try:
        if hasattr(w, "btn_satellite"):
            w.btn_satellite.setChecked(True)
            w.satellite_controls._btn_conus.setChecked(True)
            ok = _pump(app, 90, lambda: counts.get("satellite_fetcher.frames_updated", 0) > 0)
            report["notes"]["satellite"] = "frames arrived" if ok else "no frames in 90 s"
            shot("03_satellite")
    except Exception as exc:                                          # noqa: BLE001
        report["problems"].append(f"satellite: {exc!r}")

    _pump(app, max(0, a.seconds - (time.monotonic() - t0)))
    report["notes"]["radar_site"] = w.radar_controls.current_site() if hasattr(w, "radar_controls") else None
    report["notes"]["status_line"] = w.status_msg_label.text()
    report["notes"]["vehicles"] = w.vehicle_count_label.text()
    report["notes"]["network"] = w.net_indicator.text()
    shot("04_end")

    expected = ["radar_fetcher.new_data", "hazard_fetcher.spc_received", "hazard_fetcher.nws_received"]
    if a.mode == "viewer":
        expected.append("mqtt_client.connected")
    for name in expected:
        if not counts.get(name):
            report["problems"].append(f"nothing from {name} in {a.seconds} s")
    report["errors"] = sorted(set(errors))
    report["peak_mb"] = round(peak[0])
    report["wall_s"] = round(time.monotonic() - t0)
    save()
    print(json.dumps({k: report[k] for k in ("mode", "counts", "notes", "problems", "errors", "peak_mb")}, indent=1))
    print(f"\nWarnings logged: {len(report['logs'])}  ->  {out / 'report.json'}", flush=True)
    sys.stdout.flush()
    w.close()
    _pump(app, 1)
    os._exit(0 if not report["problems"] else 1)


if __name__ == "__main__":
    sys.exit(main())
