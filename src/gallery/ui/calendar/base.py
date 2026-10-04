"""日历四层的公共底子：数据、配色、锚点、选中、缩略图。

四个视图（年 / 月 / 周 / 日）要的东西几乎一样：一份按天分好的桶、一份按小时分好的桶、
当前停在哪一天、今天是几号、点到了哪天哪一格。差的那部分只有「画成什么样」，
所以这里只留状态与信号，具体怎么画交给各层。

**所有日期都是 ``datetime.date``**，不引入 ``QDate`` —— 日历的全部几何都是除法，
没有必要为了一个整数字段引入第二个日期类型（核心层同样只用标准库，核心层不得依赖 Qt）。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from gallery.core.library.calendar import (
    DayBuckets,
    HourBuckets,
    count_days,
    days_of,
    title,
)
from gallery.core.library.photos import PhotoRef
from gallery.ui.calendar.colors import CalendarColors
from gallery.ui.calendar.painting import begin as begin_painter
from gallery.ui.calendar.thumbs import ThumbStore
from gallery.ui.theme import AppTheme, current_theme

#: 文件名开头的序号 + 日期 + 机器码，例如 ``018_2021-09-12_D6D7FFA9_``。
_FILENAME_PREFIX = re.compile(r"^\d+_[\d-]+_[0-9A-Fa-f]{8}_*")


class LevelView(QWidget):
    """一层日历视图的底子。"""

    #: 用户点了某一天（那一格上有照片）：接上一层把它整个摊开。
    day_requested = Signal(object)
    #: 用户点了某几张照片（周 / 日两层）：外面据此打开大图页。
    photo_requested = Signal(object, int)
    #: 选中的东西变了（外面据此改标题与动作可用性）。
    selection_changed = Signal(object)
    #: 这一层需要的缩略图变了（外面据此向流水线要图）。
    window_changed = Signal()

    def __init__(self, level: str, thumbs: ThumbStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = level
        self._thumbs = thumbs
        self._by_day: DayBuckets = {}
        self._by_hour: HourBuckets = {}
        self._anchor = datetime.date.today()
        self._today = datetime.date.today()
        self._selected = datetime.date.today()
        # 起手就读一次当前主题：控件在构造时还没有被 ``apply_theme`` 走过，
        # 而日历一上来就要有底色（否则第一帧是黑的，下一帧才正常）。
        theme = current_theme()
        self._colors = CalendarColors.derive(theme)
        self._background = QColor(theme.background)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---- 数据 -------------------------------------------------------------
    @property
    def level(self) -> str:
        return self._level

    def set_buckets(self, by_day: DayBuckets, by_hour: HourBuckets) -> None:
        """换一整库的分桶。**不重置选中**：换库时外面会自己把选中清掉。"""
        self._by_day = by_day
        self._by_hour = by_hour
        self.update()
        self.window_changed.emit()

    @property
    def colors(self) -> CalendarColors:
        return self._colors

    @property
    def background(self) -> QColor:
        """日历画在哪个底色上（整个窗口共用的那一块）。"""
        return self._background

    def set_theme(self, theme: AppTheme) -> None:
        """系统换浅色 / 深色：重算配色并重画。"""
        self._colors = CalendarColors.derive(theme)
        self._background = QColor(theme.background)
        self.update()

    # ---- 位置 -------------------------------------------------------------
    def anchor(self) -> datetime.date:
        return self._anchor

    def days(self) -> list[datetime.date]:
        return days_of(self._level, self._anchor)

    def title(self) -> str:
        return title(self._level, self._anchor)

    def photo_count(self) -> int:
        """当前这一段日子里有多少张照片（标题栏与「空日历」提示都用它）。"""
        return count_days(self._by_day, self.days())

    def set_anchor(self, anchor: datetime.date) -> None:
        self._anchor = anchor
        self.update()
        self.window_changed.emit()

    def today(self) -> datetime.date:
        """「今天」是哪一天。截图与测试里它会被指到别的日子（真日历才能看到效果）。"""
        return self._today

    def set_today(self, today: datetime.date) -> None:
        self._today = today
        self.update()

    def set_selected(self, day: datetime.date) -> None:
        self._selected = day
        self.update()
        self.selection_changed.emit(self.selected_refs())

    # ---- 选取 -------------------------------------------------------------
    def photos_of(self, day: datetime.date) -> Sequence[PhotoRef]:
        return self._by_day.get(day, ())

    def selected_refs(self) -> list[PhotoRef]:
        return list(self.photos_of(self._selected))

    def select_day(self, day: datetime.date, *, descend: bool = False) -> None:
        """点到了某一天。``descend`` 为真时，若那天有照片就把它整个摊开（切到日视图）。"""
        self.set_selected(day)
        if descend and self.photos_of(day):
            self.day_requested.emit(day)

    # ---- 供外面问的几件事 --------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """这一层当前看得见、因此需要缩略图的照片。默认不铺照片（年视图另行实现）。"""
        return []

    def thumbnail_edge(self) -> int:
        """这一层要缩略图缩到多长（长边像素）。"""
        return 0

    # ---- 绘制 -------------------------------------------------------------
    def begin(self) -> QPainter:
        return begin_painter(self, self._background)

    def photo(self, ref: PhotoRef):  # type: ignore[no-untyped-def]
        return self._thumbs.get(ref.path)

    def column_tint(self, day: datetime.date) -> QColor:
        """周末那一列的底色；不是周末就用窗口自己的底（等于不涂）。"""
        return self._colors.weekend if day.weekday() >= 5 else self._background


def short_title(path: Path, limit: int = 24) -> str:
    """一格宽的照片标题。

    照片的文件名常常把一堆与内容无关的东西带在最前面 —— 测试相册那种
    ``018_2021-09-12_D6D7FFA9#巴黎#凡尔赛宫`` 里的序号、日期与机器码，
    还有 ``#`` 后面那一串地点。日历里每条只有一百多像素宽，先剥掉这些才看得出
    「这是哪一张」。
    """
    stem = path.stem
    if "#" in stem:
        return stem.rsplit("#", 1)[-1][:limit]
    cleaned = _FILENAME_PREFIX.sub("", stem)
    return (cleaned or stem)[:limit]


__all__ = ["LevelView", "short_title"]
