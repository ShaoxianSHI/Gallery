"""年视图：4 × 3 张小月，日期格里铺照片（365 个数字表达不了「这一天拍了几十张」的密度，
ADR 0033 决策 2）—— 有照片的那天铺**最早**的一张当封面 + 日期徽标。

**每张小月里只有它自己那个月的日子有照片、「今天」和高亮** —— 12 张小月都含上下月占位，不按
:func:`~gallery.core.library.calendar.in_month` 判就会把邻月的照片和张数算进来。
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QMouseEvent, QPainter

from gallery.core.i18n import t, t_plural
from gallery.core.library.calendar import (
    cell_cover,
    count_days,
    in_month,
    month_days,
    month_matrix,
    weekday_headers,
)
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, day_label
from gallery.ui.calendar.painting import (
    draw_text,
    fill_rounded,
    font_px,
    outline_rounded,
    paint_photo,
)
from gallery.ui.calendar.thumb_store import ThumbStore

_BOX_GAP = 22.0
_ROW_GAP = 3.0

_TITLE_HEIGHT = 20.0
_WEEKDAY_HEIGHT = 14.0
_TITLE_FONT_PX = 13
_COUNT_FONT_PX = 11
_WEEKDAY_FONT_PX = 10
_DATE_FONT_PX = 11

#: 格子圆角比别处小一号：年视图的格子只有二十几个像素。
_CELL_RADIUS = 4.0

#: 一张缩略图要缩到多长（年视图的格子最小，缩得也最小）。
THUMB_EDGE = 48


class YearView(LevelView):
    """一整年，12 张小月。"""

    def __init__(self, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__("year", thumbs, parent)

    def _grid_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(18, 6, -18, -10)

    def _box(self, index: int) -> QRectF:
        """第 ``index`` 张小月（0 = 一月）落在哪。"""
        grid = self._grid_rect()
        cell_w = (grid.width() - 3 * _BOX_GAP) / 4
        cell_h = (grid.height() - 2 * _BOX_GAP) / 3
        row, column = divmod(index, 4)
        return QRectF(
            grid.left() + column * (cell_w + _BOX_GAP),
            grid.top() + row * (cell_h + _BOX_GAP),
            cell_w,
            cell_h,
        )

    def _day_cells(self, box: QRectF) -> list[QRectF]:
        """一张小月里 42 格的落点（顺序与 :func:`month_matrix` 一致）。"""
        column = box.width() / 7
        top = box.top() + _TITLE_HEIGHT + _WEEKDAY_HEIGHT + _ROW_GAP
        row_height = max(11.0, (box.bottom() - top) / 6)
        return [
            QRectF(
                box.left() + index % 7 * column,
                top + index // 7 * row_height,
                column - 2,
                row_height - 2,
            )
            for index in range(42)
        ]

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = self.begin()
        # 12 张的日期矩阵一帧里各算一遍：``month_matrix`` 每次要重建 42 个 ``datetime.date``。
        year = self._anchor.year
        matrices = [month_matrix(datetime.date(year, index + 1, 1)) for index in range(12)]
        for index in range(12):
            self._paint_month(painter, self._box(index), index + 1, matrices[index])
        painter.end()

    def _paint_month(
        self, painter: QPainter, box: QRectF, month: int, days: list[datetime.date]
    ) -> None:
        colors = self.colors
        first = datetime.date(self._anchor.year, month, 1)
        # **只数这一月自己的日子**（``month_days``）：那 42 天含邻月占位，数进来就是把 10 月的照片
        # 算进标题写着「9 月」的那一张。
        count = count_days(self._by_day, month_days(first))
        title = QRectF(box.left(), box.top(), box.width(), _TITLE_HEIGHT)
        draw_text(
            painter,
            title,
            t("calendar.month_label", month=month),
            font_px(_TITLE_FONT_PX, QFont.Weight.DemiBold),
            colors.text,
        )
        draw_text(
            painter,
            title,
            t_plural("calendar.photo_count", count) if count else "",
            font_px(_COUNT_FONT_PX),
            colors.muted if count else colors.faint,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        column = box.width() / 7
        head = QRectF(box.left(), box.top() + _TITLE_HEIGHT, box.width(), _WEEKDAY_HEIGHT)
        for index, label in enumerate(weekday_headers()):
            draw_text(
                painter,
                QRectF(box.left() + index * column, head.top(), column, head.height()),
                label[-1],  # 「星期一」只取最后一个字：这么宽的一列表头写不下四个字
                font_px(_WEEKDAY_FONT_PX),
                colors.faint,
                Qt.AlignmentFlag.AlignCenter,
                elide=False,
            )
        for day, cell in zip(days, self._day_cells(box), strict=True):
            self._paint_day(painter, cell, day, first)

    def _paint_day(
        self, painter: QPainter, cell: QRectF, day: datetime.date, month: datetime.date
    ) -> None:
        colors = self.colors
        # **占位格只有日期号**：那几格是邻月的日子，摆照片就是把邻月的照片摆进这一张小月。
        own = in_month(day, month)
        # **点一天就高亮那一天，且只画在它自己那个月的格子里**（10 月 1 日在「9 月」末尾也出现
        # 一次，两处都涂就成了「选中了两天」）。格子只有二十几像素，底色之外还要补一圈描边。
        selected = own and day == self._selected
        if selected:
            fill_rounded(painter, cell, colors.selection, _CELL_RADIUS)
        photos = self.photos_of(day) if own else ()
        cover = cell_cover(photos)
        if cover is not None:
            paint_photo(painter, cell, self.photo(cover), radius=_CELL_RADIUS)
            size = min(cell.height() * 0.66, 16.0)
            badge = QRectF(cell.center().x() - size / 2, cell.center().y() - size / 2, size, size)
            fill_rounded(painter, badge, self.background, size / 2)
            draw_text(
                painter,
                badge,
                str(day.day),
                font_px(max(8, int(size * 0.72)), QFont.Weight.DemiBold),
                colors.text,
                Qt.AlignmentFlag.AlignCenter,
                elide=False,
            )
        else:
            draw_text(
                painter,
                cell,
                str(day.day),
                font_px(_DATE_FONT_PX),
                colors.text if selected else colors.muted if own else colors.faint,
                Qt.AlignmentFlag.AlignCenter,
                elide=False,
            )
        if selected:
            outline_rounded(painter, cell, colors.accent, 2.0, radius=_CELL_RADIUS)
        # **「今天」也只在它自己那个月里圈一次**：矩阵含上下月占位，两处都圈就成了两个「今天」。
        elif own and day == self.today():
            outline_rounded(painter, cell, colors.accent, 1.6, radius=_CELL_RADIUS)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        point = event.position()
        grid = self._grid_rect()
        if not grid.contains(point):
            return
        cell_w = (grid.width() - 3 * _BOX_GAP) / 4
        cell_h = (grid.height() - 2 * _BOX_GAP) / 3
        row = int((point.y() - grid.top()) // (cell_h + _BOX_GAP))
        column = int((point.x() - grid.left()) // (cell_w + _BOX_GAP))
        if not (0 <= row < 3 and 0 <= column < 4):
            return
        index = row * 4 + column + 1
        month = datetime.date(self._anchor.year, index, 1)
        picked = self._day_at(point, self._box(index - 1), month)
        # **占位格点了不动**：那一格是邻月的日子，而这一屏是这一年的 12 个月。
        if picked is not None and in_month(picked, month):
            self.set_selected(picked)
            self.select_cell(self.photos_of(picked), day_label(picked))

    def _day_at(self, point: object, box: QRectF, month: datetime.date) -> datetime.date | None:  # type: ignore[no-untyped-def]
        for day, cell in zip(month_matrix(month), self._day_cells(box), strict=True):
            if cell.contains(point):  # type: ignore[arg-type]
                return day
        return None

    def visible_refs(self) -> list[PhotoRef]:
        """有照片的那些天，各要一张封面 —— **每天只要一张**，且只问 ``month_days`` 里的日子。

        **挑哪一张一律问 ``cell_cover``**：各层各取一头的话，同一天按 `Y` 与按 `M` 看到的是两张脸。
        """
        refs: list[PhotoRef] = []
        for index in range(12):
            for day in month_days(datetime.date(self._anchor.year, index + 1, 1)):
                cover = cell_cover(self.photos_of(day))
                if cover is not None:
                    refs.append(cover)
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


__all__ = ["THUMB_EDGE", "YearView"]
