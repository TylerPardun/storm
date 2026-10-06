"""Choose what goes into a case package: storm tracks plus each type of
data the session loaded, all included by default (core/case_package.py),
and the time frame the radar, lidar and satellite scans are limited to."""
from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import QDateTime, Qt, QTimeZone
from PyQt6.QtWidgets import (
    QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout,
)

from core.case_package import DATA_TYPES, in_time_frame
from ui.theme import BG_BASE, TEXT_MUTED


def _size(num_bytes: int) -> str:
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.1f} GB"
    if num_bytes >= 1e6:
        return f"{num_bytes / 1e6:.1f} MB"
    return f"{max(num_bytes, 0) / 1e3:.0f} KB"


def _qdt(t: datetime) -> QDateTime:
    return QDateTime.fromSecsSinceEpoch(int(t.timestamp()), QTimeZone.utc())


def _py(q: QDateTime) -> datetime:
    return datetime.fromtimestamp(q.toSecsSinceEpoch(), timezone.utc)


class CaseExportDialog(QDialog):
    """sources: the session's provenance records; tracks: how many storm
    tracks the date has; session: the session window (start, end);
    default_frame: the time frame offered first (start, end); listed: scan
    files the session's catalogs name but it hadn't loaded
    (case_package.listed_files)."""

    def __init__(self, sources: list[dict], tracks: int, session: tuple[datetime, datetime],
                 default_frame: tuple[datetime, datetime], parent=None, listed: list[dict] = ()):
        super().__init__(parent)
        self.setWindowTitle("Export Case Package")
        self.setStyleSheet(
            f"QDialog {{ background-color: {BG_BASE}; }} QLabel {{ background: transparent; }}"
            "QDateTimeEdit { background-color: #12121E; border: 1px solid #2A3045; border-radius: 4px;"
            " padding: 3px 6px; font-size: 11px; }"
        )
        self._sources = sources
        self._listed = list(listed)
        self._session = session
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        self.setToolTip("The case settings and the list of every source file (with its checksum) are always "
                        "included.\nAnything left out is downloaded from its source when the package is opened.")

        # time frame (UTC)
        frame_title = QLabel("TIME FRAME (UTC)")
        frame_title.setStyleSheet("color: #8E97AB; font-size: 9px; letter-spacing: 1px;")
        v.addWidget(frame_title)
        frame_row = QHBoxLayout()
        frame_row.setSpacing(6)
        self._start = self._time_edit(default_frame[0])
        self._end = self._time_edit(default_frame[1])
        to = QLabel("to")
        to.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        whole = QPushButton("Whole session")
        whole.clicked.connect(self._whole_session)
        frame_row.addWidget(self._start)
        frame_row.addWidget(to)
        frame_row.addWidget(self._end)
        frame_row.addStretch(1)
        frame_row.addWidget(whole)
        v.addLayout(frame_row)
        frame_title.setToolTip("Radar, lidar and satellite scans are kept for this span;\n"
                               "daily files and everything else are packed whole.")
        self._listed_box = QCheckBox()
        self._listed_box.setChecked(True)
        self._listed_box.setToolTip("Files the session's catalogs list for this time frame but that weren't "
                                    "loaded yet, so the case opens offline with nothing missing")
        self._listed_box.toggled.connect(self._update_counts)
        self._listed_box.setVisible(bool(self._listed))
        v.addWidget(self._listed_box)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        self._boxes: dict[str, QCheckBox] = {}
        self._details: dict[str, QLabel] = {}
        self._sizes: dict[str, int] = {}
        kinds = [k for k in DATA_TYPES if any(s["kind"] == k for s in [*sources, *self._listed])]
        rows = [("tracks", "Storm tracks")] + [(k, DATA_TYPES[k]) for k in kinds]
        for r, (kind, label) in enumerate(rows):
            box = QCheckBox(label)
            count = tracks if kind == "tracks" else 1
            box.setChecked(count > 0)
            box.setEnabled(count > 0)
            box.toggled.connect(self._update_total)
            self._boxes[kind] = box
            grid.addWidget(box, r, 0)
            detail = QLabel()
            detail.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
            detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._details[kind] = detail
            grid.addWidget(detail, r, 1)
        self._details["tracks"].setText(f"{tracks} file{'s' if tracks != 1 else ''}")
        self._sizes["tracks"] = 0
        v.addLayout(grid)

        toggles = QDialogButtonBox()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Select none")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toggles.addButton(all_btn, QDialogButtonBox.ButtonRole.ActionRole)
        toggles.addButton(none_btn, QDialogButtonBox.ButtonRole.ActionRole)
        v.addWidget(toggles)

        self._total = QLabel()
        self._total.setStyleSheet("font-size: 11px;")
        v.addWidget(self._total)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._export = buttons.addButton("Export…", QDialogButtonBox.ButtonRole.AcceptRole)
        self._export.setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._start.dateTimeChanged.connect(self._update_counts)
        self._end.dateTimeChanged.connect(self._update_counts)
        self._update_counts()

    def _time_edit(self, t: datetime) -> QDateTimeEdit:
        edit = QDateTimeEdit(_qdt(t))
        edit.setTimeZone(QTimeZone.utc())
        edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        edit.setButtonSymbols(QDateTimeEdit.ButtonSymbols.NoButtons)   # the theme draws no arrows; type or scroll
        edit.setDateTimeRange(_qdt(self._session[0]), _qdt(self._session[1]))
        return edit

    def _whole_session(self) -> None:
        self._start.setDateTime(_qdt(self._session[0]))
        self._end.setDateTime(_qdt(self._session[1]))

    def time_frame(self) -> tuple[datetime, datetime]:
        return _py(self._start.dateTime()), _py(self._end.dateTime())

    def _update_counts(self, *_args) -> None:
        frame = self.time_frame()
        listed_in_frame = in_time_frame([*self._sources, *self._listed], frame)      # once, not per file
        listed = [e for e in self._listed if e["url"] in listed_in_frame]
        self._listed_box.setText(f"Include scans not yet loaded "
                                 f"({len(listed)} file{'s' if len(listed) != 1 else ''} · "
                                 f"{_size(sum(e.get('bytes') or 0 for e in listed))})")
        sources = self._sources + (self._listed if self.include_listed() else [])
        in_frame = in_time_frame(sources, frame)
        for kind, detail in self._details.items():
            if kind == "tracks":
                continue
            files = [s for s in sources if s["kind"] == kind and s["url"] in in_frame]
            size = sum(s.get("bytes") or 0 for s in files)
            self._sizes[kind] = size
            detail.setText(f"{len(files)} file{'s' if len(files) != 1 else ''}" + (f" · {_size(size)}" if size else ""))
        self._update_total()

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            if box.isEnabled():
                box.setChecked(checked)

    def _update_total(self, *_args) -> None:
        start, end = self.time_frame()
        if end <= start:
            self._total.setText("The time frame's end must be after its start.")
            self._export.setEnabled(False)
            return
        self._export.setEnabled(True)
        total = sum(self._sizes[k] for k, box in self._boxes.items() if box.isChecked())
        self._total.setText(f"Package size about {_size(total)} (before compression)")

    def include_listed(self) -> bool:
        return bool(self._listed) and self._listed_box.isChecked()

    def choices(self) -> tuple[bool, set[str], tuple[datetime, datetime] | None]:
        """(include storm tracks, data kinds to include, time frame -- None
        when it spans the whole session)"""
        kinds = {k for k, box in self._boxes.items() if box.isChecked() and k != "tracks"}
        frame = self.time_frame()
        if frame[0] <= self._session[0] and frame[1] >= self._session[1]:
            frame = None
        return self._boxes["tracks"].isChecked(), kinds, frame
