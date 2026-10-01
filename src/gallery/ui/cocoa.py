"""访问 macOS 原生层的薄薄一层。

只有 macOS 上才用得上；别的系统（以及测试用的离屏平台）这里一律返回「拿不到」，
由调用方各自降级。**不做任何猜测**：取不到就是取不到，宁可降级也不能让原生层的
异常掀翻整个窗口。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


def is_cocoa() -> bool:
    """当前是不是真的跑在 macOS 原生平台上。

    必须判断平台再碰 AppKit：离屏平台下 ``winId()`` 给的是 Qt 自己的伪句柄，
    拿它去调原生接口会直接崩。
    """
    app = QGuiApplication.instance()
    return app is not None and app.platformName() == "cocoa"


def ns_view(widget: QWidget) -> Any | None:
    """把控件的 ``winId`` 包成 NSView。取不到就返回 ``None``。"""
    if not is_cocoa():
        return None
    try:
        import objc

        handle = int(widget.winId())
    except (ImportError, TypeError, ValueError, RuntimeError):
        return None
    if handle == 0:
        return None
    try:
        # pyobjc 的 objc_object 是 C 函数，只接受关键字参数。
        return objc.objc_object(c_void_p=handle)
    except Exception:  # 原生层什么都可能抛
        logger.debug("包装 NSView 失败", exc_info=True)
        return None


def ns_window(widget: QWidget) -> Any | None:
    """控件所属的 NSWindow。取不到就返回 ``None``。"""
    view = ns_view(widget)
    if view is None:
        return None
    try:
        window = view.window()
    except Exception:
        logger.debug("取 NSWindow 失败", exc_info=True)
        return None
    return window if window is not None else None


__all__ = ["is_cocoa", "ns_view", "ns_window"]
