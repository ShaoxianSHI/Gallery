"""右键菜单（ADR 0014）与挂在菜单上的那几条只读命令。

网格与大图页**共用同一套流程**：视图只报「按在了哪」，菜单在这一侧拼；两个 ``*_context_menu``
只拼不弹，测试因此可以直接调它看内容。

「粘到哪里」由**落点**决定而不是由菜单决定，递给菜单的是**同一枚动作**（菜单栏那个 ⌘V），所以
不可能出现「写着 ⌘V、按下去却是另一个地方」的分裂；菜单里「有什么」在 :mod:`gallery.ui.menus`。
"""

from __future__ import annotations

import logging
from functools import partial

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.exif import MetadataReport, read_metadata
from gallery.core.metadata.facets import group_facet
from gallery.ui import desktop, menu_style
from gallery.ui.menus import MenuActions, TargetKind, build_menu
from gallery.ui.tasks import TaskContext
from gallery.ui.write_ops import NOTHING_SELECTED

logger = logging.getLogger(__name__)


def _context_kind(entry: GridEntry) -> TargetKind:
    """这一格是照片、相册还是分组 —— 右键菜单据此拼不同的一份（ADR 0014）。"""
    if isinstance(entry, GroupRef):
        return TargetKind.GROUP
    if isinstance(entry, FolderRef):
        return TargetKind.ALBUM
    return TargetKind.PHOTO


