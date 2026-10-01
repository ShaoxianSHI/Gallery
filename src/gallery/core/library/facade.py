"""库门面：界面层访问文件系统的唯一入口。

界面**不允许**自己 ``open()`` 原图，也不允许自己拼路径去读缓存 —— 一切都要经过这里，
这样「哪些操作会碰用户的文件、哪些只会碰缓存」在代码里始终是可审计的。

打开一个库（``Library.open``）是**只读**的：它不创建任何目录、不写任何文件。
创建配置目录是 ``gallery.core.library.config`` 里 ``prepare_library`` 的职责，
且必须由界面在用户同意后显式调用。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from gallery.core import paths
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library.config import LibraryConfig, load_config
from gallery.core.library.photos import FolderRef, GridEntry, PhotoRef
from gallery.core.library.scanner import ScanReport, iter_entries, iter_photos
from gallery.core.media.decode import RasterImage, decode_rgba
from gallery.core.media.thumbs import ThumbnailCache
from gallery.core.metadata.rename import RenameResult, rename_entry
from gallery.core.metadata.trash import TrashResult, trash_entries
from gallery.core.metadata.undo import UndoResult, next_undo_label, undo_last_write


class Library:
    """一个已打开的库。"""

    def __init__(
        self,
        root: Path,
        config: LibraryConfig,
        thumbnails: ThumbnailCache,
        *,
        cache_in_library: bool,
    ) -> None:
        self._root = root
        self._config = config
        self._thumbnails = thumbnails
        self._cache_in_library = cache_in_library

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        cache_in_library: bool = True,
        cache_base: Path | None = None,
    ) -> Library:
        """校验并打开库。

        :param cache_in_library: 缩略图缓存是否放在库内的配置目录里。
            用户拒绝写入照片文件夹时传 ``False``，缓存会落到系统目录
            （见 ADR 0001 决策 5），功能不降级。
        :param cache_base: 系统级缓存根目录，测试用；``None`` 表示走平台默认位置。
        :raises LibraryNotAccessibleError: 目录不存在或没有读取权限。
        """
        # 统一解析成绝对路径：命令行允许传相对路径（gallery 2025-苏州），
        # 而库路径会被记进会话、算进缓存摘要，相对路径换个工作目录就失效了。
        root = Path(root).expanduser().resolve()
        if not root.exists():
            raise LibraryNotAccessibleError(
                f"打不开这个文件夹：{root}。它可能已被移动、重命名或删除。",
                path=root,
            )
        if not root.is_dir():
            raise LibraryNotAccessibleError(
                f"这里不是一个文件夹：{root}。请选择一个包含照片的文件夹。",
                path=root,
            )
        if not os.access(root, os.R_OK | os.X_OK):
            raise LibraryNotAccessibleError(
                f"没有读取这个文件夹的权限：{root}。"
                "请在「系统设置 → 隐私与安全性 → 文件与文件夹」中授权后重试。",
                path=root,
            )

        config = load_config(root)
        directory = (
            paths.thumbnail_cache_dir(root)
            if cache_in_library
            else paths.external_thumbnail_cache_dir(root, base=cache_base)
        )
        thumbnails = ThumbnailCache(
            directory,
            max_edge=config.thumbnail_max_edge,
            max_bytes=config.max_cache_bytes,
        )
        return cls(root, config, thumbnails, cache_in_library=cache_in_library)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def display_name(self) -> str:
        """界面上显示的库名（通常是最后一级目录名）。"""
        return self._root.name or str(self._root)

    @property
    def config(self) -> LibraryConfig:
        return self._config

    @property
    def thumbnails(self) -> ThumbnailCache:
        return self._thumbnails

    @property
    def cache_in_library(self) -> bool:
        """缓存是否位于库内。为 ``False`` 时库目录保持零写入。"""
        return self._cache_in_library

    def iter_photos(
        self,
        *,
        should_cancel: Callable[[], bool] | None = None,
        report: ScanReport | None = None,
    ) -> Iterator[PhotoRef]:
        """按库配置递归（或不递归）遍历照片。"""
        return iter_photos(
            self._root,
            recursive=self._config.recursive,
            should_cancel=should_cancel,
            report=report,
        )

    def iter_entries(
        self,
        directory: Path | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        report: ScanReport | None = None,
    ) -> Iterator[FolderRef | PhotoRef]:
        """列出某一层的内容（相册在前，照片在后）。

        :param directory: 要列出的目录，默认是库根目录。
        :raises LibraryNotAccessibleError: 目录不存在，或不在库内 ——
            逐层浏览绝不能走出用户指定的库。
        """
        target = self._root if directory is None else Path(directory)
        if not paths.is_within(self._root, target):
            raise LibraryNotAccessibleError(
                f"不能浏览库以外的文件夹：{target}。",
                path=target,
            )
        return iter_entries(target, should_cancel=should_cancel, report=report)

    def folder_cover(
        self,
        folder: Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PhotoRef | None:
        """相册封面：文件夹里（含更深的层级）找到的第一张照片，没有则 ``None``。

        用「找到第一张就停」的搜索做封面：整棵树可能很大，而封面只需要一张。
        """
        folder = Path(folder)
        if not folder.is_dir():
            return None
        return next(iter_photos(folder, should_cancel=should_cancel), None)

    def thumbnail(self, ref: PhotoRef) -> RasterImage:
        """取缩略图（走缓存）。"""
        return self._thumbnails.get(ref)

    def preview(self, ref: PhotoRef, *, max_edge: int) -> RasterImage:
        """取用于「适配窗口」的预览图，不落盘。

        与缩略图不同，预览的尺寸随窗口变化，缓存它只会把缓存撑爆。
        """
        return decode_rgba(ref.path, max_edge=max_edge, use_draft=True)

    def full(self, ref: PhotoRef) -> RasterImage:
        """取全分辨率像素，用于 100% 查看。

        调用方必须自己控制内存：12MP 照片的 RGBA 约 48 MB。
        """
        return decode_rgba(ref.path)

    def clear_cache(self) -> int:
        """清空本库的缩略图缓存，返回删除的条目数。"""
        return self._thumbnails.clear()

    # ---- 写操作（唯一会改动用户文件的一类）------------------------------------

    def rename_entry(self, entry: GridEntry, new_name: str) -> RenameResult:
        """给照片或相册改名，并把它的缩略图缓存挪到新路径。

        这是少数会动用户文件的操作，规矩与红线见 ``core.rename`` 和 ADR 0004。
        """
        result = rename_entry(self._root, entry, new_name)
        if result.renamed:
            self._relocate_thumbnail(result.previous, result.entry)
        return result

    def move_to_trash(self, entries: Sequence[GridEntry]) -> TrashResult:
        """把照片或相册移进系统废纸篓（界面上就是「删除」）。

        规矩与红线见 ``core.trash`` 与 ADR 0008：**不销毁文件**，同卷内只是一次改名。
        缩略图缓存刻意留着不删 —— 照片还在，撤销回来时直接命中，不必重新解码。
        """
        return trash_entries(self._root, entries)

    def undo_last_write(self) -> UndoResult | None:
        """撤销最近一次写操作（改名或移到废纸篓）；没有可撤销的记录时返回 ``None``。"""
        result = undo_last_write(self._root)
        if result is not None and result.replaced is not None:
            self._relocate_thumbnail(result.replaced, result.entries[0])
        return result

    def next_undo_label(self) -> str | None:
        """下一次撤销会撤掉什么（「重命名」/「移到废纸篓」），用于菜单文案。"""
        return next_undo_label(self._root)

    def _relocate_thumbnail(self, previous: GridEntry, entry: GridEntry) -> None:
        """改名不改变像素，但缓存 key 含路径 —— 不挪就会留下永远命不中的孤儿条目。"""
        if isinstance(previous, PhotoRef) and isinstance(entry, PhotoRef):
            self._thumbnails.relocate(previous, entry)


__all__ = ["Library"]
