"""One cancellable catalog worker for the launch dialog; no widgets off-thread."""
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from archive.catalog import AvailabilityIndex, ScanCanceled


class AvailabilityWorker(QObject):
    updated = pyqtSignal(int, object)

    def __init__(self, index=None):
        # Intentionally unparented: a running Python thread retains this signal
        # source until it exits. Qt disconnects destroyed dialog receivers.
        super().__init__()
        self._index = index or AvailabilityIndex()
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._pending = None
        self._refresh_pending = False
        self._closed = False
        self._thread = None

    def request(self, generation, refresh=False):
        with self._condition:
            if self._closed:
                return
            self._cancel.set()
            self._pending = generation
            self._refresh_pending |= refresh
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="archive-catalog")
                self._thread.start()
            self._condition.notify()

    def cancel(self):
        with self._condition:
            self._cancel.set()
            self._pending = None

    def close(self):
        with self._condition:
            self._closed = True
            self._cancel.set()
            self._pending = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                generation, self._pending = self._pending, None
                refresh, self._refresh_pending = self._refresh_pending, False
                cancel = self._cancel = threading.Event()
            if refresh:
                self._index.clear()
            try:
                for snapshot in self._index.scan(cancel):
                    if cancel.is_set():
                        break
                    self.updated.emit(generation, snapshot)
            except ScanCanceled:
                pass
