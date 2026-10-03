"""裁剪模式：照片上压暗一层，中间一个可拖的框（ADR 0027）。

进这个模式之前照片已经摆正、旋转与镜像也已经叠加过了，因此**框里的坐标就是草稿要记的
那个坐标系的坐标** —— 换算只在退出这一档做一次（见 ``MainWindow._finish_crop``）。

## 为什么几何是纯函数

框的判定与拖动是这一档里最容易写出「差一个像素」的地方：手柄命中、拖到边界时夹住、
反着拖时上下边互换 —— 每一条都是可以单独列出来验的算术。所以它们在
:func:`handle_at` / :func:`resize_rect` / :func:`rect_from_drag` 三个纯函数里，
控件只负责取事件、画结果。

``HitTest`` 刻意不依赖 Qt 的坐标类型（收 ``float``），于是这些用例不必起一个 ``QApplication``。
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

#: 手柄的命中半径（点）。
HANDLE_HIT: Final = 9.0

#: 拖出来的框最短边不许小于多少**显示像素** —— 那是手感，不是存储口径
#: （存储那两道下限在 ``core.media.edit.MIN_CROP_EDGE``）。
MIN_VIEW_EDGE: Final = 24.0

#: 三分线的位置：1/3 与 2/3。
RULE_OF_THIRDS: Final = (1 / 3, 2 / 3)

#: 框线的粗细与内外两档颜色。
_BORDER: Final = 1.0
_INNER: Final = QColor(255, 255, 255, 235)
_OUTER: Final = QColor(0, 0, 0, 170)
_HANDLE_EDGE: Final = 7.0


class Handle(Enum):
    """框上的八个手柄，加上「整块拖动」与「空白处画一个新框」。"""

    NORTH_WEST = "nw"
    NORTH = "n"
    NORTH_EAST = "ne"
    EAST = "e"
    SOUTH_EAST = "se"
    SOUTH = "s"
    SOUTH_WEST = "sw"
    WEST = "w"
    MOVE = "move"
    NEW = "new"


#: 手柄在框上的相对位置（0–1）：``(x, y)``。
_HANDLE_SPOTS: Final[tuple[tuple[Handle, float, float], ...]] = (
    (Handle.NORTH_WEST, 0.0, 0.0),
    (Handle.NORTH, 0.5, 0.0),
    (Handle.NORTH_EAST, 1.0, 0.0),
    (Handle.EAST, 1.0, 0.5),
    (Handle.SOUTH_EAST, 1.0, 1.0),
    (Handle.SOUTH, 0.5, 1.0),
    (Handle.SOUTH_WEST, 0.0, 1.0),
    (Handle.WEST, 0.0, 0.5),
)


def handle_at(
    rect: tuple[float, float, float, float],
    x: float,
    y: float,
    *,
    hit: float = HANDLE_HIT,
) -> Handle:
    """点在框上的哪儿：某个手柄、整块、还是空白（:attr:`Handle.NEW`）。

    **手柄优先于「整块拖动」**：角上的那一小块既是手柄也是框，不判优先级的话，
    用户想缩框却拖动了整块。
    """
    left, top, right, bottom = rect
    for handle, fx, fy in _HANDLE_SPOTS:
        spot_x = left + (right - left) * fx
        spot_y = top + (bottom - top) * fy
        if abs(x - spot_x) <= hit and abs(y - spot_y) <= hit:
            return handle
    if left <= x <= right and top <= y <= bottom:
        return Handle.MOVE
    return Handle.NEW


def resize_rect(
    rect: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
    x: float,
    y: float,
    handle: Handle,
    *,
    min_edge: float = MIN_VIEW_EDGE,
) -> tuple[float, float, float, float]:
    """把手柄拖到 ``(x, y)``，答出新的框（``left, top, right, bottom``）。

    **反着拖会把那一条边挪到另一侧** —— 把右边缘拖过左边缘，框就镜像过去了。
    访达与预览都这样，也省掉了「要不要换成对面的手柄」那套分支。

    夹住分两回：先夹在照片内，再保证不小于最短边。顺序反过来就不成立了 ——
    先按「对面那条边」夹的话，框永远过不去，也就永远镜像不了。
    """
    left, top, right, bottom = rect
    b_left, b_top, b_right, b_bottom = bounds
    if handle in (Handle.NORTH_WEST, Handle.WEST, Handle.SOUTH_WEST):
        left = _clamp(x, b_left, b_right)
    if handle in (Handle.NORTH_WEST, Handle.NORTH, Handle.NORTH_EAST):
        top = _clamp(y, b_top, b_bottom)
    if handle in (Handle.NORTH_EAST, Handle.EAST, Handle.SOUTH_EAST):
        right = _clamp(x, b_left, b_right)
    if handle in (Handle.SOUTH_WEST, Handle.SOUTH, Handle.SOUTH_EAST):
        bottom = _clamp(y, b_top, b_bottom)
    new_left, new_top, new_right, new_bottom = _sorted(left, top, right, bottom)
    # 镜像之后仍然太窄，就把它撑回最短边 —— 撑的方向是「离这次拖的那条边最远的那条」。
    if new_right - new_left < min_edge:
        if handle in (Handle.NORTH_EAST, Handle.EAST, Handle.SOUTH_EAST):
            new_right = _clamp(new_left + min_edge, b_left, b_right)
            new_left = max(b_left, new_right - min_edge)
        else:
            new_left = _clamp(new_right - min_edge, b_left, b_right)
            new_right = min(b_right, new_left + min_edge)
    if new_bottom - new_top < min_edge:
        if handle in (Handle.SOUTH_WEST, Handle.SOUTH, Handle.SOUTH_EAST):
            new_bottom = _clamp(new_top + min_edge, b_top, b_bottom)
            new_top = max(b_top, new_bottom - min_edge)
        else:
            new_top = _clamp(new_bottom - min_edge, b_top, b_bottom)
            new_bottom = min(b_bottom, new_top + min_edge)
    return new_left, new_top, new_right, new_bottom


def move_rect(
    rect: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
    dx: float,
    dy: float,
) -> tuple[float, float, float, float]:
    """整块拖动：按位移平移，并夹在照片内（拖到边就不再往那边走）。"""
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    return (
        _clamp(left + dx, bounds[0], bounds[2] - width),
        _clamp(top + dy, bounds[1], bounds[3] - height),
        _clamp(left + dx, bounds[0], bounds[2] - width) + width,
        _clamp(top + dy, bounds[1], bounds[3] - height) + height,
    )


def rect_from_drag(
    bounds: tuple[float, float, float, float],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    min_edge: float = MIN_VIEW_EDGE,
) -> tuple[float, float, float, float]:
    """在空白处拉一个新框。夹在照片内，且不小于最短边。"""
    b_left, b_top, b_right, b_bottom = bounds
    left, right = sorted((_clamp(x0, b_left, b_right), _clamp(x1, b_left, b_right)))
    top, bottom = sorted((_clamp(y0, b_top, b_bottom), _clamp(y1, b_top, b_bottom)))
    if right - left < min_edge:
        middle = (left + right) / 2
        left = _clamp(middle - min_edge / 2, b_left, b_right)
        right = _clamp(middle + min_edge / 2, b_left, b_right)
    if bottom - top < min_edge:
        middle = (top + bottom) / 2
        top = _clamp(middle - min_edge / 2, b_top, b_bottom)
        bottom = _clamp(middle + min_edge / 2, b_top, b_bottom)
    return _sorted(left, top, right, bottom)


def _sorted(
    left: float, top: float, right: float, bottom: float
) -> tuple[float, float, float, float]:
    """反着拖出来的那一对边换回正常次序。"""
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _outline(rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)`` → ``(宽, 高, 左, 上)``（画的时候要的是前两个）。"""
    return (rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1])


