"""日历页（ADR 0033）：年 / 月 / 周三档，以及「点一格」之后的来回。

这一段里有几处**曾经是 bug 的地方**，它们同时也是这一段最该保留的注释：

- **模型里放的仍然是整库**（不是日历那一格）。日历点开一张照片要能直接进大图页，
  而大图页是按**行**工作的（``viewer_flow._load_detail``）—— 于是日历只是叠在
  「全部照片」同一遍平铺扫描上的另一页，翻图、改名、移到废纸篓、撤销一个都不用为它重写。
- **喂日历要跟着模型走**，不能在扫描收工时喂（``ordering._apply_ordering`` 里那一段）。
  模型什么时候摆好是不定的，于是「扫描完 → 喂日历」会在模型还空着的时候读出零张照片，
  整层日历从此空着，而界面上看不出任何原因。
- **从日历点开的那一格要记得自己是怎么进来的**：退回去回**日历原来的那一段**，
  而不是「全部照片」那一屏 —— 后者与用户刚做的事没有关系。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path

from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import describe_entry
from gallery.core.library.session import BROWSE_MODE_CALENDAR
from gallery.ui.tasks import TaskContext


def _photos_on_this_level(entries: Iterable[GridEntry]) -> list[PhotoRef]:
    """这一层上的照片。**组卡片里面的也算**。

    分组是网格那一页的事，但模型是共用的：某一层恰好摆着组卡片时，只认
    ``PhotoRef`` 会读到零张 —— 而零张意味着「日历整层空的」，界面上却看不出
    任何原因。组卡片里正是照片，所以把它们摊平才对。
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
        self._from_calendar = False
        self._drill_photos = None
        self._drill_label = ""
        self._browse_mode = mode
        self._group_view = None
        # 检索是横跨整个库的一层（ADR 0026 决策 1），换浏览方式时留着它等于把两个
        # 互不相干的概念叠在一起：用户看到的照片既不是「整库平铺」也不是「这一层」。
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None
        self._session = self._session.with_browse_mode(mode)
        self._save_session()
        self._update_mode_actions()
        if self._library is None:
            return
        if mode == BROWSE_MODE_CALENDAR:
            self._enter_calendar()
        elif was_calendar:
            # 日历那一页底下是**整库**（与「全部照片」同一份内容），回到网格时
            # 那一层直接就是它，不必重扫一遍盘。
            #
            # 但**分组要补回去**：日历那一页摆的是没分组的整库照片（它自己就是按
            # 日期归拢的那一层），而用户菜单里选的那个分组是他自己的选择 ——
            # 退出日历不该顺手把它取消掉。重排是就地做的（不碰盘）。
            self._stack.setCurrentWidget(self._grid_page)
            self._apply_ordering()
            self._refresh_title()
            self._update_action_states()
            self._refresh_thumbnail_window()
        else:
            self._reload_level()

    def _show_calendar(self) -> None:
        """把日历那一页摆到前面来。**只换页，不起扫描**（打开库时外面已经在扫了）。"""
        self._calendar_page.set_today(datetime.date.today())
        self._stack.setCurrentWidget(self._calendar_page)
        self._refresh_title()
        self._update_action_states()
        self._refresh_thumbnail_window()

    def _enter_calendar(self) -> None:
        """切到日历那一页，并把整库的照片与拍摄时间喂给它。

        **模型里仍然是整库**：日历点开一张照片要能直接进大图页，而大图页是按**行**
        工作的（`_load_detail`）。所以这里走的是与「全部照片」同一遍平铺扫描，
        日历只是叠在它上面的另一页 —— 于是翻图、改名、移到废纸篓、撤销、
        「在访达中显示」这些一个都不用为日历另写一份。

        开头那一段与 :meth:`_reload_level` 一样：换页就是换一层，在途的扫描与
        元数据读取一律作废重开 —— 否则刚切过去时，**上一遍扫描的结果会跟着进来**
        （它是同一个 generation 的），用户看到的是相册模式那一层混进日历。
        """
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        self._generation += 1
        self._grid.cancel_rename()
        self._cancel_facet_job()
        self._cancel_calendar_job()
        self._model.clear()
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = []
        self._level_notice = False
        self._progress_notice = False
        self._scan_issue = False
        self._current_row = -1
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        self._show_calendar()
        self._start_scan()

    def _cancel_calendar_job(self) -> None:
        """作废日历那次读拍摄时间（换库、换浏览方式时）。"""
        if self._calendar_job is not None:
            self._runner.cancel(self._calendar_job)
            self._calendar_job = None

    def _on_calendar_window_changed(self) -> None:
        """日历换层 / 翻页 / 滚动：重新向流水线要这一层看得见的那几张。

        **不必再判「这一段有没有照片」**：日历上翻到空月份不再说话（见
        :meth:`_update_level_notice`），所以这里只剩缩略图这一件事。
        """
        self._refresh_thumbnail_window()

    def _on_calendar_level_changed(self, level: str) -> None:
        """记下日历停在哪一层（进会话，与浏览方式同一个开关）。"""
        self._session = self._session.with_calendar_level(level)
        self._save_session()

    def _on_calendar_cell_requested(self, photos: object, label: object) -> None:
        """日历上点了有照片的一格：就一张直接进大图，好几张摊成一层网格。

        **两种都记得是从日历来的**，于是从那里出来时回到**日历原来的那一段与那个滚动
        位置**，而不是「全部照片」那一屏 —— 后者与用户刚才做的事没有关系，那是他三步
        之前才用过的地方。
        """
        if not isinstance(photos, list) or not photos:
            return
        if not all(isinstance(ref, PhotoRef) for ref in photos):
            return
        self._from_calendar = True
        if len(photos) == 1:
            self._open_calendar_photo(photos[0])
            return
        self._enter_calendar_drill(tuple(photos), label if isinstance(label, str) else "")

    def _open_calendar_photo(self, ref: PhotoRef) -> None:
        """日历上唯一那一张：按它在模型里的行打开大图页。

        模型里没有这一张（扫描还没到）就当没点 —— 与 ``context_menu_for`` 同一个脾气：
        宁可不弹，也不对着一个过期的行号动手。
        """
        row = self._model.row_of_path(ref.path)
        if row < 0:
            return
        self._grid.select_row(row)
        self._open_detail(self._model.index(row, 0))

    def _enter_calendar_drill(self, photos: tuple[PhotoRef, ...], label: str) -> None:
        """把日历上那一格的若干张摊到网格页（**不重新扫盘**：就这些照片）。

        这一层不进浏览方式的账：它是日历点出来的一个视图，从它退回去回日历，
        而不是退到「这一层是按文件夹进来的」。它的顺序按当前排序走 ——
        用户在网格里排好的序，在这里也该是同一个序。
        """
        self._drill_photos = photos
        self._drill_label = label
        # **在途的扫描作废**：这一层不是整库，那一遍扫描的结果落上来会把这一格的几张
        # 换回整库照片（症状是「点开一格，出来一大片」，而用户没碰任何东西）。
        # 刚切到日历就点一格时正好会这样 —— 日历那一页摆的还是上一次的桶，
        # 而整库平铺还在扫。
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
            self._generation += 1
            self._staged = []
        # **先摆内容再换页**：反过来就是换页那一刻拿的还是日历底下那一整库，
        # 按它算出来的可见行与真正要显示的这一批对不上（多要一批，还都是马上要丢的）。
        self._list_drill_photos(photos)
        self._stack.setCurrentWidget(self._grid_page)
        self._refresh_title()
        self._update_action_states()
        self._refresh_thumbnail_window()

    def _list_drill_photos(self, photos: Sequence[PhotoRef]) -> None:
        """把这一层换成这若干张（同步排一次：就几十上百张，不值得占一个后台任务）。"""
        library = self._library
        self._model.clear()
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._current_row = -1
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

    def _leave_calendar_drill(self) -> None:
        """从「那一格」退回日历，**模型回到整库**（下一层点开照片还要靠它的行）。"""
        self._drill_photos = None
        self._drill_label = ""
        self._from_calendar = False
        self._model.clear()
        self._grid.reset_for_new_level()
        self._current_row = -1
        self._back_to_calendar()
        self._start_scan()

    def _back_to_calendar(self) -> None:
        """把日历那一页摆回前面：**位置不动**（锚点与滚动条都还在那一页的控件里）。

        这里刻意不碰层级与选中：用户刚找到的那一段、那一张，是他的上下文，
        退出看一眼再回来不该把它清成「今天」。
        """
        if self._browse_mode != BROWSE_MODE_CALENDAR:
            return
        self._stack.setCurrentWidget(self._calendar_page)
        self._calendar_page.set_today(datetime.date.today())
        self._refresh_title()
        self._update_action_states()
        self._refresh_thumbnail_window()

    def _start_calendar_load(self) -> None:
        """把整库照片按天与按小时分好桶，交给日历。

        与 ``_start_facet_read`` 是同一套：**索引里全都有就直接分**（第二次打开这个库
        的样子，纯粹是一次查表），缺才起后台线程去读 —— 于是「正在读元数据…」那句话
        只在真的要读的时候才出现。
        """
        library = self._library
        if library is None:
            return
        refs = _photos_on_this_level(self._model.entries())
        if not refs:
            return
        cached = library.cached_facets(refs)
        if len(cached) == len(refs):
            self._calendar_page.set_photos(refs, cached)
            self._update_level_notice()
            self._refresh_thumbnail_window()
            return

        self._show_progress_notice(f"正在读取 {len(refs)} 张照片的拍摄时间，用于日历…")
        generation = self._generation

        def work(context: TaskContext) -> object:
            return library.photo_facets(
                refs,
                should_cancel=context.is_cancelled,
                on_progress=lambda done, total: context.progress((done, total)),
            )

        self._calendar_job = self._runner.submit(
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
