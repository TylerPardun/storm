from PyQt6.QtWidgets import QLabel
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal


class ArchiveLoadingIndicator(QLabel):
    """Small, non-blocking status text for archive-mode background startup.

    Unlike a modal loading dialog, the map and controls are usable the
    moment the session opens -- this never blocks input (see
    WA_TransparentForMouseEvents below). It shows only the single task
    still outstanding ("Loading Radar…"), advancing to the next one as
    each finishes, and disappears once none are left. No checklist, no
    per-task checkmarks -- a whole campaign root's worth of NOXP discovery
    can legitimately take minutes, and a list of five items sitting behind
    four checkmarks for that whole time reads as far more "stuck" than one
    line of text quietly changing.

    After startup the same line shows later background work (begin/end,
    e.g. "Loading Lidar truck · stop 2…"), the most recent first, so the
    user can see something is churning. startup_pending() -- not
    isVisible() -- says whether startup itself is still going.
    """

    all_done = pyqtSignal()
    MIN_SHOWN_S = 1.0     # a later activity's line stays at least this long, so it's seen

    def __init__(self, tasks: list[str], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "color: #8E97AB; font-size: 11px; font-weight: 500;"
            "background: rgba(10, 10, 15, 0.6); border-radius: 4px; padding: 4px 9px;"
        )
        self._tasks = list(tasks)
        self._done: set[str] = set()
        self._status_override: str | None = None
        self._finished = False
        self._activities: dict[str, str] = {}      # key -> text, in the order begun
        self._begun_at: dict[str, float] = {}
        self._refresh()

    def _current_task(self) -> str | None:
        for task in self._tasks:
            if task not in self._done:
                return task
        return None

    def startup_pending(self) -> bool:
        return self._current_task() is not None

    def begin(self, key: str, text: str) -> None:
        """Show background work after startup (or once startup finishes)."""
        self._activities.pop(key, None)
        self._activities[key] = text
        self._begun_at[key] = time.monotonic()
        self._refresh()

    def end(self, key: str) -> None:
        if key not in self._activities:
            return
        left = self.MIN_SHOWN_S - (time.monotonic() - self._begun_at.get(key, 0.0))
        if left > 0:
            begun = self._begun_at.get(key)
            # unless it was begun again meanwhile
            QTimer.singleShot(int(left * 1000), lambda: self._begun_at.get(key) == begun and self._finish(key))
            return
        self._finish(key)

    def _finish(self, key: str) -> None:
        if self._activities.pop(key, None) is not None:
            self._begun_at.pop(key, None)
            self._refresh()

    def _refresh(self) -> None:
        task = self._current_task()
        if task is None and not self._finished:
            self._finished = True
            self.all_done.emit()
        if task is not None:
            text = self._status_override or f"Loading {task}…"
        elif self._activities:
            text = list(self._activities.values())[-1]
        else:
            self.hide()
            return
        self.setText(text)
        self.adjustSize()
        self.show()

    def set_task_done(self, task_name: str) -> None:
        """Mark a task complete and advance to the next outstanding one."""
        self._done.add(task_name)
        self._status_override = None
        self._refresh()

    def set_task_error(self, task_name: str) -> None:
        """A failed task still counts toward completion -- it shouldn't
        hold up the indicator forever. The feature's own status line
        (status_msg_label) is what actually surfaces the error to Tyler."""
        self.set_task_done(task_name)

    def set_status(self, message: str) -> None:
        """Show a more specific message in place of the current task's
        generic "Loading {task}…" text (e.g. "Fetching first radar
        scan…"). Cleared automatically once that task completes."""
        self._status_override = message
        self._refresh()
