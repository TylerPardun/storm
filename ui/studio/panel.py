"""The Video Studio panel, in the archive bar's place while open (File > Video
Studio, Esc or Close Studio to leave). Laid out like a video editor:

  header     project name, Open / Save, undo / redo, Close Studio
  case       the whole session's clock (CaseStrip): drag or type to set the
             case time a keyframe will use; radar scan steps
  transport  Add Keyframe, play / step through the movie, movie time, zoom
  timeline   the movie (MovieTimeline): a ruler in movie seconds, one clip
             per move between keyframes (case span and speed), holds, the
             keyframes (drag to retime: later ones follow, Alt moves one
             alone; right-click for more) and the playhead (drag to scrub:
             the map shows that moment)
  inspector  the selected keyframe: case time, view, easing, hold, and the
             length / speed of the move that follows
  output     format, size, frame rate, captions; Save Frame, Export

Keys while open: Space play, K add keyframe, [ ] previous / next keyframe,
Left / Right a frame (Shift: a second), Home / End, Delete, Cmd+Z / Shift+Cmd+Z.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QDate, QDateTime, QEvent, QObject, QPointF, QRectF, QSize, Qt, QTime, QTimer, QTimeZone, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QScrollBar, QSizePolicy, QStackedLayout, QToolButton, QToolTip, QVBoxLayout,
    QWidget,
)

from core.studio import EASINGS, RESOLUTIONS, Keyframe, Project, View

STUDIO_ROOT = Path(__file__).resolve().parents[2] / "data" / "studio"

ORANGE, CYAN, RED = "#FF9F1C", "#00CFFF", "#FF5A5F"
TEXT, MUTED, DIM, GROOVE = "#E8EAF0", "#8E97AB", "#5B6480", "#1E2433"
UNDO_LIMIT = 200
SPEED_PRESETS = (30, 60, 120, 300, 600, 1200, 2400)
SCRUB_APPLY_MS = 70            # at most this often while scrubbing: each clock change loads data
PLAY_TICK_MS = 33

STYLE = """
#archiveControls { background-color: rgba(13, 13, 22, 0.97); }
#archiveControls QLineEdit, #archiveControls QDateTimeEdit, #archiveControls QDoubleSpinBox {
    background-color: rgba(26, 26, 46, 0.55); border: 1px solid rgba(46, 46, 78, 0.85);
    border-radius: 5px; color: #E8EAF0; font-size: 11px; padding: 1px 5px; }
#archiveControls QLineEdit:focus, #archiveControls QDateTimeEdit:focus, #archiveControls QDoubleSpinBox:focus {
    border-color: #FF9F1C; }
#archiveControls QLineEdit:disabled, #archiveControls QDateTimeEdit:disabled,
#archiveControls QDoubleSpinBox:disabled, #archiveControls QComboBox:disabled { color: #4A5168; }
#archiveControls QToolButton { padding: 0 7px; font-size: 11px; }
#archiveControls QComboBox { min-width: 0; padding: 1px 4px 1px 6px; font-size: 11px; }
#archiveControls QComboBox::drop-down { width: 16px; border: none; }
#archiveControls QComboBox::down-arrow {
    width: 0; height: 0; margin-right: 5px; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid #8E97AB; border-bottom: none; }
#archiveControls QComboBox QLineEdit { background: transparent; border: none; padding: 0; }
#archiveControls QToolButton:disabled { color: #3E4459; }
#archiveControls QCheckBox { color: #C8D0DE; font-size: 11px; }
#archiveControls QPushButton#studioClose {
    background-color: rgba(255, 90, 95, 0.12); border: 1px solid #FF5A5F; border-radius: 6px;
    color: #FFB3B5; font-size: 12px; font-weight: 700; padding: 0 12px; }
#archiveControls QPushButton#studioClose:hover { background-color: #FF5A5F; color: #0A0A0F; }
#archiveControls QPushButton#archivePlayButton:disabled {
    background-color: rgba(255, 159, 28, 0.15); border-color: rgba(255, 159, 28, 0.25); color: #6B5A3F; }
#archiveControls QFrame#studioBox {
    background-color: rgba(10, 10, 18, 0.55); border: 1px solid rgba(60, 70, 100, 0.55); border-radius: 6px; }
#archiveControls QScrollBar:horizontal { height: 8px; background: transparent; margin: 0 8px; }
#archiveControls QScrollBar::handle:horizontal { background: #2E3550; border-radius: 4px; min-width: 30px; }
#archiveControls QScrollBar::handle:horizontal:hover { background: #4A5470; }
#archiveControls QScrollBar::add-line, #archiveControls QScrollBar::sub-line { width: 0; }
#archiveControls QScrollBar::add-page, #archiveControls QScrollBar::sub-page { background: transparent; }
"""


def movie_clock(s: float) -> str:
    """0:04.2"""
    s = max(0.0, s)
    m = int(s // 60)
    return f"{m}:{s - 60 * m:04.1f}"


def speed_text(x: float) -> str:
    x = abs(x)
    return f"{x:.0f}×" if x >= 10 else f"{x:.1f}×"


def case_span_text(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def diamond(x: float, y: float, r: float) -> QPolygonF:
    return QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)])


def to_qdt(t: datetime) -> QDateTime:
    t = t.astimezone(timezone.utc)
    return QDateTime(QDate(t.year, t.month, t.day), QTime(t.hour, t.minute, t.second), QTimeZone.utc())


def from_qdt(q: QDateTime) -> datetime:
    return datetime.fromtimestamp(q.toSecsSinceEpoch(), timezone.utc)


def label(text: str = "", style: str = f"color: {MUTED}; font-size: 11px;") -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(style)
    return l


class _WheelGuard(QObject):
    """Scrolling over a field changes it only once it's been clicked into,
    so scrolling the panel (or the case strip) can't nudge a time or value
    by accident."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


_WHEEL_GUARD = None


def _wheel_only_when_focused(widget) -> None:
    global _WHEEL_GUARD
    if _WHEEL_GUARD is None:
        _WHEEL_GUARD = _WheelGuard()
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_WHEEL_GUARD)


class _ElidedLabel(QLabel):
    """One line that gives way to the controls beside it: shortened with …
    when there's no room, the whole text in its tooltip."""

    def __init__(self, style: str):
        super().__init__()
        self.setStyleSheet(style)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self._full = ""

    def setText(self, text: str, tip: str | None = None) -> None:
        self._full = text
        self.setToolTip(tip or text)
        self._elide()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))


def vdiv() -> QFrame:
    d = QFrame()
    d.setFrameShape(QFrame.Shape.VLine)
    d.setStyleSheet("color: #394056; margin: 3px 2px;")
    return d


