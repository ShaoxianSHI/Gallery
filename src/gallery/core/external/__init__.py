"""把 Gallery 交给别的程序去做的那几件事：开 Google 日历、把窗口交给别的应用看。

这一层**一个字节都不碰用户的照片**（绝不删除、移动用户照片），只读应用包的 ``Info.plist``、
写「另一个程序下一步该开哪个窗口」这一句话。**每一处要跑别的程序的地方都收在
:class:`Runner` 后面**，测试因此不依赖本机环境；子模块 ``apps`` / ``gcal`` / ``pwa`` /
``applescript`` / ``launch`` / ``runner`` 各自的职责见各自那一份模块文档。
"""

from gallery.core.external.applescript import (
    ChromeWindow,
    chrome_is_up,
    find_pwa_window,
    is_pwa_window,
    list_windows,
    set_window_url,
)
from gallery.core.external.apps import (
    IMAGEGLASS,
    MAPS,
    PREVIEW,
    QSPACE,
    VIEWERS,
    App,
    find_app,
    icon_png,
)
from gallery.core.external.gcal import HOST, deep_link
from gallery.core.external.launch import (
    WHERE_APP,
    WHERE_BROWSER,
    OpenOutcome,
    open_google_calendar,
    open_in_app,
    open_in_browser,
    open_in_google_maps,
    open_in_maps,
)
from gallery.core.external.maps import apple_deep_link, google_web_link
from gallery.core.external.pwa import app_icon, find_chrome_pwa, finder_icon
from gallery.core.external.runner import CommandResult, Runner, subprocess_runner

__all__ = [
    "HOST",
    "IMAGEGLASS",
    "MAPS",
    "PREVIEW",
    "QSPACE",
    "VIEWERS",
    "WHERE_APP",
    "WHERE_BROWSER",
    "App",
    "ChromeWindow",
    "CommandResult",
    "OpenOutcome",
    "Runner",
    "app_icon",
    "apple_deep_link",
    "chrome_is_up",
    "deep_link",
    "find_app",
    "find_chrome_pwa",
    "find_pwa_window",
    "finder_icon",
    "google_web_link",
    "icon_png",
    "is_pwa_window",
    "list_windows",
    "open_google_calendar",
    "open_in_app",
    "open_in_browser",
    "open_in_google_maps",
    "open_in_maps",
    "set_window_url",
    "subprocess_runner",
]
