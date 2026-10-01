"""大图查看。

用 ``QGraphicsView`` 而不是自绘控件：坐标变换、滚动、平滑缩放都是现成的 ——
``AnchorUnderMouse`` 让缩放围绕光标进行，``ScrollHandDrag`` 直接给出拖拽平移。

这里只负责「显示与变换」。两级加载（先预览、后全分辨率）由主窗口决定，
因为它才知道当前窗口大小与内存预算。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QNativeGestureEvent,
    QPainter,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QWidget,
)

from gallery.ui.zoom import ZOOM_STEP, clamp_scale, fit_scale

#: 查看区的背景色（接近 macOS 的深色看图背景）。
BACKGROUND = QColor(28, 28, 30)

#: 「正在载入」提示的字号。
MESSAGE_FONT_SIZE = 15


class DetailView(QGraphicsView):
    """单张大图的查看器。"""

    #: 用户要求返回（Esc / 回车）。
    request_close = Signal()
    #: 用户要求切换照片：-1 上一张，+1 下一张。
    request_step = Signal(int)
    #: 缩放比例变化（用于状态栏显示）。
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self._pixmap = QPixmap()
        self._fit_mode = True

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(BACKGROUND)
        self._scene.setBackgroundBrush(BACKGROUND)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._message = QLabel(self)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._message.setStyleSheet(
            f"color: rgba(255,255,255,0.75); font-size: {MESSAGE_FONT_SIZE}px;"
        )
        self._message.hide()

    # ---- 状态 --------------------------------------------------------------

    @property
    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    @property
    def current_scale(self) -> float:
        return float(self.transform().m11())

    @property
    def is_fitting(self) -> bool:
        """当前是否为「适应窗口」状态。窗口尺寸变化时据此决定要不要重新适配。"""
        return self._fit_mode

    @property
    def image_size(self) -> tuple[int, int]:
        if self._pixmap.isNull():
            return (0, 0)
        return (self._pixmap.width(), self._pixmap.height())

    # ---- 内容 --------------------------------------------------------------

    def set_image(self, image: QImage, *, refit: bool = True) -> None:
        """显示一张图像。

        :param refit: 是否重新适配窗口。替换图像（预览 → 全分辨率）时传 ``False``，
            可以保住用户当前的缩放与位置，不会「跳一下」。
        """
        self._message.hide()
        pixmap = QPixmap.fromImage(image)
        # 先放开上一张：32MP 照片的全分辨率副本约 128 MB，
        # 让新旧两张同时存在会把内存峰值直接翻倍。
        self._item.setPixmap(QPixmap())
        self._pixmap = QPixmap()
        self._scene.setSceneRect(QRectF())
        self._pixmap = pixmap
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        if refit or self._fit_mode:
            self.fit_to_window()
        else:
            self._emit_scale()

    def show_message(self, text: str) -> None:
        """清空画面并显示一句提示（例如「正在载入…」或错误说明）。"""
        self._pixmap = QPixmap()
        self._item.setPixmap(QPixmap())
        self._scene.setSceneRect(QRectF())
        self.resetTransform()
        self._message.setText(text)
        self._message.show()
        self._reposition_message()

    def clear(self) -> None:
        self.show_message("")

    # ---- 缩放 --------------------------------------------------------------

    def fit_to_window(self) -> None:
        if self._pixmap.isNull():
            return
        viewport = self.viewport().size()
        scale = fit_scale(
            (self._pixmap.width(), self._pixmap.height()),
            (viewport.width(), viewport.height()),
        )
        self._apply_scale(scale, fitting=True)

    def actual_size(self) -> None:
        if self._pixmap.isNull():
            return
        self._apply_scale(1.0, fitting=False)

    def zoom_in(self) -> None:
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_by(1 / ZOOM_STEP)

    def zoom_by(self, factor: float) -> None:
        """按倍率缩放（围绕光标位置）。"""
        if self._pixmap.isNull():
            return
        self._apply_scale(self.current_scale * factor, fitting=False)

    def _apply_scale(self, scale: float, *, fitting: bool) -> None:
        scale = clamp_scale(scale)
        self.setTransform(QTransform.fromScale(scale, scale))
        self._fit_mode = fitting
        self._emit_scale()

    def _emit_scale(self) -> None:
        self.zoom_changed.emit(self.current_scale)

    # ---- 事件 --------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        """⌘/Ctrl + 滚轮缩放；普通滚动交给基类做平移（与 macOS 预览一致）。"""
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            delta = event.angleDelta().y()
            if delta:
                self.zoom_by(ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event: QEvent) -> bool:
        # macOS 触控板捏合：原生手势事件不会走 wheelEvent。
        if (
            event.type() == QEvent.Type.NativeGesture
            and isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self.zoom_by(1.0 + float(event.value()))
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.request_close.emit()
            return
        if key == Qt.Key.Key_Left:
            self.request_step.emit(-1)
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.request_step.emit(1)
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            return
        if key == Qt.Key.Key_Minus:
            self.zoom_out()
            return
        if key == Qt.Key.Key_0:
            self.fit_to_window()
            return
        if key == Qt.Key.Key_1:
            self.actual_size()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        if self._fit_mode:
            self.fit_to_window()
        self._reposition_message()

    def _reposition_message(self) -> None:
        self._message.setGeometry(self.viewport().rect())


__all__ = ["BACKGROUND", "DetailView"]
