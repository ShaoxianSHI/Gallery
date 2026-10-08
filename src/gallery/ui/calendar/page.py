"""日历这一页：工具条 + 三层视图，负责状态与快捷键。

这一页只管**停在哪一层、停在哪一段日子、手上有哪些照片**。**``Y`` / ``M`` / ``W`` 收在这里，
不做成窗口级 QAction**：``←`` / ``→`` 在网格里是「挪当前项」、在这里是「翻页」，会互相抢
（ADR 0033 决策 6 —— 那条决策标题里的 ``D`` 已随日视图一起删掉，现在是三个键）。
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
from gallery.ui.theme import AppTheme, current_theme, ensure_installed

#: ``Y`` / ``M`` / ``W`` 三个键。**没有日视图**，所以也没有 ``D``。
_KEY_LEVELS = {
    Qt.Key.Key_Y: "year",
    Qt.Key.Key_M: "month",
    Qt.Key.Key_W: "week",
}

#: 「来了一张图，重画一次」那个防抖间隔（毫秒）。**0 = 下一轮事件循环**。
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
    #: 工具条上那几枚条件胶囊被点了（改 / 撤掉），带的是条件在列表里的序号（ADR 0040）。
    condition_edited = Signal(int)
    condition_removed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbStore()
        #: 上一次向流水线报出去的那一批（以及档位），见 `_refresh_window`。
        self._reported: list[PhotoRef] = []
        self._reported_edge = 0
        self._anchor = datetime.date.today()
        #: 选中（**高亮**）的是哪一天，**整页一份**；与 :attr:`_anchor`（停在哪一天）是两件事。
        self._selected: datetime.date | None = None
        self._today = datetime.date.today()
        self._level = "month"

        self._bar = CalendarBar()
        self._bar.step_requested.connect(self.step)
        self._bar.today_requested.connect(self.go_today)
        self._bar.level_changed.connect(self.set_level)
        # 工具条上那两枚胶囊与面包屑右端那几枚是**同一批条件**（ADR 0040 决策 9），所以信号
        # 原样往外转发 —— 外面接到的是同一个处理函数，两处不可能对不上。
        self._bar.condition_edited.connect(self.condition_edited)
        self._bar.condition_removed.connect(self.condition_removed)

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
            view.swiped.connect(self.step)
            self._stack.addWidget(view)
        #: 页面自己推给各层的那一遍（见 :meth:`_on_view_selected`）—— 那不是「用户选的」。
        self._pushing_selection = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self._stack, 1)

        # **用 ``ensure_installed()`` 交回来的监听器，别自己 ``findChild``**：它挂在
        # ``QApplication`` 上，而 ``findChild`` 只找自己的子孙 —— 找不到就永远不连接。
        self._watcher = ensure_installed()
        self._watcher.changed.connect(self._on_theme_changed)
        # 「来了一张缩略图 → 重画当前那层」的防抖定时器。**必须防抖**：``update()`` 立刻排一次整页
        # 重绘，而流水线是**一张一张**送图回来的（实测一张一张送 1890 ms，一次全送到 0.1 ms）。
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._repaint_current_view)
        self._apply_theme(current_theme())
        self.set_level("month")

    def set_photos(self, photos: Sequence[PhotoRef], values: Mapping[Path, PhotoFacets]) -> None:
        """换一整库的照片与元数据快照。分桶在这里一次算好，三层共用。

        **手上的缩略图一张都不扔**：仓库按「(路径, 档位)」记账，同一张照片换一遍库仍是那一张，
        变的只是「哪些格子有照片」—— 清空会让一屏已出图的格子全变回占位灰。文件真的变了走
        :meth:`forget_thumbnails`。
        """
        by_day = group_by_day(photos, values)
        by_hour = group_by_hour(photos, values)
        for view in self._views.values():
            view.set_buckets(by_day, by_hour)
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
        self._forget_reported()
        self._repaint_timer.start()

    def _repaint_current_view(self) -> None:
        """一张缩略图到了：让**画着格子的那个控件**重画（周视图里它不是这一层自己）。"""
        self.current_view().repaint_content()

    def _forget_reported(self) -> None:
        self._reported = []
        self._reported_edge = 0

    def level(self) -> str:
        return self._level

    def anchor(self) -> datetime.date:
        """停在哪**一天**（摆哪一段由当前那一层决定）。

        **从有照片的一格退回来时，它就是那一格的那一天**（见 :meth:`_reset_selection`），所以
        拿它去问「那一天是几号」问得到用户刚点的那一天。
        """
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

    def set_conditions(self, conditions: Sequence[str], *, editing: int | None = None) -> None:
        """把生效中的条件摆到工具条上（空 = 整块收起来，ADR 0040 决策 9）。

        **这一页自己那个面包屑是看不见的**（它挂在网格页上），所以条件不摆到工具条上，用户就
        看不到自己加了哪几条。
        """
        self._bar.set_conditions(conditions, editing=editing)

    def selected_refs(self) -> list[PhotoRef]:
        return self.current_view().selected_refs()

    def set_selected(self, day: datetime.date | None) -> None:
        """选中那一天（``None`` = 不选中任何一天）。

        **整页只有这一份**，推给当前那一层照着画：「在年视图里点一天 → 切到月视图」仍是同一天。
        """
        self._selected = day
        # **只推给当前那一层**：另外两层看不见，它们各自成为当前层时由 `set_level` 补上。
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
        """**静默**地收掉高亮，而把点过的那一天挪成锚点（外面没人要听「这一页刚露出来」）。

        **高亮与锚点是两件事**：高亮是「刚点了哪一格」，那一格展开后退回来时它是一块点不掉
        的底色；而**锚点是用户刚指着的那一天**，按 ``Y`` / ``M`` / ``W`` 该翻到它所在的那一层。
        **只挪页面这一份** —— 让周视图重新定位会把用户刚找到的滚动位置弄丢。
        """
        pointed = self._selected
        if pointed is not None:
            self._anchor = pointed
        self._selected = None
        self.current_view().clear_selection()

    def focus_current_view(self) -> None:
        """把键盘焦点交给当前那层 —— **必须点名，不能指望 Qt 自己挪**。

        从大图页退回来时 Qt 会把焦点交给页面上**第一个**能接焦点的控件，周视图里那是
        ``QScrollArea``，它会把 ← / → 当成滚动时间轴忽略掉。
        """
        self.current_view().setFocus()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """这一页重新露出来：把焦点还给当前那层，并收拾「选中」（见 :meth:`_reset_selection`）。

        **高亮收掉、锚点留下**，滚动位置**不动**（那才是用户刚找到的地方）。**第一次露出来也一并
        收掉**：起手那个「选中今天」没有人点过它，而没有点过的那一天就没有锚点可挪。
        """
        super().showEvent(event)
        self.focus_current_view()
        self._reset_selection()

    def set_level(self, level: str) -> None:
        """切到另一层。**指着哪一天，就翻到那一天所在的那一层。**

        「在年视图里点 5 月 16 号 → 按 W」必须看到 5 月 16 号那一周，所以切层前先把锚点挪到那一天
        上。「那一天」按新鲜程度取：还在高亮的那一天，否则是**退回来时留下的锚点**（见
        :meth:`_reset_selection`）。``Y`` / ``M`` / ``W``、工具条、会话恢复三条入口都走这里。
        """
        if level not in self._views:
            return
        selected = self._selected
        if selected is not None:
            self._anchor = selected
        self._level = level
        # **先把这一层摆上来，再定位**：滚动条范围要等这一层被布局过，顺序反了这次定位只能排到
        # 下一轮，于是**第一帧按旧位置画出来**（周视图里就是「一张图飞快跑下来」）。
        self._stack.setCurrentWidget(self._views[level])
        self._views[level].set_anchor(self._anchor)
        # **把选中的那一天交给这一层**：各层只存一份「我该画哪一个」，页面才是状态的持有者。
        self._pushing_selection = True
        try:
            self._views[level].set_selected(self._selected)
        finally:
            self._pushing_selection = False
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

        **留着就是指着屏幕外的一天**：年视图里点 5 月 16 号再按 ←，那一格还指着 2024 年。
        """
        selected = self._selected
        if selected is not None and not self.current_view().covers(selected):
            self.set_selected(None)

    def photo_count(self) -> int:
        """当前这一段里有多少张照片（标题栏与「空日历」提示都用它）。"""
        return self.current_view().photo_count()

    def _refresh_window(self) -> None:
        """报一次「现在看得见哪些」（必要时）。**这份判断放在页面里**，因为它要护着 ``_thumbs``。

        ``set_window`` 是**整份替换**，每次都重报等于把队列里还没跑的请求重排一遍；但「没变」不够
        —— 还得手上仍然有这几张图（``set_photos`` / ``forget_thumbnails`` 会把手上的扔掉而要的那批
        没变），少了这一条就会跳过重报、界面停在空白上。报出去不重试是流水线自己的事。
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

    def _apply_theme(self, theme: AppTheme) -> None:
        self._bar.set_colors(CalendarColors.derive(theme))
        for view in self._views.values():
            view.set_theme(theme)

    def _on_theme_changed(self) -> None:
        self._apply_theme(current_theme())

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
