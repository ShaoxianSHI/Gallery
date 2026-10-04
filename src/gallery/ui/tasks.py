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
from threading import Event, Lock
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, SignalInstance, Slot

from gallery.core.errors import GalleryError

logger = logging.getLogger(__name__)

#: 关窗时等待后台任务收尾的最长时间：超过就放弃等待，避免用户觉得关不掉。
SHUTDOWN_TIMEOUT_MS = 2000

#: 线程数的**绝对**封顶。真正的规则是「给主线程留一个核」，见
#: :func:`default_thread_count`；这一条只用来挡住核心数报得离谱的机器。
#:
#: 它原先是 4，那是在没有实测数据时定的，如今看来定得太早：Pillow 解码会释放
#: GIL（是真并行），实测冷解码吞吐 1 线程 142 张/秒、4 线程 384、8 线程 493 ——
#: 4→8 白白闲置了一半核心。16 是「再多也就这样了」的实测拐点。
MAX_THREADS = 16


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
        on_start: Callable[[int], None],
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._fn = fn
        self._token = token
        self._signals = signals
        #: 告诉 :class:`TaskRunner`「我已经开跑了」，于是它不再把这一条算作「还在排队」
        #: （见 :meth:`TaskRunner._forget_never_started`）。
        self._on_start = on_start
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 在工作线程中执行
        self._on_start(self._job_id)
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
        #: 已投递、**还没开跑**的任务号。三个 dict 的条目只在成功 / 失败回调被处理时
        #: 才摘掉，而 ``QThreadPool.clear()`` 丢掉的那一批**永远不会回报** —— 靠这一份
        #: 才摘得掉它们（见 :meth:`_forget_never_started`）。
        self._lock = Lock()
        self._queued: set[int] = set()

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
        with self._lock:
            self._queued.add(job_id)
        self._pool.start(_Job(job_id, fn, token, signals, self._mark_running))
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

    def _mark_running(self, job_id: int) -> None:  # pragma: no cover - 在工作线程中执行
        with self._lock:
            self._queued.discard(job_id)

    def _forget_never_started(self) -> None:
        """把「投递了却一次都没开跑」的那些任务的账摘干净。

        ``_callbacks`` / ``_tokens`` / ``_signals`` 三份都只在
        :meth:`_handle_success` / :meth:`_handle_failure` 里被 ``pop`` ——
        而 :meth:`abandon` 里的 ``QThreadPool.clear()`` 把排队中的那些
        ``QRunnable`` **直接删掉**：它们一个字节都不会跑，也就永远不会回报一次。
        每一批被丢掉的排队积压于是留下三份条目（回调闭包连着它捕获的一切、
        取消令牌、以及那个信号对象），关一次窗漏一批。放进几百张照片再关窗就是几百份。

        **只摘「确定没开跑」的**：``_Job.run()`` 的第一件事就是把自己从 ``_queued``
        里摘掉，所以还留在那里的就是线程池队列里的那些。残留的窗口只有一处 ——
        Qt 内部从队列里摘走 ``QRunnable`` 到调用 ``run()`` 之间的那几条指令；
        真撞上了，后果也只是那一格的结果回调不送（而这正是在关窗），
        **绝不会**变成释放后使用：那个信号对象还被正在跑的 ``_Job`` 攥着。
        """
        with self._lock:
            dropped = tuple(self._queued)
            self._queued.clear()
        for job_id in dropped:
            self._callbacks.pop(job_id, None)
            self._tokens.pop(job_id, None)
            self._signals.pop(job_id, None)

    def abandon(self, timeout_ms: int = SHUTDOWN_TIMEOUT_MS) -> bool:
        """关窗要走的一整套：作废 → **丢掉排队积压** → 才等。

        ``cancel_all`` 只动令牌，**排队中还没开始的任务照样会一个个跑起来** ——
        每一个都可能是一次几秒的解码，或一次要起子进程的元数据读取。于是关窗要多等
        ``排队的个数 × 每个的时长``：一次丢进来几百张照片时，那已经是好几分钟。

        ``QThreadPool.clear()`` 把它们直接丢掉。它只清**还没开始的**；已经在跑的仍然
        要靠令牌让它自己停下来 —— 解码那几条已经接上令牌了
        （见 ``core.media.decode._within_deadline``）。

        :return: 是否在时限内全部收工（为 ``False`` 时调用方该记一条日志）。
        """
        self.cancel_all()
        self._pool.clear()
        # 被丢掉的那一批不会回报，于是三份账得在这里摘（见 :meth:`_forget_never_started`）。
        self._forget_never_started()
        return self.wait_for_done(timeout_ms)

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
