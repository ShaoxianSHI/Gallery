"""实时监听（ADR 0013）：磁盘上变了什么，就把它落到当前这一层上。

watchdog 的回调**不在主线程**，所以先由排队连接搬回来。搬回来之后有三个坎（流式扫描还在跑、
条目还攒在暂存区等着定顺序、人正看着大图），撞上就**攒着**，等能改的那一刻一次补上。

还管两件容易漏的事：**自家写操作的回声**（改名 / 移到废纸篓 / 撤销之后监听器会再报一次同一
件事，见 :data:`WATCH_ECHO_TTL`）与**相册封面的作废**（封面是「里面第一张照片」）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from gallery.core.library.config import BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT
from gallery.core.library.facade import Library
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import describe_entry
from gallery.core.library.watcher import ChangeKind, LibraryChange, summarize

logger = logging.getLogger(__name__)

#: 自家写操作（改名 / 移到废纸篓 / 撤销）之后，监听器报回来的同一件事在这段时间内不再重复
#: 应用：漏掉一次回声会让同一行被改两遍；过期之后用户真的在访达里新建同名文件又该正常显示。
WATCH_ECHO_TTL = 2.0


class WatchFlowMixin:
    """外部文件变化怎么落到网格、内存缓存与索引上。"""

    # ---- 实时监听（ADR 0013）------------------------------------------------
    def _on_watch_changed(self, payload: object) -> None:
        """把一批外部变化落到当前这一层上（**主线程**：排队连接搬过来的）。"""
        library = self._library
        if library is None or not isinstance(payload, tuple):
            return
        changes = tuple(
            change
            for change in payload
            if isinstance(change, LibraryChange) and not self._is_echo(change)
        )
        if not changes:
            return
        logger.info("磁盘上有变化：%s", summarize(changes))
        if self._defer_watch_until_idle():
            waiting = self._watch_pending or ()
            self._watch_pending = (*waiting, *changes)
            return
        self._apply_watch_changes(library, changes)

    def _defer_watch_until_idle(self) -> bool:
        """现在还不能就地改网格吗？回答「是」，这批变化就该先攒着。

        三种时候不能就地改：扫描还在流式上屏、条目还攒在暂存区等着定顺序、人正看着大图。
        """
        return (
            self._scan_job is not None
            or self._staged is not None
            or self._stack.currentWidget() is not self._grid_page
        )

    def _flush_pending_watch(self) -> None:
        """把之前攒下的变化补上：重排一结束、或者人回到网格，都是合适的时候。"""
        changes, self._watch_pending = self._watch_pending, None
        if changes is None:
            return
        library = self._library
        if library is None:
            return
        if self._defer_watch_until_idle():
            self._watch_pending = changes  # 还是不行，继续等
            return
        self._apply_watch_changes(library, changes)

    def _on_watch_failed(self, message: str) -> None:
        """监听本身出问题不该挡住浏览，但界面从此不是最新的，得说出来。"""
        logger.info("文件监听出错：%s", message)
        self._show_banner(f"{message}（界面暂时不会自动更新，重新打开这个库即可恢复）")

    def _apply_watch_changes(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """按当前这一层的形状，把一批变化落到网格、内存缓存与索引上。"""
        if self._conditions:
            # 检索结果是横跨整个库的一层，增量逻辑接不上；重跑一次几十毫秒，比改三条分支便宜。
            self._apply_search_changes(library, changes)
            return
        added: list[GridEntry] = []
        gone: list[Path] = []
        #: 内存里缓存作废的路径（改名、内容变了、照片没了）。
        stale: set[Path] = set()
        #: 内部变了、封面可能要重找的相册。
        covers: set[Path] = set()

        for change in changes:
            if change.kind is ChangeKind.MOVED and change.dest_path is not None:
                self._apply_watch_move(change, added, gone, stale)
                continue
            if change.kind is ChangeKind.REMOVED:
                if self._is_on_current_level(change.path):
                    gone.append(change.path)
                    stale.add(change.path)
                else:
                    covers.update(self._album_covering(change.path))
                continue
            entry = describe_entry(library.root, change.path)
            if entry is None:
                continue  # 已经不在了，或者压根不是照片：这一条什么也做不了
            if change.kind is ChangeKind.MODIFIED:
                stale.add(change.path)
                if self._is_on_current_level(change.path):
                    row = self._model.row_of_path(change.path)
                    if row >= 0:
                        self._model.replace_entry(row, entry)
                        continue
                covers.update(self._album_covering(change.path))
                continue
            if self._is_on_current_level(change.path):
                added.append(entry)
                # 「这一格已经在了」也要当成文件变了：云盘同步中的照片会先以半截的样子出现
                # （解码失败），等下完了再报一次「创建」—— 不换新引用，那份「读不出来」就永远不清。
                if self._model.row_of_path(change.path) >= 0:
                    stale.add(change.path)
            else:
                covers.update(self._album_covering(change.path))

        rows = sorted(row for path in gone if (row := self._model.row_of_path(path)) >= 0)
        if rows:
            self._model.remove_paths(gone)
            library.forget_paths(gone)  # 索引里的孤儿行：照片不在了，那一行也不该留着
            self._select_after_removal(rows)
        if added:
            whole_level = self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT
            for entry in added:
                self._model.insert_entry(entry, whole_level=whole_level)
        if stale:
            self._forget_caches(stale)
        if covers:
            self._refresh_album_covers(covers)

        if added or gone:
            if self._group_view is not None and any(not isinstance(e, FolderRef) for e in added):
                # 组里的成员是上一次分组算出来的快照，新照片该不该进这个组只能重算。
                self._reenter_group_later(self._group_view.key)
                return
            # 新照片要读一次 EXIF 才排得对；其余照片命中索引，不会重读。
            self._start_facet_read()
        self._update_level_notice()
        self._refresh_thumbnail_window()

    def _apply_search_changes(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """检索这一层的变化：先把索引跟上，再重跑一次检索。

        **索引先跟上，界面后跟上**：重跑时读的就是它，不先更新的话新照片搜不到、删掉的照片还
        在结果里。变化落在两层之下时**照样重新检索**：条件能不能命中它取决于条件本身。
        """
        gone: list[Path] = []
        fresh: list[PhotoRef] = []
        stale: set[Path] = set()
        for change in changes:
            for path in (change.path, change.dest_path):
                if path is None:
                    continue
                entry = describe_entry(library.root, path)
                if isinstance(entry, PhotoRef):
                    fresh.append(entry)
                else:
                    gone.append(path)
            stale.update(change.touches)
        if gone:
            library.forget_paths(gone)
        if fresh:
            library.remember_photos(fresh)
        if stale:
            self._forget_caches(stale)
        self._apply_conditions()

    def _apply_watch_move(
        self,
        change: LibraryChange,
        added: list[GridEntry],
        gone: list[Path],
        stale: set[Path],
    ) -> None:
        """外部改名 / 移动：两端都在当前层就原地换那一格，否则一端加、一端删。"""
        library = self._library
        destination = change.dest_path
        if library is None or destination is None:
            return
        entry = describe_entry(library.root, destination)
        source_here = self._is_on_current_level(change.path)
        destination_here = self._is_on_current_level(destination)
        if source_here and destination_here:
            stale.update(change.touches)
            row = self._model.row_of_path(change.path)
            if row >= 0:
                if entry is None:
                    gone.append(change.path)  # 改着改着没了
                else:
                    self._model.replace_entry(row, entry)
            elif entry is not None:
                # 屏幕上没有它（旧名字不在这一层里）：新位置在这一层，就当新来的。
                added.append(entry)
            return
        if destination_here and entry is not None:
            added.append(entry)
        if source_here:
            gone.append(change.path)

    def _is_on_current_level(self, path: Path) -> bool:
        """这个路径是不是当前这一层直接摆出来的一条。"""
        library = self._library
        if library is None:
            return False
        if self._conditions:
            return True  # 检索横跨整个库（ADR 0026 决策 1）
        if self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT:
            return True  # 平铺与分组：整棵子树都摆在屏幕上
        return path.parent == (self._current_dir or library.root)

    def _album_covering(self, path: Path) -> tuple[Path, ...]:
        """变化发生在某个相册**内部**时，找出当前这一层里代表它的那个相册。

        相册的封面是「里面第一张照片」，所以往相册里丢新照片可能换封面 —— 那要把那一格已经
        画好的封面作废，让缩略图流水线重新去找一次。
        """
        library = self._library
        if library is None or self._browse_mode != BROWSE_MODE_ALBUMS:
            return ()
        current = self._current_dir or library.root
        try:
            relative = path.relative_to(current)
        except ValueError:
            return ()
        if len(relative.parts) < 2:
            return ()  # 就是这一层上的东西，不走这条路
        album = current / relative.parts[0]
        return (album,) if self._model.row_of_path(album) >= 0 else ()

    def _refresh_album_covers(self, albums: Iterable[Path]) -> None:
        """相册内容变了：让封面重新找一次。"""
        stale = {album for album in albums if self._model.row_of_path(album) >= 0}
        if stale:
            self._forget_caches(stale)

    def _forget_caches(self, paths: Iterable[Path]) -> None:
        """丢掉按路径索引的内存缓存（路径上已经没有文件，或者文件已经不是那个文件了）。

        磁盘上的缩略图缓存**不用动**：它的键含路径与 ``(大小, 修改时间)``，文件一改键就自然
        对不上，下次会重新解码（见 ``media.thumbs``）。
        """
        wanted = {Path(path) for path in paths}
        for path in wanted:
            self._preview_cache.pop(path, None)
            self._metadata_cache.pop(path, None)
        self._model.forget_thumbnails(wanted)
        if self._pipeline is not None:
            self._pipeline.forget(wanted)
        self._reload_open_photo(wanted)

    def _reload_open_photo(self, stale: set[Path]) -> None:
        """正看着的那张被改了，就重新载入它 —— 否则大图区与面板显示的还是旧值。"""
        if self._stack.currentWidget() is not self._detail_page or self._current_row < 0:
            return
        row = min(self._current_row, self._model.rowCount() - 1)
        photo = self._model.photo_at(row)
        if photo is not None and photo.path in stale:
            self._current_row = row
            self._load_detail(row)

    def _reenter_group_later(self, key: str) -> None:
        """分组视图里来了新照片：整个库重新分组一次，之后再回到同一个组。

        否则组卡片上的张数会一直停在快照那一刻。**这一趟不是用户自己退出去的**，所以停着的那
        一格要记下来，等回到同一个组时重新选中 —— 不然库里只是多了一张照片，人正在看的那张
        就被甩掉了。
        """
        self._pending_group = key
        self._pending_focus = self._shown_entry_key()
        self._group_view = None
        self._reload_level()

    def _maybe_reenter_group(self) -> None:
        """重排之后回到刚才那个组（它不在了就留在这一层，由横幅说明为什么）。"""
        key, self._pending_group = self._pending_group, None
        focus, self._pending_focus = self._pending_focus, None
        if key is None:
            return
        for entry in self._model.entries():
            if isinstance(entry, GroupRef) and entry.key == key:
                self.enter_group(entry, focus=focus)
                return

    def _note_self_write(self, *paths: Path) -> None:
        """记下自家刚动过的路径：监听器随后报上来的同一件事不重复应用（ADR 0013）。"""
        deadline = time.monotonic() + WATCH_ECHO_TTL
        for path in paths:
            self._watch_echoes[Path(path)] = deadline

    def _is_echo(self, change: LibraryChange) -> bool:
        """这次变化是不是自家写操作的回声。"""
        now = time.monotonic()
        for path, deadline in list(self._watch_echoes.items()):
            if deadline <= now:
                del self._watch_echoes[path]
        return any(path in self._watch_echoes for path in change.touches)

    def _was_self_written(self, path: Path) -> bool:
        """这个路径是不是自家刚刚写过的（改名 / 移到废纸篓 / 撤销）。"""
        now = time.monotonic()
        for known, deadline in list(self._watch_echoes.items()):
            if deadline <= now:
                del self._watch_echoes[known]
        return path in self._watch_echoes


__all__ = ["WatchFlowMixin"]
