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

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from gallery.core.library.calendar import (
    WEEKDAY_NAMES,
    DayBuckets,
    HourBuckets,
    week_index,
    week_start,
)
from gallery.core.library.photos import PhotoRef
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

#: 「滚到第一张照片上方」那个定位延后多久（毫秒）。**0 = 下一轮事件循环** ——
#: 要等的只是**布局**跑过一遍（滚动条的最大值在那之前还是 0）。
REVEAL_DELAY_MS = 0


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
        # **横向的滚轮事件正好落在这个滚动区上，却不会在这儿停下来**：
        # ``QScrollArea`` 对「横向为主」的滚轮事件是直接 ignore 的，于是它冒到父控件
        # —— :class:`WeekView` 自己，那里收两指甩动并翻页（见 ``LevelView.wheelEvent``）。
        # 换句话说，「时间轴左右划 = 上一周 / 下一周」这件事靠的是这个 ignore，
        # 别在这里加横向滚动条把它截下来。
        # **滚动区与它那条竖滚动条都不接焦点**：Qt 在鼠标按下时把焦点给「按到的那一层里
        # 第一个 focusPolicy != NoFocus 的控件」，而这两个默认都是 StrongFocus ——
        # 于是**在时间轴上点一下（含点那条滚动条）键盘就被抢走**：← / → 变成滚动时间轴，
        # 日历的「上一周 / 下一周」按多少次都没反应。焦点是这一页自己的（见
        # ``CalendarPage.focus_current_view``），一律交给 :class:`WeekView` 自己。
        area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        area.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        #: 「滚到第一张照片」那次定位**有没有真的落地**（见 :meth:`set_buckets`）。
        self._revealed = False
        #: 用户点中的那一格（``(哪一天, 第几个小时)``）—— **点哪一格高亮哪一格**。
        #: 切层时它原样留着（见 ``CalendarPage.set_level``：选中是哪一天就翻到哪一段）。
        self._selected_cell: tuple[datetime.date, int] | None = None
        self._header = _TimeHeader(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(area, 1)
        self._area = area
        # **内部几何也要盯着**：可见区是按「网格宽度（→行高）+ 视口高度 + 滚动位置」
        # 算的，而前两个数要等**布局跑过一遍**才定。切到这一层的那一次报窗口常常跑在
        # 布局前面（`setCurrentWidget` 之后内部布局是延后处理的），算出来的那一屏于是
        # 与真正画出来的不是同一批 —— 而布局跑完之后**没有人再报一次**，那些格子就
        # 永远没人要图（症状：切到某一周，屏幕上有几格是占位灰，切走再回来才好）。
        area.viewport().installEventFilter(self)
        self._grid.installEventFilter(self)
        # 「滚到第一张照片上方」那个定位的闸门（见 :meth:`_reveal_photos`）。
        #
        # **做成自己的子定时器，而不是 `QTimer.singleShot(0, self._apply_scroll)`**：
        # 后者是**两参**形态，这个定时器与本对象毫无关系，于是「按了 `W` → 同一轮里
        # ⌘Q 关窗」这种再平常不过的时序会让它照常触发，去调一个已经析构的
        # ``_apply_scroll``，换来一条 ``Internal C++ object already deleted``
        # （同一个道理与那条注释见 ``browser/grid.py`` 的 ``eventFilter``）。
        #
        # 子定时器把「接收方析构时自动取消」这件事变成了**自己的成员**：控件没了它也没了。
        # 顺带换掉的是那个布尔闩锁 ``_scrolling`` —— 闩锁一旦卡住（那个定时器没跑成）
        # 就再也解不开，``_reveal_photos`` 会从此次次早退，周视图再也找不到它的第一张照片；
        # ``isActive()`` 说的是「眼下是不是真的排着一个」，不存在卡住这一说。
        self._reveal = QTimer(self)
        self._reveal.setSingleShot(True)
        self._reveal.setInterval(REVEAL_DELAY_MS)
        self._reveal.timeout.connect(self._apply_scroll)
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

    # ---- 选中 -------------------------------------------------------------
    def select_hour(self, day: datetime.date, hour: int) -> None:
        """点到了某一天某一小时的那一格：高亮**那一格**，并选中那一天。

        **高亮的范围就是用户点到的那一格**（月视图里点一天高亮那一天）：先前是把**整列**
        24 行全涂一遍 —— 那是「选中一天」而不是「点了一格」，而且退不掉的底色看着像卡住了。
        一天被选中（从别的层切过来）时，表头那一格里仍有一层淡底，于是「选的是哪一天」
        还看得出来。
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
        # 换了一天（或收掉选中）之后，那一格的高亮不再属于屏幕上任何东西 —— 跟着走。
        if self._selected_cell is not None and self._selected_cell[0] != day:
            self._selected_cell = None
            self.repaint_content()
        super().set_selected(day)
        self._header.update()
        self._grid.update()

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

        **当场落位，别等下一轮**：落在 0 ms 定时器上的话，**第一帧已经按旧位置画出来了**
        —— 用户看见的是内容从上面窜下来（「一张图飞快跑下来」）。而滚动范围其实在切过来
        的那一刻常常已经有了（切层时视图已经摆好、翻页时更是本来就在屏幕上），于是先
        试一次同步的；真的还没有范围（这一层还没被布局过）才退回定时器，等
        :meth:`eventFilter` 那边在内部几何定下来之后补。

        **连着按 ← / → 只定位一次**：定时器 ``start()`` 在已经排着一个的时候会原地续期，
        而 :meth:`_apply_scroll` 读的是**此刻**的锚点，于是最后一次说了算。
        """
        self._apply_scroll()
        if not self._revealed:
            self._reveal.start()

    def set_buckets(self, by_day: DayBuckets, by_hour: HourBuckets) -> None:
        """换了桶：**上一次定位没落地的话补一次**。

        定位是 :meth:`set_anchor` 排的，而拍摄时间是**后台读完才来的**（见
        ``window/calendar_flow._start_calendar_load``）。于是刚切到周视图那一刻桶多半
        还是空的 —— 定位对着空桶跑完，把滚动条按回 0 就再也不管了：这一周明明有照片，
        屏幕却停在 00:00，而「看得见哪些」是按那个滚动位置算的，于是要的那几张
        压根没人要图（症状：切到某一周时缩略图只剩一片占位灰）。

        **只在没落地时补**：已经定位过（或用户自己滚过）的那一次不该被数据打回来。
        """
        super().set_buckets(by_day, by_hour)
        if not self._revealed:
            self._reveal_photos()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        """滚动区的视口或时间轴本体被重新布局了：报一次窗口，定位没落地就补一次。

        **这两个 resize 与 `WeekView.resizeEvent` 是三件不同的事**：整层被摆到新尺寸时
        那个会响；而「这一层本来就在这个尺寸上，只是内部的滚动区 / 时间轴刚被布局出来」
        只有这里看得见 —— 后一种正是切到这一层时最常见的形状（第一次切过去尤其如此）。
        """
        if event.type() == QEvent.Type.Resize:
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
            # **布局还没跑过**：滚动条一个范围都没有，这时候设了也没用。关键是**别把
            # 这一次算成「已落地」** —— 算上了就再也没有第二次（`_revealed` 是那道
            # 「补一次」的闸），于是这一层永远停在 00:00，而「看得见哪些」又按这个
            # 位置算：照片所在的那些格子压根没人要图（症状：切到某一周是一片占位灰）。
            # 内部几何一定下来，`eventFilter` 那边会补这一次。
            return
        self._revealed = True
        row = self.row_height()
        target = int(max(0, min(hours) - 1) * row)
        bar.setValue(max(0, min(target, bar.maximum())))
        self._grid.update()
        self.window_changed.emit()

    # ---- 缩略图窗口 -------------------------------------------------------
    def _band_refs(self, days: Sequence[datetime.date]) -> list[PhotoRef]:
        """这一条横向的带里，看得见的那几小时的照片（每小时一张）。"""
        refs: list[PhotoRef] = []
        visible = set(days)
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

    def visible_refs(self) -> list[PhotoRef]:
        """滚到看得见的那几小时里的照片，每小时一张。

        **上下再多要三小时**：滚动去抖之后才重报窗口，那一段如果正好有照片，
        用户会看见格子空着再被填上。
        """
        return self._band_refs(self.days())

    def prefetch_refs(self) -> list[PhotoRef]:
        """**相邻那一周**、同一条带里的照片：先解着，按 → / ← 时就不用等。

        不预取的话，每一段新日子都是「切过去才开始解」—— 第一次看总有一下占位灰
        （真机上一张原图解几百毫秒），而切走再切回来就不灰了（图已经在手上）。
        预取就是把那个便宜提前拿到；**只多要一条带**（不是整周 168 格），低优先级，
        解码排在看得见的那几张后面。
        """
        anchor = self._anchor
        return (
            self._band_refs(
                [day + datetime.timedelta(days=offset) for day in self.days() for offset in (-7, 7)]
            )
            if anchor
            else []
        )

    def repaint_content(self) -> None:
        """重画**时间轴与表头**——格子是它们画的，不是 ``WeekView`` 自己画的。

        ``WeekView.update()`` 只把自己那一块标脏，画着照片的是里面那个 ``_TimeGrid``。
        缩略图一张一张回来时走的就是这一次重画：漏了它，图在手上而屏幕上是占位灰。
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
        painter.fillRect(self.rect(), self._view.background)
        days = self._view.days()
        column = self._view.column_width()
        for index, day in enumerate(days):
            slot = QRectF(self._view.content_left() + index * column, 0, column, self.height())
            if day == self._view._selected:
                # **只染表头这一格**：整列 24 行全涂一遍看着像卡住了，而「选的是哪一天」
                # 这一格说得清 —— 尤其从年 / 月视图切过来时，时间轴上并没有点中的那一格。
                painter.fillRect(slot.adjusted(1, 0, -1, 0), colors.selection)
            draw_text(
                painter,
                QRectF(slot.left(), 4, slot.width(), 18),
                # **按「周日打头」数出来的列号取名字**，不是 ``day.weekday()``：
                # :data:`WEEKDAY_NAMES` 是周日 0 … 周六 6，而 ``weekday()`` 是
                # 周一 0 … 周日 6 —— 直接索引会让七个名字整体差一天，表头第一列
                # （周日）写成「周六」。
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
            for hour in range(HOURS_PER_DAY):
                photos = self._view.photos_in_hour(day, hour)
                # **点中的那一格也要画**（哪怕那一小时没有照片）：用户点的是「这一格」，
                # 高亮就该跟着这一格走，而不是跟着有没有照片走。
                if photos or self._view.is_selected_cell(day, hour):
                    self._paint_hour_row(
                        painter,
                        QRectF(slot.left(), hour * row, slot.width(), row),
                        day,
                        hour,
                        photos,
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
        self,
        painter: QPainter,
        row: QRectF,
        day: datetime.date,
        hour: int,
        photos: Sequence[PhotoRef],
    ) -> None:
        """一行：一格照片（不止一张时右下角写「N 张」），点中的那一格先铺一层选中底色。

        **与年 / 月两层同一种画法**：格子就是一张照片，不压渐变、不写标题 ——
        「这一格是几点」左窄条已经写着，而压深的那道渐变原本是给标题垫底的。

        **没有照片的那一格只留一层选中底色**：底色已经把「我点的是这一格」说清楚了，
        再补一个占位圆点就变成了「这一格有张照片还没读出来」—— 而它根本没有照片。

        **有照片、图还没到的那一格画一个安静的圆点**，与月视图同一个约定（见
        ``month_view._paint_day``）。**别把整格铺成那块浅灰**：照片到了是按比例缩在格子里
        摆的（比整格小一圈），于是「满格的灰块 → 小一圈的照片」看起来就是**闪一下、
        尺寸还在变**（月视图没有这个毛病，正是因为它画的是圆点）。
        """
        colors = self._view.colors
        x, y, cell_w, cell_h = cell_rect(row.width(), row.height())
        cell = QRectF(row.left() + x, row.top() + y, cell_w, cell_h)
        if self._view.is_selected_cell(day, hour):
            fill_rounded(painter, cell, colors.selection, CARD_RADIUS)
        if not photos:
            return
        image = self._view.photo(photos[0])
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
        # 键盘归视图自己：**点一下不等于把键盘让给时间轴的滚动区**（理由见 __init__）。
        self._view.setFocus()
        # **空白的那一格也选中**（与月视图点空白的一天同一脾气）：那一格点得中，
        # 高亮就该跟着它走；没有照片就不摊开任何东西。
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
