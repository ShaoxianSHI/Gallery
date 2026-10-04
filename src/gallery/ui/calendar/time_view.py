"""周视图与日视图：一张按小时铺开的时间轴。

两层共用同一套排版，**差别只在 :func:`~gallery.ui.calendar.layout.row_height` 一处**：

- 周视图：一列的宽度按格子比例推出一行的高度，于是**每小时恰好一格** ——
  「一个小时一张照片」不是一条写死的规则，而是放不下第二格的自然结果；
- 日视图：行高封顶（:data:`~gallery.ui.calendar.layout.DAY_ROW_CAP`），
  宽度全用来并排放几张，而且那一排**居中**（用户要的：一小时放 3 张时右边不该空一条，
  一小时放 9 张时几乎看不出来 —— 两种都齐）。

**没有拍摄时间的照片不在这里**：它们不落到任何一天，画在哪儿是另一个话题
（ADR 0033 决策 4）。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from gallery.core.library.calendar import WEEKDAY_NAMES, count_hours, week_start
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, short_title
from gallery.ui.calendar.layout import (
    CELL_GAP,
    HOURS_PER_DAY,
    cell_size,
    cells_and_offset,
    row_height,
)
from gallery.ui.calendar.painting import (
    begin,
    draw_text,
    fill_rounded,
    font_px,
    paint_photo,
    scrim,
)
from gallery.ui.calendar.thumbs import ThumbStore

#: 左窄条（写钟点）多宽。
GUTTER = 56.0
#: 表头（星期与日期）多高。
HEADER_HEIGHT = 56.0

_HOUR_FONT_PX = 10
_WEEKDAY_FONT_PX = 11
_DATE_FONT_PX = 15
_CELL_TITLE_FONT_PX = 11
_BADGE_FONT_PX = 10

#: 缩略图按周视图那一格的大小要（约一百七十像素）；日视图那一格更小，用不着重取。
THUMB_EDGE = 192


class TimeView(LevelView):
    """周 / 日两层共用的壳：固定表头 + 可滚动的时间轴。"""

    def __init__(self, level: str, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__(level, thumbs, parent)
        self._grid = _TimeGrid(self)
        area = QScrollArea()
        area.setWidget(self._grid)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        self._header = _TimeHeader(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(area, 1)
        self._area = area
        self._scrolling = False
        # **滚动要重新报一次窗口**：可见的那几小时变了，要的缩略图也就变了。
        # 去抖 80 ms：一次快速滚动会连发几十次 ``valueChanged``，而每一轮都要重算
        # 一遍「现在看得见哪几张」并向流水线报一次（网格那边是同样的处理）。
        self._scroll_notify = QTimer(self)
        self._scroll_notify.setSingleShot(True)
        self._scroll_notify.setInterval(80)
        self._area.verticalScrollBar().valueChanged.connect(self._scroll_notify.start)
        self._scroll_notify.timeout.connect(self.window_changed)

    # ---- 几何 -------------------------------------------------------------
    def days(self) -> list[datetime.date]:
        if self.level == "week":
            start = week_start(self._anchor)
            return [start + datetime.timedelta(days=offset) for offset in range(7)]
        return [self._anchor]

    def photo_count(self) -> int:
        return count_hours(self._by_hour, self.days())

    def column_width(self) -> float:
        return self.content_width() / max(1, len(self.days()))

    def content_width(self) -> float:
        """时间轴上留给各列的宽度（扣掉左边写钟点的那一窄条）。"""
        return max(1.0, self._grid.width() - GUTTER)

    def content_left(self) -> float:
        return GUTTER

    def row_height(self) -> float:
        return row_height(self.level, self.column_width())

    def photos_in_hour(self, day: datetime.date, hour: int) -> Sequence[PhotoRef]:
        return self._by_hour.get((day, hour), ())

    def now_row(self) -> float:
        """今天那一格里，「现在」落在第几行（0 = 00:00 那一行的顶）。

        **今天不是真的今天时画不出来**（截图与测试里「今天」被指到别的日子）：
        那时落在第一行顶上，而不是拿真实时钟去画一根不在这一天的线。
        """
        if datetime.date.today() != self._today:
            return 0.0
        moment = datetime.datetime.now().time()
        return moment.hour + moment.minute / 60.0

    # ---- 位置 -------------------------------------------------------------
    def set_anchor(self, anchor: datetime.date) -> None:
        super().set_anchor(anchor)
        if self.level == "day":
            self.set_selected(anchor)
        self._reveal_photos()

    def set_selected(self, day: datetime.date) -> None:
        super().set_selected(day)
        self._header.update()
        self._grid.update()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._sync_grid_height()
        self._reveal_photos()

    def _sync_grid_height(self) -> None:
        self._grid.setMinimumHeight(int(HOURS_PER_DAY * self.row_height()) + 8)

    def _reveal_photos(self) -> None:
        """停在第一张照片所在的那个小时上方一点。

        时间轴有 24 行、两千多像素，让用户自己滚过去等于把「这天有照片」这件事藏起来。
        **等一轮布局再定位**：那之前滚动条的最大值还是 0，设了也没用。
        """
        if self._scrolling:
            return
        self._scrolling = True
        QTimer.singleShot(0, self._apply_scroll)

    def _apply_scroll(self) -> None:
        self._scrolling = False
        self._sync_grid_height()
        visible = set(self.days())
        hours = [hour for (day, hour) in self._by_hour if day in visible]
        bar = self._area.verticalScrollBar()
        if not hours:
            bar.setValue(0)
            return
        row = self.row_height()
        target = int(max(0, min(hours) - 1) * row)
        bar.setValue(max(0, min(target, bar.maximum())))
        self._grid.update()

    # ---- 缩略图窗口 -------------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """滚到看得见的那几小时里的照片，每小时最多要一格那么多。

        **底部至少留四行**：刚换层时视口高度还没量出来（0），那时候算出来的是
        「一格都看不见」，于是**一张图都不会要** —— 而下一次报窗口要等到用户
        滚动，那之前的这一层就一直是空的。
        """
        refs: list[PhotoRef] = []
        visible = set(self.days())
        capacity, _offset = cells_and_offset(self.column_width(), self.row_height())
        row = self.row_height()
        bar = self._area.verticalScrollBar()
        top = bar.value()
        bottom = top + max(self._area.viewport().height(), row * 4)
        for (day, hour), photos in self._by_hour.items():
            if day not in visible:
                continue
            y = hour * row
            if y + row < top or y > bottom:
                continue
            refs.extend(photos[:capacity])
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


class _TimeHeader(QWidget):
    """表头：星期与日期。**今天是蓝圆点**（月视图里今天只是蓝字 —— 抄的就是这个差别）。"""

    def __init__(self, view: TimeView) -> None:
        super().__init__(view)
        self._view = view
        self.setFixedHeight(HEADER_HEIGHT)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colors = self._view.colors
        painter.fillRect(self.rect(), self._view.background)
        days = self._view.days()
        column = self._view.column_width()
        # **只有一列时不涂选中色**：日视图那一列永远是「选中的那一天」，
        # 涂下去整屏就是一片蓝 —— 选中色是给「七列里挑了一列」用的。
        for index, day in enumerate(days):
            slot = QRectF(self._view.content_left() + index * column, 0, column, self.height())
            painter.fillRect(slot, self._view.column_tint(day))
            if len(days) > 1 and day == self._view._selected:
                painter.fillRect(slot.adjusted(1, 0, -1, 0), colors.selection)
            draw_text(
                painter,
                QRectF(slot.left(), 4, slot.width(), 18),
                f"周{WEEKDAY_NAMES[day.weekday()]}",
                font_px(_WEEKDAY_FONT_PX),
                colors.accent if day == self._view.today() else colors.muted,
                Qt.AlignmentFlag.AlignCenter,
            )
            number = QRectF(slot.center().x() - 15, 22, 30, 28)
            if day == self._view.today():
                fill_rounded(painter, number, colors.accent, 15)
                draw_text(
                    painter,
                    number,
                    str(day.day),
                    font_px(_DATE_FONT_PX, QFont.Weight.Medium),
                    colors.on_accent,
                    Qt.AlignmentFlag.AlignCenter,
                    elide=False,
                )
            else:
                draw_text(
                    painter,
                    number,
                    str(day.day),
                    font_px(_DATE_FONT_PX, QFont.Weight.Medium),
                    colors.text,
                    Qt.AlignmentFlag.AlignCenter,
                    elide=False,
                )
            painter.setPen(colors.outline)
            painter.drawLine(int(slot.left()), 0, int(slot.left()), self.height())
        painter.setPen(colors.outline)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class _TimeGrid(QWidget):
    """时间轴本体（周视图 7 列，日视图 1 列）。"""

    def __init__(self, view: TimeView) -> None:
        super().__init__(view)
        self._view = view
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = begin(self, self._view.background)
        colors = self._view.colors
        days = self._view.days()
        column = self._view.column_width()
        row = self._view.row_height()
        left = self._view.content_left()
        right = int(left + self._view.content_width())

        for index in range(HOURS_PER_DAY + 1):
            y = index * row
            painter.setPen(colors.hairline)
            painter.drawLine(int(GUTTER), int(y), right, int(y))
            if index < HOURS_PER_DAY:
                draw_text(
                    painter,
                    QRectF(0, y + CELL_GAP, GUTTER - 10, 14),
                    f"{index:02d}:00",
                    font_px(_HOUR_FONT_PX),
                    colors.faint,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                    elide=False,
                )

        for index, day in enumerate(days):
            slot = QRectF(left + index * column, 0, column, self.height())
            painter.fillRect(slot, self._view.column_tint(day))
            if len(days) > 1 and day == self._view._selected:
                painter.fillRect(slot.adjusted(1, 0, -1, 0), colors.selection)
            for hour in range(HOURS_PER_DAY):
                photos = self._view.photos_in_hour(day, hour)
                if photos:
                    self._paint_hour_row(
                        painter, QRectF(slot.left(), hour * row, slot.width(), row), hour, photos
                    )
            painter.setPen(colors.outline)
            painter.drawLine(int(slot.left()), 0, int(slot.left()), self.height())
            if index == len(days) - 1:
                painter.drawLine(right, 0, right, self.height())

        if self._view.today() in days:
            index = days.index(self._view.today())
            y = self._view.now_row() * row
            painter.setPen(colors.now)
            painter.drawLine(int(left + index * column), int(y), right, int(y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.now)
            painter.drawEllipse(QPointF(left + index * column, y), 3.5, 3.5)
        painter.end()

    def _paint_hour_row(
        self, painter: QPainter, row: QRectF, hour: int, photos: Sequence[PhotoRef]
    ) -> None:
        """一行：照片按格子比例并排，放得下几格放几格，整排**居中**；放不下的写进徽标。"""
        colors = self._view.colors
        capacity, offset = cells_and_offset(row.width(), row.height())
        cell_w, cell_h = cell_size(row.height())
        shown = min(len(photos), capacity)
        left = row.left() + CELL_GAP + offset
        for index in range(shown):
            cell = QRectF(left + index * (cell_w + CELL_GAP), row.top() + CELL_GAP, cell_w, cell_h)
            paint_photo(painter, cell, self._view.photo(photos[index]))
            if shown == 1:
                # 压深的是**文字色**，标签写的是「底色」那一支：深色主题下正好反过来
                # （浅压深、浅底深字）。写成黑压白只在浅色主题里成立。
                scrim(painter, cell, colors.text, 0.8)
                draw_text(
                    painter,
                    cell.adjusted(6, 0, -6, 0),
                    f"{hour:02d}:00 · {short_title(photos[index].path)}",
                    font_px(_CELL_TITLE_FONT_PX),
                    colors.on_accent,
                )
        if len(photos) > shown:
            last = QRectF(
                left + (shown - 1) * (cell_w + CELL_GAP), row.top() + CELL_GAP, cell_w, cell_h
            )
            badge = QRectF(last.right() - 52, last.bottom() - 22, 46, 16)
            fill_rounded(painter, badge, colors.chip, 8)
            draw_text(
                painter,
                badge,
                f"还有 {len(photos) - shown} 张",
                font_px(_BADGE_FONT_PX),
                colors.text,
                Qt.AlignmentFlag.AlignCenter,
                elide=False,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        point = event.position()
        days = self._view.days()
        column = self._view.column_width()
        index = int((point.x() - self._view.content_left()) // column)
        if not (0 <= index < len(days)):
            return
        day = days[index]
        self._view.select_day(day)
        hour = int(point.y() // self._view.row_height())
        photos = self._view.photos_in_hour(day, hour)
        if photos:
            self._view.photo_requested.emit(list(photos), 0)


__all__ = ["GUTTER", "HEADER_HEIGHT", "THUMB_EDGE", "TimeView"]