class ContextMenuFlowMixin:
    """五张右键菜单怎么拼，以及菜单上那几条只读命令（拷贝、访达、元数据、分组条件）。"""

    # ---- 右键菜单（见 ADR 0014）----------------------------------------------
    def _show_context_menu(self, row: int) -> None:
        """网格上的一次右键：拼菜单、弹出来、用完即弃。

        粘到哪里不看菜单本身，看**选区**：右键那一格会先被选中，所以点那一行与按 ⌘V
        落在同一个目录上，不需要谁替菜单记着「这一下按的是哪个相册」。
        """
        menu = self.context_menu_for(row)
        if menu is None:
            return
        menu.exec(self._grid.cursor().pos())
        menu.deleteLater()

    def context_menu_for(self, row: int) -> QMenu | None:
        """这一下该弹什么菜单。**只拼不弹**，测试因此可以直接调它看内容。

        拼之前先刷新一次动作的可用性：判据都依赖**此刻**的选区与层级，拿几十毫秒前的状态拼
        菜单，用户会看到一个与界面不一致的灰度。``None`` 表示这次右键不该有菜单。
        """
        if self._stack.currentWidget() is not self._grid_page:
            return None
        entry = self._model.entry_at(row) if row >= 0 else None
        if row >= 0 and entry is None:
            # 扫描还在流式上屏时，模型可能已经往前挪过：宁可不弹，也不弹一份错的。
            return None
        self._update_action_states()
        kind = TargetKind.BLANK if entry is None else _context_kind(entry)
        multiple = len(self._grid.selected_rows()) > 1
        menu = build_menu(
            self._menu_actions(self._context_paste(kind, entry, multiple=multiple)),
            kind,
            multiple=multiple,
        )
        return self._dressed_menu(menu)

    def _show_detail_context_menu(self, position: QPoint) -> None:
        """大图页上的一次右键：拼菜单、弹出来、用完即弃（与网格同一套流程）。

        位置是 ``DetailView`` 报的窗口坐标 —— 视图只回答「按在了哪」，菜单归这一侧拼
        （ADR 0021）。
        """
        menu = self.detail_context_menu()
        if menu is None:
            return
        menu.exec(position)
        menu.deleteLater()

    def detail_context_menu(self) -> QMenu | None:
        """大图页该弹什么菜单。**只拼不弹**，与 :meth:`context_menu_for` 一个规矩。

        判据只有一条：现在在大图页，且**确实在看一张照片** —— 加载失败、画面还空着的时候
        右键一下也不该有菜单，里面每一条命令的对象都还不存在。
        """
        if self._stack.currentWidget() is not self._detail_page:
            return None
        if self._model.photo_at(self._current_row) is None:
            return None
        # 与网格那一侧一样先刷新一次可用性：菜单里的 QAction 是共享的，灰度得是「此刻」的。
        self._update_action_states()
        # 大图页没有「当前这一层」可粘，因此这一份动作包里压根不带「粘贴」（ADR 0018 决策 7）。
        return self._dressed_menu(build_menu(self._menu_actions(None), TargetKind.DETAIL))

    def _dressed_menu(self, menu: QMenu) -> QMenu:
        """刚拼好的菜单在下发之前要做的那几件事：底色、圆角行距、快捷键那一列。"""
        # 菜单的底色取自调色板的 ``Window`` 角色（默认来自系统），而菜单是这一下才建的、
        # 此后不会再下发主题，所以直接照抄窗口那份调色板。
        menu.setPalette(self.palette())
        # 圆角与行距只能用样式表改，而样式表一上底色就不由系统画了，必须显式写进样式表。
        menu_style.apply_style(menu)
        # 快捷键那一列由界面自己画（按字母 / 修饰符分列），得先跟 Qt 约好别重复画。
        menu_style.prepare(menu)
        return menu

    def _context_paste(
        self, kind: TargetKind, entry: GridEntry | None, *, multiple: bool
    ) -> QAction | None:
        """这一下右键该递进去的那一条「粘贴」——**粘到哪里由落点决定**（ADR 0018 决策 7）。

        | 落点 | 菜单里那一条 |
        |------|--------------|
        | **空白处** | 粘进**当前这一层** |
        | **单个相册** | 粘进**这个相册内部**，不是它所在的这一层 |
        | 照片 / 多选照片 / 分组卡片 / 多个相册 | **不出现** |
        | 大图页 | 不出现（那一页连「当前这一层」都没有） |

        前两处递进去的**是同一枚动作**（:attr:`_paste_action`，菜单栏那个 ⌘V），因此「点它」与
        「按 ⌘V」是同一件事；粘到哪里一律由 :meth:`_paste_target` 按选区算。
        """
        if kind is TargetKind.ALBUM and not multiple and isinstance(entry, FolderRef):
            # 剪贴板里没有文件则整条不出现（ADR 0018 决策 6）。
            if not desktop.read_files_from_clipboard():
                return None
            return self._paste_action
        if kind is TargetKind.BLANK and self._can_paste():
            return self._paste_action
        return None

    def _menu_actions(self, paste: QAction | None) -> MenuActions:
        """把菜单要用的动作收成一包（``ui.menus`` 只认这个，不认窗口）。"""
        # 相册清单**在用户展开子菜单时才列**：列一次要走一遍整棵目录树（3000 个相册约
        # 120 ms，全在主线程上）。「一个相册都没有就不建这条菜单」（ADR 0014 决策 4）问的是
        # ``has_albums`` —— 它找到一个就停，所以有相册时只花一次 scandir。
        copy_to_album = move_to_album = None
        if self._library is not None and self._library.has_albums():
            copy_to_album = self._album_picker("复制到相册", self._album_targets)
            move_to_album = self._album_picker("移动到相册", self._album_targets)
            copy_to_album.triggered.connect(self._on_album_chosen_for_copy)
            move_to_album.triggered.connect(self._on_album_chosen_for_move)
        return MenuActions(
            open_entry=self._open_entry_action,
            close_entry=self._close_entry_action,
            open_default=self._open_default_action,
            reveal=self._reveal_action,
            copy=self._copy_action,
            copy_path=self._copy_path_action,
            # 「新建相册」只在空白处那张菜单里（ADR 0022 决策 1），建不了时不出现。
            new_album=self._new_album_action if self._can_create_album() else None,
            copy_to_album=copy_to_album,
            move_to_album=move_to_album,
            # 「粘贴」按落点递进来：此刻粘不了就传 ``None``，而不是摆一条永远点不动的。
            paste=paste,
            rename=self._rename_action,
            trash=self._trash_action,
            metadata=self._metadata_action,
            copy_metadata=self._copy_metadata_action,
            copy_group=self._copy_group_action,
            select_all=self._select_all_action,
            up=self._up_action,
            group_choices=tuple(self._group_actions.values()),
            sort_choices=tuple(self._sort_actions.values()),
            descending=self._descending_action,
            size_choices=tuple(self._size_actions),
            # 封面那两条（ADR 0028）没有对象就是 ``None``，不出现这一行；
            # 对象是谁由 ``WriteOpsMixin`` 回答。
            set_cover=self._set_cover_action if self.cover_candidate() is not None else None,
            clear_cover=self._clear_cover_action if self.covered_album() is not None else None,
            # 「编辑元数据」没有对象、或者没装 exiftool 就不出现（ADR 0030）。
            edit_metadata=(
                self._edit_metadata_action if self.metadata_edit_candidate() is not None else None
            ),
        )

    def open_current_entry(self) -> None:
        """「打开」：照片开大图，相册与分组进去 —— 与双击是同一件事。"""
        index = self._grid.currentIndex()
        if index.isValid():
            self._on_entry_activated(index)

    def open_current_in_default_app(self) -> None:
        """「在默认应用中打开」⇧⏎：交给系统为这类文件设定的默认应用（见 ``ui.desktop``）。

        只作用于**当前这一张**。用 ``photo_at`` 而不是「取当前项再判断类型」，把分组卡片与
        相册挡在这一层。行的来源是 :meth:`_focus_row`：照网格的当前项会把上一张打开（ADR 0021）。
        """
        ref = self._model.photo_at(self._focus_row())
        if ref is None:
            return
        # 成功时没有话可说（那个应用自己会弹到前台），只有失败才上横幅。
        message = desktop.open_with_default_app(ref.path)
        if message is not None:
            self._show_banner(message)

    def reveal_selection(self) -> None:
        """「在访达中显示」：在访达里把选中的东西选出来（见 ``ui.desktop``）。

        选中的**分组卡片**摊平成它的组员（ADR 0020）：卡片自己没有路径，而它代表的
        那批照片在访达里实实在在地摆着。
        """
        paths = self._selected_paths()
        if not paths:
            self._show_banner(NOTHING_SELECTED)
            return
        self._show_banner(desktop.reveal_in_finder(paths), auto_hide=True)

    def copy_selected_paths(self) -> None:
        """「拷贝路径」⇧⌘C：与访达一样，一行一个。"""
        paths = self._selected_paths()
        if not paths:
            return
        desktop.copy_to_clipboard(desktop.format_paths(paths))
        head = "已拷贝 1 个路径。" if len(paths) == 1 else f"已拷贝 {len(paths)} 个路径。"
        self._show_banner(head, auto_hide=True)

    def copy_selected_files(self) -> None:
        """「拷贝」⌘C：把选中的**文件**放进剪贴板，访达里 ⌘V 得到一份拷贝（ADR 0017）。

        与「拷贝路径」一样作用于整个选择，相册（文件夹）也算。**一个字节都不碰原文件**：
        放进剪贴板的是路径引用，粘出来的那一份由访达去写。
        """
        paths = self._selected_paths()
        if not paths:
            return
        self._show_banner(desktop.copy_files_to_clipboard(paths), auto_hide=True)

    # ---- 右键菜单里的只读命令（见 ADR 0014）

    def _on_clipboard_changed(self) -> None:
        """剪贴板变了就重算一次 ⌘V 的可用性（只算这一条，不动别的）。"""
        in_grid = self._stack.currentWidget() is self._grid_page
        self._paste_action.setEnabled(in_grid and self._can_paste())

    def select_all_entries(self) -> None:
        """⌘A：全选当前这一层的所有格子（相册、照片、分组卡片都算）。"""
        if self._stack.currentWidget() is not self._grid_page:
            return
        self._grid.selectAll()
        self._update_action_states()

    def copy_current_metadata(self) -> None:
        """「复制元数据」：把当前这张的元数据拷成文本。

        缓存里没有就**现读一次**（这条命令在面板没展开时也可用），约 50ms，走后台线程。
        大图页上拷的是**正在看的那一张**（ADR 0021），所以行的来源是 :meth:`_focus_row`。
        """
        ref = self._model.photo_at(self._focus_row())
        if ref is None:
            return
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._put_metadata_on_clipboard(cached, ref)
            return
        generation = self._generation

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._metadata_copy_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_copied, ref, generation),
            on_error=self._on_metadata_copy_failed,
        )

    def _on_metadata_copied(self, ref: PhotoRef, generation: int, payload: object) -> None:
        self._metadata_copy_job = None
        if not isinstance(payload, MetadataReport) or generation != self._generation:
            return  # 已经换库了：这份元数据不该再跑到界面上
        self._cache_metadata(ref, payload)
        self._put_metadata_on_clipboard(payload, ref)

    def _on_metadata_copy_failed(self, message: str) -> None:
        self._metadata_copy_job = None
        logger.info("复制用的元数据读取失败：%s", message)
        self._show_banner(f"没能读出元数据：{message.rstrip('。')}")

    def _put_metadata_on_clipboard(self, report: MetadataReport, ref: PhotoRef) -> None:
        desktop.copy_to_clipboard(report.as_text())
        self._show_banner(f"已复制「{ref.name}」的元数据。", auto_hide=True)

    def copy_current_group(self) -> None:
        """「复制分组条件」：把这个组是怎么分出来的抄走。

        组卡片本身只是磁盘上那批照片的一个切面，粘到别处也得说得出它是哪一组。
        """
        group = self._model.group_at(self._grid.current_row())
        if group is None:
            return
        facet = group_facet(group.facet)
        field = facet.label if facet is not None else group.facet
        desktop.copy_to_clipboard(f"按「{field}」分组：{group.label} · {group.count} 张")
        self._show_banner(f"已复制分组条件：按「{field}」分组。", auto_hide=True)


__all__ = ["ContextMenuFlowMixin"]
