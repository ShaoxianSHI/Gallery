"""``TaskRunner.abandon()`` 丢掉排队积压时，那三份账也要跟着摘。

``_callbacks`` / ``_tokens`` / ``_signals`` 三份都只在成功 / 失败回调被处理时
``pop``；而 ``abandon()`` 里的 ``QThreadPool.clear()`` 把排队中的那些 ``QRunnable``
**直接删掉** —— 它们一个字节都不会跑，也就永远不会回报一次。每一批被丢掉的排队积压
于是留下三份条目（回调闭包连着它捕获的一切、取消令牌、以及那个信号对象），
关一次窗漏一批。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.tasks import TaskRunner  # noqa: E402


@pytest.fixture
def runner(qt_app: QApplication) -> TaskRunner:  # noqa: ARG001 - 造它就需要那个 App
    """**一个线程**：占住它的那个任务于是把后面所有提交都钉在队列里。"""
    return TaskRunner(max_threads=1)


def _blocking_job(started: threading.Event, gate: threading.Event):  # type: ignore[no-untyped-def]
    def work(context: object) -> str:  # type: ignore[no-untyped-def]
        started.set()
        gate.wait(10.0)
        return "跑完了"

    return work


class _Closing:
    """在另一条线程上跑 ``abandon`` —— 它要等那个还占着线程的活儿收工。

    放在主线程上等的话，用例得先放闸门再等它，而那一段等待没法拿条件等；
    而这个场景本身是确定性的：**闸门不开，那一个线程就永远不会空出来**。
    """

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner
        self.failures: list[BaseException] = []
        self._thread = threading.Thread(target=self._close)

    def _close(self) -> None:
        try:
            self._runner.abandon()
        except BaseException as exc:  # pragma: no cover - 出错要看得见
            self.failures.append(exc)

    def start(self) -> None:
        self._thread.start()

    def finish(self, gate: threading.Event) -> None:
        gate.set()
        self._thread.join(10.0)
        assert not self._thread.is_alive(), "abandon 没有收工"
        assert not self.failures, self.failures


def test_the_jobs_which_never_started_leave_nothing_behind(
    runner: TaskRunner,
    wait_for: Callable[..., bool],
) -> None:
    """被丢掉的那一批不会回报，于是三份账得在 ``abandon`` 里摘掉。"""
    gate = threading.Event()
    started = threading.Event()
    runner.submit(_blocking_job(started, gate))
    assert wait_for(lambda: started.is_set())

    queued = [runner.submit(lambda context: None) for _ in range(20)]
    assert len(runner._callbacks) == 21

    closing = _Closing(runner)
    closing.start()
    assert wait_for(lambda: all(job not in runner._callbacks for job in queued)), (
        "被丢掉的那一批没有摘干净"
    )
    assert set(runner._tokens) == set(runner._callbacks)
    assert set(runner._signals) == set(runner._callbacks)
    assert runner._queued == set()
    closing.finish(gate)


def test_a_job_which_is_still_running_keeps_its_bookkeeping(
    runner: TaskRunner,
    wait_for: Callable[..., bool],
) -> None:
    """**已经在跑的那一条不许摘** —— 它的信号还会在后面回来。

    摘掉之后那一格的结果回调就没人接了。场景很普通：关窗时最后一张照片正在解码，
    ``clear()`` 丢不掉它（它只清还没开始的），它跑完之后照样要发一次 ``succeeded``。
    """
    gate = threading.Event()
    started = threading.Event()
    running = runner.submit(_blocking_job(started, gate))
    assert wait_for(lambda: started.is_set())

    closing = _Closing(runner)
    closing.start()
    assert wait_for(lambda: set(runner._callbacks) == {running}), (
        "关窗时只有那一个正在跑的任务，它的账不该被摘掉"
    )
    closing.finish(gate)

    assert runner.wait_for_done(2000)
