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

from gallery.core.library.facade import Library
from gallery.core.library.photos import GridEntry, PhotoRef
from gallery.ui.imaging import to_qimage
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumbs.queue import ThumbnailQueue


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
        self._empty: set[Path] = set()
        self._generation = 0
        # 在途任务不超过线程数，保证线程池永远满载，同时让「可见」优先级真正生效。
        self._max_inflight = max(1, runner.thread_count)

    def set_window(self, visible: Sequence[GridEntry], prefetch: Sequence[GridEntry] = ()) -> None:
        """声明当前窗口：``visible`` 先出图，``prefetch`` 次之，其余请求全部丢弃。

        条目既可能是照片，也可能是相册；相册的缩略图取它内部的第一张照片当封面。
        """
        self._queue.set_window(
            [entry.path for entry in visible],
            prefetch=[entry.path for entry in prefetch],
        )
        for entry in (*visible, *prefetch):
            self._refs[entry.path] = entry
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
            return to_qimage(library.thumbnail(ref))

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
        else:
            self.ready.emit(entry.path, result)
        self._dispatch()

    def _on_error(self, entry: GridEntry, generation: int, message: str) -> None:
        self._inflight.pop(entry.path, None)
        if generation == self._generation:
            self._failed.add(entry.path)
            self.failed.emit(entry.path, message)
        self._dispatch()


__all__ = ["ThumbnailPipeline"]
