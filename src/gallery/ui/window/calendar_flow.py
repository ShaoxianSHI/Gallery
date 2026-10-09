"""日历页（ADR 0033）：年 / 月 / 周三档，以及「点一格」之后的来回。

喂日历要跟着模型走，不能等扫描结束 —— 否则模型还空着时读出零张照片，整层日历空着而看不出原因。
日历是**浏览方式**而不是一层，摆不摆由栈算（``page_role_of``：栈底是日历且顶上没有展开的层就是
日历页）；从日历点开的那一格要记得自己是怎么进来的，退回去回日历原来的那一段。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path

from gallery.core.library.config import BROWSE_MODE_CALENDAR, BROWSE_MODE_FLAT
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import describe_entry
from gallery.ui.tasks import TaskContext
from gallery.ui.window.jobs import LEVEL_CALENDAR
from gallery.ui.window.level_stack import CalendarFrame, root_for


def _photos_on_this_level(entries: Iterable[GridEntry]) -> list[PhotoRef]:
    """这一层上的照片。**组卡片里面的也算**。

    只认 ``PhotoRef`` 会在摆着组卡片的一层读到零张，而零张意味着「日历整层空的」。
    """
    refs: list[PhotoRef] = []
    for entry in entries:
        if isinstance(entry, PhotoRef):
            refs.append(entry)
        elif isinstance(entry, GroupRef):
            refs.extend(entry.photos)
    return refs


class CalendarFlowMixin:
    """三种浏览方式之间的切换，以及日历那一页与「点一格」的来回。"""

    # ---- 浏览方式与相册导航 ------------------------------------------------
    def set_browse_mode(self, mode: str) -> None:
        """切换三种浏览方式：相册（逐层进入）、全部照片（整库平铺）、日历。"""
        if mode == self._browse_mode:
            self._update_mode_actions()
            return
        was_calendar = self._browse_mode == BROWSE_MODE_CALENDAR
        # 先抓住搜索帧：``_set_root`` 会清空整条栈，之后就抓不到了。
        leaving_search = self._search_frame
        self._set_root(root_for(mode))  # 换浏览方式 = 换根，顺带清掉上面的一切
        self._cancel_condition_edit()
        # 整库检索横跨整个库，换层就得清；筛选是收窄当前层，跟着走（ADR 0040 决策 1）。
        if leaving_search is not None:
            self._leave_search_frame(leaving_search, reload=False)
        self._save_ui_state()
        self._update_mode_actions()
        if self._library is None:
            return
        if mode == BROWSE_MODE_CALENDAR:
            self._enter_calendar()
        elif was_calendar and mode == BROWSE_MODE_FLAT:
            # 刚退出检索时模型里摆的是检索结果，不是整库 —— 必须重新列出这一层。
            if leaving_search is not None:
                self._reload_level()
            else:
                self._apply_ordering()
                self._sync_ui()
        else:
            # 从日历切到「相册」要重列当前目录：模型里此刻摆的是整库照片。
            if was_calendar:
                self._cancel_calendar_job()
            self._reload_level()

    def _show_calendar(self) -> None:
        """把日历那一页摆到前面来。**只换页，不起扫描**（打开库时外面已经在扫了）。"""
        self._calendar_page.set_today(datetime.date.today())
        self._sync_ui()
        self._refresh_thumbnail_window()

    def _enter_calendar(self) -> None:
        """切到日历那一页，并把整库的照片与拍摄时间喂给它。

        **模型里仍然是整库**：日历点开一张照片要能直接进大图页，而大图页按行工作
        （``_load_detail``），所以这里走与「全部照片」同一遍平铺扫描。换页即换层：进行中的
        扫描与元数据读取一律作废重开。**还要把这一层记成日历那一层**
        （ADR 0033 决策 11 末尾引的 ADR 0025 决策 6），否则退回相册时会答成「还是这一层」。"""
        self._cancel_tier(LEVEL_CALENDAR)
        self._generation += 1
        self._arm_focus()
        self._grid.cancel_rename()
        self._model.clear()
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = []
        self._banner_text.reset_level()
        self._banner_text.clear_scan_issue()
        self._focus_state.clear_current()
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        self._show_calendar()
        self._start_scan()

    def _cancel_calendar_job(self) -> None:
        """作废日历那次读拍摄时间（换库、换浏览方式时）。"""
        self._cancel_job("_calendar_job")

    def _on_calendar_window_changed(self) -> None:
        """日历换层 / 翻页 / 滚动：重新向流水线要这一层看得见的那几张。

        日历上翻到空月份不再说话（见 :meth:`_update_level_notice`），所以只剩缩略图这一件事。
        """
        self._refresh_thumbnail_window()

    def _on_calendar_level_changed(self, level: str) -> None:
        """记下日历停在哪一层。**库级**偏好，与浏览方式各自写进 ``config.toml``。

        **先换属性再存**：``_save_ui_state`` 是从属性上拼出整份偏好的，只存不换会存回旧层级。
        """
        self._calendar_level = level
        self._save_ui_state()

    def _on_calendar_cell_requested(self, photos: object, label: object) -> None:
        """日历上点了有照片的一格：就一张直接进大图，好几张摊成一层网格。

        **两种都记得是从日历来的**，于是退出去时回到日历原来的那一段与那个滚动位置。
        """
        if not isinstance(photos, list) or not photos:
            return
        if not all(isinstance(ref, PhotoRef) for ref in photos):
            return
        if len(photos) == 1:
            # **日历格与照片是一起进出的一对**（``expanded=False``）：那一格从没摊成网格，
            # 点开直接进大图，``Esc`` 把两帧一起收掉、回日历。多张的那一格才是独立的一层。
            self._push_frame(
                CalendarFrame(
                    photos=(photos[0],),
                    label=label if isinstance(label, str) else "",
                    expanded=False,
                )
            )
            self._open_calendar_photo(photos[0])
            return
        self._enter_calendar_drill(tuple(photos), label if isinstance(label, str) else "")

    def _open_calendar_photo(self, ref: PhotoRef) -> None:
        """日历上唯一那一张：按它在模型里的行打开大图页。

        模型里没有这一张（扫描还没到）就当没点 —— 与 ``context_menu_for`` 同一个脾气。
        """
        row = self._model.row_of_path(ref.path)
        if row < 0:
            return
        self._grid.select_row(row)
        self._open_detail(self._model.index(row, 0))

    def _enter_calendar_drill(self, photos: tuple[PhotoRef, ...], label: str) -> None:
        """把日历上那一格的若干张摊到网格页（**不重新扫盘**），顺序按当前排序。

        **告诉网格吞掉紧接着的那一下**（见 ``PhotoGrid.swallow_click``）：日历上那一下按下已经
        算双击的第一下，这一页紧接着那一下会被判成「网格里的第二下」，而那个位置多半是空白，
        于是「双击空白处退一层」当场把人带回日历。
        """
        self._push_frame(CalendarFrame(photos=photos, label=label))
        # **进行中的扫描作废**：这一层不是整库，那一遍扫描落上来会把这一格的几张换回整库照片。
        if self._scan_job is not None:
            self._cancel_job("_scan_job")
            self._generation += 1
            self._staged = []
        # **先摆内容再换页**：反过来，换页那一刻拿的还是日历底下那一整库，算出来的可见行对不上。
        self._list_drill_photos(photos)
        self._grid.swallow_click()  # 见上面那段：双击的第二下属于日历那一下
        # **摆内容之后才换页**：换早了那一刻网格算出来的可见行对不上（这一格的几张还没进模型）。
        self._sync_ui()
        self._refresh_thumbnail_window()

    def _list_drill_photos(self, photos: Sequence[PhotoRef]) -> None:
        """把这一层换成这若干张（同步排一次：就几十上百张，不值得占一个后台任务）。"""
        library = self._library
        self._model.clear()
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._focus_state.clear_current()
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        root = library.root if library is not None else Path.home()
        entries = [describe_entry(root, ref.path) or ref for ref in photos]
        self._staged = entries
        if library is None:
            self._model.append(entries)
            self._staged = []
            return
        self._apply_ordering()
        self._start_facet_read()

    def _leave_calendar_cell(self) -> None:
        """**日历格那一帧已经被 ``go_up`` 弹掉了** —— 这一步只做模型与视图的收尾。

        分成两个方法是因为「弹栈」是统一的（「谁弹的都一样」），而收尾因帧而异（「日历格要回
        日历页、分组要把刚离开的卡片选中」）。收尾若自己弹一次字段，**弹栈这件事就有两处**，
        漏一处会出现「按 Esc 位置动了而模型还是那一格的内容」。
        """
        self._model.clear()
        self._grid.reset_for_new_level()
        self._focus_state.clear_current()
        self._back_to_calendar()
        self._start_scan()

    def _back_to_calendar(self) -> None:
        """把日历那一页摆回前面：**位置不动**（锚点与滚动条都还在那一页的控件里）。

        刻意不碰层级：用户刚找到的那一段是他的上下文，退出看一眼再回来不该清成「今天」。
        焦点与残留高亮由 ``CalendarPage.showEvent`` 收拾，不必两处各写一遍。
        """
        if self._browse_mode != BROWSE_MODE_CALENDAR:
            return
        self._calendar_page.set_today(datetime.date.today())
        self._sync_ui()
        self._refresh_thumbnail_window()

    def _start_calendar_load(self) -> None:
        """把整库照片按天与按小时分好桶，交给日历。

        与 ``_start_facet_read`` 同一套：**索引里全都有就直接分**，缺才起后台线程去读 ——
        于是「正在读元数据…」那句话只在真的要读的时候出现。
        """
        library = self._library
        if library is None:
            return
        refs = _photos_on_this_level(self._model.entries())
        if not refs:
            return
        generation = self._generation

        # **查索引也搬到后台** —— 理由与 ``ordering._start_facet_read`` 那条完全一样。
        def work(context: TaskContext) -> object:
            cached = library.cached_facets(refs)
            if len(cached) == len(refs):
                return cached  # 全都有 → 不报「正在读」
            context.progress(f"正在读取 {len(refs)} 张照片的拍摄时间，用于日历…")
            return library.photo_facets(
                refs,
                should_cancel=context.is_cancelled,
                on_progress=lambda done, total: context.progress((done, total)),
            )

        self._start_job(
            "_calendar_job",
            work,
            on_progress=partial(self._on_facet_progress, generation),
            on_result=partial(self._on_calendar_facets_ready, generation, refs),
            on_error=partial(self._on_calendar_facets_failed, generation),
        )

    def _on_calendar_facets_ready(
        self, generation: int, refs: list[PhotoRef], payload: object
    ) -> None:
        if generation != self._generation or not isinstance(payload, dict):
            return
        self._calendar_job = None
        self._clear_progress_notice()
        self._calendar_page.set_photos(refs, payload)
        self._update_level_notice()
        self._refresh_thumbnail_window()

    def _on_calendar_facets_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._calendar_job = None
        self._clear_progress_notice()
        self._show_banner(f"日历没能读到拍摄时间：{message}")


__all__ = ["CalendarFlowMixin"]
