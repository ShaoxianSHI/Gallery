"""把照片「记录为」某个事件 —— 菜单、面板、写库、横幅（ADR 0043）。

**这一层一个用户的文件都不碰**，所以它不住 ``write_ops``（那一个只管会动用户文件的入口，
红线 1 的守护按文件名找它）。它动的是 Gallery 自己的库。

菜单是三级的：「记录为 → 旅行 → 京都五日」。而**三层 QMenu 必须先全建出来**再填内容 ——
``menu_style.apply_style`` 是在菜单建好那一刻一次性递归给所有子菜单下发样式表的，建完再补第三层
的话，那一层会是一张系统默认的白底菜单（与 ``menus.build_lazy_album_picker`` 同一个坑）。清单
的填充一律挂 ``aboutToShow``，与相册清单那一支同一写法。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QDialog, QMenu, QMessageBox

from gallery.core.errors import EventsUnavailableError, GalleryError
from gallery.core.events.kinds import EVENT_KINDS, EventKind, kind_by_id
from gallery.core.events.orphans import OrphanScan
from gallery.core.events.prefill import EventPrefill, prefill_from_photos
from gallery.core.events.store import Event
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.facets import FACET_EVENT
from gallery.ui import banners
from gallery.ui.menu_style import ShortcutColumnMenu
from gallery.ui.panels.event_edit import DELETE_REQUESTED, EventEdit, EventEditDialog
from gallery.ui.write_ops import flattened_entries

logger = logging.getLogger(__name__)

#: 没选中照片时那两条提示。两句都要说清「先选照片」—— 用户多半是先选中再去右键的，
#: 而这一行压根不出现时他连问都问不到。
NOTHING_TO_BIND = "先选中几张照片，再记为某次事件。"
NOTHING_TO_UNBIND = "先选中要从这次事件移出来的照片。"

#: 点了菜单里的某一项要告诉窗口的事：**建一个新的**，还是**记到某一个已有名下**。
#: 「新建」那一项的 ``data`` 是 ``None``，于是两种情形不需要两个回调。
Submitter = Callable[[str, QAction], None]


class EventFlowMixin:
    """:class:`~gallery.ui.main_window.MainWindow` 上与事件有关的那几条。"""

    def _events_usable(self) -> bool:
        """事件库这次用不用得上。配置目录建不出来的库上压根没有事件（ADR 0043 决策 1）。

        **判据只有这一处**：菜单栏那两层在 ``aboutToShow`` 与 ``_update_event_actions`` 两处都要
        置灰，各写一遍就会有一个地方忘了 —— 而那个症状是「菜单摆着，按下去没反应」。
        """
        library = self._library
        return library is not None and library.events_available

    def _edit_event_usable(self) -> bool:
        """「编辑事件」那一层还多一条：**至少有一个事件**。"""
        library = self._library
        return self._events_usable() and library is not None and bool(library.all_events())

    def _update_event_actions(self) -> None:
        """事件那几处的灰度，分两种判据。

        **事件分组卡片上那两行**：只有当光标落在认得的事件卡片上才可点 —— 它只是「当前项背后
        有没有一个事件 id」这一个是非问题，不必去查库。

        **菜单栏那两条**：见 :meth:`_events_usable` —— **不是「有没有选中照片」**，与右键那一支
        不同，那一条是「把这批照片记进去」，这一条是「建一个事件 / 改一个事件」。
        """
        new_menu = getattr(self, "_new_event_menu", None)
        if new_menu is not None:
            new_menu.setEnabled(self._events_usable())
        edit_menu = getattr(self, "_edit_event_menu", None)
        if edit_menu is not None:
            edit_menu.setEnabled(self._edit_event_usable())
        if getattr(self, "_edit_event_action", None) is None:
            return
        usable = self._group_card_event_id() is not None
        self._edit_event_action.setEnabled(usable)
        self._leave_event_action.setEnabled(usable)

    def _ensure_event_actions(self) -> None:
        """建那两个**常驻窗口**的动作（事件分组卡片上的「编辑事件…」与「从这次事件移出」）。

        它们常驻而不是每次现建：要挂到窗口上（否则快捷键不注册），也要能被
        ``_update_action_states`` 按「此刻有没有一个认得的事件」管灰度（与菜单里其余动作同一套）。
        """
        if getattr(self, "_edit_event_action", None) is not None:
            return
        self._edit_event_action = QAction("编辑事件…", self)
        self._edit_event_action.triggered.connect(self._edit_event_of_group_card)
        self.addAction(self._edit_event_action)
        self._leave_event_action = QAction("从这次事件移出", self)
        self._leave_event_action.triggered.connect(self._leave_group_card)
        self.addAction(self._leave_event_action)

    def _group_card_event_id(self) -> int | None:
        """此刻光标所在那张组卡片背后的事件 id。"""
        row = self._grid.current_row()
        return _event_id_of(self._model.entry_at(row))

    def _edit_event_of_group_card(self) -> None:
        event_id = self._group_card_event_id()
        if event_id is not None:
            self.edit_event(event_id)

    def _leave_group_card(self) -> None:
        """把卡片背后的**那批照片**从这个事件里拿出来（不是一个都不动，也不是整个事件删掉）。"""
        library = self._library
        event_id = self._group_card_event_id()
        if library is None or event_id is None:
            return
        event = library.event(event_id)
        if event is None:
            self._show_banner("这个事件已经不在了。")
            return
        photos = [entry.path for entry in self._model.group_at(self._grid.current_row()).photos]  # type: ignore[union-attr]
        if not photos:
            return
        try:
            count = library.unbind_photos(photos, event.id)
        except EventsUnavailableError as exc:
            self._show_banner(exc.message)
            return
        self._after_events_changed()
        self._show_banner(banners.photos_unbound_banner(count, event.display_name))

    # ---- 菜单 --------------------------------------------------------------

    def record_as_candidate(self) -> QMenu | None:
        """「记录为」这一行摆不摆：**要选中照片，而且事件库这次用得上**。

        只读卷上事件库建不出来（ADR 0043 决策 1），那时这一行整个不出现 —— 而不是摆出来按下去
        才报错，用户并没有第三个有意义的选择。
        """
        library = self._library
        if library is None or not library.events_available or not self._selected_paths():
            return None
        return self.record_as_menu()

    def record_as_menu(self) -> QMenu:
        """「记录为」那一个子菜单。**三层先全建出来**，内容等展开时再填。

        **用 ``ShortcutColumnMenu``**：这一层「旅行 / 演出 / ……」与「从这次事件移出」都是
        子菜单行，得跟上一层的箭头一样是自绘的实心三角（理由见 ``owns_submenu_arrows``）。
        """
        menu = ShortcutColumnMenu("记录为")
        menu.setToolTipsVisible(True)
        for kind in EVENT_KINDS:
            menu.addMenu(self._event_picker(kind))
        leaving = self._leaving_events()
        self._event_leave_menu = _lazy_menu(menu, "从这次事件移出", lambda: leaving)
        self._event_leave_menu.triggered.connect(self._on_event_left)
        # **立刻**知道自己该不该摆 —— 摆一个永远点不开的空框只会让人以为坏了。
        self._event_leave_menu.setDisabled(not leaving)
        return menu

    def new_event_menu(self) -> QMenu:
        """菜单栏「编辑 → 新增事件」那一层：按类型列一行（ADR 0043）。

        **建出来的时候是空的**，挂在 ``aboutToShow`` 上填 —— 菜单栏的子菜单是常驻的，
        而事件是随时会变的。与「记录为」那一支同一理由（``menu_style.apply_style`` 在建好那一刻
        一次性递归下发样式表，建完再补子项的话那一项会留在系统默认的白底菜单里）。
        """
        menu = ShortcutColumnMenu("新增事件")
        menu.setToolTipsVisible(True)

        def fill() -> None:
            menu.clear()
            for kind in EVENT_KINDS:
                menu.addAction(kind.label).setData(kind.id)

        menu.aboutToShow.connect(fill)
        menu.triggered.connect(self._on_new_event_kind)
        return menu

    def edit_event_menu(self) -> QMenu:
        """菜单栏「编辑 → 编辑事件」那一层：列已有的那些，点一个直接开那个事件的编辑面板。

        **平铺，不按类型分组** —— 用户想改的就是某一个具体的事件，类型他多半还记得但不影响他
        要找的那一个；而这里只有一种类型（``kinds.EVENT_KINDS`` 目前一条），分组也分不出什么。

        同样挂在 ``aboutToShow`` 上填（理由见 :meth:`new_event_menu`），**零个事件时置灰** ——
        摆一个永远点不开的框只会让人以为软件坏了。
        """
        menu = ShortcutColumnMenu("编辑事件")
        menu.setToolTipsVisible(True)

        def fill() -> None:
            menu.clear()
            library = self._library
            if library is None:
                return
            for event in library.all_events():
                menu.addAction(event.display_name).setData(event.id)
            # 展开那一刻按**同一个判据**再置一次灰（见 :meth:`_events_usable`）：建完第一个事件
            # 之后 ``_update_action_states`` 可能还没被叫到，靠这一句菜单当场就是对的。
            menu.setEnabled(self._edit_event_usable())

        menu.aboutToShow.connect(fill)
        menu.triggered.connect(self._on_edit_event_picked)
        return menu

    def _on_new_event_kind(self, action: QAction) -> None:
        """菜单栏那一条：选了哪个类型就摆那个类型的新建面板。

        **不要求选中照片** —— 与右键菜单的「记录为」不同，那一条是「把这批照片记进去」，
        这一条是「建一个事件」，选中只决定要不要顺带绑上。
        """
        kind = kind_by_id(str(action.data()))
        if kind is not None:
            self.open_new_event(kind.id)

    def _on_edit_event_picked(self, action: QAction) -> None:
        """菜单栏那一条：``data`` 就是那个事件的 id。"""
        try:
            event_id = int(action.data())  # type: ignore[arg-type]
        except (TypeError, ValueError):  # pragma: no cover - 菜单里摆的都是整数
            return
        self.edit_event(event_id)

    def _event_picker(self, kind: EventKind) -> QMenu:
        """一类事件的那一层：「新建」永远在最上面，下面一条分隔线，再列已有的那些。

        **连的是 ``QMenu.triggered`` 而不是每个 ``QAction.trigged``**：前者发的是**那个动作**
        （要从它身上取 ``data()``），后者发的是一个 ``bool``。按类型连一次而不是按动作连，
        清单每次重填也不用重连。
        """

        def fill() -> None:
            submenu.clear()
            create = submenu.addAction(f"新建{kind.label}…")
            create.setData(None)
            library = self._library
            if library is None:
                return
            existing = library.events_by_kind(kind.id)
            if not existing:
                return
            submenu.addSeparator()
            for event in existing:
                submenu.addAction(event.display_name).setData(event.id)

        submenu = QMenu(kind.label)
        submenu.setToolTipsVisible(True)
        submenu.aboutToShow.connect(fill)
        submenu.triggered.connect(partial(self._on_event_chosen, kind.id))
        return submenu

    def _leaving_events(self) -> list[tuple[str, int]]:
        """当前选中的照片属于哪些事件（「从这次事件移出」那一层列的就是它）。

        **按事件平铺，不再按类型分组** —— 要离开哪一次旅行，用户自己知道是哪一次。
        """
        library = self._library
        if library is None:
            return []
        paths = self._selected_paths()
        if not paths:
            return []
        found = library.events_for_photos(paths)
        seen: dict[int, str] = {}
        for refs in found.values():
            for ref in refs:
                seen.setdefault(ref.id, ref.title)
        return [(title, event_id) for event_id, title in sorted(seen.items(), reverse=True)]

    # ---- 动作 --------------------------------------------------------------

    def _on_event_chosen(self, kind_id: str, action: QAction) -> None:
        """点了哪一项。``QMenu.triggered`` 带来的就是那个动作；``data`` 是 ``None`` 即「新建」。"""
        data = action.data()
        if data is None:
            self.open_new_event(kind_id)
            return
        self.record_selected_as(kind_id, int(data))

    def _on_event_left(self, action: QAction) -> None:
        """「从这次事件移出 → 京都五日」。"""
        library = self._library
        if library is None:
            return
        event = library.event(int(action.data()))
        photos = self._selected_paths()
        if event is None:
            self._show_banner("这个事件已经不在了。")
            return
        if not photos:
            self._show_banner(NOTHING_TO_UNBIND)
            return
        try:
            count = library.unbind_photos(photos, event.id)
        except EventsUnavailableError as exc:
            self._show_banner(exc.message)
            return
        self._after_events_changed()
        self._show_banner(banners.photos_unbound_banner(count, event.display_name))

    def open_new_event(self, kind_id: str) -> None:
        """摆出面板，让用户填一个新事件，填完把当前选中的照片记进去。"""
        kind = kind_by_id(kind_id)
        if kind is None:  # pragma: no cover - 菜单里出现的类型都是认得的
            return
        self._apply_event_edit(kind, None)

    def edit_event(self, event_id: int) -> None:
        """改一个已有事件（事件分组卡片上的「编辑事件…」走这里）。"""
        library = self._library
        if library is None:
            return
        event = library.event(event_id)
        if event is None:
            self._show_banner("这个事件已经不在了。")
            return
        kind = kind_by_id(event.kind)
        if kind is None:  # pragma: no cover - 认不出的类型压根不会进缓存
            return
        self._apply_event_edit(kind, event)

    def delete_event(self, event_id: int) -> None:
        """删掉一个事件。**要确认框**：废纸篓那支不确认是因为「废纸篓本身就是那层保险，⌘Z 再是
        一层」，而事件两样都没有（ADR 0043 决策 5）。
        """
        library = self._library
        if library is None:
            return
        event = library.event(event_id)
        if event is None:
            self._show_banner("这个事件已经不在了。")
            return
        photos = len(library.events.photos_of_event(event_id))
        extra = f"，连同 {photos} 张照片上的记录" if photos else ""
        answer = QMessageBox.question(
            self,
            "Gallery",
            f"删除「{event.display_name}」{extra}？\n\n⌘Z 可以把它连同那些记录一起恢复回来。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            outcome = library.delete_event(event_id)
        except GalleryError as exc:
            logger.info("删除事件失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._after_events_changed()
        self._show_banner(banners.event_deleted_banner(outcome))
        self._refresh_undo_label()

    def _confirm_orphan_cleanup(self, scan: OrphanScan) -> None:
        """问用户要不要清掉那些孤儿绑定（ADR 0043 决策 9）。

        **一个框分两档**（``banners.orphan_dialog`` 里说清了为什么），三个按钮：

        - 「清掉这些」：只清 ``missing`` 那一档 —— 目录可读、清单里没有它，是**结论**
        - 「连相册不存在的也清掉」：连 ``vanished`` 一起，**而那一档混着「那个盘没挂上」**
        - 「先不清」：默认按下的那一个。删用户自己填过的数据，默认必须是保守的那一个。

        **默认按钮是 Cancel**，与 :meth:`delete_event` 同一取向。**正文不许提 ⌘Z** ——
        这里没有撤销，而 ``delete_event`` 那句「⌘Z 可以…」在这里是反的。
        """
        library = self._library
        if library is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(banners.orphan_dialog_title(scan))
        box.setText(banners.orphan_dialog(scan))
        clear_all = box.addButton("先不清", QMessageBox.ButtonRole.RejectRole)
        keep_vanished: QAbstractButton | None = None
        if scan.missing:
            clear_missing = box.addButton("清掉这些", QMessageBox.ButtonRole.AcceptRole)
            if scan.vanished:
                keep_vanished = box.addButton(
                    "连相册不存在的也清掉", QMessageBox.ButtonRole.DestructiveRole
                )
        else:
            # 没有 ``missing`` 可清时，那一个按钮会变成一句空话 —— 只给两档里的两个选择。
            clear_missing = None
            keep_vanished = box.addButton("连这些一起清掉", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(clear_all)
        box.exec()
        if box.clickedButton() is clear_missing:
            include_vanished = False
        elif box.clickedButton() is keep_vanished:
            include_vanished = True
        else:
            return
        removed = library.forget_orphan_bindings(scan, include_vanished=include_vanished)
        self._after_events_changed()
        self._show_banner(banners.orphans_forgotten_banner(removed))

    def record_selected_as(self, kind_id: str, event_id: int) -> None:
        """把当前选中的照片记进这个事件。"""
        library = self._library
        if library is None:
            return
        event = library.event(event_id)
        photos = self._selected_paths()
        if event is None:
            self._show_banner("这个事件已经不在了。")
            return
        if not photos:
            self._show_banner(NOTHING_TO_BIND)
            return
        try:
            count = library.bind_photos(photos, event_id)
        except EventsUnavailableError as exc:
            self._show_banner(exc.message)
            return
        self._after_events_changed()
        self._show_banner(banners.photos_bound_banner(count, event.display_name))

    # ---- 面板 --------------------------------------------------------------

    def _apply_event_edit(self, kind: EventKind, event: Event | None) -> None:
        """摆面板 → 拿到用户填的那一份 → 写库。**一次事件编辑只写一次**（与元数据编辑器同一条）。"""
        library = self._library
        if library is None:
            return
        # 摊平只做一次：下面既要路径（绑定用）也要 ``PhotoRef``
        # （读索引要它的 size / mtime 才算命中）。
        entries = flattened_entries(self._selected_entries())
        photos = [entry.path for entry in entries]
        dialog = EventEditDialog(
            self,
            kind,
            event=event,
            selected=len(photos),
            prefill=None if event is not None else self._event_prefill(entries),
        )
        result = dialog.exec()
        if result == DELETE_REQUESTED:
            # 「删除事件…」按钮只在编辑已有事件时才摆（见 EventEditDialog._build_buttons），
            # 而这里这一次确认框要问「连同 N 张照片上的记录」—— 所以问的是那个事件本身。
            if event is not None:
                self.delete_event(event.id)
            return
        if result != QDialog.DialogCode.Accepted:
            return
        try:
            if event is None:
                outcome = library.create_event(kind.id, dialog.edit().draft)
                self._bind_the_new_one(dialog.edit(), outcome.event, photos)
                self._show_banner(banners.event_created_banner(outcome))
            else:
                outcome = library.update_event(event.id, dialog.edit().draft)
                self._show_banner(banners.event_updated_banner(outcome))
        except GalleryError as exc:
            logger.info("写事件失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._after_events_changed()
        self._refresh_undo_label()
        self._refresh_undo_label()

    def _event_prefill(self, entries: Sequence[GridEntry]) -> EventPrefill:
        """按**选中的照片**推出目的地与起止日期。

        **只读索引里已经有的那几行**（``cached_facets``），所以摆面板这一下不同步去读 EXIF
        —— 当前这一层的元数据早就读过了（``ordering._start_facet_read``），没读到的那几张
        就当没有：推不出来的那一栏留空，用户自己填。
        """
        library = self._library
        refs = [entry for entry in entries if isinstance(entry, PhotoRef)]
        if library is None or not refs:
            return EventPrefill()
        try:
            cached = library.cached_facets(refs)
        except GalleryError as exc:
            logger.info("读索引里的元数据失败：%s", exc.detail or exc.message)
            return EventPrefill()
        return prefill_from_photos(cached.values())

    def _bind_the_new_one(self, edit: EventEdit, event: Event, selected: Sequence[Path]) -> None:
        """新建之后把照片记进去：选中的那些，加上（勾着的）按日期范围挑的那些。

        **面板不碰任何一张照片**，所以这一步在这里。日期范围那一条查的是**整库** —— 旅行的照片
        不只在当前这一层，只在这一层里挑的话用户会以为自己的照片丢了（ADR 0043 决策 4）。
        """
        library = self._library
        if library is None or not edit.include_range:
            photos = list(selected)
        else:
            already = set(selected)
            photos = [
                *selected,
                *(
                    path
                    for path in self._photos_in_range(
                        edit.first_day, edit.last_day or edit.first_day
                    )
                    if path not in already
                ),
            ]
        if photos:
            library.bind_photos(photos, event.id)

    def _photos_in_range(self, first_day: str, last_day: str) -> list[Path]:
        """这几天里拍的那些照片。**查不成就说清是哪一件**，不静默什么都不做。"""
        library = self._library
        if library is None:
            return []
        try:
            return [ref.path for ref in library.photos_in_days(first_day, last_day)]
        except GalleryError as exc:
            logger.info("按日期范围挑照片失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return []

    # ---- 刷新 --------------------------------------------------------------

    def _after_events_changed(self) -> None:
        """事件变了之后要跟着动的那几样。

        **按事件分组时要重新取一次那份映射**，然后再重排。原因在重排那道闸：它判的是
        「对象换了没有」，而绑定变了之后手上那一份**仍是旧的那个对象** —— 它不会因为库里的行
        变了就变。于是直接 ``_order_now()`` 会被闸挡掉，用户看到的是「记完什么都没发生」
        （ADR 0043 决策 7）。

        顺带一件不显然的：**此刻模型里摆的是组卡片而不是照片**，而重排是从模型里取照片的 ——
        不喂一份进去就算出「一个组都没有」（症状是整屏组卡片瞬间消失）。上一次分组时的那批照片
        还留着（``_reorder_photos``），拿来当输入正是它当初的用途。

        **菜单栏那两层的灰度也在这里刷**：它们按「事件库用不用得上」与「有没有事件」置灰
        （见 :meth:`_events_usable`），而事件一变就该跟着变 —— 走 ``_update_action_states``
        覆盖的那些路（右键「记录为」那几条）都会刷，建 / 改 / 删走的面板那几条不会，
        所以**放在这里**：所有事件的改动都经过这个方法，不必每处记得补一句。
        """
        self._update_event_actions()
        if not self._groups_by_event():
            return
        if not self._reorder_photos:
            self._reload_level()
            return
        self._staged = list(self._reorder_photos)
        self._start_facet_read()


def _event_id_of(entry: GridEntry | None) -> int | None:
    """这一格背后的事件 id —— ``None`` 表示它不是一张事件分组卡片。

    组键是 ``起始日期#事件 id``（见 ``core.metadata.facets._event_group``），所以 id 从 ``#``
    后面取。**认不出就返回 ``None``**：别的分组卡片（年份、相机……）于是照旧只看到
    「复制分组条件」那一行。
    """
    if not isinstance(entry, GroupRef) or entry.facet != FACET_EVENT:
        return None
    _, separator, tail = entry.key.rpartition("#")
    return int(tail) if separator and tail.isdigit() else None


def _lazy_menu(parent: QMenu, title: str, supplier: Callable[[], list[tuple[str, int]]]) -> QMenu:
    """一个**先建出来、内容展开时再填**的子菜单（与相册清单那一支同一写法）。"""
    submenu = QMenu(title)
    submenu.setToolTipsVisible(True)
    submenu.aboutToShow.connect(partial(_fill_lazy, submenu, supplier))
    parent.addMenu(submenu)
    return submenu


def _fill_lazy(submenu: QMenu, supplier: Callable[[], list[tuple[str, int]]]) -> None:
    submenu.clear()
    for label, value in supplier():
        action = submenu.addAction(label)
        action.setData(value)


__all__ = ["EventFlowMixin"]
