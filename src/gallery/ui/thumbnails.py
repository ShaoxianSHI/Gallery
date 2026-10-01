"""缩略图流水线：把「当前窗口需要的图」变成主线程上可用的 ``QImage``。

界面线程只调用 :meth:`ThumbnailPipeline.set_window`，剩下的解码、缩放、编码落盘
全在线程池里。结果用**代次号**过滤：切库之后，旧库的任务即使跑完也不会画到新界面上。

解码失败的照片会被记住并跳过，避免每次滚动都对同一个坏文件反复重试。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from gallery.core.library import Library
from gallery.core.photos import PhotoRef
from gallery.ui.imaging import to_qimage
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumb_queue import ThumbnailQueue


class ThumbnailPipeline(QObject):
    """按窗口优先级把缩略图喂给界面。"""

    #: 一张缩略图准备好了：``(路径, QImage)``。
    ready = Signal(object, object)
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
        self._generation = 0
        # 在途任务不超过线程数，保证线程池永远满载，同时让「可见」优先级真正生效。
        self._max_inflight = max(1, runner.thread_count)

    def set_window(self, visible: Sequence[PhotoRef], prefetch: Sequence[PhotoRef] = ()) -> None:
        """声明当前窗口：``visible`` 先出图，``prefetch`` 次之，其余请求全部丢弃。"""
        self._queue.set_window(
            [ref.path for ref in visible],
            prefetch=[ref.path for ref in prefetch],
        )
        for ref in (*visible, *prefetch):
            self._refs[ref.path] = ref
        self._dispatch()

    def shutdown(self) -> None:
        """切库或关窗：作废所有在途结果，并请求取消它们的任务。"""
        self._generation += 1
        for job_id in self._inflight.values():
            self._runner.cancel(job_id)
        self._inflight.clear()
        self._queue.clear()
        self._refs.clear()

    def _dispatch(self) -> None:
        while len(self._inflight) < self._max_inflight:
            path = self._queue.pop_next()
            if path is None:
                return
            # 同一张照片不能重复投递：窗口会随滚动反复刷新，而已在解码的路径
            # 仍然会出现在新的可见窗口里。
            if path in self._inflight or path in self._failed:
                continue
            ref = self._refs.get(path)
            if ref is None:
                continue
            self._start(ref)

    def _start(self, ref: PhotoRef) -> None:
        library = self._library
        generation = self._generation

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return to_qimage(library.thumbnail(ref))

        job_id = self._runner.submit(
            work,
            on_result=partial(self._on_result, ref, generation),
            on_error=partial(self._on_error, ref, generation),
        )
        self._inflight[ref.path] = job_id

    def _on_result(self, ref: PhotoRef, generation: int, result: object) -> None:
        self._inflight.pop(ref.path, None)
        if generation == self._generation and result is not None:
            self.ready.emit(ref.path, result)
        self._dispatch()

    def _on_error(self, ref: PhotoRef, generation: int, message: str) -> None:
        self._inflight.pop(ref.path, None)
        if generation == self._generation:
            self._failed.add(ref.path)
            self.failed.emit(ref.path, message)
        self._dispatch()


__all__ = ["ThumbnailPipeline"]
