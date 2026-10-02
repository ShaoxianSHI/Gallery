"""缩略图流水线：把「当前窗口需要的图」变成主线程上可用的 ``QImage``。

界面线程只调用 :meth:`ThumbnailPipeline.set_window`，剩下的解码、缩放、编码落盘
全在线程池里。结果用**代次号**过滤：切库之后，旧库的任务即使跑完也不会画到新界面上。

**缩放也在工作线程做**（ADR 0015）：``QImage.scaled`` 线程安全，于是界面线程只剩
一次 ``QPixmap.fromImage``，不再和 ``QListView`` 的排版绘制抢那一次平滑缩放。为此
结果连同「这张图是缩到几才发出来的」一起交回（:attr:`ThumbnailPipeline.ready` 的
第三个参数）—— 尺寸对不上的结果会由 :meth:`PhotoListModel.needs_thumbnail` 认出来
并重取，所以切档瞬间在途的任务不会把格子填歪。

解码失败的照片会被记住并跳过，避免每次滚动都对同一个坏文件反复重试。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from gallery.core.library.facade import Library
from gallery.core.library.photos import GridEntry, GroupRef, PhotoRef
from gallery.ui.imaging import to_scaled_qimage
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumbs.queue import ThumbnailQueue

#: ``target_edge`` 的「不缩放」取值，同时也是 :attr:`ThumbnailPipeline.ready`
#: 第三个参数里表示「这张图没有按某个尺寸缩放过」的值。
NO_EDGE = 0


@dataclass(frozen=True, slots=True)
class ScaledThumbnail:
    """一张在工作线程里就缩好的缩略图。"""

    image: object
    #: 它是按这个长边缩的（``0`` = 原样，按当前尺寸直接用）。
    edge: int


class ThumbnailPipeline(QObject):
    """按窗口优先级把缩略图喂给界面。"""

    #: 一张缩略图准备好了：``(路径, QImage, 实际缩到的长边)``。
    ready = Signal(object, object, int)
    #: 一张缩略图解码失败：``(路径, 中文错误)``。
    failed = Signal(object, str)

    def __init__(self, library: Library, runner: TaskRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._runner = runner
        self._queue = ThumbnailQueue()
        self._refs: dict[Path, PhotoRef] = {}
        self._inflight: dict[Path, int] = {}
        self._failed: set[Path] = set()
        self._empty: set[Path] = set()
        self._generation = 0
        #: 当前要缩到多长。在**提交任务时**定格进闭包（见 :meth:`_start`）。
        self._target_edge = NO_EDGE
        # 在途任务不超过线程数，保证线程池永远满载，同时让「可见」优先级真正生效。
        self._max_inflight = max(1, runner.thread_count)

    def set_window(
        self,
        visible: Sequence[GridEntry],
        prefetch: Sequence[GridEntry] = (),
        *,
        target_edge: int = NO_EDGE,
    ) -> None:
        """声明当前窗口：``visible`` 先出图，``prefetch`` 次之，其余请求全部丢弃。

        条目可能是照片、相册或分组卡片：相册的封面要现去找，分组卡片的封面早就知道
        （组内第一张）。三者在这里都被归一成「一条路径 + 一个能出图的引用」。

        :param target_edge: 缩略图要缩到多长。在工作线程里做，``0`` 表示不缩放。
        """
        self._target_edge = max(NO_EDGE, int(target_edge))
        targets = [target for entry in visible if (target := _thumbnail_target(entry))]
        ahead = [target for entry in prefetch if (target := _thumbnail_target(entry))]
        self._queue.set_window(
            [target.path for target in targets],
            prefetch=[target.path for target in ahead],
        )
        for target in (*targets, *ahead):
            self._refs[target.path] = target
        self._dispatch()

    def shutdown(self) -> None:
        """切库或关窗：作废所有在途结果，并请求取消它们的任务。"""
        self._generation += 1
        for job_id in self._inflight.values():
            self._runner.cancel(job_id)
        self._inflight.clear()
        self._queue.clear()
        self._refs.clear()
        self._failed.clear()
        self._empty.clear()

    def forget(self, paths: Iterable[Path]) -> None:
        """这些路径上的文件变了：忘掉「读不出来 / 是空相册」的结论。

        那两个集合是**记忆**，为的是别对同一个坏文件反复重试；但文件被外部工具替换
        之后记忆就过期了 —— 不清掉，那一格会永远停在占位图上（ADR 0012）。
        """
        for path in paths:
            target = Path(path)
            self._failed.discard(target)
            self._empty.discard(target)

    def _dispatch(self) -> None:
        while len(self._inflight) < self._max_inflight:
            path = self._queue.pop_next()
            if path is None:
                return
            # 同一张照片不能重复投递：窗口会随滚动反复刷新，而已在解码的路径
            # 仍然会出现在新的可见窗口里。
            if path in self._inflight or path in self._failed or path in self._empty:
                continue
            ref = self._refs.get(path)
            if ref is None:
                continue
            self._start(ref)

    def _start(self, entry: GridEntry) -> None:
        library = self._library
        generation = self._generation
        # 在提交时就定格，而不是等任务跑完再读：切档瞬间在途的任务要带着它开始
        # 时的尺寸走完，交回来的边长才对得上它自己的像素。
        edge = self._target_edge

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            if isinstance(entry, PhotoRef):
                ref = entry
            else:
                # 相册：拿里面找到的第一张照片当封面；空相册返回 None。
                ref = library.folder_cover(entry.path, should_cancel=context.is_cancelled)
                if ref is None:
                    return None
            # 缩放在这里做（工作线程），界面线程只留一次 QPixmap.fromImage。
            return ScaledThumbnail(to_scaled_qimage(library.thumbnail(ref), edge), edge)

        job_id = self._runner.submit(
            work,
            on_result=partial(self._on_result, entry, generation),
            on_error=partial(self._on_error, entry, generation),
        )
        self._inflight[entry.path] = job_id

    def _on_result(self, entry: GridEntry, generation: int, result: object) -> None:
        self._inflight.pop(entry.path, None)
        if generation != self._generation:
            self._dispatch()
            return
        if result is None:
            # 空相册没有封面：记下来别再反复问，界面会画文件夹图标。
            self._empty.add(entry.path)
        elif isinstance(result, ScaledThumbnail):
            self.ready.emit(entry.path, result.image, result.edge)
        self._dispatch()

    def _on_error(self, entry: GridEntry, generation: int, message: str) -> None:
        self._inflight.pop(entry.path, None)
        if generation == self._generation:
            self._failed.add(entry.path)
            self.failed.emit(entry.path, message)
        self._dispatch()


def _thumbnail_target(entry: GridEntry) -> GridEntry | None:
    """这一格要显示谁的缩略图。

    分组卡片在这里就换成它组内第一张照片：封面不是现场去找的，于是它和普通照片
    走完全一样的解码与缓存路径（同一张照片当好几个组的封面时也只解码一次）。
    空分组返回 ``None`` —— 没有封面可画，界面会画文件夹图形。
    """
    if isinstance(entry, GroupRef):
        return entry.cover
    return entry


__all__ = ["NO_EDGE", "ScaledThumbnail", "ThumbnailPipeline"]