# ---------------------------------------------------------------------------
class CaseStrip(QWidget):
    """The case clock over the whole session (or, zoomed, a window that
    follows the clock): time ticks, radar scans, where the movie's keyframes
    sit in the case, and the clock. Drag to move the clock (it is set on
    release; keyframe times snap), scroll to step radar scans."""
    seek = pyqtSignal(object)                # datetime, on release

    INSET = 10

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._tc = panel._tc
        self._drag_time = None
        self._wheel = 0
        self.zoom_s: float | None = None          # None: the whole session
        self._center = self._tc.current_time
        self.setFixedHeight(42)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")

    # ---- the span shown: the whole session, or a window that follows the clock ----
    def set_zoom(self, seconds: float | None) -> None:
        self.zoom_s = seconds
        self._center = self._tc.current_time
        self.update()

    def span(self) -> tuple[datetime, datetime]:
        lo, hi = self._tc.window
        if self.zoom_s is None or (hi - lo).total_seconds() <= self.zoom_s:
            return lo, hi
        half = timedelta(seconds=self.zoom_s / 2)
        clock = self._drag_time or self._tc.current_time
        if self._drag_time is None:                      # keep the clock in view
            a, b = self._center - half, self._center + half
            margin = half * 0.1
            if not (a + margin <= clock <= b - margin):
                self._center = clock
        a = max(lo, min(self._center - half, hi - 2 * half))
        return a, a + 2 * half

    def _x(self, t: datetime) -> float:
        start, end = self.span()
        total = max(1.0, (end - start).total_seconds())
        return self.INSET + (self.width() - 2 * self.INSET) * (t - start).total_seconds() / total

    def _t(self, x: float) -> datetime:
        start, end = self.span()
        frac = min(1.0, max(0.0, (x - self.INSET) / max(1, self.width() - 2 * self.INSET)))
        t = start + timedelta(seconds=round((end - start).total_seconds() * frac))
        for k in self._panel.project.keyframes:          # snap to keyframes' case times
            if abs(self._x(k.time) - x) <= 5:
                return k.time
        return t

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        start, end = self.span()
        total = (end - start).total_seconds()
        if total <= 0:
            return
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()
        w = self.width() - 2 * self.INSET
        mid = 20
        # ticks and labels: a label at least every ~70 px
        tick = next((m for m in (5, 10, 15, 30, 60, 120, 180, 360) if w * m * 60 / total >= 22), 360)
        every = next((n for n in (1, 2, 3, 4, 6, 12, 24) if w * tick * n * 60 / total >= 64), 24)
        t = start.replace(minute=0, second=0, microsecond=0)
        while t < start:
            t += timedelta(minutes=tick)
        while t <= end:
            x = self._x(t)
            minutes = t.hour * 60 + t.minute
            labeled = (minutes // tick) % every == 0
            p.setPen(QColor("#2A3044") if not labeled else QColor("#3A4258"))
            p.drawLine(QPointF(x, mid - (7 if labeled else 4)), QPointF(x, mid + (7 if labeled else 4)))
            if labeled:
                text = f"{t:%H}Z" if t.minute == 0 and tick * every >= 60 else f"{t:%H:%M}Z"
                p.setPen(QColor(DIM))
                p.drawText(int(min(max(0, x - fm.horizontalAdvance(text) / 2), self.width() - fm.horizontalAdvance(text))),
                           self.height() - 2, text)
            t += timedelta(minutes=tick)
        p.fillRect(QRectF(self.INSET, mid - 2, w, 4), QColor(GROOVE))
        # radar scans
        p.setPen(QPen(QColor(110, 125, 165, 110), 1))
        for s in self._panel.scan_times():
            if start <= s <= end:
                x = self._x(s)
                p.drawLine(QPointF(x, mid - 6), QPointF(x, mid - 3))
        # the movie's coverage of the case and its keyframes
        ks = self._panel.project.sorted()
        if len(ks) > 1:
            lo, hi = min(k.time for k in ks), max(k.time for k in ks)
            p.fillRect(QRectF(self._x(lo), mid - 2, max(2.0, self._x(hi) - self._x(lo)), 4), QColor(ORANGE))
        sel = self._panel.selected
        for i, k in enumerate(ks):
            x = self._x(k.time)
            p.setPen(QPen(QColor("#FFFFFF") if k is sel else QColor("#0A0A0F"), 1.2))
            p.setBrush(QColor(ORANGE))
            p.drawPolygon(diamond(x, mid, 6 if k is sel else 4.5))
        # the clock
        clock = self._drag_time or self._tc.current_time
        x = self._x(clock)
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawLine(QPointF(x, 3), QPointF(x, mid + 9))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CYAN))
        p.drawEllipse(QPointF(x, 5), 4, 4)
        if self._drag_time is not None:
            text = f"{self._drag_time:%H:%M:%S}Z"
            tw = fm.horizontalAdvance(text) + 8
            bx = min(max(0, x + 7), self.width() - tw)
            p.setBrush(QColor(0, 0, 0, 200))
            p.drawRoundedRect(QRectF(bx, 0, tw, 14), 3, 3)
            p.setPen(QColor(CYAN))
            p.drawText(int(bx + 4), 11, text)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_time = self._t(e.position().x())
            self.update()

    def mouseMoveEvent(self, e) -> None:
        t = self._t(e.position().x())
        if self._drag_time is not None:
            self._drag_time = t
            self.update()
        else:
            QToolTip.showText(e.globalPosition().toPoint(),
                             f"{t:%H:%M:%S} UTC — click or drag to set the clock\nScroll: radar scans (Shift: minutes)", self)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_time is not None:
            t, self._drag_time = self._drag_time, None
            self.seek.emit(t)
            self.update()

    def wheelEvent(self, e) -> None:
        """Scroll: a radar scan per notch (Shift: a minute)."""
        d = e.angleDelta()
        self._wheel += d.y() if abs(d.y()) >= abs(d.x()) else -d.x()
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel > 0 else -1
            self._wheel -= 120 * step
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier or not self._panel.scan_times():
                self._panel._nudge_clock(60 * step)
            else:
                self._panel._scan(step)
        e.accept()


