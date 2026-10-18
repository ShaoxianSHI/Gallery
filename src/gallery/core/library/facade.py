"""库门面：界面层访问文件系统的唯一入口 —— 界面不许自己 ``open()`` 原图或自己拼路径读缓存，
这样「哪些操作会碰用户的文件、哪些只会碰缓存」在代码里始终是可审计的。

**打开一个库只在两种情况下会写**（ADR 0038 决策 2、决策 3）：建配置目录，以及清掉它下面的旧
缩略图缓存；建不出来时**不报错**，只退化成内存索引，界面在横幅上说一声，浏览照旧。除缩略图
（**只**在系统目录里）之外，Gallery 关于一个库的一切都在配置目录里。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Container, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial, wraps
from pathlib import Path
from typing import TYPE_CHECKING, Concatenate, Final, cast
from uuid import uuid4

from gallery.core import paths
from gallery.core.errors import ConfigError, GalleryError, LibraryNotAccessibleError
from gallery.core.events import sources
from gallery.core.events import store as events
from gallery.core.events.import_shows import read_show_export, show_identity
from gallery.core.events.kinds import MOVIE, SHOW
from gallery.core.events.models import MovieInfo, MovieRef
from gallery.core.events.orphans import OrphanScan, find_orphans, forget_orphans
from gallery.core.events.store import (
    Event,
    EventDraft,
    EventRef,
    EventStore,
    EventWriteOutcome,
    event_by_id,
    events_of_kind,
)
from gallery.core.library.config import (
    LibraryConfig,
    LibraryUIState,
    discard_legacy_external_index,
    discard_legacy_thumb_cache,
    load_config,
    prepare_library,
    save_config,
)
from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import (
    FolderRef,
    GridEntry,
    PhotoRef,
    is_supported_photo,
)
from gallery.core.library.place_pass import (
    _needs_lookup,
    _offset_progress,
    _resolve_batch,
    _resolved,
)
from gallery.core.library.scanner import (
    ScanReport,
    has_album,
    iter_albums,
    iter_entries,
    iter_photos,
)
from gallery.core.library.search import RESULT_LIMIT, SearchCondition, SearchOutcome
from gallery.core.library.watcher import LibraryChange, LibraryWatcher
from gallery.core.media.decode import RasterImage, decode_rgba
from gallery.core.media.thumbs import ThumbnailCache
from gallery.core.metadata.album import DEFAULT_ALBUM_NAME, CreatedAlbum, create_album
from gallery.core.metadata.batch import BatchMetadataSource, read_facets
from gallery.core.metadata.download import save_downloaded_file
from gallery.core.metadata.facets import (
    PhotoFacets,
    mark_place_resolved,
    with_resolved_place,
)
from gallery.core.metadata.metadata_edit import (
    MetadataEdit,
    MetadataPlan,
    MetadataState,
    plan_metadata_edit,
)
from gallery.core.metadata.paste import PasteResult, copy_into, duplicate
from gallery.core.metadata.rename import RenameResult, rename_entry
from gallery.core.metadata.transfer import MoveResult, move_into
from gallery.core.metadata.trash import TrashResult, trash_entries
from gallery.core.metadata.undo import UndoResult, next_undo_label, undo_last_write
from gallery.core.metadata.writeback import ExifToolWriter, MetadataWriter, write_metadata
from gallery.core.sql import is_stale_or_locked

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver, Status

logger = logging.getLogger(__name__)

#: 解析地点时一次处理多少张。与 exiftool 自己的批次（``batch.BATCH_SIZE``）对齐：累积到一批
#: 正好起一次子进程，进度也一跳一跳地报，既不空转也不刷屏。
PLACE_PASS_BATCH: Final = 400

#: 写文件表时一批多少张。太大则一次占用索引锁的时间长，而那把锁界面读索引时也要用。
PHOTO_INDEX_BATCH: Final = 2000


def _retries_after_reopening[**P, R](
    method: Callable[Concatenate[Library, P], R],
) -> Callable[Concatenate[Library, P], R]:
    """写库的入口套一层：底下的库文件被换掉（或连接被占住）就重开一次再跑一遍。

    :meth:`Library.facet_index` 与 :meth:`Library.events` 都是**按连接现取**的，所以重跑
    自然拿到新连接；换连接只能换在整段事务之外 —— 半途换会把同一笔事务的前半截留在旧连接上。
    **只套单笔事务的写**：下载封面、搬用户文件那些重跑一遍会多出一个文件。
    """

    @wraps(method)
    def wrapper(self: Library, *args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return method(self, *args, **kwargs)
        except sqlite3.Error as exc:
            if not is_stale_or_locked(exc) or self._reopen_index() is None:
                raise
            return method(self, *args, **kwargs)

    # ``wraps`` 交回来的是 ``_Wrapped``，类型检查器认不出它与原签名的关系。
    return cast("Callable[Concatenate[Library, P], R]", wrapper)


@dataclass(frozen=True, slots=True)
class PlacePass:
    """跑完一遍 :meth:`Library.resolve_places` 的结果。"""

    #: 扫过多少张照片。
    scanned: int = 0
    #: 其中有几张真的反查了坐标。
    resolved: int = 0


@dataclass(frozen=True, slots=True)
class MetadataWriteOutcome:
    """:meth:`Library.write_metadata_edit` 的结果。"""

    #: 写完之后这张照片的引用。文件已经不在时为 ``None``。
    entry: PhotoRef | None
    #: 已确定的字段，以及写完之后文件里会有的拍摄时间与位置。
    plan: MetadataPlan
    #: 是否留下了可回滚的日志记录。为 ``False`` 时界面要说明「⌘Z 撤销不了」。
    journaled: bool = True
    #: 是否真的动过文件。什么都没改时为 ``False``，此时连日志也没记。
    written: bool = False


@dataclass(frozen=True, slots=True)
class ShowImportPlan:
    """一次导入的**预演**：表格里读出来的草稿，以及其中几场与库里已有的重复。

    ``duplicates`` 只按名称 + 日期 + 场馆比（见
    :func:`~gallery.core.events.import_shows.show_identity`），用来在写库之前问用户一句。
    真正写的时候会重算一遍 —— 用户可能选「全部导入」。
    """

    drafts: tuple[EventDraft, ...]
    duplicates: int = 0
    #: 表格里没被当成演出的行，一行一句中文（含行号与原因）。
    skipped: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """表格里一共读出了多少场。"""
        return len(self.drafts)


@dataclass(frozen=True, slots=True)
class ShowImportOutcome:
    """:meth:`Library.import_shows` 的结果。

    ``duplicates`` 是这次因为「跳过重复」而没有建的场数；``skipped`` 是一行一句、可以直接
    念给用户听的跳过说明（含行号与原因）。
    """

    created: int
    duplicates: int = 0
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MovieImportPlan:
    """「导入影片」的**预演**：抓到的影片元数据，以及其中几部与库里已有编号重复。

    ``failures`` 是一行一句的中文（哪一条编号为什么没抓到），可以直接念给用户听。
    """

    movies: tuple[MovieInfo, ...] = ()
    duplicates: int = 0
    failures: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """这次一共抓到了几部。"""
        return len(self.movies)


@dataclass(frozen=True, slots=True)
class MovieImportOutcome:
    """:meth:`Library.import_movies` 的结果。"""

    created: int = 0
    duplicates: int = 0
    failures: tuple[str, ...] = ()
    #: 是否每一部都留下了可回滚的日志记录；为 ``False`` 时界面要说明「没能记下操作日志」。
    journaled: bool = True
    #: 这次写进用户文件夹的封面文件。
    files: tuple[Path, ...] = ()


class Library:
    """一个已打开的库。"""

    def __init__(
        self,
        root: Path,
        config: LibraryConfig,
        thumbnails: ThumbnailCache,
        *,
        writer: MetadataWriter | None = None,
    ) -> None:
        self._root = root
        self._config = config
        self._thumbnails = thumbnails
        #: 元数据写回的工具（ADR 0030）。``None`` 表示用默认的 exiftool，测试注入假实现 ——
        #: 界面据此判断「编辑元数据」这一条该不该出现。
        self._writer = writer
        #: exiftool 探测的结果（``None`` = 还没探过）。**整个应用只有这一个判据** —— 界面层
        #: 再造一个 ``ExifToolReader`` 去探就变成同一个「装没装」的问题有两套问法。
        self._exiftool_available: bool | None = None
        #: 配置目录是不是真的建出来了。为 ``False`` 时索引只在内存里用，界面据此决定要不要在
        #: 横幅上说明「这个库只读，索引没落盘」。
        self._config_dir_ready = paths.has_config_dir(root)
        # 索引只在真的用到分组 / 排序时才打开（ADR 0009）—— 打开它本身不写任何文件。
        self._index: FacetIndex | None = None
        #: 打开 / 关掉索引那把连接时的锁，**开与关共用**：几个线程同时第一次访问时只许有一条连接
        #: —— 两条连接抢同一份 ``index.db`` / ``events.db``，写的时候会报 ``database is locked``。
        self._index_lock = threading.Lock()
        #: 整份事件清单的会话缓存（ADR 0043 决策 7）。``None`` = 还没读过或刚被写操作作废。
        self._events_cache: tuple[Event, ...] | None = None

    @property
    def writer(self) -> MetadataWriter | None:
        """元数据写回工具（界面判断功能可不可用时用 :meth:`can_write_metadata`）。"""
        return self._writer

    @property
    def config_dir_ready(self) -> bool:
        """配置目录是不是真的存在（建不出来时为 ``False``，只读卷上会发生）。"""
        return self._config_dir_ready

    @classmethod
    def open(cls, root: Path, *, cache_base: Path | None = None) -> Library:
        """校验并打开库。**只碰 Gallery 自己的文件**：保证配置目录存在（建不出来就退化成内存索引，
        见 :meth:`config_dir_ready`），并清掉**旧版**留在配置目录 ``cache/`` 下的可再生缩略图。

        :param cache_base: 系统级缩略图缓存根目录，测试用；``None`` 表示走平台默认位置。
        :raises LibraryNotAccessibleError: 目录不存在或没有读取权限。
        """
        # 统一解析成绝对路径：命令行允许传相对路径，而库路径会被记进会话、算进缓存摘要。
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

        prepare_library(root)
        # 两处存量清理一起做（ADR 0038 决策 6）：库内那块缩略图与库外那份索引，新版一个都不再写。
        discarded = discard_legacy_thumb_cache(root) + discard_legacy_external_index()
        if discarded:
            logger.info("清掉旧版缓存 %d 个文件（库根 %s）", discarded, root)
        config = load_config(root)
        thumbnails = ThumbnailCache(
            paths.thumbnail_cache_dir(root, base=cache_base),
            max_edge=config.thumbnail_max_edge,
            max_bytes=config.max_cache_bytes,
        )
        return cls(root, config, thumbnails)

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
    def ui(self) -> LibraryUIState:
        """这个库的界面偏好（上次看到哪儿了）。"""
        return self._config.ui

    def set_ui_state(self, ui: LibraryUIState) -> None:
        """把界面偏好写进 ``config.toml``，并让内存里那份跟着更新（ADR 0038 决策 4）。

        **内存那份必须一起换**：刚切的那一档要立刻生效，等下次打开库才变界面会与自己记的
        对不上。配置目录建不出来时**什么都不做也不报错** —— 那台机器上本来就存不住这些。
        """
        if ui == self._config.ui:
            return  # 没变就不写：一次没有变化的改写只是白费一次 fsync
        if not self._config_dir_ready:
            return
        updated = replace(self._config, ui=ui)
        save_config(self._root, updated)
        self._config = updated

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

    def albums(self, *, report: ScanReport | None = None) -> list[FolderRef]:
        """库里**全部**的相册，按相对路径排序 —— 「复制到相册 / 移动到相册」的目标清单。

        只读，一次列完而不是流式：菜单要的是一张完整的清单，缺一半比慢一点糟糕得多；而菜单
        **每次弹出时现列**，所以用户在访达里新建的相册立刻就能选到。
        """
        return list(iter_albums(self._root, report=report))

    def has_albums(self) -> bool:
        """库里**有没有**相册 —— 找到一个就停。**不要为「有没有」把整列一遍**：那会把右键菜单
        最贵的一步（3000 个相册约 120 ms 的遍历）付在构造菜单时，而清单要到点开子菜单才需要。

        判据在 :func:`~gallery.core.library.scanner.has_album`，可见性规则必须与
        :func:`~gallery.core.library.scanner.iter_albums` 一致，否则菜单会出现挑了也没用的条目。
        """
        return has_album(self._root)

    def album_label(self, folder: Path) -> str:
        """相册在菜单里显示的名字：相对库根的路径（``2025/苏州``），顶层相册就是它自己的名字。
        **用相对路径而不是单纯的名字**是必须的：两个年份下面各有一个「苏州」时，菜单里光写
        「苏州」用户分不清自己要挪到哪去。
        """
        target = Path(folder)
        # 库根本身（相对位置是空串）与库外都给文件夹自己的名字 —— 这一处显示的是菜单项。
        return paths.relative_posix(self._root, target) or target.name

    def folder_cover(
        self,
        folder: Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        skip: Container[Path] = (),
    ) -> PhotoRef | None:
        """相册封面：用户指定的那一张，没有指定就找文件夹里（含更深层级）的第一张，没有则 ``None``。
        用「找到第一张就停」的搜索做封面：整棵树可能很大，而封面只需要一张。

        :param skip: 这些照片当过封面却读不出来（云盘上没下完的那些），跳过它们往后找 ——
            少了它，一张下到一半的照片会让整张卡片一直挂着永远出不来、也换不掉的黑封面。
        """
        folder = Path(folder)
        if not folder.is_dir():
            return None
        # **指定的那一张排第一**（ADR 0028）：它也照 ``skip`` 走 —— 读不出来时退回「第一张照片」。
        chosen = self.album_cover(folder)
        if chosen is not None and chosen.path not in skip:
            return chosen
        for ref in iter_photos(folder, should_cancel=should_cancel):
            if ref.path not in skip:
                return ref
        return None

    def album_cover(self, folder: Path) -> PhotoRef | None:
        """这个相册**被指定过**的封面照片。没指定、指定的那张已经不在了、或者它现在压根不是一张能读的
        照片，都返回 ``None`` —— 调用方据此退回「里面第一张照片」（ADR 0028 决策 4）。那一行
        配置**不会因此被删掉**：照片挪出去又挪回来时，它又该是封面了。
        """
        key = _relative_posix(self._root, folder)
        if key is None:
            return None
        stored = self._config.album_covers.get(key)
        if stored is None:
            return None
        path = self._root / stored
        # 先按扩展名与「是不是文件」筛掉，再 stat：读不出来的那张留给流水线去报错。
        if not is_supported_photo(path) or not path.is_file():
            return None
        try:
            return PhotoRef.from_path(path)
        except OSError:
            return None

    def has_album_cover(self, folder: Path) -> bool:
        """这个相册有没有**被指定过**封面（不管那张照片现在还在不在）。

        界面靠它决定「取消相册封面」这一条出不出现：用户看得见自己设过，
        哪怕那张照片此刻已经不在了 —— 那也仍然是他设过的。
        """
        key = _relative_posix(self._root, folder)
        return key is not None and key in self._config.album_covers

    def set_album_cover(self, album: Path, photo: Path) -> None:
        """把 ``photo`` 记成 ``album`` 的封面，写进 ``config.toml``（ADR 0028）。**只写 Gallery
        自己的配置文件**，用户的照片一个字节都不动 —— 因此它既不记 journal 也不进
        撤销栈，撤销由对称的 :meth:`clear_album_cover` 负责（决策 5）。

        :raises ConfigError: 封面照片不在这个相册里、不是一张受支持的照片，或这个库没有配置目录。
        """
        album_key = self._album_key(album)
        photo = Path(photo)
        photo_key = _relative_posix(self._root, photo)
        if photo_key is None:
            raise ConfigError(f"「{photo.name}」不在这个库里，没法拿它当封面。", path=photo)
        if not is_supported_photo(photo):
            raise ConfigError(
                f"「{photo.name}」不是 Gallery 支持的照片格式，换一张试试。", path=photo
            )
        if not photo_key.startswith(f"{album_key}/"):
            # 与 ``config.from_mapping`` 里那条校验同一个判据：写进去的与读得出来的必须是同一种键。
            raise ConfigError(
                f"封面得是「{Path(album).name}」里的照片，「{photo.name}」不在这个相册里。",
                path=photo,
            )
        self._write_covers({**self._config.album_covers, album_key: photo_key})

    def clear_album_cover(self, album: Path) -> bool:
        """取消这个相册的指定封面，回到「里面第一张照片」；本来就没有就返回 ``False``。没设置过就
        **什么都不写** —— 一次没有变化的配置改写只是白白多一次 ``fsync``。
        """
        key = _relative_posix(self._root, album)
        if key is None or key not in self._config.album_covers:
            return False
        self._write_covers(
            {name: cover for name, cover in self._config.album_covers.items() if name != key}
        )
        return True

    def _album_key(self, album: Path) -> str:
        """相册在配置文件里的那把键：相对库根的 posix 路径。"""
        album = Path(album)
        if not album.is_dir():
            raise ConfigError(f"「{album.name}」不是一个文件夹，没法给它设封面。", path=album)
        key = _relative_posix(self._root, album)
        if key is None:
            if paths.resolved(album) == paths.resolved(self._root):
                raise ConfigError(
                    "库根目录不是一个相册，它在网格上不是一张卡片，没有封面可设。",
                    path=album,
                )
            raise ConfigError(f"「{album.name}」不在这个库里，没法给它设封面。", path=album)
        return key

    def _write_covers(self, covers: Mapping[str, str]) -> None:
        """把封面那张表写回 ``config.toml``，并让内存里的配置跟着更新。**内存那份必须一起换**：只写
        文件不换内存，用户会看着自己刚设的封面没换上，直到下一次打开库才生效。
        """
        if not self._config_dir_ready:
            # 只读卷上配置目录建不出来，封面设置没有地方存 —— 那台机器上本来也存不住，所以不硬写。
            raise ConfigError(
                f"这个库建不了 {paths.config_dir(self._root).name} 目录，封面设置没有地方保存。"
                "把这个文件夹移到可以写入的地方再试。",
                path=self._root,
            )
        updated = replace(self._config, album_covers=dict(covers))
        save_config(self._root, updated)
        self._config = updated

    def thumbnail(
        self, ref: PhotoRef, *, should_cancel: Callable[[], bool] | None = None
    ) -> RasterImage:
        """取缩略图（走缓存）。"""
        return self._thumbnails.get(ref, should_cancel=should_cancel)

    def preview(
        self,
        ref: PhotoRef,
        *,
        max_edge: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RasterImage:
        """取用于「适配窗口」的预览图，不落盘。

        与缩略图不同，预览的尺寸随窗口变化，缓存它只会把缓存撑爆。
        """
        return decode_rgba(ref.path, max_edge=max_edge, use_draft=True, should_cancel=should_cancel)

    def full(
        self, ref: PhotoRef, *, should_cancel: Callable[[], bool] | None = None
    ) -> RasterImage:
        """取全分辨率像素，用于 100% 查看。

        调用方必须自己控制内存：12MP 照片的 RGBA 约 48 MB。
        """
        return decode_rgba(ref.path, should_cancel=should_cancel)

    def clear_cache(self) -> int:
        """清空本库的缩略图缓存，**连索引一起清**，返回删除的缩略图条目数。

        索引是另一份缓存，但它记的也是「像素之外的东西」，所以「清空缓存」在用户看来是一次操作，
        在代码里就该是一次操作。**两张表一起清**：只清一张的话，检索会拿一份刚被丢掉的文件表
        继续查。代价是下一次分组重读一遍元数据 —— 缓存删掉本来就可重扫恢复。
        """
        removed = self._thumbnails.clear()
        if self._index is not None:
            self._index.clear()
        return removed

    # ---- 实时监听 ------------------------------------------------------------

    def watch(
        self,
        *,
        on_change: Callable[[tuple[LibraryChange, ...]], None],
        on_error: Callable[[str], None] | None = None,
    ) -> LibraryWatcher:
        """开始监听这个库，返回监听器（**调用方负责停它**：切库与关窗时都要停）。

        监听范围跟着库配置的 ``recursive``；回调在自己的线程上被调用，界面要用排队连接把它搬回
        主线程（ADR 0013）。监听是纯读的：只 ``stat``，不写不删。
        """
        return LibraryWatcher(
            self._root,
            recursive=self._config.recursive,
            on_change=on_change,
            on_error=on_error,
        )

    @_retries_after_reopening
    def forget_paths(self, gone: Sequence[Path]) -> int:
        """把已经不在库里的路径从索引中抹掉，返回删掉了几行。

        照片消失之后索引里那一行不会自己走。**只删给定的路径**：相册模式下「当前这一层」不等于
        「整个库」，按层去重会误伤别的相册（ADR 0013）。索引还没打开时什么也不做。
        """
        if self._index is None or not gone:
            return 0
        return self._index.forget(gone)

    # ---- 分组 / 排序用的元数据（只读）----------------------------------------

    @property
    def facet_index(self) -> FacetIndex:
        """facet 缓存（首次调用时才打开数据库）。配置目录建不出来时**不开盘** —— 退回纯内存缓存，
        本次会话照常能用，只是下次打开要重读一遍 EXIF。

        **打开这一步要串行**：几个线程同时第一次进来会各建一条连接，于是同一份 ``index.db`` /
        ``events.db`` 上有两条连接，其中一条写的时候报 ``database is locked``（ADR 0064）。
        """
        with self._index_lock:
            if self._index is None:
                path = paths.index_file(self._root) if self._config_dir_ready else None
                # 事件库挂在**索引这条连接**上（ADR 0043 决策 1）：SQL 层仍然是一张库，
                # 生命周期上仍是两个文件、两份 ``user_version``。挂钩只有这一处接线。
                self._index = FacetIndex(path, attach=partial(events.attach, root=self._root))
                self._events_cache = None
            return self._index

    @property
    def events(self) -> EventStore:
        """事件库（ADR 0043）。配置目录建不出来时**每一次都是不可用的那个** —— 与索引同源。"""
        attached = self.facet_index.attached
        store = attached if isinstance(attached, EventStore) else EventStore(self._root)
        # guard: allow-long-comment（漏掉一处不报错：库写进去了，界面那份清单还是旧的）
        # 清单缓存的作废**挂在唯一的写入方上**，而不是抄在每个写方法后面（见
        # :meth:`EventStore.on_change`）。原先那四处手写的地方没有测试盯着，漏一处
        # 不报错 —— 库写进去了，界面上那份清单还是旧的。
        store.on_change = self._invalidate_events
        store.reopen = self._reopen_index
        return store

    def _invalidate_events(self) -> None:
        """:meth:`all_events` 那份会话缓存作废。**只有写入口会调它。**"""
        self._events_cache = None

    def _reopen_index(self) -> sqlite3.Connection | None:
        """**无条件**重开索引连接（事件库跟着一起重挂），返回新连接，换不上返回 ``None``。

        判据来自一次真实的写失败，或事件库那份文件被换掉了（见 ``store._live_connection``）。
        """
        index = self._index
        if index is None:
            return None
        self._events_cache = None
        return index.reopen()

    @property
    def events_available(self) -> bool:
        """此刻事件功能能不能用。界面据此让「记录为」那一行不出现。"""
        return self.events.available

    def all_events(self) -> tuple[Event, ...]:
        """整份事件清单，**读一次就缓存住**。

        一个库的事件只有几十条，而菜单要按类型列它、面板要预填、分组要给标签 —— 每次都查一遍
        等于把同一条 ``SELECT`` 花在「弹出右键菜单」这种地方。写入口只有下面那几个，每次写完
        就地作废这份缓存，于是它不可能与库里的不一致。
        """
        if self._events_cache is None:
            self._events_cache = self.events.all_events()
        return self._events_cache

    def event(self, event_id: int) -> Event | None:
        """按 id 取一个事件（从上面那份缓存里找，不查库）。"""
        return event_by_id(self.all_events(), event_id)

    def events_by_kind(self, kind_id: str) -> tuple[Event, ...]:
        """某一类的全部事件，顺序即菜单顺序（新→旧）。"""
        return events_of_kind(self.all_events(), kind_id)

    def photos_in_days(self, first_day: str, last_day: str) -> tuple[PhotoRef, ...]:
        """这几天里拍的那些照片（容器型事件最常用的那一步：「这一周的都算」，ADR 0043 决策 4）。

        **走索引、整库查**：旅行的照片不只在当前这一层，只在这一层里挑会让人以为照片丢了。
        :raises IndexUnavailableError: 索引没落盘（只读卷）。
        """
        return tuple(self.facet_index.photos_in_days(first_day, last_day))

    def events_for_photos(
        self, photos: Sequence[PhotoRef | Path]
    ) -> dict[Path, tuple[EventRef, ...]]:
        """这些照片分别属于哪些事件 —— **分组与筛选要的那一份，走 SQL**。

        **必须在后台线程里调**：这是唯一一个随照片数增长的查询（一层可能五千张），而分组本身
        是在主线程同步算的（ADR 0043 决策 7）。
        """
        return self.events.events_for_photos(_paths_of(photos))

    # ---- 事件的写入口（它不动用户文件，动的是我们自己的库）--------------------

    @_retries_after_reopening
    def create_event(self, kind_id: str, draft: EventDraft) -> EventWriteOutcome:
        """新建一个事件。**不写照片、不写 journal 之外的任何用户文件**；索引也不碰
        （索引是 EXIF 的投影，事件不是 EXIF）。"""
        return self.events.create(kind_id, draft)

    @_retries_after_reopening
    def update_event(self, event_id: int, draft: EventDraft) -> EventWriteOutcome:
        """改一个事件。公共列与类型独有字段在**同一次写**里改完，所以只值一次 ⌘Z。"""
        return self.events.update(event_id, draft)

    @_retries_after_reopening
    def delete_event(self, event_id: int) -> EventWriteOutcome:
        """删一个事件，连带它那些绑定（返回值里带着，撤销要靠它放回去）。"""
        return self.events.delete(event_id)

    def plan_show_import(self, path: Path) -> ShowImportPlan:
        """读一张「演迹」导出的 ``.xlsx``，**先不写库**，只算出会建哪些、其中几场是重复的。

        重复按名称 + 日期 + 场馆判，见 :func:`~gallery.core.events.import_shows.show_identity`。
        :raises ShowImportError: 文件读不出来，或者不是「演迹」的演出列表。
        """
        parsed = read_show_export(Path(path))
        existing = self._show_identities()
        duplicates = sum(1 for draft in parsed.drafts if _draft_identity(draft) in existing)
        return ShowImportPlan(drafts=parsed.drafts, duplicates=duplicates, skipped=parsed.skipped)

    def import_shows(
        self, plan: ShowImportPlan, *, skip_duplicates: bool = False
    ) -> ShowImportOutcome:
        """按 :meth:`plan_show_import` 的预演把演出建进库里（ADR 0047）。

        ``skip_duplicates`` 为真时，名称 + 日期 + 场馆与库里已有的相同的那几场不建。每一行走
        :meth:`create_event` 建一条演出事件，于是 journal、缓存作废与手工新建完全一致。
        **一个用户的文件都不碰**，只写 Gallery 自己的 ``events.db``。
        """
        existing = self._show_identities()
        created = 0
        duplicates = 0
        for draft in plan.drafts:
            identity = _draft_identity(draft)
            if skip_duplicates and identity in existing:
                duplicates += 1
                continue
            self.create_event(SHOW.id, draft)
            created += 1
            existing.add(identity)
        return ShowImportOutcome(created=created, duplicates=duplicates, skipped=plan.skipped)

    def _show_identities(self) -> set[tuple[str, str, str]]:
        """库里已有演出的判重键集合。"""
        return {
            show_identity(event.text("title"), event.text("started_on"), event.text("venue"))
            for event in self.events_by_kind(SHOW.id)
        }

    @_retries_after_reopening
    def bind_photos(self, photos: Iterable[PhotoRef | Path], event_id: int) -> int:
        """把照片记进这个事件，返回新记进去几张。

        **不上撤销栈**（ADR 0043 决策 5）：反向动作同样是一键（「记录为 → 无」），而「这一周
        的照片都算」一次就是几百张。
        """
        return self.events.bind(_paths_of(photos), event_id)

    @_retries_after_reopening
    def unbind_photos(self, photos: Iterable[PhotoRef | Path], event_id: int) -> int:
        """把这些照片从这个事件里拿出来。"""
        return self.events.unbind(_paths_of(photos), event_id)

    @_retries_after_reopening
    def set_event_cover(self, event_id: int, photo: PhotoRef | Path) -> EventWriteOutcome:
        """把这个事件的封面设成某一张照片，**它必须已经绑在这个事件上**（ADR 0048 决策 1）。**不进**
        ``WRITE_METHODS_ON_LIBRARY``：写的是 Gallery 自己的 ``events.db``，不碰用户的照片。

        :raises CoverNotBoundError: 那张照片不属于这个事件（卡片只能画组内那一张）。
        """
        return self.events.set_cover(event_id, _paths_of([photo])[0])

    @_retries_after_reopening
    def clear_event_cover(self, event_id: int) -> bool:
        """取消封面：回到「组内第一张」。**没设过就是 ``False``** —— 界面据此不摆那一行。"""
        return self.events.clear_cover(event_id)

    # ---- 影片：按编号抓取与导入（ADR 0063 / 0068）----------------------------

    def movie_url(self, event: Event) -> str | None:
        """这个影片事件该打开的网址；没有编号就是 ``None``。

        JavDB 那一栏的站点前缀来自 ``config.toml`` 的 ``movie_site``；YouTube 与 B 站的地址形状
        是固定的（见 :func:`gallery.core.events.sources.movie_url`）。
        """
        code = event.text("code").strip()
        if not code:
            return None
        ref = MovieRef(source=sources.source_of(event.text("source")), code=code)
        return sources.movie_url(ref, jav_site=self._config.movie_site)

    def plan_movie_import(
        self,
        entries: Sequence[str],
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        client: sources.MovieSource | None = None,
    ) -> MovieImportPlan:
        """把用户敲的那一批「编号或链接」抓一遍元数据（**只读**），算出一份预演。

        **要联网，跑在后台线程里**。逐条抓，抓不到的那条记进 ``failures``（不中断其余）——
        认不出是哪一行的、源站上没有的，都在里面。
        :param client: 抓取用的入口，测试注入假实现；``None`` 表示默认的三个来源。
        """
        getter = client if client is not None else sources.HttpSources()
        total = len(entries)
        movies: list[MovieInfo] = []
        failures: list[str] = []
        for index, raw in enumerate(entries, start=1):
            if should_cancel is not None and should_cancel():
                break
            try:
                movies.append(getter.fetch(sources.parse_ref(raw)))
            except GalleryError as exc:
                failures.append(f"{raw}：{exc.message}")
            if on_progress is not None:
                on_progress(index, total)
        existing = self._movie_keys()
        duplicates = sum(1 for movie in movies if movie.ref in existing)
        return MovieImportPlan(
            movies=tuple(movies), duplicates=duplicates, failures=tuple(failures)
        )

    def import_movies(
        self,
        plan: MovieImportPlan,
        *,
        target: Path,
        skip_duplicates: bool = False,
        should_cancel: Callable[[], bool] | None = None,
        client: sources.MovieSource | None = None,
    ) -> MovieImportOutcome:
        """按预演把影片建进库里：下封面 → 在 ``target`` 写成一个新文件 → 建事件 → 绑定封面。

        **封面是写进用户文件夹的新文件**（「只增不改」，绝不覆盖；ADR 0063），写盘走
        :func:`~gallery.core.metadata.download.save_downloaded_file` 并记 journal。**要联网**，
        跑在后台线程里；逐部做，失败的那部记进 ``failures`` 不中断其余。
        """
        getter = client if client is not None else sources.HttpSources()
        existing = self._movie_keys()
        batch = uuid4().hex[:12]
        created = 0
        duplicates = 0
        journaled = True
        files: list[Path] = []
        failures = list(plan.failures)
        for movie in plan.movies:
            if should_cancel is not None and should_cancel():
                break
            if skip_duplicates and movie.ref in existing:
                duplicates += 1
                continue
            try:
                data = getter.cover_bytes(movie)
                saved = save_downloaded_file(
                    self._root, target, _cover_name(movie), data, batch=batch
                )
            except GalleryError as exc:
                failures.append(f"{movie.code}：{exc.message}")
                continue
            journaled = journaled and saved.journaled
            outcome = self.create_event(MOVIE.id, movie.to_draft())
            self.bind_photos([saved.created], outcome.event.id)
            # 刚绑上，所以这一句不会抛「不属于这次事件」；真抛了也只是没有封面，不该毁掉整次导入。
            try:
                self.set_event_cover(outcome.event.id, saved.created)
            except GalleryError as exc:
                failures.append(f"{movie.code}：没能设成封面，{exc.message}")
            created += 1
            files.append(saved.created)
            existing.add(movie.ref)
        return MovieImportOutcome(
            created=created,
            duplicates=duplicates,
            failures=tuple(failures),
            journaled=journaled,
            files=tuple(files),
        )

    def _movie_keys(self) -> set[MovieRef]:
        """库里已有影片的「（来源, 编号）」集合（判重键就是它，ADR 0068）。

        **编号按来源自己的规矩比**：YouTube 与 B 站的编号大小写敏感，拿归一过的 JavDB 番号去比
        会把两部不同的片子当成同一部。
        """
        return {
            MovieRef(
                source=sources.source_of(event.text("source")), code=event.text("code").strip()
            )
            for event in self.events_by_kind(MOVIE.id)
            if event.text("code").strip()
        }

    def find_orphan_bindings(
        self, *, should_cancel: Callable[[], bool] | None = None
    ) -> OrphanScan:
        """哪些绑定指向了已经不存在的照片（ADR 0043 决策 9）。**只读，跑在后台线程里。**

        三档判定与「为什么读不到不等于没有」都在 :mod:`gallery.core.events.orphans` 里说；
        界面这一侧只负责起任务、弹确认框、按下按钮之后删。
        """
        return find_orphans(self.events, should_cancel=should_cancel)

    def forget_orphan_bindings(self, scan: OrphanScan, *, include_vanished: bool) -> int:
        """按用户点头的范围删掉那些绑定，返回删了几条。

        ``include_vanished`` 管的是「相册不存在」那一档 —— **它混着「那个盘没挂上」**，
        所以默认 ``False``。
        """
        return forget_orphans(self.events, scan, include_vanished=include_vanished)

    def photo_facets(
        self,
        refs: Sequence[PhotoRef],
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        status: Status | None = None,
        source: BatchMetadataSource | None = None,
        resolver: PlaceResolver | None = None,
    ) -> dict[Path, PhotoFacets]:
        """取这些照片的分组 / 排序字段，**只读**。先查缓存，只对没命中或文件已改过的去真读 EXIF，
        新结果顺手写进索引，于是下一次切换分组字段几乎立刻完成。**地点**：给了 ``resolver`` 就按
        坐标离线反查补齐（文件里写了以文件为准），没给就只认文件里写的地点，不联网。
        :param status: 首次加载城市库时，用它把进度说给界面听。
        :raises MetadataError: 主来源整体失败（例如 exiftool 跑不起来）。
        """
        index = self.facet_index
        cached = index.lookup(refs)
        missing = [ref for ref in refs if ref.path not in cached]
        if missing:
            fresh = read_facets(
                [ref.path for ref in missing],
                source=source,
                should_cancel=should_cancel,
                on_progress=(
                    None if on_progress is None else _offset_progress(len(cached), on_progress)
                ),
            )
            if resolver is not None:
                fresh = {
                    path: _resolved(facets, resolver, status) for path, facets in fresh.items()
                }
            index.store([(ref, fresh[ref.path]) for ref in missing if ref.path in fresh])
            cached.update(fresh)
        if resolver is not None:
            # guard: allow-long-comment（与上面读新照片那一段是并行的，重构成 elif 就会整段跳过）
            # 缓存里可能还留着「有坐标、没结论」的旧行，既然反查器在手就顺手补上。
            # **与上面读新照片那一段是并行的，不是二选一** —— 写成 ``elif`` 就会「这一批里有任何
            # 一张没缓存过」整段跳过，而那恰恰是最常见的情形。
            by_path = {ref.path: ref for ref in refs}
            pending = [path for path, facets in cached.items() if _needs_lookup(facets)]
            resolved: list[tuple[PhotoRef, PhotoFacets]] = []
            for path in pending:
                if should_cancel is not None and should_cancel():
                    break
                facets = _resolved(cached[path], resolver, status)
                cached[path] = facets
                resolved.append((by_path[path], facets))
            # **只写回真正改动过的那几行**：否则五万张的库里哪怕只有一行待补，也是五万次 UPDATE。
            if resolved:
                index.store(resolved)
        return cached

    def cached_facets(self, refs: Sequence[PhotoRef]) -> dict[Path, PhotoFacets]:
        """索引里**仍然有效**的那些（没读过的、文件被改过的都不算）。元数据面板靠它显示地点
        （ADR 0010）：纯读缓存，不碰 EXIF，因此可以放在主线程上调用。
        """
        return self.facet_index.lookup(refs)

    # ---- 检索（ADR 0026）-----------------------------------------------------

    def indexed_photos(self) -> int:
        """索引的文件表里有多少行。**用来回答「还有没有活干」**（见 :meth:`index_photos`）。"""
        return self.facet_index.count_photos()

    def remember_photos(self, refs: Sequence[PhotoRef]) -> int:
        """刚变化的那几张立刻写进文件表（监听器那条路，ADR 0013）。

        与 :meth:`index_photos` 同一个写入口，只是**只写这几张**：等整库那一遍
        跑完再补的话，用户刚放进来的照片在他这次检索里就是不存在的（ADR 0026 决策 5）。
        """
        return self.facet_index.store_photos(refs, root=self._root)

    def _iter_photo_batches(
        self, batch_size: int, should_cancel: Callable[[], bool] | None
    ) -> Iterator[tuple[list[PhotoRef], int]]:
        """按 ``batch_size`` 成批产出照片，顺带给出「到此为止扫过多少张」，两条整库遍历共用。

        **最后那半批只在没被取消时才产出**：取消了还写一次，等于用户按了停却停不下来。
        """
        batch: list[PhotoRef] = []
        scanned = 0
        for ref in self.iter_photos(should_cancel=should_cancel):
            scanned += 1
            batch.append(ref)
            if len(batch) < batch_size:
                continue
            if should_cancel is not None and should_cancel():
                return
            yield batch, scanned
            batch = []
        if batch and not (should_cancel is not None and should_cancel()):
            yield batch, scanned

    def index_photos(
        self,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_work: Callable[[int], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """把整个库的照片与索引的文件表**对一遍**，返回新写了多少行。

        **是「对账」不是「首次建表」**：应用没开着时外部加进来的照片、监听器没来得及写的，都靠它
        补上 —— 只认「表空不空」的话，表里有一行之后就再也不补，那些照片在搜索里永远不存在。幂等
        （``(大小, 修改时间)`` 对得上就不写），所以对一遍只花一次遍历、几乎不写。
        ``on_work`` 只在真的写了行时叫一次；``on_progress`` 是已扫过多少。
        """
        index = self.facet_index
        written = 0
        announced = False
        scanned = 0
        for batch, scanned in self._iter_photo_batches(PHOTO_INDEX_BATCH, should_cancel):
            newly = index.store_photos(batch, root=self._root)
            written += newly
            if not announced and newly:
                announced = True
                if on_work is not None:
                    on_work(scanned)
            if on_progress is not None:
                on_progress(scanned)
        if on_progress is not None:
            on_progress(scanned)
        return written

    def matching_paths(self, conditions: Sequence[SearchCondition]) -> set[Path]:
        """这些条件命中了哪些路径 —— **只读这一层摆着的那部分**的筛选（ADR 0040）。

        范围收在调用方（它手上已有这一层的照片，求交即可），所以这一份是整库的命中集合。不能用
        :meth:`search_photos` 那份额代替：那份按 :data:`RESULT_LIMIT` 截断，相交少一条就少一张照片。
        :raises IndexUnavailableError: 索引没落盘，界面据此说清是哪一件。
        """
        return {Path(key) for key in self.facet_index.match_paths(conditions)}

    def search_photos(
        self, conditions: Sequence[SearchCondition], *, limit: int = RESULT_LIMIT
    ) -> SearchOutcome:
        """按这些条件检索**整个库**，只读。

        :raises IndexUnavailableError: 索引没落盘（多半是照片所在的磁盘不允许写缓存），
            或者 :meth:`index_photos` 那一遍还没跑完 —— 界面据此说清是哪一件，
            而不是让用户对着一个空结果猜。
        """
        outcome = self.facet_index.search(conditions, limit=limit)
        if len(outcome.refs) < outcome.total:
            return outcome  # 截断了：总数只能照索引说的报
        # 没截断时总数可以给准的：索引里可能留着文件早已被删掉的行，逐条问一次还在不在。
        alive = [ref for ref in outcome.refs if ref.path.exists()]
        return SearchOutcome(refs=alive, total=len(alive))

    def resolve_photo_place(
        self,
        ref: PhotoRef,
        latitude: float,
        longitude: float,
        *,
        resolver: PlaceResolver,
        status: Status | None = None,
    ) -> PhotoFacets:
        """按坐标反查**这一张**的地点，并把结论并回索引。坐标由调用方给出（元数据面板手里已经读到了），
        所以不必再读一次 EXIF；这是 :meth:`resolve_places` 之外的回退（ADR 0010）。

        **只并进已有的那一行，不新写一行**：索引里还存着拍摄时间、相机那些字段，写半行会挡住
        以后那次完整的 EXIF 读取（那一行会被当成「已经读过了」）。没有有效行就只返回结论。
        """
        place = resolver.place_for(latitude, longitude, status=status)
        index = self.facet_index
        known = index.lookup([ref]).get(ref.path)
        if known is None:
            return mark_place_resolved(
                with_resolved_place(
                    replace(PhotoFacets(), latitude=latitude, longitude=longitude), place
                ),
                usable=resolver.ready,
            )
        merged = mark_place_resolved(
            with_resolved_place(replace(known, latitude=latitude, longitude=longitude), place),
            usable=resolver.ready,
        )
        index.store([(ref, merged)])
        return merged

    def resolve_places(
        self,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_work: Callable[[int], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        status: Status | None = None,
        resolver: PlaceResolver | None = None,
        source: BatchMetadataSource | None = None,
    ) -> PlacePass:
        """把整个库里还没有地点结论的照片补齐，**只读照片、只写自己的缓存**（ADR 0010）。幂等，已有
        结论的行**不读 EXIF**，没有坐标的行同样跳过，文件被改过的行按 ``(大小, 修改时间)`` 重读。
        :param on_work: ``(这次要处理多少张)``，**只在真的找到活干时叫一次**（空跑时不叫）。
        :param on_progress: ``(已扫过, 总数)``，边扫边数所以总数通常是 0（表示还不知道）。
        """
        index = self.facet_index
        scanned = 0
        resolved = 0
        announced = False

        def announce(count: int) -> None:
            nonlocal announced
            if not announced:
                announced = True
                if on_work is not None:
                    on_work(count)

        for batch, scanned in self._iter_photo_batches(PLACE_PASS_BATCH, should_cancel):
            resolved += _resolve_batch(
                index,
                batch,
                resolver=resolver,
                status=status,
                source=source,
                should_cancel=should_cancel,
                on_work=announce,
            )
            if on_progress is not None:
                on_progress(scanned, 0)
        if on_progress is not None:
            on_progress(scanned, scanned)  # 没有总数时最后补一次收尾
        return PlacePass(scanned=scanned, resolved=resolved)

    def close_index(self) -> None:
        """关掉索引连接（切库 / 关窗）。与打开走同一把锁：**别在别人正在开的时候关**。"""
        with self._index_lock:
            if self._index is not None:
                self._index.close()
                self._index = None

    # ---- 写操作（唯一会改动用户文件的一类）------------------------------------

    def exiftool_available(self) -> bool:
        """exiftool 现在能不能用 —— **整个应用只有这一个判据**（界面层只问它）。

        **探一次就记住**（探测是 ``shutil.which``，菜单每次弹出都会问）；``None``（没注入 writer）
        **不是「能用」而是「还没探过」**：当成能用的话生产路径会恒为真，没装的机器上菜单照样出现
        「编辑元数据…」，用户填完点保存才报错。
        """
        if self._exiftool_available is None:
            self._exiftool_available = (self._writer or ExifToolWriter()).available()
        return self._exiftool_available

    def can_write_metadata(self) -> bool:
        """此刻能不能改照片里的元数据 —— 没装 exiftool 时为 ``False``。

        界面据此**不出现**「编辑元数据」那一条，而不是摆一条点下去只会报错的命令
        （与「粘贴」同一个取向，ADR 0014 决策 4）。
        """
        return self.exiftool_available()

    def write_metadata_edit(
        self,
        ref: PhotoRef,
        state: MetadataState,
        edit: MetadataEdit,
        *,
        resolver: PlaceResolver | None = None,
    ) -> MetadataWriteOutcome:
        """把用户编辑的元数据**写回照片文件**（ADR 0030 / 0031 / 0032），界面只管何时调它。
        :func:`~gallery.core.metadata.metadata_edit.plan_metadata_edit` 翻译字段后交给
        :func:`~gallery.core.metadata.writeback.write_metadata` 写盘、回读校验、必要时回滚。返回的
        新引用**界面必须拿去换掉网格里那一行**，索引靠 ``(大小, 修改时间)`` 自动过期。
        :raises MetadataWriteError: 坐标不合法、时间或时区看不懂、写盘失败，或**写完校验不过**。
        """
        plan = plan_metadata_edit(state, edit, resolver=resolver)
        if plan.is_empty:
            return MetadataWriteOutcome(entry=ref, plan=plan, written=False)
        result = write_metadata(self._root, ref, plan.assignments, writer=self._writer)
        if result.entry is not None:
            self._thumbnails.relocate(ref, result.entry)
        return MetadataWriteOutcome(
            entry=result.entry, plan=plan, journaled=result.journaled, written=result.written
        )

    def rename_entry(self, entry: GridEntry, new_name: str) -> RenameResult:
        """给照片或相册改名，并把它的缩略图缓存挪到新路径。

        这是少数会动用户文件的操作，规矩见 ``core.rename`` 和 ADR 0004。
        """
        result = rename_entry(self._root, entry, new_name, events=self.events)
        if result.renamed:
            self._relocate_thumbnail(result.previous, result.entry)
        return result

    def move_to_trash(self, entries: Sequence[GridEntry]) -> TrashResult:
        """把照片或相册移进系统废纸篓（界面上就是「删除」）。

        规矩见 ``core.trash`` 与 ADR 0008：**不销毁文件**，同卷内只是一次改名。
        缩略图缓存刻意留着不删 —— 照片还在，撤销回来时直接命中，不必重新解码。
        """
        return trash_entries(self._root, entries)

    def copy_into(
        self,
        target: Path,
        sources: Sequence[Path],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PasteResult:
        """把 ``sources`` **复制**进 ``target``（界面上是「粘贴」⌘V 与「复制到相册」）。

        规矩见 ``core.paste`` 与 ADR 0018：**只增不改**（源文件一个字节不动）、**绝不覆盖**
        （同名按访达的办法改叫「一张 2.jpg」）、**不在撤销栈里**（撤销粘贴就是删文件）。新粘
        进来的文件由监听器（ADR 0013）自己发现并显示到界面，这里刻意不碰界面那一层。
        """
        return copy_into(self._root, target, sources, should_cancel=should_cancel)

    def move_into(
        self,
        target: Path,
        sources: Sequence[Path],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> MoveResult:
        """把 ``sources`` **移动**进 ``target``（界面上就是「移动到相册」）。

        规矩见 ``core.transfer`` 与 ADR 0019：这是唯一会让用户的文件**离开原处**的操作，只在用户
        亲手点了菜单项时才发生，且**进撤销栈**（⌘Z 把它挪回原相册），与粘贴一样**绝不覆盖**。
        **事件绑定跟着走**（ADR 0043 决策 8），撤销也跟回来：绑定按路径记，而移动改的是绝对路径。
        """
        return move_into(
            self._root, target, sources, should_cancel=should_cancel, events=self.events
        )

    def duplicate(
        self,
        sources: Sequence[Path],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PasteResult:
        """把 ``sources`` 各自**复制到它自己所在的那一层**（界面上是「复制」⌘D，ADR 0044）。

        名字是 ``一张.jpg`` → ``一张的副本.jpg``，撞名退成 ``一张的副本 2.jpg``。规矩与
        :meth:`copy_into` 完全一致，**因为它就是那一条路**：只增不改、绝不覆盖、不在撤销栈里
        （撤销一次复制就是删文件，而删除只能用户亲手触发）；目标目录不用挑，新副本由监听器自己显示。
        """
        return duplicate(self._root, sources, should_cancel=should_cancel)

    def create_album(self, parent: Path, name: str = DEFAULT_ALBUM_NAME) -> CreatedAlbum:
        """在 ``parent`` 里新建一个空相册（界面上是「新建相册」⇧⌘N）。

        规矩见 ``core.metadata.album`` 与 ADR 0022：只增不改、绝不覆盖（同名改叫
        「新建相册 2」）、**不进撤销栈**（撤销就是删文件夹，而删除只能用户亲手触发），但记账。
        新建出来的是**普通的空文件夹**，用户往里丢什么、怎么整理，Gallery 都不管。
        """
        return create_album(self._root, parent, name)

    def undo_last_write(self) -> UndoResult | None:
        """撤销最近一次写操作（改名、移到废纸篓、移动到相册、编辑元数据或改事件）。

        没有可撤销的记录时返回 ``None``。
        """
        result = undo_last_write(self._root, writer=self._writer, events=self.events)
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


def _relative_posix(root: Path, path: Path) -> str | None:
    """``path`` 相对 ``root`` 的 posix 写法；不在库内、或它就是库根本身，返回 ``None``。

    与 :meth:`Library.album_label` 只是对「库根本身」的处置不同（那边写文件夹的名字，
    这一边当成「没有」）：库根在网格上不是一张卡片，「给它设封面」根本不该成立
    （ADR 0028 决策 2）。两者走的是同一个 :func:`gallery.core.paths.relative_posix`。
    """
    return paths.relative_posix(root, path) or None


def _draft_identity(draft: EventDraft) -> tuple[str, str, str]:
    """一份演出草稿的判重键（与 :meth:`Library._show_identities` 同一套字段）。"""
    return show_identity(draft.text("title"), draft.text("started_on"), draft.text("venue"))


#: 封面文件名认的后缀；不在其中的（含查不到后缀的）一律按 ``.jpg`` —— 源站给的多半是 jpg。
_COVER_SUFFIXES: Final = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def _cover_name(movie: MovieInfo) -> str:
    """封面写进用户文件夹时叫什么：``IPX-660.jpg``（编号 + 从地址认出来的后缀）。"""
    code = movie.code.strip() or "cover"
    first = movie.cover_urls[0] if movie.cover_urls else ""
    lowered = first.lower().split("?", 1)[0]
    suffix = next((item for item in _COVER_SUFFIXES if lowered.endswith(item)), ".jpg")
    return f"{code}{suffix}"


def _paths_of(photos: Iterable[PhotoRef | Path]) -> list[Path]:
    """把「照片引用或裸路径」一律落成路径。

    三个事件入口（查询归属、绑定、解绑）都要这一步：界面有时手上的是一整行
    :class:`PhotoRef`，有时只有一个路径（拖进来的、从别处拼的）。
    """
    return [photo.path if isinstance(photo, PhotoRef) else Path(photo) for photo in photos]


__all__ = [
    "PLACE_PASS_BATCH",
    "Library",
    "MetadataWriteOutcome",
    "MovieImportOutcome",
    "MovieImportPlan",
    "PlacePass",
    "ShowImportOutcome",
    "ShowImportPlan",
]
