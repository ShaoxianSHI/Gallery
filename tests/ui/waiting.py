"""界面测试里「等到某个后台事情做完」—— **只有这一份**。

扫描、分组排序的元数据读取、缩略图解码、一次粘贴，全都在线程池里跑。测试要断言
的是**结果**，于是就得先把事件循环转到那个结果出现为止。

这份代码曾经散成 8 份（``wait_until`` / ``_wait`` 各写一遍轮询循环，``WAIT_SECONDS``
定了两个不同的值），外加 conftest 里一份 fixture 形态的 ``wait_for``。问题不在慢，
在于**同一件事有 8 份写法**：改一处不会改到其余七处。

两种形态各自对应一件事：

- :func:`wait_until` —— 答一个是非问题（「到了吗」），不成立就返回 ``False``，
  由用例自己决定怎么报。
- :func:`wait_for_level` —— 专等「这一层排好了」，不成立**直接抛**并把当前行数
  写进消息（那一层还没排好时看到的现象就是「模型里一个格子都没有」）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtWidgets import QApplication

#: 轮询间隔（秒）。够密才抓得住一闪而过的东西，又不至于把 CPU 跑满。
#:
#: 曾经各模块用 ``0.02``（比这个粗四倍）。粗的那一套每轮要多等 15ms，一轮默认超时
#: 8 秒就是 400 轮，多花 6 秒 —— 也就是一轮测试里白白多出的三分之一。
POLL_SECONDS = 0.005

#: 默认超时（秒）。够跑完一次完整扫描与排序。
DEFAULT_TIMEOUT_SECONDS = 8.0


def wait_until(
    qt_app: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """转到 ``predicate()`` 成立为止，返回它是否真的成立过。

    成立就**立刻**返回（不等满一轮），所以「正常情况下」的耗时由事情本身决定，
    与 ``timeout`` 无关 —— ``timeout`` 只是挂住时的保险。
    """
    deadline = time.monotonic() + timeout
    while True:
        qt_app.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def wait_for_level(
    window: object,
    qt_app: QApplication,
    ready: Callable[[], bool],
    *,
    what: str = "这一层",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """等「这一层排好了」（扫描与排序都在后台线程上）。不成立就**抛**，并报出当前行数。

    「行数 0」是最容易看懂的那个失败现象：``_scan_job is None`` 还没成立，模型里
    一个格子都没有，用例于是拿到 ``-1`` 然后一路错下去。把它写进消息里，比让人
    回头去猜省事得多。
    """
    rows = getattr(getattr(window, "_model", None), "rowCount", lambda: "?")()
    if not wait_until(qt_app, ready, timeout=timeout):
        raise AssertionError(f"{what}没有在时限内就绪（当前 {rows()} 行）")


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "POLL_SECONDS", "wait_for_level", "wait_until"]
