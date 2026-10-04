"""日历这一页：工具条 + 三层视图，负责状态与快捷键。

页面只管三件事：**现在停在哪一层、停在哪一段日子、手上有哪些照片**；
画什么交给那三个视图（它们各自 :class:`~gallery.ui.calendar.base.LevelView`），
几何交给 :mod:`~gallery.ui.calendar.layout`，日期与分桶交给
:mod:`gallery.core.library.calendar`。

**快捷键 ``Y`` / ``M`` / ``W`` 收在这一页，不做成窗口级 QAction**：
它们与网格那一层没有任何关系，而 ``←`` ``→`` 在网格里是「挪当前项」，在日历里是
「翻页」 —— 做成窗口级就会把网格的上下左右也一起抢走（ADR 0033 决策 6）。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QShowEvent
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from gallery.core.library.calendar import LEVELS, group_by_day, group_by_hour, shift
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets
from gallery.ui.calendar.base import LevelView
from gallery.ui.calendar.colors import CalendarColors
from gallery.ui.calendar.month_view import MonthView
from gallery.ui.calendar.thumbs import ThumbStore
from gallery.ui.calendar.time_view import WeekView
from gallery.ui.calendar.toolbar import CalendarBar
from gallery.ui.calendar.year_view import YearView
from gallery.ui.theme import AppTheme, ThemeWatcher, current_theme, ensure_installed

#: ``Y`` / ``M`` / ``W`` 三个键。**没有日视图**，所以也没有 ``D``。
_KEY_LEVELS = {
    Qt.Key.Key_Y: "year",
    Qt.Key.Key_M: "month",
    Qt.Key.Key_W: "week",
}

#: 「来了一张图，重画一次」那个闸门的间隔（毫秒）。**0 = 下一轮事件循环**。
REPAINT_INTERVAL_MS = 0


class CalendarPage(QWidget):
    """日历那一页。"""

    #: 停在哪一层了（外面据此记进会话）。
    level_changed = Signal(str)
    #: 用户点了日历上一格里的照片（外面据此打开大图页，或摊成一层网格）。
    cell_requested = Signal(object, str)
    #: 这一层需要的缩略图变了（外面据此向流水线要图）。
    window_changed = Signal()
    #: 选中的东西变了（外面据此改标题与动作可用性）。
    selection_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbStore()
        #: 上一次向流水线报出去的那一批（以及档位），见 `_refresh_window`。
        self._reported: list[PhotoRef] = []
        self._reported_edge = 0
        self._anchor = datetime.date.today()
        #: 选中的是哪一天。**整页一份**（理由见 :meth:`set_selected`）。
        self._selected: datetime.date | None = None
        self._today = datetime.date.today()
        self._level = "month"

        self._bar = CalendarBar()
        self._bar.step_requested.connect(self.step)
        self._bar.today_requested.connect(self.go_today)
        self._bar.level_changed.connect(self.set_level)

        self._views: dict[str, LevelView] = {
            "year": YearView(self._thumbs),
            "month": MonthView(self._thumbs),
            "week": WeekView(self._thumbs),
        }
        self._stack = QStackedWidget()
        for level in LEVELS:
            view = self._views[level]
            view.cell_requested.connect(self.cell_requested)
            view.window_changed.connect(self.window_changed)
            view.selection_changed.connect(self._on_view_selected)
            self._stack.addWidget(view)
        #: 页面自己推给各层的那一遍（见 :meth:`_on_view_selected`）—— 那不是「用户选的」。
        self._pushing_selection = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self._stack, 1)

        ensure_installed()
        # 「来了一张缩略图 → 重画当前那层」的闸门。**必须闸，不能一张一张画**：
        # 流水线是**一张一张**送图回来的（每一张解码完就发一次 ``ready``），
        # 而 ``update()`` 是立刻排一次整页重绘 —— 于是一整屏几百张缩略图就是几百帧
        # 完完整整的年视图重画。实测 300 张照片铺满 2024（405 个有图的格子）时，
        # 一张一张送是 **1890 ms**，一次全送到只要 **0.1 ms**。
        #
        # **用单次定时器而不是数一数**：``start()`` 在已经跑着的时候会原地**续期**，
        # 于是来一百张就是「事件循环下一轮画一次」，不需要任何计数或阈值。
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._repaint_current_view)
        self._apply_theme(current_theme())
        self._watcher = self.findChild(ThemeWatcher)
        if self._watcher is not None:
            self._watcher.changed.connect(self._on_theme_changed)
        self.set_level("month")

    # ---- 数据 -------------------------------------------------------------
    def set_photos(self, photos: Sequence[PhotoRef], values: Mapping[Path, PhotoFacets]) -> None:
        """换一整库的照片与元数据快照。分桶在这里一次算好，四层共用。"""
        by_day = group_by_day(photos, values)
        by_hour = group_by_hour(photos, values)
        for view in self._views.values():
            view.set_buckets(by_day, by_hour)
        # 手上的图全扔了，所以「报过了」那份记录也一起扔（理由见 `_refresh_window`）。
        self._thumbs.clear()
        self._forget_reported()
        self._refresh_window()

    def thumbnail_edge(self) -> int:
        return self.current_view().thumbnail_edge()

    def set_thumbnail(self, path: object, image: object, edge: int = 0) -> None:
        """记下一张缩略图，然后**排**一次重画（不是立刻画，见 ``_repaint_timer``）。"""
        if isinstance(path, Path) and isinstance(image, QImage):
            self._thumbs.set(path, image, edge)
            self._repaint_timer.start()

    def forget_thumbnails(self, paths: Iterable[Path]) -> None:
        self._thumbs.forget(paths)
        # 忘掉的那几张可能正在可见区里，所以「报过了」那份记录同样作废。
        self._forget_reported()
        self._repaint_timer.start()

    def _repaint_current_view(self) -> None:
        self.current_view().update()

    def _forget_reported(self) -> None:
        self._reported = []
        self._reported_edge = 0

    # ---- 位置 -------------------------------------------------------------
    def level(self) -> str:
        return self._level

    def anchor(self) -> datetime.date:
        return self._anchor

    def today(self) -> datetime.date:
        return self._today

    def set_today(self, today: datetime.date) -> None:
        self._today = today
        for view in self._views.values():
            view.set_today(today)

    def current_view(self) -> LevelView:
        return self._views[self._level]

    def selected_day(self) -> datetime.date | None:
        """选中的是哪一天（没有选中就是 ``None``）。**三层共用这一份。**"""
        return self._selected

    def selected_refs(self) -> list[PhotoRef]:
        return self.current_view().selected_refs()

    def set_selected(self, day: datetime.date | None) -> None:
        """选中那一天（``None`` = 不选中任何一天）。

        **整页只有这一份**，推给当前那一层照着画：于是「在年视图里点一天 → 切到月视图」
        仍然是同一天，而不是一层一份、切过去就忘了。
        """
        self._selected = day
        # **只推给当前那一层**：另外两层看不见，推了也是白推；它们各自成为当前层时
        # 由 `set_level` 补上那一次。
        self._pushing_selection = True
        try:
            self.current_view().set_selected(day)
        finally:
            self._pushing_selection = False
        self.selection_changed.emit(self.selected_refs())

    def _on_view_selected(self) -> None:
        """某一层说「我选中了某一天」（用户点的那一下）：整页的这一份跟着它走。"""
        if self._pushing_selection:
            return  # 页面自己推下去的那一遍，不是用户的动作
        self.set_selected(self.current_view().selected_day())

    def clear_selection(self) -> None:
        """不选中任何一天（用户动作：会通知外面）。"""
        self.set_selected(None)

    def _reset_selection(self) -> None:
        """**静默**地不选中任何一天 —— 外面没人要听「这一页刚露出来」这件事。

        与 :meth:`clear_selection` 只差一个信号，而这个信号在本页**一个接收方都没有**
        （`grep selection_changed src/` 只有声明与那一发）。曾经在 ``showEvent`` 里发它，
        那一发足以让整轮界面测试在**下一个**窗口构造时段错误 —— 位置在
        ``SearchField.__init__``，与这里八竿子打不着：控件还活着的时候就已经被弄坏，
        要等下一次大分配才崩。
        """
        self._selected = None
        self.current_view().clear_selection()

    def focus_current_view(self) -> None:
        """把键盘焦点交给当前那层。

        **必须点名交给视图，不能指望 Qt 自己挪**：从大图页退回来时 Qt 会把焦点顺着焦点链
        交给页面上**第一个**能接焦点的控件 —— 周视图里那是那个 ``QScrollArea``，它把
        ← / → 当成滚动时间轴吃掉了，于是日历的「上一周 / 下一周」按多少次都没反应
        （症状：点进一张照片再退出来，左右键就废了）。月视图里落到的恰好是视图自己，
        所以只有周视图犯病。
        """
        self.current_view().setFocus()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """这一页重新露出来：把焦点还给当前那层，并把留在这里的「选中」收掉。

        **收掉选中是因为那一格已经摊开了**：用户点的是某一天，进的是那张照片或那一格；
        退回来时那一格不在眼前，留着的高亮是一块点不掉的底色（周视图里点空白格子什么也
        不会发生 —— 它提前 return 了 —— 于是它永远留着）。**第一次露出来也一并收掉**：
        起手那个「选中今天」没有人点过它，而「今天」本来就由蓝字与蓝圆点写着。
        锚点与滚动位置**不动**：那才是用户刚找到的地方，理由见
        ``window/calendar_flow._back_to_calendar``。
        """
        super().showEvent(event)
        self.focus_current_view()
        self._reset_selection()

    def set_level(self, level: str) -> None:
        """切到另一层。**选中了哪一天，就翻到那一天所在的那一层。**

        「在年视图里点 5 月 16 号 → 按 W」必须看到 5 月 16 号那一周，而不是上周剩下
        的那几天 —— 所以切层前先把锚点挪到选中的那一天上。三条入口（``Y`` / ``M`` /
        ``W``、工具条的分段选择器、会话恢复）都走这里，一处就够。
        """
        if level not in self._views:
            return
        selected = self._selected
        if selected is not None:
            self._anchor = selected
        self._level = level
        self._views[level].set_anchor(self._anchor)
        # **把选中的那一天交给这一层**（它成为当前层的那一刻）—— 各层只存一份「我该
        # 画哪一个」，页面才是那一份状态的持有者。
        self._pushing_selection = True
        try:
            self._views[level].set_selected(self._selected)
        finally:
            self._pushing_selection = False
        self._stack.setCurrentWidget(self._views[level])
        self._bar.set_level(level)
        self._bar.set_title(level, self._anchor)
        self.focus_current_view()
        self._refresh_window()
        self.level_changed.emit(level)

    def step(self, steps: int) -> None:
        """翻页。年 ± 一年、月 ± 一月、周 ± 七天。"""
        self._anchor = shift(self._level, self._anchor, steps)
        self._views[self._level].set_anchor(self._anchor)
        self._drop_selection_off_screen()
        self._bar.set_title(self._level, self._anchor)
        self._refresh_window()

    def go_today(self) -> None:
        self._anchor = self._today
        self._views[self._level].set_anchor(self._today)
        self._drop_selection_off_screen()
        self._bar.set_title(self._level, self._today)
        self._refresh_window()

    def _drop_selection_off_screen(self) -> None:
        """翻到了别的一段日子：选中的那一天不在这儿了，那块高亮就收掉。

        **留着就是指着屏幕外的一天**：年视图里点 5 月 16 号再按 ←，屏幕上是 2023 年，
        而那一格还指着 2024 年的 5 月 16 号 —— 用户以为改的是他看见的那一张。
        """
        selected = self._selected
        if selected is not None and not self.current_view().covers(selected):
            self.set_selected(None)

    def photo_count(self) -> int:
        """当前这一段里有多少张照片（标题栏与「空日历」提示都用它）。"""
        return self.current_view().photo_count()

    # ---- 缩略图 -----------------------------------------------------------
    def _refresh_window(self) -> None:
        """报一次「现在看得见哪些」（必要时）。**判断放在页面里**，不放在外面。

        ``ThumbnailQueue.set_window`` 是**整份替换**：换一层、翻一页、滚动去抖一次
        都要重算一遍「看得见哪几张」，而算出来的常常是同一批 —— 每次都重报等于把队列
        里还没跑的请求重排一遍，谁都排在后面。

        但「没变」**不够**：还得**手上仍然有**这几张图。``set_photos``（换库、重扫）
        与 ``forget_thumbnails``（文件变了）都会把手上的那份扔掉，而要的那一批本身
        没变 —— 于是那次重报被跳过，界面停在空白上，直到用户碰巧换个月又切回来
        （症状：月视图里点开一格再退出来，那一片的缩略图不出来了）。

        把这份判断放在页面里，是因为它要保护的东西就在这儿：``_thumbs``。放在外面
        就得在每一处丢图的调用点记得作废那份「报过了」的记录 —— 上一版就是这么漏的。

        报出去之后**不重试**是流水线自己的事：解不出来的路径它记在 ``_failed`` 里，
        再报也只是被跳过，不会反复解码。
        """
        view = self.current_view()
        edge = view.thumbnail_edge()
        if edge:
            self._thumbs.set_edge(edge)
        visible = view.visible_refs()
        if (
            visible == self._reported
            and edge == self._reported_edge
            and all(view.photo(ref) is not None for ref in visible)
        ):
            return
        self._reported = visible
        self._reported_edge = edge
        self.window_changed.emit()

    # ---- 配色 -------------------------------------------------------------
    def _apply_theme(self, theme: AppTheme) -> None:
        self._bar.set_colors(CalendarColors.derive(theme))
        for view in self._views.values():
            view.set_theme(theme)

    def _on_theme_changed(self) -> None:
        self._apply_theme(current_theme())

    # ---- 快捷键 -----------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        key = event.key()
        if key in _KEY_LEVELS:
            self.set_level(_KEY_LEVELS[key])
            return
        if key == Qt.Key.Key_Left:
            self.step(-1)
            return
        if key == Qt.Key.Key_Right:
            self.step(1)
            return
        if key == Qt.Key.Key_T:
            self.go_today()
            return
        super().keyPressEvent(event)


__all__ = ["REPAINT_INTERVAL_MS", "CalendarPage"]
