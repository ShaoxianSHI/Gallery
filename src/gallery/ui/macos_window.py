"""macOS 专有：让内容顶到窗口最上沿，标题栏那一段变成浮在照片上的毛玻璃。

**为什么原来会有一条白条**：macOS 的标题栏自带一块不透明底色，Qt 也默认把内容
避开它（``safeAreaMargins``，28 点高）。所以照片从标题栏**下面**开始，中间永远隔
着一行白的 —— 这正是要去掉的东西。

**怎么去掉**：

1. 给 NSWindow 加上 ``NSWindowStyleMaskFullSizeContentView``，内容视图铺满整个窗口；
2. ``titlebarAppearsTransparent = YES``，标题栏不再自己画底；
3. 关掉 Qt 的 ``WA_ContentsMarginsRespectsSafeArea`` —— 少了这一步，Qt 仍然会把内容
   往下挪 28 点（这一步是 Qt 6 的公开开关，不必再跟布局较劲）。

做完这三步，红黄绿按钮和标题文字仍然由系统绘制，仍然能拖动窗口，而照片会一路顶到
窗口最上沿。**但标题栏那一段不能空着**：那里得铺一层系统毛玻璃把底下的照片糊掉，
否则就是硬邦邦的照片顶着标题文字。那层毛玻璃就是 :class:`TitleBarGlass`。

拿不到原生层（非 macOS、离屏平台）时全部退回原样：一个多余的浮层都不加。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from gallery.ui.cocoa import is_cocoa, ns_window
from gallery.ui.vibrancy import FrostedPanel

logger = logging.getLogger(__name__)

#: 标题栏那一段的高度（点）。取不到原生窗口时的兜底值，与 macOS 默认一致。
FALLBACK_TITLEBAR_HEIGHT = 28


def enable_full_size_content(window: QWidget) -> bool:
    """让内容铺满整个窗口、标题栏不再自带白底。成功返回 ``True``。

    返回 ``False`` 表示没改成（非 macOS / 取不到 NSWindow / 原生层报错），调用方
    应当按「窗口还是老样子」处理。
    """
    if not is_cocoa():
        return False
    try:
        from AppKit import NSColor, NSWindowStyleMaskFullSizeContentView
    except ImportError:
        logger.info("没有 pyobjc，标题栏保持系统默认样式")
        return False
    native = ns_window(window)
    if native is None:
        return False
    try:
        native.setStyleMask_(int(native.styleMask()) | NSWindowStyleMaskFullSizeContentView)
        native.setTitlebarAppearsTransparent_(True)
        native.setBackgroundColor_(NSColor.clearColor())
    except Exception:  # 原生层失败只该降级，不该崩窗口
        logger.info("改标题栏失败，保持系统默认样式", exc_info=True)
        return False
    # 这一步是关键：不关掉它，Qt 依旧会把内容往下挪一个标题栏的高度。
    flag = Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea
    window.setAttribute(flag, False)
    central = window.centralWidget()
    if central is not None:
        central.setAttribute(flag, False)
    return True


def titlebar_height(window: QWidget) -> int:
    """标题栏占多少点高（毛玻璃条就画这么高）。

    开了全尺寸内容之后，``contentRectForFrameRect_`` 会等于整个窗口，算不出高度；
    所以用 ``contentLayoutRect`` —— 它是系统给出的「内容安全区」，高度差就是标题栏。
    """
    if not is_cocoa():
        return 0
    native = ns_window(window)
    if native is None:
        return 0
    try:
        frame_height = native.frame().size.height
        layout = native.contentLayoutRect()
    except Exception:
        logger.debug("读标题栏高度失败", exc_info=True)
        return 0
    height = int(round(frame_height - layout.size.height))
    # 全屏时系统不再保留标题栏，算出 0 或负数都当作没有标题栏。
    return height if height > 0 else 0


class TitleBarGlass(FrostedPanel):
    """盖在标题栏那一段上的毛玻璃条。

    纯装饰：鼠标穿透（还能点到底下的照片、还能拖动窗口），跟随宽度随窗口拉伸。
    """

    def __init__(self, parent: QWidget) -> None:
        # 贴着窗口上沿，底部是直角：圆角留给内部浮层（面包屑），这里不圆。
        super().__init__(parent, name="titlebar_glass", corner_radius=0.0, mouse_transparent=True)

    def fit(self, width: int, height: int) -> None:
        """铺满整个宽度，高度就是标题栏那一条。"""
        self.setGeometry(0, 0, width, max(0, height))
        self.raise_()

    @property
    def _effect(self) -> Any | None:  # 调试用：底层毛玻璃视图
        return self._vibrancy._effect


__all__ = [
    "FALLBACK_TITLEBAR_HEIGHT",
    "TitleBarGlass",
    "enable_full_size_content",
    "titlebar_height",
]
