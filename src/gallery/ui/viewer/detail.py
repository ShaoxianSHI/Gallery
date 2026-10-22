"""大图查看。

用 ``QGraphicsView`` 而不是自绘控件：坐标变换、滚动、平滑缩放都是现成的（``AnchorUnderMouse``
让缩放围绕光标，``ScrollHandDrag`` 给出拖拽平移）。这里只负责显示与变换，两级加载（先预览、
后全分辨率）由主窗口决定；右键菜单也不在这里拼，视图只报「按了右键、在这个位置」（ADR 0021）。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QContextMenuEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
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

from gallery.ui import theme
from gallery.ui.motion import scaled_ms
from gallery.ui.viewer.zoom import MAX_FIT_SCALE, MAX_SCALE, ZOOM_STEP, clamp_scale, fit_scale

OVERLAY_FONT_SIZE = 13
OVERLAY_MARGIN = 16

#: 回到默认视图的动画时长。再长就显得拖沓，再短就看不出是「平滑」过去的。
FIT_ANIMATION_MS = 220

#: 两张照片之间先淡出、再淡入，各用这么长（毫秒）。放映逐张切换用它：慢到能看清是一次
#: 真正的过渡，又不至于让三秒一张的节奏等太久。
FADE_OUT_MS = 450
FADE_IN_MS = 450

#: 判断「已经处于默认视图」的容差：比例用相对误差，位置用屏幕像素。
SCALE_EPSILON = 0.002
CENTER_TOLERANCE_PX = 2.0


#: 角标只用于「加载失败时说明原因」，不显示「正在载入」。
_OVERLAY_STYLE = f"""
QLabel#detailOverlay {{
    background: rgba(150, 40, 40, 0.82);
    color: rgba(255, 255, 255, 0.94);
    border-radius: 9px;
    padding: 6px 12px;
    font-size: {OVERLAY_FONT_SIZE}px;
}}
"""


class _FadeLayer(QWidget):
    """淡出阶段盖在视口上的旧画面快照。

    **不参与场景变换**：视图已经按新图重新适配，如果让旧图继续做场景里的 item，它会跟着
    新的缩放与中心一起跳一下。这里只把切换前那一帧原样画在视口坐标里，旧画面就不会动。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_snapshot(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.setGeometry(self.parentWidget().rect())

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def opacity(self) -> float:
        return self._opacity

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        try:
            painter.setOpacity(self._opacity)
            painter.drawPixmap(self.rect(), self._pixmap)
        finally:
            painter.end()


class DetailView(QGraphicsView):
    """单张大图的查看器。"""

    request_close = Signal()
    request_step = Signal(int)
    #: 按了空格：放映中归「暂停 / 继续」，其余时候与右方向键一样换下一张（由窗口决定）。
    pause_requested = Signal()
    zoom_changed = Signal(float)
    #: 用户在照片上按了右键。位置是**窗口坐标**（``QMenu.exec`` 直接吃它）。
    context_requested = Signal(QPoint)
    #: 用户按住 ⌥ 点了照片：打开它所属影片事件的网站（ADR 0063）。
    movie_open_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, animation_speed: str = "normal") -> None:
        super().__init__(parent)
        self._animation_speed = animation_speed
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self._pixmap = QPixmap()
        self._fit_mode = True
        self._overlay_text = ""

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._overlay = QLabel(self)
        self._overlay.setObjectName("detailOverlay")
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay.setWordWrap(True)
        self._overlay.setStyleSheet(_OVERLAY_STYLE)
        self._overlay.hide()

        self._animating = False
        self._anim_from_scale = 1.0
        self._anim_to_scale = 1.0
        self._anim_from_center = QPointF()
        self._anim_to_center = QPointF()
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self._animation_ms(FIT_ANIMATION_MS))
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._animation_frame)
        self._animation.finished.connect(self._animation_finished)

        #: 上一张先独立地淡出，淡完才轮到新照片淡入 —— 两段各一个动画。淡出的是**视口快照**，
        #: 所以新图在底下重新适配窗口时，旧画面不会跟着缩放或移动。
        self._fade_layer: _FadeLayer | None = None
        self._fade_animation = QVariantAnimation(self)
        self._fade_animation.setDuration(self._animation_ms(FADE_OUT_MS))
        self._fade_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._fade_animation.valueChanged.connect(self._fade_frame)
        self._fade_animation.finished.connect(self._fade_finished)
        self._fade_in_animation = QVariantAnimation(self)
        self._fade_in_animation.setDuration(self._animation_ms(FADE_IN_MS))
        self._fade_in_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._fade_in_animation.valueChanged.connect(self._fade_in_frame)
        self._fade_in_animation.finished.connect(self._fade_in_finished)

    def _apply_theme(self) -> None:
        """底色跟着主题走：硬编码深色会让浅色主题下点开照片突然变一块黑。"""
        color = theme.current_theme().background
        self.setBackgroundBrush(color)
        self._scene.setBackgroundBrush(color)

    @property
    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    @property
    def current_scale(self) -> float:
        return float(self.transform().m11())

    @property
    def image_size(self) -> tuple[int, int]:
        if self._pixmap.isNull():
            return (0, 0)
        return (self._pixmap.width(), self._pixmap.height())

    def set_image(self, image: QImage, *, refit: bool = True, fade: bool = False) -> None:
        """显示一张图像。

        :param refit: 是否重新适配窗口。替换图像（预览 → 全分辨率）时传 ``False``，
            可以保住用户当前的缩放与位置。
        :param fade: 是否先让**上一张**淡出、再让新图淡入。放映逐张切换时用它；同一张的
            预览 → 全分辨率替换不该淡入，否则画面会来回闪。
        """
        self.hide_overlay()
        self.stop_animation()
        # **在改场景之前抓**：旧图现在的缩放、位置、留白都还在这一帧里。
        previous = self.viewport().grab() if fade else QPixmap()
        pixmap = QPixmap.fromImage(image)
        # 先放开上一张：32MP 照片的全分辨率副本约 128 MB，新旧同时存在会翻倍内存峰值。
        self._item.setPixmap(QPixmap())
        self._pixmap = QPixmap()
        self._scene.setSceneRect(QRectF())
        self._pixmap = pixmap
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        if not previous.isNull():
            self._start_fade(previous)
        if refit or self._fit_mode:
            self.fit_to_window()
        else:
            self._emit_scale()

    def prepare_switch(self) -> None:
        """切图前的准备：撤掉上一张可能残留的错误提示，但不显示任何加载提示 ——
        上一张继续留在屏幕上等新图，一闪而过的角标比多等 50ms 更扎眼。
        """
        if self._overlay.isVisible():
            self.hide_overlay()

    def show_error(self, text: str) -> None:
        """显示加载失败的原因。上一张留在原处，右上角给出说明。"""
        self._overlay_text = text
        self._show_overlay()

    def hide_overlay(self) -> None:
        self._overlay.hide()

    def clear_image(self) -> None:
        """真正清空画面（切库或没有可显示内容时用），会释放全分辨率副本。"""
        self._clear_fade()
        self.hide_overlay()
        self.stop_animation()
        self._pixmap = QPixmap()
        self._item.setPixmap(QPixmap())
        self._scene.setSceneRect(QRectF())
        self.resetTransform()

    def set_animation_speed(self, speed: str) -> None:
        """换一档动画速度：正在跑的那一段不打断，下一段用新时长。"""
        self._animation_speed = speed
        self._animation.setDuration(self._animation_ms(FIT_ANIMATION_MS))
        self._fade_animation.setDuration(self._animation_ms(FADE_OUT_MS))
        self._fade_in_animation.setDuration(self._animation_ms(FADE_IN_MS))

    def _animation_ms(self, base_ms: int) -> int:
        return scaled_ms(base_ms, self._animation_speed)

    def _start_fade(self, previous: QPixmap) -> None:
        """先让上一张淡出；它完全消失之后才开始新图淡入。

        旧画面是一张**视口快照**，不跟着场景变换走；新图在底下按自己的尺寸适配窗口，
        但**这一阶段一直保持透明**，所以旧画面既不会缩放 / 移位，也不会被下一张提前顶掉。
        """
        self._clear_fade()
        if self._animation_ms(FADE_OUT_MS) <= 0:
            return  # 「关」档：直接显示新图，不铺快照
        self._item.setOpacity(0.0)
        layer = _FadeLayer(self.viewport())
        layer.set_snapshot(previous)
        layer.show()
        layer.raise_()
        self._fade_layer = layer
        self._fade_animation.stop()
        self._fade_animation.setStartValue(1.0)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.start()

    def _fade_frame(self, value: object) -> None:
        if self._fade_layer is not None:
            self._fade_layer.set_opacity(float(value))  # type: ignore[arg-type]

    def _fade_finished(self) -> None:
        """上一张已经彻底消失：撤掉快照，再让新图从透明淡入。"""
        self._remove_fade_layer()
        self._fade_in_animation.stop()
        self._fade_in_animation.setStartValue(0.0)
        self._fade_in_animation.setEndValue(1.0)
        self._fade_in_animation.start()

    def _fade_in_frame(self, value: object) -> None:
        self._item.setOpacity(float(value))  # type: ignore[arg-type]

    def _fade_in_finished(self) -> None:
        self._item.setOpacity(1.0)

    def _remove_fade_layer(self) -> None:
        self._fade_animation.stop()
        layer = self._fade_layer
        self._fade_layer = None
        if layer is not None:
            layer.hide()
            layer.deleteLater()

    def _clear_fade(self) -> None:
        self._fade_in_animation.stop()
        self._remove_fade_layer()
        self._item.setOpacity(1.0)

    def _show_overlay(self) -> None:
        self._overlay.setText(self._overlay_text)
        self._overlay.adjustSize()
        self._overlay.show()
        self._reposition_overlay()

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
        self.stop_animation()
        # 「适应窗口」不能套用手动缩放的上限：小图铺满视口需要的倍数会超过 8 倍
        # （168 像素的占位缩略图在大窗口里要 12 倍以上），卡住的话占位图只占中间一小块。
        scale = clamp_scale(scale, maximum=MAX_FIT_SCALE if fitting else MAX_SCALE)
        self.setTransform(QTransform.fromScale(scale, scale))
        self._fit_mode = fitting
        self._emit_scale()

    def fit_scale_value(self) -> float:
        """当前窗口下「适应窗口」的比例。"""
        viewport = self.viewport().size()
        return fit_scale(
            (self._pixmap.width(), self._pixmap.height()),
            (viewport.width(), viewport.height()),
        )

    def animate_to_fit(self) -> None:
        """平滑回到默认视图：适应窗口并把图片摆回正中。

        直接 setTransform 会「跳」一下，把比例与视口中心一起插值才看得出它是怎么过去的。
        """
        if self._pixmap.isNull():
            return
        target_scale = self.fit_scale_value()
        target_center = QPointF(self._pixmap.width() / 2, self._pixmap.height() / 2)
        start_scale = self.current_scale
        start_center = self.mapToScene(self.viewport().rect().center())
        if self._is_default_view(start_scale, start_center, target_scale, target_center):
            # 已经在那儿了：不播动画，只把状态摆正。
            self.stop_animation()
            self._fit_mode = True
            self._emit_scale()
            return

        if self._animation_ms(FIT_ANIMATION_MS) <= 0:
            self._apply_scale(target_scale, fitting=True)
            self.centerOn(target_center)
            return

        self._anim_from_scale = start_scale
        self._anim_to_scale = target_scale
        self._anim_from_center = start_center
        self._anim_to_center = target_center
        self._animating = True
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def _is_default_view(
        self,
        scale: float,
        center: QPointF,
        target_scale: float,
        target_center: QPointF,
    ) -> bool:
        if abs(scale - target_scale) > SCALE_EPSILON:
            return False
        tolerance = CENTER_TOLERANCE_PX / max(scale, 1e-6)
        return (
            abs(center.x() - target_center.x()) <= tolerance
            and abs(center.y() - target_center.y()) <= tolerance
        )

    def _animation_frame(self, value: object) -> None:
        progress = float(value)  # type: ignore[arg-type]
        scale = self._anim_from_scale + (self._anim_to_scale - self._anim_from_scale) * progress
        center = QPointF(
            self._anim_from_center.x()
            + (self._anim_to_center.x() - self._anim_from_center.x()) * progress,
            self._anim_from_center.y()
            + (self._anim_to_center.y() - self._anim_from_center.y()) * progress,
        )
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(center)
        # 状态栏的百分比跟着动画走，而不是结束时才跳一下。
        self.zoom_changed.emit(scale)

    def _animation_finished(self) -> None:
        self._animating = False
        self._fit_mode = True
        self._emit_scale()

    def stop_animation(self) -> None:
        if self._animation.state() != QVariantAnimation.State.Stopped:
            self._animation.stop()
        self._animating = False

    def _emit_scale(self) -> None:
        self.zoom_changed.emit(self.current_scale)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """双击图片：平滑回到默认视图（适应窗口并居中）。"""
        if event.button() == Qt.MouseButton.LeftButton and not self._pixmap.isNull():
            self.animate_to_fit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """按住 ⌥ 点一下：打开这一张所属影片的网站（ADR 0063）。"""
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
            and not self._pixmap.isNull()
        ):
            self.movie_open_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """右键照片：把位置告诉窗口，菜单由 ``MainWindow`` 拼（ADR 0021）。

        视图只回答「这一下落在哪」，菜单里有什么它不该知道。传 ``globalPos`` 而不是
        ``pos``（``QMenu.exec`` 要屏幕坐标）；画面上什么都没有时也不弹。
        """
        if self._pixmap.isNull():
            event.accept()
            return
        self.context_requested.emit(event.globalPos())
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """⌘ + 滚轮缩放；普通滚动交给基类做平移（与 macOS 预览一致）。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.request_close.emit()
            return
        if key == Qt.Key.Key_Left:
            self.request_step.emit(-1)
            return
        if key == Qt.Key.Key_Right:
            self.request_step.emit(1)
            return
        if key == Qt.Key.Key_Space:
            self.pause_requested.emit()
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

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        if self._fit_mode and not self._animating:
            self.fit_to_window()
        if self._fade_layer is not None:
            self._fade_layer.setGeometry(self.viewport().rect())
        self._reposition_overlay()

    def _reposition_overlay(self) -> None:
        """贴到视口右上角；文案过长时换行并限制宽度，不会横穿整张照片。"""
        area = self.viewport().geometry()
        self._overlay.setMaximumWidth(max(160, area.width() - 2 * OVERLAY_MARGIN))
        self._overlay.adjustSize()
        width = self._overlay.width()
        x = area.right() - OVERLAY_MARGIN - width + 1
        self._overlay.move(max(area.left() + OVERLAY_MARGIN, x), area.top() + OVERLAY_MARGIN)


__all__ = [
    "FADE_IN_MS",
    "FADE_OUT_MS",
    "FIT_ANIMATION_MS",
    "OVERLAY_MARGIN",
    "DetailView",
]
