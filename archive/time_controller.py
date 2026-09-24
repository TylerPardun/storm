
import logging
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# how often the internal timer fires (wall-clock milliseconds).
_TICK_MS = 500

# available playback speed multipliers.
SPEED_OPTIONS = [1, 5, 10, 30, 60, 120, 300]

# Legacy default step retained for callers outside ArchiveControls.
STEP_SECONDS = 30


class TimeController(QObject):
    """
    Central archive clock.

    Signals
    -------
    time_changed(datetime)
        Emitted every time the current archive time advances, either via
        play-mode ticking or a manual step/jump.  Always UTC-aware.
    playing_changed(bool)
        Emitted when play/pause state changes.
    """

    time_changed    = pyqtSignal(object)   # datetime (UTC-aware)
    playing_changed = pyqtSignal(bool)
    window_changed  = pyqtSignal(object, object)   # (start, end) datetimes

    def __init__(self, start_time: datetime, parent=None):
        super().__init__(parent)

        # normalise to UTC.
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        else:
            start_time = start_time.astimezone(timezone.utc)

        self._current_time: datetime = start_time
        # The session spans its UTC day and can run into the next morning
        # (archive/session.py); until the day's activity is known the end
        # sits at the latest possible point.
        from archive.session import session_bounds
        self._window_start, self._window_end = session_bounds(start_time)
        self._speed_idx: int = 2          # default 10×
        self._playing: bool = False

        # accumulator: wall-clock ms that have elapsed toward the next archive
        self._accum_ms: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)


    @property
    def current_time(self) -> datetime:
        return self._current_time

    @property
    def speed(self) -> int:
        return SPEED_OPTIONS[self._speed_idx]

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def window(self) -> tuple[datetime, datetime]:
        return self._window_start, self._window_end

    def set_window_end(self, end: datetime) -> None:
        """Move the end of the playable span (e.g. once the day's activity is
        known); pulls the current time back inside if needed."""
        end = end.astimezone(timezone.utc)
        if end == self._window_end:
            return
        self._window_end = max(end, self._window_start + timedelta(seconds=1))
        self.window_changed.emit(self._window_start, self._window_end)
        clamped = self._clamp(self._current_time)
        if clamped != self._current_time:
            self.pause()
            self.set_time(clamped)

    def _clamp(self, dt: datetime) -> datetime:
        return max(self._window_start, min(self._window_end - timedelta(seconds=1), dt))


    def set_time(self, dt: datetime) -> None:
        """Jump to an arbitrary archive time."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        self._current_time = self._clamp(dt)
        self._accum_ms = 0.0
        self.time_changed.emit(self._current_time)

    def step_forward(self) -> None:
        """Advance by STEP_SECONDS and pause."""
        self.step(STEP_SECONDS)

    def step_backward(self) -> None:
        """Rewind by STEP_SECONDS and pause."""
        self.step(-STEP_SECONDS)

    def step(self, seconds: int) -> None:
        """Pause and move by an arbitrary number of archive seconds."""
        self.pause()
        self._advance(seconds)

    def play(self) -> None:
        if self._playing:
            return
        self._playing = True
        self._accum_ms = 0.0
        self._timer.start()
        self.playing_changed.emit(True)
        log.debug("Archive play started at speed=%dx", self.speed)

    def pause(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._timer.stop()
        self.playing_changed.emit(False)

    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def set_speed(self, multiplier: int) -> None:
        """Set playback speed.  multiplier must be in SPEED_OPTIONS."""
        if multiplier in SPEED_OPTIONS:
            self._speed_idx = SPEED_OPTIONS.index(multiplier)
        else:
            # clamp to nearest.
            self._speed_idx = min(
                range(len(SPEED_OPTIONS)),
                key=lambda i: abs(SPEED_OPTIONS[i] - multiplier),
            )
        log.debug("Archive speed set to %dx", self.speed)

    def set_speed_by_index(self, idx: int) -> None:
        self._speed_idx = max(0, min(len(SPEED_OPTIONS) - 1, idx))

    def set_precision_playback(self, enabled: bool) -> None:
        """Configure exact one-second ticks for dense observation playback."""
        self.pause()
        self._timer.setInterval(1000 if enabled else _TICK_MS)
        if enabled:
            self.set_speed(1)
            self._current_time = self._current_time.replace(microsecond=0)
            self.time_changed.emit(self._current_time)

    def window_seconds(self) -> int:
        """Length of the playable span, in seconds."""
        return int((self._window_end - self._window_start).total_seconds())

    def seconds_since_start(self) -> int:
        """Current archive time as seconds since the session's 00:00:00 UTC."""
        return int((self._current_time - self._window_start).total_seconds())

    def set_seconds_since_start(self, secs: int) -> None:
        """Jump to a position given as seconds since the session's 00:00:00 UTC."""
        self.set_time(self._window_start + timedelta(seconds=secs))


    def _ms_per_archive_second(self) -> float:
        """Wall-clock ms required to advance one archive second at current speed."""
        return self._timer.interval() / self.speed

    def _on_tick(self) -> None:
        """Called every _TICK_MS wall-clock ms while playing."""
        # each wall-clock tick represents `speed` archive-seconds worth of time.
        archive_seconds = self._timer.interval() / 1000.0 * self.speed
        self._advance(archive_seconds)

    def _advance(self, archive_seconds: float) -> None:
        new_time = self._clamp(self._current_time + timedelta(seconds=archive_seconds))
        if new_time == self._current_time:
            if self._playing and new_time >= self._window_end - timedelta(seconds=1):
                self.pause()  # reached the end of the session
            return
        self._current_time = new_time
        self.time_changed.emit(self._current_time)
