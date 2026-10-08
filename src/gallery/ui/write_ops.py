"""界面上**会动用户文件**的那些入口，全在这儿（红线 1）。

界面上所有调 ``rename_entry`` / ``move_to_trash`` / ``copy_into`` / ``duplicate`` /
``undo_last_write`` 的地方都只许出现在这个文件里（守护测试按此钉住）。**它不碰文件**：每个写操作
都走 ``self._library`` 上的方法 —— 界面决定**时机**，``core`` 保证**后果**（必记 journal、
可 ⌘Z 撤销）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QMenu

from gallery.core import paths
from gallery.core.errors import GalleryError
from gallery.core.library.config import BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.metadata.capture_time import CaptureTime
from gallery.core.metadata.exif import read_metadata
from gallery.core.metadata.journal import (
    ACTION_EVENT,
    ACTION_EVENT_DELETE,
    ACTION_METADATA,
    ACTION_RENAME,
)
from gallery.core.metadata.metadata_edit import MetadataEdit, MetadataState
from gallery.core.metadata.paste import PasteResult
from gallery.core.metadata.place_edit import PlaceState
from gallery.core.metadata.rename import RenameResult
from gallery.core.metadata.report import MetadataReport
from gallery.core.metadata.transfer import MoveResult
from gallery.core.metadata.undo import UndoResult
from gallery.ui import banners, desktop, menus
from gallery.ui.panels.metadata_edit import MetadataEditDialog
from gallery.ui.tasks import TaskContext

logger = logging.getLogger(__name__)


#: 选中分组卡片而要改的却是**卡片本身**时的那句解释（ADR 0020）。
#: 摊平之后只有「改名」还会走到这里 —— 卡片不是磁盘上的东西，改不了名也删不掉。
GROUP_NOT_FILE_ACTION = (
    "分组不是磁盘上的文件，改不了名也删不掉。双击进入分组后选中里面的照片再操作。"
)


#: 摊平之后一个路径都没剩下（正常情况下不会：组卡片必带至少一张照片）。
NOTHING_SELECTED = "没有可处理的东西。"


def shared_parent(photos: Sequence[PhotoRef]) -> Path | None:
    """这组照片**全都在同一个相册**里时返回那个相册，否则 ``None``（分组不是文件夹，只有底
    下碰巧只有一个相册时才有「粘到哪去」可言，ADR 0018 决策 1）。

    **扫到第二个不一样的目录就收工**，而不是把整组的父目录收成集合再解析一遍 —— 后者在分组
    视图下每次动作状态刷新都要走一遍（三万张散在一百个目录里 31.4 ms/次）。
    改成一趟、遇到第二个**解析后**不同的目录就返回 ``None``：那已经是定论，剩下的不必看。

    **早退只能按解析后的值判**：两个写法不一样的父目录可能指向同一个地方（``a/./b``、
    符号链接），只比原始路径会把那种情形判成「不在同一个相册」。
    """
    if not photos:
        return None
    first = photos[0].path.parent
    # 全都在同一个原始父目录时返回的是**原始**那个（不是解析后的）—— 原实现就是这个语义。
    resolved_first: Path | None = None
    for photo in photos:
        parent = photo.path.parent
        if parent == first:
            continue
        if resolved_first is None:
            resolved_first = paths.resolved(first)
        if paths.resolved(parent) != resolved_first:
            return None
    return first if resolved_first is None else resolved_first


def flattened_entries(entries: Sequence[GridEntry]) -> list[GridEntry]:
    """把选区里的**分组卡片换成组员**（按路径去重、保持网格顺序），相册保持相册。

    卡片不是磁盘上的东西、它代表的那批照片是，所以「对选区做点什么」的命令都该作用在摊平后的
    结果上（ADR 0020）。**不动选区** —— 界面上仍然是那张卡片被选着。
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
    """**会动用户文件的那些入口** —— 粘贴 / 新建相册 / 复制与移动到相册 / 改名 /
    编辑元数据 / 移到废纸篓 / 撤销。

    **红线 1 在代码里就靠这一个文件变成一句可查的话**：``tests/test_guards.py`` 扫的是
    「调用 ``Library.rename_entry`` / ``move_to_trash`` / ``copy_into`` / ``move_into`` /
    ``create_album`` / ``undo_last_write`` / ``write_metadata_edit`` 的地方只许是这一个
    文件」。别的模块要动用户文件，只能从这里进。

    而它**自己一个文件都不碰**（另一条守护盯着）：它只决定「什么时候调、成功了怎么改
    界面」，怎么动、怎么记账、怎么撤销全在 ``core/metadata/*``（红线 4）。
    """

    def _paste_target(self) -> Path | None:
        """⌘V 要粘进哪个文件夹 —— **先看选区，再看这一层**（ADR 0018 决策 1、决策 7）。

        **恰好选中一个相册**时粘进那个相册内部（访达的「粘贴项目」）；其余情况都粘进当前这一
        层：多选时说不清「粘进哪一个相册」。没选中相册时判据**按磁盘上的真实位置**算 —— 分组
        不是文件夹，只有当这一组照片本来就都在同一个相册里才算有目录（否则 ⌘V 置灰）。
        """
        album = self._selected_album()
        if album is not None:
            return album
        if self._group_view is not None:
            return shared_parent(self._group_view.photos)
        return self._level_directory()

    def _selected_album(self) -> Path | None:
        """选区里**恰好是一个相册**时返回它，否则 ``None``（判据见 :meth:`_paste_target`）。

        **直接读真选区，不走 :meth:`PhotoGrid.selected_rows`**（那一位在选区为空时会退回当前
        项），也**不算大图页的选区**（ADR 0021）—— 粘文件进用户没选的地方是最坏的一种错。
        """
        if not self._on_grid_page():
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

        相册模式是当前这一层的文件夹（停在顶层就是库根），全部照片是库根，**分组里是 ``None``**
        （新建出来的相册卡片插进那一层就是错位，ADR 0022 决策 1）；「粘到哪去」与「建到哪去」
        共用这一个判据。
        """
        library = self._library
        if library is None or self._group_view is not None:
            return None
        if self._browse_mode == BROWSE_MODE_ALBUMS:
            return self._current_dir or library.root
        return library.root

    def _can_create_album(self) -> bool:
        """此刻建不建得了相册：得有「当前这一层」这个目录（建不了就不出现，ADR 0014 决策 4）。"""
        return self._level_directory() is not None

    def _can_paste(self) -> bool:
        """此刻按 ⌘V 有没有用：得有可粘的目录，剪贴板里也得**真有文件**（读一次约 1 微秒）。"""
        return self._paste_target() is not None and bool(desktop.read_files_from_clipboard())

    def paste_into_current_folder(self) -> None:
        """「粘贴」⌘V：把剪贴板里的文件**复制**进当前这一层的目录（ADR 0018）。

        **粘到哪里只看选区**（:meth:`_paste_target`）：选中了一个相册就粘进它内部，否则粘进当前
        这一层。菜单里那一条「粘贴」也是这个动作，因此按 ⌘V 与点它落在同一个目录上。
        """
        target = self._paste_target()
        if target is None:
            self._show_banner("这里没有可以粘贴进去的文件夹。")
            return
        self._paste_into(target)

    def _paste_into(self, target: Path) -> None:
        """把剪贴板里的文件复制进 ``target``。⌘V 与右键菜单里那一条共用这一段。

        复制在后台线程上跑（一次几百 MB）。**新粘进来的照片由监听器自己发现并上屏**
        （ADR 0013）：重扫会把网格清空、选中全丢。
        """
        library = self._library
        if library is None:
            return
        sources = desktop.read_files_from_clipboard()
        if not sources:
            self._show_banner("剪贴板里没有可粘贴的文件。")
            return
        if self._write_job is not None:
            # 两次写入同时跑会争同一个名字（各自先看「一张 2.jpg」在不在），后到的那个会把
            # 先到的盖掉 —— 粘贴、复制到相册、移动到相册共用这一道闸门。
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

    def duplicate_selected(self) -> None:
        """「复制」⌘D：把选中的每一项**在原位置**造一份副本（ADR 0044）。

        目标目录不用挑 —— 每项目标就是它自己的父目录，所以选区跨了几个相册都能一次做完。
        选中的分组卡片摊平成组员一起复制，与卡片上的「移到废纸篓」同一套语义。

        **新副本不进撤销栈**（撤销一次复制就是删文件，红线 1），横幅因此不承诺 ⌘Z；
        **不选中新副本** —— 新文件由监听器（ADR 0013）自己上屏，而那要等它扫到。
        """
        library = self._library
        if library is None:
            return
        sources = self._selected_paths()
        if not sources:
            self._show_banner(NOTHING_SELECTED)
            return
        if self._write_job is not None:
            # 与粘贴 / 复制到相册 / 移动到相册共用这一道闸门（ADR 0019 决策 6）。
            self._show_banner("上一次操作还没结束。")
            return

        def work(context: TaskContext) -> object:
            return library.duplicate(sources, should_cancel=context.is_cancelled)

        self._show_banner(f"正在复制 {len(sources)} 份副本…")
        self._write_job = self._runner.submit(
            work,
            on_result=self._on_duplicate_done,
            on_error=self._on_duplicate_failed,
        )

    def _on_duplicate_done(self, payload: object) -> None:
        self._write_job = None
        if isinstance(payload, PasteResult):
            # 判据与粘贴同一套（ADR 0029 决策 3）：失败项与「没记下账」都要留在屏幕上。
            self._show_banner(
                banners.duplicate_banner(payload),
                auto_hide=payload.journaled and not payload.failures,
            )

    def _on_duplicate_failed(self, message: str) -> None:
        self._write_job = None
        logger.info("复制副本失败：%s", message)
        self._show_banner(f"没能复制：{message.rstrip('。')}")

    def create_album(self) -> None:
        """「新建相册」⇧⌘N：在当前这一层建一个空相册，**建完立刻进行内重命名**。

        **不等监听器**（ADR 0013）：它要等文件系统事件跑一趟，而行内重命名必须马上有那一格可改
        —— 自己插进模型并记进「自家写操作」，监听器报上来的同一件事就不会被重复应用。
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
        self._model.insert_entry(
            created.album,
            whole_level=self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT,
        )
        # 重排后再问行号，**不要拿 insert_entry 交回来的那个**：那一行是按「同名相邻、升序」
        # 找的，而相册卡片的实际方向跟着「逆序」（ADR 0049）—— 勾了逆序时它是反的。
        self._apply_ordering()
        row = self._model.row_of_path(created.path)
        if row < 0:
            # 插不进去（理论上不会：这一层里还没有同一个路径）。宁可重扫一次，
            # 也不能让磁盘上多了一个文件夹而界面上看不见。
            self._reload_level()
            self._show_banner(f"已新建相册「{created.name}」。", auto_hide=True)
            return
        self._grid.select_row(row)
        # 空文件夹里建了相册之后，「这一层是空的」那句提示就该收起来了（判据是网格上有没有
        # 条目）。监听器不会替我们做这件事：自家写的那次已经被摘掉。
        self._update_level_notice()
        self._refresh_thumbnail_window()
        if not created.journaled:
            # 文件夹已经在手上了，缺的只是一条历史。如实说，别让用户以为「随时可查」。
            self._show_banner("没能记下操作日志。")
        self._grid.begin_rename(self._model.index(row, 0))

    # ---- 相册封面（ADR 0028）-------------------------------------------------
    # 这一段写的是 ``config.toml``：用户的照片一个字节都不动，因此不触犯红线 1，也**不进撤销
    # 栈**（撤销走相册右键菜单里「取消相册封面」那一条对称的命令）。收在这个文件里是因为它
    # 同样是「用户按了一下之后才发生的写入」。

    def cover_candidate(self) -> PhotoRef | None:
        """「设为相册封面」此刻作用于哪张照片；不该出现这一条时返回 ``None``。

        三道判据缺一条就**不出现**（不是置灰，ADR 0014 决策 4）：恰好选中一张照片（分组卡片不
        算）、它属于某个相册、父目录确实还在。
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
        """「取消相册封面」此刻作用于哪个相册；没指定过封面就返回 ``None``（那条就不出现）。"""
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

        **相册是照片的父目录，不问「现在站在哪一层」**；不走后台线程（改一个键值对再落一次盘，
        与 ``mkdir`` 同量级）。
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

        **两处记忆都要清**：模型里已经缩放好的那张（否则网格一直挂着旧封面）与流水线里对这个
        相册的记忆（少了「里面读不出图」那一条，原本空着的相册会永远停在文件夹图标上）。
        """
        self._model.forget_thumbnails([album])
        if self._pipeline is not None:
            self._pipeline.forget([album])
        self._refresh_thumbnail_window()

    # ---- 元数据编辑（ADR 0030 / 0031 / 0032）---------------------------------------
    # **本项目里第一个真正改动用户照片内容的入口**（其余那些改的是文件名与所在目录）：写之前
    # 要拿到那份元数据（填对话框、也是回滚与撤销的依据），写之后要把界面上所有按「路径 + 大小
    # + 修改时间」认照片的地方一起换掉。

    def metadata_edit_candidate(self) -> PhotoRef | None:
        """「编辑元数据」此刻作用于哪张照片；不该出现这一条时返回 ``None``（判据同
        :meth:`cover_candidate`：**恰好是一张照片**且此刻改得动，即装了 exiftool）。"""
        library = self._library
        if library is None or not library.can_write_metadata():
            return None
        entry = self._model.entry_at(self._focus_row())
        return entry if isinstance(entry, PhotoRef) else None

    def edit_metadata_of_current(self) -> None:
        """「编辑元数据…」：先把元数据读到手，再弹对话框，保存后写回。

        面板没展开时多半没有缓存，**现读一次**（约 50ms，走后台线程）—— 在主线程上直接读会让
        界面卡一下。
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
        """摆出编辑器。**对话框只产出「用户填的那一份」，写盘在下面那一步**；拍摄时间与位置
        **一起**从这份报告里取（ADR 0032）。"""
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

        **写盘是同步的**（exiftool 一次约 50~200ms）；**时间与位置一次写回**（ADR 0032 决策 4）：
        按一次保存得到的是一条日志、一下 ⌘Z、一句横幅。
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

        1. **屏蔽监听器回声**（ADR 0013）：否则监听器会把这份缓存整个作废并重取。
        2. **丢掉按路径索引的内存缓存**：元数据与整图预览都拿「路径 + 大小 + 修改时间」当键。
        3. **换掉网格里那一行的引用**（它还挂着旧的大小与修改时间），再**重排** —— 这一张可能
           已经不属于当前的分组 / 排序位置了（就地插行会插到错位置）。
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

    def _album_picker(
        self, title: str, supplier: Callable[[], Sequence[tuple[str, Path]]]
    ) -> QMenu:
        """拿一个「到展开那一刻才现列」的相册子菜单（ADR 0019）。

        **传的是 supplier 而不是清单本身**：列清单要递归走一遍整棵目录树，那是主线程上的 120 ms
        （3000 个相册），且付在「右键一下」的那一刻；挪到 ``aboutToShow`` 之后每次展开都重新问
        一次，访达里新建的相册下一次展开就能选到（给清单加缓存会让它退化成「有时看不到」）。
        """
        return menus.build_lazy_album_picker(title, supplier)

    def _album_targets(self) -> tuple[tuple[str, Path], ...]:
        """递归列出全库相册，标签是相对路径（``2025/苏州``）—— 两个年份下各有一个「苏州」时，
        光写名字用户分不清要挪到哪去（见 ``Library.album_label``）。"""
        library = self._library
        if library is None:
            return ()
        return tuple((library.album_label(folder.path), folder.path) for folder in library.albums())

    def _transfer_selected(self, target: Path, *, move: bool) -> None:
        """把选中的照片**复制**或**移动**进 ``target``。

        两者共用这一段，差别只在调哪个 ``Library`` 方法与横幅里的动词；**一次搬几百 MB**，所以
        走后台线程（与 ⌘V 同一道闸门）。选中的分组卡片摊平成组员（ADR 0020）。
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

    def _selected_entries(self, rows: Sequence[int] | None = None) -> list[GridEntry]:
        """「此刻这些命令作用于谁」—— 网格页是整个选区，大图页是正在看的那一张。

        **大图页不能问网格的选区**（翻图只动 ``_current_row``，网格的选区停在打开大图的那一格）
        —— 那样「在访达中显示」「拷贝路径」作用到的会是一张屏幕上根本没有的照片（ADR 0021）。

        :param rows: 已经问过一次网格选区的话把它传进来，**别再问第二遍** ——
            ``selected_rows`` 要为每一个选中项新建一个 ``QModelIndex``，实测三万张全选时
            一次 15.4 ms，而 :meth:`MainWindow._update_action_states` 一次里要问两遍
            （这一处与「改名」的判据各一遍）。那是纯重复劳动。
        """
        if self._in_detail_page():
            row = self._current_row
            entry = self._model.entry_at(row) if row >= 0 else None
            return [] if entry is None else [entry]
        return [
            entry
            for entry in (
                self._model.entry_at(row)
                for row in (self._grid.selected_rows() if rows is None else rows)
            )
            if entry is not None
        ]

    def _focus_row(self) -> int:
        """「当前这一格」的行号：网格页是当前项，大图页是正在看的那一张（ADR 0021）。"""
        if self._in_detail_page():
            return self._current_row
        return self._grid.current_row()

    def _selected_paths(self) -> list[Path]:
        """命令要作用在磁盘上的哪一串东西（按网格顺序，已去重）。

        **分组卡片在这里摊平成它的组员**（ADR 0020），于是「拷贝」「移到废纸篓」这些命令一条都
        不用改；摊平只发生在命令的对象上，不动选区。
        """
        return [entry.path for entry in flattened_entries(self._selected_entries())]

    def rename_current_entry(self) -> None:
        """菜单 / ⌘R：**大图页改窗口标题栏那一行，网格里仍是就地的行内编辑**（ADR 0025 决策 3）。

        两处改的是同一个写操作入口（``_on_rename_committed``），所以记账、横幅、撤销、监听器回声
        在两边一样都不少。
        """
        if self._in_detail_page():
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

        选中的**分组卡片**摊平成组员（ADR 0020）。不弹确认框，理由和访达一样：废纸篓本身就是那
        层保险，⌘Z 再是一层。**大图页上删的是正在看的那一张**，删掉之后接着看下一张（ADR 0021）。
        """
        library = self._library
        if library is None:
            return
        if self._grid.is_renaming():
            return  # 键盘正在行内输入框里，删掉正在编辑的那一张只会让人以为出了错
        in_detail = self._in_detail_page()
        if not in_detail and not self._on_grid_page():
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
        # 网格页上删的不记 —— 撤回来之后留在网格上才是对的。只记**真正移走的**那一张。
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

        ``_select_after_removal`` 落到的格正合「看下一张」；只有两种情况退回网格：那一格是相册或
        分组卡片，或者整层一张照片都不剩了。
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
        """这一行是不是已经不在磁盘上了（分组卡片没有路径可比：整组都进了废纸篓才算「这一行没
        了」，只走了一部分的话卡片留着，ADR 0020 决策 4）。"""
        entry = self._model.entry_at(row)
        if entry is None:
            return False
        if isinstance(entry, GroupRef):
            return bool(entry.photos) and all(photo.path in moved_paths for photo in entry.photos)
        return entry.path in moved_paths

    def _forget_trashed(self, entries: Sequence[GridEntry]) -> None:
        """丢掉内存里按路径索引的缓存。磁盘上的缩略图缓存**留着**：照片还在废纸篓里，撤销回来时
        直接命中（与改名的处理相反）。"""
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
        # **这一次撤销之后，「跳回刚删掉的那张」就不作数了**：那一摞栈已经矮了一格，无论撤的
        # 是哪一种（下面三支）都只对紧接着的那一次有效。
        trashed_in_detail, self._detail_trashed = self._detail_trashed, None
        if result.action in (ACTION_EVENT, ACTION_EVENT_DELETE):
            # 撤销一次事件改动（ADR 0043）：**网格里没有它的条目**，所以这里不碰模型，只把
            # 按事件分组的那些卡片重算一遍 —— 事件回来了/改回去了，组卡片就该跟着变。
            self._after_events_changed()
            self._show_banner(
                banners.event_undo_banner(result, "事件"),
                auto_hide=result.journaled and not result.failures,
            )
            self._refresh_undo_label()
            return
        if result.action == ACTION_METADATA:
            # guard: allow-long-comment（撤销本身也是一次写回，大小与修改时间又变了一次）
            # 撤销位置编辑：照片回到原样，但**大小与修改时间又变了一次**（撤销本身也是一次
            # 写回），所以网格里那一行、内存缓存、分组排序全都要跟上 —— 与正向那次走的是同一
            # 个 ``_after_metadata_write``。**它不是删除**：撤的是字段，照片一直在那儿。
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

        删掉之后界面会接着看下一张，而撤销会整层重排 —— 刚取回来的那张可能落在视野之外，所以
        还在大图页就换过去、已退回网格就重新打开它。**在网格页上删的不动**，**只对紧接着的那
        一次撤销有效**。
        """
        if trashed is None or not any(entry.path == trashed for entry in result.entries):
            return
        row = self._model.row_of_path(trashed)
        if row < 0 or self._model.photo_at(row) is None:
            return
        # 网格与大图从此指着同一格（与 ``_show_next_after_trash`` 那条约定一致）。
        self._grid.select_row(row, ensure_visible=False)
        if self._in_detail_page():
            self._current_row = row
            self._load_detail(row)
            return
        # 已经退回网格（删的是这一层最后一张）：**重新打开它** —— 照片帧要真的压回去，
        # 「在大图页」才是栈说了算，而不是画面上的一厢情愿。
        self._open_detail(self._model.index(row, 0))

    def _apply_restore(self, result: UndoResult) -> None:
        """把撤销删除取回来的条目放回网格，然后按当前分组 / 排序重排一次。

        重排而不是就地插行：取回来的那张该落在哪取决于当前的排序字段与方向；整层重排一次是几十
        微秒的事，给出的位置一定是对的。
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
        # 大图页上标题栏那一行显示的就是这个名字 —— 改完不刷新的话，用户改完却看到旧名字
        # （⌘Z 撤回时同理，ADR 0025 决策 4）。
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
