"""把 Gallery 交给别的程序去做的那几件事：开 Google 日历、把窗口交给别的应用看。

这一层**一个字节都不碰用户的照片**（红线 1）：它读的是应用包的 ``Info.plist``，
写的是「另一个程序下一步该开哪个窗口」这一句话。启动外部应用不在
:mod:`~gallery.core.metadata` 的职责里，于是单立一个包而不是塞进既有那几层。

分成这么几个模块，是按「哪一部分值得单独测」切的：

- :mod:`~gallery.core.external.gcal` —— 哪一层 + 哪一天 → 一个 URL（纯函数）
- :mod:`~gallery.core.external.pwa` —— 本机装没装那个 PWA（扫应用包，读 ``Info.plist``）
- :mod:`~gallery.core.external.applescript` —— Chrome 里那些窗口的枚举与改写
- :mod:`~gallery.core.external.launch` —— 走 PWA 还是走浏览器，以及失败时怎么降级
- :mod:`~gallery.core.external.runner` —— 要跑别的程序时统一从这里出去

**下拉框里那几项不在这里**：它们各自要用不同的手段（访达那一项走
:mod:`gallery.ui.desktop`，本来就是界面层的事），而「界面上摆哪几项、每一项点下去
做什么」属于 :mod:`gallery.ui.external_button`。

**每一处要跑别的程序的地方都收在 :class:`Runner` 后面**，测试因此不依赖本机装没装
Chrome、也不真的打开任何窗口（``AGENTS.md``：测试不得依赖本机已安装的外部程序）。
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
