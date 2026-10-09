"""让那条栈成为「我在哪」的**唯一来源** —— 六个字段退成从栈算出来的属性（ADR 0041 实施步骤 2）。

六个名字从实例字段变成 ``property``，读的地方照旧；赋值统一改成压栈 / 弹栈 / 换根，写的地方
从 30 处降到 2 处（``_push_frame`` / ``_pop_frame``）。两个容易搞错的判据写成独立属性：
``_group_view`` 是「最近一个分组帧」而非「栈顶那一帧」（分组可以压在相册或搜索结果上）；
``_current_dir`` 是「相册链上最近的那一帧」（ADR 0041 决策 1），日历视图下它是根。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.ui.window.state.level_stack import (
    AlbumFrame,
    CalendarFrame,
    GroupFrame,
    LevelStack,
    PageRole,
    PhotoFrame,
    RootKind,
    SearchFrame,
    browse_mode_of,
    page_role_of,
)


class _StackHost(Protocol):
    """带一条栈的宿主。这里只声明**这个 mixin 用到的那几个**。

    **刻意不 import ``MainWindow``**：那个类 import 本模块会成环（它在基类列表里带本 mixin）。
    """

    _level_stack: LevelStack
    _library: object

    def _reload_level(self, *, focus: object = None) -> None: ...


class LevelStackMixin:
    """栈的读写面：六个字段的兼容层 + 压栈 / 弹栈的那两条路。"""

    # ---- 那六个字段（现在是属性了）---------------------------------------------

    @property
    def _browse_mode(self) -> str:
        """浏览方式 = **栈底那一帧**（ADR 0041 决策 1），不再是独立字段。

        ``_browse_mode`` 有 25 处读，全在这一句里换算 —— 而「换浏览方式」从此只有一处会做
        （:meth:`_set_root`），不可能再出现「改了浏览方式却忘了清分组」那种状态。
        """
        return browse_mode_of(self._level_stack.root)

    @property
    def _current_dir(self) -> Path | None:
        """相册链上最近的那一帧；**在日历视图下是根**（日历页没有子目录）。

        返回根而不是 ``None``：``enter_folder`` 的祖先判断拿它与库根比，而日历上压根不该走那条路。
        """
        for frame in reversed(self._level_stack.frames):
            if isinstance(frame, AlbumFrame):
                return frame.path
        if self._level_stack.root is RootKind.CALENDAR:
            library = getattr(self, "_library", None)
            root = getattr(library, "root", None)
            return Path(root) if root is not None else None
        return None

    @property
    def _group_view(self) -> GroupRef | None:
        """**栈顶那一帧是不是分组**。

        搜索结果平铺、不压分组（ADR 0041 决策 2a），所以分组只可能压在相册 / 日历格上 ——
        看栈顶就够，不必从底往上扫。
        """
        top = self._level_stack.top
        return top.group if isinstance(top, GroupFrame) else None

    @property
    def _drill_photos(self) -> tuple[PhotoRef, ...] | None:
        """日历那一格的**照片**；不在日历格上时是 ``None``。

        **空元组与 ``None`` 是两件事，别合成一处**：空元组是「点开的那一格一张照片也没有」，``None``
        是「根本不在日历那一格里」。合成一处，「在那一格里但空的」会变成「不在那一格」，于是
        :meth:`MainWindow._reload_level` 里那句「条件非空就清掉日历格」反而**不**清它，留着只会让人
        以为还在那一格（那一格展开的内容是空的）。回归测试盯着这一条。"""
        frame = self._calendar_frame
        return frame.photos if frame is not None else None

    @property
    def _calendar_frame(self) -> CalendarFrame | None:
        """栈上最近那个日历格（没有就是 ``None``）。"""
        for frame in reversed(self._level_stack.frames):
            if isinstance(frame, CalendarFrame):
                return frame
        return None

    @property
    def _drill_label(self) -> str:
        """那一格的名字（「3 天前」那种）。"""
        frame = self._calendar_frame
        return frame.label if frame is not None else ""

    @property
    def _from_calendar(self) -> bool:
        """现在这一层是不是从日历点进来的。

        **判据是「栈上有没有日历格」**，而不是原来的独立标志位 —— 那个标志位要在六个地方手动
        跟着置位与清零（4 处），而漏一处就是「从日历点开、退回去之后还以为是」。
        """
        return self._calendar_frame is not None

    @property
    def _search_frame(self) -> SearchFrame | None:
        """栈上最近那个搜索结果帧。**搜索结果的判据只有一个地方** —— 让每个调用点自己同时看
        ``_conditions`` 与 ``_search_scope``，正是这类 bug 的来源。"""
        for frame in reversed(self._level_stack.frames):
            if isinstance(frame, SearchFrame):
                return frame
        return None

    @property
    def _is_in_search_frame(self) -> bool:
        """条件正**接管**这一层（整库检索），而不是在收窄它。

        **问的是「栈顶是不是搜索帧」**：栈模型下搜索结果就是一层，而筛选不入栈（ADR 0041 决策 2）
        —— 所以「有条件」不再足以推出「在检索」，那两件事第一次分得开。
        """
        return isinstance(self._level_stack.top, SearchFrame)

    @property
    def _photo_frame(self) -> PhotoFrame | None:
        """栈顶那一帧是不是大图页（正看着某一张）。"""
        top = self._level_stack.top
        return top if isinstance(top, PhotoFrame) else None

    # ---- 写：全部汇到这一处 ---------------------------------------------------

    def _set_root(self, root: RootKind) -> None:
        """换栈底 —— **顺带清空上面的一切**（换浏览方式就是「清栈换根」，ADR 0041 决策 3）。

        落在各自底部、不带任何残留，是用户明确要的：切走再切回来不回到刚才那个位置。
        """
        self._level_stack = self._level_stack.with_root(root)

    # ---- 画哪一页：栈的函数，不是各条路径各自挑的 --------------------------------

    @property
    def _page_role(self) -> PageRole:
        """这条栈此刻该画成哪一页（:func:`page_role_of` 的界面侧入口）。

        「我在大图页 / 在网格页 / 在日历页」以及各处的动作可用性都问它 —— 一次算出来，
        不必每一处各自看一遍控件。
        """
        library = getattr(self, "_library", None)
        return page_role_of(self._level_stack, has_library=library is not None)

    def _sync_page_to_stack(self) -> None:
        """把当前页摆成这条栈所表示的那一页 —— **整个窗口只在这里给内容页换页**。

        页面是栈的函数（:func:`page_role_of`）：各条路径只改栈。回归测试盯着「除这里之外
        没有第二处换页」，于是「页面与栈对不上」这类 bug 没有生长的入口。**不自动挂在四个
        写入口上**：``_enter_calendar_drill`` 要求「先把这一格的几张摆进模型、再换页」，
        换早了那一刻算出来的可见行对不上，所以由每条路径在**该换的那一拍**显式叫一声。"""
        stack_widget = getattr(self, "_stack", None)
        if stack_widget is None:
            return  # 栈可以在没有界面的宿主上单独驱动（见 tests），那种宿主没有页面可摆
        stack_widget.setCurrentWidget(self._page_for_role(self._page_role))

    def _page_for_role(self, role: PageRole) -> object:
        """那一页对应哪一个控件。"""
        return {
            PageRole.WELCOME: self._welcome,
            PageRole.GRID: self._grid_page,
            PageRole.CALENDAR: self._calendar_page,
            PageRole.DETAIL: self._detail_page,
        }[role]

    def _drop_search_frames(self) -> None:
        """弹掉栈上**所有**搜索结果帧（进相册时用 —— 进相册就是退出整库检索，ADR 0041 决策 3）。

        **弹到栈顶不是搜索帧为止**，而不是只弹一层：搜索帧之上可能还压着分组或照片，而那一摞都
        属于「搜索」这一次浏览；只弹一层的话它们会留在栈上，Esc 弹掉的是它们而搜索帧还在。
        """
        self._level_stack = self._level_stack.pop_until(
            lambda stack: not isinstance(stack.top, SearchFrame)
        )

    def _drop_calendar_cell_frame(self) -> None:
        """**摘掉栈上的日历格**，而且只摘它一个 —— 它上面压着的那些（搜索帧）一个都不动。

        摘的时候栈是 ``[CalendarFrame, SearchFrame]``，日历格**不在栈顶**：先
        :meth:`SearchFlowMixin._sync_search_frame` 压上搜索帧，再由
        :meth:`MainWindow._reload_level` 摘掉它。**别用 ``_pop_to(lambda: ...)``**（那会从栈顶
        连续弹、连带弹掉刚压上的搜索帧且不再压回来），**也别写成「栈顶是日历格才摘」**。"""
        frames = self._level_stack.frames
        index = next(
            (i for i, frame in enumerate(frames) if isinstance(frame, CalendarFrame)),
            None,
        )
        if index is not None:
            self._level_stack = self._level_stack.without_frame(index)

    def _push_album_chain(self, target: Path) -> None:
        """压出从「现在这一层」到 ``target`` 的**整条相册链**（一层一层一帧）。

        从库根直接点进 ``旅行/2024`` 要压两帧，Esc 一次退到「旅行」、两次退到库根；压一帧不行，
        那样「旅行/2024」与「旅行/2025」在栈上长得一样。**先把原先那一摞弹掉**（换相册等于换了一条
        路）：判据是「栈顶是不是搜索帧」而**不是**「栈顶是不是相册帧」，否则相册上压着分组或照片时
        一帧都不弹。"""
        library = getattr(self, "_library", None)
        root = getattr(library, "root", None)
        if root is None:
            self._push_frame(AlbumFrame(target))
            return
        # 弹到「栈顶是搜索帧」为止 —— 整库检索帧是唯一该活下来的那一摞之上的东西。
        # 日历格不在这里特意留住：``_reload_level`` 每一次都调 ``_drop_calendar_cell_frame``。
        self._level_stack = self._level_stack.pop_until(
            lambda stack: isinstance(stack.top, SearchFrame)
        )
        # 从库根到目标的那几层，一层一帧压上去。
        try:
            relative = target.relative_to(root)
        except ValueError:  # 不在库里 —— 调用点已经查过，这里只是不再崩一次
            self._push_frame(AlbumFrame(target))
            return
        current = Path(root)
        for part in relative.parts:
            current = current / part
            self._push_frame(AlbumFrame(current))

    def _push_frame(
        self, frame: object, narrowing: tuple[object, ...] = (), *, replace: bool = False
    ) -> None:
        """压一帧（``replace=True`` 是换栈顶，见 :meth:`LevelStack.replace_top`）。

        **所有「进入某处」都走这里** —— 原来那些「顺便把分组清掉」的手工同步全部消失，因为
        「回到某一层」在栈上就是压一个相册帧。
        """
        push = self._level_stack.replace_top if replace else self._level_stack.push
        self._level_stack = push(frame, narrowing)  # type: ignore[arg-type]

    def _pop_frame(self) -> LevelStack | None:
        """弹掉栈顶一帧。**只有根帧时什么也不做**，返回弹之前那一份（给调用点看）。

        返回旧栈是为了「弹完之后要拿被弹掉的那一帧做点什么」—— 访达退上一层要把刚离开的那一级
        留在选中状态，而那正需要知道弹掉的是什么。
        """
        if not self._level_stack.can_pop():
            return None
        before = self._level_stack
        self._level_stack = self._level_stack.pop()
        return before

    def _pop_to(self, predicate: object) -> None:
        """连续弹到 ``predicate`` 为真（给「退到相册视图的根」这类动作）。"""
        self._level_stack = self._level_stack.pop_until(predicate)  # type: ignore[arg-type]


__all__ = ["LevelStackMixin"]
