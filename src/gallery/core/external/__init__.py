"""把 Gallery 交给别的程序去做的那几件事：开 Google 日历、把窗口交给别的应用看。

这一层**一个字节都不碰用户的照片**（绝不删除、移动用户照片），只读应用包的 ``Info.plist``、
写「另一个程序下一步该开哪个窗口」这一句话。子模块：``gcal``（深链）、``pwa``（装没装）、
``applescript``（枚举与改写窗口）、``launch``（走哪条路、失败怎么降级）、``runner``（统一出口）。
**每一处要跑别的程序的地方都收在 :class:`Runner` 后面**，测试因此不依赖本机环境。
"""

from gallery.core.external.applescript import (
    ChromeWindow,
    chrome_is_up,
    find_pwa_window,
    is_pwa_window,
    list_windows,
    set_window_url,
)
from gallery.core.external.gcal import HOST, deep_link
from gallery.core.external.launch import (
    WHERE_APP,
    WHERE_BROWSER,
    OpenOutcome,
    open_google_calendar,
    open_in_app,
    open_in_browser,
)
from gallery.core.external.pwa import app_icon, find_chrome_pwa, finder_icon
from gallery.core.external.runner import CommandResult, Runner, subprocess_runner

__all__ = [
    "HOST",
    "WHERE_APP",
    "WHERE_BROWSER",
    "ChromeWindow",
    "CommandResult",
    "OpenOutcome",
    "Runner",
    "app_icon",
    "chrome_is_up",
    "deep_link",
    "find_chrome_pwa",
    "find_pwa_window",
    "finder_icon",
    "is_pwa_window",
    "list_windows",
    "open_google_calendar",
    "open_in_app",
    "open_in_browser",
    "set_window_url",
    "subprocess_runner",
]
