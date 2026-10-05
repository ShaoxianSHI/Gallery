"""分组与排序（ADR 0009）：这一层里的照片按什么归拢、按什么排队。

它是一段**完整的流程**（换字段 → 后台读元数据 → 报进度 → 按快照重排），而**别的所有流程都要
经过** :meth:`OrderingMixin._apply_ordering`（打开库、检索、监听到新照片）。

它同时是「流程的总闸」：``_staged``（暂存区）、``_generation``、``_facet_values`` 都由这里
维护 —— 混在别处的话，「扫出来的条目为什么还没上屏」就得跨三个文件去找。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from gallery.core.library.config import BROWSE_MODE_ALBUMS, BROWSE_MODE_CALENDAR
from gallery.core.library.facade import Library
from gallery.core.library.groups import SortOrder, build_groups, sort_photos
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.exif import ExifToolReader
from gallery.core.metadata.facets import (
    GROUP_FACETS,
    SORT_FACETS,
    Facet,
    group_facet,
    needs_metadata,
    sort_facet,
)
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui.navigation import EntryKey, entry_key
from gallery.ui.tasks import TaskContext
from gallery.ui.window.notice import NoticeSlot


class OrderingMixin:
    """「视图 → 分组 / 排序」两个菜单，以及把当前这一层重排一遍的那条链。"""

    # ---- 分组与排序（见 ADR 0009）--------------------------------------------
    def _build_order_menus(self, view_menu: QMenu) -> None:
        """「视图 → 分组 / 排序」两个子菜单。

        放菜单而不是窗口上的控件：照片区那点高度不该拿来放两个下拉。
        """
        group_menu = view_menu.addMenu("分组")
        group_menu.setToolTipsVisible(True)
        self._group_actions: dict[str | None, QAction] = {
            None: self._order_choice(group_menu, "不分组", None, self.set_group_facet)
        }
        for facet in GROUP_FACETS:
            self._group_actions[facet.id] = self._order_choice(
                group_menu,
                facet.label,
                facet.id,
                self.set_group_facet,
                unavailable=self._unavailable_hint(facet),
            )

        sort_menu = view_menu.addMenu("排序")
        sort_menu.setToolTipsVisible(True)
        # 菜单里**没有「不排序」**：扫描顺序是内部实现，摆成选项只会让人在两种
        # 看不懂的顺序之间来回试。
        self._sort_actions: dict[str, QAction] = {}
        for facet in SORT_FACETS:
            self._sort_actions[facet.id] = self._order_choice(
                sort_menu,
                facet.label,
                facet.id,
                self.set_sort_facet,
                unavailable=self._unavailable_hint(facet),
            )
        sort_menu.addSeparator()
        self._descending_action = QAction("逆序", self)
        self._descending_action.setCheckable(True)
        self._descending_action.toggled.connect(self.set_sort_descending)
        sort_menu.addAction(self._descending_action)

    def _order_choice(
        self,
        menu: QMenu,
        label: str,
        facet_id: str | None,
        handler: Callable[[str | None], None],
        *,
        unavailable: str | None = None,
    ) -> QAction:
        """菜单里的一个单选项。``unavailable`` 非空时置灰并说明原因。"""
        action = QAction(label, self)
        action.setCheckable(True)
        if unavailable is not None:
            action.setEnabled(False)
            action.setToolTip(unavailable)
        else:
            action.triggered.connect(lambda _checked=False, target=facet_id: handler(target))
        menu.addAction(action)
        return action

    def _unavailable_hint(self, facet: Facet) -> str | None:
        """没装 exiftool 时，只有 exiftool 读得到的那些字段要**明说**为什么用不了。

        降级可以，假装没降级不行：让用户分出一个「全都没有」的组，比一开始就告诉他
        「这个字段要装 exiftool」更糟。
        """
        if not facet.needs_exiftool or self._has_exiftool:
            return None
        return "需要 exiftool 才能读取这个字段。安装后即可使用：brew install exiftool"

    @property
    def _has_exiftool(self) -> bool:
        """exiftool 现在能不能用。启动时探一次，之后不再反复起进程去问。"""
        if self._exiftool_available is None:
            self._exiftool_available = ExifToolReader().available()
        return self._exiftool_available

    def set_group_facet(self, facet_id: str | None) -> None:
        """切换分组字段（菜单用）。"""
        if facet_id is not None and group_facet(facet_id) is None:
            return  # 非法 id：会话文件里的旧值也可能走到这里，静默忽略
        if facet_id == self._group_facet:
            self._sync_order_actions()
            return
        self._group_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        # 换分组字段等于换了一组组卡片，原来进入的那个组已经不存在了。
        self._group_view = None
        self._reload_level()

    def set_sort_facet(self, facet_id: str | None) -> None:
        """切换排序字段（菜单用）。组内排序也跟着换 —— 这正是它的用处。"""
        if facet_id is None or sort_facet(facet_id) is None:
            return  # 没有「不排序」这个选择；非法 id 只可能来自旧会话，静默忽略
        if facet_id == self._sort_facet:
            self._sync_order_actions()
            return
        self._sort_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def set_sort_descending(self, descending: bool) -> None:
        """切换正序 / 逆序。只重排当前这一层，不必重新读元数据。"""
        if descending == self._sort_descending:
            return
        self._sort_descending = descending
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def _remember_ordering(self) -> None:
        # 分组 / 排序是**库级**偏好（ADR 0038 决策 4）：切库时不该把 A 库的分组法传染给 B 库。
        self._save_ui_state()

    def _sync_order_actions(self) -> None:
        """让菜单的勾选状态跟着实际选择走（含从会话恢复出来的选择）。"""
        for facet_id, action in self._group_actions.items():
            action.setChecked(facet_id == self._group_facet)
        for facet_id, action in self._sort_actions.items():
            action.setChecked(facet_id == self._sort_facet)
        self._descending_action.setChecked(self._sort_descending)

    def _current_order(self) -> SortOrder:
        return SortOrder(facet=sort_facet(self._sort_facet), descending=self._sort_descending)

    def _current_group_facet(self) -> Facet | None:
        return group_facet(self._group_facet)

    def _needs_facet_read(self) -> bool:
        """当前的选择是否必须真去读 EXIF 才能算出顺序。"""
        group = None if self._group_view is not None else self._current_group_facet()
        return needs_metadata((group, sort_facet(self._sort_facet)))

    # ---- 进入与退出分组 ------------------------------------------------------

    def enter_group(self, group: GroupRef, *, focus: EntryKey | None = None) -> None:
        """进入一个分组（双击组卡片）。

        分组不是磁盘上的目录，因此不走 ``enter_folder``：它记不住路径，退出时直接回到进来
        之前的那一层。

        :param focus: 到了这一层选中哪一格。只有软件自己为了重新分组绕出去再回来时才给；
            用户点进来的一律谁也不选。
        """
        self._group_view = group
        self._reload_level(focus=focus)

    def _leave_group(self) -> None:
        group = self._group_view
        if group is None:
            return
        self._group_view = None
        # 退回来的那一层选中这张组卡片：分组不在磁盘上，退出时回不到「上一层目录」。
        self._reload_level(focus=entry_key(group))

    # ---- 读取本层元数据并重排 ------------------------------------------------

    def _start_facet_read(self) -> None:
        """按当前选择给这一层补齐元数据，然后重排。

        只有真的要读 EXIF 的字段才会走到这里（按文件名排序是纯算的）。读过一次的结果落进
        索引，所以第二次换分组字段几乎立刻完成。
        """
        library = self._library
        if library is None or not self._needs_facet_read():
            self._apply_ordering()
            return
        # 暂存优先：刚扫完的这一层还在暂存区里，模型上一条也没有。
        entries = self._staged if self._staged is not None else self._model.entries()
        refs = [entry for entry in entries if isinstance(entry, PhotoRef)]
        if not refs:
            self._apply_ordering()
            return
        generation = self._generation

        # **索引里全都有就直接排**（「第二次打开这个库」的样子）：起后台线程、报「正在读取
        # N 张照片的元数据」都是在说一件没发生的事，用户没法分辨是又在干活还是软件出了毛病。
        cached = library.cached_facets(refs)
        if len(cached) == len(refs):
            self._facet_values = cached
            self._apply_ordering()
            return

        self._show_progress_notice(
            f"正在读取 {len(refs)} 张照片的元数据，用于{'分组' if self._group_facet else '排序'}…"
        )

        resolver = self._place_lookup()

        def work(context: TaskContext) -> object:
            return library.photo_facets(
                refs,
                should_cancel=context.is_cancelled,
                on_progress=lambda done, total: context.progress((done, total)),
                status=lambda text: context.progress(text),
                resolver=resolver,
            )

        self._facet_job = self._runner.submit(
            work,
            on_progress=partial(self._on_facet_progress, generation),
            on_result=partial(self._on_facets_ready, generation),
            on_error=partial(self._on_facets_failed, generation),
        )

    def _show_progress_notice(self, text: str) -> None:
        """「正在读取元数据…」**让着**用户刚做完的那件事（与 ``_show_place_notice`` 同一套）。

        这不是客气，是一次真实存在的抢话：用户刚编辑完位置，横幅上正写着「坐标已按『杭州』
        更新」，紧接着的 facet 重读把它顶掉、读完再收走 —— 那句话他就永远看不到了，而它是
        唯一告诉他「坐标被动过」的地方（ADR 0030）。
        """
        self._progress_slot.show(text, defer=False)

    def _clear_progress_notice(self) -> None:
        """收掉「正在读取元数据…」那条 —— **只在自己还占着的时候收**（见 ``NoticeSlot.clear``）。"""
        self._progress_slot.clear()

    @property
    def _progress_slot(self) -> NoticeSlot:
        return NoticeSlot(self, text_attr="_progress_notice_text", flag_attr="_progress_notice")

    def _update_level_notice(self) -> None:
        """这一层什么都没有时说一声；有了内容就把这句收回去。

        **判据是「网格上有没有条目」，不是「有没有相册 / 照片」**：分组会把照片换成一张张组
        卡片，只数相册和照片会把一屏组卡片误判成空。

        **日历那一页不另说一句**：翻到没有照片的月份是常事，横幅冒出来等于把「翻过去了」说成
        「出问题了」。
        """
        if self._library is None:
            return
        if self._scan_issue:
            # 扫描出过问题时横幅归它：那句「哪些内容没读到」更该说，这里也不许把它收掉。
            return
        if self._model.rowCount():
            if self._level_notice:
                self._level_notice = False
                self._banner.hide()
            return
        self._level_notice = True
        if self._conditions:
            # 检索没命中时**不要说那句格式清单**：它与此刻无关，用户会以为文件格式不对。
            self._show_banner("这些筛选条件下没有照片。")
        elif self._group_view is not None:
            self._show_banner("这个分组里没有照片了。")
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._show_banner("这个文件夹里既没有子文件夹，也没有可显示的照片。")
        else:
            self._show_banner("没有找到可显示的照片。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP。")

    def _cancel_facet_job(self) -> None:
        """作废在途的元数据读取（换库、换层、换分组字段时）。"""
        if self._facet_job is not None:
            self._runner.cancel(self._facet_job)
            self._facet_job = None

    def _place_lookup(self) -> LazyPlaceResolver:
        """共用的地点反查器：分组、元数据面板、打开库时那一遍都用这一个。

        城市库（解压后约 1.2 MB）只该读一次，所以三条路径共用同一个实例 —— 反查器自己
        缓存着加载好的索引与查过的坐标。
        """
        if self._place_resolver is None:
            self._place_resolver = LazyPlaceResolver()
        return self._place_resolver

    def _on_facet_progress(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        # 每一条都走 ``_show_progress_notice``：记下自己写过的话，收尾才收得掉自己那一句。
        if isinstance(payload, str):
            self._show_progress_notice(payload)  # 「正在加载城市库…」这类说明
            return
        if not isinstance(payload, tuple):
            return
        done, total = payload if len(payload) == 2 else (0, 0)
        if isinstance(done, int) and isinstance(total, int) and total:
            self._show_progress_notice(f"正在读取元数据…{done} / {total}")

    def _on_facets_ready(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._facet_values = payload if isinstance(payload, dict) else {}
        self._clear_progress_notice()
        self._apply_ordering()
        # 这一层读完了，才轮到整库那两遍（见 _maybe_start_place_pass 里的理由）。
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _on_facets_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._show_banner(f"读取元数据失败，按默认顺序显示：{message}")
        self._apply_ordering()
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _apply_ordering(self) -> None:
        """把当前这一层按分组 / 排序重排一次，然后一次性摆到网格上。

        **大图页上不重排**（重排会让那一行的内容换掉，而标题栏还写着刚才那张的名字，ADR 0025
        决策 4），**日历那一页要重排**（它的模型里装着整库，模型空着的话从日历点照片没反应）。

        **日历在这里喂，不在扫描收工时喂**：排序要读元数据时，重排落在那个后台任务回来之后，
        而「扫描完 → 喂日历」会在模型还空着时读出零张照片，整层日历从此空着。
        """
        library = self._library
        if library is None:
            return
        in_grid = self._stack.currentWidget() is self._grid_page
        try:
            # 大图页上不重排（ADR 0025 决策 4）—— 但日历那一页仍然要喂。
            if self._stack.currentWidget() is not self._detail_page:
                self._reorder_level(library)
        finally:
            # 顺序定下来了，这才有资格回答「这一层是不是空的」。
            self._update_level_notice()
            if in_grid:
                # 也才找得到「退上去时该停住的那一格」：组卡片本身就是重排出来的。
                self._restore_focus()
                # 分组视图里来了新照片时，这里是重新分组之后回到原处的地方（ADR 0013）。
                self._maybe_reenter_group()
            # 暂存区到这里才清空：攒着的变化从这一刻起才能安全地落到网格上。
            self._flush_pending_watch()
            # 模型此刻摆着的就是这一层，**这时**喂日历才对得上（见上面那段）。
            self._feed_calendar()

    def _reorder_level(self, library: Library) -> None:
        # 暂存的优先：那一层还没上屏，顺序就是这里算出来的。
        staged = self._staged
        entries = staged if staged is not None else list(self._model.entries())
        self._staged = None
        albums = [entry for entry in entries if isinstance(entry, FolderRef)]
        photos = [entry for entry in entries if isinstance(entry, PhotoRef)]
        # 相册卡片永远排在前面：先看文件夹、再看按元数据归拢出来的组，是既有心智。
        order = self._current_order()
        # 已经在分组里面了（再分一次就出不来），**日历那一页也不分组**：它自己就是按日期归拢
        # 的那一层，模型里要装整库照片；带着分组进去时模型里全是组卡片，日历读到零张照片。
        grouped = self._group_view is not None or self._browse_mode == BROWSE_MODE_CALENDAR
        group = None if grouped else self._current_group_facet()
        values = self._facet_values
        if group is not None:
            ordered: list[GridEntry] = [
                *albums,
                *build_groups(photos, facet=group, order=order, root=library.root, values=values),
            ]
        else:
            ordered = [*albums, *sort_photos(photos, order=order, root=library.root, values=values)]
        if staged is None and ordered == list(self._model.entries()):
            return  # 什么都没攒、顺序也没变：让整屏重画一次毫无意义
        selected = self._grid.selected_rows()
        anchor = self._model.photo_at(self._grid.current_row())
        self._model.reorder(ordered)
        if anchor is not None:
            row = self._model.row_of_path(anchor.path)
            if row >= 0:
                self._grid.select_row(row, ensure_visible=False)
        elif selected:
            self._grid.select_row(min(selected), ensure_visible=False)
        self._refresh_thumbnail_window()


__all__ = ["OrderingMixin"]
