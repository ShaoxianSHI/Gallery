"""窗口顶边那一行：摆位置、显示什么、按住拖动、双击铺满（ADR 0025 / 0037）。

它只管那一条 —— 改名的输入框归 :class:`~gallery.ui.window.widgets.title_bar.TitleBar`。
**双击做的是「铺满」不是真全屏**（见 :meth:`TitleBarFlowMixin._toggle_filled`），
而 Qt 的鼠标覆写留在 :class:`~gallery.ui.main_window.MainWindow` 自己身上（Qt 按 MRO 找
第一个实现）。
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QPointF

from gallery.ui.window.widgets.external_button import BUTTON_GAP, BUTTON_WIDTH, button_rect
from gallery.ui.window.widgets.search_field import HEIGHT as SEARCH_FIELD_HEIGHT
from gallery.ui.window.widgets.search_field import MARGIN_RIGHT as SEARCH_FIELD_MARGIN_RIGHT
from gallery.ui.window.widgets.search_field import TEXT_ALIGN_DROP as SEARCH_FIELD_TEXT_ALIGN_DROP
from gallery.ui.window.widgets.search_field import TOGGLE_EDGE as SCOPE_TOGGLE_EDGE
from gallery.ui.window.widgets.search_field import TOGGLE_GAP as SCOPE_TOGGLE_GAP
from gallery.ui.window.widgets.search_field import WIDTH as SEARCH_FIELD_WIDTH
from gallery.ui.window.widgets.title_bar import EXTRA_PADDING as TITLE_BAR_EXTRA
from gallery.ui.window.widgets.title_bar import HEIGHT as TITLE_BAR_HEIGHT

#: 网格页**右边**留多少（点），比左边窄得多：这一页最右边就是那条滚动条，它自己还带着
#: :data:`~gallery.ui.scrollbar_style.HANDLE_GAP` 那 2 点，两段加起来才是滑块右缘到窗口右缘的
#: 距离。**别把它并回** ``main_window._GRID_PAGE_MARGIN_H`` —— 左边那 10 点是给照片留的呼吸，
#: 右边这 2 点是给滚动条的，两件事。网格页的布局也要它（``MainWindow`` 从这里取）。
_GRID_PAGE_MARGIN_RIGHT: Final = 2

#: 从按下算起，光标要移动多少点才算「拖动」而不是「双击时手的抖动」。
#:
#: **不能用 ``QApplication.startDragDistance``**：那是**拖放（DnD）**的分界线，macOS 上
#: 是 10 点 —— 拿它当窗口拖动的那条线，窗口要先等光标走满 10 点才开始跟手，读起来就是
#: 「拖之前得先动一会儿」。macOS 自己的标题栏差不多在 2~3 点就动了。
DRAG_THRESHOLD: Final = 3


class TitleBarFlowMixin:
    _title_bar_drag: TitleBarDrag

    def _init_title_bar_drag(self) -> None:
        self._title_bar_drag = TitleBarDrag()

    # 这两个是转发 —— ``tests/ui/test_title_bar.py`` 直接摸它们断言「按下了」与「没在拖」。
    @property
    def _title_bar_drag_armed(self) -> bool:
        return self._title_bar_drag._armed

    @property
    def _title_bar_drag_origin(self) -> QPointF | None:
        return self._title_bar_drag._origin

    _title_bar_drag_origin: QPointF | None

    """摆那一条的位置、决定它显示什么、按住拖窗口、双击铺满。"""

    def _is_title_bar(self, point: QPointF) -> bool:
        """这一点是不是落在标题栏那一段（窗口顶边与内容区之间）。

        **真全屏时一律不是**（那一整条已经收起来了，见 :meth:`_layout_title_bar`），**铺满时
        照旧是** —— 那只是窗口变大，于是双击铺满之后还能双击还原。
        """
        if not self._owns_title_bar or self.isFullScreen():
            return False
        central = self.centralWidget()
        return central is not None and point.y() < central.geometry().top()

    def _press_belongs_to_the_title_bar(self, position: QPointF) -> bool:
        """这一次按下是不是**归标题栏**（「按住拖窗口」与「双击铺满」该轮到这里）。

        **改到一半不算**：那一段上正摆着输入框，拖走它等于把正在改的名字甩到屏幕外。两件事
        用同一句判据，于是不会出现「拖不动却能双击」这种半截状态。
        """
        return self._is_title_bar(position) and not self._title_bar.is_editing()

    def _arm_title_bar_drag(self, global_position: QPointF) -> None:
        """记下这一次按下落在标题栏（全局坐标）。

        **先不做决定**：这一下可能是双击的第一下，也可能是一拖的开头 —— 判据是「接下来
        动没动、动了多远」（见 :meth:`_title_bar_drag_began`），按下这一刻还看不出来。
        """
        self._title_bar_drag.arm(global_position)

    def _title_bar_drag_began(self, global_position: QPointF) -> bool:
        """鼠标已经移开够远，这一次按下可以确定是拖动而不是双击的一半。

        **按距离判，不按时间**：定一个「等满双击周期」的定时器看着像在模仿系统，实际是让
        窗口在这段时间里**一步都不跟手**，正常的一拖在时限之前就松手了。这条线是
        :data:`DRAG_THRESHOLD`（一个很小的数），**不是拖放那条 10 点**。
        """
        return self._title_bar_drag.moved_far_enough(global_position)

    def _disarm_title_bar_drag(self) -> None:
        """把「这一次按下」清掉：松手、双击、或者已经真的拖起来了，三种情况都到这儿。"""
        self._title_bar_drag.disarm()

    def _start_title_bar_drag(self) -> None:
        """真的发起系统拖动 —— 鼠标已经动得够远，确认这一下不是双击。

        **必须从一次鼠标事件里发起**：Qt 在 macOS 上把它实现成
        ``performWindowDragWithEvent:``，那个事件要么取自**当前**的 ``NSApp.currentEvent``，
        要么由 Qt 按当前指针位置现合成一个；从定时器回调里调它两样都不成立。
        ``startSystemMove`` 返回假（平台不支持、或系统自己会拖）就什么都不做。"""
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def _toggle_filled(self) -> None:
        """铺满 / 还原 —— 也就是 macOS 的**缩放**（AppKit 的 ``zoom:``，⌃⌘F 那个键）。

        **占满的是菜单栏与 Dock 之间的那块地方，两样都还在**，也不另开 Space：``showMaximized`` 在
        macOS 上正是 ``[NSWindow zoom:nil]``，落到 ``visibleFrame``。**这不是 macOS 的「填满」**
        （那个会盖住菜单栏与 Dock），系统设置里那一档**不读**。**还原回进铺满之前那一份几何**
        （``showNormal`` 恢复的正是它），拖到一半再双击回来，窗口该回到刚才那个大小与位置。"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _refresh_title(self) -> None:
        """窗口顶边那一行显示什么：**大图页是正在看的那张照片的文件名，其余是相册名**。

        判据只有这一段（「现在在哪一页 + 那一格是谁」），于是翻图、改名（含 ⌘Z 撤回）、换库走
        的都是同一段算术，不会有一处忘了更新而标题还写着旧名。
        """
        photo = self._model.photo_at(self._current_row) if self._in_detail_page() else None
        if photo is not None:
            name = photo.name
        elif self._drill_photos is not None:
            # 从日历某一格展开的那一层：标题要说得清在看哪一格，否则与「全部照片」长得一模一样。
            name = f"{self._drill_label} · {len(self._drill_photos)} 张"
        else:
            name = self._library_title
        if self._owns_title_bar:
            self._title_bar.set_text(name)
        else:
            # 标题栏那一段不归窗口（例如 Linux），照旧交给系统画（ADR 0025 决策 2）。
            self.setWindowTitle(name)

    def _layout_title_bar(self) -> None:
        """把 ``TitleBar`` 与搜索胶囊摆在窗口顶边那一条上。

        那一条的高度是**安全区**顶出来的那 28 点，不硬编码。**真全屏**时系统自己也不画
        标题栏，这一条跟着收起来（否则会盖在照片上，ADR 0025 决策 2）；铺满窗口不在此列。
        **两个控件是摆在一起的，不是一套布局**（ADR 0026 决策 8）。**内容区让出多少也归
        这里**：真全屏时那一段收起来，让出去的 12 点就该还回去。"""
        if not self._owns_title_bar:
            return
        # 判据是**真全屏，不是铺满**：铺满只是窗口变大，那一条照旧在（见 :meth:`_toggle_filled`）。
        full_screen = self.isFullScreen()
        self._stack.setContentsMargins(0, 0 if full_screen else TITLE_BAR_EXTRA, 0, 0)
        if full_screen:
            # 高度给 0，于是它与胶囊都不会盖在照片上（ADR 0025 决策 2）。
            self._title_bar.setGeometry(0, 0, self.width(), 0)
            self._title_bar.hide()
            self._search_field.hide()
            self._external_button.hide()
            self._scope_toggle.hide()
            return
        central = self.centralWidget()
        top = central.geometry().top() if central is not None else 0
        # 安全区之外**再加** :data:`TITLE_BAR_EXTRA`：三页的内容都从 ``安全区 + EXTRA`` 开始。
        height = max(top + TITLE_BAR_EXTRA, TITLE_BAR_HEIGHT)
        self._title_bar.setGeometry(0, 0, self.width(), height)
        self._title_bar.setVisible(True)
        # 胶囊摆在**这一条**里（内容区从 ``top + EXTRA`` 开始），再往下挪
        # :data:`SEARCH_FIELD_TEXT_ALIGN_DROP`。
        field_height = min(SEARCH_FIELD_HEIGHT, height - 4)
        # guard: allow-long-comment（间距与同高只从 SCOPE_TOGGLE_GAP 一处出，各算各的会歪）
        # **右上角那一块有两样东西**（ADR 0040 决策 5）：切换按钮贴着窗口右边，胶囊被推到它左边。
        # 两者的间距与同高都由 :data:`SCOPE_TOGGLE_GAP` 与 ``field_height`` 一处给出 —— 各算各的
        # 会「歪了一两个点」（见 ``external_button.button_rect``）。
        block_right = self.width() - SEARCH_FIELD_MARGIN_RIGHT
        toggle_width = min(SCOPE_TOGGLE_EDGE, max(0, field_height))
        toggle_x = block_right - toggle_width
        search_x = toggle_x - SCOPE_TOGGLE_GAP - SEARCH_FIELD_WIDTH
        self._search_field.setGeometry(
            search_x,
            (height - field_height) // 2 + SEARCH_FIELD_TEXT_ALIGN_DROP,
            SEARCH_FIELD_WIDTH,
            field_height,
        )
        self._search_field.show()
        # **要抬一次**：布局跑完之后，中央控件与菜单栏会被抬到「最后创建的子控件」之上把胶囊盖住。
        self._search_field.raise_()
        # 切换按钮**建在胶囊之后**（`_build_ui` 里），于是这一次抬就是为它准备的。
        self._scope_toggle.setGeometry(toggle_x, self._search_field.y(), toggle_width, field_height)
        self._scope_toggle.show()
        self._scope_toggle.raise_()
        #: **窗口右缘到胶囊右缘**有多远 —— 面包屑那几枚条件胶囊对齐的就是这条线（ADR 0026 决策
        #: 8），这一个数是两者之间唯一的换算依据。
        inset_to_capsule = SEARCH_FIELD_MARGIN_RIGHT + toggle_width + SCOPE_TOGGLE_GAP

        # 「用外部应用打开」**从胶囊那一块往左长出来**（见 ``button_rect``），于是两者必然同高、同一
        # 基线。**显隐的判据只有一处**（``is_available``）；真全屏时它整个不见。
        if self._external_button.isVisible():
            self._external_button.setGeometry(button_rect(self._search_field.geometry()))
            self._external_button.raise_()
        # 标题文字与 ⌘R 的输入框都要给右上角这一整块让出来（胶囊**加上左边的按钮**）。
        self._title_bar.reserve_right(
            inset_to_capsule
            + SEARCH_FIELD_WIDTH
            + SEARCH_FIELD_MARGIN_RIGHT
            + (BUTTON_WIDTH + BUTTON_GAP if self._external_button.isVisible() else 0)
        )

        # guard: allow-long-comment（对齐的是胶囊不是那一整块；对齐到按钮右缘会在中间空出一截）
        # 面包屑右端那几枚条件胶囊与这个胶囊**右缘对齐**；换算只在这一处做 —— 面包屑摆在一份有
        # 边距的布局里，右缘比窗口右缘靠内 :data:`_GRID_PAGE_MARGIN_RIGHT` 点。
        # **对齐的是胶囊而不是那一整块**：切换按钮在胶囊右边，让条件胶囊对齐到按钮的右缘会在
        # 中间空出一截，看着像它们不属于任何一个框。
        self._breadcrumb.set_right_inset(inset_to_capsule - _GRID_PAGE_MARGIN_RIGHT)

    def _sync_fill_action(self) -> None:
        """「铺满窗口 / 还原窗口」那一句跟着此刻的状态走。

        **两处都要调**：菜单那一条平时跟着 :meth:`_update_action_states` 走，而双击标题栏与按
        ⌃⌘F 都不经过那条路。**判据是「铺满」而不是「真全屏」** —— 菜单上这一条说的就是铺满，
        与绿按钮悬停菜单里的「进入全屏幕」不是同一件事。
        """
        self._fill_action.setText("还原窗口" if self.isMaximized() else "铺满窗口")


