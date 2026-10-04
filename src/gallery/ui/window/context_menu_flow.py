"""右键菜单（ADR 0014）与挂在菜单上的那几条只读命令。

网格与大图页**共用同一套流程**：视图只报「按在了哪」，菜单在这一侧拼
（:meth:`ContextMenuFlowMixin._show_context_menu` / ``_show_detail_context_menu``）。
两个 ``*_context_menu`` 方法**只拼不弹**，测试因此可以直接调它看内容，不必真的弹一次。

「粘到哪里」由**落点**决定而不是由菜单决定（:meth:`ContextMenuFlowMixin._context_paste`）：
空白处粘进当前这一层，按在单个相册上粘进那个相册**内部**，照片 / 多选 / 分组卡片上压根
不出现这一行；而递给菜单的**是同一枚动作**（菜单栏那个 ⌘V），所以菜单里那一行照常写着
⌘V，点它与按 ⌘V 是同一件事 —— 不可能出现「写着 ⌘V、按下去却是另一个地方」的分裂。

菜单里「**有什么**」在 :mod:`gallery.ui.menus`（ADR 0014 / 0018 / 0021 / 0023），
「**长什么样**」在 :mod:`gallery.ui.menu_style`，这一段只负责把落点翻译成一包动作
（:meth:`ContextMenuFlowMixin._menu_actions`）再交给它们。
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

        粘到哪里不看菜单本身，看**选区**（:meth:`_paste_target`）—— 右键那一格会先把它
        选中（``context_menu_for`` 之前网格已经 ``select_for_context`` 过了），所以点那一行
        与按 ⌘V 落在同一个目录上，不需要谁替菜单记着「这一下按的是哪个相册」。
        """
        menu = self.context_menu_for(row)
        if menu is None:
            return
        menu.exec(self._grid.cursor().pos())
        menu.deleteLater()

    def context_menu_for(self, row: int) -> QMenu | None:
        """这一下该弹什么菜单。**只拼不弹**，测试因此可以直接调它看内容。

        拼之前先刷新一次动作的可用性：「上一级」退不退得回去、「重命名」会不会被分组
        挡掉，判据都依赖**此刻**的选区与层级（见 :meth:`_update_action_states`）。
        拿几十毫秒前的状态来拼菜单，用户会看到一个与界面不一致的灰度。

        「粘贴」拼在哪、粘到哪，按**落点**定（见 :meth:`_context_paste`）：空白处粘进
        当前这一层，按在某个相册上则粘进那个相册**内部**，按在照片上则压根不出现。

        ``None`` 表示这次右键不该有菜单（不在网格页、行号已经过期）。
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
        （ADR 0021）。菜单用完即弃：它里面那份 ``QAction`` 是共用的，但这份菜单本身不是。
        """
        menu = self.detail_context_menu()
        if menu is None:
            return
        menu.exec(position)
        menu.deleteLater()

    def detail_context_menu(self) -> QMenu | None:
        """大图页该弹什么菜单。**只拼不弹**，与 :meth:`context_menu_for` 一个规矩。

        判据只有一条：现在在大图页，且**确实在看一张照片**。加载失败、画面还空着的时候
        右键一下也不该有菜单 —— 里面每一条命令的对象都还不存在。
        """
        if self._stack.currentWidget() is not self._detail_page:
            return None
        if self._model.photo_at(self._current_row) is None:
            return None
        # 与网格那一侧同样先刷新一次可用性：菜单里那些行共享同一份 QAction，灰度得是
        # 「此刻」的灰度，而不是进大图页时算的那一份。
        self._update_action_states()
        # 大图页没有「当前这一层」可粘，因此这一份动作包里压根不带「粘贴」
        # （ADR 0018 决策 7），与网格里单张照片那份一致。
        return self._dressed_menu(build_menu(self._menu_actions(None), TargetKind.DETAIL))

    def _dressed_menu(self, menu: QMenu) -> QMenu:
        """刚拼好的菜单在下发之前要做的那几件事：底色、圆角行距、快捷键那一列。

        网格与大图页共用同一段 —— 两边拼出来的菜单长得不一样，用起来却必须是同一个软件。
        """
        # 菜单的底色取自调色板的 ``Window`` 角色，而它默认来自系统（浅色 ``#ececec``），
        # 于是按一次右键就掉出整块底色之外。菜单是**这一下**才建的，此后不会再有一次
        # 主题下发，所以直接照抄窗口那份统一调色板（``ui.theme`` 逐控件下发的那份）。
        # 系统随后换浅色 / 深色也不影响它：下一次右键建的就是一个新的。
        menu.setPalette(self.palette())
        # 圆角与行距：macOS 样式给的那圈约 5 像素、每行 18 像素高，太挤，只能用样式表改。
        # 而样式表一上，面板就不再由系统画、底色会掉回系统菜单色，所以底色必须显式写进
        # 样式表（见 ``ui.menus`` 模块文档）。
        menu_style.apply_style(menu)
        # 快捷键那一列我们自己画（按字母 / 修饰符分列），得先跟 Qt 约好别重复画。
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
        | 大图页 | 不出现（那一页连「当前这一层」都没有，见 :meth:`detail_context_menu`） |

        前两处递进去的**是同一枚动作**（:attr:`_paste_action`，菜单栏那个 ⌘V）—— 因此空白处
        与相册那两份菜单里那一行都照常写着 ⌘V，而且**点它与按 ⌘V 是同一件事**，不可能出现
        「写着 ⌘V、按下去却是另一个地方」的分裂。粘到哪里一律由 :meth:`_paste_target` 算：
        这一格被右键选中着，它就是那个相册（访达的「粘贴项目」：按在哪个文件夹上就粘进哪个
        文件夹）；空白处那一次选区是空的，于是粘进当前这一层。

        其余落点一律不出现，按的是用户给的取向：按在一张照片上时「粘到哪」说不清（这一格
        未必属于当前这一层），多选相册时「粘进哪一个」说不清。⌘V 一直都在 —— **菜单摆不摆
        它，与快捷键怎么用是两回事**（⌘V 粘到哪里照样按选区算，见 :meth:`_paste_target`）。
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
        """把菜单要用的动作收成一包（``ui.menus`` 只认这个，不认窗口）。

        ``paste`` 由 :meth:`_context_paste` 按**这一下的落点**算好递进来（照片那一处压根不递），
        摆在第几段由每张菜单自己决定（``ui.menus``）。
        """
        # 相册清单**在用户展开子菜单时才列**（``_album_picker``）：列一次要走一遍整棵
        # 目录树（3000 个相册约 120 ms，全在主线程上），而「右键一下」并不等于
        # 「要看这两个子菜单里有什么」。
        #
        # 「一个相册都没有就干脆不建这条菜单」那条取向仍然留着（ADR 0014 决策 4：
        # 空子菜单点开是个空框），但问法换成了 ``has_albums`` —— 它找到一个就停，
        # 所以有相册时那一下是**一次 scandir**，而不是一次全树遍历。
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
            # 「新建相册」只在空白处那张菜单里（ADR 0022 决策 1），而建不了时不出现 ——
            # 与「粘贴」同一个取向。此时它的快捷键 ⇧⌘N 由 ``_update_action_states`` 置灰。
            new_album=self._new_album_action if self._can_create_album() else None,
            # 两个相册清单：库里一个相册都没有时压根不出现（空子菜单是个空框，
            # 与 ADR 0014 决策 4 同一个取向）。每次弹出现建，所以连的是这一份的信号。
            copy_to_album=copy_to_album,
            move_to_album=move_to_album,
            # 「粘贴」由 :meth:`_context_paste` 按落点递进来：此刻粘不了（剪贴板里没有
            # 文件）就传 ``None``，菜单里干脆不出现，而不是摆一条永远点不动的。
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
            # 封面那两条（ADR 0028）按**这一下的落点与选区**算，与「新建相册」同一个做法：
            # 此刻没有对象就是 ``None``，菜单里不出现这一行，而不是摆一条点不动的。
            # 对象是谁由 ``WriteOpsMixin`` 回答 —— 它与「粘到哪去」「动的是哪些东西」
            # 是同一个前置问题，答案自然也在同一个地方。
            set_cover=self._set_cover_action if self.cover_candidate() is not None else None,
            clear_cover=self._clear_cover_action if self.covered_album() is not None else None,
            # 「编辑元数据」按「此刻有没有一张照片可编辑」算（ADR 0030）：没有对象、
            # 或者没装 exiftool，这一行都不出现，而不是摆一条点下去只会报错的。
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

        作用于**当前这一张**，多选时说不清打开哪一张，因此菜单里那一行压根不出现。
        用 ``photo_at`` 而不是「取当前项再判断类型」：分组卡片与相册在这条命令上没有
        意义（相册是文件夹，让系统在访达里打开它是「打开」自己的活），这一层直接挡住，
        免得拼错一次就在这里抛 ``AttributeError``。

        行的来源是 :meth:`_focus_row` 而不是网格的当前项：大图页上看的可能已经是翻过去的
        那一张（ADR 0021），照着网格的当前项会把上一张交给默认应用打开。
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
        那批照片在访达里实实在在地摆着 —— 这正是用户右键一张「Canon · 128 张」时
        想看到的东西。
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
        放进剪贴板的是路径引用，粘出来的那一份由访达去写，不在 Gallery 的地盘上。
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

        缓存里没有就**现读一次** —— 这条命令在面板没展开时也可用，而那时多半没有缓存
        可用。读一次约 50ms，走后台线程，完成时用横幅说一声。

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
