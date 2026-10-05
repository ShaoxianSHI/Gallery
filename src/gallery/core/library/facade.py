"""库门面：界面层访问文件系统的唯一入口。

界面**不允许**自己 ``open()`` 原图，也不允许自己拼路径去读缓存 —— 一切都要经过这里，
这样「哪些操作会碰用户的文件、哪些只会碰缓存」在代码里始终是可审计的。

**打开一个库只在两种情况下会写**（ADR 0038 决策 2、决策 3）：建 配置目录，
以及把它下面的旧缩略图缓存清掉。建不出来时**不报错**，只是退化成内存索引 ——
界面在横幅上说一声，浏览照旧。

除缩略图（**只**在系统目录里）之外，Gallery 关于一个库的一切都在 配置目录 里：
配置、索引、写操作日志、库级界面偏好。写回 ``config.toml`` 的方法有三处
（:meth:`Library.set_album_cover` / :meth:`Library.clear_album_cover` /
:meth:`Library.set_ui_state`），都只碰 Gallery 自己的配置文件，用户的照片一个字节都不动，
而且都只在用户亲手做了某个动作之后才发生。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Container, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from gallery.core import paths
from gallery.core.errors import ConfigError, LibraryNotAccessibleError
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
from gallery.core.metadata.paste import PasteResult, copy_into
from gallery.core.metadata.rename import RenameResult, rename_entry
from gallery.core.metadata.transfer import MoveResult, move_into
from gallery.core.metadata.trash import TrashResult, trash_entries
from gallery.core.metadata.undo import UndoResult, next_undo_label, undo_last_write
from gallery.core.metadata.writeback import MetadataWriter, write_metadata
from gallery.core.places.dataset import is_real_coordinate

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver, Status

logger = logging.getLogger(__name__)

#: 解析地点时一次处理多少张照片。
#:
#: 与 exiftool 自己的批次（``batch.BATCH_SIZE``）对齐：攒满一批正好起一次子进程，
#: 进度也能一跳一跳地报，既不空转也不刷屏。
PLACE_PASS_BATCH: Final = 400

#: 写文件表时一批多少张。太小则语句条数多，太大则一次占用索引锁的时间长 ——
#: 那一把锁是界面读索引时也要用的（与 :data:`PLACE_PASS_BATCH` 同一个道理）。
PHOTO_INDEX_BATCH: Final = 2000


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
    #: 落定的字段，以及写完之后文件里会有的拍摄时间与位置。
    plan: MetadataPlan
    #: 是否留下了可回滚的日志记录。为 ``False`` 时界面要说明「⌘Z 撤销不了」。
    journaled: bool = True
    #: 是否真的动过文件。什么都没改时为 ``False``，此时连日志也没记。
    written: bool = False


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
        #: 元数据写回的工具（ADR 0030）。``None`` 表示用默认的 exiftool，
        #: 测试注入假实现 —— 界面据此判断「编辑元数据」这一条该不该出现。
        self._writer = writer
        #: 配置目录 是不是真的建出来了。为 ``False`` 时索引只在内存里用，
        #: 界面拿它决定要不要在横幅上说明「这个库只读，索引没落盘」。
        self._config_dir_ready = paths.has_config_dir(root)
        # 索引只在真的用到分组 / 排序时才打开（ADR 0009）—— 打开它本身不写任何文件。
        self._index: FacetIndex | None = None

    @property
    def writer(self) -> MetadataWriter | None:
        """元数据写回工具（界面判断功能可不可用时用 :meth:`can_write_metadata`）。"""
        return self._writer

    @property
    def config_dir_ready(self) -> bool:
        """配置目录 是不是真的存在（建不出来时为 ``False``，只读卷上会发生）。"""
        return self._config_dir_ready

    @classmethod
    def open(cls, root: Path, *, cache_base: Path | None = None) -> Library:
        """校验并打开库。

        **会做两件事，两件都只碰 Gallery 自己的文件**（ADR 0038 决策 2、决策 6）：

        1. 保证 配置目录 存在。没有弹窗了 —— 用户在这条路上没有第三个有意义
           的选择，建不出来就退化成内存索引（见 :meth:`config_dir_ready`）。
        2. 清掉**旧版**留在 配置目录下的 ``cache/`` 里的缩略图（缩略图已经搬去系统目录）。
           那整块是「同一个库两份缓存」里多出来的那一份，全是可再生的缩略图。

        :param cache_base: 系统级缩略图缓存根目录，测试用；``None`` 表示走平台默认位置。
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

        prepare_library(root)
        # 两处存量清理一起做（ADR 0038 决策 6）：库内那块缩略图与库外那份索引，
        # 都是「同一个库两份缓存」留下的东西，新版一个都不再写。
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

        界面改排序、改日历层级、捏缩略图档位都走这里 —— 换库之后那些选择各自重来，
        而它们记在哪一个库里是有意义的。

        **内存那份必须一起换**，理由与 :meth:`_write_covers` 一样：刚切的那一档要立刻
        生效，等下一次打开库才变的话界面会与自己记的东西对不上。

        配置目录 建不出来时（只读卷）**什么都不做也不报错** —— 那台机器上本来就
        存不住这些，界面这一次的运行仍然照常用它。
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

        只读，一次列完而不是流式：菜单要的是一张完整的清单（用户是照着它挑的，缺一半
        比慢一点糟糕得多），而一个相册目录的规模最多是几千项，列一次约几毫秒。

        菜单**每次弹出时现列**，因此用户在访达里新建的相册立刻就能选到，不必重开软件。
        """
        return list(iter_albums(self._root, report=report))

    def has_albums(self) -> bool:
        """库里**有没有**相册 —— 找到一个就停。

        **只为了「有没有」而问，却把它整列一遍**，那是把右键菜单最贵的一步
        （3000 个相册约 120 ms 的目录遍历）又付回了构造菜单的那一刻，而用户点开那个
        子菜单的时机才是真正需要清单的时候。

        判据在 :func:`~gallery.core.library.scanner.has_album`，可见性规则与
        :func:`~gallery.core.library.scanner.iter_albums` 完全一致（隐藏项、配置目录、
        符号链接一律不算）—— 这两条必须一起看，否则菜单会出现一个挑了也没用的条目。
        """
        return has_album(self._root)

    def album_label(self, folder: Path) -> str:
        """相册在菜单里显示的名字：相对库根的路径（``2025/苏州``）。

        顶层相册就是它自己的名字。**用相对路径而不是单纯的名字**是必须的：两个不同
        的年份下面各有一个「苏州」，菜单里光写「苏州」用户分不清自己要挪到哪去。
        """
        target = Path(folder)
        try:
            # 带记忆的解析（见 :func:`gallery.core.paths.resolved`）：菜单**每次弹出**
            # 都要给**每个**相册算一遍，而这些路径全都来自同一个已经解析过的库根
            # （见 ``Library.open``）。两千个相册的库，一次右键就是四千趟 ``lstat``，
            # 而答案与上一次逐字相同。
            relative = paths.resolved(target).relative_to(paths.resolved(self._root))
        except (OSError, ValueError):
            return target.name
        # ``relative_to`` 对「就是它自己」给出 ``Path('.')``，写出来是 "." ——
        # 菜单里一个孤零零的点。库根没有相对路径可写，那就写它自己的名字。
        return relative.as_posix() if relative.parts else target.name

    def folder_cover(
        self,
        folder: Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        skip: Container[Path] = (),
    ) -> PhotoRef | None:
        """相册封面：用户指定的那一张，没有指定就找文件夹里（含更深的层级）的第一张，
        再没有则 ``None``。

        用「找到第一张就停」的搜索做封面：整棵树可能很大，而封面只需要一张。

        :param skip: 这些照片当过封面却读不出来（云盘上没下完的那些），跳过它们往后找。
            少了它，一张下到一半的照片会让整张相册卡片一直挂着它 —— 用户看到的是一张
            永远出不来、也换不掉的黑封面。全部被跳过时返回 ``None``，由界面决定说什么。
        """
        folder = Path(folder)
        if not folder.is_dir():
            return None
        # **指定的那一张排第一**（ADR 0028）：它也照 ``skip`` 走 —— 指定的那张读不出来时
        # 退回「第一张照片」，而不是让整张卡片一直黑着。
        chosen = self.album_cover(folder)
        if chosen is not None and chosen.path not in skip:
            return chosen
        for ref in iter_photos(folder, should_cancel=should_cancel):
            if ref.path not in skip:
                return ref
        return None

    def album_cover(self, folder: Path) -> PhotoRef | None:
        """这个相册**被指定过**的封面照片。

        没指定、指定的那张已经不在了、或者它现在压根不是一张能读的照片，都返回
        ``None`` —— 调用方据此退回「里面第一张照片」（ADR 0028 决策 4）。

        那一行配置**不会因此被删掉**：用户把照片挪出去又挪回来时，它又该是封面了。
        """
        key = _relative_posix(self._root, folder)
        if key is None:
            return None
        stored = self._config.album_covers.get(key)
        if stored is None:
            return None
        path = self._root / stored
        # 先按扩展名与「是不是文件」筛掉，再 stat：一张读不出来的照片留给流水线去报错
        # （它有换下一张当封面的机制），这里只挡「压根不是照片」的那些。
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
        """把 ``photo`` 记成 ``album`` 的封面，写进 ``config.toml``（ADR 0028）。

        **只写 Gallery 自己的配置文件**，用户的照片一个字节都不动（红线 1）——
        因此它既不记 journal 也不进撤销栈：那一摞栈装的是「动了用户文件」的那些操作。
        撤销由对称的 :meth:`clear_album_cover` 负责（决策 5）。

        :raises ConfigError: 封面照片不在这个相册里、不是一张受支持的照片、
            或者这个库压根没有配置目录（用户当初选的是「缓存到系统目录」）。
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
            # 与 ``config.from_mapping`` 里那条校验同一个判据：写进去的和读得出来的
            # 必须是同一种键，否则这条设置在下一次打开库时就成了一句报错。
            raise ConfigError(
                f"封面得是「{Path(album).name}」里的照片，「{photo.name}」不在这个相册里。",
                path=photo,
            )
        self._write_covers({**self._config.album_covers, album_key: photo_key})

    def clear_album_cover(self, album: Path) -> bool:
        """取消这个相册的指定封面，回到「里面第一张照片」。本来就没有就返回 ``False``。

        没设置过就**什么都不写** —— 一次没有任何变化的配置改写只是白白多一次
        ``fsync``，还可能在只读的库上凭空造出一个报错。
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
            if album.resolve() == self._root:
                raise ConfigError(
                    "库根目录不是一个相册，它在网格上不是一张卡片，没有封面可设。",
                    path=album,
                )
            raise ConfigError(f"「{album.name}」不在这个库里，没法给它设封面。", path=album)
        return key

    def _write_covers(self, covers: Mapping[str, str]) -> None:
        """把封面那张表写回 ``config.toml``，并让内存里的配置跟着更新。

        **内存那份必须一起换**：封面是刚写进去的，同一个窗口里下一次要画那张卡片时
        问的就是内存里这份 —— 只写文件不换内存，用户会看着自己刚设的封面没换上，
        直到下一次打开库才生效。
        """
        if not self._config_dir_ready:
            # 只读卷上 配置目录 建不出来，封面设置没有地方存。
            # 那台机器上本来也存不住，所以只说清楚，不硬写。
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

        索引是另一份缓存，但它记的也是「像素之外的东西」，所以「清空缓存」在用户看来
        是一次操作，在代码里就该是一次操作 —— :meth:`FacetIndex.clear` 的文档写着
        「清空缩略图缓存时一并清掉，避免两边的内容对不上」，而它原先**一个调用点都没有**，
        于是「清空缩略图缓存」清掉了图、留下了搜索：用户看到的正是那句
        「缩略图清了但搜索还在」。

        只清 facet 表是不够的（那样检索会立刻拿一份刚被丢掉的文件表继续查），
        所以两张表一起清。代价是下一次分组要重新读一遍元数据 —— 那本来就是
        「缓存没了」的应有之义，而重新读出来的东西与磁盘上完全一致（红线 5）。
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

        监听范围跟着库配置的 ``recursive``：用户说不递归，就别去翻他的子文件夹。
        回调在自己的线程上被调用，界面要用排队连接把它搬回主线程（ADR 0012）。

        监听是纯读的：只 ``stat``，不写不删。
        """
        return LibraryWatcher(
            self._root,
            recursive=self._config.recursive,
            on_change=on_change,
            on_error=on_error,
        )

    def forget_paths(self, gone: Sequence[Path]) -> int:
        """把已经不在库里的路径从索引中抹掉，返回删掉了几行。

        照片消失之后索引里那一行不会自己走。**只删给定的路径**：相册模式下
        「当前这一层」不等于「整个库」，按层去重会误伤别的相册（ADR 0012）。

        索引还没打开时什么也不做：为了删几行去建一个数据库不值得。
        """
        if self._index is None or not gone:
            return 0
        return self._index.forget(gone)

    # ---- 分组 / 排序用的元数据（只读）----------------------------------------

    @property
    def facet_index(self) -> FacetIndex:
        """facet 缓存（首次调用时才打开数据库）。

        配置目录 建不出来时**不开盘**——退回纯内存缓存，本次会话照常能用，
        只是下次打开要重读一遍 EXIF。
        """
        if self._index is None:
            path = paths.index_file(self._root) if self._config_dir_ready else None
            self._index = FacetIndex(path)
        return self._index

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
        """取这些照片的分组 / 排序字段，**只读**。

        先查缓存，只对没命中或已改过（大小 / 修改时间变了）的那些去真读 EXIF；
        读到的新结果顺手写进索引，于是下一次切换分组字段几乎立刻完成。

        **地点**：相机原图几乎不写 IPTC / XMP 地点，只写 GPS。给了 ``resolver``
        就会按坐标离线反查一次，把国家 / 省州 / 城市补齐（文件里写了就以文件为准），
        结论同样进索引，所以只反查一次。没给 ``resolver`` 就只认文件里写的地点 ——
        地点那三栏会显示成「未知」，而不是悄悄替用户联网查点什么。

        :param source: 替换默认的 exiftool 批量来源（测试用）。
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
            # 缓存里可能还留着「有坐标、没结论」的旧行（打开库时的那一遍还没跑到它，
            # 或者城市数据是后来才下好的）。既然反查器在手，就顺手把它们补上 ——
            # 补完写回索引，于是这类照片不会被永久钉在「未知」上。
            #
            # **两件事与上面那一段是并行的，不是二选一。** 曾经这里是 ``elif``，
            # 于是「这一批里有任何一张没缓存过」就整段跳过 —— 而那恰恰是最常见的
            # 情形（打开一个库，总有新照片）：旧行永远补不上，直到哪一次恰好全命中。
            # 两个分支各干各的：上面读新的，这里补旧的。
            by_path = {ref.path: ref for ref in refs}
            pending = [path for path, facets in cached.items() if _needs_lookup(facets)]
            resolved: list[tuple[PhotoRef, PhotoFacets]] = []
            for path in pending:
                if should_cancel is not None and should_cancel():
                    break
                facets = _resolved(cached[path], resolver, status)
                cached[path] = facets
                resolved.append((by_path[path], facets))
            # **只写回真正改动过的那几行。** 曾经这里把 ``cached`` 里**每一行**都
            # UPSERT 一遍 —— 五万张照片的库里哪怕只有一行待补，也是五万次 UPDATE。
            if resolved:
                index.store(resolved)
        return cached

    def cached_facets(self, refs: Sequence[PhotoRef]) -> dict[Path, PhotoFacets]:
        """索引里**仍然有效**的那些（没读过的、文件被改过的都不算）。

        元数据面板靠它显示地点：结论已经在库里就直接显示，不必在面板上再查一遍
        （ADR 0010）。纯读缓存，不碰 EXIF，因此可以放在主线程上调用。
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

    def index_photos(
        self,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_work: Callable[[int], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """把整个库的照片写进索引的文件表，返回新写了多少行。

        **只有真的有活干才起**（文件表一行都没有），与 :meth:`resolve_places`
        同一个套路（ADR 0010、ADR 0026 决策 5）：打开库时顺手填好，之后每次检索
        就只是一次查表。否则用户第一次搜索看到的是「一条都没有」，而那不是答案，
        是索引还没建。

        幂等，第二次几乎不要钱：``(大小, 修改时间)`` 对得上的行根本不写。

        :param on_work: ``(这次要写多少行)``，**只在真的找到活干时叫一次** ——
            已经建好索引的库这一遍是空跑，界面据此不该报进度。
        :param on_progress: ``(已扫过)``。总数事先不知道，于是只报已处理。
        """
        index = self.facet_index
        if index.count_photos() > 0:
            return 0
        written = 0
        announced = False
        scanned = 0
        batch: list[PhotoRef] = []
        for ref in self.iter_photos(should_cancel=should_cancel):
            scanned += 1
            batch.append(ref)
            if len(batch) < PHOTO_INDEX_BATCH:
                continue
            if should_cancel is not None and should_cancel():
                break
            written += index.store_photos(batch, root=self._root)
            batch.clear()
            if not announced:
                announced = True
                if on_work is not None:
                    on_work(scanned)
            if on_progress is not None:
                on_progress(scanned)
        if batch and not (should_cancel is not None and should_cancel()):
            written += index.store_photos(batch, root=self._root)
        if on_progress is not None:
            on_progress(scanned)
        return written

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
        # 没截断时总数可以给准的：**索引里可能留着文件早已被删掉的那些行**
        # （监听器还没来得及清、或这一遍压根没起过），把它们算进「共 N 张」
        # 就是在说一件不存在的事。逐条问一次还在不在 —— 结果集顶多几千条。
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
        """按坐标反查**这一张**的地点，并把结论并回索引。

        坐标由调用方给出（元数据面板手里已经读到了），所以不必再读一次 EXIF。
        这是 :meth:`resolve_places` 之外的兜底：打开时那一遍还没轮到这张照片，
        或者它是后来才加进库的（ADR 0010）。

        **只并进已有的那一行，不新写一行**：索引里还存着拍摄时间、相机那些字段，
        写半行会挡住以后那次完整的 EXIF 读取（那一行会被当成「已经读过了」）。
        没有有效行就只返回结论，等整库那一遍来补。
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
        """把整个库里还没有地点结论的照片补齐，**只读照片、只写自己的缓存**。

        第一次打开一个库就跑一遍（ADR 0010），跑完之后按国家 / 省州 / 城市分组与
        元数据面板里的地点都直接读索引，不必各自再算一遍。

        幂等，而且第二次几乎不要钱：

        - 已有结论的行（有坐标且 ``place_checked``）**连 EXIF 都不读**；
        - 没有坐标的行同样跳过 —— 它永远没得反查；
        - 文件被改过的行按 ``(大小, 修改时间)`` 判为过期，重读一次（可能刚补上 GPS）。

        顺带的好处是这一遍把整库的 facet 都填上了，于是按年份、相机分组也变成立刻完成。

        :param on_work: ``(这次要处理多少张)``，**只在真的找到活干时叫一次**。
            库已经解析过时这一遍是空跑，界面靠它判断该不该报进度 ——
            「正在解析…」这句话只在真有活的时候出现（否则它在说一件没发生的事）。
        :param on_progress: ``(已扫过, 总数)``。边扫边数，所以**总数通常是 0**
            （表示还不知道）；界面据此只报已处理多少。
        :param status: 首次加载城市库时，用它把进度说给界面听。
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

        batch: list[PhotoRef] = []
        for ref in self.iter_photos(should_cancel=should_cancel):
            scanned += 1
            batch.append(ref)
            if len(batch) < PLACE_PASS_BATCH:
                continue
            if should_cancel is not None and should_cancel():
                break
            resolved += _resolve_batch(
                index,
                batch,
                resolver=resolver,
                status=status,
                source=source,
                should_cancel=should_cancel,
                on_work=announce,
            )
            batch.clear()
            if on_progress is not None:
                on_progress(scanned, 0)
        if batch and not (should_cancel is not None and should_cancel()):
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
            on_progress(scanned, scanned)  # 没有总数时最后补一次收尾
        return PlacePass(scanned=scanned, resolved=resolved)

    def close_index(self) -> None:
        """关掉索引连接（切库 / 关窗）。"""
        if self._index is not None:
            self._index.close()
            self._index = None

    # ---- 写操作（唯一会改动用户文件的一类）------------------------------------

    def can_write_metadata(self) -> bool:
        """此刻能不能改照片里的元数据 —— 没装 exiftool 时为 ``False``。

        界面据此**不出现**「编辑元数据」那一条，而不是摆一条点下去只会报错的命令
        （与「粘贴」同一个取向，ADR 0014 决策 4）。
        """
        return self._writer is None or self._writer.available()

    def write_metadata_edit(
        self,
        ref: PhotoRef,
        state: MetadataState,
        edit: MetadataEdit,
        *,
        resolver: PlaceResolver | None = None,
    ) -> MetadataWriteOutcome:
        """把用户编辑的元数据**写回照片文件**（ADR 0030 / 0031 / 0032）。

        四步都在这里做完，界面只管「什么时候调」：

        1. :func:`~gallery.core.metadata.metadata_edit.plan_metadata_edit` 把「照片此刻的值」
           与「用户填完的那一份」翻译成一组字段 —— 拍摄时间、坐标与三层地名**一次算完**；
        2. :func:`~gallery.core.metadata.writeback.write_metadata` 写盘、**回读校验**、
           必要时回滚、记账；
        3. **缩略图缓存换键**：文件的大小与修改时间都变了，而缩略图的键正是
           ``(路径, mtime, size)``。像素一个字节没动，所以把缓存条目挪到新键上，
           省掉一次重新解码；
        4. 返回新的引用 —— **界面必须拿它换掉网格里那一行**，否则那一行挂着的还是旧的
           大小与修改时间（缓存与索引都按它判新旧）。

        **一次编辑只写一次**：时间与位置在同一个对话框里，分成两次写回会让用户按一次保存
        却要撤两下（ADR 0032 决策 4）。

        索引那一行**故意什么都不做**：``(大小, 修改时间)`` 对不上就自动作废，下次分组或
        排序时重读一次就更新了。写回原文件而不是改索引，索引就永远是 EXIF 的投影，
        两处不可能说两套话。

        :raises MetadataWriteError: 坐标不合法、时间或时区看不懂、写盘失败、
            或**写完校验不过**（已回滚）。
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

    def copy_into(
        self,
        target: Path,
        sources: Sequence[Path],
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PasteResult:
        """把 ``sources`` **复制**进 ``target``（界面上是「粘贴」⌘V 与「复制到相册」）。

        规矩与红线见 ``core.paste`` 与 ADR 0018：**只增不改**（源文件一个字节不动）、
        **绝不覆盖**（同名就按访达的办法改叫「一张 2.jpg」）、**不在撤销栈里**
        （撤销粘贴就是删文件）。新粘进来的文件由监听器（ADR 0013）自己发现并上屏，
        这里刻意不去动界面那一层。
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

        规矩与红线见 ``core.transfer`` 与 ADR 0019：这是唯一会让用户的文件**离开原处**
        的操作，因此只在用户亲手点了菜单项时才发生，且**进撤销栈** —— ⌘Z 把它挪回原来的
        相册（撤销一次移动仍然是一次移动，不是删除）。与粘贴一样**绝不覆盖**。
        """
        return move_into(self._root, target, sources, should_cancel=should_cancel)

    def create_album(self, parent: Path, name: str = DEFAULT_ALBUM_NAME) -> CreatedAlbum:
        """在 ``parent`` 里新建一个空相册（界面上是「新建相册」⇧⌘N）。

        规矩与红线见 ``core.metadata.album`` 与 ADR 0022：只增不改、绝不覆盖（同名改叫
        「新建相册 2」）、**不进撤销栈**（撤销就是删文件夹，红线 1 不允许），但记账。
        新建出来的是**普通的空文件夹**，用户往里丢什么、怎么整理，Gallery 都不管。
        """
        return create_album(self._root, parent, name)

    def undo_last_write(self) -> UndoResult | None:
        """撤销最近一次写操作（改名、移到废纸篓、移动到相册或编辑元数据）。

        没有可撤销的记录时返回 ``None``。
        """
        result = undo_last_write(self._root, writer=self._writer)
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

    与 :meth:`Library.album_label` 是同一套理由：库根与扫描产出的路径都已经解析过，
    ``/var`` 与 ``/private/var`` 说的是同一个文件夹，混着比就永远对不上（macOS 上
    ``tmp_path`` 就在这条链上，测试尤其容易撞上）。

    库根自己返回 ``None`` 而不是 ``"."``：库根在网格上不是一张相册卡片，
    「给它设封面」这件事根本不该成立（ADR 0028 决策 2）。
    """
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix() if relative.parts else None


def _has_coordinates(facets: PhotoFacets) -> bool:
    """这张照片有没有一个**能用来反查**的坐标。

    经纬度同时为 0 不算：那是相机定不到 GPS 时的占位写法（见
    :func:`gallery.core.places.dataset.is_real_coordinate`）。否则这些照片每开一次库
    都会被送去反查一次，而每次都会得到同一个来自几内亚湾的错答案。
    """
    if facets.latitude is None or facets.longitude is None:
        return False
    return is_real_coordinate(facets.latitude, facets.longitude)


def _needs_place(facets: PhotoFacets) -> bool:
    """有没有坐标、却还有地点字段是空的 —— 那种行值得再反查一次。

    判据是「**还缺**」而不是「完全没有」：文件里只写了城市、省州还是空的，
    那就仍然该用反查补上（``with_resolved_place`` 一个字段一个字段地补）。
    """
    if not _has_coordinates(facets):
        return False
    return facets.city is None or facets.region is None or facets.country is None


def _needs_lookup(facets: PhotoFacets | None) -> bool:
    """这一行还有没有反查的余地（索引里那一遍要不要理它）。

    四种情况，从头到尾都要说清楚，少一种就会出现「每开一次库就重算一遍」：

    - 没有有效行（从没读过，或文件被改过）→ 要读 EXIF；
    - 有坐标但没查过 → 要反查；
    - 没坐标 → **永远**没得反查，别为它起一次 EXIF；
    - 查过 → 已经有结论了（哪怕结论是「附近没有城市」），不再问。
    """
    if facets is None:
        return True
    if not _has_coordinates(facets):
        return False
    return not facets.place_checked


def _resolved(facets: PhotoFacets, resolver: PlaceResolver, status: Status | None) -> PhotoFacets:
    """按坐标补地点，并记下「这次查过了」。

    查不到就原样返回地点字段 —— 那是结论，不是错误。数据没下好时反查器会说自己
    没准备好（``ready`` 为假），那就**不记**：这次没查成，下次还得再来一遍。
    """
    if not _needs_place(facets):
        return facets
    place = resolver.place_for(facets.latitude, facets.longitude, status=status)  # type: ignore[arg-type]
    return mark_place_resolved(with_resolved_place(facets, place), usable=resolver.ready)


def _resolve_batch(
    index: FacetIndex,
    refs: Sequence[PhotoRef],
    *,
    resolver: PlaceResolver | None,
    status: Status | None,
    source: BatchMetadataSource | None,
    should_cancel: Callable[[], bool] | None,
    on_work: Callable[[int], None] | None = None,
) -> int:
    """把这一批里「还没有地点结论」的照片补齐，返回反查了几张。

    已有结论的行直接跳过，**连 EXIF 都不读** —— 这是第二次运行几乎不要钱的原因。
    ``on_work`` 在这一批真有活干时叫（上层只会在第一次时往外说）。
    """
    cached = index.lookup(refs)
    todo = [ref for ref in refs if _needs_lookup(cached.get(ref.path))]
    if not todo:
        return 0
    if on_work is not None:
        on_work(len(todo))
    fresh = read_facets(
        [ref.path for ref in todo],
        source=source,
        should_cancel=should_cancel,
    )
    resolved = 0
    rows: list[tuple[PhotoRef, PhotoFacets]] = []
    for ref in todo:
        facets = fresh.get(ref.path)
        if facets is None:
            continue  # 压根没读出来：下次还要再试，不进索引
        if _needs_place(facets):
            if resolver is not None:
                facets = _resolved(facets, resolver, status)
                resolved += 1
        elif _has_coordinates(facets):
            # **文件自己就写全了地点**（Lightroom、Google Photos 导出的 XMP 如此）：
            # 反查器没什么可补的，于是 :func:`_resolved` 那一路走不到，
            # ``place_checked`` 也就永远记不上 —— 下一次开库会为同一张照片**再起一次
            # exiftool**，「第二次几乎不要钱」就成了空话。那份地点同样是结论（而且
            # 比反查更准），照 ADR 0010「文件里写了就以文件为准」，记下来就是。
            #
            # 判据是「**已经齐全**」而不是「查过」：地点还缺着（只有国家、没有城市）
            # 时**不能**记 —— 那等于把这张照片永久钉在「未知」上，等城市数据到位了
            # 也不会再问一遍（:func:`mark_place_resolved` 讲的就是这件事）。
            facets = mark_place_resolved(facets)
        rows.append((ref, facets))
    index.store(rows)
    return resolved


def _offset_progress(
    offset: int, on_progress: Callable[[int, int], None]
) -> Callable[[int, int], None]:
    """把「这一批的进度」翻译成「整次读取的进度」。"""

    def report(done: int, total: int) -> None:
        on_progress(offset + done, offset + total)

    return report


__all__ = ["PLACE_PASS_BATCH", "Library", "MetadataWriteOutcome", "PlacePass"]
