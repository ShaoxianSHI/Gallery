"""实时监听库目录树的变化（README 路线图 M4，决策见 ADR 0013）。

职责只有一条：**把文件系统的事件流，归并成一批「哪个路径新增 / 消失 / 改名 / 内容变了」
交给上层**。至于网格怎么插行、缓存怎么作废，是界面与 core 其他模块的事，这里一概不做。

三个设计要点：

- **只读**。这里只 ``stat``、不写不删，对用户文件的磁盘风险为零（红线 1）。
- **必须去抖**。访达拷 500 张照片就是 500 个创建事件，一个一个处理等于重扫 500 次；
  所以这里按路径合并，并等一段静默之后才交出一批（见 :data:`DEBOUNCE_MS`）。
- **不认识 Qt、不认识事件循环**。回调在自己的线程上被调用（去抖定时器同样不在主线程），
  要搬回主线程是界面层的事（见 ``ui/watch.py``）。

「跳过什么」的规则不在这里重写一遍，统一用 :func:`gallery.core.library.scanner.is_visible_path`
——扫描与监听必须是同一套，否则监听会报出扫描根本不看的文件。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from watchdog.events import (
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MODIFIED,
    EVENT_TYPE_MOVED,
    FileSystemEvent,
    FileSystemEventHandler,
    FileSystemMovedEvent,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from gallery.core.library.photos import is_supported_photo
from gallery.core.library.scanner import is_visible_path

logger = logging.getLogger(__name__)

#: 静默这么久之后把攒下的一批交出去（毫秒）。
#:
#: 访达拷贝一批照片时事件是连着来的，这个值要够大才不会把一次拷贝拆成几十次网格改动；
#: 又要够小，用户松手之后不该等太久才看到照片出现。
DEBOUNCE_MS: Final = 400

#: 一直有变化时最多攒这么久（毫秒）也交一批。
#:
#: 没有这一条，一个慢慢拷进来的大目录会一直攒着，直到拷完才一起出现 —— 那时用户
#: 早就以为没反应了。有它就变成「一批批地出现」。
MAX_HOLD_MS: Final = 3000

#: 停监听时等待 watchdog 线程收尾的上限（秒）。
STOP_JOIN_SECONDS: Final = 2.0

#: 给新出现的目录补挂监听时向下找的层数上限。
#:
#: 一次拖进来一整棵深目录树是常事，但无限深也没意义：这个上限只在非 macOS 平台
#: 起作用（见 :data:`_NATIVE_RECURSIVE`）。
MAX_SUBTREE_DEPTH: Final = 16

#: macOS 的 FSEvents 后端本身就是递归的（watchdog 自己在源码里注明「fsevents 默认
#: 就是递归的」，只是把非递归的监听过滤成只报直接子项），因此**一个根目录的递归监听
#: 已经覆盖整棵子树**。别的平台（inotify）不递归，新目录必须逐个补挂。
_NATIVE_RECURSIVE: Final = sys.platform == "darwin"


class ChangeKind(Enum):
    """变化到界面这一侧看到的四种语义。"""

    #: 新出现了（新建，或从别处移进来）。
    ADDED = "added"
    #: 不在了（删除，或被移到库外）。
    REMOVED = "removed"
    #: 还在，只是换了位置（访达改名、拖到另一个相册）。
    MOVED = "moved"
    #: 还在同一个位置，但内容变了（外部工具改过 EXIF、覆盖写、大图另存）。
    MODIFIED = "modified"


@dataclass(frozen=True, slots=True)
class LibraryChange:
    """一条变化。路径都已经被解析成库内的绝对路径。"""

    kind: ChangeKind
    path: Path
    #: 改名 / 移入移出时的新位置；其余情况没有。
    dest_path: Path | None = None
    #: 说的是不是文件夹。
    is_dir: bool = False

    @property
    def touches(self) -> tuple[Path, ...]:
        """这次变化涉及的路径（改名的两端）。"""
        return (self.path,) if self.dest_path is None else (self.path, self.dest_path)


_KIND_BY_EVENT: Final[dict[str, ChangeKind]] = {
    EVENT_TYPE_CREATED: ChangeKind.ADDED,
    EVENT_TYPE_DELETED: ChangeKind.REMOVED,
    EVENT_TYPE_MOVED: ChangeKind.MOVED,
    EVENT_TYPE_MODIFIED: ChangeKind.MODIFIED,
}
# 打开 / 关闭文件也会报事件（macOS 上尤其多）。它们只说明「有人读了一下这个文件」，
# 对相册软件没有意义，放进来只会让界面无缘无故重画。


def coalesce(changes: Iterable[LibraryChange]) -> tuple[LibraryChange, ...]:
    """把一段时间里先后到达的变化合并成「每个路径一条」。

    纯函数，不碰线程也不碰 Qt，因此可以逐条单测。三条合并规则：

    - **新增之后又改** → 还是新增：界面反正要重新取一次缩略图与元数据，
      分成两条只会多做一次无用功。
    - **新增之后又删** → 什么都没有：拷贝中途取消、编辑器留下的临时文件。
    - **同一路径删除之后又出现** → 当作内容变了：网格上那一行一直在，
      只是里面的东西换了。

    改名的两端由 watchdog 合成 :class:`~watchdog.events.FileSystemMovedEvent` 一起送来，
    所以这里不做「删除 + 新增 = 改名」的猜测 —— 访达拖 3 张照片进来也会长成那样，
    猜错会把新照片的格子继承成旧照片的缩略图。
    """
    merged: dict[Path, LibraryChange | None] = {}
    order: list[Path] = []
    for change in changes:
        if change.kind is ChangeKind.MOVED and change.dest_path is not None:
            # 改名的旧身份结束、新身份开始：两端各自此前攒下的都作废。
            for path in change.touches:
                if path in merged:
                    merged[path] = None
            merged[change.dest_path] = change
            if change.dest_path not in order:
                order.append(change.dest_path)
            continue
        previous = merged.get(change.path)
        combined = _combine(previous, change)
        if change.path not in merged:
            order.append(change.path)
        merged[change.path] = combined
    return tuple(change for change in (merged[path] for path in order) if change is not None)


def _combine(previous: LibraryChange | None, change: LibraryChange) -> LibraryChange | None:
    """同一路径上的前后两条怎么合并；``None`` 表示这条路径最终什么也不必做。"""
    if previous is None:
        return change
    if previous.kind is ChangeKind.ADDED:
        if change.kind is ChangeKind.MODIFIED:
            return previous
        if change.kind is ChangeKind.REMOVED:
            return None
        return change
    if previous.kind is ChangeKind.REMOVED:
        # 文件已经不在了，之后的「改了」是残留事件：界面若照做就会去找一个不存在的
        # 文件。同一路径又冒出来（删了又拷回来）才当成内容变了 —— 那一行一直在。
        if change.kind is ChangeKind.ADDED:
            return LibraryChange(kind=ChangeKind.MODIFIED, path=change.path, is_dir=change.is_dir)
        return previous
    if previous.kind is ChangeKind.MOVED and change.kind is ChangeKind.MODIFIED:
        return previous  # 改完名又被改内容：改名这条信息更重要
    return change


class _Handler(FileSystemEventHandler):
    """把 watchdog 的事件翻成 :class:`LibraryChange`，当场丢掉不关心的。"""

    def __init__(self, root: Path, ingest: Callable[[LibraryChange], None]) -> None:
        super().__init__()
        self._root = root
        self._ingest = ingest

    def on_any_event(self, event: FileSystemEvent) -> None:
        change = _to_change(self._root, event)
        if change is not None:
            self._ingest(change)


def _to_change(root: Path, event: FileSystemEvent) -> LibraryChange | None:
    """事件 → 变化。类型对不上、路径扫描看不见，一律 ``None``。"""
    if isinstance(event, FileSystemMovedEvent):
        return _moved_change(root, event)
    kind = _KIND_BY_EVENT.get(event.event_type)
    # MOVED 一定走上面那条分支；落到这里的 MOVED 说明事件形态不认识，宁可放过。
    if kind is None or kind is ChangeKind.MOVED:
        return None
    path = _normalize(root, os.fsdecode(event.src_path))
    if path == root or not is_visible_path(root, path):
        # 库根本身不是网格上的一条（它就是这一层的容器）。macOS 上 FSEvents 一开始
        # 监听就会先报一条根目录的「创建」，滤掉它才不至于每次开库都白动一次界面。
        return None
    if not event.is_directory and not is_supported_photo(path):
        # 扫描同样不收它：``读我.txt``、``.cr2``（RAW 还没支持）、编辑器临时文件……
        # 报上去只是让界面白忙一趟。目录没有扩展名，这条规则只对文件成立。
        return None
    return LibraryChange(kind=kind, path=path, is_dir=event.is_directory)


def _normalize(root: Path, raw: str) -> Path:
    """把事件里的路径对齐到库内部用的那种写法。

    库根在 ``Library.open`` 里已经解析过，扫描产出的路径因此都是解析后的绝对路径；
    事件给的未必是（macOS 上 ``/var`` 链到 ``/private/var``），不对齐的话同一个
    文件会顶着两个路径，界面里那些按路径索引的东西全都对不上。
    """
    path = Path(raw)
    try:
        return root / path.relative_to(root)
    except ValueError:
        return path.resolve()


def _moved_change(root: Path, event: FileSystemMovedEvent) -> LibraryChange | None:
    """移动的两端可能一端在库内、一端在库外，各自要报成不同的语义。

    两端都在库内 → 改名（界面可以原地换掉那一格）；移进来 → 新增；移出去 → 消失。
    移进移出废纸篓与别的盘走的正是后两条路。
    """
    source = _normalize(root, os.fsdecode(event.src_path))
    destination = _normalize(root, os.fsdecode(event.dest_path))
    here = is_visible_path(root, source)
    there = is_visible_path(root, destination)
    if here and there:
        return LibraryChange(
            kind=ChangeKind.MOVED,
            path=source,
            dest_path=destination,
            is_dir=event.is_directory,
        )
    if there:
        return LibraryChange(kind=ChangeKind.ADDED, path=destination, is_dir=event.is_directory)
    if here:
        return LibraryChange(kind=ChangeKind.REMOVED, path=source, is_dir=event.is_directory)
    return None


class LibraryWatcher:
    """监听一个库目录树。

    :param root: 库根目录。
    :param recursive: 是否监听子文件夹（跟着库的 ``recursive`` 配置）。
    :param on_change: ``(一批变化)``，**在自己的线程上被调用**（去抖定时器线程）。
        回调里不许碰界面：要用排队连接把它搬回主线程，见 ``ui/watch.py``。
    :param on_error: 回调本身出问题时的出口（可选）。
    :param debounce_ms: 静默期，见 :data:`DEBOUNCE_MS`。
    :param max_hold_ms: 连续变化时的最长攒批时间，见 :data:`MAX_HOLD_MS`。
    :param observer_factory: 造 watchdog 观察者的工厂，测试注入假的。
    """

    def __init__(
        self,
        root: Path,
        *,
        recursive: bool = True,
        on_change: Callable[[tuple[LibraryChange, ...]], None],
        on_error: Callable[[str], None] | None = None,
        debounce_ms: int = DEBOUNCE_MS,
        max_hold_ms: int = MAX_HOLD_MS,
        observer_factory: Callable[[], BaseObserver] | None = None,
    ) -> None:
        # 解析一次：事件给的路径是解析后的（macOS 上 ``/var`` 链到 ``/private/var``），
        # 两边写法不一致的话，「是不是库根本身」这种判断会漏。
        self._root = Path(root).expanduser().resolve()
        self._recursive = recursive
        self._on_change = on_change
        self._on_error = on_error
        self._debounce = max(0, debounce_ms) / 1000
        self._max_hold = max(self._debounce, max_hold_ms) / 1000
        self._factory = observer_factory if observer_factory is not None else Observer
        self._observer: BaseObserver | None = None
        self._handler = _Handler(self._root, self._ingest)
        self._lock = threading.Lock()
        self._pending: list[LibraryChange] = []
        self._timer: threading.Timer | None = None
        self._first_at = 0.0

    @property
    def running(self) -> bool:
        return self._observer is not None

    def start(self) -> None:
        """开始监听。已经在跑就什么也不做。"""
        if self._observer is not None:
            return
        observer = self._factory()
        observer.schedule(self._handler, str(self._root), recursive=self._recursive)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """停止监听并丢掉还没交出去的批次。

        关窗、切库时必须显式调用：否则 watchdog 的线程还会往一个已经销毁的窗口发信号。
        """
        observer, self._observer = self._observer, None
        with self._lock:
            timer, self._timer = self._timer, None
            self._pending.clear()
        if timer is not None:
            timer.cancel()
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=STOP_JOIN_SECONDS)

    def flush(self) -> None:
        """把攒下的变化合并成一批交出去。

        正常由去抖定时器调用；公开出来是为了让测试不必真等那 400 毫秒。
        """
        with self._lock:
            timer, self._timer = self._timer, None
            pending, self._pending = self._pending, []
        if timer is not None:
            timer.cancel()
        batch = coalesce(pending)
        if not batch:
            return
        try:
            self._on_change(batch)
        except Exception:
            logger.exception("处理文件变化时抛出未预期异常")
            if self._on_error is not None:
                self._on_error("监听到文件变化，但处理时出了问题，详情已写入日志。")

    def _ingest(self, change: LibraryChange) -> None:
        """收下一条变化（watchdog 的线程在调）。"""
        if change.kind is ChangeKind.MODIFIED and change.is_dir:
            # 目录的「内容变了」几乎总是某个子项变了 —— 那件事自己会报事件。
            # 目录自己只在被改名、被删掉时才需要关心。
            return
        if change.is_dir and change.kind is not ChangeKind.REMOVED:
            self._watch_new_directory(change.dest_path or change.path)
        overdue = False
        with self._lock:
            if not self._pending:
                self._first_at = time.monotonic()
            self._pending.append(change)
            now = time.monotonic()
            if now - self._first_at >= self._max_hold:
                overdue = True
            else:
                self._arm(now + self._debounce)
        if overdue:
            self.flush()

    def _arm(self, deadline: float) -> None:
        """（在锁内调用）重开去抖定时器。"""
        if self._timer is not None:
            self._timer.cancel()
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), self.flush)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _watch_new_directory(self, directory: Path) -> None:
        """给新出现的目录补挂监听 —— **只有非 macOS 平台需要**，理由见模块文档。

        拖进来一整棵目录树时要一路挂下去，所以是递归的；层数封顶是为了不给
        「拷贝一个家目录进来」这种事无限展开。
        """
        observer = self._observer
        if observer is None or not self._recursive or _NATIVE_RECURSIVE:
            return
        stack: list[tuple[Path, int]] = [(directory, 0)]
        while stack:
            current, depth = stack.pop()
            if depth > MAX_SUBTREE_DEPTH or not is_visible_path(self._root, current):
                continue
            observer.schedule(self._handler, str(current), recursive=True)
            try:
                with os.scandir(current) as entries:
                    children = [
                        Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)
                    ]
            except OSError:
                continue
            stack.extend((child, depth + 1) for child in children)


def summarize(changes: Sequence[LibraryChange]) -> str:
    """一句话说明这批变化（记日志用；界面自己的提示另有说法）。"""
    counts: dict[ChangeKind, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    labels = {
        ChangeKind.ADDED: "新增",
        ChangeKind.REMOVED: "消失",
        ChangeKind.MOVED: "改名",
        ChangeKind.MODIFIED: "内容变化",
    }
    return "、".join(f"{labels[kind]} {counts[kind]} 项" for kind in ChangeKind if kind in counts)


__all__ = [
    "DEBOUNCE_MS",
    "MAX_HOLD_MS",
    "STOP_JOIN_SECONDS",
    "ChangeKind",
    "LibraryChange",
    "LibraryWatcher",
    "coalesce",
    "summarize",
]
