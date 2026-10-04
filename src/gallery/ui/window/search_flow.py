"""检索（ADR 0026）：把一串「``#字段 值``」变成一层照片，外加背后那个索引。

两件事，且它们是**同一个功能的正反面**：

- :meth:`SearchFlowMixin._start_search` —— 条件成立之后按条件查，命中横跨整个库；
- :meth:`SearchFlowMixin._maybe_start_photo_index` —— **没有索引就什么都搜不到**，
  所以打开库时顺手把整库写进索引，等在检索的那一遍跑完会自己把这次检索重跑一次
  （决策 5）。这一遍与地点解析**串行**：两边各自要遍历整棵子树，同时跑等于把磁盘读两遍。

ADR 0026 决策 7 定的规矩在这里落地：**检索结果接着走与扫描完全相同的那段收尾**
（排序、缩略图、焦点恢复、空层提示、监听增量），另写一条平行路径才是 bug 的来源。

条件是**临时的**（决策 9）：换库就清掉，面包屑上那些胶囊跟着消失。
"""

from __future__ import annotations

import logging
from functools import partial

from gallery.core.library.facade import Library
from gallery.core.library.scanner import ScanReport
from gallery.core.library.search import (
    RESULT_LIMIT,
    ParsedInput,
    SearchCondition,
    make_condition,
    parse_input,
    unusable_reason,
)
from gallery.ui.tasks import TaskContext
from gallery.ui.window.library_flow import SCAN_BATCH_SIZE
from gallery.ui.window.notice import NoticeSlot

logger = logging.getLogger(__name__)


