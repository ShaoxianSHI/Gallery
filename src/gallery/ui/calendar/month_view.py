"""月视图：7 列 × 6 行，每格一叠「照片条」。

照 Google Calendar 月视图的骨架（表头七个星期名、周末两列淡底、每格左上角是日期，
下面一格一格列条目，放不下就写「还有 N 张」），条目的内容换成一张缩略图 + 时间 + 标题。

**今天只写成蓝字，不加药丸** —— Google 月视图是这么画的（它的周视图里今天才是蓝圆点，
两处不一样，别抄混）。
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QMouseEvent, QPainter

from gallery.core.library.calendar import WEEKDAY_HEADERS, month_matrix
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, short_title
from gallery.ui.calendar.painting import (
    draw_text,
    fill_rounded,
    font_px,
    paint_photo,
)
from gallery.ui.calendar.thumbs import ThumbStore

_HEAD_HEIGHT = 26.0
_CELL_GAP = 6.0
_CHIP_HEIGHT = 19.0
_CHIP_GAP = 3.0
_CHIP_THUMB = 15.0

_HEAD_FONT_PX = 11
_DATE_FONT_PX = 11
_CHIP_FONT_PX = 11
_MORE_FONT_PX = 10

#: 一张缩略图要缩到多长（月视图的条目里那张只有十五个像素）。
THUMB_EDGE = 32


class MonthView(LevelView):
    """一个月，6 × 7 格。"""

    def __init__(self, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__("month", thumbs, parent)

    # ---- 几何 -------------------------------------------------------------
    def _grid_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(16, 6, -16, -14)

    def _column_width(self) -> float:
        return self._grid_rect().width() / 7

    def _day_cell(self, index: int) -> QRectF:
        grid = self._grid_rect()
        column = grid.width() / 7
        body_top = grid.top() + _HEAD_HEIGHT + 1
        row_height = (grid.bottom() - body_top) / 6
        row, column_index = divmod(index, 7)
        return QRectF(
            grid.left() + column_index * column + _CELL_GAP / 2,
            body_top + row * row_height + _CELL_GAP / 2,
            column - _CELL_GAP,
            row_height - _CELL_GAP,
        )

    # ---- 绘制 -------------------------------------------------------------
    def paintEvent(self, _event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = self.begin()
        colors = self.colors
        grid = self._grid_rect()
        body_top = grid.top() + _HEAD_HEIGHT
        days = month_matrix(self._anchor)

        for index in range(len(days)):
            if index % 7 >= 5:  # 周末两列淡底
                painter.fillRect(
                    QRectF(
                        grid.left() + index % 7 * grid.width() / 7,
                        body_top,
                        grid.width() / 7,
                        grid.bottom() - body_top,
                    ),
                    colors.weekend,
                )
        for index, label in enumerate(WEEKDAY_HEADERS):
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
                colors.text if index == self._anchor.weekday() else colors.muted,
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
        draw_text(
            painter,
            QRectF(cell.left() + 1, cell.top(), 28, 18),
            str(day.day),
            font_px(_DATE_FONT_PX, QFont.Weight.Medium),
            colors.accent
            if day == self.today()
            else (colors.text if day.month == self._anchor.month else colors.faint),
            elide=False,
        )
        photos = self.photos_of(day)
        if not photos:
            return
        top = cell.top() + 21
        step = _CHIP_HEIGHT + _CHIP_GAP
        room = max(0, int((cell.bottom() - top) // step))
        for ref in photos[:room]:
            chip = QRectF(cell.left(), top, cell.width(), _CHIP_HEIGHT)
            fill_rounded(painter, chip, colors.chip, 4)
            paint_photo(
                painter,
                QRectF(chip.left() + 2, chip.top() + 2, _CHIP_THUMB, _CHIP_HEIGHT - 4),
                self.photo(ref),
                radius=3,
            )
            draw_text(
                painter,
                chip.adjusted(_CHIP_THUMB + 6, 0, -4, 0),
                short_title(ref.path),
                font_px(_CHIP_FONT_PX),
                colors.text,
            )
            top += step
        if len(photos) > room:
            draw_text(
                painter,
                QRectF(cell.left(), top - 1, cell.width(), 16),
                f"还有 {len(photos) - room} 张",
                font_px(_MORE_FONT_PX),
                colors.accent,
            )

    # ---- 交互 -------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        grid = self._grid_rect()
        column = grid.width() / 7
        body_top = grid.top() + _HEAD_HEIGHT + 1
        row_height = (grid.bottom() - body_top) / 6
        point = event.position()
        column_index = int((point.x() - grid.left()) // column)
        row = int((point.y() - body_top) // row_height)
        if 0 <= column_index < 7 and 0 <= row < 6:
            self.select_day(month_matrix(self._anchor)[row * 7 + column_index], descend=True)

    # ---- 缩略图窗口 -------------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """一屏放得下的那些条目，各要一张缩略图。"""
        refs: list[PhotoRef] = []
        for index, day in enumerate(month_matrix(self._anchor)):
            cell = self._day_cell(index)
            if not cell.intersects(self.rect()):
                continue
            photos = self.photos_of(day)
            refs.extend(photos[:4])
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


__all__ = ["THUMB_EDGE", "MonthView"]
