"""分组与排序（ADR 0009）：这一层里的照片按什么归拢、按什么排队。

它是一段**完整的流程**（换字段 → 后台读元数据 → 报进度 → 按快照重排），而**别的所有流程都要
经过** :meth:`OrderingMixin._apply_ordering`（打开库、检索、监听到新照片）。它同时维护
``_staged``（暂存区）、``_generation``、``_facet_values``，混在别处的话「扫出来的条目为什么
还没显示到界面」就得跨三个文件去找。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Final

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from gallery.core.events.kinds import EVENT_KINDS
from gallery.core.events.store import EventRef
from gallery.core.library.config import BROWSE_MODE_ALBUMS, BROWSE_MODE_CALENDAR
from gallery.core.library.facade import Library
from gallery.core.library.groups import SortOrder, build_groups, sort_photos
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.facets import (
    FACET_EVENT,
    GROUP_FACETS,
    SORT_FACETS,
    Facet,
    PhotoFacets,
    event_field_facet,
    group_facet,
    groupable_event_fields,
    is_event_facet,
    needs_metadata,
    sort_facet,
)
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui.tasks import TaskContext
from gallery.ui.window.navigation import EntryKey, entry_key
from gallery.ui.window.state.level_stack import GroupFrame
from gallery.ui.window.state.ordering_state import OrderingState
from gallery.ui.window.state.staging import Staging

#: 「正在读取 N 张照片的元数据，用于…」—— **只在真的缺东西时才报**（见 ``_start_facet_read``）。
#: 放在模板里是因为它现在从工作线程里发出去，而那一侧拿不到 ``self``。
_FACET_PROGRESS: Final = "正在读取 {} 张照片的元数据，用于{}…"


@dataclass(frozen=True, slots=True)
class _LevelFacets:
    """后台那一次读取的结果。

    **两样一起回来**而不是分两条任务：它们要的是同一个「这一层的照片」，而分组是在主线程同步算的，
    所以两样都得在那一次后台里取好（ADR 0043 决策 7）。``events`` 只在按事件分组时非空。
    """

    values: dict[Path, PhotoFacets]
    events: dict[Path, tuple[EventRef, ...]] = field(default_factory=dict)


def _events_of(library: Library, refs: Sequence[PhotoRef]) -> dict[Path, tuple[EventRef, ...]]:
    """这些照片各属于哪些事件。**事件库不可用时是空的一份**，不是报错 —— 那一行菜单压根
    不该摆出来（ADR 0043 决策 1）。"""
    if not library.events_available:
        return {}
    return library.events_for_photos(refs)


class OrderingMixin:
    _staging: Staging

    def _init_staging(self) -> None:
        self._staging = Staging()

    # 转发 —— 测试直接摸 ``window._staged``（`tests/ui/test_calendar_flow.py` 有一处
    # 直接赋值造「正在暂存」的样子）。
    @property
    def _staged(self) -> list[GridEntry] | None:
        return self._staging.peek()

    @_staged.setter
    def _staged(self, value: list[GridEntry] | None) -> None:
        self._staging._staged = value

    """「视图 → 分组 / 排序」两个菜单，以及把当前这一层重排一遍的那条链。

    存储住在 :class:`~gallery.ui.window.ordering_state.OrderingState` 里
    （ADR 0058 阶段 2），本 mixin 只管「怎么排」。窗口上留着一组同名 property 转发 ——
    **转发不产生第二份存储**。
    """

    _ordering: OrderingState

    def _init_ordering(self) -> None:
        """建这一族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._ordering = OrderingState()

    # guard: allow-long-comment（9 组转发，拆开就没法一眼看出「名字 ↔ 槽」一一对应）
    # **为什么留这一层** —— 测试直接摸 ``window._group_facet`` / ``_sort_facet``，
    # 而属性名一个字都不改是本包的规矩。

    @property
    def _facet_values(self) -> dict[Path, PhotoFacets]:
        return self._ordering._facet_values

    @_facet_values.setter
    def _facet_values(self, value: dict[Path, PhotoFacets]) -> None:
        self._ordering._facet_values = value

    @property
    def _event_values(self) -> dict[Path, tuple[EventRef, ...]]:
        return self._ordering._event_values

    @_event_values.setter
    def _event_values(self, value: dict[Path, tuple[EventRef, ...]]) -> None:
        self._ordering._event_values = value

    @property
    def _group_facet(self) -> str | None:
        return self._ordering._group_facet

    @_group_facet.setter
    def _group_facet(self, value: str | None) -> None:
        self._ordering._group_facet = value

    @property
    def _sort_facet(self) -> str:
        return self._ordering._sort_facet

    @_sort_facet.setter
    def _sort_facet(self, value: str) -> None:
        self._ordering._sort_facet = value

    @property
    def _sort_descending(self) -> bool:
        return self._ordering._sort_descending

    @_sort_descending.setter
    def _sort_descending(self, value: bool) -> None:
        self._ordering._sort_descending = value

    @property
    def _place_resolver(self) -> LazyPlaceResolver | None:
        return self._ordering._place_resolver

    @_place_resolver.setter
    def _place_resolver(self, value: LazyPlaceResolver | None) -> None:
        self._ordering._place_resolver = value

    @property
    def _reorder_fingerprint(self) -> tuple[object, ...] | None:
        return self._ordering._reorder_fingerprint

    @_reorder_fingerprint.setter
    def _reorder_fingerprint(self, value: tuple[object, ...] | None) -> None:
        self._ordering._reorder_fingerprint = value

    @property
    def _reorder_values(self) -> dict[Path, PhotoFacets] | None:
        return self._ordering._reorder_values

    @_reorder_values.setter
    def _reorder_values(self, value: dict[Path, PhotoFacets] | None) -> None:
        self._ordering._reorder_values = value

    @property
    def _reorder_events(self) -> dict[Path, tuple[EventRef, ...]] | None:
        return self._ordering._reorder_events

    @_reorder_events.setter
    def _reorder_events(self, value: dict[Path, tuple[EventRef, ...]] | None) -> None:
        self._ordering._reorder_events = value

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
            if facet.id == FACET_EVENT:
                # 紧接着「事件」摆三类的字段子菜单：它们都是「照片属于哪次事件」的延伸。
                self._add_event_field_menus(group_menu)

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

    def _add_event_field_menus(self, group_menu: QMenu) -> None:
        """「分组 → 旅行 / 演出 / 影片」，每一层右侧是这一类事件的字段（ADR 0043 决策 7）。

        清单来自 :func:`groupable_event_fields`：公共的「开始日期」在前，类型独有的字段在后。
        挑一个就是把这一层按那一栏归拢（「按旅行·目的地」）。
        """
        for kind in EVENT_KINDS:
            submenu = group_menu.addMenu(kind.label)
            submenu.setToolTipsVisible(True)
            for declared in groupable_event_fields(kind):
                facet = event_field_facet(kind.id, declared.id)
                if facet is None:
                    continue
                self._group_actions[facet.id] = self._order_choice(
                    submenu, declared.label, facet.id, self.set_group_facet
                )

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
        """exiftool 现在能不能用。

        **只问 :meth:`Library.exiftool_available`**
        （:class:`~gallery.core.library.facade.Library`）：不要在界面层自己造一个
        ``ExifToolReader`` 去探 —— 同一个「装没装」的问题两套问法会有不一致的答案。
        库还没打开时一律当作「能用」，真打开库之后这条会被重新问到。"""
        library = self._library
        return True if library is None else library.exiftool_available()

    def set_group_facet(self, facet_id: str | None) -> None:
        """切换分组字段（菜单用）。"""
        if facet_id is not None and group_facet(facet_id) is None:
            return  # 非法 id：会话文件里的旧值也可能走到这里，静默忽略
        if facet_id == self._group_facet:
            self._sync_ui()
            return
        self._group_facet = facet_id
        self._remember_ordering()
        # 换分组字段等于换了一组组卡片，原来进入的那个组已经不存在了。
        # 栈：弹到没有分组帧为止（分组只可能压一层，所以至多弹一帧）。
        self._pop_to(lambda stack: self._group_view is None)
        self._reload_level()

    def set_sort_facet(self, facet_id: str | None) -> None:
        """切换排序字段（菜单用）。组内排序也跟着换 —— 这正是它的用处。"""
        if facet_id is None or sort_facet(facet_id) is None:
            return  # 没有「不排序」这个选择；非法 id 只可能来自旧会话，静默忽略
        if facet_id == self._sort_facet:
            self._sync_ui()
            return
        self._sort_facet = facet_id
        self._remember_ordering()
        self._reload_level()

    def set_sort_descending(self, descending: bool) -> None:
        """切换正序 / 逆序。只重排当前这一层，不必重新读元数据。"""
        if descending == self._sort_descending:
            return
        self._sort_descending = descending
        self._remember_ordering()
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

    def _level_shows_group_cards(self) -> bool:
        """这一层的行是不是**组卡片** —— 于是「往里插一行照片」在这里根本不成立。

        照片只存在于 ``GroupRef.photos`` 里，模型的行不是这一层的照片列表，所以派生视图的
        变化只能整层重算（ADR 0013 决策 3 的那条例外）。
        """
        return (
            self._group_view is None
            and self._browse_mode != BROWSE_MODE_CALENDAR
            and self._current_group_facet() is not None
        )

    def _needs_facet_read(self) -> bool:
        """当前的选择是否必须真去读 EXIF 才能算出顺序 —— **或者才能算出筛选结果**。

        筛选那一半是 ADR 0040 决策 3：``#相机 Canon`` 要的是 ``facets.camera``，而 facet 表
        只有读过 EXIF 的照片才有行；不把这一半算进来，筛选就等于拿一张空表去查。
        **事件分组也要走这个后台任务**（ADR 0043 决策 7）：它要的是「照片各属于哪些事件」，
        而 :func:`needs_metadata` 不为它开口子。"""
        group = None if self._group_view is not None else self._current_group_facet()
        if needs_metadata((group, sort_facet(self._sort_facet))):
            return True
        if group is not None and is_event_facet(group.id):
            return True
        return self._conditions_need_metadata()

    def _conditions_need_metadata(self) -> bool:
        """生效中的条件里有没有一条必须真读 EXIF 才算得出来（只在筛选作用域下算数）。

        整库检索不在这里算：它自己有一道「索引没建全就挂起」的检查（:meth:`_start_search`），
        而筛选这一层是照常枚举出来的，facet 读取就挂在同一条链上（见 :meth:`_start_facet_read`）。
        """
        return self._is_filtering() and self._filters.needs_metadata()

    def _facet_read_purpose(self) -> str:
        """那句「用于…」的话。**说清这一次读是为了什么** —— 读元数据有几件不同的理由，
        说错一件用户就不知道进度条在忙什么。
        """
        if self._conditions_need_metadata():
            return "筛选"
        return "分组" if self._group_facet else "排序"

    def _groups_by_event(self) -> bool:
        """当前是不是按事件那一族分组（事件本身，或某一类事件的某个字段）。

        进了某个组之后不算 —— 那一层已经是展开的那一批照片。
        """
        if self._group_view is not None:
            return False
        facet = self._current_group_facet()
        return facet is not None and is_event_facet(facet.id)

    # ---- 进入与退出分组 ------------------------------------------------------

    def enter_group(self, group: GroupRef, *, focus: EntryKey | None = None) -> None:
        """进入一个分组（双击组卡片）。

        分组不是磁盘上的目录，因此不走 ``enter_folder``：它记不住路径，退出时直接回到
        进来之前的那一层。

        :param focus: 到了这一层选中哪一格；用户点进来的一律谁也不选。"""
        self._push_frame(GroupFrame(group))
        self._reload_level(focus=focus)

    def _leave_group(self) -> None:
        group = self._group_view
        if group is None:
            return
        self._pop_frame()
        # 退回来的那一层选中这张组卡片：分组不在磁盘上，退出时回不到「上一层目录」。
        self._reload_level(focus=entry_key(group))

    # ---- 读取本层元数据并重排 ------------------------------------------------

    def _start_facet_read(self) -> None:
        """按当前选择给这一层补齐元数据，然后重排。

        只有真的要读 EXIF 的字段才会走到这里（按文件名排序是纯算的）；读过一次的结果落进索引，第二次
        换分组字段几乎立刻完成。**筛选的条件要元数据时也走到这里**（:meth:`_needs_facet_read`）：facet
        读取会**写**索引，而筛选查询要**读**索引 —— 顺序反了就是拿一张空表去筛。
        """
        library = self._library
        if library is None or not self._needs_facet_read():
            self._order_now()
            return
        # 暂存优先：刚扫完的这一层还在暂存区里，模型上一条也没有。
        staged = self._staging.peek()
        entries = staged if staged is not None else self._model.entries()
        refs = [entry for entry in entries if isinstance(entry, PhotoRef)]
        if not refs:
            self._order_now()
            return
        generation = self._generation
        resolver = self._place_lookup()

        # guard: allow-long-comment（整层查一次（实测三万张 234 ms），换分组与换排序都走）
        # **查索引这一步也搬到后台。** 它是整层查一次（实测三万张 234 ms），而这一段在换分组
        # 字段、换排序、**以及监听器每次触发重排**时都要走一遍；``FacetIndex.lookup`` 是纯读
        # 且在锁内（见 :mod:`gallery.core.library.index`），在工作线程上调是同一件事。
        def work(context: TaskContext) -> object:
            events = _events_of(library, refs) if self._groups_by_event() else {}
            cached = library.cached_facets(refs)
            if len(cached) == len(refs):
                # 「索引里全都有」——**不报进度**（见下面那段），直接用这份。
                return _LevelFacets(values=cached, events=events)
            # 缺东西才起横幅：起后台线程、报「正在读取 N 张照片的元数据」都是在说一件
            # 没发生的事，用户没法分辨是又在干活还是软件出了毛病。
            context.progress(_FACET_PROGRESS.format(len(refs), self._facet_read_purpose()))
            return _LevelFacets(
                values=library.photo_facets(
                    refs,
                    should_cancel=context.is_cancelled,
                    on_progress=lambda done, total: context.progress((done, total)),
                    status=lambda text: context.progress(text),
                    resolver=resolver,
                ),
                events=events,
            )

        self._start_job(
            "_facet_job",
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
        self._banner_text.progress.show(text, defer=False)

    def _clear_progress_notice(self) -> None:
        """收掉「正在读取元数据…」那条 —— **只在自己还占着的时候收**（见 ``NoticeSlot.clear``）。"""
        self._banner_text.progress.clear()

    def _update_level_notice(self) -> None:
        """这一层什么都没有时说一声；有了内容就把这句收回去。

        **判据是「网格上有没有条目」，不是「有没有相册 / 照片」**：分组会把照片换成一张张
        组卡片，只数相册和照片会把一屏组卡片误判成空。**日历那一页不另说一句**：翻到没有
        照片的月份是常事，横幅冒出来等于把「翻过去了」说成「出问题了」。
        """
        if self._library is None:
            return
        if not self._banner_text.may_show_progress:
            # 扫描出过问题时横幅归它：那句「哪些内容没读到」更该说，这里也不许把它收掉。
            return
        if self._model.rowCount():
            if self._banner_text.level_notice:
                # 有了内容就把那句收回去 —— **只在自己还占着的时候收**，否则会把别人的话收掉。
                self._banner_text.level_notice = False
                self._banner.hide()
            return
        if self._is_filtering():
            # **说的是「这一层里没有」而不是「这些条件下没有」**（ADR 0040 决策 4）：整库检索
            # 的那句话在这个视图里是骗人的 —— 用户看到的正是这一层，而整库里明明有照片。
            self._say_level_is_empty("当前这一层里没有符合这些条件的照片。")
        elif self._is_library_search():
            # 检索没命中时**不要说那句格式清单**：它与此刻无关，用户会以为文件格式不对。
            self._say_level_is_empty("这些筛选条件下没有照片。")
        elif self._group_view is not None:
            self._say_level_is_empty("这个分组里没有照片了。")
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._say_level_is_empty("这个文件夹里既没有子文件夹，也没有可显示的照片。")
        else:
            self._say_level_is_empty(
                "没有找到可显示的照片。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP。"
            )

    def _say_level_is_empty(self, text: str) -> None:
        """空层那一句**经轴去说** —— 于是「这一句是我们说的」这个记号只有一处会置（ADR 0054）。"""
        self._banner_text.say_level_is_empty(text)

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
        self._finish_job("_facet_job")
        ready = payload if isinstance(payload, _LevelFacets) else _LevelFacets(values={})
        self._facet_values = ready.values
        # **每回都重新赋值**（哪怕是空的）：从「按事件分组」换到别的字段时，那份映射就该被丢掉，
        # 否则它一直挂在内存里，而重排那道检查会以为输入没变。
        self._event_values = ready.events
        self._clear_progress_notice()
        self._order_now()
        # 这一层读完了，才轮到整库那两遍（见 _maybe_start_place_pass 里的理由）。
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _on_facets_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._finish_job("_facet_job")
        self._show_banner(f"读取元数据失败，按默认顺序显示：{message}")
        self._order_now()
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _order_now(self) -> None:
        """这一层显示到界面之前的**唯一**一个漏斗：先按条件收窄（只在筛选作用域下），再排序。

        **所有出口都必须走它**，不能直接调 :meth:`_apply_ordering` —— 那样每个调用点都要
        自己判断一遍「现在是不是在筛选」，漏一处就出现「这一层没被筛」或「没被筛的东西被
        筛掉了」。相反，:meth:`SearchFlowMixin._start_level_filter` 的两端调的是
        :meth:`_apply_ordering` 而不是这里 —— 漏斗走完就该排序，排序完再回到漏斗就是递归。"""
        self._start_level_filter()

    def _apply_ordering(self) -> None:
        """把当前这一层按分组 / 排序重排一次，然后一次性摆到网格上。

        **大图页上不重排**（重排会让那一行的内容换掉，而标题栏还写着刚才那张的名字，
        ADR 0025 决策 4），**日历那一页要重排**（它的模型里装着整库，模型空着的话从日历点
        照片没反应）。**日历在这里喂，不在扫描结束时喂**：排序要读元数据时重排落在那个
        后台任务回来之后，而「扫描完 → 喂日历」会在模型还空着时读出零张照片。"""
        library = self._library
        if library is None:
            return
        in_grid = self._on_grid_page()
        try:
            # 大图页上不重排（ADR 0025 决策 4）—— 但日历那一页仍然要喂。
            if not self._in_detail_page():
                self._reorder_level(library)
        finally:
            # 顺序定下来了，这才有资格回答「这一层是不是空的」。
            self._update_level_notice()
            if in_grid:
                # 也才找得到「退上去时该停住的那一格」：组卡片本身就是重排出来的。
                self._restore_focus()
                # 分组视图里来了新照片时，这里是重新分组之后回到原处的地方（ADR 0013）。
                self._maybe_reenter_group()
            # 暂存区到这里才清空：暂存的变化从这一刻起才能安全地落到网格上。
            self._flush_pending_watch()
            # 模型此刻摆着的就是这一层，**这时**喂日历才对得上（见上面那段）。
            self._feed_calendar()

    def _reorder_level(self, library: Library) -> None:
        # 暂存的优先：那一层还没显示到界面，顺序就是这里算出来的。
        staged = self._staging.take()
        entries = staged if staged is not None else list(self._model.entries())
        albums = [entry for entry in entries if isinstance(entry, FolderRef)]
        photos = [entry for entry in entries if isinstance(entry, PhotoRef)]
        # 相册卡片永远排在前面：先看文件夹、再看按元数据归拢出来的组，是既有心智。
        order = self._current_order()
        # 相册卡片**只按名称排，方向跟着「逆序」**（ADR 0049）：它们不进 `sort_facet`（文件夹没有
        # 拍摄时间与相机），而原先连「逆序」都不看，于是「照片翻了、相册没翻」。
        albums.sort(key=lambda folder: folder.name.casefold(), reverse=order.descending)
        # 分组只在这一层摆卡片时才有（判据只有 :meth:`_level_shows_group_cards` 一处）：
        # 已经在分组里面就再分不出来，而日历那一页自己按日期归拢、要装整库照片。
        group = self._current_group_facet() if self._level_shows_group_cards() else None
        values = self._facet_values
        events = self._event_values

        # guard: allow-long-comment（检查排在排序之后就白付一次整层排序；判据取输入）
        # **先判「会不会变」，再排序**（这道检查排在排序之后就等于白付一次整层排序）。判据取
        # **输入**而不是输出（输出比的是刚算出来的 ``ordered``），比内容而不是比身份（``photos``
        # / ``albums`` 是现造的两个列表，指针比对永不命中，而模型里存的就是**同一批**
        # ``PhotoRef``，逐个 ``==`` 走的是身份捷径）。
        # 判据里刻意有的几样：``library.root`` 也要记（``sort_photos`` / ``build_groups`` 靠它算
        # 相对路径那类字段的排序值）；分组字段只记 ``id``；**不记** ``self._group_view``
        # （它在非空时 ``group`` 必为 ``None``，两个组视图来回换不影响输出）。
        fingerprint = (
            group.id if group is not None else None,
            self._sort_facet,
            self._sort_descending,
            library.root,
        )
        # ``values`` 单独用 ``is`` 判并**持有强引用**（存在 ``self._reorder_values`` 里）—— 记
        # ``id(values)`` 会踩到地址复用：换库后新 dict 落在同一地址上，这道检查就会跳过排序。
        if (
            staged is None
            and fingerprint == self._reorder_fingerprint
            and values is self._reorder_values
            and events is self._reorder_events
            and tuple(albums) == self._reorder_albums
            and tuple(photos) == self._reorder_photos
        ):
            return  # 输入逐字没变，顺序也就逐字没变：连排序都不必算

        if group is not None:
            ordered: list[GridEntry] = [
                *albums,
                *build_groups(
                    photos,
                    facet=group,
                    order=order,
                    root=library.root,
                    values=values,
                    event_values=events,
                ),
            ]
        else:
            ordered = [*albums, *sort_photos(photos, order=order, root=library.root, values=values)]
        self._reorder_fingerprint = fingerprint
        self._reorder_values = values
        # **事件那份也要进这道检查**（ADR 0043 决策 7）：绑定变了一定要重排，否则用户记完照片
        # 看到的是「什么都没发生」。同样是判 ``is`` 且持有强引用，不记 ``id()``。
        self._reorder_events = events
        self._reorder_photos, self._reorder_albums = tuple(photos), tuple(albums)
        if staged is None and ordered == list(self._model.entries()):
            return  # 什么都没暂存、顺序也没变：让整屏重画一次毫无意义
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