class SearchFlowMixin:
    """检索框那一套条件，以及「把整库写进索引」那一遍后台维护。"""

    # ---- 检索（ADR 0026）------------------------------------------------------
    def _start_search(self, library: Library) -> None:
        """按已生效的条件检索整个库，把命中的照片攒进这一层。

        **索引没建全时先把它建起来**（ADR 0026 决策 5）：否则用户看到的是「一条都没有」，
        而那不是答案，是索引还没建。那一遍跑完会自己重跑这次检索（见
        :meth:`_on_photo_index_done`），所以这一轮什么都不做就算数。
        """
        if library.indexed_photos() == 0:
            self._maybe_start_photo_index()
            return
        generation = self._generation
        conditions = tuple(self._conditions)

        def work(context: TaskContext) -> object:
            outcome = library.search_photos(conditions, limit=RESULT_LIMIT)
            refs = outcome.refs
            # 与扫描一样**分批送回**（``_on_scan_batch`` 直接吃这个形状）：结果可能有几千条，
            # 攒齐再一次给出去，中间那段宁可留白（横幅会说明在读什么）。
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
        """检索完了，**接着走与扫描完全相同的那段收尾**。

        facet 补齐、分组排序、缩略图预取、焦点恢复、空层提示、监听增量，一样都不少
        （ADR 0026 决策 7）—— 另写一条平行路径才是 bug 的来源。
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

        三种「提交了但没东西可提交」，各自安静地退开：

        - 空串（回车了两下）；
        - 只敲了字段名（``#相机``）—— 那是补全，不是条件；
        - 值写坏了（``#ISO abc``）—— **这一种要说一句话**，用户以为条件生效了。
        """
        editing = self._editing_condition
        parsed = parse_input(raw)
        condition = None if parsed is None else make_condition(parsed)
        if condition is None:
            # **正在改的那一条被清空后回车 = 撤掉它**。这时输入框是空的，按回车没有别的
            # 意思，而「把这条删了」正是用户清空它想要的结果 —— 于是撤掉这一条，
            # 免得「点一下胶囊就没了」那种意外（改与撤必须是两个动作）。
            if parsed is None and editing is not None:
                self._on_condition_removed(editing)
                return
            self._warn_unusable_input(parsed)
            return
        if self._library is None:
            # 没有库的时候收下这条条件是**悄悄的**：面包屑那一行压根不显示，
            # 用户看不见自己敲了什么，换库时它又被清掉 —— 与其这样，
            # 不如说一句「还没有库可搜」。
            self._show_banner("先打开一个照片文件夹，再搜索。")
            return
        if editing is not None and 0 <= editing < len(self._conditions):
            # **就地替换**：改一条条件的结果仍然是那一条，位置也不变 —— 换成「删掉旧的、
            # 追加一条新的」的话，面包屑上的顺序会跳，用户看着像是多了一条。
            self._conditions[editing] = condition
        else:
            self._conditions.append(condition)
        self._editing_condition = None
        self._warn_metadata_without_exiftool(condition)
        self._apply_conditions()

    def _warn_unusable_input(self, parsed: object) -> None:
        """没提交成的一条输入，值不值得说一句。

        只说「值写坏了」的那一种（``#ISO abc``）：还在挑字段（``#相机``）不是毛病，
        空串也不是 —— 每一句多余的提示都在教用户忽略横幅。

        **具体理由由 :func:`gallery.core.library.search.unusable_reason` 说**：横幅是
        用户唯一能看见的反馈，而「要一个值」对 ``2024～2023`` 与 ``2024-13`` 是同一句话 ——
        两种毛病要改的地方不一样。
        """
        if not isinstance(parsed, ParsedInput) or parsed.selecting or parsed.field is None:
            return
        self._show_banner(
            f"「{parsed.field.label}」{unusable_reason(parsed.field, parsed.value)}"
            "不支持的说法不会变成一条筛选条件。"
        )

    def _warn_metadata_without_exiftool(self, condition: SearchCondition) -> None:
        """提交了一条要读 EXIF 才读得到的条件，而 exiftool 不在。

        与 :meth:`_unavailable_hint` 同一个取向：**降级可以，假装没降级不行**。
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

        正在改某一条的时候，``Esc`` 是**放弃这一次改动**（那一条原样留着）——
        否则「改到一半想溜」与「撤掉一条」是同一下，用户没法中途反悔。
        """
        if self._editing_condition is not None:
            self._cancel_condition_edit()
            return
        self.go_up()

    def _on_search_active_changed(self, _active: bool) -> None:
        """正在输入时别的命令让开 —— 与两个改名输入框同一件事（键盘归输入框）。"""
        self._update_action_states()

    def _on_condition_edited(self, position: int) -> None:
        """点面包屑上第 ``position`` 枚胶囊：把它放回输入框接着改。

        **不是就地改**：胶囊上改不了字（要打字），所以把原文放回搜索框，由用户敲完回车
        （:meth:`_on_search_submitted` 里就地替换那一条）。按钮上会标出「正在改这一条」。
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
        # 序号会往前挪一位 —— 不跟着挪，正在改的那一条就变成了**另一条**。
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
            return  # 标题栏不归我们（见 theme.unify_title_bar），那一侧没有搜索框
        self._search_field.focus_search()

    def _apply_conditions(self) -> None:
        """条件变了（加一条、撤一条、清空）之后重新列出这一层。"""
        self._search_total = None
        self._reload_level()

    # ---- 建立检索索引（ADR 0026 决策 5）---------------------------------------

    def _maybe_start_photo_index(self) -> None:
        """把整个库的照片写进索引的文件表，**只有真的有活干才起**。

        与 :meth:`_maybe_start_place_pass` 同一个套路（ADR 0010）：打开库时顺手填好，
        之后每次检索就只是一次查表。第一遍跑完之前检索会「什么都搜不到」——
        所以这一遍自己会在结束时把在等的检索重跑一次（见 :meth:`_on_photo_index_done`）。

        与地点那一遍**串行**：两边各自要遍历整棵子树，同时跑等于把磁盘读两遍。
        地点那遍先跑（它要起 exiftool 子进程，更贵）；这一遍等它结束再起。
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

        # 横幅**不在这儿亮**：已经建过索引的库这一遍是空跑，先亮等于在说一件没发生的事
        # （与 ``_maybe_start_place_pass`` 同一句理由）。
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
        # **这里不按代次判断**：这一遍要回答的是「此刻有没有人在等检索」，
        # 而条件列表才是那一刻的真相。拿提交时的代次来比会漏掉最常见的情形 ——
        # 用户在索引维护**进行当中**提交了条件，那一下 ``_reload_level`` 已经把代次 +1，
        # 于是这次检索永远不会被补跑，用户看到的是一个空结果（实测并行下会随机变红）。
        #
        # 切库时那一遍会被取消、条件也一并清掉（``_teardown_library``），
        # 所以「此刻的条件」不可能属于一个已经关掉的文件夹。
        if self._conditions:
            self._apply_conditions()

    def _on_photo_index_failed(self, message: str) -> None:
        self._index_job = None
        self._clear_index_notice()
        # 建不了索引不该让浏览整个不可用，只是检索做不了。**把库名从「起过」里拿掉**，
        # 下次换回这个库还能再试一次 —— 否则一次失败就把这个库的检索永久关掉了。
        self._forget_indexed_root()
        logger.info("建立检索索引失败：%s", message)
        if self._conditions:
            self._show_banner("这个库没法建立检索索引，搜索暂时用不了。")

    def _cancel_photo_index(self) -> None:
        """作废在途的索引维护（切库、关窗时）。"""
        if self._index_job is not None:
            self._runner.cancel(self._index_job)
            self._index_job = None
            # 半途取消等于这一遍**没做完**：记在 ``_index_roots`` 里它就再也起不来了，
            # 而下一次切回这个库的用户看到的是「搜不到任何东西」。
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
