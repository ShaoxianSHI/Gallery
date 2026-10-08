"""Chrome 里那些窗口：枚举出来、认出哪个是 PWA、改写它的地址。

PWA 窗口本就挂在 Chrome 主进程里，AppleScript 能改写其标签地址，于是启动它、等窗口开出来、
再改写那个窗口即可（ADR 0034 决策 1）。**这是 Chrome 未对外承诺的内部行为**，所以每个失败
路径都必须安静而明确：认不出窗口就退回浏览器，而不是开错一个。窗口认人的判据是「窗口名为空
+ 只有一页 + 是目标站点」——否则会把用户正在看的那一页直接跳走。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from gallery.core.external.runner import Runner

#: 浏览器主进程的命令行特征。**只认这一行**（helper 们不算）——
#: 我们等的是「浏览器起来了」，不是「有几个渲染进程在跑」。
CHROME_PROCESS_PATTERN = "Google Chrome.app/Contents/MacOS/Google Chrome"

#: 浏览器应用名（AppleScript 里用它寻址）。
CHROME_APP_NAME = "Google Chrome"

#: 枚举窗口的脚本。**每一件东西各占一行，前面一个字母做前缀** —— 页面标题里什么字符都可能出现，
#: 挑不出一个安全的分隔符，而「按行首字母认字段」出了错直接看得懂。**倒着枚举**，于是输出的
#: 头一个就是最新的窗口，:func:`find_pwa_window` 认到头一个就走。
_LIST_WINDOWS_SCRIPT = """
tell application "Google Chrome"
	set out to ""
	repeat with i from (count of windows) to 1 by -1
		set out to out & "W" & i & linefeed
		try
			set out to out & "N" & (name of window i) & linefeed
		on error
			set out to out & "N" & linefeed
		end try
		repeat with j from 1 to (count of tabs of window i)
			try
				set out to out & "T" & (URL of tab j of window i) & linefeed
			end try
		end repeat
	end repeat
	return out
end tell
"""


@dataclass(frozen=True)
class ChromeWindow:
    """Chrome 里的一个窗口：**序号**（改地址时要用）、名字、里面每一页的地址。"""

    index: int
    name: str
    urls: tuple[str, ...]


def chrome_is_up(runner: Runner) -> bool:
    """浏览器的进程在不在。

    **用 ``pgrep`` 而不是 AppleScript 去问**：AppleScript 会**自动启动目标应用**，于是
    「查一下在不在」变成了「把它启动一遍」，还会顺带开出一个「新标签页」窗口。``pgrep``
    什么也不会启动（ADR 0034 决策 4）。
    """
    return runner(["pgrep", "-f", CHROME_PROCESS_PATTERN]).returncode == 0


def _parse_listing(text: str) -> list[ChromeWindow]:
    """把枚举脚本的输出读成窗口列表。

    **输出被截断或一行都读不出来时就返回空列表**：那是「现在认不出窗口」，
    不是「有窗口但读错了」——两种情况在界面上都该走同一条降级。
    """
    windows: list[ChromeWindow] = []
    index: int | None = None
    name = ""
    urls: list[str] = []

    def flush() -> None:
        if index is not None:
            windows.append(ChromeWindow(index=index, name=name, urls=tuple(urls)))

    for line in text.splitlines():
        if line.startswith("W") and line[1:].strip().isdigit():
            flush()
            index = int(line[1:].strip())
            name = ""
            urls = []
        elif line.startswith("N"):
            name = line[1:]
        elif line.startswith("T"):
            urls.append(line[1:])
    flush()
    return windows


def list_windows(runner: Runner) -> list[ChromeWindow]:
    """列出 Chrome 里的窗口（最新的在前）。读不出来就是空列表。"""
    result = runner(["osascript", "-e", _LIST_WINDOWS_SCRIPT])
    if result.returncode != 0:
        return []
    return _parse_listing(result.stdout)


def is_pwa_window(window: ChromeWindow, host: str) -> bool:
    """这个窗口是不是那个 PWA（纯函数，不碰 subprocess）。

    三个条件缺一不可：**窗口名为空**（无标签栏的应用窗口才没有标题，否则会把开着日历标签的
    普通窗口误认，改地址就改到用户正在看的那一页）、**只有一页**、**是目标站点**（认站点，
    不是某一条具体地址）。
    """
    return (
        window.name.strip() == ""
        and len(window.urls) == 1
        and urlparse(window.urls[0]).hostname == host
    )


def find_pwa_window(windows: Sequence[ChromeWindow], host: str) -> int | None:
    """认出那个 PWA 窗口的序号，认不出就是 ``None``（**不猜、不挑一个差不多的**）。"""
    for window in windows:
        if is_pwa_window(window, host):
            return window.index
    return None


def set_window_url(index: int, url: str, runner: Runner) -> bool:
    """把第 ``index`` 个窗口里那一页的地址改掉。成功与否回一个布尔。

    **换地址而不是重新启动**：PWA 窗口起一次要几秒，而改地址是当场生效的
    （实测：窗口标题立刻从「2025 年」变成目标那一年）。
    """
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "{CHROME_APP_NAME}" '
        f'to set URL of tab 1 of window {int(index)} to "{escaped}"'
    )
    return runner(["osascript", "-e", script]).returncode == 0


__all__ = [
    "CHROME_APP_NAME",
    "CHROME_PROCESS_PATTERN",
    "ChromeWindow",
    "chrome_is_up",
    "find_pwa_window",
    "is_pwa_window",
    "list_windows",
    "set_window_url",
]
