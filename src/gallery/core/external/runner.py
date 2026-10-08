"""要跑别的程序时，统一从 :class:`Runner` 出去。

**这一层存在的理由只有一个：让测试碰不到本机。** PWA 装没装、Chrome 在不在跑、窗口叫什么
在测试机上都不可控，:class:`Runner` 让它们变成一个可以塞假实现的参数。
:func:`subprocess_runner` 是真实现；测试传自己的进去。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

#: 一次外部调用最多等这么久（秒）。**不是「程序该跑多久」，是「等不到就当它不行」** ——
#: 界面上的对策是降级到浏览器（见 ``launch.open_google_calendar``），用户等的是
#: 一个开得起来的日历，不是等一个开不起来的。
COMMAND_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class CommandResult:
    """跑一个外部程序的结果。**只留判据要用的那几样**，stderr 不进界面。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    """跑一条命令。

    **是协议而不是基类**：测试传一个普通函数进去就行，不必继承 —— 而那正是这里真正
    需要的形状（``tests/core/external/test_launch.py`` 里的 ``FakeRunner`` 就是个函数）。
    """

    def __call__(
        self, argv: Sequence[str], *, timeout: float | None = COMMAND_TIMEOUT_SECONDS
    ) -> CommandResult:
        """跑 ``argv`` 并等它结束。**不该抛异常** —— 跑不起来本身就是一种结果。"""
        ...


def subprocess_runner(
    argv: Sequence[str], *, timeout: float | None = COMMAND_TIMEOUT_SECONDS
) -> CommandResult:
    """真实现：起一个子进程，等它结束。

    **文件描述符全部丢给 /dev/null**：这些程序（``open``、``osascript``、``pgrep``）
    对 Gallery 没有意义的标准输出/标准错误，而 ``subprocess`` 默认把它们接到管道上，
    管道没人读就会在输出够多时把子进程挂住。
    """
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=-1, stderr="超时")
    except OSError as error:
        # 程序压根不在（没装 osascript、非 macOS）：这属于「用户可修复」，不是崩溃。
        return CommandResult(returncode=-2, stderr=str(error))
    return CommandResult(returncode=done.returncode, stdout=done.stdout, stderr=done.stderr)


#: 等一会儿。抽成参数是为了测试里不真的睡 —— 等东西要等条件，不要等秒数。
Sleeper = Callable[[float], None]
