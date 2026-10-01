"""后台任务：把 ``core`` 的纯函数放到线程池里跑，再把结果安全地送回主线程。

三条规则：

1. **主线程只做两件事**：投递任务、处理结果。扫描、解码、编码缩略图全部在工作线程。
2. 信号从工作线程发出，槽函数在 ``TaskRunner`` 所在的线程（主线程）执行 ——
   这里显式使用 ``QueuedConnection``，绝不从工作线程碰控件。
3. 每个任务都有取消令牌。切库或关窗时一次性取消，避免旧结果盖到新界面上。

``core`` 完全不知道线程的存在：它只看到一个 ``should_cancel`` 回调。
"""

from __future__ import annotations

import itertools
import logging
import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, SignalInstance, Slot

from gallery.core.errors import GalleryError

logger = logging.getLogger(__name__)

#: 关窗时等待后台任务收尾的最长时间：超过就放弃等待，避免用户觉得关不掉。
SHUTDOWN_TIMEOUT_MS = 2000

#: 线程数上限。解码是 CPU 密集的，超过物理核心数只会互相抢。
MAX_THREADS = 4


def default_thread_count() -> int:
    """默认工作线程数：给主线程留一个核。"""
    return max(1, min(MAX_THREADS, (os.cpu_count() or 2) - 1))


class CancellationToken:
    """跨线程的取消标志。

    ``core`` 只看到 ``is_cancelled`` 这个回调，所以它不需要认识线程或事件循环。
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(slots=True)
class TaskContext:
    """交给任务函数的东西：一个取消检查，一个回报进度的出口。"""

    token: CancellationToken
    emit: Callable[[object], None]

    def is_cancelled(self) -> bool:
        return self.token.is_cancelled

    def progress(self, payload: object) -> None:
        """从工作线程回报一批中间结果（会在主线程被转交）。"""
        self.emit(payload)


@dataclass(slots=True)
class _Callbacks:
    on_result: Callable[[object], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_progress: Callable[[object], None] | None = None


class _JobSignals(QObject):
    """``QRunnable`` 不是 ``QObject``，信号要挂在一个辅助对象上。"""

    succeeded = Signal(int, object)
    failed = Signal(int, str, str)
    progressed = Signal(int, object)


class _Job(QRunnable):
    def __init__(
        self,
        job_id: int,
        fn: Callable[[TaskContext], Any],
        token: CancellationToken,
        signals: _JobSignals,
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._fn = fn
        self._token = token
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 在工作线程中执行
        context = TaskContext(
            token=self._token,
            emit=lambda payload: self._emit(self._signals.progressed, self._job_id, payload),
        )
        try:
            result = self._fn(context)
        except GalleryError as exc:
            # 领域错误是「给用户看的」，直接把中文消息带上去。
            self._emit(self._signals.failed, self._job_id, exc.message, exc.detail or "")
        except Exception:
            logger.exception("后台任务 %s 抛出未预期异常", self._job_id)
            self._emit(
                self._signals.failed,
                self._job_id,
                "出现了一个未预期的问题，详情已写入日志。",
                traceback.format_exc(),
            )
        else:
            self._emit(self._signals.succeeded, self._job_id, result)

    @staticmethod
    def _emit(signal: SignalInstance, *args: object) -> None:
        """发信号，并容忍对象已经销毁的情况。

        正常关窗会先取消任务并等待收尾（见 ``MainWindow.closeEvent``），
        但进程异常退出时窗口可能比工作线程先被回收，此时发信号会抛 RuntimeError。
        这种情况没有任何补救价值，安静地记一条日志即可，不要污染 stderr。
        """
        try:
            signal.emit(*args)
        except RuntimeError:
            logger.debug("后台任务的结果已无处送达（接收方已销毁）")


class TaskRunner(QObject):
    """界面层唯一的后台执行入口。"""

    def __init__(self, parent: QObject | None = None, *, max_threads: int | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads or default_thread_count())
        self._ids = itertools.count(1)
        self._callbacks: dict[int, _Callbacks] = {}
        self._tokens: dict[int, CancellationToken] = {}
        self._signals: dict[int, _JobSignals] = {}

    @property
    def thread_count(self) -> int:
        return self._pool.maxThreadCount()

    def submit(
        self,
        fn: Callable[[TaskContext], Any],
        *,
        on_result: Callable[[object], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> int:
        """投递一个任务，返回任务号（可用于取消）。

        ``fn`` 会收到一个 :class:`TaskContext`；它的返回值交给 ``on_result``，
        抛出的 ``GalleryError`` 的中文消息交给 ``on_error``。
        三个回调都在主线程执行。
        """
        job_id = next(self._ids)
        token = CancellationToken()
        signals = _JobSignals()
        # 显式排队连接：信号来自工作线程，槽必须在主线程执行。
        signals.succeeded.connect(self._handle_success, Qt.ConnectionType.QueuedConnection)
        signals.failed.connect(self._handle_failure, Qt.ConnectionType.QueuedConnection)
        signals.progressed.connect(self._handle_progress, Qt.ConnectionType.QueuedConnection)
        self._callbacks[job_id] = _Callbacks(
            on_result=on_result, on_error=on_error, on_progress=on_progress
        )
        self._tokens[job_id] = token
        # 信号对象必须活到任务结束（它承载着从工作线程发回的事件），
        # 因此由 TaskRunner 持有，直到任务的成功/失败回调被处理。
        self._signals[job_id] = signals
        self._pool.start(_Job(job_id, fn, token, signals))
        return job_id

    def cancel(self, job_id: int) -> None:
        """请求取消；已经在跑的任务会在下一次检查点时停下。"""
        token = self._tokens.get(job_id)
        if token is not None:
            token.cancel()

    def cancel_all(self) -> None:
        for token in self._tokens.values():
            token.cancel()

    def wait_for_done(self, timeout_ms: int = SHUTDOWN_TIMEOUT_MS) -> bool:
        """等待所有任务结束（关窗时调用，避免工作线程访问已销毁的对象）。"""
        return self._pool.waitForDone(timeout_ms)

    @Slot(int, object)
    def _handle_success(self, job_id: int, result: object) -> None:
        callbacks = self._callbacks.pop(job_id, None)
        self._tokens.pop(job_id, None)
        self._signals.pop(job_id, None)
        if callbacks is not None and callbacks.on_result is not None:
            callbacks.on_result(result)

    @Slot(int, str, str)
    def _handle_failure(self, job_id: int, message: str, detail: str) -> None:
        callbacks = self._callbacks.pop(job_id, None)
        self._tokens.pop(job_id, None)
        self._signals.pop(job_id, None)
        if detail:
            logger.debug("后台任务 %s 失败详情：%s", job_id, detail)
        if callbacks is not None and callbacks.on_error is not None:
            callbacks.on_error(message)

    @Slot(int, object)
    def _handle_progress(self, job_id: int, payload: object) -> None:
        callbacks = self._callbacks.get(job_id)
        if callbacks is not None and callbacks.on_progress is not None:
            callbacks.on_progress(payload)


__all__ = [
    "MAX_THREADS",
    "SHUTDOWN_TIMEOUT_MS",
    "CancellationToken",
    "TaskContext",
    "TaskRunner",
    "default_thread_count",
]
