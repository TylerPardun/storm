"""Exercise cancellation with a blocked request and rapid replacement queries."""
import time
from threading import Event

from PyQt6.QtTest import QTest

from archive.catalog import AvailabilityIndex, ALL_PLATFORMS, ScanCancelled
from ui.launch.availability import AvailabilityWorker


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def test_one_worker_cancels_active_request_and_keeps_only_latest_pending(qapp, listed_platform):
    started, cancelled, release = Event(), Event(), Event()
    calls = []
    def fetch(url, token):
        calls.append(url)
        if len(calls) == 1:
            started.set()
            assert token.wait(2)
            cancelled.set()
            assert release.wait(2)
            raise ScanCancelled()
        return '<html>THREDDS catalog</html>'
    worker = AvailabilityWorker(AvailabilityIndex([listed_platform()], fetch, pace=0))
    updates = []
    worker.updated.connect(lambda generation, snapshot: updates.append((generation, snapshot)))
    try:
        worker.request(1)
        _wait(started.is_set)
        original_thread = worker._thread
        worker.request(2)
        _wait(cancelled.is_set)
        worker.request(3)
        release.set()
        _wait(lambda: any(g == 3 and s.checked == s.total for g, s in updates))
        assert worker._thread is original_thread
        assert not any(g == 2 for g, _ in updates)
        assert not any(g == 1 and s.checked for g, s in updates)
        assert len(calls) == 3  # cancelled first listing, then the two catalogs
        worker.request(4)
        _wait(lambda: any(g == 4 and s.checked == s.total for g, s in updates))
        assert len(calls) == 3  # changing dates after completion is metadata-only
    finally:
        release.set()
        worker.close()
        worker._thread.join(timeout=3)
    assert not worker._thread.is_alive()


def test_close_interrupts_active_scan_and_joins_without_more_requests(qapp, listed_platform):
    started = Event()
    calls = []
    def fetch(url, cancel):
        calls.append(url)
        started.set()
        assert cancel.wait(2)
        raise ScanCancelled()
    worker = AvailabilityWorker(AvailabilityIndex([listed_platform()], fetch, pace=0))
    worker.request(1)
    _wait(started.is_set)
    worker.close()
    worker._thread.join(timeout=3)
    assert not worker._thread.is_alive()
    assert len(calls) == 1
