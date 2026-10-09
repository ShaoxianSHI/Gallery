"""导航：这一层是哪一层、退回去时到哪儿、该选中哪一格（ADR 0026 / 0041）。

它把「位置」这件事收在一处：进入相册 / 退回上一层 / 撤一条条件 / 重新列出当前层，以及
重列之后**该选中哪一格**（往上一层退要停在刚离开的相册或组，换层则谁也不选）。判据靠的是
**层标识**（``navigation.level_key``）而不是行号 —— 两种情形在屏幕上长得一样。
"""

from __future__ import annotations

from pathlib import Path

from gallery.core import paths
from gallery.core.library.config import BROWSE_MODE_ALBUMS
from gallery.ui.navigation import (
    ENTRY_KIND_PATH,
    EntryKey,
    LevelKey,
    breadcrumb_parts,
    entry_key,
    level_key,
    path_key,
    row_of_entry_key,
)
from gallery.ui.window.jobs import LEVEL
from gallery.ui.window.level_stack import (
    AlbumFrame,
    CalendarFrame,
    GroupFrame,
    PhotoFrame,
    RootKind,
    SearchFrame,
)


class NavigationFlowMixin:
    """进入 / 退出层级，以及到时该停在哪一格。"""

    def enter_folder(self, directory: Path, *, focus: EntryKey | None = None) -> None:
        """进入一个相册（子文件夹）。库以外的路径一律拒绝。

        :param focus: 到了这一层该选中哪一格；``Esc`` 退上去时传的是刚离开的那个相册。
        **进目录就是退出整库检索**，但**筛选不在此列**（ADR 0040 决策 1）：条件跟着这一帧
        走、弹回来时自动恢复。**存进去的是解析过的路径**：不解析就对不上「是不是在当前
        这一层」。"""
        library = self._library
        if library is None:
            return
        target = Path(directory)
        if not paths.is_within(library.root, target):
            return
        resolved = target.resolve()
        # 进相册 = 退出整库检索（ADR 0041 决策 3）。**必须先弹搜索帧再压相册链**：搜索帧压在
        # 相册链之上，而 ``_drop_search_frames`` 只从栈顶往上弹，反过来弹掉的是刚压上去的那一层。
        leaving_search = self._search_frame is not None
        if leaving_search:
            self._leave_search_frame(self._search_frame, reload=False)
            self._drop_search_frames()
        self._push_album_chain(resolved)
        # **只有整库检索要清条件**（理由见上面那段）：筛选要跟着走进这个相册。
        self._reload_level(focus=focus)

    def go_up(self) -> None:
        """**弹掉栈顶一帧** —— 只管位置，与条件无关（ADR 0041 决策 1）。

        「能退什么」不看一串手写的 ``if``，而是**看弹掉的那一帧是什么**，四种情况在
        :meth:`_leave_frame` 里分派：加一种新层级只需在那一处加一支。退上去时**把刚离开的那一级留在
        选中状态**（访达里退上一层就是这样）；只此一处例外 —— 再点进去是另一次浏览，那一层不留上一次
        的选中（见 :meth:`_arm_focus`）。"""
        if self._library is None:
            return
        popped = self._pop_frame()
        if popped is None:
            return  # 只有根帧：退不动就安静地不动
        self._leave_frame(popped.top)

    def _leave_frame(self, frame: object) -> None:
        """**刚弹掉的是哪一帧，就按那一帧做收尾**（重新列出这一层、必要时回日历 / 选中原卡片）。

        **这是 ``go_up`` 唯一需要「认得帧」的地方**：日历格那一帧要回日历页、分组那一帧
        要把刚离开的卡片重新选中、相册与搜索帧只是重列那一层。**``go_up`` 是「先弹再收尾」**，
        所以这一支**不能**走 ``enter_folder``（那会再压一帧，栈越退越深）。
        """
        if isinstance(frame, AlbumFrame):
            # 弹掉相册帧之后**底下那一帧**就是要去的地方（可能是根帧 = 库根）。
            target = self._level_stack.top
            focus = path_key(frame.path)
            if isinstance(target, GroupFrame):
                self._reload_level(focus=entry_key(target.group))
                return
            if isinstance(target, CalendarFrame):
                self._leave_calendar_cell()
                return
            self._reload_level(focus=focus)
            return
        if isinstance(frame, GroupFrame):
            self._reload_level(focus=entry_key(frame.group))
            return
        if isinstance(frame, CalendarFrame):
            self._leave_calendar_cell()
            return
        if isinstance(frame, PhotoFrame):
            # **离开大图页就把横幅收掉**（那句话说的是刚才那一张照片）：在这里收，因为走到
            # ``leave_detail_page`` 时照片帧已经弹掉，按栈判「刚才在不在大图页」永远为假。
            self._clear_banner()
            # **大图页的「上一级」就是这里** —— 退到哪一层看照片帧底下压着什么。
            top = self._level_stack.top
            if isinstance(top, CalendarFrame) and not top.expanded:
                # 只有一张的那一格：日历格与照片是**一起进出的一对**（见 ``expanded``），
                # 从没摊成网格，于是连它一起收掉，直接回日历那一页。
                self._pop_frame()
                self._back_to_calendar()
                return
            if isinstance(top, CalendarFrame):
                # 底下压着展开的网格：退回那一层（模型里还是这一格的几张）。
                self.leave_detail_page()
                return
            if top is None and self._level_stack.root is RootKind.CALENDAR:
                # 栈底就是日历、上面没有别的层：直接回日历那一页。
                self._back_to_calendar()
                return
            self.leave_detail_page()
            return
        if isinstance(frame, SearchFrame):
            # 条件是那一帧的内容，所以帧没了条件也没了 —— 底下那一层恢复成「没搜过」的样子。
            # **必须把刚弹掉的那一帧传进去**：弹完之后重查栈只会得到「压根没有搜索帧」。
            self._leave_search_frame(frame)
            return
        self._reload_level()

    def pop_last_condition(self) -> None:
        """撤掉最后一条筛选条件（``⇧Esc``，或菜单里的那一行）。

        **与 :meth:`go_up` 分开是用户选定的**：Esc 纯粹退位置，退不掉的时候什么也不做，也不
        顺手撤掉一条条件 —— 「按 Esc 退一层，结果条件少了一条」这件事没法从屏幕上分辨出
        「我退了」还是「我撤了一条」。
        """
        if not self._filters.pop_last():
            return
        self._sync_search_frame()  # 撤光了就不再是整库检索 → 弹掉搜索帧（ADR 0041 决策 3）
        self._apply_conditions()

    def _can_go_up(self) -> bool:
        """现在退不退得回去。**就是「栈深 > 1」**（ADR 0041 决策 1）。

        判据与 :meth:`go_up` 用**同一句话**（栈深），不各手写一遍那串 ``if`` ——「退错了一层」
        不是任何一句能自检的东西。
        """
        if self._library is None or self._grid.is_renaming():
            return False
        return self._level_stack.can_pop()

    def _on_background_activated(self) -> None:
        """双击了网格里没有格子的地方：退回上一层，与 ``Esc`` 是同一件事。

        这一层空着（空相册、空分组）时尤其需要它 —— 屏幕上根本没有可双击的东西。退不动时
        安静地什么都不做：到了头本来就是一个合法状态。
        """
        if self._grid.is_renaming():
            # 改到一半：这一下先归输入框。既不把人带走，也不打扰他。
            return
        self._cancel_condition_edit()  # 改到一半：这一下算放弃（ADR 0040 决策 12）
        if self._can_go_up():
            self.go_up()

    def _on_breadcrumb_requested(self, directory: object) -> None:
        if isinstance(directory, Path):
            self.enter_folder(directory)

    def _reload_level(self, *, focus: EntryKey | None = None) -> None:
        """重新列出当前层（切模式、进入或返回相册时都走这里）。

        :param focus: 到了这一层要选中哪一格；**只有往上一层退才会给**，其余由
            :meth:`_arm_focus` 自己判断。
        """
        self._cancel_tier(LEVEL)
        self._arm_focus(focus)
        # 换代：让已经在路上的旧结果作废。
        self._generation += 1
        self._grid.cancel_rename()
        self._facet_values = {}
        self._model.clear()
        # 网格先清干净：当前项留着的话，⌘R / ⌘⌫ 的可用性判据里它仍然算数（菜单亮着，对象却不存在）。
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = []  # 换层要重扫，先暂存，别先摆一屏旧顺序
        self._level_notice = False
        self._progress_notice = False
        # 换层只把横幅收掉，**不打断**全库解析：它是整个库的事，与当前在哪一层无关。
        self._place_notice_text = None
        self._scan_issue = False
        self._focus_state.clear_current()
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        # 整库检索不是日历这一层的一部分：条件撤光之后 Esc 不该退到日历的那一格。
        self._drop_calendar_cell_frame()
        # **摆哪一页由栈算出来**（`page_role_of`）：这一处只把栈改干净，页面是它的函数。
        if self._on_calendar_page():
            # 日历自己那份缩略图窗口要等换页之后才算得对（它读此刻是哪一页），所以整条尾巴归它。
            self._show_calendar()
        else:
            self._sync_ui()
        self._start_scan()

    # ---- 到了这一层该选中哪一格 ----------------------------------------------
    # **往上一层退**时仍停在刚离开的那个相册 / 那个组；**还是这一层重排一遍**时跟着停着的那一格走；
    # **换了层**时谁也不选 —— 留着上一次的选择，用户看到的是上一趟的高亮。三者都靠**标识**而不是
    # 行号，比一比**层标识**就知道属于哪一种。

    def _arm_focus(self, focus: EntryKey | None = None) -> None:
        """记下到了新的一层该选中哪一格。

        :param focus: 显式指定（往上一层退时给的那个相册 / 那个组）；不给就由下面的规则判断。
        判断靠比层标识（``navigation.level_key``）：``enter_*`` 先改状态再重载，所以问
        「现在在哪一层」得到的是**新的**那一层，正好拿来和轴上记着的那份比。"""
        self._focus_state.arm(self._current_level_key(), self._shown_entry_key(), focus)

    def _current_level_key(self) -> LevelKey:
        """当前这一层的标识（见 ``navigation.level_key``）。"""
        group = None if self._group_view is None else (self._group_view.facet, self._group_view.key)
        return level_key(
            self._browse_mode,
            self._current_dir,
            group=group,
            conditions=self._filters.identities(),
            scope=self._search_scope,
        )

    def _shown_entry_key(self) -> EntryKey | None:
        """网格上此刻停着的那一格是谁；不是同一层（或者压根没有）就是 ``None``。"""
        if not self._focus_state.is_same_level(self._current_level_key()):
            return None
        entry = self._model.entry_at(self._grid.current_row())
        return None if entry is None else entry_key(entry)

    def _restore_focus(self) -> None:
        """这一层排好之后，把该选中的一格选中。

        **每一层只摆一次**：摆完之后就是用户自己的选择了，后台再重排（来了新照片、换排序）
        不该把它拽回去 —— 那种重排由 ``_reorder_level`` 的锚点负责。找不到就什么都不选。
        """
        key = self._focus_state.take_focus()
        if key is None:
            return
        row = self._row_of_focus(key)
        if row >= 0:
            self._grid.select_row(row)  # 一并滚过去：这一层可能长到一屏之外

    def _row_of_focus(self, key: EntryKey) -> int:
        """该选中的那一格现在在第几行（``-1`` = 这一层里已经没有了）。

        照片与相册走模型现成的路径索引；组卡片没有索引，扫一遍这一层 —— 两条路不能合成一条，
        因为这一层里的照片可能有几万张。
        """
        if key[0] == ENTRY_KIND_PATH:
            return self._model.row_of_path(Path(key[1]))
        return row_of_entry_key(self._model.entries(), key)

    def _update_breadcrumb(self) -> None:
        """面包屑那一行写什么（**顺带把日历工具条上那几枚胶囊也摆好**）。

        **两种作用域写的是两件不同的事**（ADR 0040 决策 4）：整库检索时只报**库名**（范围真的变成
        整库了，留着「旅行 ▸ 2024」读起来像「只在这个文件夹里搜到 4 张」，而那 4 张大半在别处，
        ADR 0026 决策 1b）；筛选时报**完整路径 + 那一组**，因为范围没变。
        """
        library = self._library
        # 平铺模式下平时不显示面包屑，但**分组里与有条件时一律显示**（ADR 0026 决策 9）。
        in_albums = self._browse_mode == BROWSE_MODE_ALBUMS
        conditions = self._filters.chip_texts()
        editing = self._editing_condition if conditions else None
        # guard: allow-long-comment（日历那几枚要摆在每一个早退之前，摆在末尾会漏掉「撤光条件」）
        # **日历工具条那几枚先摆，且在每一个早退之前**（ADR 0040 决策 9）。摆在末尾就漏了
        # 「撤光条件」那一步：早退在 ``conditions`` 为空时直接返回，那一枚撤掉的胶囊就一直挂着，
        # 要切一次视图再切回来才走。
        self._sync_calendar_conditions(conditions, editing)
        if self._drill_photos is not None:
            # 日历点出来的那一格不是文件夹，面包屑没有一段能指；标题里已经写着它在哪一段日子。
            self._hide_breadcrumb()
            return
        if library is None or (not in_albums and self._group_view is None and not conditions):
            self._hide_breadcrumb()
            return
        if self._is_library_search():
            # **检索时只报库名：既不报当前文件夹，也不报当前那一组**（检索横跨整个库，ADR 0026
            # 决策 1）。范围要收窄加一条「#相册 …」；``_group_view`` 与 ``_current_dir`` 都留着。
            parts = [(library.display_name, library.root)]
            group = None
        else:
            parts = (
                breadcrumb_parts(library.root, self._current_dir or library.root)
                if in_albums
                else [(library.display_name, library.root)]
            )
            # 筛选时这一组**说的是真的**：它就是这一层的范围。
            group = self._group_view_label()
        self._breadcrumb.set_path(
            parts,
            group=group,
            conditions=conditions,
            result_count=self._search_total if conditions else None,
            editing=editing,
            scope=self._search_scope,
        )
        self._breadcrumb.show()

    def _hide_breadcrumb(self) -> None:
        """藏起来，**并且清空内容**。

        ``hide()`` 只让控件不画，上一轮摆的那些还留在布局里（``set_path`` 开头就是 ``_clear()``）；
        「不摆」与「摆成空的」不是同一件事。
        """
        self._breadcrumb.clear_content()
        self._breadcrumb.hide()


__all__ = ["NavigationFlowMixin"]
