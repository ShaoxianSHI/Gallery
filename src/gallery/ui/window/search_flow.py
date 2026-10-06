"""检索（ADR 0026）：把一串「``#字段 值``」变成一层照片，外加背后那个索引。

没有索引就什么都搜不到，所以打开库时顺手把整库写进索引；这一遍与地点解析**串行**（两边各自
要遍历整棵子树，同时跑等于把磁盘读两遍）。

**检索结果接着走与扫描完全相同的那段收尾**（决策 7：排序、缩略图、焦点恢复、空层提示、监听
增量），另写一条平行路径才是 bug 的来源；条件是**临时的**（决策 9），换库就清掉。
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from gallery.core.library.config import SEARCH_SCOPE_LEVEL, SEARCH_SCOPES
from gallery.core.library.facade import Library
from gallery.core.library.photos import FolderRef, GridEntry, PhotoRef
from gallery.core.library.scanner import ScanReport
from gallery.core.library.search import (
    RESULT_LIMIT,
    ParsedInput,
    SearchCondition,
    albums_kept,
    make_condition,
    parse_input,
    unusable_reason,
)
from gallery.ui.tasks import TaskContext
from gallery.ui.window.library_flow import SCAN_BATCH_SIZE
from gallery.ui.window.notice import NoticeSlot

logger = logging.getLogger(__name__)


def _entry_survives(entry: GridEntry, matching: set[Path], kept_albums: frozenset[Path]) -> bool:
    """这一格在筛选后留不留。

    **照片按路径、卡片按「它自己那张卡留没留」** —— 卡片判据由 :func:`search.albums_kept` 在
    core 那一侧算完（要遍历整份命中集合），所以这里只查表，不再各算一遍前缀。
    """
    if isinstance(entry, FolderRef):
        return entry.path in kept_albums
    if isinstance(entry, PhotoRef):
        return entry.path in matching
    return True  # 分组卡片由 _reorder_level 重算，不归这里管


class SearchFlowMixin:
    """检索框那一套条件，以及「把整库写进索引」那一遍后台维护。

    **条件有两种作用域**（ADR 0040 决策 1）：整库检索的结果**接管**当前层，而筛选只**收窄**
    当前层、视图本身不动。整个窗口里凡是「条件非空就意味着什么」的地方一律问
    :meth:`_is_library_search` —— 七个判据各自问一次 ``self._conditions`` 是这类 bug 的来源，
    而症状（筛选时日历被换成网格页、进相册时条件被清掉）看起来都像「另一个功能坏了」。
    """

    # ---- 作用域（ADR 0040）----------------------------------------------------

    def _is_library_search(self) -> bool:
        """条件正**接管**这一层（整库检索），而不是在收窄它。

        **全窗口唯一的那句判据**：``bool(self._conditions)`` 只说明「有条件」，不说明条件管到
        哪儿 —— 两者在筛选下必须分开，而分开的地方多了就一定会漏一处。
        """
        return bool(self._conditions) and self._search_scope != SEARCH_SCOPE_LEVEL

    def _is_filtering(self) -> bool:
        """条件正**收窄**这一层，且这一层仍然是它自己（月视图仍然是月视图）。"""
        return bool(self._conditions) and self._search_scope == SEARCH_SCOPE_LEVEL

    def set_search_scope(self, scope: str) -> None:
        """切到哪一种作用域（切换按钮与菜单项都走这里）。

        **条件不清空**：这是同一个胶囊里的同一批条件，用户改的只是「它们管到哪儿」。清掉等于
        逼他重新敲一遍，而那串输入他刚刚才确认过。
        """
        if scope not in SEARCH_SCOPES or scope == self._search_scope:
            self._sync_search_scope()
            return
        self._search_scope = scope
        self._remember_ordering()  # 作用域是库级偏好（ADR 0040 决策 6）
        self._sync_search_scope()
        # **有条件才重列这一层**：没有条件时两种作用域的显示完全一样（都是这一层的全部内容），
        # 重列只是让网格无意义地闪一下。
        if self._conditions:
            self._reload_level()
        else:
            self._update_action_states()

    def _sync_search_scope(self) -> None:
        """胶囊上的那句话、切换按钮与菜单项的勾，三处一起跟着作用域走。"""
        filtering = self._search_scope == SEARCH_SCOPE_LEVEL
        self._search_field.set_scope(filtering)
        self._scope_toggle.set_filtering(filtering)
        self._scope_action.setChecked(filtering)
        self._update_action_states()

    # ---- 筛选（ADR 0040 决策 3）------------------------------------------------

    def _start_level_filter(self) -> None:
        """把这一层按条件收窄（**不在筛选作用域时直接往下走**，见 :meth:`_order_now`）。

        收窄是**一次相交**而不是另写一条列举链：这一层的照片照常枚举（相册 / 平铺 / 分组快照各
        走各的现成路径），然后拿一份「命中路径集合」去求交。于是分桶、排序、缩略图预取、
        焦点恢复、空层提示、监听增量全都原样复用 —— 与整库检索共用同一条收尾路径
        （ADR 0026 决策 7 的同一条原则，另写一条平行路径才是 bug 的来源）。

        **索引没建全时先把它建起来**（与 :meth:`_start_search` 同一道闸，理由也一样）：文件表
        里没有行时，一条都命中不了，用户看到的是「这个视图里没有符合这些条件的照片」，而那
        不是答案，是索引还没建。那一遍跑完会自己重跑这一遍（``_on_photo_index_done``）。
        """
        library = self._library
        if not self._is_filtering() or library is None:
            self._apply_ordering()
            return
        if library.indexed_photos() == 0:
            self._maybe_start_photo_index()
            return
        generation = self._generation
        conditions = tuple(self._conditions)

        def work(context: TaskContext) -> object:
            matching = library.matching_paths(conditions)
            if context.is_cancelled():
                return None
            # 相册卡片不是照片，但一张照片都不在它子树里的卡片该收起来 —— 否则用户点进去
            # 看到的是个空层。判据是「子树里有没有命中」，由 core 一次算完（ADR 0040 决策 3）。
            staged = self._staged if self._staged is not None else self._model.entries()
            albums = [entry for entry in staged if isinstance(entry, FolderRef)]
            kept_albums = {album.path for album in albums_kept(albums, matching, root=library.root)}
            return (matching, frozenset(kept_albums))

        self._filter_job = self._runner.submit(
            work,
            on_result=partial(self._on_level_filtered, generation),
            on_error=partial(self._on_filter_failed, generation),
        )

    def _on_level_filtered(self, generation: int, payload: object) -> None:
        """命中集合回来了：把这一层收窄，然后照常往下走（排序、分桶、缩略图）。

        **收窄落在 ``_staged`` 上而不是模型上**：这一层还没上屏（换层时暂存区优先），在那里收
        窄才不会出现「先摆一屏未筛选的、再换成筛过的」那种闪。
        """
        if generation != self._generation:
            return
        self._filter_job = None
        if not isinstance(payload, tuple) or len(payload) != 2:
            self._apply_ordering()
            return
        matching, kept_albums = payload
        if not isinstance(matching, set) or not isinstance(kept_albums, frozenset):
            self._apply_ordering()
            return
        staged = self._staged
        if staged is not None:
            self._staged = [
                entry for entry in staged if _entry_survives(entry, matching, kept_albums)
            ]
        else:
            # 已经上屏（监听器增量那一路）：就地换掉模型内容。
            entries = self._model.entries()
            kept = [entry for entry in entries if _entry_survives(entry, matching, kept_albums)]
            if kept != list(entries):
                self._model.clear()
                self._model.append(kept)
        self._search_total = (
            len(self._staged) if self._staged is not None else self._model.rowCount()
        )
        self._update_breadcrumb()
        # **直接排**：筛选已经做完，再绕回 ``_order_now`` 会再筛一次（同一个漏斗递归到自己）。
        self._apply_ordering()

    def _on_filter_failed(self, generation: int, message: str) -> None:
        """筛选没能完成。**说清是哪一件**，别让用户对着一个空层猜。"""
        if generation != self._generation:
            return
        self._filter_job = None
        self._show_banner(f"筛选没能完成：{message}")
        # 照常把这一层排出来：一片空白比未筛选的内容更没用，而横幅已经说清发生了什么。
        self._apply_ordering()

    def _cancel_level_filter(self) -> None:
        """作废在途的筛选（换层、换库、关窗时）。"""
        if self._filter_job is not None:
            self._runner.cancel(self._filter_job)
            self._filter_job = None

    # ---- 检索（ADR 0026）------------------------------------------------------
    def _start_search(self, library: Library) -> None:
        """按已生效的条件检索整个库，把命中的照片攒进这一层。

        **索引没建全时先把它建起来**（ADR 0026 决策 5）：否则用户看到的是「一条都没有」，
        而那不是答案，是索引还没建。那一遍跑完会自己重跑这次检索。
        """
        if library.indexed_photos() == 0:
            self._maybe_start_photo_index()
            return
        generation = self._generation
        conditions = tuple(self._conditions)

        def work(context: TaskContext) -> object:
            outcome = library.search_photos(conditions, limit=RESULT_LIMIT)
            refs = outcome.refs
            # 与扫描一样**分批送回**（``_on_scan_batch`` 直接吃这个形状）：结果可能有几千条。
            for start in range(0, len(refs), SCAN_BATCH_SIZE):
                context.progress(tuple(refs[start : start + SCAN_BATCH_SIZE]))
            return outcome.total

        self._search_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_search_done, generation),
            on_error=partial(self._on_search_failed, generation),
        )

    def _on_search_done(self, generation: int, payload: object) -> None:
        """检索完了，**接着走与扫描完全相同的那段收尾**（ADR 0026 决策 7）。

        facet 补齐、分组排序、缩略图预取、焦点恢复、空层提示、监听增量，一样都不少 ——
        另写一条平行路径才是 bug 的来源。
        """
        if generation != self._generation:
            return
        self._search_job = None
        total = payload if isinstance(payload, int) else 0
        # 命中数变了要刷新面包屑：它就写在那枚「搜索结果（128）」里。
        if total != self._search_total or self._search_total is None:
            self._search_total = total
            self._update_breadcrumb()
        self._on_scan_done(generation, ScanReport())

    def _on_search_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._search_job = None
        self._search_total = 0
        self._update_breadcrumb()
        self._show_banner(f"检索没能完成：{message}")
        self._apply_ordering()  # 把这一层按「没有结果」收尾，界面不至于卡在空白上

    def _cancel_search(self) -> None:
        """作废在途的检索（换层、换库、关窗时）。"""
        if self._search_job is not None:
            self._runner.cancel(self._search_job)
            self._search_job = None

    def _on_search_submitted(self, raw: str) -> None:
        """搜索框里回车了：把这一串变成一条条件，然后重新列出这一层。

        三种「提交了但没东西可提交」都安静地退开：空串、只敲了字段名（``#相机``，那是补全不是
        条件）、值写坏了（``#ISO abc``）—— 最后一种要说一句话，因为用户以为条件生效了。
        """
        editing = self._editing_condition
        parsed = parse_input(raw)
        condition = None if parsed is None else make_condition(parsed)
        if condition is None:
            # **正在改的那一条被清空后回车 = 撤掉它**：这时输入框是空的，按回车没有别的意思，
            # 而「把这条删了」正是用户清空它想要的结果 —— 免得出现「点一下胶囊就没了」那种意外。
            if parsed is None and editing is not None:
                self._on_condition_removed(editing)
                return
            self._warn_unusable_input(parsed)
            return
        if self._library is None:
            # 没有库的时候收下这条条件是**悄悄的**：面包屑不显示它，换库时又被清掉 —— 不如说一句。
            self._show_banner(
                "先打开一个照片文件夹，再筛选。"
                if self._is_filtering()
                else "先打开一个照片文件夹，再搜索。"
            )
            return
        if editing is not None and 0 <= editing < len(self._conditions):
            # **就地替换**：改一条条件的结果仍是那一条、位置也不变 —— 否则面包屑上的顺序会跳。
            self._conditions[editing] = condition
        else:
            self._conditions.append(condition)
        self._editing_condition = None
        self._warn_metadata_without_exiftool(condition)
        self._apply_conditions()

    def _warn_unusable_input(self, parsed: object) -> None:
        """没提交成的一条输入，值不值得说一句。

        只说「值写坏了」的那一种（``#ISO abc``）：还在挑字段不是毛病，空串也不是 —— 每一句
        多余的提示都在教用户忽略横幅。**具体理由由 ``search.unusable_reason`` 说**：对
        ``2024～2023`` 与 ``2024-13`` 是两种要改的地方不同的毛病。
        """
        if not isinstance(parsed, ParsedInput) or parsed.selecting or parsed.field is None:
            return
        self._show_banner(
            f"「{parsed.field.label}」{unusable_reason(parsed.field, parsed.value)}"
            "不支持的说法不会变成一条筛选条件。"
        )

    def _warn_metadata_without_exiftool(self, condition: SearchCondition) -> None:
        """提交了一条要读 EXIF 才读得到的条件，而 exiftool 不在。

        与 :meth:`_unavailable_hint` 同一个取向：**降级可以，假装没降级不行** ——
        搜「相机」一条都没有，用户只会以为自己的照片没写过相机。
        """
        if not self._has_exiftool or not condition.field.needs_metadata:
            return
        self._show_banner(
            f"没装 exiftool，「{condition.field.label}」这个字段读不出来"
            "（文件名、相册、格式、大小不受影响）。安装后即可使用：brew install exiftool"
        )

    def _on_search_dismissed(self) -> None:
        """搜索框里按 Esc 且里面是空的。

        正在改某一条的时候，``Esc`` 是**放弃这一次改动**（那一条原样留着）—— 否则
        「改到一半想溜」与「撤掉一条」是同一下，用户没法中途反悔。

        **走的是 :meth:`pop_last_condition` 而不是 :meth:`go_up`**（ADR 0040 决策 11）：那一版转给
        ``go_up``，于是搜索框里那个 Esc 也变成了「退一层」—— 用户在子相册里点进搜索框按 Esc，
        位置没退、反而少了一条条件。搜索框这一处的语境是**条件**，所以它撤条件。
        """
        if self._editing_condition is not None:
            self._cancel_condition_edit()
            return
        self.pop_last_condition()

    def _on_search_active_changed(self, _active: bool) -> None:
        """正在输入时别的命令让开 —— 与两个改名输入框同一件事（键盘归输入框）。"""
        self._update_action_states()

    def _on_condition_edited(self, position: int) -> None:
        """点面包屑上第 ``position`` 枚胶囊：把它放回输入框接着改。

        **不是就地改**：胶囊上改不了字，所以把原文放回搜索框，由用户敲完回车（那时才就地
        替换那一条）。按钮上会标出「正在改这一条」。
        """
        if not 0 <= position < len(self._conditions):
            return
        self._editing_condition = position
        self._search_field.load(self._conditions[position].as_input)
        self._update_breadcrumb()

    def _cancel_condition_edit(self) -> None:
        """放弃这一次改动：胶囊原样留着，输入框清空。"""
        self._editing_condition = None
        self._search_field.stop_edit()
        self._update_breadcrumb()

    def _on_condition_removed(self, position: int) -> None:
        """撤掉第 ``position`` 枚条件胶囊（点它右端的 ✕，或改到一半清空后回车）。"""
        if not 0 <= position < len(self._conditions):
            return
        del self._conditions[position]
        # 序号会往前挪一位 —— 不跟着挪，正在改的那一条就变成了另一条。
        editing = self._editing_condition
        if editing == position:
            self._editing_condition = None
        elif editing is not None and editing > position:
            self._editing_condition = editing - 1
        self._apply_conditions()

    def clear_search_conditions(self) -> None:
        """「清除搜索条件」：一条不剩，回到普通的一层。"""
        if not self._conditions:
            return
        self._conditions.clear()
        self._editing_condition = None
        self._apply_conditions()

    def focus_search(self) -> None:
        """⌘F：把光标放进标题栏那个胶囊。"""
        if not self._owns_title_bar:
            return  # 标题栏不归这里管（见 theme.unify_title_bar），那一侧没有搜索框
        self._search_field.focus_search()

    def _apply_conditions(self) -> None:
        """条件变了（加一条、撤一条、清空）之后重新列出这一层。"""
        self._search_total = None
        self._reload_level()

    # ---- 建立检索索引（ADR 0026 决策 5、ADR 0040 决策 3）---------------------------

    def _maybe_start_photo_index(self) -> None:
        """把整个库的照片写进索引的文件表，**只有真的有活干才起**。

        与 :meth:`_maybe_start_place_pass` 同一个套路（ADR 0010）：打开库时顺手填好，之后每次
        检索就只是一次查表。第一遍跑完之前检索「什么都搜不到」，所以它自己会在结束时把在等的
        检索重跑一次。

        与地点那一遍**串行**（两边各自要遍历整棵子树），地点那遍先跑，它要起 exiftool 子进程。
        """
        library = self._library
        if (
            library is None
            or self._index_job is not None
            or library.root in self._index_roots
            or self._facet_job is not None
            or self._place_job is not None
            or self._scan_issue
        ):
            return
        self._index_roots.add(library.root)

        def work(context: TaskContext) -> object:
            return library.index_photos(
                should_cancel=context.is_cancelled,
                on_work=lambda count: context.progress(("work", count)),
                on_progress=lambda count: context.progress(count),
            )

        # 横幅**不在这儿亮**：已经建过索引的库这一遍是空跑，先亮等于在说一件没发生的事。
        self._index_job = self._runner.submit(
            work,
            on_progress=self._on_photo_index_progress,
            on_result=self._on_photo_index_done,
            on_error=self._on_photo_index_failed,
        )

    def _on_photo_index_progress(self, payload: object) -> None:
        if self._index_notice_text is None or self._scan_issue:
            return
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "work":
            self._show_index_notice(f"正在建立检索索引…{payload[1]} 张")
            return
        if isinstance(payload, int) and payload > 0:
            self._show_index_notice(f"正在建立检索索引…已处理 {payload} 张")

    def _on_photo_index_done(self, payload: object) -> None:
        self._index_job = None
        self._clear_index_notice()
        written = payload if isinstance(payload, int) else 0
        logger.info("检索索引：写入 %d 张", written)
        # **这里不按代次判断**：这一遍要回答的是「此刻有没有人在等检索」，条件列表才是那一刻
        # 的真相 —— 用户在索引维护**进行当中**提交条件时，那一下已经把代次 +1，按代次比会
        # 让这次检索永远不会被补跑。切库时那一遍会被取消、条件也一并清掉。
        if self._conditions:
            self._apply_conditions()

    def _on_photo_index_failed(self, message: str) -> None:
        self._index_job = None
        self._clear_index_notice()
        # 建不了索引不该让浏览不可用。**把库名从「起过」里拿掉**，下次换回这个库还能再试一次。
        self._forget_indexed_root()
        logger.info("建立检索索引失败：%s", message)
        if self._conditions:
            # **说清是哪一种条件**：两个功能坏在同一个原因上（那个卷不许写缓存），而用户看到的
            # 是他自己刚才做的那件事 —— 把话说成他正在用的那个功能，他才不会去别处找原因。
            self._show_banner(
                "这个库没法建立检索索引，筛选暂时用不了。"
                if self._is_filtering()
                else "这个库没法建立检索索引，搜索暂时用不了。"
            )

    def _cancel_photo_index(self) -> None:
        """作废在途的索引维护（切库、关窗时）。"""
        if self._index_job is not None:
            self._runner.cancel(self._index_job)
            self._index_job = None
            # 半途取消等于这一遍**没做完**：留在 ``_index_roots`` 里它就再也起不来了。
            self._forget_indexed_root()
        self._clear_index_notice()

    def _forget_indexed_root(self) -> None:
        """把这个库从「索引维护起过」那份名单里拿掉（取消或失败之后）。"""
        if self._library is not None:
            self._index_roots.discard(self._library.root)

    def _show_index_notice(self, text: str) -> None:
        """进度消息不许抢横幅 —— 理由与 :meth:`_show_place_notice` 一模一样。"""
        self._index_slot.show(text)

    def _clear_index_notice(self) -> None:
        self._index_slot.clear()

    @property
    def _index_slot(self) -> NoticeSlot:
        return NoticeSlot(self, text_attr="_index_notice_text")


__all__ = ["SearchFlowMixin"]
