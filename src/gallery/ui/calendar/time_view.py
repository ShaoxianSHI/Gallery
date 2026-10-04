"""周视图：7 列 × 24 小时的时间轴。

每一小时一行，行高按格子比例从列宽推出来，于是**每小时恰好一格** ——
「一个小时一张照片」不是一条规则，而是放不下第二格的自然结果。

**点一格就是一次下钻**：那一小时有几张照片就摊出几张（见
:meth:`~gallery.ui.calendar.base.LevelView.select_cell` 发出的信号）。
日历上没有更细的一层：再单开一个「日视图」摊出来的还是同一批照片，
而「那天下午拍了什么」点一下那一小时就到了。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from gallery.core.library.calendar import WEEKDAY_NAMES, week_start
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.base import LevelView, hour_label
from gallery.ui.calendar.layout import CELL_GAP, HOURS_PER_DAY, cell_rect, row_height
from gallery.ui.calendar.painting import (
    begin,
    draw_text,
    fill_rounded,
    font_px,
    paint_cell_photo,
)
from gallery.ui.calendar.thumbs import ThumbStore

#: 左窄条（写钟点）多宽。
GUTTER = 56.0
#: 表头（星期与日期）多高。
HEADER_HEIGHT = 56.0

_HOUR_FONT_PX = 10
_WEEKDAY_FONT_PX = 11
_DATE_FONT_PX = 15
#: 缩略图按一格的大小要（约一百七十像素）。
THUMB_EDGE = 192

#: 可见区上下再多要几小时（滚动去抖之后才重报窗口，那一段留白就露出来了）。
PREFETCH_HOURS = 3

#: 滚动去抖：一次快速滚动会连发几十次 ``valueChanged``，而每一轮都要重算一遍
#: 「现在看得见哪几张」并向流水线报一次（网格那边是同样的处理）。
SCROLL_DEBOUNCE_MS = 80


class WeekView(LevelView):
    """一周七列；表头固定，时间轴可滚动。"""

    def __init__(self, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__("week", thumbs, parent)
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
        self._scroll_notify = QTimer(self)
        self._scroll_notify.setSingleShot(True)
        self._scroll_notify.setInterval(SCROLL_DEBOUNCE_MS)
        self._area.verticalScrollBar().valueChanged.connect(self._scroll_notify.start)
        self._scroll_notify.timeout.connect(self.window_changed)

    # ---- 几何 -------------------------------------------------------------
    def days(self) -> list[datetime.date]:
        start = week_start(self._anchor)
        return [start + datetime.timedelta(days=offset) for offset in range(7)]

    def column_width(self) -> float:
        return self.content_width() / max(1, len(self.days()))

    def content_width(self) -> float:
        """时间轴上留给各列的宽度（扣掉左边写钟点的那一窄条）。"""
        return max(1.0, self._grid.width() - GUTTER)

    def content_left(self) -> float:
        return GUTTER

    def row_height(self) -> float:
        return row_height(self.column_width())

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
        self._reveal_photos()

    def set_selected(self, day: datetime.date) -> None:
        super().set_selected(day)
        self._header.update()
        self._grid.update()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)  # type: ignore[arg-type]
        # **改变窗口不重新定位**：用户滚到下午两点，这时拖一下窗口就跳回第一张照片，
        # 那不是在帮他看清，是在把他刚找到的位置弄丢。这里只同步高度。
        self._sync_grid_height()

    def _sync_grid_height(self) -> None:
        self._grid.setMinimumHeight(int(HOURS_PER_DAY * self.row_height()) + 8)

    def _reveal_photos(self) -> None:
        """停在第一张照片所在的那个小时上方一点。

        时间轴有 24 行、两千多像素，让用户自己滚过去等于把「这周有照片」这件事藏起来。
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
        self.window_changed.emit()

    # ---- 缩略图窗口 -------------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """滚到看得见的那几小时里的照片，每小时一张。

        **上下再多要三小时**：滚动去抖之后才重报窗口，那一段如果正好有照片，
        用户会看见格子空着再被填上。
        """
        refs: list[PhotoRef] = []
        visible = set(self.days())
        row = self.row_height()
        bar = self._area.verticalScrollBar()
        top = bar.value()
        bottom = top + max(self._area.viewport().height(), row * 4)
        margin = row * PREFETCH_HOURS
        for (day, hour), photos in self._by_hour.items():
            if day not in visible or not photos:
                continue
            y = hour * row
            if y + row < top - margin or y > bottom + margin:
                continue
            refs.append(photos[0])
        return refs

    def thumbnail_edge(self) -> int:
        return THUMB_EDGE


class _TimeHeader(QWidget):
    """表头：星期与日期。**今天是蓝圆点**（月视图里今天只是蓝字 —— 抄的就是这个差别）。"""

    def __init__(self, view: WeekView) -> None:
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
        for index, day in enumerate(days):
            slot = QRectF(self._view.content_left() + index * column, 0, column, self.height())
            painter.fillRect(slot, self._view.column_tint(day))
            if day == self._view._selected:
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
    """时间轴本体（7 列 × 24 小时）。"""

    def __init__(self, view: WeekView) -> None:
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
            if day == self._view._selected:
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
        """一行：一张圆角照片铺进那一格，不止一张时右下角写「N 张」。

        **与年 / 月两层同一种画法**：格子就是一张照片，不压渐变、不写标题 ——
        「这一格是几点」左窄条已经写着，而压深的那道渐变原本是给标题垫底的。
        """
        colors = self._view.colors
        x, y, cell_w, cell_h = cell_rect(row.width(), row.height())
        cell = QRectF(row.left() + x, row.top() + y, cell_w, cell_h)
        paint_cell_photo(
            painter,
            cell,
            self._view.photo(photos[0]),
            count=len(photos),
            chip=colors.chip,
            text=colors.text,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        point = event.position()
        days = self._view.days()
        column = self._view.column_width()
        index = int((point.x() - self._view.content_left()) // column)
        if not (0 <= index < len(days)):
            return
        day = days[index]
        hour = int(point.y() // self._view.row_height())
        photos = list(self._view.photos_in_hour(day, hour))
        if not photos:
            return
        self._view.select_day(day)
        self._view.select_cell(photos, hour_label(day, hour))


__all__ = [
    "GUTTER",
    "HEADER_HEIGHT",
    "PREFETCH_HOURS",
    "SCROLL_DEBOUNCE_MS",
    "THUMB_EDGE",
    "WeekView",
]
