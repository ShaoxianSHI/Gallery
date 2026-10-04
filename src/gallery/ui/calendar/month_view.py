"""月视图：7 列 × 6 行，每格**一张照片**。

照 Google Calendar 月视图的骨架（表头七个星期名、周末两列淡底、每格左上角是日期，
下面是这个月的第一张照片铺满整格），而格子里**只画一张** —— 一格一叠条目看着像
待办清单，而这里是照片：每天摆一张封面，想看那天全部就点它。

一格放得下几张由 `layout.fitted_size` 按**照片自己的比例**算，与年视图同一手法。

**今天只写成蓝字，不加药丸** —— Google 月视图是这么画的（它的周视图里今天才是蓝圆点，
两处不一样，别抄混）。
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QMouseEvent, QPainter

from gallery.core.library.calendar import WEEKDAY_HEADERS, month_matrix
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, day_label
from gallery.ui.calendar.layout import CARD_RADIUS, fitted_size
from gallery.ui.calendar.painting import (
    dot,
    draw_text,
    fill_rounded,
    font_px,
    paint_photo,
)
from gallery.ui.calendar.thumbs import ThumbStore

_HEAD_HEIGHT = 26.0
_CELL_GAP = 6.0
_NUMBER_HEIGHT = 20.0
_COUNT_CHIP = 34.0

_HEAD_FONT_PX = 11
_DATE_FONT_PX = 12
_COUNT_FONT_PX = 10

#: 一张缩略图要缩到多长（一格大约两百多像素宽）。
THUMB_EDGE = 288


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

        # **一格一张**：当天最早的那张（按拍摄时间排好之后的第一张）铺满日期下面那块，
        # 比例按它自己的来 —— 竖幅照片上下留边、横向照片左右留边，与网格视图同一手法。
        area = QRectF(
            cell.left(),
            cell.top() + _NUMBER_HEIGHT,
            cell.width(),
            cell.height() - _NUMBER_HEIGHT,
        )
        image = self.photo(photos[0])
        if image is not None:
            width, height = fitted_size(image.width(), image.height(), area.width(), area.height())
            fitted = QRectF(
                area.center().x() - width / 2, area.center().y() - height / 2, width, height
            )
            paint_photo(painter, fitted, image, radius=CARD_RADIUS)
            if len(photos) > 1:
                chip = QRectF(
                    fitted.right() - _COUNT_CHIP - 4, fitted.bottom() - 18, _COUNT_CHIP, 14
                )
                fill_rounded(painter, chip, colors.chip, 7)
                draw_text(
                    painter,
                    chip,
                    f"{len(photos)} 张",
                    font_px(_COUNT_FONT_PX),
                    colors.text,
                    Qt.AlignmentFlag.AlignCenter,
                    elide=False,
                )
        else:
            # 缩略图还没到：画一个安静的占位（一个居中的小圆点），不留空白
            dot(painter, area.center(), 2.5, colors.faint)

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
            day = month_matrix(self._anchor)[row * 7 + column_index]
            self.set_selected(day)
            self.select_cell(self.photos_of(day), day_label(day))

    # ---- 缩略图窗口 -------------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """一屏放得下的那些天，各要一张缩略图（每天就一张）。"""
        refs: list[PhotoRef] = []
        for index, day in enumerate(month_matrix(self._anchor)):
            cell = self._day_cell(index)
            if not cell.intersects(self.rect()):
                continue
            photos = self.photos_of(day)
            if photos:
                refs.append(photos[0])
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


__all__ = ["THUMB_EDGE", "MonthView"]
