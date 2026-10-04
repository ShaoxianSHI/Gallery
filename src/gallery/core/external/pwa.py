"""本机装没装某个 Chrome PWA —— 按它自己的启动地址认，不按 ``.app`` 的名字认。

那个包在本机叫「**Google 日历**」：名字跟着 Chrome 的界面语言走（英文系统上是
``Google Calendar``）。拿名字去问 LaunchServices，在别人的机器上必然落空，于是
判据换成**它自己的启动地址**：读 ``Contents/Info.plist`` 里的
:data:`SHORTCUT_URL_KEY`，hostname 等于要找的站点即命中。与语言无关。

**为什么非得知道装没装**：已安装的 PWA 在 macOS 上是一个真正的应用，能用它的图标、
能占一个 Dock 位；没装的话就只能退回浏览器，图标也得换成画的那个（ADR 0034）。
"""

from __future__ import annotations

import plistlib
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

#: Chrome 把装好的 PWA 摆在哪儿（两处，缺一不可：本机的实测就是落在 ``~/Applications`` 下）。
APPS_DIR_NAME = "Chrome Apps.localized"

#: 那个应用包里，「启动时打开哪个地址」这一项。
SHORTCUT_URL_KEY = "CrAppModeShortcutURL"

#: 图标在包里的位置。有它就直接用——**那是应用自己的图标，永远是最新的**，
#: 而我们不往仓库里放第三方商标素材（ADR 0034 决策 6）。
ICON_RELATIVE = Path("Contents") / "Resources" / "app.icns"

#: 扫哪儿。顺序即优先级。
DEFAULT_SEARCH_DIRS: tuple[Path, ...] = (
    Path.home() / "Applications" / APPS_DIR_NAME,
    Path("/Applications") / APPS_DIR_NAME,
)


def _shortcut_host(app: Path) -> str | None:
    """读一个应用包的启动地址，取出它的 hostname。读不到就是 ``None``。

    **读不出来不是错误**：`.app` 可能正在被改写、可能压根不是 PWA（那个目录里还
    躺着别的东西）、plist 也可能损坏。跳过去继续扫下一个——「没找到」本来就该是
    一个安静的结果，用户不该看到一句警告。
    """
    plist_path = app / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    url = info.get(SHORTCUT_URL_KEY)
    if not isinstance(url, str):
        return None
    return urlparse(url).hostname


@cache
def _find(host: str, search_dirs: tuple[Path, ...]) -> Path | None:
    for base in search_dirs:
        if not base.is_dir():
            continue
        # 排序是为了**结果稳定**：目录里的顺序由文件系统决定，而「先找到哪个」
        # 不该随机器上的创建先后变化。
        for app in sorted(base.glob("*.app")):
            if _shortcut_host(app) == host:
                return app
    return None


def find_chrome_pwa(host: str, *, search_dirs: Sequence[Path] = DEFAULT_SEARCH_DIRS) -> Path | None:
    """本机装的那个 PWA 应用包在哪儿，没装就是 ``None``。

    **同一个 (站点, 目录) 只扫一次**：Chrome 更新会改那个目录，而一次会话里
    问一次就够——真要问第二次，早先那个答案多半也还没过期。
    """
    return _find(host, tuple(search_dirs))


def app_icon(app: Path) -> Path | None:
    """应用包自带图标在哪儿，没有就别画那个图标（而不是画一个错的）。"""
    icon = app / ICON_RELATIVE
    return icon if icon.is_file() else None


__all__ = [
    "APPS_DIR_NAME",
    "DEFAULT_SEARCH_DIRS",
    "ICON_RELATIVE",
    "SHORTCUT_URL_KEY",
    "app_icon",
    "find_chrome_pwa",
]