# ---------------------------------------------------------------------------
class MovieTimeline(QWidget):
    """The movie in movie seconds: ruler, clips, holds, keyframes, playhead."""
    scrub = pyqtSignal(float)               # movie seconds
    view_changed = pyqtSignal()             # zoom or scroll

    PAD = 14
    RULER_H = 18
    TRACK_Y = 24
    TRACK_H = 48

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.pps = 40.0                     # pixels per movie second
        self.left = 0.0                     # movie seconds at the left edge
        self._drag = None                   # (keyframe, press x, its at) while dragging one
        self._drag_moved = False
        self._scrubbing = False
        self.setFixedHeight(self.TRACK_Y + self.TRACK_H + 6)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---- geometry ----------------------------------------------------------
    def x(self, m: float) -> float:
        return self.PAD + (m - self.left) * self.pps

    def m(self, x: float) -> float:
        return self.left + (x - self.PAD) / self.pps

    def visible_seconds(self) -> float:
        return max(0.1, (self.width() - 2 * self.PAD) / self.pps)

    def content_seconds(self) -> float:
        return max(self._panel.project.duration() * 1.15 + 2.0, self.visible_seconds())

    def set_left(self, left: float) -> None:
        self.left = max(0.0, min(left, self.content_seconds() - self.visible_seconds()))
        self.update()
        self.view_changed.emit()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        anchor_m = self.m(anchor_x)
        self.pps = min(600.0, max(2.0, self.pps * factor))
        self.set_left(anchor_m - (anchor_x - self.PAD) / self.pps)

    def fit(self) -> None:
        d = max(self._panel.project.duration(), 4.0)
        self.pps = min(600.0, max(2.0, (self.width() - 2 * self.PAD) / (d * 1.08)))
        self.set_left(0.0)

    def ensure_visible(self, m: float) -> None:
        if m < self.left or m > self.left + self.visible_seconds() * 0.96:
            self.set_left(m - self.visible_seconds() * 0.1)

    def _hit(self, x: float, y: float) -> Keyframe | None:
        if y < self.TRACK_Y - 8:
            return None
        best, dist = None, 8.0
        for k in self._panel.project.keyframes:
            d = abs(self.x(k.at) - x)
            if d <= dist:
                best, dist = k, d
        return best

    def segment_at(self, m: float) -> int:
        """Index of the keyframe whose hold or following move contains m (-1: none)."""
        ks = self._panel.project.sorted()
        for i, k in enumerate(ks):
            end = ks[i + 1].at if i + 1 < len(ks) else k.leaves
            if k.at - 1e-9 <= m < end or (i + 1 == len(ks) and abs(m - k.at) < 1e-6):
                return i
        return -1

    # ---- painting ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()
        W = self.width()
        project = self._panel.project
        ks = project.sorted()
        duration = project.duration()
        top, h = self.TRACK_Y, self.TRACK_H

        # ruler
        p.fillRect(QRectF(0, 0, W, self.RULER_H), QColor(10, 10, 18, 120))
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * self.pps >= 56), 600)
        minor = step / 5
        first = int(self.left / minor)
        n = first
        while True:
            m = n * minor
            x = self.x(m)
            if x > W:
                break
            major = abs(m / step - round(m / step)) < 1e-6
            p.setPen(QColor("#3A4258") if not major else QColor("#56607C"))
            p.drawLine(QPointF(x, self.RULER_H - (7 if major else 3)), QPointF(x, self.RULER_H))
            if major:
                text = movie_clock(m) if step < 1 else f"{int(m // 60)}:{int(m % 60):02d}"
                p.setPen(QColor(DIM))
                p.drawText(int(x + 3), 11, text)
            n += 1

        # the track
        p.fillRect(QRectF(0, top, W, h), QColor(255, 255, 255, 8))
        if not ks:
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, top, W, h), Qt.AlignmentFlag.AlignCenter, "No keyframes")
        thumbs = self._panel.thumbs
        sel = self._panel.selected
        for i, k in enumerate(ks):
            # its hold
            if k.hold > 0:
                r = QRectF(self.x(k.at), top + 6, max(1.0, k.hold * self.pps), h - 12)
                p.setPen(QPen(QColor("#4A5470"), 1, Qt.PenStyle.DashLine))
                p.setBrush(QColor(60, 68, 92, 120))
                p.drawRoundedRect(r, 4, 4)
                if r.width() > 46:
                    p.setPen(QColor(MUTED))
                    p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, f"hold {k.hold:.1f}s")
            if i + 1 == len(ks):
                break
            # the move to the next keyframe: a clip
            b = ks[i + 1]
            r = QRectF(self.x(k.leaves) + 1, top + 2, max(1.0, self.x(b.at) - self.x(k.leaves) - 2), h - 4)
            case_s = (b.time - k.time).total_seconds()
            if case_s > 0:
                fill, edge = QColor(255, 159, 28, 46), QColor(ORANGE)
            elif case_s < 0:
                fill, edge = QColor(190, 120, 255, 46), QColor("#BE78FF")
            else:
                fill, edge = QColor(0, 207, 255, 34), QColor(CYAN)
            selected_clip = k is sel
            p.setPen(QPen(edge if selected_clip else edge.darker(160), 1.6 if selected_clip else 1))
            p.setBrush(fill.lighter(140) if selected_clip else fill)
            p.drawRoundedRect(r, 5, 5)
            text_x = r.left() + 8
            thumb = thumbs.get(self._panel.thumb_key(k))
            if thumb is not None and r.width() > 120:
                th = h - 12
                tw = th * 16 / 9
                p.drawPixmap(QRectF(r.left() + 5, r.top() + 4, tw, th).toRect(), thumb)
                text_x += tw + 2
            if r.right() - text_x > 30:
                radar = project.radar_at(k.at)
                station = f"{radar['station']} · " if radar and radar.get("station") else ""
                if case_s:
                    line1 = f"{station}{k.time:%H:%M:%S} → {b.time:%H:%M:%S}Z"
                    line2 = (f"{speed_text(project.segment_speed(i))} · {case_span_text(case_s)} of case"
                             + (" · reverse" if case_s < 0 else "") + f" · {k.easing}")
                else:
                    line1 = f"{station}camera move at {k.time:%H:%M:%S}Z"
                    line2 = f"clock still · {k.easing}"
                avail = int(r.right() - text_x - 6)
                p.setPen(QColor(TEXT))
                p.drawText(int(text_x), int(r.top() + 17), fm.elidedText(line1, Qt.TextElideMode.ElideRight, avail))
                p.setPen(QColor(MUTED))
                p.drawText(int(text_x), int(r.top() + 31), fm.elidedText(line2, Qt.TextElideMode.ElideRight, avail))

        # the end of the movie
        if ks:
            x = self.x(duration)
            p.setPen(QPen(QColor("#56607C"), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, top - 4), QPointF(x, top + h))

        # keyframes
        for i, k in enumerate(ks):
            x = self.x(k.at)
            r = 7.5 if k is sel else 6
            p.setPen(QPen(QColor("#FFFFFF") if k is sel else QColor("#0A0A0F"), 1.5))
            p.setBrush(QColor(ORANGE))
            p.drawPolygon(diamond(x, top, r))

        # the playhead
        x = self.x(self._panel.playhead)
        p.setPen(QPen(QColor(RED), 2))
        p.drawLine(QPointF(x, 4), QPointF(x, top + h))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RED))
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x + 6, 6), QPointF(x, 12),
                                 QPointF(x - 6, 6)]))

    # ---- mouse --------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        kf = self._hit(x, y)
        if e.button() == Qt.MouseButton.RightButton:
            if kf is None:
                i = self.segment_at(self.m(x))
                kf = self._panel.project.sorted()[i] if i >= 0 else None
            self._panel.context_menu(kf, e.globalPosition().toPoint(), self.m(x))
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if kf is not None:
            self._panel.select(kf)
            self._drag = (kf, x, kf.at)
            self._drag_moved = False
            return
        self._scrubbing = True
        m = self.m(x)
        if y >= self.TRACK_Y:
            i = self.segment_at(m)
            if i >= 0:
                self._panel.select(self._panel.project.sorted()[i])
        self.scrub.emit(m)

    def mouseMoveEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            kf, x0, at0 = self._drag
            if not self._drag_moved and abs(x - x0) < 3:
                return
            if not self._drag_moved:
                self._panel.snapshot()
                self._drag_moved = True
            at = at0 + (x - x0) / self.pps
            if abs(self.x(self._panel.playhead) - self.x(at)) <= 6:       # snap to the playhead
                at = self._panel.playhead
            else:
                fps = self._panel.project.fps
                at = round(at * fps) / fps
            ripple = not (e.modifiers() & Qt.KeyboardModifier.AltModifier)
            self._panel.project.move(kf, at, ripple=ripple)
            self._panel.edited(scroll=False)
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"{movie_clock(kf.at)}" + ("" if ripple else "  (only this keyframe)"), self)
            return
        if self._scrubbing and e.buttons() & Qt.MouseButton.LeftButton:
            self.scrub.emit(self.m(x))
            return
        hit = self._hit(x, y)
        self.setCursor(Qt.CursorShape.SizeHorCursor if hit is not None and self._panel.project.sorted().index(hit) > 0
                       else Qt.CursorShape.ArrowCursor)
        if hit is not None:
            ks = self._panel.project.sorted()
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"Keyframe {ks.index(hit) + 1} · {movie_clock(hit.at)} · {hit.time:%H:%M:%S}Z\n"
                              "Drag to retime (later keyframes follow; Alt: this one only)\n"
                              "Double-click to go there · right-click for more", self)

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None and self._drag_moved:
            self._panel.edited()
        self._drag = None
        self._scrubbing = False

    def mouseDoubleClickEvent(self, e) -> None:
        kf = self._hit(e.position().x(), e.position().y())
        if kf is not None:
            self._panel.go_to(kf)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta()
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.zoom(1.0015 ** d.y(), e.position().x())
        else:
            px = d.x() if abs(d.x()) > abs(d.y()) else -d.y()
            self.set_left(self.left - px / 2 / self.pps)
        e.accept()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.set_left(self.left)


