"""Summarize a STORM performance log (perf_logs/perf-*.jsonl, from --perf-log).

    python scripts/summarize_perf_log.py                  # the newest log
    python scripts/summarize_perf_log.py perf_logs/perf-TB55-20261015-140210.jsonl
    python scripts/summarize_perf_log.py --out report.md  # also write it to a file

Prints the computer, how long each heavy step took (count, median, 90th
percentile, worst), memory and CPU over the session, how often the window
froze, how smoothly the map drew, and any warnings STORM logged.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STEP_LABELS = {
    "radar.download": "Radar download (AWS)",
    "radar.decode": "Radar decode",
    "radar.render": "Radar image (archive)",
    "radar.render_live": "Radar image (live)",
    "lidar.fetch": "Lidar file fetch",
    "lidar.parse": "Lidar file parse",
    "lidar.render": "Lidar sweep image",
    "catalog.scan": "Archive catalog scan",
}


def _load(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue                       # a line cut off by a crash
    return records


def _pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _fmt_ms(ms):
    if ms is None:
        return "—"
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.0f} ms"


def summarize(records: list[dict]) -> str:
    out = []
    by_kind = defaultdict(list)
    for r in records:
        by_kind[r.get("kind")].append(r)
    system = (by_kind.get("system") or [{}])[0]
    duration = max((r.get("t", 0) for r in records), default=0)

    out.append(f"# STORM performance log — {system.get('computer', '?')}, {system.get('started_utc', '?')}")
    out.append("")
    out.append("## Computer")
    out.append(f"- CPU: {system.get('cpu', '?')} ({system.get('logical_cpus', '?')} logical cores)")
    out.append(f"- RAM: {system.get('ram_total_mb', '?')} MB total, {system.get('ram_available_mb', '?')} MB free at start")
    out.append(f"- OS: {system.get('os', '?')}; Python {system.get('python', '?')}; STORM {system.get('storm', '?')}")
    for r in by_kind.get("screen", [])[:1]:
        out.append(f"- Screen: {r.get('width')}×{r.get('height')} at {r.get('scale')}× scaling, {r.get('screens')} screen(s)")
    for r in by_kind.get("gpu", [])[:1]:
        out.append(f"- Map graphics: WebGL {r.get('webgl')} on {r.get('renderer', '?')} ({r.get('vendor', '?')}), "
                   f"max texture {r.get('max_texture')}")
    versions = system.get("versions") or {}
    if versions:
        out.append("- Libraries: " + ", ".join(f"{k} {v}" for k, v in sorted(versions.items())))
    out.append(f"- Session length: {duration / 60:.1f} min")
    for r in by_kind.get("archive.start", []):
        out.append(f"- Archive session opened at t={r['t']:.0f} s for {r.get('archive_time')}")
    out.append("")

    out.append("## Steps")
    out.append("| Step | Count | Median | 90th pct | Worst | Detail |")
    out.append("|---|---|---|---|---|---|")
    for kind, label in STEP_LABELS.items():
        rows = [r for r in by_kind.get(kind, []) if "ms" in r]
        if not rows:
            continue
        groups = defaultdict(list)
        for r in rows:
            key = (f"{r.get('size')} px{' cropped' if r.get('cropped') else ''}" if kind.startswith("radar.render")
                   else "")
            groups[key].append(r["ms"])
        for detail, values in sorted(groups.items()):
            out.append(f"| {label} | {len(values)} | {_fmt_ms(statistics.median(values))} | "
                       f"{_fmt_ms(_pct(values, 0.9))} | {_fmt_ms(max(values))} | {detail} |")
    downloads = by_kind.get("radar.download", [])
    if downloads:
        mb = sum(r.get("mb", 0) for r in downloads)
        secs = sum(r.get("ms", 0) for r in downloads) / 1000
        retries = sum(1 for r in downloads if r.get("attempt", 1) > 1)
        out.append(f"\nRadar downloads: {mb:.0f} MB in {secs:.0f} s ({mb * 8 / max(secs, 1e-6):.1f} Mbit/s while "
                   f"downloading); {retries} needed more than one try.")
    shown = by_kind.get("radar.shown", [])
    if shown:
        first = next((r for r in shown if r.get("first")), shown[0])
        start = (by_kind.get("archive.start") or [{"t": 0}])[0]["t"]
        out.append(f"First radar image {first['t'] - start:.0f} s after the archive session opened; "
                   f"{len(shown)} radar frames shown, {len(by_kind.get('radar.shown_sharp', []))} sharp.")
    for r in by_kind.get("mesonet.loaded", []):
        out.append(f"Mobile mesonet loaded at t={r['t']:.0f} s: {r.get('vehicles')} vehicles, {r.get('rows')} rows.")
    out.append("")

    samples = by_kind.get("sample", [])
    if samples:
        out.append("## Memory and CPU (every 5 s)")
        rss = [s["rss_mb"] for s in samples if s.get("rss_mb", -1) >= 0]
        peak = max((s.get("peak_mb", -1) for s in samples), default=-1)
        avail = [s["system_available_mb"] for s in samples if s.get("system_available_mb", -1) >= 0]
        cpu = [s["cpu_pct"] for s in samples if "cpu_pct" in s]
        if rss:
            out.append(f"- STORM memory: median {statistics.median(rss):.0f} MB, highest {max(rss):.0f} MB "
                       f"(peak ever {peak:.0f} MB; the map's web view runs in separate processes, not counted here)")
        if avail:
            out.append(f"- Free system memory: lowest {min(avail):.0f} MB, median {statistics.median(avail):.0f} MB")
        if cpu:
            out.append(f"- STORM CPU (% of one core): median {statistics.median(cpu):.0f} %, "
                       f"90th pct {_pct(cpu, 0.9):.0f} %, highest {max(cpu):.0f} %")
        out.append("")

    stalls = [r["ms"] for r in by_kind.get("ui_stall", [])]
    out.append("## Responsiveness")
    if stalls:
        out.append(f"- Window froze {len(stalls)} times for ≥ 0.3 s; worst {stalls and max(stalls) / 1000:.1f} s; "
                   f"{sum(stalls) / 1000:.0f} s frozen in total ({100 * sum(stalls) / 1000 / max(duration, 1):.1f} % of the session)")
        long_ones = sorted((r for r in by_kind["ui_stall"] if r["ms"] >= 1000), key=lambda r: -r["ms"])[:5]
        stacks = by_kind.get("ui_stall_stack", [])
        for r in long_ones:
            near = [x.get("kind") for x in records if abs(x.get("t", 0) - r["t"]) < 3 and x.get("kind") not in
                    ("sample", "ui_stall", "map.frames", "ui_stall_stack")]
            out.append(f"  - {r['ms'] / 1000:.1f} s at t={r['t']:.0f} s; around it: {', '.join(sorted(set(near))) or 'nothing logged'}")
            # stacks captured during this freeze (it ended at r['t'], began r['ms'] earlier)
            during = [x for x in stacks if r["t"] - r["ms"] / 1000 - 0.5 <= x["t"] <= r["t"] + 0.5]
            if during:
                deepest = during[-1]
                out.append(f"    main thread after {deepest['frozen_s']} s: "
                           + " ← ".join(reversed([line for line in deepest['stack'] if '/' in line or '.py' in line][-6:])))
    else:
        out.append("- No window freezes of 0.3 s or more.")
    frames = by_kind.get("map.frames", [])
    if frames:
        fps = [f["frames"] / 5.0 for f in frames if f.get("frames") is not None]
        worst = max((f.get("worst_ms", 0) for f in frames), default=0)
        slow = sum(f.get("slow_frames", 0) for f in frames)
        out.append(f"- Map drawing: median {statistics.median(fps):.0f} frames/s (lowest {min(fps):.0f}); "
                   f"worst single frame {worst} ms; {slow} frames slower than 50 ms")
    out.append("")

    notes = by_kind.get("note", [])
    playback = by_kind.get("playback", [])
    if notes or playback:
        out.append("## Notes")
        for r in notes:
            out.append(f"- t={r['t']:.0f} s: {r.get('what')} ({', '.join(f'{k}={v}' for k, v in r.items() if k not in ('t', 'kind', 'what', 'thread'))})")
        speeds = Counter(r.get("speed") for r in playback if r.get("playing"))
        if speeds:
            out.append("- Played at: " + ", ".join(f"{s}× ({n})" for s, n in sorted(speeds.items())))
        out.append("")

    logs = by_kind.get("log", [])
    out.append("## Warnings and errors STORM logged")
    if logs:
        counts = Counter((r.get("level"), r.get("msg", "")[:160]) for r in logs)
        for (level, msg), n in counts.most_common(15):
            out.append(f"- {level} ×{n}: {msg}")
    else:
        out.append("- None.")
    if not any(r.get("kind") == "end" for r in records):
        out.append("\nThe log has no end line: STORM was closed abnormally or is still running.")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", nargs="?", help="a perf-*.jsonl file (default: the newest in perf_logs/)")
    ap.add_argument("--out", help="also write the report here")
    a = ap.parse_args()
    if a.log:
        path = Path(a.log)
    else:
        logs = sorted((ROOT / "perf_logs").glob("perf-*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not logs:
            print("No logs in perf_logs/. Start STORM with --perf-log first.")
            return 1
        path = logs[-1]
    report = summarize(_load(path))
    if a.out:
        Path(a.out).write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
