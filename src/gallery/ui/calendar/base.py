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
from PySide6.QtGui import QColor, QPainter, QWheelEvent
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
from gallery.ui.calendar.swipe import SwipeDetector, scroll_delta
from gallery.ui.calendar.thumbs import ThumbStore
from gallery.ui.theme import AppTheme, current_theme

#: 「几何变了 → 重报一次看得见哪些」的闸门（毫秒）。与网格那一侧同一个数。
#:
#: **拖窗口边时 ``resizeEvent`` 连发几十次**，而每一次都要重算一遍「现在看得见哪几张」
#: 并向流水线报一次 —— 单次定时器的 ``start()`` 在已经跑着的时候会原地续期，于是整段拖动
#: 只在停下来之后报一次。
GEOMETRY_DEBOUNCE_MS = 60

#: 滚动事件里「手指已经离板子、系统在补惯性」的阶段。**那一段不该翻页。**
#:
#: macOS 的 ``NSEventPhaseMomentum*`` 原样映射成这一个值（Qt 只绑了旧的这四个：
#: ``ScrollBegin`` / ``ScrollUpdate`` / ``ScrollEnd`` / ``ScrollMomentum``），
#: 手指还按着板子的那三种都不在里头。
_MOMENTUM_PHASE = Qt.ScrollPhase.ScrollMomentum


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
    #: 用户在视图上横向甩了一下（触控板两指）：``1`` = 往后一段，``-1`` = 往前一段。
    #:
    #: **与 ``cell_requested`` 分开**：那一条问的是「这里拍了什么」，这一条问的是
    #: 「上一段 / 下一段」—— 翻页这件事归页面（:meth:`page.CalendarPage.step`）管，
    #: 三层各发同一个方向的整数，不自己挪锚点。
    swiped = Signal(int)

    def __init__(self, level: str, thumbs: ThumbStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = level
        self._thumbs = thumbs
        self._by_day: DayBuckets = {}
        self._by_hour: HourBuckets = {}
        self._anchor = datetime.date.today()
        self._today = datetime.date.today()
        self._selected: datetime.date | None = datetime.date.today()
        #: 「甩一下」的判据（阈值与那几道闸在 :mod:`~gallery.ui.calendar.swipe`，
        #: **三层各留一份**：攒到一半切到别的层再切回来，不该接着上一次那半下算）。
        self._swipe = SwipeDetector()
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
        self.repaint_content()
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
        self.repaint_content()

    # ---- 位置 -------------------------------------------------------------
    def anchor(self) -> datetime.date:
        return self._anchor

    def days(self) -> list[datetime.date]:
        """这一层**自己**的那几天（不含上下月补位）—— 「有几张」按这个数。

        铺格子用的 6 × 7（含补位）是另一张表，各视图自己拿
        :func:`~gallery.core.library.calendar.month_matrix` 画。
        """
        return days_of(self._level, self._anchor)

    def title(self) -> str:
        return title(self._level, self._anchor)

    def photo_count(self) -> int:
        """当前这一段日子里有多少张照片（标题栏与「空日历」提示都用它）。

        **邻月的照片不算**：停在九月，十月 1 号那一张既不在九月的格子里，
        也不该出现在九月的张数里。
        """
        return count_days(self._by_day, self.days())

    def set_anchor(self, anchor: datetime.date) -> None:
        self._anchor = anchor
        self.repaint_content()
        self.window_changed.emit()

    def today(self) -> datetime.date:
        """「今天」是哪一天。截图与测试里它会被指到别的日子（真日历才能看到效果）。"""
        return self._today

    def set_today(self, today: datetime.date) -> None:
        self._today = today
        self.repaint_content()

    def set_selected(self, day: datetime.date | None) -> None:
        """选中那一天（``None`` = 不选中任何一天）。

        **这一份归 :class:`~gallery.ui.calendar.page.CalendarPage` 管**，三层共用：
        外面推下来一次，三层各存一份只为了照着画（见
        :meth:`~gallery.ui.calendar.page.CalendarPage.set_selected`）。
        """
        self._selected = day
        self.repaint_content()
        self.selection_changed.emit(self.selected_refs())

    def clear_selection(self) -> None:
        """不选中任何一天。"""
        self.set_selected(None)

    def selected_day(self) -> datetime.date | None:
        """选中的是哪一天（没有选中就是 ``None``）。"""
        return self._selected

    def covers(self, day: datetime.date) -> bool:
        """这一天在这一层**自己的**那几天里吗（翻页之后据此收掉选中的高亮）。

        口径就是 :meth:`days`：周视图是周日到周六，月 / 年视图是那一月 / 那一年自己的
        29–31 天（**不含补位** —— 补位格点不动，也就没有「点中一个邻月的日子」这回事）。
        """
        return day in self.days()

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

    def prefetch_refs(self) -> list[PhotoRef]:
        """**顺手先解**的那几张（低优先级），默认不预取。

        翻页是日历里最高频的动作，而下一段日子的图要等切过去才开始解 —— 于是第一次
        看到的总是先灰一下再出图（真机上那一张原图要解几百毫秒），而切走再切回来就
        不灰了（图已经在手上）。预取就是把「切回来才有的那个便宜」提前拿到。
        """
        return []

    # ---- 手势 -------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """触控板**两指横向拖动** = 翻一页（与 ``←`` / ``→`` 同一件事，见
        :mod:`~gallery.ui.calendar.swipe`）。

        **macOS 的两指划走的是滚轮事件**，不是 ``QPanGesture`` —— 所以判据只在
        ``QWheelEvent`` 这一处，不要另起一套手势（那一套在真机上收不到）。

        **往左划 = 往后一段**（拖动那一页纸的方向，理由见 ``swipe`` 的模块说明）。

        **只接横向**：竖着划照旧交给基类，周视图那个 ``QScrollArea`` 正是靠它滚时间轴。
        斜着划（横向小于纵向）也算竖着 —— 斜着滚时间轴是常事，斜一下就翻页不是。
        """
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        horizontal = abs(scroll_delta(float(pixel.x()), float(angle.x())))
        vertical = abs(scroll_delta(float(pixel.y()), float(angle.y())))
        if horizontal <= vertical:
            super().wheelEvent(event)
            return
        phase = event.phase()
        # **手指抬起来之后 macOS 自己补的那一截惯性不算**：手已经离板子了却还在翻页，
        # 读起来是「我明明停手了，它自己又跳了一个月」。
        if phase == _MOMENTUM_PHASE:
            event.accept()
            return
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._swipe.begin()
        steps = self._swipe.feed(float(pixel.x()), float(angle.x()))
        if steps:
            self.swiped.emit(steps)
        event.accept()

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
    def repaint_content(self) -> None:
        """让**画着内容的那个控件**重画一次。

        **不是每一层都画在自己身上**：月 / 年视图把格子画在自己身上（``self.update()``
        就够），而周视图画在时间轴那一个子控件上（见 :meth:`WeekView.repaint_content`）。
        统一走这一句，外面（``CalendarPage`` 收到缩略图之后那次重画）才不会漏掉周视图 ——
        漏掉的表现是「图明明已经在手上，屏幕上还是占位灰，**等多久都不变**，一滚动
        或一换层就出来」（滚动与换层会强制重画那一块）。
        """
        self.update()

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