# ---------------------------------------------------------------------------
class StudioPanel(QWidget):
    closed = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, window, parent=None):
        super().__init__(parent)
        from ui.studio.capture import MapCapture
        self._w = window
        self._tc = window._time_ctrl
        self.project = Project()
        self.selected: Keyframe | None = None
        self.playhead = 0.0
        self.thumbs: dict[str, QPixmap] = {}
        self._path: Path | None = None
        self._dirty = False
        self._undo: list[tuple[dict, int]] = []
        self._redo: list[tuple[dict, int]] = []
        self._coalesce = None
        self._capture = MapCapture(window, self)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(PLAY_TICK_MS)
        self._play_timer.timeout.connect(self._play_tick)
        self._play_clock = 0.0
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.timeout.connect(self._apply_playhead)
        self._last_apply = 0.0
        self._radar_seen = None                          # radar changes in the RADAR tab show up here
        self._radar_watch = QTimer(self)
        self._radar_watch.setInterval(700)
        self._radar_watch.timeout.connect(self._check_radar)
        self._count_timer = QTimer(self)                 # frames to draw: counted once edits settle
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(300)
        self._count_timer.timeout.connect(self._count_frames)
        self._flash_text = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self.setObjectName("archiveControls")          # the archive bar's look
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self._build()
        self._shortcuts: list[QShortcut] = []
        self._tc.time_changed.connect(self._on_clock)
        self._tc.window_changed.connect(lambda *_a: self._case.update())
        self.refresh()

    # ---- layout ------------------------------------------------------------
    def _tool(self, text, slot, tip="", height=24) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setFixedHeight(height)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _c=False: slot())
        return b

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # header -------------------------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(label("● VIDEO STUDIO", f"color: {ORANGE}; font-size: 11px; font-weight: 800; letter-spacing: 1px;"))
        self._name = QLineEdit(self.project.name)
        self._name.setFixedSize(180, 24)
        self._name.setToolTip("The movie's name: used for its file names")
        self._name.textEdited.connect(self._renamed)
        head.addWidget(self._name)
        head.addWidget(self._tool("Open…", self._open, "Open a saved studio project"))
        head.addWidget(self._tool("Save", self._save, "Save the project (keyframes and output settings)"))
        head.addWidget(self._tool("Save As…", lambda: self._save(ask=True)))
        head.addWidget(self._tool("New", self._new, "Start an empty project"))
        head.addWidget(vdiv())
        self._btn_undo = self._tool("Undo", self.undo, "Undo (⌘Z)")
        self._btn_redo = self._tool("Redo", self.redo, "Redo (⇧⌘Z)")
        head.addWidget(self._btn_undo)
        head.addWidget(self._btn_redo)
        head.addStretch()
        self._summary = label("")
        head.addWidget(self._summary)
        head.addSpacing(8)
        close = QPushButton("✕  Close Studio")
        close.setObjectName("studioClose")
        close.setFixedHeight(26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Back to the archive bar (Esc). The movie stays here while STORM is open; Save keeps it.")
        close.clicked.connect(self._close)
        head.addWidget(close)
        root.addLayout(head)

        # the case clock -------------------------------------------------------
        case = QHBoxLayout()
        case.setSpacing(6)
        tag = label("CASE\nCLOCK", f"color: {CYAN}; font-size: 9px; font-weight: 800; letter-spacing: 1px;")
        tag.setFixedWidth(44)
        tag.setToolTip("The case time on the map. Keyframes record it with the view.")
        case.addWidget(tag)
        self._case = CaseStrip(self, self)
        self._case.seek.connect(self._set_clock)
        case.addWidget(self._case, 1)
        self._case_zoom = QComboBox()
        for text, secs in (("Session", None), ("6 h", 6 * 3600), ("2 h", 2 * 3600), ("30 min", 1800)):
            self._case_zoom.addItem(text, secs)
        self._case_zoom.setFixedSize(76, 24)
        self._case_zoom.setToolTip("How much of the case the strip shows; zoomed in, it follows the clock")
        self._case_zoom.currentIndexChanged.connect(lambda _i: self._case.set_zoom(self._case_zoom.currentData()))
        case.addWidget(self._case_zoom)
        case.addWidget(self._tool("◀ scan", lambda: self._scan(-1), "Previous radar scan ( , )"))
        case.addWidget(self._tool("−1m", lambda: self._nudge_clock(-60)))
        self._clock = QDateTimeEdit()
        self._clock.setTimeZone(QTimeZone.utc())
        self._clock.setDisplayFormat("d MMM  HH:mm:ss 'UTC'")
        self._clock.setButtonSymbols(QDateTimeEdit.ButtonSymbols.NoButtons)
        self._clock.setFixedSize(150, 24)
        self._clock.setToolTip("Type a case time (UTC) and press Return")
        self._clock_typed = False                        # set only by the user's own edits
        self._clock.dateTimeChanged.connect(lambda _d: setattr(self, "_clock_typed", True))
        self._clock.editingFinished.connect(self._clock_edited)
        case.addWidget(self._clock)
        case.addWidget(self._tool("+1m", lambda: self._nudge_clock(60)))
        case.addWidget(self._tool("scan ▶", lambda: self._scan(1), "Next radar scan ( . )"))
        self._local = label("")
        self._local.setFixedWidth(118)
        case.addWidget(self._local)
        root.addLayout(case)

        # transport ------------------------------------------------------------------
        tr = QHBoxLayout()
        tr.setSpacing(5)
        self._btn_add = QPushButton("◆  Add Keyframe")
        self._btn_add.setObjectName("archivePlayButton")
        self._btn_add.setFixedHeight(26)
        self._btn_add.setToolTip("Record the case clock and the map view at the playhead (K).\n"
                                 "At the end of the movie it's added after the last keyframe;\n"
                                 "on a keyframe it replaces that keyframe; elsewhere it splits the move.")
        self._btn_add.clicked.connect(self.add_keyframe)
        tr.addWidget(self._btn_add)
        tr.addWidget(vdiv())
        tr.addWidget(self._tool("⏮", lambda: self.seek(0.0, apply=True), "Start of the movie (Home)"))
        tr.addWidget(self._tool("◆◀", lambda: self.step_keyframe(-1), "Previous keyframe ( [ )"))
        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setObjectName("archivePlayButton")
        self._btn_play.setFixedSize(84, 26)
        self._btn_play.setToolTip("Play the movie from the playhead on the map (Space).\n"
                                  "A preview: the map draws as fast as data arrives; Export renders every frame complete.")
        self._btn_play.clicked.connect(self.toggle_play)
        tr.addWidget(self._btn_play)
        tr.addWidget(self._tool("▶◆", lambda: self.step_keyframe(1), "Next keyframe ( ] )"))
        tr.addWidget(self._tool("⏭", lambda: self.seek(self.project.duration(), apply=True), "End of the movie (End)"))
        self._movie_time = label("", f"color: {TEXT}; font-size: 12px; font-weight: 700;")
        self._movie_time.setMinimumWidth(120)
        tr.addWidget(self._movie_time)
        self._loop = QCheckBox("loop")
        tr.addWidget(self._loop)
        tr.addSpacing(10)
        self._hint = label("", f"color: {DIM}; font-size: 11px;")
        self._hint.setMinimumWidth(40)
        self._hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tr.addWidget(self._hint, 1)
        tr.addWidget(label("Movie"))
        self._btn_slower = self._tool("½× speed", lambda: self._retime(2.0),
                                      "Slow the whole movie down: twice as long, every move and pause in proportion")
        tr.addWidget(self._btn_slower)
        self._btn_faster = self._tool("2× speed", lambda: self._retime(0.5),
                                      "Speed the whole movie up: half as long, every move and pause in proportion")
        tr.addWidget(self._btn_faster)
        self._movie_len = self._spin(0.5, 3600, 1, " s", "The movie's length: type one and every move and pause\n"
                                                         "is stretched or squeezed to fit")
        self._movie_len.valueChanged.connect(self._movie_length_edited)
        tr.addWidget(self._movie_len)
        tr.addSpacing(8)
        tr.addWidget(label("Zoom"))
        tr.addWidget(self._tool("−", lambda: self._timeline.zoom(1 / 1.5), "Zoom out (⌘ + scroll)"))
        tr.addWidget(self._tool("Fit", lambda: self._timeline.fit(), "Show the whole movie"))
        tr.addWidget(self._tool("+", lambda: self._timeline.zoom(1.5), "Zoom in (⌘ + scroll)"))
        root.addLayout(tr)

        # timeline -------------------------------------------------------------------
        self._timeline = MovieTimeline(self, self)
        self._timeline.scrub.connect(lambda m: self.seek(m, apply=True, throttle=True))
        self._timeline.view_changed.connect(self._sync_scrollbar)
        root.addWidget(self._timeline)
        self._scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._scroll.valueChanged.connect(lambda v: self._timeline.set_left(v / 100.0))
        root.addWidget(self._scroll)

        # the selected keyframe, and the output ------------------------------------
        # One keyframe at a time, in plain words: when it is in the case (and
        # a button to retake it from the clock and map), then how it gets to
        # the next keyframe: how fast the case plays, the camera's motion and
        # any pause first. Everything else is on the timeline.
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        insp = QFrame()
        insp.setObjectName("studioBox")
        self._insp_stack = QStackedLayout(insp)
        empty = label("", f"color: {DIM}; font-size: 12px;")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        self._insp_empty = empty
        self._insp_stack.addWidget(empty)
        editor = QWidget()
        g = QGridLayout(editor)
        g.setContentsMargins(8, 6, 8, 8)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)
        self._insp_stack.addWidget(editor)
        self._thumb = QLabel()
        self._thumb.setFixedSize(120, 68)
        self._thumb.setStyleSheet(f"background: #0A0A12; border: 1px solid #2A3044; border-radius: 3px; color: {ORANGE};")
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setToolTip("The map at this keyframe")
        g.addWidget(self._thumb, 0, 0, 2, 1)

        row0 = QHBoxLayout()
        row0.setSpacing(6)
        self._kf_title = label("", f"color: {ORANGE}; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        row0.addWidget(self._kf_title)
        row0.addSpacing(6)
        self._kf_time = QDateTimeEdit()
        self._kf_time.setTimeZone(QTimeZone.utc())
        self._kf_time.setDisplayFormat("d MMM  HH:mm:ss 'UTC'")
        self._kf_time.setButtonSymbols(QDateTimeEdit.ButtonSymbols.NoButtons)
        self._kf_time.setFixedSize(150, 24)
        self._kf_time.setToolTip("When in the case this keyframe is. Type a time and press Return;\n"
                                 "the moves on either side speed up or slow down to match.")
        self._kf_time.editingFinished.connect(self._kf_time_edited)
        row0.addWidget(self._kf_time)
        row0.addSpacing(8)
        self._kf_radar = label("", f"color: {TEXT}; font-size: 11px;")
        self._kf_radar.setToolTip("The radar this keyframe shows, from here on in the movie.\n"
                                  "To change it, pick another radar or product in the RADAR tab,\n"
                                  "then Use current radar.")
        row0.addWidget(self._kf_radar)
        self._btn_use_radar = self._tool("Use current radar", self._use_radar,
                                         "Show the radar on screen now (station, product, tilt) from this keyframe on")
        row0.addWidget(self._btn_use_radar)
        row0.addStretch()
        self._btn_update = self._tool("Retake from clock + map", self.update_selected,
                                      "Make this keyframe the case clock and the map view as they are now")
        row0.addWidget(self._btn_update)
        self._btn_delete = self._tool("Delete", lambda: self.delete_selected(), "Delete this keyframe (⌫)")
        row0.addWidget(self._btn_delete)
        g.addLayout(row0, 0, 1)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self._next_label = label("", f"color: {MUTED}; font-size: 11px; font-weight: 700;")
        row1.addWidget(self._next_label)
        self._speed = QComboBox()
        self._speed.setEditable(True)
        self._speed.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for x in SPEED_PRESETS:
            self._speed.addItem(f"{x}×", x)
        self._speed.setFixedSize(84, 24)
        self._speed.setToolTip("How fast the case plays on the way to the next keyframe:\n"
                               "60× = a minute of the case each second of video. Pick or type one.")
        self._speed.activated.connect(lambda _i: self._speed_edited())
        self._speed.lineEdit().editingFinished.connect(self._speed_edited)
        row1.addWidget(self._speed)
        self._length = self._spin(0.1, 3600, 1, " s", "Seconds of video for the move to the next keyframe")
        self._length.valueChanged.connect(self._length_edited)
        row1.addWidget(self._length)
        self._move_info = _ElidedLabel(f"color: {MUTED}; font-size: 11px;")
        row1.addWidget(self._move_info, 1)
        self._btn_speed_all = self._tool("Use for all", self._speed_to_all, "Give every move in the movie this speed")
        row1.addWidget(self._btn_speed_all)
        row1.addSpacing(8)
        self._easing_label = label("camera")
        row1.addWidget(self._easing_label)
        self._easing = QComboBox()
        for key, text in (("smooth", "smooth"), ("linear", "steady"), ("ease-in", "speeds up"), ("ease-out", "slows down")):
            self._easing.addItem(text, key)
        self._easing.setFixedSize(100, 24)
        self._easing.setToolTip("How the camera pans and zooms to the next keyframe's view\n"
                                "(the clock always runs at a steady rate)")
        self._easing.currentIndexChanged.connect(lambda _i: self._easing_edited())
        row1.addWidget(self._easing)
        row1.addSpacing(8)
        self._hold_label = label("pause")
        row1.addWidget(self._hold_label)
        self._hold = self._spin(0, 120, 1, " s", "Stay still on this keyframe for a moment first\n"
                                                 "(the rest of the movie moves later)")
        self._hold.valueChanged.connect(self._hold_edited)
        row1.addWidget(self._hold)
        g.addLayout(row1, 1, 1)
        bottom.addWidget(insp, 1)

        out = QFrame()
        out.setObjectName("studioBox")
        og = QGridLayout(out)
        og.setContentsMargins(8, 6, 8, 8)
        og.setHorizontalSpacing(6)
        og.setVerticalSpacing(6)
        r0 = QHBoxLayout()
        r0.setSpacing(6)
        self._format = QComboBox()
        for text, key in (("MP4 video", "mp4"), ("GIF", "gif"), ("PNG frames", "png")):
            self._format.addItem(text, key)
        self._format.setFixedSize(96, 24)
        r0.addWidget(self._format)
        self._res = QComboBox()
        self._res.addItems(list(RESOLUTIONS))
        self._res.setFixedSize(140, 24)
        self._res.setToolTip("Rendered natively at this size (the map shows the same area)")
        r0.addWidget(self._res)
        self._fps = QComboBox()
        for f in (24, 30, 60):
            self._fps.addItem(f"{f} fps", f)
        self._fps.setFixedSize(66, 24)
        self._fps.setToolTip("Frames per second: smoothness, not speed. The case plays at each move's speed (×).")
        r0.addWidget(self._fps)
        og.addLayout(r0, 0, 0)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(label("clock"))
        self._step = QComboBox()
        for key, text in (("smooth", "smooth"), ("1 min", "every minute"), ("5 min", "every 5 min"),
                          ("radar scans", "each radar scan")):
            self._step.addItem(text, key)
        self._step.setFixedSize(118, 24)
        self._step.setToolTip("How often the case clock (and the data drawn) changes in the movie.\n"
                              "Stepped, like a radar loop: much quicker to export, since only one frame\n"
                              "per step has new data to draw. Smooth: trails and vehicles move every frame.")
        r1.addWidget(self._step)
        self._ov_time = QCheckBox("time")
        self._ov_time.setToolTip("Burn in the case time (UTC)")
        self._ov_status = QCheckBox("data")
        self._ov_status.setToolTip("Burn in the radar site, scan time and product, and the lidar")
        self._ov_legend = QCheckBox("legend")
        for c in (self._ov_time, self._ov_status, self._ov_legend):
            r1.addWidget(c)
        r1.addStretch()
        og.addLayout(r1, 1, 0)
        btns = QVBoxLayout()
        btns.setSpacing(6)
        self._btn_frame = QPushButton("Save Frame…")
        self._btn_frame.setObjectName("archivePlayButton")
        self._btn_frame.setStyleSheet("QPushButton { background: transparent; color: #FF9F1C; }")
        self._btn_frame.setFixedHeight(24)
        self._btn_frame.setToolTip("A PNG of the map as it is now, with the captions chosen here")
        self._btn_frame.clicked.connect(lambda: self._w._archive_screenshot())
        btns.addWidget(self._btn_frame)
        self._btn_export = QPushButton("Export Movie…")
        self._btn_export.setObjectName("archivePlayButton")
        self._btn_export.setFixedHeight(24)
        self._btn_export.clicked.connect(self._export)
        btns.addWidget(self._btn_export)
        og.addLayout(btns, 0, 1, 2, 1)
        bottom.addWidget(out)
        for w in (self._format, self._res, self._fps, self._step):
            w.currentIndexChanged.connect(self._settings_changed)
        for c in (self._ov_time, self._ov_status, self._ov_legend):
            c.toggled.connect(self._settings_changed)
        for w in (self._kf_time, self._speed, self._length, self._easing, self._hold, self._format, self._res,
                  self._fps, self._step, self._clock, self._case_zoom):
            _wheel_only_when_focused(w)
        root.addLayout(bottom)
        self._load_settings_into_ui()


    def _spin(self, lo, hi, decimals, suffix, tip) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSuffix(suffix)
        s.setToolTip(tip)
        s.setKeyboardTracking(False)
        s.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        s.setFixedSize(66, 24)
        return s

    # ---- showing and leaving ------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._shortcuts:
            self._install_shortcuts()
        for s in self._shortcuts:
            s.setEnabled(True)
        self._radar_watch.start()
        QTimer.singleShot(0, self._sync_scrollbar)
        self.refresh()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.stop()
        self._radar_watch.stop()
        for s in self._shortcuts:
            s.setEnabled(False)

    def _install_shortcuts(self) -> None:
        win = self.window()

        def key(seq, slot):
            sc = QShortcut(QKeySequence(seq), win)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)
        key(Qt.Key.Key_Space, self.toggle_play)
        key("K", self.add_keyframe)
        key("[", lambda: self.step_keyframe(-1))
        key("]", lambda: self.step_keyframe(1))
        key(Qt.Key.Key_Left, lambda: self.nudge(-1 / self.project.fps))
        key(Qt.Key.Key_Right, lambda: self.nudge(1 / self.project.fps))
        key("Shift+Left", lambda: self.nudge(-1.0))
        key("Shift+Right", lambda: self.nudge(1.0))
        key(Qt.Key.Key_Home, lambda: self.seek(0.0, apply=True))
        key(Qt.Key.Key_End, lambda: self.seek(self.project.duration(), apply=True))
        key(Qt.Key.Key_Delete, self.delete_selected)
        key(Qt.Key.Key_Backspace, self.delete_selected)
        key(",", lambda: self._scan(-1))
        key(".", lambda: self._scan(1))

    def _close(self) -> None:
        self.stop()
        self.closed.emit()

    # ---- the case clock -----------------------------------------------------------
    def scan_times(self) -> list[datetime]:
        ac = getattr(self._w, "_archive_controls", None)
        return getattr(ac, "_scan_times", []) if ac is not None else []

    def _set_clock(self, t: datetime) -> None:
        self.stop()
        self._tc.pause()
        self._tc.set_time(t)

    def _nudge_clock(self, seconds: int) -> None:
        self._set_clock(self._tc.current_time + timedelta(seconds=seconds))

    def _scan(self, direction: int) -> None:
        self.stop()
        ac = getattr(self._w, "_archive_controls", None)
        if ac is not None:
            ac._jump_to_scan(direction)

    def _clock_edited(self) -> None:
        """Return (or leaving the box) after typing a time: go there. Leaving
        it untouched changes nothing, even if the clock moved meanwhile."""
        if self._clock_typed:
            self._clock_typed = False
            self._set_clock(from_qdt(self._clock.dateTime()))

    def _on_clock(self, t: datetime) -> None:
        self._case.update()
        if not self._clock.hasFocus():
            start, end = self._tc.window
            self._clock.blockSignals(True)
            self._clock.setDateTimeRange(to_qdt(start), to_qdt(end))
            self._clock.setDateTime(to_qdt(t))
            self._clock.blockSignals(False)
            self._clock_typed = False
        ac = getattr(self._w, "_archive_controls", None)
        self._local.setText(ac._local_label.text() if ac is not None else "")

    # ---- the playhead -------------------------------------------------------------
    def seek(self, m: float, apply: bool = False, throttle: bool = False) -> None:
        self.playhead = min(max(0.0, m), self.project.duration())
        self._timeline.ensure_visible(self.playhead)
        self._timeline.update()
        self._update_movie_time()
        if apply and self.project.keyframes:
            if throttle:
                wait = SCRUB_APPLY_MS - (_time.monotonic() - self._last_apply) * 1000
                if wait > 0:
                    if not self._scrub_timer.isActive():
                        self._scrub_timer.start(int(wait))
                    return
            self._apply_playhead()

    def _apply_playhead(self) -> None:
        if not self.project.keyframes:
            return
        self._last_apply = _time.monotonic()
        when, view = self.project.evaluate(self.playhead)
        self._w.studio_apply_radar(self.project.radar_at(self.playhead))
        self._capture.apply(self.project.stepped(when, self.scan_times()), view)

    def nudge(self, seconds: float) -> None:
        self.stop()
        self.seek(self.playhead + seconds, apply=True)

    def go_to(self, kf: Keyframe | None) -> None:
        if kf is None:
            return
        self.stop()
        self.select(kf)
        self.seek(kf.at, apply=True)

    def step_keyframe(self, direction: int) -> None:
        ks = self.project.sorted()
        if not ks:
            return
        if direction > 0:
            target = next((k for k in ks if k.at > self.playhead + 1e-6), None)
        else:
            target = next((k for k in reversed(ks) if k.at < self.playhead - 1e-6), None)
        if target is not None:
            self.go_to(target)

    # ---- preview playback -----------------------------------------------------------
    def toggle_play(self) -> None:
        if self._play_timer.isActive():
            self.stop()
            return
        if len(self.project.keyframes) < 2:
            return
        if self.playhead >= self.project.duration() - 1e-3:
            self.playhead = 0.0
        self._tc.pause()
        self._play_clock = _time.monotonic()
        self._play_timer.start()
        self._btn_play.setText("❚❚  Pause")
        self._apply_playhead()

    def stop(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._btn_play.setText("▶  Play")

    def _play_tick(self) -> None:
        now = _time.monotonic()
        m = self.playhead + (now - self._play_clock)
        self._play_clock = now
        d = self.project.duration()
        if m >= d:
            if self._loop.isChecked():
                m = 0.0
            else:
                self.seek(d, apply=True)
                self.stop()
                return
        self.seek(m, apply=True)

    # ---- selection --------------------------------------------------------------------
    def select(self, kf: Keyframe | None) -> None:
        self.selected = kf
        self._coalesce = None
        self.refresh()

    # ---- undo ---------------------------------------------------------------------------
    def snapshot(self, coalesce=None) -> None:
        """Remember the project before an edit. Edits with the same coalesce
        key in a row (typing in one field) undo together."""
        if coalesce is not None and coalesce == self._coalesce:
            return
        self._coalesce = coalesce
        self._undo.append((self.project.to_json(), self._selected_index()))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._dirty = True

    def _selected_index(self) -> int:
        ks = self.project.sorted()
        return ks.index(self.selected) if self.selected in ks else -1

    def _restore(self, state) -> None:
        data, index = state
        self.project = Project.from_json(data)
        ks = self.project.sorted()
        self.selected = ks[index] if 0 <= index < len(ks) else None
        self.playhead = min(self.playhead, self.project.duration())
        self._coalesce = None
        self._dirty = True
        self._load_settings_into_ui()
        self.refresh()

    def undo(self) -> None:
        if self._undo:
            self._redo.append((self.project.to_json(), self._selected_index()))
            self._restore(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append((self.project.to_json(), self._selected_index()))
            self._restore(self._redo.pop())

    # ---- keyframes --------------------------------------------------------------------------
    @staticmethod
    def thumb_key(kf: Keyframe) -> str:
        v = kf.view
        return f"{kf.time.isoformat()}|{v.lon:.5f}|{v.lat:.5f}|{v.zoom:.3f}|{v.bearing:.1f}|{v.pitch:.1f}"

    def _take_thumb(self, kf: Keyframe) -> None:
        """A small picture of the map for the keyframe (the map is showing it now)."""
        def grab():
            pix = self._w.map_widget.grab()
            if not pix.isNull():
                self.thumbs[self.thumb_key(kf)] = pix.scaled(QSize(224, 126), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                              Qt.TransformationMode.SmoothTransformation)
                self.refresh()
        QTimer.singleShot(150, grab)

    def add_keyframe(self) -> None:
        """K / Add Keyframe: the case clock and the map view, at the playhead."""
        self.stop()

        def got(view: View):
            when = self._tc.current_time
            p = self.project
            ks = p.sorted()
            at_end = not ks or self.playhead >= p.duration() - 1e-3
            on = p.keyframe_at(self.playhead)
            last = ks[-1] if ks else None
            if at_end and last is not None and when == last.time and _same_view(view, last.view):
                self.flash(
                    f"No change since keyframe {len(ks)}")
                return
            self.snapshot()
            if at_end:
                kf = p.append(when, view, easing=self.selected.easing if self.selected else "smooth")
                what = "added"
            elif on is not None:
                on.time, on.view = when, view
                kf, what = on, "updated"
            else:
                kf = p.add(Keyframe(at=self.playhead, time=when, view=view,
                                    easing=self.selected.easing if self.selected else "smooth"))
                what = "inserted"
            kf.radar = self._w.studio_radar_state()
            self.selected = kf
            self.playhead = kf.at
            self._take_thumb(kf)
            self.edited()
            n = p.sorted().index(kf) + 1
            self.flash(f"Keyframe {n} {what} · {when:%H:%M:%S}Z")
        self._capture.read_view(got)

    def update_selected(self) -> None:
        """Both: the keyframe becomes the clock and map as they are now."""
        kf = self.selected
        if kf is None:
            return

        def got(view):
            self.snapshot()
            kf.time, kf.view = self._tc.current_time, view
            kf.radar = self._w.studio_radar_state()
            self._take_thumb(kf)
            self.edited()
        self._capture.read_view(got)

    def delete_selected(self, ripple: bool = False) -> None:
        kf = self.selected
        if kf is None:
            return
        ks = self.project.sorted()
        i = ks.index(kf)
        self.snapshot()
        self.project.remove(kf, ripple=ripple)
        ks = self.project.sorted()
        self.selected = ks[min(i, len(ks) - 1)] if ks else None
        self.playhead = min(self.playhead, self.project.duration())
        self.edited()

    def duplicate(self, kf: Keyframe) -> None:
        self.snapshot()
        copy = Keyframe(at=kf.at, time=kf.time, view=View(**vars(kf.view)), easing=kf.easing,
                        radar=dict(kf.radar) if kf.radar else None)
        self.project.add(copy)
        self.selected = kf                                    # the original moved after the copy
        self.edited()

    def context_menu(self, kf: Keyframe | None, pos, m: float) -> None:
        menu = QMenu(self)
        if kf is not None:
            n = self.project.sorted().index(kf) + 1
            self.select(kf)
            menu.addAction(f"Keyframe {n} · {kf.time:%H:%M:%S}Z").setEnabled(False)
            menu.addAction("Go to", lambda: self.go_to(kf))
            menu.addAction("Update to clock and map", self.update_selected)
            menu.addAction("Duplicate", lambda: self.duplicate(kf))
            easing = menu.addMenu("Easing")
            for e in EASINGS:
                a = easing.addAction(e, lambda e=e: self._set_easing(kf, e))
                a.setCheckable(True)
                a.setChecked(kf.easing == e)
            menu.addSeparator()
            menu.addAction("Delete", self.delete_selected)
            menu.addAction("Delete and close the gap", lambda: self.delete_selected(ripple=True))
            menu.addSeparator()
        menu.addAction("Move playhead here", lambda: self.seek(m, apply=True))
        menu.addAction("Add keyframe at playhead", self.add_keyframe)
        menu.exec(pos)

    def _set_easing(self, kf: Keyframe, easing: str) -> None:
        self.snapshot()
        kf.easing = easing
        self.edited()

    # ---- inspector edits --------------------------------------------------------------------
    def _segment_index(self) -> int:
        ks = self.project.sorted()
        if self.selected not in ks:
            return -1
        i = ks.index(self.selected)
        return i if i + 1 < len(ks) else -1

    def _kf_time_edited(self) -> None:
        kf = self.selected
        t = from_qdt(self._kf_time.dateTime())
        if kf is None or t == kf.time.replace(microsecond=0):
            return
        self.snapshot()
        kf.time = t
        self.thumbs.pop(self.thumb_key(kf), None)
        self.edited()

    def _easing_edited(self) -> None:
        key = self._easing.currentData()
        if self.selected is not None and self.selected.easing != key:
            self.snapshot(("easing", id(self.selected)))
            self.selected.easing = key
            self.edited(inspector=False)

    def _hold_edited(self, value: float) -> None:
        if self.selected is not None:
            self.snapshot(("hold", id(self.selected)))
            self.project.set_hold(self.selected, value)
            self.edited(inspector=False, keep=self._hold)

    def _length_edited(self, value: float) -> None:
        i = self._segment_index()
        if i >= 0:
            self.snapshot(("length", id(self.selected)))
            self.project.set_segment_length(i, value)
            self.edited(inspector=False, keep=self._length)

    def _typed_speed(self) -> float | None:
        text = self._speed.currentText().replace("×", "").replace("x", "").strip()
        try:
            x = float(text)
        except ValueError:
            return None
        return x if x > 0 else None

    def _speed_edited(self) -> None:
        i = self._segment_index()
        x = self._typed_speed()
        if i < 0 or x is None:
            self._refresh_inspector()
            return
        if abs(abs(self.project.segment_speed(i)) - x) < 1e-6:
            return
        self.snapshot()
        self.project.set_segment_speed(i, x)
        self.edited()

    def _check_radar(self) -> None:
        state = self._w.studio_radar_state()
        if state != self._radar_seen:
            self._radar_seen = state
            self._refresh_inspector(update_fields=False)

    def _retime(self, factor: float) -> None:
        if len(self.project.keyframes) < 2:
            return
        self.snapshot()
        self.project.retime(factor)
        self.playhead *= factor
        self.edited()
        self._timeline.fit()

    def _movie_length_edited(self, seconds: float) -> None:
        d = self.project.duration()
        if d <= 0 or abs(seconds - d) < 0.05:
            return
        self.snapshot(("length", "movie"))
        factor = seconds / d
        self.project.retime(factor)
        self.playhead *= factor
        self.edited(keep=self._movie_len)
        self._timeline.fit()

    def _use_radar(self) -> None:
        kf = self.selected
        state = self._w.studio_radar_state()
        if kf is None or state is None:
            return
        self.snapshot()
        kf.radar = state
        self.edited()
        self.flash(f"Keyframe {self.project.sorted().index(kf) + 1}: {self._radar_text(state)}")

    def _radar_text(self, radar: dict | None) -> str:
        if not radar:
            return ""
        product = str(radar.get("product") or "")
        names = {"reflectivity": "REF", "velocity": "VEL", "spectrum_width": "SW", "differential_reflectivity": "ZDR",
                 "cross_correlation_ratio": "CC", "differential_phase": "PHI"}
        tilt = radar.get("tilt")
        return f"{radar.get('station', '?')} {names.get(product, product)}" + (f" · tilt {tilt + 1}" if tilt else "")

    def scan_interval(self) -> float | None:
        """Typical seconds between the radar's scans (None until known)."""
        scans = self.scan_times()
        if len(scans) < 3:
            return None
        gaps = sorted((b - a).total_seconds() for a, b in zip(scans, scans[1:]))
        return gaps[len(gaps) // 2]

    def scan_pace(self, speed: float) -> str:
        """How long each radar scan is on screen at this speed, in words."""
        gap = self.scan_interval()
        if not gap or speed <= 0:
            return ""
        each = gap / speed
        return (f"a radar scan every {each:.1f} s" if each < 10 else f"a radar scan every {each:.0f} s")

    def _speed_to_all(self) -> None:
        x = self._typed_speed()
        if x is None:
            return
        self.snapshot()
        for i in range(len(self.project.keyframes) - 1):
            self.project.set_segment_speed(i, x)          # camera-only moves keep their length
        self.edited()
        self.flash(f"All moves: {speed_text(x)}")

    def edited(self, inspector: bool = True, scroll: bool = True, keep=None) -> None:
        """The project changed: redraw (the inspector too, except the field being typed in)."""
        self._dirty = True
        self.playhead = min(self.playhead, self.project.duration())
        self.refresh(inspector=inspector, keep=keep)
        if scroll:
            self._sync_scrollbar()

    # ---- output settings ------------------------------------------------------------------
    def _load_settings_into_ui(self) -> None:
        p = self.project
        ws = (self._format, self._res, self._fps, self._step, self._ov_time, self._ov_status, self._ov_legend,
              self._name)
        for w in ws:
            w.blockSignals(True)
        self._format.setCurrentIndex(max(0, self._format.findData(p.format)))
        self._res.setCurrentText(p.resolution)
        self._fps.setCurrentIndex(max(0, self._fps.findData(p.fps)))
        self._step.setCurrentIndex(max(0, self._step.findData(p.time_step)))
        self._ov_time.setChecked(p.overlays.time_stamp)
        self._ov_status.setChecked(p.overlays.status_line)
        self._ov_legend.setChecked(p.overlays.legend)
        self._name.setText(p.name)
        for w in ws:
            w.blockSignals(False)

    def _settings_changed(self, *_a) -> None:
        p = self.project
        p.format = self._format.currentData()
        p.resolution = self._res.currentText()
        p.fps = int(self._fps.currentData())
        p.time_step = self._step.currentData()
        p.overlays.time_stamp = self._ov_time.isChecked()
        p.overlays.status_line = self._ov_status.isChecked()
        p.overlays.legend = self._ov_legend.isChecked()
        self._dirty = True
        self.refresh(inspector=False)

    def _renamed(self, text: str) -> None:
        self.project.name = text.strip() or "movie"
        self._dirty = True
        self.refresh(inspector=False)

    # ---- display ---------------------------------------------------------------------------
    def flash(self, text: str, seconds: float = 5.0) -> None:
        """A message in the transport row for a few seconds (the window's
        status line is in the archive bar, hidden while the studio is open)."""
        self._flash_text = text
        self._hint.setStyleSheet(f"color: {ORANGE}; font-size: 11px; font-weight: 600;")
        self._hint.setText(text)
        self._flash_timer.start(int(seconds * 1000))

    def _count_frames(self) -> None:
        p = self.project
        if len(p.keyframes) < 2:
            return
        draw = p.distinct_frames(self.scan_times())
        total = p.frame_count()
        text = self._summary.text().split("  ·  ⟳")[0]
        self._summary.setText(f"{text}  ·  {draw}/{total} frames to draw")
        self._summary.setToolTip("Frames the export has to wait for the map to draw; the rest repeat the one\n"
                                 "before (the clock is held by the time step and the camera is still).")

    def _end_flash(self) -> None:
        self._flash_text = None
        self._hint.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.refresh(inspector=False)

    def _sync_scrollbar(self) -> None:
        tl = self._timeline
        hidden = tl.content_seconds() - tl.visible_seconds()
        self._scroll.blockSignals(True)
        self._scroll.setRange(0, max(0, int(hidden * 100)))
        self._scroll.setPageStep(int(tl.visible_seconds() * 100))
        self._scroll.setSingleStep(max(1, int(tl.visible_seconds() * 10)))
        self._scroll.setValue(int(tl.left * 100))
        self._scroll.blockSignals(False)
        self._scroll.setVisible(hidden > 0.05)

    def _update_movie_time(self) -> None:
        d = self.project.duration()
        self._movie_time.setText(f"{movie_clock(self.playhead)} / {movie_clock(d)}")

    def refresh(self, inspector: bool = True, keep=None) -> None:
        p = self.project
        ks = p.sorted()
        if self.selected not in ks:
            self.selected = None
        self._timeline.update()
        self._case.update()
        self._update_movie_time()
        self._on_clock(self._tc.current_time)
        n = len(ks)
        name = p.name + (" •" if self._dirty and n else "")
        if n:
            self._summary.setText(f"{name}  ·  {n} keyframe{'s' if n != 1 else ''}  ·  {movie_clock(p.duration())}")
            self._count_timer.start()
        else:
            self._summary.setText("")
        if not self._flash_text:
            self._hint.setText("")                       # only brief messages (flash) show here
        self._btn_play.setEnabled(n >= 2)
        self._btn_export.setEnabled(n >= 2)
        self._btn_export.setToolTip("Render every frame complete, at the size chosen" if n >= 2
                                    else "Add two or more keyframes first")
        for b in (self._btn_slower, self._btn_faster, self._movie_len):
            b.setEnabled(n >= 2)
        if keep is not self._movie_len:
            self._movie_len.blockSignals(True)
            self._movie_len.setValue(max(0.5, p.duration()))
            self._movie_len.blockSignals(False)
        self._btn_undo.setEnabled(bool(self._undo))
        self._btn_redo.setEnabled(bool(self._redo))
        self._refresh_inspector(update_fields=inspector, keep=keep)

    def _refresh_inspector(self, update_fields: bool = True, keep=None) -> None:
        p = self.project
        ks = p.sorted()
        kf = self.selected
        if kf is None:
            self._insp_empty.setText("No keyframe selected" if ks else "No keyframes")
            self._insp_stack.setCurrentIndex(0)
            return
        self._insp_stack.setCurrentIndex(1)
        i = ks.index(kf)
        seg = self._segment_index()
        case_s = (ks[seg + 1].time - kf.time).total_seconds() if seg >= 0 else 0.0
        self._kf_title.setText(f"KEYFRAME {i + 1} OF {len(ks)}")
        thumb = self.thumbs.get(self.thumb_key(kf))
        if thumb is not None:
            self._thumb.setPixmap(thumb.scaled(self._thumb.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                               Qt.TransformationMode.SmoothTransformation))
        else:
            self._thumb.clear()
            self._thumb.setText("◆")
        self._thumb.setToolTip(f"The map at this keyframe: {kf.view.describe()}")
        self._kf_radar.setText(f"radar {self._radar_text(kf.radar)}" if kf.radar else "radar: as on screen")
        self._btn_use_radar.setVisible(self._w.studio_radar_state() not in (None, kf.radar))

        has_move = seg >= 0
        timed = has_move and case_s != 0
        for w in (self._easing, self._easing_label):
            w.setVisible(has_move)
        self._speed.setVisible(timed)
        self._btn_speed_all.setVisible(timed and len(ks) > 2)
        self._length.setVisible(has_move and not timed)
        self._next_label.setVisible(has_move)
        if not has_move:
            self._move_info.setText("")
        elif timed:
            self._next_label.setText(f"TO {seg + 2}")
            pace = self.scan_pace(abs(p.segment_speed(seg)))
            self._move_info.setText(f"{p.segment_length(seg):.1f} s" + (" · backward" if case_s < 0 else ""),
                                    f"{p.segment_length(seg):.1f} s of video for {case_span_text(case_s)} of case"
                                    + (", backward" if case_s < 0 else "") + (f"; {pace}" if pace else ""))
        else:
            self._next_label.setText(f"TO {seg + 2} · camera only")
            self._move_info.setText("")
        if not update_fields:
            return
        start, end = self._tc.window
        for w in (self._kf_time, self._easing, self._hold, self._length, self._speed):
            if w is keep:
                continue
            w.blockSignals(True)
            if w is self._kf_time:
                w.setDateTimeRange(to_qdt(start), to_qdt(end))
                w.setDateTime(to_qdt(kf.time))
            elif w is self._easing:
                w.setCurrentIndex(max(0, w.findData(kf.easing)))
            elif w is self._hold:
                w.setValue(kf.hold)
            elif w is self._length and has_move:
                w.setValue(p.segment_length(seg))
            elif w is self._speed and timed:
                x = abs(p.segment_speed(seg))
                j = w.findData(round(x)) if abs(x - round(x)) < 0.05 else -1
                if j >= 0:
                    w.setCurrentIndex(j)
                else:
                    w.setEditText(speed_text(x))
            w.blockSignals(False)

    # ---- files ---------------------------------------------------------------------------
    def folder(self) -> Path:
        day = self._w._track_session_day() if hasattr(self._w, "_track_session_day") else self._tc.current_time.date()
        return STUDIO_ROOT / f"{day:%Y%m%d}"

    def _save(self, ask: bool = False) -> bool:
        path = self._path
        if ask or path is None:
            chosen, _ = QFileDialog.getSaveFileName(self, "Save Studio Project",
                                                    str(self.folder() / f"{self.project.name}.json"),
                                                    "STORM studio project (*.json)")
            if not chosen:
                return False
            path = Path(chosen if chosen.endswith(".json") else chosen + ".json")
        self.project.save(path)
        self._path = path
        self._dirty = False
        self.flash(f"Saved {path.name}")
        self.refresh(inspector=False)
        return True

    def _discard_ok(self) -> bool:
        if not self._dirty or not self.project.keyframes:
            return True
        reply = QMessageBox.question(self, "Video Studio", f"Save the changes to “{self.project.name}” first?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if reply == QMessageBox.StandardButton.Save:
            return self._save()
        return reply == QMessageBox.StandardButton.Discard

    def _open(self) -> None:
        if not self._discard_ok():
            return
        folder = self.folder()
        path, _ = QFileDialog.getOpenFileName(self, "Open Studio Project",
                                              str(folder if folder.is_dir() else STUDIO_ROOT),
                                              "STORM studio project (*.json)")
        if path:
            self.load(Path(path))

    def load(self, path: Path) -> bool:
        try:
            project = Project.load(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Open Studio Project", f"{path.name}: {exc}")
            return False
        self.stop()
        self.project = project
        self._path = path
        self._dirty = False
        self._undo.clear()
        self._redo.clear()
        self.selected = project.sorted()[0] if project.keyframes else None
        self.playhead = 0.0
        self._load_settings_into_ui()
        self.refresh()
        self._timeline.fit()
        if project.keyframes:
            self._apply_playhead()
        return True

    def _new(self) -> None:
        if not self._discard_ok():
            return
        self.stop()
        self.project = Project()
        self._path = None
        self._dirty = False
        self._undo.clear()
        self._redo.clear()
        self.selected = None
        self.playhead = 0.0
        self._load_settings_into_ui()
        self.refresh()
        self._timeline.fit()

    def _export(self) -> None:
        self.stop()
        self.export_requested.emit()


def _same_view(a: View, b: View) -> bool:
    return all(abs(x - y) < 1e-6 for x, y in zip(vars(a).values(), vars(b).values()))
