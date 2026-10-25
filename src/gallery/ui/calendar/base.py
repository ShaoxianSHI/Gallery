"""日历三层的公共底子：数据、配色、锚点、选中、缩略图 —— 三层差的只有「画成什么样」。

**所有日期都是 ``datetime.date``**，不引入 ``QDate``：核心层不得依赖 Qt，几何全是除法。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QWheelEvent
from PySide6.QtWidgets import QWidget

from gallery.core.i18n import t
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
from gallery.ui.calendar.thumb_store import ThumbStore
from gallery.ui.theme import AppTheme, current_theme

#: 「几何变了 → 重报一次看得见哪些」的防抖间隔（毫秒）。与网格那一侧同一个数。
#: 拖窗口边时 ``resizeEvent`` 连发几十次；单次定时器的 ``start()`` 会原地续期，于是只报一次。
GEOMETRY_DEBOUNCE_MS = 60

#: 滚动事件里「手指已经离板子、系统在补惯性」的阶段：**那一段不该翻页**，否则手一离板还会再跳。
_MOMENTUM_PHASE = Qt.ScrollPhase.ScrollMomentum


class LevelView(QWidget):
    """一层日历视图的底子。"""

    #: 用户点了日历上的一格：``(这一格的照片, 这一格的标题)``。打开大图还是摊成网格由外面定。
    cell_requested = Signal(object, str)
    #: 选中的东西变了（外面据此改标题与动作可用性）。
    selection_changed = Signal(object)
    #: 这一层需要的缩略图变了（外面据此向流水线要图）。
    window_changed = Signal()
    #: 用户在视图上横向甩了一下：``1`` = 往后一段，``-1`` = 往前一段（翻页归 ``page.step``）。
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
        #: 「甩一下」的判据。**三层各留一份**：累积到一半切层再切回来，不该接着上次那半下算。
        self._swipe = SwipeDetector()
        # 构造时还没被 ``apply_theme`` 走过，而起手就得有底色（否则第一帧是黑的）。
        theme = current_theme()
        self._colors = CalendarColors.derive(theme)
        self._background = QColor(theme.background)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 子定时器而不是 ``QTimer.singleShot``：后者与本对象无关，「关窗 / 换库」那轮照样触发。
        self._geometry_notify = QTimer(self)
        self._geometry_notify.setSingleShot(True)
        self._geometry_notify.setInterval(GEOMETRY_DEBOUNCE_MS)
        self._geometry_notify.timeout.connect(self.window_changed)

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

    def anchor(self) -> datetime.date:
        return self._anchor

    def days(self) -> list[datetime.date]:
        """这一层**自己**的那几天（不含上下月占位的格子）—— 「有几张」按这个数。"""
        return days_of(self._level, self._anchor)

    def title(self) -> str:
        return title(self._level, self._anchor)

    def photo_count(self) -> int:
        """当前这一段日子里的照片数（**邻月的不算**；标题与「空日历」提示都用它）。"""
        return count_days(self._by_day, self.days())

    def set_anchor(self, anchor: datetime.date) -> None:
        self._anchor = anchor
        self.repaint_content()
        self.window_changed.emit()

    def today(self) -> datetime.date:
        """「今天」是哪一天（截图与测试里它会被指到别的日子，真日历才看得到效果）。"""
        return self._today

    def set_today(self, today: datetime.date) -> None:
        self._today = today
        self.repaint_content()

    def set_selected(self, day: datetime.date | None) -> None:
        """选中那一天（``None`` = 不选中任何一天）。

        **这一份归 :class:`~gallery.ui.calendar.page.CalendarPage` 管**，三层各存一份只为照着画。
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

        口径就是 :meth:`days`：占位的格子点不动，也就没有「点中一个邻月的日子」这回事。
        """
        return day in self.days()

    def photos_of(self, day: datetime.date) -> Sequence[PhotoRef]:
        return self._by_day.get(day, ())

    def selected_refs(self) -> list[PhotoRef]:
        return list(self.photos_of(self._selected)) if self._selected is not None else []

    def select_day(self, day: datetime.date) -> None:
        """只改选中，不展开（周视图点格子时选中那天用的就是它）。"""
        self.set_selected(day)

    def select_cell(self, photos: Sequence[PhotoRef], label: str) -> None:
        """点到了有照片的一格：选中它，然后问外面「这一格怎么展开」。"""
        if not photos:
            return
        self.cell_requested.emit(list(photos), label)

    def visible_refs(self) -> list[PhotoRef]:
        """这一层当前看得见、因此需要缩略图的照片（默认不铺照片，年视图另行实现）。"""
        return []

    def thumbnail_edge(self) -> int:
        """这一层要缩略图缩到多长（长边像素）。"""
        return 0

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """触控板**两指横向拖动** = 翻一页（**往左划 = 往后一段**，与 ``←`` / ``→`` 同一件事）。

        **macOS 的两指划走的是滚轮事件**，不是 ``QPanGesture``（那一套真机上收不到）。
        **只接横向**（斜着划也算竖着）：竖着划照旧交给基类，周视图靠它滚时间轴。
        """
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        horizontal = abs(scroll_delta(float(pixel.x()), float(angle.x())))
        vertical = abs(scroll_delta(float(pixel.y()), float(angle.y())))
        if horizontal <= vertical:
            super().wheelEvent(event)
            return
        phase = event.phase()
        if phase == _MOMENTUM_PHASE:
            event.accept()
            return
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._swipe.begin()
        steps = self._swipe.feed(float(pixel.x()), float(angle.x()))
        if steps:
            self.swiped.emit(steps)
        event.accept()

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - 名字是 Qt 定的
        """改了几何：**看得见哪些也跟着变了**，于是要重新报一次。

        没有这一步，外面拿着的还是**改之前**那份清单 —— 多出来的那些格子没人要图，
        就一直空着，直到用户碰巧滚一下或换一层才补上。
        """
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._geometry_notify.start()

    def repaint_content(self) -> None:
        """让**画着内容的那个控件**重画一次 —— 周视图的格子画在时间轴子控件上，不是自己身上。

        统一走这一句，外面（收到缩略图之后那次重画）才不会漏掉它：漏掉的表现是「图已经在
        手上，屏幕上还是占位灰，**等多久都不变**，一滚动或一换层就出来」。
        """
        self.update()

    def begin(self) -> QPainter:
        return begin_painter(self)

    def photo(self, ref: PhotoRef):  # type: ignore[no-untyped-def]
        return self._thumbs.get(ref.path)


def day_label(day: datetime.date) -> str:
    """一格（一天）在标题里怎么写 —— 展开成网格时那一层的名字。"""
    return t("calendar.day_label", month=day.month, day=day.day)


def hour_label(day: datetime.date, hour: int) -> str:
    """一格（一个钟点）在标题里怎么写。"""
    return t("calendar.hour_label", month=day.month, day=day.day, hour=hour)


__all__ = ["GEOMETRY_DEBOUNCE_MS", "LevelView", "day_label", "hour_label"]
