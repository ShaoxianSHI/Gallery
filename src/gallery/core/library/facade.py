"""库门面：界面层访问文件系统的唯一入口。

界面**不允许**自己 ``open()`` 原图，也不允许自己拼路径去读缓存 —— 一切都要经过这里，
这样「哪些操作会碰用户的文件、哪些只会碰缓存」在代码里始终是可审计的。

打开一个库（``Library.open``）是**只读**的：它不创建任何目录、不写任何文件。
创建配置目录是 ``gallery.core.library.config`` 里 ``prepare_library`` 的职责，
且必须由界面在用户同意后显式调用。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Container, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from gallery.core import paths
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library.config import LibraryConfig, load_config
from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import FolderRef, GridEntry, PhotoRef
from gallery.core.library.scanner import (
    ScanReport,
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
from gallery.core.metadata.paste import PasteResult, copy_into
from gallery.core.metadata.rename import RenameResult, rename_entry
from gallery.core.metadata.transfer import MoveResult, move_into
from gallery.core.metadata.trash import TrashResult, trash_entries
from gallery.core.metadata.undo import UndoResult, next_undo_label, undo_last_write
from gallery.core.places.dataset import is_real_coordinate

if TYPE_CHECKING:
    from gallery.core.places.resolver import PlaceResolver, Status

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
        # 索引只在真的用到分组 / 排序时才打开 —— 打开库本身仍然一个字节都不写。
        self._index: FacetIndex | None = None

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

    def albums(self, *, report: ScanReport | None = None) -> list[FolderRef]:
        """库里**全部**的相册，按相对路径排序 —— 「复制到相册 / 移动到相册」的目标清单。

        只读，一次列完而不是流式：菜单要的是一张完整的清单（用户是照着它挑的，缺一半
        比慢一点糟糕得多），而一个相册目录的规模最多是几千项，列一次约几毫秒。

        菜单**每次弹出时现列**，因此用户在访达里新建的相册立刻就能选到，不必重开软件。
        """
        return list(iter_albums(self._root, report=report))

    def album_label(self, folder: Path) -> str:
        """相册在菜单里显示的名字：相对库根的路径（``2025/苏州``）。

        顶层相册就是它自己的名字。**用相对路径而不是单纯的名字**是必须的：两个不同
        的年份下面各有一个「苏州」，菜单里光写「苏州」用户分不清自己要挪到哪去。
        """
        target = Path(folder)
        try:
            relative = target.resolve().relative_to(self._root.resolve())
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
        """相册封面：文件夹里（含更深的层级）找到的第一张照片，没有则 ``None``。

        用「找到第一张就停」的搜索做封面：整棵树可能很大，而封面只需要一张。

        :param skip: 这些照片当过封面却读不出来（云盘上没下完的那些），跳过它们往后找。
            少了它，一张下到一半的照片会让整张相册卡片一直挂着它 —— 用户看到的是一张
            永远出不来、也换不掉的黑封面。全部被跳过时返回 ``None``，由界面决定说什么。
        """
        folder = Path(folder)
        if not folder.is_dir():
            return None
        for ref in iter_photos(folder, should_cancel=should_cancel):
            if ref.path not in skip:
                return ref
        return None

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
        """清空本库的缩略图缓存，返回删除的条目数。"""
        return self._thumbnails.clear()

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
        """facet 缓存（首次调用时才打开数据库）。"""
        if self._index is None:
            self._index = FacetIndex.open(self._root, in_library=self._cache_in_library)
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
        """撤销最近一次写操作（改名、移到废纸篓或移动到相册）；没有可撤销的记录时返回 ``None``。"""
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


__all__ = ["PLACE_PASS_BATCH", "Library", "PlacePass"]
