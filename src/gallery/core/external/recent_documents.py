"""把最近打开的库登记进系统那份「最近项目」—— Dock 右键菜单里那一块就是它（ADR 0062）。

Dock 是**另一个进程**，按 Launch Services 记着的那份列表自己画菜单 —— 也正因为如此，
**应用没运行时它照样画得出来**。pyobjc 只在这里用：Qt 没有任何 API 碰得到
``NSDocumentController``。
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from gallery.core.constants import BUNDLE_IDENTIFIER

logger = logging.getLogger(__name__)

__all__ = ["publish"]


class SystemRecents(Protocol):
    """系统那份列表的读写口。真实现只有 pyobjc 那两句话，测试里换成会记账的假实现。"""

    def clear(self) -> None:
        """清空这份列表。"""

    def note(self, root: Path) -> None:
        """把一个库登记到列表末尾。"""


def publish(roots: Sequence[Path]) -> None:
    """把 ``roots``（新的在前）整份交给系统，替换掉它现在记着的那些。

    **先清空再重建**：系统那份只增不减，被删掉的目录会一直挂着（点下去才报错），只有整份
    重来才能与我们这份一致。登记不上去也不算错 —— 它只该让 Dock 里少几项。
    """
    try:
        recents = _system_recents()
        if recents is None:
            return
        recents.clear()
        for root in _register_order(roots):
            recents.note(root)
    except Exception:
        # 少几项最近项目，不该挡着打开库。
        logger.warning("把最近打开的库登记进系统列表失败", exc_info=True)


def _register_order(roots: Sequence[Path]) -> list[Path]:
    """登记的次序：**最旧的先登记**，磁盘上已经没有的不要。

    Dock 菜单从图标往上弹，视线落在最下面那一项；而系统的条数上限是从尾部截断的。最新的
    必须最后登记，才既落在菜单底部、又不会被截掉。
    """
    return [root for root in roots if root.is_dir()][::-1]


def _system_recents() -> SystemRecents | None:
    """拿到系统那份列表；**只有真正的 Gallery.app 才拿得到**。

    裸解释器（``uv run gallery``、测试）跑起来时 ``NSBundle.mainBundle()`` 是解释器自己那个
    包而不是 Gallery.app，登记进去等于往别人的最近项目里塞东西 —— 何况 Dock 上没有那个包，
    塞了也看不见。于是测试天然不碰本机的最近项目。
    """
    appkit = _appkit()
    if appkit is None:
        return None
    if appkit.NSBundle.mainBundle().bundleIdentifier() != BUNDLE_IDENTIFIER:
        return None
    return _PyObjCRecents(appkit)


def _appkit() -> Any:
    """导入 AppKit。没装 pyobjc（非 macOS）时返回 ``None``，这个功能静默关掉。

    用 ``importlib`` 而不是 ``import``：pyobjc 没有类型桩，而 ``core/`` 是 ``mypy --strict``。
    """
    try:
        return importlib.import_module("AppKit")
    except ImportError:
        return None


class _PyObjCRecents:
    """``NSDocumentController`` 上那两句话。除了 :func:`publish` 谁都不该建它。"""

    def __init__(self, appkit: Any) -> None:
        self._kit = appkit

    def clear(self) -> None:
        self._controller().clearRecentDocuments_(None)

    def note(self, root: Path) -> None:
        self._controller().noteNewRecentDocumentURL_(self._kit.NSURL.fileURLWithPath_(str(root)))

    def _controller(self) -> Any:
        return self._kit.NSDocumentController.sharedDocumentController()
