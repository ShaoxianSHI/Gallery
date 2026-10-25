"""Gallery 会递东西过去的那几个外部应用：本机装没装、图标长什么样。

**按 bundle id 认，不按 ``.app`` 的名字认**：包叫「QSpace」还是「QSpace Pro」都认得出，
Launch Services 才是系统认账的那份名册（与 PWA 那条同一个理由，ADR 0034 决策 2）。**显示名
只进菜单与横幅** —— ``open -a`` 不认中文名（实测「预览」会被它拒掉），递给它的永远是名册给的
那个路径。这一层**不碰用户的照片**，只问一次名册、让系统画一次图标。
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

#: 向系统要图标时给多大（**点**，不是像素 —— Retina 上拿回来的是两倍像素）。菜单里最大也就
#: 16 点；而系统手上那张原图是 1024，编码一次要一百多毫秒、快 2 MB，不能直接往上递。
ICON_EDGE = 64


@dataclass(frozen=True)
class App:
    """下拉框里用得到的一项外部应用。

    :param id: 这一项的标识；点下去做什么由界面按它分派。
    :param name: 菜单与横幅里写的名字（:data:`PREVIEW` 那一项是中文的）。
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

#: 系统自带的「地图」：**macOS 上唯一认坐标的地图应用**（见 ``external.maps``）。
#: 它注册了 ``maps`` URL scheme；别的 Mac 地图应用接不了带位置的链接。
MAPS = App(id="maps", name="地图", bundle_ids=("com.apple.Maps",))

#: 系统自带的「日历」：**没有日期 URL scheme**，只能靠 AppleScript 导航
#: （见 ``external.apple_calendar``）。
CALENDAR = App(id="calendar", name="日历", bundle_ids=("com.apple.iCal",))

#: 大图页那一列「用哪个软件看这一张」。名字可能是中文，而它**只进菜单与横幅**。
PREVIEW = App(id="preview", name="预览", bundle_ids=("com.apple.Preview",))
IMAGEGLASS = App(
    id="imageglass",
    name="ImageGlass",
    bundle_ids=("com.duongdieuphap.imageglass",),
)

#: Picview：同样是图片浏览器。bundle id 取自它自己的 ``Info.plist``，**大小写照抄**。
PICVIEW = App(id="picview", name="Picview", bundle_ids=("com.chitaner.Picview",))

#: IINA：视频播放器。只在「正在看一个视频」的大图页上摆，且固定在第一位（ADR 0093）。
#: bundle id 取自它自己的 Info.plist；显示名是拉丁字母，不译。
IINA = App(id="iina", name="IINA", bundle_ids=("com.colliderli.iina",))

#: 图片应用那一份白名单 —— **这是一份手写的名单，不是问 Launch Services 要来的**：系统那份
#: 「打开方式」名单里还躺着 ColorSync Utility / Chrome / Safari / QuickTime，它们声明能开图，
#: 递一张照片过去得到的却是别的东西（浏览器标签、一帧画面）。**次序不表示摆出来的次序**，
#: 下拉框里那几项的先后由 :meth:`MainWindow._external_entries` 写定（ADR 0067）。
VIEWERS: tuple[App, ...] = (PREVIEW, IMAGEGLASS, PICVIEW)


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


def icon_png(app: App, edge: int = ICON_EDGE) -> bytes | None:
    """这个应用的图标，**让系统画好的那一张**（PNG 字节）；取不到就是 ``None``。

    **别自己从包里那个 ``.icns`` 拆**：同一个应用包，macOS 在 Dock 里画的那一张与文件里存的
    那一份可以不一样 —— 老式图标把圆角外面那片填成不透明白色，系统画的时候会替它套上自己的
    遮罩，自己拆就把那圈白带出来了（本机实测 ImageGlass 的四角是纯白、不透明）。
    """
    appkit = _appkit()
    found = find_app(app)
    if appkit is None or found is None:
        return None
    try:
        image = appkit.NSWorkspace.sharedWorkspace().iconForFile_(str(found))
        canvas = appkit.NSImage.alloc().initWithSize_(appkit.NSMakeSize(edge, edge))
        canvas.lockFocus()
        image.drawInRect_(appkit.NSMakeRect(0, 0, edge, edge))
        canvas.unlockFocus()
        rep = appkit.NSBitmapImageRep.imageRepWithData_(canvas.TIFFRepresentation())
        data = rep.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
    except Exception:  # 图标是这个应用的一部分，不是它本身 —— 少一个图标不该把窗口弄挂
        return None
    return None if data is None else bytes(data)


def _lookup(workspace: Any, bundle_ids: Sequence[str]) -> Path | None:
    """按名册逐个问 bundle id，返回第一个**真的还在那儿**的应用包。

    名册里可能留着一条指向已删除应用的记录，所以拿到路径还要问一句它是不是目录
    —— 不然那一项会一直挂在下拉框里，点下去什么都不会发生。
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
    """导入 AppKit。没装 pyobjc（非 macOS）时返回 ``None``，这几项就整个不出现。

    用 ``importlib`` 而不是 ``import``：pyobjc 没有类型桩，而 ``core/`` 是 ``mypy --strict``
    （与 ``recent_documents`` 同一个做法）。
    """
    try:
        return importlib.import_module("AppKit")
    except ImportError:
        return None


__all__ = [
    "CALENDAR",
    "ICON_EDGE",
    "IINA",
    "IMAGEGLASS",
    "MAPS",
    "PICVIEW",
    "PREVIEW",
    "QSPACE",
    "VIEWERS",
    "App",
    "find_app",
    "icon_png",
]
