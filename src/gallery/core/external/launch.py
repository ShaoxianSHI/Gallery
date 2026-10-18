"""打开哪一个、怎么打开、以及打不开时怎么办。

**PWA 路径分三步**：启动那个应用 → 等浏览器真的在跑 → 等窗口开出来 → 改写窗口地址。
**降级不是礼貌，是必需品**：改写窗口地址依赖 Chrome 未对外承诺的内部行为（ADR 0034
决策 1），任何一步落空都退到系统默认浏览器，并把**为什么降级**带回去，让横幅照实说
「已改用浏览器打开」，而不是让用户对着没反应的按钮猜（ADR 0024）。
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gallery.core.external.applescript import (
    chrome_is_up,
    find_pwa_window,
    list_windows,
    set_window_url,
)
from gallery.core.external.gcal import HOST, deep_link
from gallery.core.external.pwa import DEFAULT_SEARCH_DIRS, find_chrome_pwa
from gallery.core.external.runner import Runner, subprocess_runner

#: 开在应用里。
WHERE_APP = "app"
#: 开在浏览器里。
WHERE_BROWSER = "browser"

#: 「启动起来」最多等多久（秒）。浏览器冷启动实测两三秒，20 是留给机器正忙的余量。
CHROME_WAIT_SECONDS = 20.0
#: 「窗口开出来」最多等多久（秒）。窗口一出现就该被认出来，12 秒足够。
WINDOW_WAIT_SECONDS = 12.0
#: 两次查看之间隔多久。**只是一个不让循环空转的间隔**，不是「等这么久」——
#: 下面的等待全部都是「条件满足就走」（见 :func:`_wait_until`）。
POLL_SECONDS = 0.4


@dataclass(frozen=True)
class OpenOutcome:
    """打开之后的结果。横幅上那一句由它拼（``ui.banners``），不在这里。"""

    #: 开在 :data:`WHERE_APP` 还是 :data:`WHERE_BROWSER`。
    where: str
    #: 降级或出错的**中文说明**，用户可照着做。没有就是 ``None``。
    detail: str | None = None

    @property
    def in_app(self) -> bool:
        return self.where == WHERE_APP


def _wait_until(
    ready: Callable[[], bool],
    *,
    timeout: float,
    sleeper: Callable[[float], None],
    now: Callable[[], float],
) -> bool:
    """反复问 ``ready``，条件满足就走。

    **等的是条件，不是秒数**：每轮先问一次再决定还等不等，于是正常情况下一步就走完
    （实测整个 PWA 路径一点三秒），只有真的不行时才会等到 :data:`CHROME_WAIT_SECONDS`。
    ``sleeper`` 与 ``now`` 都是参数，于是测试里可以把时钟拨快、白等不用真睡。
    """
    deadline = now() + timeout
    while True:
        if ready():
            return True
        if now() >= deadline:
            return False
        sleeper(POLL_SECONDS)


def open_in_browser(url: str, *, runner: Runner | None = None) -> OpenOutcome:
    """交给系统默认浏览器。**降级走到这里，没装 PWA 时走到这里，影片网站也走这里。**"""
    run = runner or subprocess_runner
    result = run(["open", url])
    detail = None if result.returncode == 0 else "没能打开浏览器"
    return OpenOutcome(where=WHERE_BROWSER, detail=detail)


def open_in_app(
    app: Path,
    url: str,
    *,
    runner: Runner | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    chrome_wait: float = CHROME_WAIT_SECONDS,
    window_wait: float = WINDOW_WAIT_SECONDS,
) -> OpenOutcome:
    """启动那个应用，把它开在 ``url`` 上；开不到就降级到浏览器。

    顺序**不能换**：先 ``open`` 再等浏览器（``open`` 立刻返回，把它带起来要几秒）；
    等浏览器用 ``pgrep`` 而不是 AppleScript（后者会自动启动目标应用，把「查一下在不在」
    变成「把它启动一遍」，还会顺带开出一个「新标签页」窗口）。
    """
    run = runner or subprocess_runner
    launched = run(["open", "-a", str(app)])
    if launched.returncode != 0:
        return _fallback(url, "没能启动那个应用", run=run)

    up = _wait_until(lambda: chrome_is_up(run), timeout=chrome_wait, sleeper=sleeper, now=now)
    if not up:
        return _fallback(url, "等不到浏览器启动", run=run)

    # 探针只跑一次，最后那个序号从闭包里取 —— 每多枚举一轮就是多跑一次 AppleScript，
    # 而这一轮里用户正等着。
    found: list[int] = []

    def probe() -> bool:
        window = _peek_pwa_window(run)
        if window is None:
            return False
        found.append(window)
        return True

    if not _wait_until(probe, timeout=window_wait, sleeper=sleeper, now=now):
        return _fallback(url, "没认出那个应用的窗口", run=run)
    if not set_window_url(found[0], url, run):
        return _fallback(url, "没能改写那个窗口的地址", run=run)
    return OpenOutcome(where=WHERE_APP)


def _peek_pwa_window(runner: Runner) -> int | None:
    """枚举窗口并认出 PWA 的那个序号。"""
    return find_pwa_window(list_windows(runner), HOST)


def _fallback(url: str, reason: str, *, run: Runner) -> OpenOutcome:
    """降级：**真的去开浏览器**，并把「为什么降级」一起带回去。

    别只改一个 ``where=WHERE_BROWSER`` 就完事 —— 那样横幅会说「已改用浏览器打开」，
    而浏览器压根没被叫起来，用户对着一个什么都没发生的界面猜。
    """
    outcome = open_in_browser(url, runner=run)
    if outcome.detail:
        return OpenOutcome(where=WHERE_BROWSER, detail=f"{reason}，浏览器也没能打开")
    return OpenOutcome(where=WHERE_BROWSER, detail=f"{reason}，已改用浏览器打开")


def open_google_calendar(
    level: str,
    day: dt.date,
    *,
    account: int = 0,
    runner: Runner | None = None,
    search_dirs: Sequence[Path] = DEFAULT_SEARCH_DIRS,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> OpenOutcome:
    """把 Google 日历开到「哪一层 + 哪一天」。**装了 PWA 就开在应用里，没装就开在浏览器。**"""
    url = deep_link(level, day, account)
    run = runner or subprocess_runner
    if sys.platform != "darwin":
        # 别的平台上那个「应用」压根不存在（PWA 是 Chrome 在 macOS 上做出来的），
        # 与其去问一个注定没有的目录，不如直接走浏览器。
        return open_in_browser(url, runner=run)
    app = find_chrome_pwa(HOST, search_dirs=search_dirs)
    if app is None:
        return open_in_browser(url, runner=run)
    return open_in_app(app, url, runner=run, sleeper=sleeper, now=now)


__all__ = [
    "CHROME_WAIT_SECONDS",
    "POLL_SECONDS",
    "WHERE_APP",
    "WHERE_BROWSER",
    "WINDOW_WAIT_SECONDS",
    "OpenOutcome",
    "open_google_calendar",
    "open_in_app",
    "open_in_browser",
]
