"""Gallery 会递东西过去的那几个外部应用：本机装没装、图标在哪儿。

**按 bundle id 认，不按 ``.app`` 的名字认**：包叫「QSpace」还是「QSpace Pro」都认得出，
Launch Services 才是系统认账的那份名册（与 PWA 那条同一个理由，ADR 0034 决策 2）。**显示名
只进菜单与横幅** —— ``open -a`` 不认中文名（实测「预览」会被它拒掉），递给它的永远是名册给的
那个路径。这一层**不碰用户的照片**，只问一次名册、读一次应用包的 ``Info.plist``。
"""

from __future__ import annotations

import importlib
import plistlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

#: 图标文件名写在 ``Info.plist`` 的这一项里，展开目录是它旁边那个 ``Resources``
#: （与 PWA 读 ``CrAppModeShortcutURL`` 同一个道理：名字由应用自己说，不照着本机那一个拼）。
ICON_KEY = "CFBundleIconFile"

INFO_PLIST = Path("Contents") / "Info.plist"
ICONS_DIR = Path("Contents") / "Resources"


@dataclass(frozen=True)
class App:
    """下拉框里用得到的一项外部应用。
    :param id: 这一项的标识；点下去做什么由界面按它分派。
    :param name: 菜单与横幅里写的名字（:data:`VIEWERS` 里那两项是中文的）。
    :param bundle_ids: **按优先级排**的 bundle id，认第一个真的还在那儿的。
    """

    id: str
    name: str
    bundle_ids: tuple[str, ...]


#: QSpace 的 bundle id：同一个作者有两个发行版，Pro 在前（本机实测那个包就叫「QSpace Pro」）；
#: 都装了就认 Pro，只装了一个也认得出。
QSPACE = App(
    id="qspace",
    name="QSpace",
    bundle_ids=("com.jinghaoshe.qspace.pro", "com.jinghaoshe.qspace"),
)

#: 大图页那一列「用哪个软件看这一张」，按显示顺序。**这是一份手写的白名单** ——
#: 系统那份「打开方式」名单里还躺着 ColorSync Utility / Chrome / Safari / QuickTime，
#: 它们声明能开图，递一张照片过去得到的却是别的东西（浏览器标签、一帧画面）。
VIEWERS: tuple[App, ...] = (
    App(id="preview", name="预览", bundle_ids=("com.apple.Preview",)),
    App(id="imageglass", name="ImageGlass", bundle_ids=("com.duongdieuphap.imageglass",)),
)


@cache
def find_app(app: App) -> Path | None:
    """这个应用的应用包在哪儿；没装（或者不是 macOS）就是 ``None``。

    **同一个问题只问一次**：界面每次翻页都要问一遍「摆不摆这一项」，而名册在会话中间不会变
    —— 用户真去装了它，重开一次 Gallery 就认出来了。
    """
    appkit = _appkit()
    if appkit is None:
        return None
    return _lookup(appkit.NSWorkspace.sharedWorkspace(), app.bundle_ids)


def icon_path(app: App) -> Path | None:
    """这个应用的图标在哪儿；取不到就是 ``None``，界面那一层退回自画的轮廓（与访达同一套）。

    别人的商标**不随包分发**（ADR 0034 决策 6），所以一律读系统里那一份。
    """
    found = find_app(app)
    if found is None:
        return None
    return icon_in(found)


def icon_in(bundle: Path) -> Path | None:
    """一个应用包里那个图标文件；读不到就是 ``None``。

    读不出来不是错误：包可能正在被更新、``Info.plist`` 也可能损坏 —— 那只是少一个图标，
    不该把整个下拉框弄挂。
    """
    try:
        with (bundle / INFO_PLIST).open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    name = info.get(ICON_KEY)
    if not isinstance(name, str) or not name:
        return None
    # 这一项有的包写 ``QSpace Pro``、有的写 ``QSpace Pro.icns``，两种都认。
    icon = bundle / ICONS_DIR / name
    if icon.suffix != ".icns":
        icon = icon.with_suffix(".icns")
    return icon if icon.is_file() else None


def _lookup(workspace: Any, bundle_ids: Sequence[str]) -> Path | None:
    """按名册逐个问 bundle id，返回第一个**真的还在那儿**的应用包。

    名册里可能留着一条指向已删除应用的记录，所以拿到路径还要问一句它是不是目录
    —— 不然那一项会一直在下拉框里，点下去什么都不会发生。
    """
    for bundle_id in bundle_ids:
        url = workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
        if url is None:
            continue
        path = Path(url.path())
        if path.is_dir():
            return path
    return None


def _appkit() -> Any:
    """导入 AppKit。没装 pyobjc（非 macOS）时返回 ``None``，这一项就整个不出现。

    用 ``importlib`` 而不是 ``import``：pyobjc 没有类型桩，而 ``core/`` 是 ``mypy --strict``
    （与 ``recent_documents`` 同一个做法）。
    """
    try:
        return importlib.import_module("AppKit")
    except ImportError:
        return None


__all__ = [
    "ICONS_DIR",
    "ICON_KEY",
    "INFO_PLIST",
    "QSPACE",
    "VIEWERS",
    "App",
    "find_app",
    "icon_in",
    "icon_path",
]
