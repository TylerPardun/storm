"""Find archive dates by campaign, years, months and instruments.

Opened from the archive launch panel ("Find dates…"). It reads the same
availability snapshot as the launch calendar, so it fills in as the catalog
scan runs. Choosing a date sets the archive start date; the time is kept.
"""
from __future__ import annotations

import calendar
from collections import Counter
from datetime import date

from PyQt6.QtCore import QDate, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from archive.catalog import CAMPAIGN_YEARS, platforms_by_site
from ui.launch.icons import combo_down_arrow_qss
from ui.launch.styles import _DIALOG_STYLE

FIRST_YEAR = 1999
_MESONET = "FOFS Mobile Mesonet"


# -- filtering (no widgets) ---------------------------------------------------

def dates_by_site(snapshot, sites) -> dict[str, frozenset[date]]:
    """Every date with data for each site (all of the site's products)."""
    if snapshot is None:
        return {}
    return {
        site: frozenset(d for p in plats for d in getattr(snapshot.platforms.get(p.platform_id), "dates", ()))
        for site, plats in sites.items()
    }


def match_dates(by_site, chosen=(), require_all=True, years=None, months=(), campaign_years=None):
    """Dates that pass every filter.

    chosen: sites that must have data -- every one of them (require_all) or
    at least one. With none chosen, any site counts. years is an inclusive
    (first, last) pair; months a set of month numbers (empty = all).
    """
    pools = [by_site.get(s, frozenset()) for s in chosen] or list(by_site.values())
    if not pools:
        return frozenset()
    dates = frozenset.intersection(*pools) if chosen and require_all else frozenset().union(*pools)
    return frozenset(
        d for d in dates
        if (years is None or years[0] <= d.year <= years[1])
        and (not months or d.month in months)
        and (campaign_years is None or d.year in campaign_years)
    )


def sites_on(day, by_site) -> list[str]:
    return sorted(s for s, dates in by_site.items() if day in dates)


# -- widgets --------------------------------------------------------------------

