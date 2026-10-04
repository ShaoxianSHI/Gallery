"""界面上**会动用户文件**的那些入口，全在这儿（红线 1）。

**为什么把它们收成一个文件**：红线 1 说「绝不删除、移动用户照片，重命名是唯一被豁免的
操作」。在三千多行的 ``main_window.py`` 里，那句话只能靠人记；而收在这里之后它变成一句
可以查的话 —— 界面上所有调 ``rename_entry`` / ``move_to_trash`` / ``copy_into`` /
``move_entries`` / ``undo_last_write`` 的地方都落在这个文件里，守护测试就是这么钉的。

这一份拥有：

- **粘贴**（ADR 0018）：粘进选中的那个相册，否则粘进当前这一层。
- **新建相册**（ADR 0022）。
- **复制到相册 / 移动到相册**（ADR 0019）。
- **改名**（ADR 0004）与**移到废纸篓**（ADR 0008）—— 仅有的两个直接改原件的入口。
- **撤销**（⌘Z）：改回去，或者从废纸篓取回来。
- **设为相册封面 / 取消相册封面**（ADR 0028）—— 写的是 ``config.toml``，不碰照片。
- **编辑元数据**（拍摄时间与坐标、三层地名，ADR 0030 / 0031 / 0032）—— **本项目里第一个
  真正改动用户照片内容的入口**：写的是 EXIF / IPTC / XMP。规矩见
  ``core.metadata.writeback``。
- 以及它们共用的那一个前置问题：「**现在动的是哪些东西**」。

**它不碰文件。** 每一个真正的写操作都走 ``self._library`` 上的某个方法
（``Library.rename_entry`` / ``move_to_trash`` / ``undo_last_write`` …），而那些方法在
``core/`` 里、必记 journal、可 ⌘Z 撤销。这一份只负责「什么时候调、调完怎么把界面
对齐」。这条边界与 ``core`` 那边的红线是一对：界面决定**时机**，``core`` 保证**后果**。

**它是一个 mixin**，因为它到处用 ``self._grid`` / ``self._model`` / ``self._banner`` ——
41 处 ``self.``，其中约二十处是窗口自己提供的状态。抽成独立对象要把这些一个个改成
构造参数，那是一次**行为**改动；收成一个 mixin 只动「方法在哪个文件里」，调用点一个
都不用改。把依赖写在这里，是为了不让它隐形。

依赖（由 ``MainWindow`` 提供）：

======================  ==========================================================
``_library``            当前库；没有库时每条命令都直接返回
``_runner`` / ``_write_job``  后台写操作与「正在写」那一摊
``_detail_trashed``     大图页上刚删掉的那一张，⌘Z 之后跳回它（ADR 0021）
``_model`` / ``_grid``  网格与模型，写完要同步它们
``_pipeline``           缩略图流水线；换封面要让它忘掉对这个相册的记忆
``_stack`` / ``_grid_page`` / ``_detail_page``  「现在在网格页还是大图页」决定粘到哪里
``_group_view`` / ``_browse_mode`` / ``_current_dir``  当前这一层的判据
``_preview_cache`` / ``_metadata_cache``  写完要把受影响的缓存条目丢掉
``_show_banner`` / ``_reload_level`` / ``_update_action_states``  写完的收尾
``_show_banner`` 的 ``auto_hide``          做完的确认说过就走，出了岔子就留着（ADR 0029）
``_note_self_write`` / ``_refresh_thumbnail_window`` / ``_update_level_notice``  同上
``_title_bar`` / ``_refresh_title``  大图页的改名落在窗口标题栏那一行（ADR 0025）
======================  ==========================================================
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QMenu

from gallery.core.errors import GalleryError
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.session import BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT
from gallery.core.metadata.capture_time import CaptureTime
from gallery.core.metadata.exif import MetadataReport, read_metadata
from gallery.core.metadata.journal import ACTION_METADATA, ACTION_RENAME
from gallery.core.metadata.metadata_edit import MetadataEdit, MetadataState
from gallery.core.metadata.paste import PasteResult
from gallery.core.metadata.place_edit import PlaceState
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.transfer import MoveResult
from gallery.core.metadata.undo import UndoResult
from gallery.ui import banners, desktop, menus
from gallery.ui.panels.metadata_edit import MetadataEditDialog
from gallery.ui.tasks import TaskContext

logger = logging.getLogger(__name__)


#: 选中分组卡片而要改的却是**卡片本身**时的那句解释（ADR 0020）。
#:
#: 摊平之后（``_selected_paths``）只有「改名」还会走到这里：组里的照片是真的能拷、能搬、
#: 能进废纸篓的，而「把这个卡片改名 / 删掉这个卡片」依然做不到 —— 它不是磁盘上的东西。
GROUP_NOT_FILE_ACTION = (
    "分组不是磁盘上的文件，改不了名也删不掉。双击进入分组后选中里面的照片再操作。"
)


#: 摊平之后一个路径都没剩下（正常情况下不会：组卡片必带至少一张照片）。
NOTHING_SELECTED = "没有可处理的东西。"


def shared_parent(photos: Sequence[PhotoRef]) -> Path | None:
    """这组照片是不是**全都在同一个相册**里；是就返回那个相册，不是就返回 ``None``。

    分组是元数据切出来的视图，本身不是文件夹：只有当它底下碰巧只有一个相册时，
    才有「粘到哪去」这个问题可言（见 ADR 0018 决策 1）。

    先比字面路径（绝大多数情况一步就够），实在不止一个才去解析真实位置 ——
    ``/private/var/…`` 与 ``/var/…`` 说的是同一个文件夹，但解析是要碰磁盘的。
    """
    if not photos:
        return None
    parents = {photo.path.parent for photo in photos}
    if len(parents) == 1:
        return parents.pop()
    resolved = {parent.resolve() for parent in parents}
    return resolved.pop() if len(resolved) == 1 else None


def flattened_entries(entries: Sequence[GridEntry]) -> list[GridEntry]:
    """把选区里的**分组卡片换成组员**，其余条目原样保留（按路径去重、保持网格顺序）。

    分组是元数据切出来的一个切面，不是磁盘上的东西，而它代表的那批照片实实在在躺在
    磁盘上 —— 所以凡是「对选区里的东西做点什么」的命令，都该作用在摊平之后的结果上
    （ADR 0020）。摊平的**只有分组卡片**：相册保持相册（整棵子树进废纸篓、整个文件夹
    拷走，那本来就是「对这个相册做什么」）。

    去重是必要的：同一张照片既被单独选中、又被它所在的组一起选中时只该算一次
    （否则粘贴会在同一个目录里造出一张「一张 2」）。

    摊平只发生在**命令的对象**上，**不动选区** —— 用户右键的是一张卡片，界面上仍然是
    一张卡片被选着，撤销与重选都还照旧。
    """
    flat: list[GridEntry] = []
    seen: set[Path] = set()
    for entry in entries:
        for item in entry.photos if isinstance(entry, GroupRef) else (entry,):
            if item.path not in seen:
                seen.add(item.path)
                flat.append(item)
    return flat


class WriteOpsMixin:
    def _paste_target(self) -> Path | None:
        """⌘V 要粘进哪个文件夹 —— **先看选区，再看这一层**（ADR 0018 决策 1、决策 7）。

        | 选区 | 粘到哪里 |
        |---|---|
        | **恰好选中一个相册** | **那个相册内部**（访达的「粘贴项目」） |
        | 其余 —— 没选中、选中照片或分组卡片、选了不止一个 | 当前这一层 |

        「**恰好**一个」是刻意的：这一层里照片与相册卡片是**混着摆**的（一个文件夹下面既
        可以有照片也可以有子文件夹），选区里可能同时有照片与相册、也可能是好几个相册 ——
        那都说不出「粘进哪一个相册」。此时照旧粘进当前这一层：那是用户在这条命令上积累起来
        的习惯，不该被一次多选悄悄改掉。

        右键某个相册时菜单里那一条「粘贴」也是它（ADR 0018 决策 7），而右键**会先把那一格
        选中**（``select_for_context``，ADR 0014 决策 2）—— 于是点那一行与按 ⌘V 天然落在
        同一个目录上，不必有谁替谁记着「这一下按的是哪个相册」。

        没选中相册时的那一层，判据仍然**按磁盘上的真实位置**算：

        - **相册模式**：当前这一层的文件夹，停在顶层时就是库根；
        - **全部照片**：整层是平铺的，唯一说得清的目录是库根；
        - **分组里**：分组不是文件夹，**只有当这一组照片本来就都在同一个相册里**，
          那个相册才算它的目录；散在多个相册里的组没有可粘的地方（于是 ⌘V 置灰）。
          其余情况就是当前这一层自己对应的目录（见 :meth:`_level_directory`）。

        分组里那一条正是用户要的「不同分组如果是同一个相册，还是算同一目录」：判据是
        磁盘上的位置，不是分组本身 —— 按相机和按年份各开一张卡片，只要底下都是同一个
        京都，粘进去的就是同一个京都。
        """
        album = self._selected_album()
        if album is not None:
            return album
        if self._group_view is not None:
            return shared_parent(self._group_view.photos)
        return self._level_directory()

    def _selected_album(self) -> Path | None:
        """选区里**恰好是一个相册**时返回它，否则 ``None``（判据见 :meth:`_paste_target`）。

        **直接读选区，不走 :meth:`PhotoGrid.selected_rows`** —— 那一位在选区为空时会
        **退回当前项**（网格刚打开时 Qt 可能顺手把第 0 行设成当前项，见 ``selected_rows``
        的注释），而普通左键点空白处只清选区、**不清当前项**（``select_for_context``
        才两样一起清，那是右键走的路）。于是「选中过相册 → 点空白取消选中」在那一层看起来
        仍选着那个相册，⌘V 就会照旧粘进一个用户已经取消选择的目录 —— 粘文件进用户没选的
        地方是这个功能里最坏的一种错，代价远大于反过来（没粘到他以为的地方，他看得见）。

        **只认网格页的选区**：大图页上「选中」的是正在看的那一张（``_selected_entries``
        那边就换过人了，ADR 0021），那一格不是用户选出来的，不该把 ⌘V 带去别处。
        """
        if self._stack.currentWidget() is not self._grid_page:
            return None
        selection = self._grid.selectionModel()
        if selection is None:
            return None
        rows = sorted({index.row() for index in selection.selectedIndexes()})
        if len(rows) != 1:
            return None
        entry = self._model.entry_at(rows[0])
        return entry.path if isinstance(entry, FolderRef) else None

    def _level_directory(self) -> Path | None:
        """当前这一层对应的磁盘目录，**没有就返回 ``None``**。

        - **相册模式**：当前这一层的文件夹，停在顶层时就是库根；
        - **全部照片**：整层是平铺的，唯一说得清的目录是库根；
        - **分组里**：``None`` —— 分组不是文件夹，而且那一层网格上摆的**全是照片**，
          新建出来的相册卡片插进去就是个错位的东西（ADR 0022 决策 1）。粘贴在分组里
          还有一条更宽松的路（见 :meth:`_paste_target`），新建相册没有。

        「粘到哪去」与「建到哪去」共用这一个判据，因此两处永远不会说出两个答案。
        """
        library = self._library
        if library is None or self._group_view is not None:
            return None
        if self._browse_mode == BROWSE_MODE_ALBUMS:
            return self._current_dir or library.root
        return library.root

    def _can_create_album(self) -> bool:
        """此刻建不建得了相册：得有「当前这一层」这个目录。

        与 :meth:`_can_paste` 同一取向 —— 建不了就不出现，而不是摆一条点不动的
        （ADR 0014 决策 4）。
        """
        return self._level_directory() is not None

    def _can_paste(self) -> bool:
        """此刻按 ⌘V 有没有用：得有可粘的目录，剪贴板里也得**真有文件**。

        读剪贴板约 1 微秒（实测 200 次 0.2ms），因此随时问得起，不必替它省。
        """
        return self._paste_target() is not None and bool(desktop.read_files_from_clipboard())

    def paste_into_current_folder(self) -> None:
        """「粘贴」⌘V：把剪贴板里的文件**复制**进当前这一层的目录（ADR 0018）。

        **粘到哪里只看选区**（:meth:`_paste_target`，ADR 0018 决策 7）：选中了一个相册就
        粘进那个相册内部（访达的「粘贴项目」，右键那一格已经把它选中了），否则粘进当前
        这一层。菜单里那一条「粘贴」也是这个动作，因此那一行写着 ⌘V，而菜单开着时按 ⌘V
        与点它必然落在同一个目录上。
        """
        target = self._paste_target()
        if target is None:
            self._show_banner("这里没有可以粘贴进去的文件夹。")
            return
        self._paste_into(target)

    def _paste_into(self, target: Path) -> None:
        """把剪贴板里的文件复制进 ``target``。⌘V 与右键菜单里那一条共用这一段。

        复制在后台线程上跑（大照片一个几百 MB，挡住界面就是卡住），完成后横幅说清
        粘了哪些、跳过了哪些、为什么失败。**新粘进来的照片由监听器自己发现并上屏**
        （ADR 0013），这里不去重扫这一层 —— 重扫会把网格清空，选中与滚动位置全丢。
        """
        library = self._library
        if library is None:
            return
        sources = desktop.read_files_from_clipboard()
        if not sources:
            self._show_banner("剪贴板里没有可粘贴的文件。")
            return
        if self._write_job is not None:
            # 两次写入同时跑会争同一个名字（各自先看「一张 2.jpg」在不在），后到的
            # 那个会把先到的盖掉 —— 那是这个项目最不能出现的一种事。粘贴、复制到相册、
            # 移动到相册共用这一道闸门。
            self._show_banner("上一次操作还没结束。")
            return

        def work(context: TaskContext) -> object:
            return library.copy_into(target, sources, should_cancel=context.is_cancelled)

        self._show_banner(f"正在把 {len(sources)} 项粘贴到「{target.name}」…")
        self._write_job = self._runner.submit(
            work,
            on_result=partial(self._on_paste_done, target),
            on_error=self._on_paste_failed,
        )

    def _on_paste_done(self, target: Path, payload: object) -> None:
        self._write_job = None
        if isinstance(payload, PasteResult):
            # 做完的确认**说过就走**，除非这句话里还有用户要处理的东西：没粘成的那些、
            # 没能记下的操作日志（判据见 ADR 0029 决策 3）。
            self._show_banner(
                banners.paste_banner(payload, target.name),
                auto_hide=payload.journaled and not payload.failures,
            )

    def _on_paste_failed(self, message: str) -> None:
        self._write_job = None
        logger.info("粘贴失败：%s", message)
        self._show_banner(f"没能粘贴：{message.rstrip('。')}")

    def create_album(self) -> None:
        """「新建相册」⇧⌘N：在当前这一层建一个空相册，**建完立刻进行内重命名**。

        三步，与访达那条一模一样：建出来 → 插进网格并选中 → 行内重命名的输入框摆到
        那一行上，光标已经在等用户起名。

        **为什么不等监听器（ADR 0013）**：监听要等文件系统事件跑一趟，而行内重命名
        必须马上有那一格可改 —— 等它就等于用户看着光标在空气里闪几十毫秒。所以这里
        自己插进模型，同时把这一个路径记进「自家写操作」集合，稍后监听器报上来的同一
        件事不会被重复应用（与改名、删除那几处完全一致）。

        **不走后台线程**：那是一次 ``mkdir``，不搬数据。粘贴一次几百 MB 才需要。

        建出来的是**普通的空文件夹**，不往里放任何东西 —— 照片怎么进去、怎么整理，
        用户说了算。紧接着那一次改名是普通改名：记账，``⌘Z`` 能撤。
        """
        library = self._library
        target = self._level_directory()
        if library is None or target is None:
            self._show_banner("这里没有可以新建相册的文件夹。")
            return
        if self._grid.is_renaming():
            # 键盘正在另一个行内输入框里：新建会让两个输入框同时活着，先做完手上这个。
            return
        try:
            created = library.create_album(target)
        except GalleryError as exc:
            logger.info("新建相册失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._note_self_write(created.path)
        row = self._model.insert_entry(
            created.album,
            whole_level=self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT,
        )
        if row < 0:
            # 插不进去（理论上不会：这一层里还没有同一个路径）。宁可重扫一次，
            # 也不能让磁盘上多了一个文件夹而界面上看不见。
            self._reload_level()
            self._show_banner(f"已新建相册「{created.name}」。", auto_hide=True)
            return
        self._grid.select_row(row)
        # 空文件夹里建了相册之后，「这一层是空的」那句提示就该收起来了（它判据是
        # 「网格上有没有条目」）。监听器不会替我们做这件事：自家写的那次已经被摘掉。
        self._update_level_notice()
        self._refresh_thumbnail_window()
        if not created.journaled:
            # 文件夹已经在手上了，缺的只是一条历史。如实说，别让用户以为「随时可查」。
            self._show_banner("没能记下操作日志。")
        self._grid.begin_rename(self._model.index(row, 0))

    # ---- 相册封面（ADR 0028）-------------------------------------------------
    #
    # 这一段写的是 ``config.toml``，也就是 **Gallery 自己的配置文件**：用户的照片一个
    # 字节都不动，因此不触犯红线 1，也**不进撤销栈**（那一摞栈装的是「动了用户文件」
    # 的那些操作）。它仍然收在这个文件里，是因为它毕竟是「用户按了一下之后才发生的写入」，
    # 而这一整个文件就是为了让那种写入在代码里一处可查（见模块文档）。
    #
    # 撤销是**对称的那一条命令**：相册右键菜单里的「取消相册封面」，不靠 ⌘Z。

    def cover_candidate(self) -> PhotoRef | None:
        """「设为相册封面」此刻作用于哪张照片；不该出现这一条时返回 ``None``。

        三道判据，缺一条就**不出现**（不是置灰，ADR 0014 决策 4）：

        - **恰好选中一张照片**。分组卡片不算：卡片不是磁盘上的东西，「把这个卡片当封面」
          说不清指的是哪一张（组里恰好一张时更会让人以为说的是整组）。
        - 它**属于某个相册**：库根下的照片没有相册卡片可以当封面，父目录就是库根。
        - 父目录确实还在（照片刚被移走时它可能已经没了）。

        大图页上算的是**正在看的那一张** —— 与 :meth:`_selected_entries` 同一个分叉
        （ADR 0021）：翻过图之后网格的选区早就不是这一张了。
        """
        library = self._library
        if library is None:
            return None
        entries = self._selected_entries()
        if len(entries) != 1:
            return None
        entry = entries[0]
        if not isinstance(entry, PhotoRef):
            return None
        album = entry.path.parent
        if album == library.root or not album.is_dir():
            return None
        return entry

    def covered_album(self) -> Path | None:
        """「取消相册封面」此刻作用于哪个相册；没有可取消的就返回 ``None``。

        判据是「选中的**恰好是一个**相册，而且它真的被指定过封面」—— 没指定过的那一
        条就**不出现**（用户看得见自己设过，也就没有需要撤销的东西）。
        """
        library = self._library
        entries = self._selected_entries()
        if library is None or len(entries) != 1:
            return None
        entry = entries[0]
        if not isinstance(entry, FolderRef):
            return None
        return entry.path if library.has_album_cover(entry.path) else None

    def set_selected_as_album_cover(self) -> None:
        """「设为相册封面」：把这一张记成它**所在那个相册**的封面（ADR 0028 决策 1）。

        **相册是照片的父目录，不问「现在站在哪一层」** —— 用户在「全部照片」那一屏
        里选到一张照片，说的一定是「它自己那个相册」，而不是当前这一层。

        不走后台线程：改一个键值对再落一次盘，与 ``mkdir`` 同量级（ADR 0022 决策 6）。
        """
        library = self._library
        photo = self.cover_candidate()
        if library is None or photo is None:
            self._show_banner("这里没有可以设为相册封面的照片。")
            return
        album = photo.path.parent
        try:
            library.set_album_cover(album, photo.path)
        except GalleryError as exc:
            logger.info("设置相册封面失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._refresh_album_cover(album)
        self._show_banner(banners.album_cover_banner(album.name, photo.name), auto_hide=True)

    def clear_album_cover(self) -> None:
        """「取消相册封面」：这个相册回到「里面第一张照片」当封面（ADR 0028 决策 5）。"""
        library = self._library
        album = self.covered_album()
        if library is None or album is None:
            return
        try:
            library.clear_album_cover(album)
        except GalleryError as exc:
            logger.info("取消相册封面失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._refresh_album_cover(album)
        self._show_banner(banners.album_cover_cleared_banner(album.name), auto_hide=True)

    def _refresh_album_cover(self, album: Path) -> None:
        """封面换了人，让那一张相册卡片重新去取图。

        **两处记忆都要清**，少任何一处新封面都不会立刻出现：

        - **模型**里已经缩放好的那张。不清的话 :meth:`PhotoListModel.needs_thumbnail`
          说「这张我有了」，网格就一直挂着旧封面 —— 而相册卡片的键就是相册自己的路径，
          它并不知道封面已经换了人。
        - **流水线**里对这个相册的记忆：试过哪些照片当封面（:class:`_Cover`）与
          「里面读不出图」。少了后一个，**一个原本空着的相册会永远停在文件夹图标上** ——
          用户刚把里面唯一那张设成封面，看到的却还是那个图标。

        相册卡片不在当前这一屏时这两步都是空转（模型里没有那一行），而回到上一层时
        本来就会重新列一遍。
        """
        self._model.forget_thumbnails([album])
        if self._pipeline is not None:
            self._pipeline.forget([album])
        self._refresh_thumbnail_window()

    # ---- 元数据编辑（ADR 0030 / 0031 / 0032）---------------------------------------
    #
    # **本项目里第一个真正改动用户照片内容的入口。** 其余那些改的是文件名与所在目录，
    # 这一条改的是照片里的字节 —— 于是它比别的都多两件事：写之前要拿到那份元数据
    # （用来填对话框、也是回滚与撤销的依据），写之后要把界面上**所有按
    # 「路径 + 大小 + 修改时间」认照片**的地方一起换掉。

    def metadata_edit_candidate(self) -> PhotoRef | None:
        """「编辑元数据」此刻作用于哪张照片；不该出现这一条时返回 ``None``。

        判据与 :meth:`cover_candidate` 同一取向：**恰好是一张照片**（网格页是当前项、
        大图页是正在看的那一张，ADR 0021），且此刻改得动（装了 exiftool）。分组卡片不是
        磁盘上的东西，说不清「编辑它的元数据」指的是哪一张。
        """
        library = self._library
        if library is None or not library.can_write_metadata():
            return None
        entry = self._model.entry_at(self._focus_row())
        return entry if isinstance(entry, PhotoRef) else None

    def edit_metadata_of_current(self) -> None:
        """「编辑元数据…」：先把元数据读到手，再弹对话框，保存后写回。

        面板没展开时多半没有缓存，**现读一次**（与「复制元数据」同一套路）：读一次约
        50ms，走后台线程，读完才弹对话框。**不在这里直接读** —— 那会让界面卡一下，
        而用户点的只是一条命令。
        """
        ref = self.metadata_edit_candidate()
        if ref is None:
            self._show_banner("这里没有可以编辑元数据的照片。")
            return
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._open_metadata_editor(ref, cached)
            return
        generation = self._generation

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._show_banner(f"正在读取 {ref.name} 的元数据…")
        self._metadata_copy_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_read_for_edit, ref, generation),
            on_error=partial(self._on_metadata_edit_read_failed, ref, generation),
        )

    def _on_metadata_read_for_edit(self, ref: PhotoRef, generation: int, payload: object) -> None:
        self._metadata_copy_job = None
        if not isinstance(payload, MetadataReport) or generation != self._generation:
            return  # 已经换库或换照片了：这份元数据不该再跑到界面上
        self._cache_metadata(ref, payload)
        if self.metadata_edit_candidate() != ref:
            self._show_banner("已经换了一张照片，请在它上面重新点一次「编辑元数据」。")
            return
        self._open_metadata_editor(ref, payload)

    def _on_metadata_edit_read_failed(self, ref: PhotoRef, generation: int, message: str) -> None:
        self._metadata_copy_job = None
        logger.info("编辑元数据前的元数据读取失败：%s", message)
        if generation == self._generation:
            self._show_banner(f"读不出「{ref.name}」的元数据，改不了它。")

    def _open_metadata_editor(self, ref: PhotoRef, report: MetadataReport) -> None:
        """摆出编辑器。**对话框只产出「用户填的那一份」，写盘在下面那一步。**

        拍摄时间与位置**一起**从这份报告里取：编辑器是一段（ADR 0032），它的两个输入框
        都要照片此刻的值填着才算「访达的显示简介」那一种用法。
        """
        state = MetadataState(
            capture=CaptureTime(
                taken_at=report.captured_at,
                offset=report.captured_offset,
            ),
            place=PlaceState(
                city=report.place_city,
                region=report.place_region,
                country=report.place_country,
                latitude=report.latitude,
                longitude=report.longitude,
            ),
        )
        dialog = MetadataEditDialog(self, state, resolver=self._place_lookup(), photo_name=ref.name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_metadata_edit(ref, state, dialog.edit())

    def _apply_metadata_edit(self, ref: PhotoRef, state: MetadataState, edit: MetadataEdit) -> None:
        """把编辑结果写回，并让界面上每一处「认照片」的地方都跟上。

        **写盘是同步的**：exiftool 一次约 50~200ms，命令本身是「保存」这个动作，
        弹个进度框比直接等它更让人烦。真正的耗时（大图）不在这一步。

        **时间与位置一次写回**（ADR 0032 决策 4）：一个对话框里按一次保存，得到的是
        一条日志、一下 ⌘Z、一句横幅。
        """
        library = self._library
        if library is None:
            return
        try:
            outcome = library.write_metadata_edit(ref, state, edit, resolver=self._place_lookup())
        except GalleryError as exc:
            logger.info("编辑元数据失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._after_metadata_write(outcome.entry)
        self._show_banner(banners.metadata_edit_banner(outcome, ref.name))
        self._refresh_undo_label()

    def _after_metadata_write(self, entry: PhotoRef | None) -> None:
        """写回之后，把界面上认照片的地方全部换一遍。**少任何一件都像「没生效」。**

        按顺序：

        1. **屏蔽监听器回声**（ADR 0013）。不屏蔽的话，那条 MODIFIED 会让监听器把这份
           缓存整个作废并重取 —— 于是 :meth:`Library.write_place` 里那次
           ``thumbnails.relocate`` 换键就白做了，要重新解码一遍。
        2. **丢掉按路径索引的内存缓存**：元数据与整图预览都是拿
           ``(路径, 大小, 修改时间)`` 当键的，两者都已经过期。
        3. **换掉网格里那一行的引用**：它还挂着旧的大小与修改时间，于是缩略图与索引
           都认为这一格没变过。
        4. **重排**：这一张可能已经不属于当前的分组 / 排序位置了 —— 按城市分组时把
           坐标改到另一个城市，它就该从这一组里离开。就地插行会插到错位置，所以整层重排
           （几十微秒，见 :meth:`_apply_restore` 的同一段理由）。
        """
        if entry is None:
            return  # 写完文件已经不在了；这时说什么都是多余的，交给监听器
        path = entry.path
        self._note_self_write(path)
        self._metadata_cache.pop(path, None)
        self._preview_cache.pop(path, None)
        row = self._model.row_of_path(path)
        if row >= 0:
            self._model.replace_entry(row, entry)
        # 这一张的元数据快照已经过期：从记忆里摘掉，让下面那一遍只重读它一个。
        self._facet_values.pop(path, None)
        self._start_facet_read()
        if self._panel.isVisible():
            self._load_metadata()

    def _on_album_chosen_for_copy(self, action: QAction) -> None:
        """用户在「复制到相册」里挑了一个相册。目标路径由子菜单挂在 ``action.data()`` 上。"""
        target = action.data()
        if isinstance(target, Path):
            self._transfer_selected(target, move=False)

    def _on_album_chosen_for_move(self, action: QAction) -> None:
        """用户在「移动到相册」里挑了一个相册。"""
        target = action.data()
        if isinstance(target, Path):
            self._transfer_selected(target, move=True)

    def _album_picker(self, title: str, albums: Sequence[tuple[str, Path]]) -> QMenu | None:
        """拿现成的相册清单拼一个子菜单；**清单为空就返回 ``None``**。

        清单由调用方**每次弹出时现列**（:meth:`_album_targets`）：用户在访达里新建一个
        相册，下一次右键就能选到，不必重开软件。列全库（不是只列当前这一层）是用户定的
        取向 —— 见 ADR 0019 决策 1。

        清单是**参数而不是在这里现列**，是因为一次右键要拼两个这样的子菜单：
        现列就是走两遍整棵目录树。列一遍、两个子菜单共用，那才是 ADR 0019 说的
        「每次弹出时现列」。

        目标相册里若恰好有一张正要移动的照片，那次操作会被如实记成「它已经在那个相册
        里了」。与粘贴同一条规矩（ADR 0018 决策 2），因此不必在这里把它藏起来。
        """
        if not albums:
            return None
        return menus.build_album_picker(title, albums)

    def _album_targets(self) -> tuple[tuple[str, Path], ...]:
        """递归列出全库相册，标签是相对路径（``2025/苏州``）。

        两个年份下面各有一个「苏州」时，光写名字用户分不清要挪到哪去，所以标签用
        相对路径（见 :meth:`gallery.core.library.facade.Library.album_label`）。
        """
        library = self._library
        if library is None:
            return ()
        return tuple((library.album_label(folder.path), folder.path) for folder in library.albums())

    def _transfer_selected(self, target: Path, *, move: bool) -> None:
        """把选中的照片**复制**或**移动**进 ``target``。

        两者共用这一段：源、后台任务、横幅、失败处理全都一样，差别只在调哪个
        ``Library`` 方法，以及横幅里说的是「复制」还是「移动」。**一次搬几百 MB**，
        所以都走后台线程（与 ⌘V 同一道闸门，见 :attr:`_write_job`）。

        选中的**分组卡片**摊平成它的组员（ADR 0020）：搬的是照片，不是它所在的目录 ——
        所以 ADR 0019 决策 5 挡的那一半（不搬相册、一次只动照片）仍然成立。

        移走的东西由监听器（ADR 0013）自己从这一层里摘掉：这里刻意不重扫 ——
        重扫会把网格清空，选中与滚动位置全丢，而监听器已经把该做的做完了。
        """
        library = self._library
        if library is None:
            return
        sources = self._selected_paths()
        if not sources:
            self._show_banner(NOTHING_SELECTED)
            return
        if self._write_job is not None:
            self._show_banner("上一次操作还没结束。")
            return
        verb = "移动" if move else "复制"

        def work(context: TaskContext) -> object:
            if move:
                return library.move_into(target, sources, should_cancel=context.is_cancelled)
            return library.copy_into(target, sources, should_cancel=context.is_cancelled)

        self._show_banner(f"正在把 {len(sources)} 项{verb}到「{target.name}」…")
        self._write_job = self._runner.submit(
            work,
            on_result=partial(self._on_transfer_done, target, verb),
            on_error=partial(self._on_transfer_failed, verb),
        )

    def _on_transfer_done(self, target: Path, verb: str, payload: object) -> None:
        self._write_job = None
        if not isinstance(payload, (MoveResult, PasteResult)):
            return
        # 与粘贴同一判据（ADR 0029 决策 3）：确认说过就走，出了岔子就留着。
        self._show_banner(
            banners.transfer_banner(payload, target.name, verb),
            auto_hide=payload.journaled and not payload.failures,
        )
        if isinstance(payload, MoveResult):
            self._refresh_undo_label()

    def _on_transfer_failed(self, verb: str, message: str) -> None:
        self._write_job = None
        logger.info("%s到相册失败：%s", verb, message)
        self._show_banner(f"没能{verb}：{message.rstrip('。')}")

    def _selected_entries(self) -> list[GridEntry]:
        """「此刻这些命令作用于谁」—— 网格页是整个选区，大图页是正在看的那一张。

        **大图页不能问网格的选区**：翻图（``←`` / ``→``）只动 ``_current_row``，网格的选区
        停在打开大图的那一格。于是在大图页里问选区，「在访达中显示」「拷贝路径」这些
        命令作用到的会是一张**屏幕上根本没有**的照片 —— 用户看到的与菜单要做的事正好相反
        （ADR 0021）。
        """
        if self._stack.currentWidget() is self._detail_page:
            row = self._current_row
            entry = self._model.entry_at(row) if row >= 0 else None
            return [] if entry is None else [entry]
        return [
            entry
            for entry in (self._model.entry_at(row) for row in self._grid.selected_rows())
            if entry is not None
        ]

    def _focus_row(self) -> int:
        """「当前这一格」的行号：网格页是当前项，大图页是正在看的那一张（ADR 0021）。

        与 :meth:`_selected_entries` 同一个分叉，理由也一样：网格的当前项停在大图打开时的
        那一格，翻过图之后它就过期了。
        """
        if self._stack.currentWidget() is self._detail_page:
            return self._current_row
        return self._grid.current_row()

    def _selected_paths(self) -> list[Path]:
        """命令要作用在磁盘上的哪一串东西（按网格顺序，已去重）。

        **分组卡片在这里摊平成它的组员**（ADR 0020）：卡片不是磁盘上的东西，它代表的
        那批照片是。「在访达中显示」「拷贝」「拷贝路径」「复制到相册」「移动到相册」
        「移到废纸篓」因此一条都不用改 —— 它们要的一直是路径，只是这一串现在可能来自
        一张分组卡片。

        摊平只发生在**命令的对象**上，不动选区：用户右键的是一张卡片，界面上仍然是
        一张卡片被选着。
        """
        return [entry.path for entry in flattened_entries(self._selected_entries())]

    def rename_current_entry(self) -> None:
        """菜单 / ⌘R：**大图页改窗口标题栏那一行，网格里仍是就地的行内编辑**（ADR 0025 决策 3）。

        两处改的**是同一个写操作入口**（``_on_rename_committed``），换的只是「在哪一行上改」——
        所以记账、横幅、撤销、监听器回声在两边一样都不少。
        """
        if self._stack.currentWidget() is self._detail_page:
            # 没有标题栏那一段的平台上大图页没有改名字段，动作那边也是关着的（见
            # ``_update_action_states``）；这里只是不让一个看不见的输入框把键盘吃掉。
            if self._title_bar.isVisible():
                self._title_bar.begin_edit()
            return
        index = self._grid.currentIndex()
        if not index.isValid():
            return
        if self._model.group_at(index.row()) is not None:
            self._show_banner(GROUP_NOT_FILE_ACTION)
            return
        self._grid.begin_rename(index)

    def _on_rename_requested(self, index: QModelIndex) -> None:
        """再次点击已选中的格子：进入行内重命名。"""
        if self._model.group_at(index.row()) is not None:
            self._show_banner(GROUP_NOT_FILE_ACTION)
            return
        self._grid.begin_rename(index)

    def _on_rename_active_changed(self, _active: bool) -> None:
        """改名的这段时间里 Esc 是「取消改名」，菜单里的「上一级」也跟着让开。"""
        self._update_action_states()

    def _on_rename_committed(self, row: int, text: str) -> None:
        entry = self._model.entry_at(row)
        library = self._library
        if entry is None or library is None:
            return
        try:
            result = library.rename_entry(entry, text)
        except GalleryError as exc:
            logger.info("重命名失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._apply_rename(result, row)
        self._refresh_undo_label()

    def trash_selected(self) -> None:
        """⌘⌫：把选中的照片 / 相册移进系统废纸篓。

        选中的**分组卡片**摊平成它的组员（ADR 0020）：删不掉的是那张卡片，删得掉的是
        它代表的那批照片 —— 与「进去全选再删」得到的是同一件事，只是少了一趟。

        不弹确认框，理由和访达一样：废纸篓本身就是那层保险，⌘Z 再是一层；
        弹窗只会让「整理一批照片」变成一连串的确认。做完立刻用横幅说清楚。

        **大图页上也删得动**（ADR 0021），删的是**正在看的那一张**：删掉之后不退回网格，
        而是接着看下一张 —— 看照片时最顺手的动作就是「这张不要，下一张」（见
        :meth:`_show_next_after_trash`）。网格那一侧的行为一个字没改。
        """
        library = self._library
        if library is None:
            return
        if self._grid.is_renaming():
            return  # 键盘正在行内输入框里，删掉正在编辑的那一张只会让人以为见鬼了
        in_detail = self._stack.currentWidget() is self._detail_page
        if not in_detail and self._stack.currentWidget() is not self._grid_page:
            return
        # 大图页作用的是正在看的那一张，网格页是整个选区（见 _selected_entries）；
        # 网格上的分组卡片摊平之后是它那批组员（ADR 0020）—— 大图页上不会遇到卡片。
        rows = [self._current_row] if in_detail else self._grid.selected_rows()
        entries = flattened_entries(self._selected_entries())
        if not entries:
            return
        try:
            result = library.move_to_trash(entries)
        except GalleryError as exc:
            logger.info("移到废纸篓失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            return
        self._forget_trashed(result.moved)
        # 监听器随后会把「这些路径不见了」报回来；这里已经摘过行了，不必再摘一次。
        self._note_self_write(*(entry.path for entry in result.moved))
        # 只摘真正移走的那些行：批量里失败的那几张还得留在网格上。
        moved_paths = {entry.path for entry in result.moved}
        removed = [row for row in rows if self._is_row_moved(row, moved_paths)]
        self._model.remove_rows(removed)
        self._select_after_removal(removed)
        # **大图页上删掉的那一张记下来**：⌘Z 之后要跳回它（见 ``_jump_back_to_restored``）。
        # 网格页上删的不记 —— 撤回来之后留在网格上才是对的。只记**真正移走的**那一张，
        # 移失败的压根不算删过。
        self._detail_trashed = None
        if in_detail:
            self._detail_trashed = next(
                (entry.path for entry in result.moved if entry.path == entries[0].path),
                None,
            )
            self._show_next_after_trash()
        # 确认说过就走；没移走的那些与「⌘Z 撤销不了」要留着（ADR 0029 决策 3）。
        self._show_banner(
            banners.trash_banner(result),
            auto_hide=result.journaled and not result.failures,
        )
        self._refresh_undo_label()
        self._refresh_thumbnail_window()

    def _show_next_after_trash(self) -> None:
        """删掉正在看的那一张之后，接着看下一张（ADR 0021）。

        落在哪一格由 ``_select_after_removal`` 决定 —— 原来那格的位置，没了就是最后一格 ——
        对「看下一张」来说正好。两种情况之外只能退回网格：

        - 那一格是**相册**或分组卡片（大图页删掉的是这一层的最后一张照片，而这一层里还有相册）；
        - 整层**一张照片都不剩了**：已经没有可看的，网格是唯一说得清的地方。

        留在大图页显示一张已经不存在的照片，比退回网格更糟。
        """
        row = self._grid.current_row()
        # 网格的选区已经落到下一格：大图页与网格从此必须指着同一格（``_current_row`` 与
        # 网格当前项对不上时，⌘Z 取回来的照片会插到别处去）。
        self._current_row = row
        if self._model.photo_at(row) is None:
            self.show_grid()
            return
        self._load_detail(row)

    def _is_row_moved(self, row: int, moved_paths: set[Path]) -> bool:
        """这一行是不是已经不在磁盘上了。

        分组卡片没有路径可比：它整组都进了废纸篓才算「这一行没了」（ADR 0020 决策 4），
        只走了一部分的话卡片留着，张数下一次列这一层时更新 —— 与在分组视图里删掉组内
        几张照片时完全一样。
        """
        entry = self._model.entry_at(row)
        if entry is None:
            return False
        if isinstance(entry, GroupRef):
            return bool(entry.photos) and all(photo.path in moved_paths for photo in entry.photos)
        return entry.path in moved_paths

    def _forget_trashed(self, entries: Sequence[GridEntry]) -> None:
        """丢掉内存里按路径索引的缓存。

        磁盘上的缩略图缓存**留着**：照片还在废纸篓里，撤销回来时直接命中，
        不必重新解码（这与改名的处理相反，见 ``Library.move_to_trash``）。
        """
        for entry in entries:
            self._preview_cache.pop(entry.path, None)
            self._metadata_cache.pop(entry.path, None)

    def _select_after_removal(self, removed: Sequence[int]) -> None:
        """摘掉几行之后，把选中落到原来那一带（访达是选中后一个）。"""
        if not removed:
            return
        target = min(removed)
        if target >= self._model.rowCount():
            target = self._model.rowCount() - 1
        if target >= 0:
            self._grid.select_row(target, ensure_visible=False)

    def undo_last_write(self) -> None:
        """⌘Z：撤销最近一次写操作 —— 改回去，或者从废纸篓取回来。"""
        library = self._library
        if library is None:
            return
        try:
            result = library.undo_last_write()
        except GalleryError as exc:
            logger.info("撤销失败：%s", exc.detail or exc.message)
            self._show_banner(exc.message)
            self._refresh_undo_label()
            return
        if result is None:
            self._show_banner("没有可撤销的操作。")
            return
        # **这一次撤销之后，「跳回刚删掉的那张」就不作数了**：那一摞栈已经矮了一格。
        # 无论撤的是哪一种（下面三支），这一条都只对紧接着的那一次有效。
        trashed_in_detail, self._detail_trashed = self._detail_trashed, None
        if result.action == ACTION_METADATA:
            # 撤销位置编辑：照片回到原样，但**大小与修改时间又变了一次**（撤销本身也是
            # 一次写回），所以网格里那一行、内存缓存、分组排序全都要跟上 —— 与正向那次
            # 走的是同一个 ``_after_metadata_write``。**它不是删除**：撤的是字段，
            # 照片一直在那儿，所以「跳回刚删掉的那张」与它无关。
            for entry in result.entries:
                self._after_metadata_write(entry)
            self._show_banner(banners.undo_banner(result))
            self._refresh_undo_label()
            return
        if result.action == ACTION_RENAME and result.replaced is not None:
            # 撤销之前，这一行挂在改完名的新路径上 —— 要按它找行号。
            row = self._model.row_of_path(result.replaced.path)
            self._apply_rename(
                RenameResult(
                    entry=result.entries[0],
                    previous=result.replaced,
                    renamed=True,
                    journaled=result.journaled,
                ),
                row,
                banner=banners.undo_banner(result),
            )
        else:
            self._apply_restore(result)
            self._jump_back_to_restored(trashed_in_detail, result)
            self._show_banner(
                banners.undo_banner(result),
                auto_hide=result.journaled and not result.failures,
            )
        self._refresh_undo_label()

    def _jump_back_to_restored(self, trashed: Path | None, result: UndoResult) -> None:
        """大图页上删掉一张之后按 ⌘Z：**跳回刚被删掉的那一张**（ADR 0021 的另一半）。

        删掉一张之后界面会接着看下一张（看照片时最顺手的动作就是「这张不要，下一张」），
        于是撤回来时人正站在**另一张**上；而撤销会整层重排，刚取回来的那张完全可能落在
        视野之外。「跳回去」才是用户按 ⌘Z 时真正想要的 —— 看回刚才那张。

        两种情形都要接住：

        - **还在大图页**：换到刚取回来的那一张；
        - **因为这一层空了已经从大图页退回网格**（见 :meth:`_show_next_after_trash`）：
          重新打开它 —— 删除是在大图页上做的，撤销理应把人放回原处。

        **在网格页上删的不动**：``trashed`` 是 ``None``（见 :meth:`trash_selected`），
        ⌘Z 在网格上就该留在网格上。取回来的条目里没有那一张（撤的是别的一次操作、
        或者它已经不在当前这一层）时同样不动 —— 跳到一个不相干的地方比不跳更糟。

        **只对紧接着的那一次撤销有效**：每一次删除都会把 ``_detail_trashed`` 重写一遍
        （在网格页上删的写成 ``None``），而这一次撤销之后它就被用掉了 —— 于是第二次
        ⌘Z 撤的是别的一次操作时，不会被上一次留下的那条带跑。
        """
        if trashed is None or not any(entry.path == trashed for entry in result.entries):
            return
        row = self._model.row_of_path(trashed)
        if row < 0 or self._model.photo_at(row) is None:
            return
        self._current_row = row
        # 网格与大图从此指着同一格（与 ``_show_next_after_trash`` 那条约定一致）。
        self._grid.select_row(row, ensure_visible=False)
        if self._stack.currentWidget() is not self._detail_page:
            self._stack.setCurrentWidget(self._detail_page)
            self._detail.setFocus()
            self._update_action_states()
        self._load_detail(row)

    def _apply_restore(self, result: UndoResult) -> None:
        """把撤销删除取回来的条目放回网格，然后按当前分组 / 排序重排一次。

        重排而不是就地插行：一张照片取回来之后该落在哪，取决于当前的排序字段与方向
        （逆序时光标插到「最后一个更大值之后」恰恰是错的位置）。整层重排一次是
        几十微秒的事，而它给出的位置一定是对的。
        """
        if not result.entries:
            return
        self._note_self_write(*(entry.path for entry in result.entries))
        for entry in result.entries:
            self._model.insert_entry(entry)
        self._apply_ordering()
        restored = [self._model.row_of_path(entry.path) for entry in result.entries]
        first = min((row for row in restored if row >= 0), default=-1)
        if first >= 0:
            self._grid.select_row(first, ensure_visible=False)
        self._refresh_thumbnail_window()

    def _apply_rename(self, result: RenameResult, row: int, *, banner: str | None = None) -> None:
        """把一次改名（或撤销）同步进模型与各级缓存。"""
        stale = result.previous.path
        self._preview_cache.pop(stale, None)
        self._metadata_cache.pop(stale, None)
        self._note_self_write(stale, result.entry.path)
        if not result.renamed:
            self._grid.select_row(row, ensure_visible=False)
            return
        if row >= 0 and self._model.replace_entry(row, result.entry):
            self._grid.select_row(row, ensure_visible=False)
        else:
            self._reload_level()
        # 大图页上标题栏那一行显示的就是这个名字 —— 改完不刷新的话，用户盯着标题栏
        # 改完却看到旧名字（⌘Z 撤回时同理，ADR 0025 决策 4）。
        self._refresh_title()
        # 记上了才说过就走：没记上时那句话里有一句「⌘Z 撤销不了」，那是用户必须知道的
        # （ADR 0029 决策 3）。撤销回来时传进来的是撤销那句话，判据一样成立。
        self._show_banner(banner or banners.rename_banner(result), auto_hide=result.journaled)


__all__ = [
    "GROUP_NOT_FILE_ACTION",
    "NOTHING_SELECTED",
    "WriteOpsMixin",
    "flattened_entries",
    "shared_parent",
]
