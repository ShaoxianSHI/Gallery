"""实时监听（ADR 0013）：磁盘上变了什么，就把它落到当前这一层上。

watchdog 的回调**不在主线程**，由排队连接搬回来；搬回来之后有三个坎（流式扫描还在跑、条目还
暂存在暂存区等着定顺序、**当前页不是网格页**），撞上就先暂存，等能改的那一刻一次补上。
还管两件容易漏的事：**自家写操作的回环**（改名 / 移到废纸篓 / 撤销之后监听器会再报一次同一件事，
见 :data:`WATCH_ECHO_TTL`）与**相册封面的作废、以及把事件绑定跟一次外部改名 / 移动**。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from gallery.core.events.store import follow_moves
from gallery.core.library.config import BROWSE_MODE_FLAT
from gallery.core.library.facade import Library
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import describe_entry
from gallery.core.library.watcher import ChangeKind, LibraryChange, summarize
from gallery.ui.window.state.pending_watch import PendingWatch

logger = logging.getLogger(__name__)

#: 自家写操作（改名 / 移到废纸篓 / 撤销）之后，监听器报回来的同一件事在这段时间内不再重复
#: 应用：漏掉一次回环会让同一行被改两遍；过期之后用户真的在访达里新建同名文件又该正常显示。
WATCH_ECHO_TTL = 2.0


class WatchFlowMixin:
    """外部文件变化怎么落到网格、内存缓存与索引上。

    「暂存着还没落地的那批」住在 :class:`~gallery.ui.window.pending_watch.PendingWatch` 里
    （ADR 0058 阶段 4）—— 这一间与 ``main_window`` / ``library_flow`` 都碰过那两个字段。
    """

    # 声明在类级别 —— 与 ``jobs.py`` 同一个理由（回调末尾把暂存放掉的那一句会被当成定义）。
    # **真存储在 :class:`PendingWatch` 里**，这两个是转发 —— 测试直接摸它们。
    @property
    def _watch_pending(self) -> tuple[LibraryChange, ...] | None:
        return self._pending_watch._watch_pending

    @_watch_pending.setter
    def _watch_pending(self, value: tuple[LibraryChange, ...] | None) -> None:
        self._pending_watch._watch_pending = value

    @property
    def _pending_group(self) -> str | None:
        return self._pending_watch._pending_group

    @_pending_group.setter
    def _pending_group(self, value: str | None) -> None:
        self._pending_watch._pending_group = value

    _pending_watch: PendingWatch

    def _init_pending_watch(self) -> None:
        self._pending_watch = PendingWatch()

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
        self._follow_moved_bindings(library, changes)
        if self._defer_watch_until_idle():
            self._pending_watch.put(changes)
            return
        self._apply_watch_changes(library, changes)

    def _follow_moved_bindings(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """外部改名 / 移动之后把事件绑定跟过去（ADR 0043 决策 8）。

        **在延后判断之前**：延后只该推迟「改界面」，不该推迟「修数据」—— 一批变化被暂存进
        ``_watch_pending`` 之后可能被直接丢掉（扫描一完成就 ``_watch_pending = None``
        再重扫那一层），而绑定一旦跟着界面状态走，那正是「照片还在、事件没了」的来路。
        **自家写操作已经搬过一次，这里搬不到什么**：改名的回环被滤掉，移动不屏蔽回环。"""
        follow_moves(
            library.events,
            (
                (change.path, change.dest_path, change.is_dir)
                for change in changes
                if change.kind is ChangeKind.MOVED and change.dest_path is not None
            ),
        )

    def _defer_watch_until_idle(self) -> bool:
        """现在还不能就地改网格吗？回答「是」，这批变化就该先暂存。

        三种时候不能就地改：扫描还在流式显示到界面、条目还在暂存区等着定顺序、**当前页不是
        网格页**（大图页与日历页都算 —— 日历页上摆的是整库，不能就地往网格里插行）。
        """
        return self._scan_job is not None or self._staging.is_staging or not self._on_grid_page()

    def _flush_pending_watch(self) -> None:
        """把之前暂存的变化补上：重排一结束、或者人回到网格，都是合适的时候。"""
        changes = self._pending_watch.take()
        if changes is None:
            return
        library = self._library
        if library is None:
            return
        if self._defer_watch_until_idle():
            self._pending_watch.put(changes)  # 还是不行，继续等
            return
        self._apply_watch_changes(library, changes)

    def _on_watch_failed(self, message: str) -> None:
        """监听本身出问题不该挡住浏览，但界面从此不是最新的，得说出来。"""
        logger.info("文件监听出错：%s", message)
        self._show_banner(
            f"{message.rstrip('。')}。界面暂时不会自动更新；按 ⌘R 可重新扫描当前层级，"
            "重新打开库可恢复自动更新。"
        )

    def _apply_watch_changes(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """按当前这一层的形状，把一批变化落到网格、内存缓存与索引上。"""
        #: 内部变了、封面要重找的相册。**先算、先作废** —— 下面两条路都会重新要一遍缩略图，
        #: 而那份「这个相册是空的」记忆会让 :meth:`ThumbnailPipeline._dispatch` 当场跳过。
        covers: set[Path] = set()
        for change in changes:
            covers.update(self._albums_touched_by(change))
        if covers:
            self._refresh_album_covers(covers)
        # 索引的文件表**整库口径**跟上（不只当前这一层）—— 两条路都要，见 :meth:`_sync_index`。
        self._sync_index(library, changes)
        if self._conditions:
            # guard: allow-long-comment（有条件时增量逻辑接不上，两种作用域都重跑一遍）
            # 有条件时增量逻辑一律接不上，于是两种作用域都重跑一遍：整库检索重跑一次查询，筛选
            # 重新枚举这一层再收窄一次（ADR 0040 决策 6）。真增量仍要逐张判断「新照片命中吗」，
            # 那还是得查索引，而 ``_reload_level`` 已经把整条链接通了。
            self._apply_search_changes(changes)
            return
        added: list[GridEntry] = []
        gone: list[Path] = []
        #: 内存里缓存作废的路径（改名、内容变了、照片没了）。
        stale: set[Path] = set()

        for change in changes:
            if change.kind is ChangeKind.MOVED and change.dest_path is not None:
                self._apply_watch_move(change, added, gone, stale)
                continue
            if change.kind is ChangeKind.REMOVED:
                if self._is_on_current_level(change.path):
                    gone.append(change.path)
                    stale.add(change.path)
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
            if self._is_on_current_level(change.path):
                added.append(entry)
                # 「这一格已经在了」也要当成文件变了：云盘同步中的照片会先以半截的样子出现
                # （解码失败），等下完了再报一次「创建」—— 不换新引用，那份「读不出来」就永远不清。
                if self._model.row_of_path(change.path) >= 0:
                    stale.add(change.path)

        rows = sorted(row for path in gone if (row := self._model.row_of_path(path)) >= 0)
        if rows:
            self._model.remove_paths(gone)
            self._select_after_removal(rows)
        if stale:
            self._forget_caches(stale)
        if not added and not gone:
            self._update_level_notice()
            self._refresh_thumbnail_window()
            return
        if self._level_is_grouped():
            # 这一层按分组铺开（卡片或分区），照片的归属是派生出来的，所以只能整层重算
            # —— 「就地把新照片并进某一组」不成立（ADR 0013 决策 3 的那条例外）。
            self._reload_level()
            return
        if added:
            whole_level = self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT
            for entry in added:
                self._model.insert_entry(entry, whole_level=whole_level)
        if self._group_view is not None and any(not isinstance(e, FolderRef) for e in added):
            # 组里的成员是上一次分组算出来的快照，新照片该不该进这个组只能重算。
            self._reenter_group_later(self._group_view.key)
            return
        # 新照片要读一次 EXIF 才排得对；其余照片命中索引，不会重读。
        self._start_facet_read()
        self._update_level_notice()
        self._refresh_thumbnail_window()

    def _apply_search_changes(self, changes: Sequence[LibraryChange]) -> None:
        """条件生效时这一层的变化：索引已由 :meth:`_sync_index` 跟上，这里重跑一次条件。

        **两种作用域都走这里**（ADR 0040 决策 6）：重跑时读的是索引，所以索引必须先更新 ——
        不先更新的话新照片筛不到、删掉的照片还在结果里。变化落在两层之下时**照样重跑**：
        条件能不能命中它取决于条件本身。
        """
        stale = {path for change in changes for path in change.touches}
        if stale:
            self._forget_caches(stale)
        self._apply_conditions()

    def _sync_index(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """把这一批变化落到索引的文件表上（**整库口径**，不只当前这一层）。

        监听器那条路原先只在「有检索条件」时才更新文件表，于是没有条件时新来的照片进不了检索 ——
        而打开库那一遍过去只在表为空时才跑，补不上，于是搜索永远找不到它们。**删掉 / 改名走掉的
        那一端按事件说的算**（那时文件已经不在了），新增 / 改名来到的那一端按磁盘实况算。变化落在
        当前这一层之外时照样跟上，因为搜索看的是整个库。
        """
        gone: list[Path] = []
        fresh: list[PhotoRef] = []
        for change in changes:
            if change.kind is ChangeKind.MOVED and change.dest_path is not None:
                gone.append(change.path)
                entry = describe_entry(library.root, change.dest_path)
                if isinstance(entry, PhotoRef):
                    fresh.append(entry)
                continue
            if change.kind is ChangeKind.REMOVED:
                gone.append(change.path)
                continue
            entry = describe_entry(library.root, change.path)
            if isinstance(entry, PhotoRef):
                fresh.append(entry)
            else:
                gone.append(change.path)
        if gone:
            library.forget_paths(gone)
        if fresh:
            library.remember_photos(fresh)

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
        """这个路径是不是当前这一层直接摆出来的一条。

        **筛选不在此列**（ADR 0040 决策 6）：筛选时这一层仍然是它自己，判据照旧按浏览方式算 ——
        「筛选在相册模式下会不会把别的相册的新照片也收进来」由筛选那一遍的相交负责。
        """
        library = self._library
        if library is None:
            return False
        if self._is_library_search():
            return True  # 检索横跨整个库（ADR 0026 决策 1）
        if self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT:
            return True  # 平铺与分组：整棵子树都摆在屏幕上
        return path.parent == (self._current_dir or library.root)

    def _albums_touched_by(self, change: LibraryChange) -> tuple[Path, ...]:
        """这次变化落在哪些相册**里面** —— 那些相册的封面要重新找一次。

        **答案不看「那个相册是不是正好在屏幕上」**：模型是换层时重扫出来的，流水线上
        「这个相册是空的」那份记忆却活过换层；不作废，那张卡片会一直停在文件夹图标上。
        **一路问到库根**：往空的 ``A/B`` 里丢一张，``A`` 的封面也跟着变。
        """
        return (*self._albums_above(change.path), *self._albums_above(change.dest_path))

    def _albums_above(self, path: Path | None) -> tuple[Path, ...]:
        """``path`` 所在的那个相册，以及它上面每一层相册（到库根为止）。

        库外的路径给不出任何东西（监听器把移出库的那一端解析成绝对路径，见
        :func:`gallery.core.library.watcher._normalize`）：第一层就出库时 ``found`` 还是空的，
        往上走过了库根时停在已经记下的那几层上。
        """
        library = self._library
        if library is None or path is None:
            return ()
        root = library.root
        found: list[Path] = []
        folder = Path(path).parent
        while folder != root:
            try:
                folder.relative_to(root)
            except ValueError:
                break
            found.append(folder)
            folder = folder.parent
        return tuple(found)

    def _refresh_album_covers(self, albums: Iterable[Path]) -> None:
        """相册内容变了：让封面重新找一次。**不论那张卡片此刻在不在这一层上**（理由见
        :meth:`_albums_touched_by`）。"""
        self._forget_caches(albums)

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
        if not self._in_detail_page() or self._current_row < 0:
            return
        row = min(self._current_row, self._model.rowCount() - 1)
        photo = self._model.photo_at(row)
        if photo is not None and photo.path in stale:
            self._focus_state.set_current(row)
            self._load_detail(row)

    def _reenter_group_later(self, key: str) -> None:
        """分组视图里来了新照片：整个库重新分组一次，之后再回到同一个组。

        否则组卡片上的张数会一直停在快照那一刻。**这一趟不是用户自己退出去的**，所以停着的那
        一格要记下来，等回到同一个组时重新选中 —— 不然库里只是多了一张照片，人正在看的那张
        就被甩掉了。
        """
        self._pending_watch.remember_group(key)
        self._focus_state.remember_pending(self._shown_entry_key())
        # 栈：退到分组之前那一层（分组的帧被弹掉，而**底下那层原样留着**）。
        self._pop_to(lambda stack: self._group_view is None)
        self._reload_level()

    def _maybe_reenter_group(self) -> None:
        """重排之后回到刚才那个组（它不在了就留在这一层，由横幅说明为什么）。"""
        key = self._pending_watch.forget_group()
        focus = self._focus_state.take_pending()
        if key is None:
            return
        for entry in self._model.entries():
            if isinstance(entry, GroupRef) and entry.key == key:
                self.enter_group(entry, focus=focus)
                return

    def _note_self_write(self, *paths: Path) -> None:
        """记下自家刚动过的路径：监听器随后报上来的同一件事不重复应用（ADR 0013）。

        **撤销也走这里**（:meth:`~gallery.ui.write_ops.WriteOpsMixin._apply_restore`），
        这一条决定 :data:`WATCH_ECHO_TTL` 该取多少。代价要说清：撤销改名之后那
        ``WATCH_ECHO_TTL`` 秒内，用户**真的**从外面改了同一个文件，那次变化会被当成回环
        吞掉，直到下一次扫描才补上；取小窗口就短、取大就少漏一次。"""
        deadline = time.monotonic() + WATCH_ECHO_TTL
        for path in paths:
            self._watch_echoes[Path(path)] = deadline

    def _is_echo(self, change: LibraryChange) -> bool:
        """这次变化是不是自家写操作的回环。"""
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
