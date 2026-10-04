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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent
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


class CalendarPage(QWidget):
    """日历那一页。"""

    #: 停在哪一层了（外面据此记进会话）。
    level_changed = Signal(str)
    #: 用户点了日历上一格里的照片（外面据此打开大图页，或摊成一层网格）。
    cell_requested = Signal(object, str)
    #: 这一层需要的缩略图变了（外面据此向流水线要图）。
    window_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbStore()
        #: 上一次向流水线报出去的那一批（以及档位），见 `_refresh_window`。
        self._reported: list[PhotoRef] = []
        self._reported_edge = 0
        self._anchor = datetime.date.today()
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
            self._stack.addWidget(view)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self._stack, 1)

        ensure_installed()
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
        if isinstance(path, Path) and isinstance(image, QImage):
            self._thumbs.set(path, image, edge)
            self.current_view().update()

    def forget_thumbnails(self, paths: Iterable[Path]) -> None:
        self._thumbs.forget(paths)
        # 忘掉的那几张可能正在可见区里，所以「报过了」那份记录同样作废。
        self._forget_reported()
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

    def set_level(self, level: str) -> None:
        if level not in self._views:
            return
        self._level = level
        self._views[level].set_anchor(self._anchor)
        self._stack.setCurrentWidget(self._views[level])
        self._bar.set_level(level)
        self._bar.set_title(level, self._anchor)
        self._views[level].setFocus()
        self._refresh_window()
        self.level_changed.emit(level)

    def step(self, steps: int) -> None:
        """翻页。年 ± 一年、月 ± 一月、周 ± 七天、日 ± 一天。"""
        self._anchor = shift(self._level, self._anchor, steps)
        self._views[self._level].set_anchor(self._anchor)
        self._bar.set_title(self._level, self._anchor)
        self._refresh_window()

    def go_today(self) -> None:
        self._anchor = self._today
        self._views[self._level].set_anchor(self._today)
        self._bar.set_title(self._level, self._today)
        self._refresh_window()

    def open_day(self, day: datetime.date) -> None:
        """把某一天整个摊开（年 / 月两层里点到了那一天）。"""
        self._anchor = day
        self.set_level("day")

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


__all__ = ["CalendarPage"]
