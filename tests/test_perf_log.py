"""--perf-log: the log is off by default, costs nothing then, and records
what the summary reads."""
import json
import sys
from pathlib import Path

import pytest

from core import perf_log

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from summarize_perf_log import _load, summarize  # noqa: E402


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(perf_log, "SAMPLE_S", 0.05)
    yield tmp_path
    perf_log.stop()


def test_off_by_default_and_harmless():
    assert not perf_log.enabled()
    perf_log.event("radar.decode", ms=1)                 # nothing happens, nothing raises
    with perf_log.timed("lidar.render") as info:
        info["rays"] = 3
    perf_log.note_ui_lag(5000)
    assert perf_log.path() is None


def test_records_steps_samples_and_stalls(log_dir):
    import time
    path = perf_log.start(root=log_dir, argv=["main.py", "--perf-log"])
    assert path is not None and path.parent == log_dir / "perf_logs"
    perf_log.event("archive.start", archive_time="2013-05-31T23:00:00+00:00")
    perf_log.event("radar.decode", ms=780, station="KTLX")
    perf_log.event("radar.render", size=4096, cropped=True, ms=1900)
    with perf_log.timed("lidar.render", rays=3852) as info:
        info["field"] = "velocity"
    with pytest.raises(ValueError):
        with perf_log.timed("lidar.parse"):
            raise ValueError("bad file")
    perf_log.note_ui_lag(1200)
    time.sleep(0.2)                                       # a few samples
    perf_log.stop()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    kinds = [r["kind"] for r in records]
    assert kinds[0] == "system" and kinds[-1] == "end"
    system = records[0]
    assert system["logical_cpus"] and system["cpu"] and "--perf-log" in system["argv"]
    lidar = next(r for r in records if r["kind"] == "lidar.render")
    assert lidar["rays"] == 3852 and lidar["field"] == "velocity" and lidar["ok"] is True and "ms" in lidar
    assert next(r for r in records if r["kind"] == "lidar.parse")["ok"] is False
    assert any(r["kind"] == "ui_stall" and r["ms"] == 1200 for r in records)
    sample = next(r for r in records if r["kind"] == "sample")
    assert {"rss_mb", "peak_mb", "cpu_pct", "ui_lag_max_ms"} <= set(sample)

    report = summarize(_load(path))
    assert "Radar decode" in report and "4096 px cropped" in report and "Lidar sweep image" in report
    assert "froze 1 times" in report


def test_requested_by_flag_or_environment(monkeypatch):
    monkeypatch.delenv("STORM_PERF_LOG", raising=False)
    assert perf_log.requested(["main.py", "--perf-log"])
    assert not perf_log.requested(["main.py"])
    monkeypatch.setenv("STORM_PERF_LOG", "1")
    assert perf_log.requested(["main.py"])


def test_a_log_cut_off_by_a_crash_still_summarizes(tmp_path):
    log = tmp_path / "perf-x.jsonl"
    log.write_text('{"t":0,"kind":"system","computer":"TB"}\n{"t":1,"kind":"radar.decode","ms":900}\n{"t":2,"kin')
    report = summarize(_load(log))
    assert "Radar decode" in report and "no end line" in report


def test_a_freeze_records_what_the_main_thread_was_running(log_dir):
    import time
    path = perf_log.start(root=log_dir)
    perf_log.note_ui_lag(0)                       # a heartbeat, then the main thread is busy

    def slow_main_thread_work():
        time.sleep(1.4)
    slow_main_thread_work()
    perf_log.stop()
    stacks = [json.loads(line) for line in path.read_text().splitlines()
              if '"ui_stall_stack"' in line]
    assert stacks and stacks[0]["frozen_s"] >= 1.0
    assert any("slow_main_thread_work" in frame for frame in stacks[0]["stack"])
