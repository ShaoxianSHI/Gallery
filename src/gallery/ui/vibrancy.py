"""毛玻璃背景：macOS 上借用系统的 ``NSVisualEffectView``。

**为什么不用 ``QGraphicsBlurEffect``**：那个效果只模糊控件**自己画出来的内容**。背景
透明的时候等于在模糊空气，拿它冒充毛玻璃，得到的只是一块纯色 —— 用户要的是「透出
后面的照片并把它糊掉」，这种活只能交给系统合成器实时做。

**挂在哪一层**：Qt 在 Cocoa 上把没有原生窗口的子控件直接画进父控件的 backing store
（实测：``QNSView`` 没有任何子视图，文字也是画在它自己的 backing store 上的），而
AppKit 里子视图永远画在父视图自身内容的**上面**。所以毛玻璃**不能**做成控件的子视图
—— 那会把文字一起盖住。正确的位置是：放进控件视图的父视图里，紧挨着它下面一层
（``NSWindowBelow`` + 相对位置）。于是层次自下而上正好是：照片（父视图的 backing
store）→ 毛玻璃（实时糊掉照片）→ 文字（控件自己的 backing store）。

**拿不到就降级**：不是 macOS、没装 pyobjc、离屏平台（测试就是）一律 ``attached`` 为
``False``，调用方改用半透明底色。功能只降级、不报错 —— 界面出不来是不能接受的。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

#: 悬浮条的圆角半径（逻辑像素）。跟照片圆角取同一个值，才像同一套东西。
CORNER_RADIUS = 10.0

#: 需要重新对齐毛玻璃位置的事件：控件移动、缩放、显隐、换父视图。
_SYNC_EVENTS = frozenset(
    {
        QEvent.Type.Move,
        QEvent.Type.Resize,
        QEvent.Type.Show,
        QEvent.Type.Hide,
        QEvent.Type.ParentChange,
    }
)


def _appkit() -> dict[str, Any] | None:
    """取 AppKit 里毛玻璃要用的符号；取不到（没装 pyobjc、非 macOS）返回 ``None``。

    故意在函数里 import：pyobjc 只在 macOS 上装，模块顶层 import 会让整个
    ``gallery.ui`` 在别的系统上直接 ImportError。
    """
    try:
        from AppKit import (
            NSVisualEffectBlendingModeWithinWindow,
            NSVisualEffectMaterialSidebar,
            NSVisualEffectStateFollowsWindowActiveState,
            NSVisualEffectView,
            NSWindowBelow,
        )
    except ImportError:
        return None
    return {
        "view": NSVisualEffectView,
        "material": NSVisualEffectMaterialSidebar,
        # WithinWindow 模糊同一窗口里画在它后面的东西（照片）。BehindWindow 是给
        # 半透明窗口用的，用在这里会糊到窗口外面去。
        "blending": NSVisualEffectBlendingModeWithinWindow,
        "state": NSVisualEffectStateFollowsWindowActiveState,
        "below": NSWindowBelow,
    }


def _ns_view(widget: QWidget) -> Any | None:
    """把控件的 ``winId`` 包成 NSView。取不到就返回 ``None``，不做任何猜测。"""
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
    except Exception:  # 原生层什么都可能抛，不能让它掀翻整个窗口
        logger.debug("包装 NSView 失败", exc_info=True)
        return None


class Vibrancy(QObject):
    """贴在某个控件下面、跟着它移动缩放的毛玻璃层。

    建好控件就建一个 ``Vibrancy``，然后看 ``attached``：为 ``False`` 就退回半透明
    底色。控件还没进窗口时挂不上也没关系，第一次显示时会自动再试 —— ``attached``
    变化时 ``attached_changed`` 会通知调用方换样式。
    """

    #: ``attached`` 状态变了（第一次挂上，或始终挂不上）。
    attached_changed = Signal(bool)

    def __init__(self, widget: QWidget, *, corner_radius: float = CORNER_RADIUS) -> None:
        super().__init__(widget)
        self._widget = widget
        self._corner_radius = corner_radius
        app = QGuiApplication.instance()
        self._appkit = _appkit() if app is not None and app.platformName() == "cocoa" else None
        self._effect: Any | None = None
        self._attached = False
        self._sync_pending = False
        widget.installEventFilter(self)
        self._set_attached(self._attach())

    @property
    def attached(self) -> bool:
        """毛玻璃是否真的挂上了。挂不上时调用方应当改用半透明底色。"""
        return self._attached

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt 的接口名
        if event.type() in _SYNC_EVENTS:
            if not self._attached:
                # 窗口还没显示时控件没有父视图，第一次 Show 才是挂得上的时机。
                self._set_attached(self._attach())
            self._sync()
            self._schedule_sync()
        return super().eventFilter(watched, event)

    def _schedule_sync(self) -> None:
        """事件之后再补一次同步。

        必须补：Qt 是**先**改控件几何、**后**改原生 NSView 的 frame，而布局改的是几何，
        那一刻 NSView 还停在旧位置（实测面包屑从 640×480 缩到 88×27，原生视图要等到
        随后的布局落地才跟上）。只在事件里同步一次的话，毛玻璃会一直停在旧框上 ——
        表现为一整块糊在半屏上的大方块。
        """
        if self._sync_pending:
            return  # 一次布局改动会连着来好几个事件，只补最后那一次
        self._sync_pending = True
        QTimer.singleShot(0, self._deferred_sync)

    def _deferred_sync(self) -> None:
        self._sync_pending = False
        self._sync()

    def _set_attached(self, value: bool) -> None:
        if value == self._attached:
            return
        self._attached = value
        self.attached_changed.emit(value)

    def _attach(self) -> bool:
        """把毛玻璃视图插进控件视图的父视图、紧挨在它下面一层。"""
        if self._appkit is None:
            return False
        widget = self._widget
        # 少了这一句，Qt 会用不透明底色把毛玻璃盖住。
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        view = _ns_view(widget)
        if view is None:
            return False
        parent = view.superview()
        if parent is None:
            return False  # 还没进窗口，等 Show 之后再试
        try:
            # 用 view.frame()，不要自己换算：frame 本来就是「相对父视图」的位置和大小，
            # 正是毛玻璃需要的。convertRect 之类反而会踩坑 —— bounds 是控件**创建时**
            # 的大小（默认 640×480），Qt 只改 frame 不改 bounds，拿它换算出来的框会一直
            # 是那个旧尺寸，面包屑被布局缩到 88×27 之后毛玻璃还铺着 640×480。
            effect = self._appkit["view"].alloc().initWithFrame_(view.frame())
            effect.setMaterial_(self._appkit["material"])
            effect.setBlendingMode_(self._appkit["blending"])
            effect.setState_(self._appkit["state"])
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(self._corner_radius)
            effect.layer().setMasksToBounds_(True)
            parent.addSubview_(effect)
            parent.addSubview_positioned_relativeTo_(effect, self._appkit["below"], view)
        except Exception:  # 原生层失败只该降级，不该崩窗口
            logger.info("挂毛玻璃失败，退回半透明底色", exc_info=True)
            return False
        self._effect = effect
        self._sync()
        return True

    def _sync(self) -> None:
        """控件动了，就把毛玻璃挪到同样的位置与大小。"""
        effect = self._effect
        if effect is None:
            return
        view = _ns_view(self._widget)
        if view is None:
            return
        parent = view.superview()
        if parent is None:
            return
        # pyobjc 每次访问都新建一个 Python 包装对象，只能用 == 比指针，不能用 is。
        if effect.superview() != parent:
            # 控件被换过父视图（切页面、重排布局），毛玻璃得跟着搬家。
            parent.addSubview_positioned_relativeTo_(effect, self._appkit["below"], view)
        # view.frame() 就是「相对父视图」的位置与大小，直接用。
        effect.setFrame_(view.frame())
        # 控件隐藏时它的视图会被 Qt 移走，但毛玻璃是我们单独加的，得自己跟着藏。
        effect.setHidden_(not self._widget.isVisible())


__all__ = ["CORNER_RADIUS", "Vibrancy"]
