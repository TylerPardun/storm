"""Open STORM archive sessions for many dates, use each like a user would,
and report what loaded, what didn't, and what went wrong.

    python scripts/check_archive_days.py 2024-04-27 2022-05-29 ...
    python scripts/check_archive_days.py --preset          # the representative set below
    python scripts/check_archive_days.py --report RUN_DIR  # rebuild a run's report

Each date runs in its own process (memory is fully released between
dates), one at a time:
  1. opens the archive session at 20Z, as the launch window would
  2. records what the day has and what loaded, and how long each took:
     radar (the station it picked and why), mobile mesonet, recorded
     vehicles, lidar, NOXP, satellite
  3. steps the clock through the hours the vehicles were driving and waits
     for each moment to finish drawing (the right radar scan, lidar sweep,
     trails, satellite), with a screenshot
  4. switches the radar to velocity and back, turns satellite on and off,
     shows a lidar if the day has one, and NOXP if it has that
  5. makes a short movie (Video Studio) at 720p
  6. collects every warning and error STORM logged
Results go to case_data/day_checks/<run>/ (beside the storm/ checkout,
outside git): one JSON per date, screenshots, and report.md.

Careful with the machine and THREDDS: a memory watchdog stops a date above
--mem-limit-mb (3 GB; the Mac has 8 GB) and peaks over 2.4 GB are flagged; dates run one at a time with a rest
between them (THREDDS stalls under bursts of requests); a run can be
stopped and started again -- finished dates are skipped (--redo to repeat).
Don't run it while STORM is open.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT.parent / "case_data" / "day_checks"
UTC = timezone.utc

# A spread over eras and data mixes (from the launch window's date index).
PRESET = [
    ("2009-06-05", "VORTEX2 Goshen County: 7 probes, the oldest radar era"),
    ("2010-05-10", "VORTEX2 Oklahoma outbreak: 7 probes"),
    ("2013-05-31", "El Reno: NOXP only"),
    ("2015-06-02", "MG1-3 and the NOXP scout"),
    ("2017-05-16", "CLAMPS lidar and surface met, 2 probes"),
    ("2019-05-17", "TORUS 2019: lidar truck, far-field, windsonde"),
    ("2020-08-25", "CLAMPS with TROPoe, tropical season"),
    ("2022-04-13", "PERiLS IOP4: every data type, CopterSonde"),
    ("2022-05-29", "TORUS 2022: hailcam, windsonde2, CSM lidar"),
    ("2024-04-27", "Plains outbreak: lidar truck and probes"),
    ("2026-04-26", "the latest season"),
]
START_HOUR = 20                  # the session opens at 20Z, a typical choice
SAMPLES = 6                      # moments checked through the active hours
FRAME_TIMEOUT_S = 90
REST_BETWEEN_DATES_S = 45        # let THREDDS breathe
DATE_TIMEOUT_S = 25 * 60


# =============================================================================================
# the parent: one child process per date, memory watchdog, report
# =============================================================================================
def tree_rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    rows = [tuple(int(x) for x in line.split()) for line in out.splitlines() if line.strip()]
    family, total, grew = {pid}, 0, True
    while grew:
        grew = False
        for p, pp, _ in rows:
            if pp in family and p not in family:
                family.add(p); grew = True
    return sum(r for p, _, r in rows if p in family) / 1024


def storm_running() -> bool:
    """A Python process running STORM's main.py (not a shell whose command
    line merely mentions it)."""
    import re
    out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
    storm = re.compile(r"^\S*python[\d.]*\s+(?:-\S+\s+)*\S*\bmain\.py(?:\s|$)")
    return any(storm.match(line.strip()) for line in out.splitlines())


def run_parent(dates: list[tuple[str, str]], out: Path, mem_limit: float, redo: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    if storm_running():
        print("STORM is open: close it first (two STORM sessions don't fit in 8 GB).")
        return 2
    for n, (day, why) in enumerate(dates):
        result = out / f"{day}.json"
        if result.exists() and not redo:
            print(f"[{day}] done before, skipped")
            continue
        print(f"\n[{day}] {why}", flush=True)
        log = open(out / f"{day}.log", "w")
        child = subprocess.Popen([sys.executable, __file__, "--child", day, "--out", str(out),
                                  "--mem-limit-mb", str(mem_limit)],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        t0, peak, stopped = time.time(), 0.0, ""
        while child.poll() is None:
            time.sleep(2)
            mb = tree_rss_mb(child.pid)
            peak = max(peak, mb)
            if mb > mem_limit:
                stopped = f"memory {mb:.0f} MB > {mem_limit:.0f} MB"
            elif time.time() - t0 > DATE_TIMEOUT_S:
                stopped = f"took longer than {DATE_TIMEOUT_S // 60} min"
            if stopped:
                child.kill()
                child.wait()
                break
        log.close()
        data = json.loads(result.read_text()) if result.exists() else {"date": day, "phases": {}}
        data.update(why=why, parent_peak_mb=round(peak), wall_s=round(time.time() - t0),
                    exit_code=child.returncode, stopped=stopped or data.get("stopped", ""))
        result.write_text(json.dumps(data, indent=1, default=str))
        print(f"[{day}] {verdict(data)[0]}  ({data['wall_s']} s, peak {data['parent_peak_mb']} MB)"
              + (f"  STOPPED: {stopped}" if stopped else ""), flush=True)
        if n + 1 < len(dates):
            time.sleep(REST_BETWEEN_DATES_S)
    write_report(out)
    print(f"\nReport: {out / 'report.md'}")
    return 0


# =============================================================================================
# the child: one archive session
# =============================================================================================
class Recorder:
    """Results for one date, written after every step so a crash still leaves them."""

    def __init__(self, out: Path, day: str):
        self.path = out / f"{day}.json"
        self.shots = out / "shots" / day
        self.shots.mkdir(parents=True, exist_ok=True)
        self.data = {"date": day, "started": datetime.now(UTC).isoformat(), "phases": {}, "frames": [],
                     "actions": {}, "logs": [], "problems": []}
        self.t0 = time.monotonic()
        self.save()

    def phase(self, name: str, ok, **info) -> None:
        self.data["phases"][name] = {"ok": ok, "at_s": round(time.monotonic() - self.t0, 1), **info}
        self.save()

    def problem(self, text: str) -> None:
        self.data["problems"].append(text)
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1, default=str))


def run_child(day: str, out: Path, mem_limit: float) -> int:
    import faulthandler
    import logging
    import tempfile
    import threading
    faulthandler.enable(all_threads=True)
    sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
    os.chdir(ROOT)
    rec = Recorder(out, day)

    class Collect(logging.Handler):
        def emit(self, r):
            if len(rec.data["logs"]) < 2000:
                rec.data["logs"].append({"level": r.levelname, "logger": r.name, "msg": r.getMessage()[:300],
                                         "at_s": round(time.monotonic() - rec.t0, 1)})
    logging.getLogger().addHandler(Collect(level=logging.WARNING))
    logging.getLogger().setLevel(logging.INFO)

    def own_rss_mb():
        return tree_rss_mb(os.getpid())

    def watchdog():                                   # leave cleanly before the parent has to kill us
        while True:
            if own_rss_mb() > mem_limit * 0.95:
                rec.data["stopped"] = "memory watchdog"
                rec.problem(f"memory passed {mem_limit * 0.95:.0f} MB; stopped")
                os._exit(3)
            time.sleep(1)
    threading.Thread(target=watchdog, daemon=True).start()

    from verify_offline_case import _app, _pump
    app, mw = _app(Path(tempfile.mkdtemp(prefix="storm_daycheck_")))
    when = datetime.strptime(day, "%Y-%m-%d").replace(hour=START_HOUR, tzinfo=UTC)
    w = mw.MainWindow(archive_time=when)
    w.resize(1400, 900); w.move(40, 40); w.show()

    def shot(name):
        _pump(app, 0.5)
        w.grab().scaledToWidth(1000).save(str(rec.shots / f"{name}.png"))

    def wait(seconds, until):
        t = time.monotonic()
        ok = _pump(app, seconds, until)
        return ok, round(time.monotonic() - t, 1)

    try:
        # ---- startup -------------------------------------------------------------
        ok, s = wait(120, lambda: hasattr(w, "_archive_controls"))
        rec.phase("map_ready", ok, seconds=s)
        if not ok:
            rec.problem("the archive session never started (map not ready in 2 min)")
            return 1
        ac = w._archive_controls
        ok, s = wait(180, lambda: ac._radar_time_label.text() != "Radar —")
        radar = getattr(w, "_archive_radar", None)
        rec.phase("radar_first_image", ok, seconds=s, station=getattr(radar, "station", None),
                  label=ac._radar_time_label.text(), status=ac._radar_status.text())
        if not ok:
            rec.problem("no radar image within 3 min of opening")
        ok, s = wait(240, lambda: getattr(w, "_archive_vehicle_obs_loaded", False)
                     or not getattr(w, "_archive_vehicle_obs_started", False))
        vo = getattr(w, "_archive_vehicle_obs", None)
        vehicles = sorted(getattr(vo, "_observations", {}) or {})
        rec.phase("mobile_mesonet", ok, seconds=s, vehicles=vehicles,
                  rows=sum(len(v) for v in (getattr(vo, "_observations", {}) or {}).values()),
                  status=ac._obs_status.text())
        ok, s = wait(60, lambda: getattr(getattr(w, "_archive_mqtt", None), "_loaded", False))
        rec.phase("recorded_vehicles", ok, seconds=s)
        window = w._time_ctrl.window
        rec.phase("session_window", True, start=window[0].isoformat(), end=window[1].isoformat())
        ok, s = wait(240, lambda: getattr(w, "_lidar_survey_done", False))
        rec.phase("lidar_survey", ok, seconds=s,
                  locations=[f"{l.instrument} {getattr(l, 'key', '')}" for l in getattr(w, "_lidar_locations", [])][:20])
        rec.phase("noxp", True, available=getattr(w, "_noxp_station_site", None) is not None)
        rec.phase("satellite", True, status=ac._sat_status.toolTip() or ac._sat_status.text())
        rec.data["rss_after_startup_mb"] = round(own_rss_mb())
        shot("00_startup")

        # ---- the active hours ------------------------------------------------------------
        times = active_span(vo, window)
        rec.data["active_span"] = [t.isoformat() for t in times[:1] + times[-1:]]
        for i, t in enumerate(times):
            w._time_ctrl.pause(); w._time_ctrl.set_time(t)
            ok, s = wait(FRAME_TIMEOUT_S, lambda: w.studio_frame_pending() is None)
            frame = {"time": t.strftime("%H:%M:%SZ"), "ok": ok, "seconds": s,
                     "pending": None if ok else w.studio_frame_pending(),
                     "radar": ac._radar_time_label.text(), "radar_status": ac._radar_status.text(),
                     "obs": ac._obs_status.text(), "vehicles_shown": w.vehicle_count_label.text(),
                     "rss_mb": round(own_rss_mb())}
            rec.data["frames"].append(frame)
            if not ok:
                rec.problem(f"{frame['time']}: still drawing after {FRAME_TIMEOUT_S} s ({frame['pending']})")
            shot(f"{i + 1:02d}_{t:%H%M}Z")
            rec.save()

        # ---- things a user does ---------------------------------------------------------------
        do_actions(w, app, rec, wait, shot)

        # ---- a short movie ----------------------------------------------------------------------
        try:
            studio_movie(w, app, rec, times, out)
        except Exception as exc:                                     # noqa: BLE001
            rec.problem(f"Video Studio: {exc!r}")
        rec.data["rss_end_mb"] = round(own_rss_mb())
        rec.data["finished"] = True
        rec.save()
        return 0
    except Exception:                                               # noqa: BLE001
        rec.problem("the check itself failed: " + traceback.format_exc()[-800:])
        return 1
    finally:
        rec.save()
        try:
            w.close(); _pump(app, 1)
        except Exception:                                           # noqa: BLE001
            pass
        os._exit(0 if rec.data.get("finished") else 1)


def active_span(vo, window) -> list[datetime]:
    """SAMPLES moments through the hours the vehicles were driving (first to
    last moving), or 20Z-01Z when the day has no vehicle tracks."""
    from archive.session import _MOVING_KM_PER_MINUTE, _MOVING_WINDOW_S, _km, last_moving_time
    firsts, lasts = [], []
    for obs in (getattr(vo, "_observations", {}) or {}).values():
        obs = list(obs)
        j = 0
        for i, o in enumerate(obs):
            while (obs[j].timestamp - o.timestamp).total_seconds() < -_MOVING_WINDOW_S:
                j += 1
            if j < i and _km(obs[j], o) > _MOVING_KM_PER_MINUTE:
                firsts.append(o.timestamp)
                break
        last = last_moving_time(obs)
        if last:
            lasts.append(last)
    start, end = window
    lo = max(start + timedelta(hours=12), min(firsts)) if firsts else start + timedelta(hours=20)
    hi = min(end - timedelta(minutes=1), max(lasts)) if lasts else start + timedelta(hours=25)
    if hi <= lo:
        hi = lo + timedelta(hours=2)
    step = (hi - lo) / (SAMPLES - 1)
    return [(lo + step * k).replace(microsecond=0) for k in range(SAMPLES)]


def do_actions(w, app, rec, wait, shot) -> None:
    ac = w._archive_controls
    acts = rec.data["actions"]
    # radar: velocity, then back to reflectivity
    try:
        if getattr(w, "_archive_radar", None) is not None:
            w.radar_controls.set_current_product("velocity")
            ok, s = wait(FRAME_TIMEOUT_S, lambda: w.studio_frame_pending() is None
                         and getattr(w._current_radar_scan, "pyart_field", "") == "velocity")
            acts["radar_velocity"] = {"ok": ok, "seconds": s, "label": ac._radar_time_label.text()}
            shot("50_velocity")
            if not ok:
                rec.problem(f"radar velocity didn't draw in {FRAME_TIMEOUT_S} s ({w.studio_frame_pending()})")
            w.radar_controls.set_current_product("reflectivity")
            ok, s = wait(FRAME_TIMEOUT_S, lambda: w.studio_frame_pending() is None)
            acts["radar_back_to_reflectivity"] = {"ok": ok, "seconds": s}
    except Exception as exc:                                         # noqa: BLE001
        rec.problem(f"radar product switch: {exc!r}")
    # satellite: on (it is only fetched while on), then off again
    try:
        if hasattr(w, "btn_satellite"):
            w.btn_satellite.setChecked(True)
            w.satellite_controls._btn_conus.setChecked(True)          # the user's CONUS click
            ok, s = wait(120, lambda: getattr(w, "_archive_sat_has_data", False) and w.studio_frame_pending() is None)
            acts["satellite"] = {"ok": ok, "seconds": s, "status": ac._sat_status.toolTip() or ac._sat_status.text()}
            shot("55_satellite")
            if not ok and "none" not in acts["satellite"]["status"]:
                rec.problem(f"satellite didn't appear in 120 s ({acts['satellite']['status']})")
            w.satellite_controls._btn_conus.setChecked(False)
            w.btn_satellite.setChecked(False)
    except Exception as exc:                                         # noqa: BLE001
        rec.problem(f"satellite: {exc!r}")
    # lidar: show the first location found
    try:
        locs = getattr(w, "_lidar_locations", [])
        if locs:
            loc = locs[0]
            w._time_ctrl.set_time(loc.scans[len(loc.scans) // 2].start)
            w._on_lidar_location_clicked(loc.key)
            ok, s = wait(FRAME_TIMEOUT_S, lambda: w.studio_frame_pending() is None
                         and getattr(w, "_lidar_overlay_key", None) is not None)
            acts["lidar"] = {"ok": ok, "seconds": s, "location": f"{loc.instrument} {loc.key}", "scans": len(loc.scans)}
            shot("60_lidar")
            if not ok:
                rec.problem(f"lidar {loc.instrument} didn't draw in {FRAME_TIMEOUT_S} s ({w.studio_frame_pending()})")
    except Exception as exc:                                         # noqa: BLE001
        rec.problem(f"lidar: {exc!r}")
    # NOXP
    try:
        if getattr(w, "_noxp_station_site", None) is not None:
            from ui.controls.radar_controls import NOXP_SITE_ID
            w._on_radar_station_clicked(NOXP_SITE_ID)
            ok, s = wait(150, lambda: "NOXP" in ac._radar_time_label.text())
            acts["noxp"] = {"ok": ok, "seconds": s, "label": ac._radar_time_label.text()}
            shot("70_noxp")
            if not ok:
                rec.problem("NOXP was offered but no NOXP image appeared in 150 s")
    except Exception as exc:                                         # noqa: BLE001
        rec.problem(f"NOXP: {exc!r}")
    rec.save()


def studio_movie(w, app, rec, times, out: Path) -> None:
    """Two keyframes 20 minutes of case apart, 720p, 10 fps, a minute's steps."""
    from verify_offline_case import _pump
    from core.studio import Keyframe, Project
    from ui.studio.exporter import StudioExporter
    view = {}
    w._toggle_studio()
    s = w._studio
    s._capture.read_view(lambda v: view.update(v=v))
    _pump(app, 5, lambda: "v" in view)
    t = times[len(times) // 2]
    p = Project(fps=10, resolution="720p (1280×720)", format="mp4", name="check")
    p.add(Keyframe(0.0, t, view["v"], radar=w.studio_radar_state()))
    p.append(t + timedelta(minutes=20), view["v"])
    res = {}
    movie = out / "shots" / rec.data["date"] / "movie.mp4"
    ex = StudioExporter(w, p, movie, w)
    ex.finished.connect(lambda ok, msg: res.update(ok=ok, msg=msg))
    t0 = time.monotonic()
    ex.start()
    _pump(app, 300, lambda: "ok" in res)
    rec.data["actions"]["studio_movie"] = {"ok": bool(res.get("ok")), "message": res.get("msg", "timed out"),
                                           "seconds": round(time.monotonic() - t0, 1), "frames": len(ex._frames),
                                           "slow_frames": ex.capture.slow_frames,
                                           "bytes": movie.stat().st_size if movie.exists() else 0}
    if not res.get("ok"):
        rec.problem(f"Video Studio export: {res.get('msg', 'timed out')}")
    w._toggle_studio()
    rec.save()


# =============================================================================================
# the report
# =============================================================================================
HIGH_MEMORY_MB = 2400          # flagged: worth bringing down on an 8 GB machine


def verdict(d: dict) -> tuple[str, str]:
    if d.get("stopped") or not d.get("finished"):
        return "RED", "didn't finish" + (f": {d['stopped']}" if d.get("stopped") else "")
    if d.get("problems"):
        return "YELLOW", f"{len(d['problems'])} problem(s)"
    if (d.get("parent_peak_mb") or 0) > HIGH_MEMORY_MB:
        return "YELLOW", f"high memory ({d['parent_peak_mb']} MB)"
    return "GREEN", "everything drew"


def write_report(out: Path) -> None:
    rows, all_logs = [], {}
    results = sorted(p for p in out.glob("*.json"))
    for path in results:
        d = json.loads(path.read_text())
        rows.append(d)
        for entry in d.get("logs", []):
            if entry["level"] in ("WARNING", "ERROR", "CRITICAL"):
                key = (entry["level"], entry["logger"], _shape(entry["msg"]))
                all_logs.setdefault(key, set()).add(d["date"])
    lines = [f"# STORM archive day check — {out.name}", "",
             "| Date | Result | Radar | Vehicles | Lidar | NOXP | Frames drawn | Slowest frame | Movie | Peak MB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for d in rows:
        ph, fr, ac = d.get("phases", {}), d.get("frames", []), d.get("actions", {})
        v, why = verdict(d)
        radar = ph.get("radar_first_image", {})
        movie = ac.get("studio_movie", {})
        lines.append("| {date} | **{v}** {why} | {radar} | {veh} | {lidar} | {noxp} | {fok}/{fn} | {slow} | {movie} | {mb} |".format(
            date=d["date"], v=v, why=why,
            radar=(radar.get("station") or "none") + ("" if radar.get("ok") else " (no image)"),
            veh=len(ph.get("mobile_mesonet", {}).get("vehicles", [])),
            lidar=len(ph.get("lidar_survey", {}).get("locations", [])) or "–",
            noxp="yes" if ph.get("noxp", {}).get("available") else "–",
            fok=sum(1 for f in fr if f["ok"]), fn=len(fr),
            slow=f"{max((f['seconds'] for f in fr), default=0):.0f} s",
            movie=("ok" if movie.get("ok") else "FAILED") if movie else "–",
            mb=d.get("parent_peak_mb", "?")))
    lines += ["", "## Problems by date", ""]
    for d in rows:
        if d.get("problems") or d.get("stopped"):
            lines.append(f"**{d['date']}** ({d.get('why', '')})")
            lines += [f"- {p}" for p in d.get("problems", [])]
            if d.get("stopped"):
                lines.append(f"- stopped: {d['stopped']}")
            lines.append("")
    lines += ["## Warnings and errors STORM logged (grouped; dates where seen)", ""]
    for (level, logger, msg), dates in sorted(all_logs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"- **{level}** `{logger}` — {msg}  _({len(dates)}: {', '.join(sorted(dates))})_")
    (out / "report.md").write_text("\n".join(lines) + "\n")


def _shape(msg: str) -> str:
    """A log message with its numbers and times blanked, so repeats group together."""
    import re
    msg = re.sub(r"\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?", "<time>", msg)
    msg = re.sub(r"\b\d[\d.:/_-]*\b", "#", msg)
    return msg[:200]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dates", nargs="*", help="YYYY-MM-DD")
    ap.add_argument("--preset", action="store_true", help="the representative set of dates")
    ap.add_argument("--out", type=Path, default=None, help="run folder (default: a new one under case_data/day_checks)")
    ap.add_argument("--mem-limit-mb", type=float, default=3000,
                    help="stop a date above this (STORM plus its map process); the Mac has 8 GB")
    ap.add_argument("--redo", action="store_true", help="repeat dates already done in this run folder")
    ap.add_argument("--report", type=Path, help="rebuild the report for this run folder and stop")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return run_child(a.child, a.out, a.mem_limit_mb)
    if a.report:
        write_report(a.report)
        print(a.report / "report.md")
        return 0
    dates = PRESET if a.preset else [(d, "") for d in a.dates]
    if not dates:
        ap.error("give dates, or --preset")
    out = a.out or DEFAULT_OUT / datetime.now().strftime("%Y%m%d-%H%M")
    return run_parent(dates, out, a.mem_limit_mb, a.redo)


if __name__ == "__main__":
    sys.exit(main())
