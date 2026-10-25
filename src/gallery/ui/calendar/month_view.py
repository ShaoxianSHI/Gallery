"""月视图：7 列 × 6 行，每格**一张照片**（当天最早的那张当封面，点它看那天全部）。

**今天只写成蓝字，不加药丸**（周视图里今天才是蓝圆点，别抄混）；**占位格只有日期号、没有
照片** —— 那 6 × 7 是为了对齐星期几铺满的，而这一屏回答的是「这个月拍了什么」。
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QMouseEvent, QPainter

from gallery.core.library.calendar import (
    cell_cover,
    in_month,
    month_matrix,
    week_index,
    weekday_headers,
)
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, day_label
from gallery.ui.calendar.layout import fitted_size
from gallery.ui.calendar.painting import dot, draw_text, fill_rounded, font_px, paint_cell_photo
from gallery.ui.calendar.thumb_store import ThumbStore

_HEAD_HEIGHT = 26.0
_CELL_GAP = 6.0
_NUMBER_HEIGHT = 20.0

_HEAD_FONT_PX = 11
_DATE_FONT_PX = 12

#: 一张缩略图要缩到多长（一格大约两百多像素宽）。
THUMB_EDGE = 288


class MonthView(LevelView):
    """一个月，6 × 7 格。"""

    def __init__(self, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__("month", thumbs, parent)

    def _grid_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(16, 6, -16, -14)

    def _column_width(self) -> float:
        """一列多宽（格间距在 :meth:`_day_cell` 里才扣）。"""
        return self._grid_rect().width() / 7

    def _day_cell(self, index: int) -> QRectF:
        grid = self._grid_rect()
        column = self._column_width()
        body_top = grid.top() + _HEAD_HEIGHT + 1
        row_height = (grid.bottom() - body_top) / 6
        row, column_index = divmod(index, 7)
        return QRectF(
            grid.left() + column_index * column + _CELL_GAP / 2,
            body_top + row * row_height + _CELL_GAP / 2,
            column - _CELL_GAP,
            row_height - _CELL_GAP,
        )

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = self.begin()
        colors = self.colors
        grid = self._grid_rect()
        body_top = grid.top() + _HEAD_HEIGHT
        days = month_matrix(self._anchor)

        for index, label in enumerate(weekday_headers()):
            draw_text(
                painter,
                QRectF(
                    grid.left() + index * grid.width() / 7,
                    grid.top(),
                    grid.width() / 7,
                    _HEAD_HEIGHT,
                ),
                label,
                font_px(_HEAD_FONT_PX),
                # 「今天那一列」按周日打头的列号问：``weekday()`` 是周一 0，索引列名会差一格。
                colors.text if index == week_index(self.today()) else colors.muted,
                Qt.AlignmentFlag.AlignCenter,
            )
        painter.setPen(colors.outline)
        painter.drawLine(int(grid.left()), int(body_top), int(grid.right()), int(body_top))

        for index, day in enumerate(days):
            cell = self._day_cell(index)
            if day == self._selected:
                fill_rounded(painter, cell.adjusted(-2, -2, 2, 2), colors.selection, 6)
            self._paint_day(painter, cell, day)
        painter.end()

    def _paint_day(self, painter: QPainter, cell: QRectF, day: datetime.date) -> None:
        colors = self.colors
        outside = not in_month(day, self._anchor)
        draw_text(
            painter,
            QRectF(cell.left() + 1, cell.top(), 28, 18),
            str(day.day),
            font_px(_DATE_FONT_PX, QFont.Weight.Medium),
            colors.accent if day == self.today() else (colors.faint if outside else colors.text),
            elide=False,
        )
        # **占位格到此为止**：那几格是邻月的日子，摆照片就是把邻月的照片摆进这一月。
        if outside:
            return
        photos = self.photos_of(day)
        cover = cell_cover(photos)
        if cover is None:
            return

        # **一格一张**：挑哪一张问核心层的 ``cell_cover``，与年 / 周两层同一个来源。
        area = QRectF(
            cell.left(),
            cell.top() + _NUMBER_HEIGHT,
            cell.width(),
            cell.height() - _NUMBER_HEIGHT,
        )
        image = self.photo(cover)
        if image is None:
            # 缩略图还没到：画一个居中的小圆点。**别铺满格灰块** —— 照片到了是缩在格子里摆的，
            # 比整格小一圈，两帧摆在一起就是「闪一下，尺寸还在变」。
            dot(painter, area.center(), 2.5, colors.faint)
            return
        width, height = fitted_size(image.width(), image.height(), area.width(), area.height())
        fitted = QRectF(
            area.center().x() - width / 2, area.center().y() - height / 2, width, height
        )
        paint_cell_photo(
            painter,
            fitted,
            image,
            count=len(photos),
            chip=colors.chip,
            text=colors.text,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        grid = self._grid_rect()
        column = grid.width() / 7
        body_top = grid.top() + _HEAD_HEIGHT + 1
        row_height = (grid.bottom() - body_top) / 6
        point = event.position()
        column_index = int((point.x() - grid.left()) // column)
        row = int((point.y() - body_top) // row_height)
        if 0 <= column_index < 7 and 0 <= row < 6:
            day = month_matrix(self._anchor)[row * 7 + column_index]
            # **占位格点了不动**：展开邻月那天的照片会让人以为「这一格明明是空的」。
            if not in_month(day, self._anchor):
                return
            self.set_selected(day)
            self.select_cell(self.photos_of(day), day_label(day))

    def visible_refs(self) -> list[PhotoRef]:
        """一屏放得下的那些天，各要一张缩略图（**占位格不要**：那里不摆照片，解出来永远画不上）。"""
        refs: list[PhotoRef] = []
        for index, day in enumerate(month_matrix(self._anchor)):
            cell = self._day_cell(index)
            if not cell.intersects(self.rect()):
                continue
            if not in_month(day, self._anchor):
                continue
            photos = self.photos_of(day)
            cover = cell_cover(photos)
            if cover is not None:
                refs.append(cover)
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


__all__ = ["THUMB_EDGE", "MonthView"]
