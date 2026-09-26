"""Table view of the storm track, for exact times and positions -- MESO-VIEW's
TrackPointDialog (mm_review/app.py), synchronized with the map.

Edits stay in the table until Apply, which commits them to the track as one
undoable edit. Selecting a row selects that point on the map (and the other
way round); double-clicking a row moves the clock to the point's time. When
the track changes on the map while the table has unapplied edits, the table
keeps them and says so rather than discarding them.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.storm_track import TrackPoint
from ui.theme import ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_COLUMNS = ("ID", "Time (UTC)", "Latitude", "Longitude", "Source", "Radar")
_EDITABLE = (1, 2, 3)
_BAD = QColor("#7A2230")


def parse_time(text: str, session_day: date) -> datetime | None:
    """'YYYY-MM-DD HH:MM[:SS]', or just 'HH:MM[:SS]' on the session date."""
    text = text.strip()
    for fmt in (TIME_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return datetime.combine(session_day, t, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def rows_to_points(rows: list[tuple[int | None, str, str, str]], originals: dict[int, TrackPoint],
                   session_day: date) -> tuple[list[TrackPoint], dict[tuple[int, int], str]]:
    """Validate table rows (point_id or None for a new row, time, lat, lon).
    Returns the track and, per bad (row, column), the reason. Two points at
    the same second are ambiguous -- the track keeps one point per time --
    so both are marked."""
    errors: dict[tuple[int, int], str] = {}
    parsed = []
    for r, (point_id, time_text, lat_text, lon_text) in enumerate(rows):
        when = parse_time(time_text, session_day)
        if when is None:
            errors[(r, 1)] = "Time must be YYYY-MM-DD HH:MM:SS (or HH:MM:SS on this date)"
        try:
            lat = float(lat_text)
            if not -90 <= lat <= 90:
                raise ValueError
        except ValueError:
            lat = None
            errors[(r, 2)] = "Latitude must be a number from -90 to 90"
        try:
            lon = float(lon_text)
            if not -180 <= lon <= 180:
                raise ValueError
        except ValueError:
            lon = None
            errors[(r, 3)] = "Longitude must be a number from -180 to 180"
        parsed.append((point_id, when, lat, lon))

    seen: dict[datetime, int] = {}
    for r, (_, when, _, _) in enumerate(parsed):
        if when is None:
            continue
        if when in seen:
            for row in (seen[when], r):
                errors[(row, 1)] = f"Two points at {when:%H:%M:%S} -- keep one point per time"
        seen.setdefault(when, r)
    if errors:
        return [], errors

    next_id = max([*originals, *(pid for pid, *_ in parsed if pid is not None)], default=0) + 1
    points = []
    for point_id, when, lat, lon in parsed:
        old = originals.get(point_id) if point_id is not None else None
        if old is None:
            points.append(TrackPoint(point_id=next_id, time=when, lat=lat, lon=lon, source="table"))
            next_id += 1
        elif (old.time, round(old.lat, 5), round(old.lon, 5)) != (when, round(lat, 5), round(lon, 5)):
            points.append(replace(old, time=when, lat=lat, lon=lon, source="table_edited"))
        else:
            points.append(old)
    return sorted(points, key=lambda p: p.time), {}


class TrackTableDialog(QDialog):
    """Signals
    -------
    apply_requested(list)   the table's validated track, to commit as one edit
    point_selected(int)     a row was selected: select that point on the map
    jump_requested(int)     a row was double-clicked: move the clock to it
    """

    apply_requested = pyqtSignal(list)
    point_selected = pyqtSignal(int)
    jump_requested = pyqtSignal(int)

    def __init__(self, session_day: date, parent=None):
        super().__init__(parent)
        self._session_day = session_day
        self._originals: dict[int, TrackPoint] = {}
        self._dirty = False
        self._syncing = False
        self._default_time = None
        self.setWindowTitle("Storm Track Points")
        self.setModal(False)
        self.setMinimumSize(620, 360)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_BASE}; }}
            QLabel {{ background: transparent; }}
            QTableWidget {{ background-color: {BG_ELEVATED}; color: #E8EAF0;
                            gridline-color: #2E2E4E; font-size: 11px; }}
            QTableWidget::item:selected {{ background-color: {ACCENT}; color: #0A0A0F; }}
            QHeaderView::section {{ background-color: {BG_BASE}; color: {TEXT_MUTED};
                                    border: none; padding: 3px 4px; font-size: 10px; }}
        """)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        hint = QLabel(
            f"Session {self._session_day:%Y-%m-%d}. Times in UTC as YYYY-MM-DD HH:MM:SS, or HH:MM:SS "
            "on the session date. Select a row to select the point; double-click to go to its time. "
            "Edits apply when you click Apply (undo with Ctrl+Z)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        v.addWidget(hint)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(self._on_double_clicked)
        v.addWidget(self._table, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #FFB4B4; font-size: 10px;")
        v.addWidget(self._status)

        row = QHBoxLayout()
        self._btn_add = QPushButton("ADD ROW")
        self._btn_add.setToolTip("New row at the current time; fill in its position")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_delete = QPushButton("DELETE ROW")
        self._btn_delete.clicked.connect(self._delete_row)
        self._btn_revert = QPushButton("REVERT")
        self._btn_revert.setToolTip("Discard unapplied edits and show the track as it is")
        self._btn_revert.clicked.connect(lambda: self.load(list(self._originals.values()), force=True))
        self._btn_apply = QPushButton("APPLY")
        self._btn_apply.setObjectName("primaryButton")
        self._btn_apply.clicked.connect(self.apply)
        for b in (self._btn_add, self._btn_delete, self._btn_revert):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self._btn_apply)
        v.addLayout(row)
        self._set_dirty(False)

    # ---- data in/out -------------------------------------------------
    def set_default_time(self, when: datetime) -> None:
        """Time given to a new row (the archive clock)."""
        self._default_time = when

    def load(self, points: list[TrackPoint], *, force: bool = False) -> None:
        """Show the track. Unapplied edits are kept unless `force`."""
        if self._dirty and not force:
            self._originals = {p.point_id: p for p in points}
            self._status.setText("The track changed on the map. Apply keeps your table edits; "
                                 "Revert shows the track as it is now.")
            return
        self._originals = {p.point_id: p for p in points}
        self._syncing = True
        self._table.setRowCount(0)
        for p in sorted(points, key=lambda p: p.time):
            self._append(p.point_id, p.time.astimezone(timezone.utc).strftime(TIME_FORMAT),
                         f"{p.lat:.5f}", f"{p.lon:.5f}", p.source,
                         " ".join(x for x in (p.radar_site, p.product_label or p.product) if x))
        self._syncing = False
        self._status.setText("")
        self._set_dirty(False)

    def _append(self, point_id, time_text, lat_text, lon_text, source="", radar="") -> int:
        r = self._table.rowCount()
        self._table.insertRow(r)
        values = ("" if point_id is None else str(point_id), time_text, lat_text, lon_text, source, radar)
        for c, value in enumerate(values):
            item = QTableWidgetItem(value)
            if c not in _EDITABLE:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if c == 0:
                item.setData(Qt.ItemDataRole.UserRole, point_id)
            self._table.setItem(r, c, item)
        return r

    def rows(self) -> list[tuple[int | None, str, str, str]]:
        return [
            (self._table.item(r, 0).data(Qt.ItemDataRole.UserRole),
             self._table.item(r, 1).text(), self._table.item(r, 2).text(), self._table.item(r, 3).text())
            for r in range(self._table.rowCount())
        ]

    def apply(self) -> bool:
        points, errors = rows_to_points(self.rows(), self._originals, self._session_day)
        self._syncing = True
        for r in range(self._table.rowCount()):
            for c in _EDITABLE:
                item = self._table.item(r, c)
                item.setBackground(_BAD if (r, c) in errors else QColor(0, 0, 0, 0))
                item.setToolTip(errors.get((r, c), ""))
        self._syncing = False
        if errors:
            self._status.setText(f"{len(errors)} cell(s) need fixing (hover for why); nothing applied.")
            return False
        self._set_dirty(False)
        self.apply_requested.emit(points)
        return True

    # ---- selection sync ---------------------------------------------
    def select_point(self, point_id: int | None) -> None:
        """Mirror a selection made on the map."""
        self._syncing = True
        self._table.clearSelection()
        for r in range(self._table.rowCount()):
            if self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) == point_id:
                self._table.selectRow(r)
                self._table.scrollToItem(self._table.item(r, 1))
                break
        self._syncing = False

    def _selected_point_id(self):
        rows = self._table.selectionModel().selectedRows()
        return self._table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _on_selection_changed(self) -> None:
        point_id = self._selected_point_id()
        if not self._syncing and point_id is not None:
            self.point_selected.emit(point_id)

    def _on_double_clicked(self, row: int, _column: int) -> None:
        point_id = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if point_id is not None and point_id in self._originals:
            self.jump_requested.emit(point_id)

    # ---- editing ----------------------------------------------------
    def _on_item_changed(self, _item) -> None:
        if not self._syncing:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._btn_apply.setEnabled(dirty)
        self._btn_revert.setEnabled(dirty)

    def _add_row(self) -> None:
        when = self._default_time.strftime(TIME_FORMAT) if self._default_time else ""
        self._syncing = True
        r = self._append(None, when, "", "", "table")
        self._syncing = False
        self._set_dirty(True)
        self._table.setCurrentCell(r, 2)
        self._table.editItem(self._table.item(r, 2))

    def _delete_row(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self._table.removeRow(rows[0].row())
            self._set_dirty(True)
