@echo off
REM scripts/launch_storm_perf_log.bat -- start STORM with the performance log on
REM (core/perf_log.py). Each session writes perf_logs\perf-<computer>-<time>.jsonl;
REM afterwards run scripts\collect_perf_logs.py to zip the logs for upload.
CALL "%~dp0launch_storm.bat" --perf-log
