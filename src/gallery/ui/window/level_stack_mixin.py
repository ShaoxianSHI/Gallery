"""让那条栈成为「我在哪」的**唯一来源** —— 原来的六个字段退成从栈算出来的属性。

**这一份是「第二遍」（ADR 0041 实施步骤 2）**。第一步只加了数据结构、行为没变；这一步让那六个
字段彻底不再被赋值，于是「它们之间有没人声明的互斥关系」这件事**从类型上消失**。

做法是**读的地方一个都不改**：那六个名字从「实例字段」变成 ``property``，所有 ``self._group_view
is not None`` 之类照旧工作，而赋值的地方逐个改成压栈 / 弹栈 / 换根。这样 ``grep`` 到的行数
一样多，而**写的地方从 30 处降到 2 处**（``_push_frame`` / ``_pop_frame``）。

两个容易搞错、于是写成独立方法的判据：

- **``_group_view`` 是「最近一个分组帧」，不是「栈顶那一帧」**。分组可以压在相册上、也可以压在
  搜索结果上（搜索结果里同样会生成分组卡片）。把它写成 ``栈顶是分组`` 就会在「搜索结果→分组」
  之后又在相册视图里误判成没进组 —— 那正是原 bug 换个形状再犯。
- **``_current_dir`` 是「相册链上最近的那一帧」**，相册链与日历链互斥（ADR 0041 决策 1）：
  在日历上它是根（而不是 ``None``）—— 因为日历视图的「当前位置」本来就是日历页，没有子目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from gallery.core.library.photos import GroupRef, PhotoRef
from gallery.ui.window.level_stack import (
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

        **空元组与 ``None`` 是两件事，别合成一处**：空元组是「点开的那一格一张照片也没有」
        （它被删了，或者本来就是空格子），而 ``None`` 是「根本不在日历那一格里」。合成一处，
        「在那一格里但空的」会变成「不在那一格」，于是 :meth:`MainWindow._reload_level` 里那句
        「条件非空就清掉日历格」反而**不**清它 —— 而那一格摊开的内容是空的，留着只会让人以为
        还在那一格。守护测试盯着这一条。
        """
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

        **页面不是各条路径各自决定的一件事，而是栈的函数**（:func:`page_role_of`）：各条路径
        只改栈，该画哪一页由这一句算出来。守护测试盯着「除这里之外没有第二处换页」，于是
        「页面与栈对不上」这一类 bug 没有生长的入口。

        **不挂在四个写入口上自动跑**：``_enter_calendar_drill`` 要求「先把这一格的几张摆进
        模型、再换页」（换早了那一刻算出来的可见行对不上），所以由每条路径在**该换的那一拍**
        显式叫一声。
        """
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

        **弹到栈顶不是搜索帧为止**，而不是只弹一层：搜索帧之上可能还压着分组或照片
        （用户在搜索结果里进了分组再点面包屑上的某个相册），而那一摞都属于「搜索」这一次
        浏览。只弹一层的话它们会留在栈上，Esc 弹掉的是它们而搜索帧还在 ——
        「按 Esc 什么都没发生」。
        """
        self._level_stack = self._level_stack.pop_until(
            lambda stack: not isinstance(stack.top, SearchFrame)
        )

    def _drop_calendar_cell_frame(self) -> None:
        """**摘掉栈上的日历格**，而且只摘它一个 —— 它上面压着的那些（搜索帧）一个都不动。

        日历格与搜索帧的**先后**由两次不同的调用决定：先
        :meth:`SearchFlowMixin._sync_search_frame` 压上搜索帧，再由
        :meth:`MainWindow._reload_level` 摘掉日历格。所以摘的时候栈是
        ``[CalendarFrame, SearchFrame]``，而日历格**不在栈顶**。

        **别用 ``_pop_to(lambda: self._calendar_frame is None)``** —— 那是「从栈顶连续弹」，
        在这个栈上会把刚压上去的搜索帧一并弹掉，而它不会被压回来，留下一个矛盾状态：条件还在、
        作用域还是 library，可栈上没有搜索帧。下一次换浏览方式时 ``set_browse_mode`` 因此抓不到帧、
        清不掉条件。

        **也别写成「栈顶是日历格才摘」**：那样一句在真实时序里永远不成立（栈顶是搜索帧），
        日历格摘不掉，于是撤光条件之后按 Esc 会退到「他这一趟没去过的那一格」。
        """
        frames = self._level_stack.frames
        index = next(
            (i for i, frame in enumerate(frames) if isinstance(frame, CalendarFrame)),
            None,
        )
        if index is not None:
            self._level_stack = self._level_stack.without_frame(index)

    def _push_album_chain(self, target: Path) -> None:
        """压出从「现在这一层」到 ``target`` 的**整条相册链**（一层一层一帧）。

        从库根直接点进 ``旅行/2024`` 要压两帧（旅行、2024），Esc 一次退到「旅行」、两次退到库根。
        压一帧不行：那样「旅行/2024」与「旅行/2025」在栈上长得一样，退一次就掉回库根。

        **先把原先那一摞弹掉**（换相册等于换了一条路）。判据是「栈顶是不是搜索帧」而**不是
        「栈顶是不是相册帧」**：相册上面压着分组或照片帧，只看栈顶会一帧都不弹。
        """
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
