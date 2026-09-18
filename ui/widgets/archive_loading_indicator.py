from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt, pyqtSignal


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
    """

    all_done = pyqtSignal()

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
        self._refresh()

    def _current_task(self) -> str | None:
        for task in self._tasks:
            if task not in self._done:
                return task
        return None

    def _refresh(self) -> None:
        task = self._current_task()
        if task is None:
            self.hide()
            if not self._finished:
                self._finished = True
                self.all_done.emit()
            return
        self.setText(self._status_override or f"Loading {task}…")
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