class CropOverlay(QWidget):
    """盖在大图视口上的那一层。**只画与拖，回车 / Esc 交出去。**"""

    #: 用户按回车：框选定了（场景坐标）。
    committed = Signal(float, float, float, float)
    #: 用户按 Esc：什么都没发生。
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._drag: Handle | None = None
        self._origin: QPointF | None = None
        self.hide()

    # ---- 对外 --------------------------------------------------------------

    def begin(self, bounds: QRectF, initial: QRectF | None = None) -> None:
        """进入裁剪模式。``initial`` 给上一次裁的框，没有就默认留中间 80%。

        :param bounds: 照片在**场景坐标**里的矩形。
        """
        self._bounds = (bounds.left(), bounds.top(), bounds.right(), bounds.bottom())
        if initial is not None:
            self._rect = (
                max(initial.left(), bounds.left()),
                max(initial.top(), bounds.top()),
                min(initial.right(), bounds.right()),
                min(initial.bottom(), bounds.bottom()),
            )
        else:
            inset_x = (bounds.width() - bounds.width() * 0.8) / 2
            inset_y = (bounds.height() - bounds.height() * 0.8) / 2
            self._rect = (
                bounds.left() + inset_x,
                bounds.top() + inset_y,
                bounds.right() - inset_x,
                bounds.bottom() - inset_y,
            )
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def finish(self) -> None:
        """收起这一层（不发信号）。"""
        self._drag = None
        self._origin = None
        self.hide()

    def scene_rect(self) -> QRectF:
        """当前那个框（场景坐标）。"""
        return QRectF(
            self._rect[0],
            self._rect[1],
            self._rect[2] - self._rect[0],
            self._rect[3] - self._rect[1],
        )

    # ---- 绘制 --------------------------------------------------------------

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt 定的名字
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        """先压暗框外，再画框、三分线与手柄。

        **压暗用四个矩形而不是整屏涂一层再「挖」回来**：挖要靠合成模式，
        那一路在高分屏上的边缘会出现一圈发亮的接缝（实测）。
        """
        width, height, left, top = _outline(self._rect)
        full = QRectF(0, 0, self.width(), self.height())
        shade = QColor(0, 0, 0, 150)
        for piece in (
            QRectF(full.left(), full.top(), full.width(), top - full.top()),
            QRectF(full.left(), top + height, full.width(), full.bottom() - top - height),
            QRectF(full.left(), top, left - full.left(), height),
            QRectF(left + width, top, full.right() - left - width, height),
        ):
            if piece.width() > 0 and piece.height() > 0:
                painter.fillRect(piece, shade)
        rect = QRectF(left, top, width, height)
        for fraction in RULE_OF_THIRDS:
            x = left + width * fraction
            y = top + height * fraction
            _line(painter, QPointF(x, top), QPointF(x, top + height))
            _line(painter, QPointF(left, y), QPointF(left + width, y))
        _stroke(painter, rect, _INNER, 2.0)
        _stroke(painter, rect, _OUTER, _BORDER)
        for _, fx, fy in _HANDLE_SPOTS:
            _stroke(
                painter,
                QRectF(
                    left + width * fx - _HANDLE_EDGE / 2,
                    top + height * fy - _HANDLE_EDGE / 2,
                    _HANDLE_EDGE,
                    _HANDLE_EDGE,
                ),
                _INNER,
                2.0,
            )

    # ---- 拖拽 --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 定的名字
        point = event.position()
        scene = self._to_scene(point)
        handle = handle_at(self._rect, scene.x(), scene.y())
        self._drag = handle
        self._origin = QPointF(scene.x(), scene.y())
        if handle is Handle.NEW:
            self._rect = rect_from_drag(self._bounds, scene.x(), scene.y(), scene.x(), scene.y())
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 定的名字
        if self._drag is None or self._origin is None:
            return
        scene = self._to_scene(event.position())
        if self._drag is Handle.MOVE:
            self._rect = move_rect(
                self._rect,
                self._bounds,
                scene.x() - self._origin.x(),
                scene.y() - self._origin.y(),
            )
        elif self._drag is Handle.NEW:
            self._rect = rect_from_drag(
                self._bounds, self._origin.x(), self._origin.y(), scene.x(), scene.y()
            )
        else:
            self._rect = resize_rect(self._rect, self._bounds, scene.x(), scene.y(), self._drag)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 定的名字
        self._drag = None
        self._origin = None

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt 定的名字
        """回车选定、Esc 放弃。**这里接住就走**，别让它们落到 ``DetailView`` 上 ——
        那边的 Esc 是「退出大图」，在裁一半时按一下会连照片一起退出去。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emit_rect()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        event.ignore()

    def event(self, event: QEvent) -> bool:
        """把 Esc 从窗口那一级的快捷键里抢回来（与 ``RenameEdit`` 同一个理由）。"""
        if event.type() == event.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    # ---- 内部 --------------------------------------------------------------

    def _emit_rect(self) -> None:
        """交出那个框。**收浮层由接收方负责**（见 ``MainWindow._on_crop_committed``）。

        这里再 ``finish`` 一次是防呆：万一这一层被单独用起来，没有接收方的话它会
        留在屏幕上（``finish`` 是幂等的，多调一次没有代价）。
        """
        self.committed.emit(*self._rect)
        self.finish()

    def _to_scene(self, point: QPointF) -> QPointF:
        """视口坐标 → 场景坐标（要经它所属的那个 ``QGraphicsView``）。"""
        view = self.parentWidget()
        while view is not None and not hasattr(view, "mapToScene"):
            view = view.parentWidget()
        if view is None:
            return QPointF(point)
        return view.mapToScene(QPoint(int(point.x()), int(point.y())))


def _stroke(painter: QPainter, rect: QRectF, color: QColor, width: float) -> None:
    pen = QPen(color)
    pen.setWidthF(width)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)


def _line(painter: QPainter, start: QPointF, end: QPointF) -> None:
    pen = QPen(QColor(255, 255, 255, 120))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.drawLine(start, end)


__all__ = [
    "HANDLE_HIT",
    "MIN_VIEW_EDGE",
    "CropOverlay",
    "Handle",
    "handle_at",
    "move_rect",
    "rect_from_drag",
    "resize_rect",
]
