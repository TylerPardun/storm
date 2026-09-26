"""Rapidly open, use, and close archive-mode MainWindows in one process, to
catch native crashes in startup/teardown (e.g. a background worker or timer
calling into a window that has already been deleted).

Needs a display (it opens real windows, parked off-screen) and network
access. A crash prints a Python thread dump (faulthandler) and exits with a
non-zero status; a clean run ends with "STRESS DONE".

    python scripts/stress_archive_sessions.py --seed 1 --cycles 20
"""
import argparse
import faulthandler
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

DATES = [datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc),
         datetime(2025, 6, 6, 20, 0, tzinfo=timezone.utc),
         datetime(2013, 5, 31, 23, 0, tzinfo=timezone.utc)]
LIFETIMES = [0.3, 1.5, 4, 8, 15]            # seconds; short ones close mid-startup
ACTIONS = ["none", "station", "product", "seek", "velocity"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cycles", type=int, default=12)
    args = parser.parse_args()
    faulthandler.enable(all_threads=True)

    import logging
    logging.basicConfig(level=logging.ERROR)
    from PyQt6.QtWidgets import QApplication
    from PyQt6 import QtWebEngineWidgets  # noqa: F401 -- must precede QApplication
    import main as storm_main
    storm_main._register_storm_scheme()
    import net_compat
    net_compat.prefer_ipv4()
    app = QApplication(sys.argv)
    import runtime_flags
    runtime_flags.FLAGS.admin_mode = True
    import core.workspace as workspace          # keep test tracks out of ~/STORM
    scratch = Path(tempfile.mkdtemp(prefix="storm_stress_"))
    workspace.workspaces_root = lambda: scratch / "workspaces"
    from ui.app import main_window as mw

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.005)

    rng = random.Random(args.seed)
    started = time.time()
    for cycle in range(args.cycles):
        when, life, action = rng.choice(DATES), rng.choice(LIFETIMES), rng.choice(ACTIONS)
        window = mw.MainWindow(archive_time=when)
        window.resize(1200, 800)
        window.move(-3000, 0)
        window.show()
        pump(life / 2)
        if action == "station" and getattr(window, "_archive_radar", None):
            window._on_radar_station_clicked(rng.choice(["KTLX", "KFDR", "KLBB", "KAMA"]))
        elif action in ("product", "velocity") and hasattr(window, "radar_controls"):
            window.radar_controls.set_current_product("velocity")
            if action == "velocity":
                window.radar_controls._chk_dealias.setChecked(True)
        elif action == "seek" and getattr(window, "_time_ctrl", None):
            window._time_ctrl.set_time(when.replace(hour=23))
        pump(life / 2)
        window.close()
        window.deleteLater()
        pump(0.2)
        print(f"cycle {cycle}: {when:%Y-%m-%d} lived {life}s, {action}, closed ok "
              f"(t={time.time() - started:.0f}s)", flush=True)
    print("STRESS DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