class _MonthGrid(QWidget):
    """Years (newest first) by months; each cell shaded by its number of
    matching dates. Clicking a cell picks that month; clicking it again
    clears the pick."""

    month_clicked = pyqtSignal(object)        # (year, month) or None

    LABEL_W, CELL_W, CELL_H, HEAD_H = 40, 30, 20, 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._years: list[int] = []
        self._counts: Counter = Counter()
        self._picked = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, years, counts, picked):
        self._years, self._counts, self._picked = list(years), counts, picked
        self.updateGeometry()
        self.setMinimumHeight(self.sizeHint().height())
        self.update()

    def sizeHint(self):
        return QSize(self.LABEL_W + 12 * self.CELL_W, self.HEAD_H + max(1, len(self._years)) * self.CELL_H)

    def _cell_at(self, pos):
        col = int((pos.x() - self.LABEL_W) // self.CELL_W)
        row = int((pos.y() - self.HEAD_H) // self.CELL_H)
        if pos.x() < self.LABEL_W or pos.y() < self.HEAD_H or not (0 <= col < 12 and 0 <= row < len(self._years)):
            return None
        return self._years[row], col + 1

    def mouseMoveEvent(self, event):
        cell = self._cell_at(event.position())
        if cell:
            n = self._counts.get(cell, 0)
            self.setToolTip(f"{calendar.month_name[cell[1]]} {cell[0]}: {n} date{'s' if n != 1 else ''}")
        else:
            self.setToolTip("")

    def mousePressEvent(self, event):
        cell = self._cell_at(event.position())
        if cell and (self._counts.get(cell) or cell == self._picked):
            self.month_clicked.emit(None if cell == self._picked else cell)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor("#8E97AB"))
        for m in range(12):
            r = QRectF(self.LABEL_W + m * self.CELL_W, 0, self.CELL_W, self.HEAD_H)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, calendar.month_abbr[m + 1][0])
        top = max(self._counts.values(), default=0)
        for row, year in enumerate(self._years):
            y = self.HEAD_H + row * self.CELL_H
            p.setPen(QColor("#8E97AB"))
            p.drawText(QRectF(0, y, self.LABEL_W - 6, self.CELL_H),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(year))
            for m in range(12):
                n = self._counts.get((year, m + 1), 0)
                tile = QRectF(self.LABEL_W + m * self.CELL_W, y, self.CELL_W, self.CELL_H).adjusted(1.5, 1.5, -1.5, -1.5)
                if n:
                    # #123C50 (one date) up to #00CFFF (the busiest month)
                    t = (n / top) ** 0.5 if top else 0
                    fill = QColor(int(0x12 + t * (0x00 - 0x12)), int(0x3C + t * (0xCF - 0x3C)), int(0x50 + t * (0xFF - 0x50)))
                else:
                    fill = QColor("#141424")
                p.setPen(QColor("#E8EAF0") if (year, m + 1) == self._picked else Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(tile, 3, 3)
                if n:
                    p.setPen(QColor("#0A0A0F") if t > 0.6 else QColor("#E8EAF0"))
                    p.drawText(tile, Qt.AlignmentFlag.AlignCenter, str(n))
        p.end()


_FINDER_STYLE = """
QPushButton#monthBtn, QPushButton#segBtn {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 4px;
    color: #8E97AB;
    font-size: 11px;
    padding: 4px 0px;
}
QPushButton#segBtn { padding: 3px 10px; }
QPushButton#monthBtn:hover, QPushButton#segBtn:hover { border-color: #2A4A5E; }
QPushButton#monthBtn:checked, QPushButton#segBtn:checked {
    background-color: #123C50;
    border-color: #00CFFF;
    color: #9BE8FF;
}
QPushButton#linkBtn {
    background: transparent; border: none; color: #00CFFF;
    font-size: 11px; font-weight: 600; padding: 0;
}
QPushButton#linkBtn:hover { color: #5CE1FF; text-decoration: underline; }
QPushButton#launchBtn:disabled { background-color: #1A1A2E; color: #5A5B6A; }
QListWidget#browseDatesList::indicator {
    width: 12px; height: 12px;
    border: 1px solid #2A2A3E; border-radius: 3px;
    background-color: #12121E;
}
QListWidget#browseDatesList::indicator:checked {
    background-color: #00CFFF; border-color: #00CFFF;
}
QLabel#groupLabel { color: #5A5B6A; font-size: 10px; font-weight: 600; }
QScrollArea#gridScroll { background: transparent; border: none; }
QWidget#gridHolder { background: transparent; }
"""


class FindDatesDialog(QDialog):
    """Filters on the left; a year-by-month count grid and the matching
    dates (with the instruments that have data on each) on the right."""

    date_chosen = pyqtSignal(object)          # datetime.date

    def __init__(self, parent=None, sites=None):
        super().__init__(parent)
        self.setWindowTitle("Find archive dates")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setStyleSheet(_DIALOG_STYLE + combo_down_arrow_qss() + _FINDER_STYLE)
        self._sites = sites if sites is not None else platforms_by_site()
        self._snapshot = None
        self._by_site: dict[str, frozenset[date]] = {}
        self._picked_month = None
        self._current = None
        self._build_ui()
        self.resize(780, 560)

    # -- layout -------------------------------------------------------------

    def _field(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._field("CAMPAIGN"))
        self._campaign = QComboBox()
        self._campaign.addItem("Any campaign", None)
        for name, years in CAMPAIGN_YEARS.items():
            self._campaign.addItem(f"{name}  ({', '.join(map(str, years))})", name)
        left.addWidget(self._campaign)

        left.addSpacing(4)
        left.addWidget(self._field("YEARS"))
        years_row = QHBoxLayout()
        self._year_from, self._year_to = QComboBox(), QComboBox()
        for combo in (self._year_from, self._year_to):
            for year in range(FIRST_YEAR, QDate.currentDate().year() + 1):
                combo.addItem(str(year), year)
            combo.setMaxVisibleItems(10)
        self._year_to.setCurrentIndex(self._year_to.count() - 1)
        years_row.addWidget(self._year_from, 1)
        to = QLabel("to")
        to.setObjectName("hint")
        years_row.addWidget(to)
        years_row.addWidget(self._year_to, 1)
        left.addLayout(years_row)

        left.addSpacing(4)
        months_head = QHBoxLayout()
        months_head.addWidget(self._field("MONTHS"))
        months_head.addStretch()
        self._months_clear = self._link("All", lambda: self._set_months(()))
        months_head.addWidget(self._months_clear)
        left.addLayout(months_head)
        grid = QGridLayout()
        grid.setSpacing(4)
        self._month_btns = []
        for m in range(1, 13):
            btn = QPushButton(calendar.month_abbr[m])
            btn.setObjectName("monthBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.toggled.connect(self._refresh)
            grid.addWidget(btn, (m - 1) // 6, (m - 1) % 6)
            self._month_btns.append(btn)
        left.addLayout(grid)

        left.addSpacing(4)
        inst_head = QHBoxLayout()
        inst_head.addWidget(self._field("INSTRUMENTS"))
        inst_head.addStretch()
        self._mode_all = self._seg("All of")
        self._mode_any = self._seg("Any of")
        self._mode_all.setToolTip("Dates when every checked instrument has data")
        self._mode_any.setToolTip("Dates when at least one checked instrument has data")
        self._mode_all.setChecked(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn in (self._mode_all, self._mode_any):
            group.addButton(btn)
            inst_head.addWidget(btn)
        self._mode_all.toggled.connect(self._refresh)
        left.addLayout(inst_head)
        self._instruments = QListWidget()
        self._instruments.setObjectName("browseDatesList")
        self._instruments.setMinimumWidth(250)
        self._instruments.itemChanged.connect(self._refresh)
        self._build_instrument_items()
        left.addWidget(self._instruments, 1)
        inst_foot = QHBoxLayout()
        self._inst_hint = QLabel("None checked: any instrument")
        self._inst_hint.setObjectName("hint")
        inst_foot.addWidget(self._inst_hint, 1)
        inst_foot.addWidget(self._link("Clear", self._clear_instruments))
        left.addLayout(inst_foot)
        root.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(6)
        self._status = QLabel()
        self._status.setObjectName("fieldLabel")
        right.addWidget(self._status)
        self._grid = _MonthGrid()
        self._grid.month_clicked.connect(self._on_month_clicked)
        holder = QWidget()
        holder.setObjectName("gridHolder")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._grid)
        hl.addStretch()
        scroll = self._grid_scroll = QScrollArea()
        scroll.setObjectName("gridScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(self._grid.sizeHint().width() + 14)
        right.addWidget(scroll)

        days_head = QHBoxLayout()
        self._days_label = QLabel()
        self._days_label.setObjectName("fieldLabel")
        days_head.addWidget(self._days_label, 1)
        self._month_clear = self._link("Show all months", lambda: self._on_month_clicked(None))
        days_head.addWidget(self._month_clear)
        right.addLayout(days_head)
        self._days = QListWidget()
        self._days.setObjectName("browseDatesList")
        self._days.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._days.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._days.currentItemChanged.connect(self._update_use_button)
        self._days.itemDoubleClicked.connect(self._use_selected)
        right.addWidget(self._days, 1)

        foot = QHBoxLayout()
        hint = QLabel("Double-click a date, or select one and press Use this date.")
        hint.setObjectName("hint")
        foot.addWidget(hint, 1)
        self._use_btn = QPushButton("Use this date")
        self._use_btn.setObjectName("launchBtn")
        self._use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_btn.clicked.connect(self._use_selected)
        foot.addWidget(self._use_btn)
        right.addLayout(foot)
        root.addLayout(right, 1)

        self._campaign.currentIndexChanged.connect(self._on_campaign_changed)
        self._year_from.currentIndexChanged.connect(self._on_years_changed)
        self._year_to.currentIndexChanged.connect(self._on_years_changed)
        self._refresh()

    def _link(self, text, slot):
        btn = QPushButton(text)
        btn.setObjectName("linkBtn")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _seg(self, text):
        btn = QPushButton(text)
        btn.setObjectName("segBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _build_instrument_items(self):
        mesonet = sorted(s for s, plats in self._sites.items() if {p.family for p in plats} == {_MESONET})
        others = sorted(s for s in self._sites if s not in mesonet)
        self._instruments.blockSignals(True)
        for heading, sites in (("RADAR, LIDAR, PROFILERS, UAS", others), ("MOBILE MESONET", mesonet)):
            if not sites:
                continue
            head = QListWidgetItem(heading)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setForeground(QColor("#5A5B6A"))
            font = head.font()
            font.setPixelSize(10)
            font.setBold(True)
            head.setFont(font)
            self._instruments.addItem(head)
            for site in sites:
                item = QListWidgetItem(site)
                item.setData(Qt.ItemDataRole.UserRole, site)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setToolTip(", ".join(sorted({p.family for p in self._sites[site]})))
                self._instruments.addItem(item)
        self._instruments.blockSignals(False)

    # -- state ----------------------------------------------------------------

    def set_snapshot(self, snapshot):
        self._snapshot = snapshot
        self._by_site = dates_by_site(snapshot, self._sites)
        self._refresh()

    def set_current_date(self, day):
        """Highlight the launch panel's date in the list when it matches."""
        self._current = day
        self._refresh()

    def chosen_sites(self) -> list[str]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self._site_items()
                if it.checkState() == Qt.CheckState.Checked]

    def _site_items(self):
        return [it for it in (self._instruments.item(i) for i in range(self._instruments.count()))
                if it.data(Qt.ItemDataRole.UserRole)]

    def _months(self):
        return {m for m, btn in enumerate(self._month_btns, 1) if btn.isChecked()}

    def _set_months(self, months):
        for m, btn in enumerate(self._month_btns, 1):
            btn.blockSignals(True)
            btn.setChecked(m in months)
            btn.blockSignals(False)
        self._refresh()

    def _clear_instruments(self):
        self._instruments.blockSignals(True)
        for it in self._site_items():
            it.setCheckState(Qt.CheckState.Unchecked)
        self._instruments.blockSignals(False)
        self._refresh()

    def _year_range(self):
        return self._year_from.currentData(), self._year_to.currentData()

    def _on_campaign_changed(self):
        years = CAMPAIGN_YEARS.get(self._campaign.currentData())
        lo, hi = (min(years), max(years)) if years else (FIRST_YEAR, QDate.currentDate().year())
        for combo, year in ((self._year_from, lo), (self._year_to, hi)):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(year))
            combo.blockSignals(False)
        self._refresh()

    def _on_years_changed(self):
        lo, hi = self._year_range()
        if lo > hi:                          # keep the range the right way round
            other = self._year_to if self.sender() is self._year_from else self._year_from
            other.blockSignals(True)
            other.setCurrentIndex(other.findData(lo if other is self._year_to else hi))
            other.blockSignals(False)
        self._refresh()

    def _on_month_clicked(self, cell):
        self._picked_month = cell
        self._refresh()

    def _filters(self, with_instruments=True):
        chosen = self.chosen_sites() if with_instruments else ()
        return dict(chosen=chosen, require_all=self._mode_all.isChecked(), years=self._year_range(),
                    months=self._months(), campaign_years=CAMPAIGN_YEARS.get(self._campaign.currentData()))

    def matching_dates(self) -> frozenset[date]:
        return match_dates(self._by_site, **self._filters())

    def _refresh(self, *_):
        if not hasattr(self, "_days"):
            return                            # still building the UI
        dates = self.matching_dates()
        chosen = self.chosen_sites()

        # instrument counts under the other filters
        base = self._filters(with_instruments=False)
        self._instruments.blockSignals(True)
        for it in self._site_items():
            site = it.data(Qt.ItemDataRole.UserRole)
            n = len(match_dates(self._by_site, chosen=(site,), **{k: v for k, v in base.items() if k != "chosen"}))
            it.setText(f"{site}   {n}" if self._snapshot is not None else site)
            it.setForeground(QColor("#E8EAF0") if n or it.checkState() == Qt.CheckState.Checked else QColor("#5A5B6A"))
        self._instruments.blockSignals(False)
        self._inst_hint.setText(
            "None checked: any instrument" if not chosen
            else f"{len(chosen)} checked: dates with {'all' if self._mode_all.isChecked() else 'any'} of them")

        counts = Counter((d.year, d.month) for d in dates)
        if self._picked_month and not counts.get(self._picked_month):
            self._picked_month = None
        # only years with a match (newest first), so a narrow search reads at a glance
        years = sorted({d.year for d in dates}, reverse=True)
        self._grid.set_data(years, counts, self._picked_month)
        self._grid_scroll.setFixedHeight(min(self._grid.sizeHint().height(), 220) + 2)

        snap = self._snapshot
        if snap is None:
            self._status.setText("Checking the archive catalogs…")
        else:
            scanning = f" · still checking catalogs ({snap.checked}/{snap.total})" if snap.checked < snap.total else ""
            self._status.setText(f"{len(dates)} matching dates in {len({d.year for d in dates})} years{scanning}")

        shown = sorted((d for d in dates if not self._picked_month
                        or (d.year, d.month) == self._picked_month), reverse=True)
        if self._picked_month:
            y, m = self._picked_month
            self._days_label.setText(f"{calendar.month_name[m].upper()} {y} — {len(shown)} DATES")
        else:
            self._days_label.setText("ALL MATCHING DATES")
        self._month_clear.setVisible(self._picked_month is not None)
        self._fill_days(shown, chosen)

    def _fill_days(self, shown, chosen):
        selected = self._days.currentItem().data(Qt.ItemDataRole.UserRole) if self._days.currentItem() else None
        keep = selected if selected in shown else (self._current if self._current in shown else None)
        self._days.blockSignals(True)
        self._days.clear()
        for d in shown:
            on = sites_on(d, self._by_site)
            # checked instruments first, then the rest
            on.sort(key=lambda s: (s not in chosen, s))
            item = QListWidgetItem(f"{d.isoformat()}     {' · '.join(on)}")
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setToolTip(f"{d.strftime('%A %d %B %Y')}\n" + "\n".join(on))
            self._days.addItem(item)
            if d == keep:
                self._days.setCurrentItem(item)
        self._days.blockSignals(False)
        if self._days.currentItem():
            self._days.scrollToItem(self._days.currentItem())
        self._update_use_button()

    def _update_use_button(self, *_):
        self._use_btn.setEnabled(self._days.currentItem() is not None)

    def _use_selected(self, *_):
        item = self._days.currentItem()
        if item is None:
            return
        self.date_chosen.emit(item.data(Qt.ItemDataRole.UserRole))
        self.close()
