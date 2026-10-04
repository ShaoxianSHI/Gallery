"""界面测试里「等到某个后台事情做完」—— **只有这一份**。

扫描、分组排序的元数据读取、缩略图解码、一次粘贴，全都在线程池里跑。测试要断言
的是**结果**，于是就得先把事件循环转到那个结果出现为止。

这份代码曾经散成 8 份（``wait_until`` / ``_wait`` 各写一遍轮询循环，``WAIT_SECONDS``
定了两个不同的值），外加 conftest 里一份 fixture 形态的 ``wait_for``。问题不在慢，
在于**同一件事有 8 份写法**：改一处不会改到其余七处。

三种形态各自对应一件事：

- :func:`wait_until` —— 答一个是非问题（「到了吗」），不成立就返回 ``False``，
  由用例自己决定怎么报。
- :func:`wait_for_level` —— 专等「这一层排好了」，不成立**直接抛**并把当前行数
  写进消息（那一层还没排好时看到的现象就是「模型里一个格子都没有」）。
- :func:`wait_for_idle` —— 专等「MainWindow 背后那几个后台任务收工」，不成立
  **直接抛**。
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

#: 「MainWindow 背后那一遍收工了」要看的后台任务。
#:
#: **只有一份的理由**：这三条曾散在五个测试模块里各写一遍轮询循环（合计 114 处调用），
#: 而它们全都**超时静默返回** —— 扫描卡住与扫描成功在返回值上完全一样，于是失败被
#: 推迟到后面某条莫名其妙的断言上。任务名以后要改（比如某一遍换了实现），改一处就够。
#: 「后台还没收工」要看的那些任务。
#:
#: **日历读拍摄时间那次（``_calendar_job``）也在里面**：日历在浏览方式切过去之后才
#: 起这个任务，而它挂着的时候横幅上写着进度 —— 一个仍显示「正在读取」的窗口不叫空闲。
#: 漏掉它的后果很具体：用例以为日历已经有数据了，于是断言「这一段有几张」拿到 0，
#: 报出来的是一条与真正原因（任务还没回来）毫不相干的失败。
BACKGROUND_JOBS = ("_scan_job", "_facet_job", "_place_job", "_calendar_job")


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
    rows = getattr(getattr(window, "_model", None), "rowCount", lambda: "?")
    if not wait_until(qt_app, ready, timeout=timeout):
        raise AssertionError(f"{what}没有在时限内就绪（当前 {rows()} 行）")


def window_is_idle(window: object, *, also: tuple[str, ...] = ()) -> bool:
    """``window`` 背后那几个后台任务是不是都收工了（``None`` 即收工）。

    ``also`` 放这一条**额外**也要等的任务名，同样按「是 ``None`` 算收工」判 ——
    检索索引（``_index_job``）与尚未提交的暂存选择（``_staged``）都落在这条路上。
    """
    return all(getattr(window, name, None) is None for name in (*BACKGROUND_JOBS, *also))


def wait_for_idle(
    qt_app: QApplication,
    window: object,
    *,
    also: tuple[str, ...] = (),
    what: str = "后台那一遍",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """等 :func:`window_is_idle` 成立。**没等到就直接抛**。

    不抛是这里最要紧的一件事：这些调用点原本**超时也返回**，于是「扫描卡死」与
    「扫描完成」在返回值上是一回事 —— 卡住时用例会带着一个空模型继续往下走，最后
    报出来的是某条不相干的断言，超时上限设了多少秒根本看不出来。
    """
    if not wait_until(qt_app, lambda: window_is_idle(window, also=also), timeout=timeout):
        pending = [name for name in (*BACKGROUND_JOBS, *also) if getattr(window, name, None)]
        raise AssertionError(f"{what}没有在 {timeout:g} 秒内收工（还挂着：{pending}）")
    # 再转一圈再交差：扫描是流式的，判定成立的那一刻可能还有最后一批在路上。
    qt_app.processEvents()


__all__ = [
    "BACKGROUND_JOBS",
    "DEFAULT_TIMEOUT_SECONDS",
    "POLL_SECONDS",
    "wait_for_idle",
    "wait_for_level",
    "wait_until",
    "window_is_idle",
]
