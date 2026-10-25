"""周视图：7 列 × 24 小时的时间轴（表头固定，时间轴可滚动）。

行高按格子比例从列宽推出来，于是**每小时恰好一格** —— 「一个小时一张」不是规则，而是放不下
第二格的自然结果。**点一格就是一次下钻**，没有更细的「日视图」。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from gallery.core.library.calendar import (
    WEEKDAY_NAMES,
    DayBuckets,
    HourBuckets,
    cell_cover,
    week_index,
    week_start,
)
from gallery.core.library.photos import PhotoRef
from gallery.ui import theme
from gallery.ui.calendar.base import LevelView, hour_label
from gallery.ui.calendar.layout import (
    CARD_RADIUS,
    CELL_GAP,
    HOURS_PER_DAY,
    cell_rect,
    row_height,
)
from gallery.ui.calendar.painting import (
    begin,
    dot,
    draw_text,
    fill_rounded,
    font_px,
    paint_cell_photo,
)
from gallery.ui.calendar.thumb_store import ThumbStore
from gallery.ui.scrollbar_style import apply_scrollbar_style

#: 左窄条（写钟点）多宽。
GUTTER = 56.0
HEADER_HEIGHT = 56.0

_HOUR_FONT_PX = 10
_WEEKDAY_FONT_PX = 11
_DATE_FONT_PX = 15
#: 缩略图按一格的大小要（约一百七十像素）。
THUMB_EDGE = 192

#: 可见区上下再多要几小时：滚动防抖之后才重报窗口，那一段留白会露出来。
PREFETCH_HOURS = 3

#: 滚动防抖：一次快速滚动会连发几十次 ``valueChanged``，每次都重算一遍可见区。
SCROLL_DEBOUNCE_MS = 80

#: 「滚到第一张照片上方」的定位延后多久（毫秒）。**0 = 下一轮事件循环**：要等的只是布局跑过。
REVEAL_DELAY_MS = 0


class WeekView(LevelView):
    """一周七列；表头固定，时间轴可滚动。"""

    def __init__(self, thumbs: ThumbStore, parent: object = None) -> None:  # type: ignore[arg-type]
        super().__init__("week", thumbs, parent)
        self._grid = _TimeGrid(self)
        area = QScrollArea()
        area.setWidget(self._grid)
        # ``setWidget`` 会把内容区设成 AutoFillBackground；不点掉它，整条时间轴会拿调色板铺一层
        # （ADR 0096：只有窗口铺底）。
        theme.mark_see_through(self._grid)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        # 视口是 ``QScrollArea`` 自己的子控件，样式表那条管不到它；它不铺，露出的才是窗口那块
        # （ADR 0096）。
        theme.mark_see_through(area.viewport())
        # guard: allow-long-comment（横向滚轮必须传递到 WeekView，滚动区与滚动条都不接焦点）
        # **横向的滚轮事件正好落在这个滚动区上，却不会停在这儿**：``QScrollArea`` 对「横向为主」的
        # 事件直接 ignore，于是传递到 :class:`WeekView` 自己翻页 —— 别加横向滚动条把它截下来。
        # 滚动区与竖滚动条都**不接焦点**：Qt 在鼠标按下时把焦点给「按到的那一层里第一个能接焦点
        # 的控件」，不设 NoFocus 的话点一下时间轴（含点滚动条）就把 ← / → 抢去滚时间轴。
        area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        area.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 与网格、元数据面板共用同一份滚动条样式（那个类型选择器只命中 ``QScrollArea`` 本身，
        # **不**级联到滚动条上，所以这里那条仍然是原生的、带一条常驻实心轨道）。
        apply_scrollbar_style(area.verticalScrollBar())
        #: 「滚到第一张照片」那次定位**有没有真的落地**（见 :meth:`set_buckets`）。
        self._revealed = False
        #: 用户点中的那一格（``(哪一天, 第几个小时)``）—— **点哪一格高亮哪一格**。
        self._selected_cell: tuple[datetime.date, int] | None = None
        self._header = _TimeHeader(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(area, 1)
        self._area = area
        # **内部几何也要盯着**：可见区按「行高 + 视口高 + 滚动位置」算，前两个要等**布局跑过**才
        # 定，而切层那次报窗口常常跑在布局前面，布局跑完却没有人再报一次。
        area.viewport().installEventFilter(self)
        self._grid.installEventFilter(self)
        # 「滚到第一张照片上方」那一步的防抖定时器（见 :meth:`_reveal_photos`）。**用子定时器**比
        # ``QTimer.singleShot(0, ...)`` 好：「按了 W → 同一轮 ⌘Q 关窗」会让它去调已析构的槽。
        self._reveal = QTimer(self)
        self._reveal.setSingleShot(True)
        self._reveal.setInterval(REVEAL_DELAY_MS)
        self._reveal.timeout.connect(self._apply_scroll)
        self._scroll_notify = QTimer(self)
        self._scroll_notify.setSingleShot(True)
        self._scroll_notify.setInterval(SCROLL_DEBOUNCE_MS)
        self._area.verticalScrollBar().valueChanged.connect(self._scroll_notify.start)
        self._scroll_notify.timeout.connect(self.window_changed)

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

    def select_hour(self, day: datetime.date, hour: int) -> None:
        """点到了某一天某一小时的那一格：高亮**那一格**，并选中那一天。

        **高亮的范围就是用户点到的那一格**：整列 24 行全涂一遍是「选中一天」，而且那底色退不掉，
        看着像卡住了。选中哪天在表头那一格里仍有一层淡底。
        """
        self._selected_cell = (day, hour)
        self.set_selected(day)
        self.repaint_content()  # 同一天换一格时 `set_selected` 不重画，这里补上

    def selected_cell(self) -> tuple[datetime.date, int] | None:
        """用户点中的那一格（没有就是 ``None``）。"""
        return self._selected_cell

    def is_selected_cell(self, day: datetime.date, hour: int) -> bool:
        """这一格就是用户点中的那一格吗。"""
        return self._selected_cell == (day, hour)

    def set_selected(self, day: datetime.date | None) -> None:
        if self._selected_cell is not None and self._selected_cell[0] != day:
            self._selected_cell = None
            self.repaint_content()
        super().set_selected(day)
        self._header.update()
        self._grid.update()

    def now_row(self) -> float:
        """今天那一格里「现在」落在第几行（0 = 00:00）；**「今天」不是真的今天时画不出来**。"""
        if datetime.date.today() != self._today:
            return 0.0
        moment = datetime.datetime.now().time()
        return moment.hour + moment.minute / 60.0

    def set_anchor(self, anchor: datetime.date) -> None:
        super().set_anchor(anchor)
        self._reveal_photos()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)  # type: ignore[arg-type]
        # **改变窗口不重新定位**：拖一下窗口就跳回第一张照片，是在把用户刚找到的位置弄丢。
        self._sync_grid_height()

    def _sync_grid_height(self) -> None:
        self._grid.setMinimumHeight(int(HOURS_PER_DAY * self.row_height()) + 8)

    def _reveal_photos(self) -> None:
        """停在第一张照片所在的那个小时上方一点（24 行两千多像素，让用户自己滚等于把它藏起来）。

        **当场落位，别留一帧**：挂在 0 ms 定时器上的话**第一帧已经按旧位置画出来了**，用户看见内容
        从上面窜下来。滚动范围在切过来那一刻常常已经有了，先同步试一次；真没有才退回定时器等
        :meth:`eventFilter` 补。
        """
        self._apply_scroll()
        if not self._revealed:
            self._reveal.start()

    def set_buckets(self, by_day: DayBuckets, by_hour: HourBuckets) -> None:
        """换了桶：**上一次定位没落地的话补一次**。

        拍摄时间是**后台读完才来的**，刚切到周视图那一刻桶多半是空的 —— 定位对着空桶跑完把滚动条
        按回 0 就再也不管了，而「看得见哪些」按那个位置算。
        **已经落地的那一次不补**：换库与重扫每一步都换桶，无条件补等于把用户正在看的位置拽回去。
        """
        super().set_buckets(by_day, by_hour)
        if not self._revealed:
            self._reveal_photos()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        """滚动区的视口或时间轴本体被重新布局了：报一次窗口、补一次定位、**并重画表头**。

        「内部刚被布局出来」而不是整层换了尺寸，这一种只有这里看得见，而那正是切层时最常见的形状。
        **表头必须在这里重画**：它的列宽取自时间轴宽度，竖滚动条一出现列宽就变，而表头自己一个
        像素都没动（``render()`` / ``grab()`` 会强制重画，所以像素探针看不到这种陈旧）。
        """
        if event.type() == QEvent.Type.Resize:
            self._header.update()
            self._geometry_notify.start()
            if not self._revealed:
                self._reveal_photos()
        return super().eventFilter(watched, event)

    def _apply_scroll(self) -> None:
        self._sync_grid_height()
        visible = set(self.days())
        hours = [hour for (day, hour) in self._by_hour if day in visible]
        bar = self._area.verticalScrollBar()
        if not hours:
            bar.setValue(0)
            return  # 这一周还没有照片：算「没落地」，等桶到了再补一次
        if bar.maximum() <= 0:
            # **布局还没跑过**：滚动条一个范围都没有，设了也没用 —— 关键是**别把这一次算成
            # 「已落地」**（`_revealed` 决定要不要补一次），几何定下来 ``eventFilter`` 会补。
            return
        self._revealed = True
        row = self.row_height()
        target = int(max(0, min(hours) - 1) * row)
        bar.setValue(max(0, min(target, bar.maximum())))
        self._grid.update()
        self.window_changed.emit()

    def visible_refs(self) -> list[PhotoRef]:
        """滚到看得见的那几小时里的照片，每小时一张（**挑哪一张问 ``cell_cover``**）。

        **上下再多要三小时**（:data:`PREFETCH_HOURS`）：滚动防抖之后才重报窗口，那一段如果正好
        有照片，用户会看见格子空着再被填上。
        """
        refs: list[PhotoRef] = []
        visible = set(self.days())
        row = self.row_height()
        bar = self._area.verticalScrollBar()
        top = bar.value()
        bottom = top + max(self._area.viewport().height(), row * 4)
        margin = row * PREFETCH_HOURS
        for (day, hour), photos in self._by_hour.items():
            if day not in visible:
                continue
            y = hour * row
            if y + row < top - margin or y > bottom + margin:
                continue
            cover = cell_cover(photos)
            if cover is not None:
                refs.append(cover)
        return refs

    def repaint_content(self) -> None:
        """重画**时间轴与表头**——格子是它们画的，不是 ``WeekView`` 自己画的。

        ``WeekView.update()`` 只把自己那一块标记为需要重绘：缩略图回来时漏了它，图在手上而屏是灰。
        """
        self._grid.update()
        self._header.update()

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
        days = self._view.days()
        column = self._view.column_width()
        for index, day in enumerate(days):
            slot = QRectF(self._view.content_left() + index * column, 0, column, self.height())
            if day == self._view._selected:
                painter.fillRect(slot.adjusted(1, 0, -1, 0), colors.selection)
            draw_text(
                painter,
                QRectF(slot.left(), 4, slot.width(), 18),
                # **按「周日打头」数出来的列号取名字**：``WEEKDAY_NAMES`` 是周日 0 … 周六 6，
                # 而 ``weekday()`` 是周一 0 … 周日 6，直接索引会让表头第一列（周日）写成「周六」。
                f"周{WEEKDAY_NAMES[week_index(day)]}",
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

    def _visible_hours(self, event: object, row: float) -> tuple[int, int]:
        """这次重画覆盖哪几行小时（闭区间）。

        取 ``event.region()`` 的外接矩形 —— 那才是 Qt 真正要重画的范围。**拿不到就当整块
        都可见**（比画少了安全得多：宁可多画，不可在该画的地方留空白）。
        """
        region = getattr(event, "region", None)
        bounds = region().boundingRect() if callable(region) else None
        if bounds is None or not bounds.isValid() or row <= 0:
            return 0, HOURS_PER_DAY - 1
        first = int(bounds.top() // row)
        # **下界留一行余量**：贴着可见区上沿的那一行，它的标签画在格子里侧（``y + CELL_GAP``），
        # 上沿正好切在标签中间时多留一格才画得全。少这一行就是「第一行的小时数被切掉一半」。
        last = int(bounds.bottom() // row) + 1
        return max(0, first), min(HOURS_PER_DAY - 1, last)

    def paintEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = begin(self)
        colors = self._view.colors
        days = self._view.days()
        column = self._view.column_width()
        row = self._view.row_height()
        left = self._view.content_left()
        right = int(left + self._view.content_width())

        # guard: allow-long-comment（裁剪区只能从 event 取，写成 painter.clipRect() 会一格都不画）
        # **只画看得见的那几行**（下方两段循环都按这个范围收口）：整条时间轴比视口高三倍，而
        # :meth:`WeekView.update` 每张缩略图回来都标记为需要重绘整块，逐格走满是纯浪费。
        # 裁剪区**只能从 ``event`` 上取** —— Qt 不会把它设到我们自己新建的 painter 上
        # （``painter.clipBoundingRect()`` 是空矩形），写成 ``painter.clipRect()`` 会算出
        # 「第一行 0、最后一行 -1」，于是**一格都不画**。光栅层面早被 ``QBackingStore`` 裁掉了。
        first, last = self._visible_hours(event, row)

        for index in range(first, min(last + 1, HOURS_PER_DAY) + 1):
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
            for hour in range(first, last + 1):
                photos = self._view.photos_in_hour(day, hour)
                # **点中的那一格也要画**（哪怕那一小时没有照片）：高亮跟着这一格走，不是照片。
                if photos or self._view.is_selected_cell(day, hour):
                    self._paint_hour_row(
                        painter,
                        QRectF(slot.left(), hour * row, slot.width(), row),
                        day,
                        hour,
                        photos,
                    )
            # 列分隔线是**通高**的，不受可见行范围影响（它在 x 上不在 y 上）。
            painter.setPen(colors.outline)
            painter.drawLine(int(slot.left()), 0, int(slot.left()), self.height())
            if index == len(days) - 1:
                painter.drawLine(right, 0, right, self.height())

        # 「现在」那条线**只画在今天那一格里**：划到最右边看着像「从今天往后延伸的进度」。
        if self._view.today() in days:
            index = days.index(self._view.today())
            y = self._view.now_row() * row
            line_left = left + index * column
            line_right = left + (index + 1) * column
            painter.setPen(colors.now)
            painter.drawLine(int(line_left), int(y), int(line_right), int(y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.now)
            painter.drawEllipse(QPointF(line_left, y), 3.5, 3.5)
        painter.end()

    def _paint_hour_row(
        self,
        painter: QPainter,
        row: QRectF,
        day: datetime.date,
        hour: int,
        photos: Sequence[PhotoRef],
    ) -> None:
        """一行：一格照片（不止一张时右下角写「N 张」），点中的那一格先铺一层选中底色。

        **与年 / 月两层同一种画法**：格子就是一张照片，不压渐变、不写标题。**没有照片的那一格只留
        选中底色**（再补个占位圆点就成了「有张照片还没读出来」）。**有照片、图还没到的那一格画
        圆点**：铺满格灰块的话，照片到了是小一圈的，两帧摆在一起就是「闪一下，尺寸还在变」。
        """
        colors = self._view.colors
        x, y, cell_w, cell_h = cell_rect(row.width(), row.height())
        cell = QRectF(row.left() + x, row.top() + y, cell_w, cell_h)
        if self._view.is_selected_cell(day, hour):
            fill_rounded(painter, cell, colors.selection, CARD_RADIUS)
        cover = cell_cover(photos)
        if cover is None:
            return
        image = self._view.photo(cover)
        if image is None:
            dot(painter, cell.center(), 2.5, colors.faint)
            return
        paint_cell_photo(
            painter,
            cell,
            image,
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
        self._view.setFocus()
        # **空白的那一格也选中**：那一格点得中，高亮就该跟着它走；没有照片就不展开任何东西。
        self._view.select_hour(day, hour)
        photos = list(self._view.photos_in_hour(day, hour))
        if not photos:
            return
        self._view.select_cell(photos, hour_label(day, hour))


__all__ = [
    "GUTTER",
    "HEADER_HEIGHT",
    "PREFETCH_HOURS",
    "REVEAL_DELAY_MS",
    "SCROLL_DEBOUNCE_MS",
    "THUMB_EDGE",
    "WeekView",
]
