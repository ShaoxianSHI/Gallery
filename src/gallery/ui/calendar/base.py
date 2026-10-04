"""日历三层的公共底子：数据、配色、锚点、选中、缩略图。

三个视图（年 / 月 / 周）要的东西几乎一样：一份按天分好的桶、一份按小时分好的桶、
当前停在哪一天、今天是几号、点到了哪天哪一格。差的那部分只有「画成什么样」，
所以这里只留状态与信号，具体怎么画交给各层。

**所有日期都是 ``datetime.date``**，不引入 ``QDate`` —— 日历的全部几何都是除法，
没有必要为了一个整数字段引入第二个日期类型（核心层同样只用标准库，核心层不得依赖 Qt）。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
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

#: 「几何变了 → 重报一次看得见哪些」的闸门（毫秒）。与网格那一侧同一个数。
#:
#: **拖窗口边时 ``resizeEvent`` 连发几十次**，而每一次都要重算一遍「现在看得见哪几张」
#: 并向流水线报一次 —— 单次定时器的 ``start()`` 在已经跑着的时候会原地续期，于是整段拖动
#: 只在停下来之后报一次。
GEOMETRY_DEBOUNCE_MS = 60


class LevelView(QWidget):
    """一层日历视图的底子。"""

    #: 用户点了日历上的一格（那一格上有照片）：``(这一格的照片, 这一格的标题)``。
    #:
    #: **只有这一个动作**：一层日历里点任何一格，问的都是同一个问题 ——「这里拍了什么」。
    #: 外面据此决定是直接打开大图（就一张）还是摊成一层网格（好几张），界面自己不必分情况。
    cell_requested = Signal(object, str)
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
        self._selected: datetime.date | None = datetime.date.today()
        # 起手就读一次当前主题：控件在构造时还没有被 ``apply_theme`` 走过，
        # 而日历一上来就要有底色（否则第一帧是黑的，下一帧才正常）。
        theme = current_theme()
        self._colors = CalendarColors.derive(theme)
        self._background = QColor(theme.background)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 「几何变了要重报一次窗口」的闸门（见 :meth:`resizeEvent`）。
        #
        # **做成自己的子定时器**而不是 ``QTimer.singleShot(0, ...)``：后者与本对象毫无
        # 关系，于是「关窗 / 换库」那一轮里它照样会触发，去调一个已经析构的槽
        # （同一个道理见 ``browser/grid.py`` 的 ``eventFilter`` 与 ``time_view`` 的
        # ``_reveal``）。
        self._geometry_notify = QTimer(self)
        self._geometry_notify.setSingleShot(True)
        self._geometry_notify.setInterval(GEOMETRY_DEBOUNCE_MS)
        self._geometry_notify.timeout.connect(self.window_changed)

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

    def set_selected(self, day: datetime.date | None) -> None:
        """选中那一天（``None`` = 不选中任何一天）。"""
        self._selected = day
        self.update()
        self.selection_changed.emit(self.selected_refs())

    def clear_selection(self) -> None:
        """不选中任何一天。"""
        self.set_selected(None)

    # ---- 选取 -------------------------------------------------------------
    def photos_of(self, day: datetime.date) -> Sequence[PhotoRef]:
        return self._by_day.get(day, ())

    def selected_refs(self) -> list[PhotoRef]:
        return list(self.photos_of(self._selected)) if self._selected is not None else []

    def select_day(self, day: datetime.date) -> None:
        """只改选中，不摊开（周视图点格子时选中那天用的就是它）。"""
        self.set_selected(day)

    def select_cell(self, photos: Sequence[PhotoRef], label: str) -> None:
        """点到了有照片的一格：选中它，然后问外面「这一格怎么摊开」。"""
        if not photos:
            return
        self.cell_requested.emit(list(photos), label)

    # ---- 供外面问的几件事 --------------------------------------------------
    def visible_refs(self) -> list[PhotoRef]:
        """这一层当前看得见、因此需要缩略图的照片。默认不铺照片（年视图另行实现）。"""
        return []

    def thumbnail_edge(self) -> int:
        """这一层要缩略图缩到多长（长边像素）。"""
        return 0

    # ---- 几何 -------------------------------------------------------------
    def resizeEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        """改了几何：**看得见哪些也跟着变了**，于是要重新报一次。

        没有这一步就是「周视图的缩略图有时出不来」：拖一下窗口边会多出几个小时的格子
        （或换掉整屏的那一批），而外面仍然拿着**改之前**那份清单 —— 多出来的那些没人要图，
        就一直空着，直到用户碰巧滚一下或换一层才补上。

        **去抖 60 ms**（见 :data:`GEOMETRY_DEBOUNCE_MS`）：拖窗口边时这一串连发几十次，
        而每一次都要重算一遍「现在看得见哪几张」。
        """
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._geometry_notify.start()

    # ---- 绘制 -------------------------------------------------------------
    def begin(self) -> QPainter:
        return begin_painter(self, self._background)

    def photo(self, ref: PhotoRef):  # type: ignore[no-untyped-def]
        return self._thumbs.get(ref.path)


def day_label(day: datetime.date) -> str:
    """一格（一天）在标题里怎么写 —— 摊开成网格时那一层的名字。"""
    return f"{day.month}月{day.day}日"


def hour_label(day: datetime.date, hour: int) -> str:
    """一格（一个钟点）在标题里怎么写。"""
    return f"{day.month}月{day.day}日 {hour:02d}:00"


__all__ = ["GEOMETRY_DEBOUNCE_MS", "LevelView", "day_label", "hour_label"]