__all__ = ["DRAG_THRESHOLD", "TitleBarFlowMixin"]


class TitleBarDrag:
    """**「这一次按下」的状态**（ADR 0058 阶段 4）。

    按下落在标题栏只说明「可能是拖的开头」，这一下也可能是双击的第一下 ——
    判据是接下来动没动、动了多远（见 :meth:`moved_far_enough`）。

    那两个字段原先在 ``MainWindow`` 上，而只有这一间改它们。
    """

    __slots__ = ("_armed", "_origin")

    def __init__(self) -> None:
        self._armed = False
        self._origin: QPointF | None = None

    def arm(self, position: QPointF) -> None:
        """记下这一次按下落在哪（全局坐标）。**先不做决定**。"""
        self._armed = True
        self._origin = position

    def moved_far_enough(self, position: QPointF) -> bool:
        """移开够远就确定是拖动。

        **按距离判，不按时间**：定一个「等满双击周期」的定时器看着像在模仿系统，实际是
        让窗口在这段时间里**一步都不跟手**，正常的一拖在时限之前就松手了。
        """
        if not self._armed or self._origin is None:
            return False
        return (position - self._origin).manhattanLength() >= DRAG_THRESHOLD

    def disarm(self) -> None:
        """清掉「这一次按下」：松手、双击、或已经真的拖起来了，三种都到这儿。"""
        self._armed = False
        self._origin = None
