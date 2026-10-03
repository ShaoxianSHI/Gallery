"""编辑那四个图标：**现画的**，不往资源里塞图（与 ``ui.search_field`` 的放大镜同一做法）。

一个圆弧加一支箭头、一个竖虚线加两个三角、两条直角折线 —— 换成图标文件就要多一份
要打包、要适配深浅两档的资源，而它们永远是同一个形状。

**坐标一律用逻辑像素**，只有画布尺寸乘上 :paramref:`ratio`。``QPainter`` 会按设备像素比
缩放它收到的那套坐标，于是拿设备像素画上去，在 Retina 上就是放大了一倍。

笔宽、端点与拐角都照放大镜那一档（1.4 点、圆头圆角），否则四个图标摆在一行里会显得
一个比一个重。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Final

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

#: 图标画布的边长（点）。比按钮的命中区小一圈 —— 那正是它看着「不挤」的原因。
ICON_EDGE: Final = 15

#: 笔宽。
_STROKE: Final = 1.4

#: 旋转图标里那段圆弧跨多少度（缺口是它的补）。
_ARC_SPAN: Final = 290.0

#: 圆弧拆成多少段。60 段在 15 点的画布上肉眼是光滑的，再多只是白费。
_ARC_STEPS: Final = 60

#: 箭头两片刃张开的角度，以及箭尖伸出圆弧之外的那一点（角度 / 点）。
_HEAD_SPREAD: Final = 26.0
_HEAD_REACH: Final = 1.8

#: 顺时针那条弧从哪个角度起扫。角度从三点钟方向起、**逆时针为正**，所以顺时针是往回扫。
#:
#: 这个数是量出来的：缺口落在右上、箭头停在正上方偏右并朝右 —— 与系统图标同一位置。
#: 等价写法是「90° + 90° − 缺口的一半」，写死这个数加一行注释更好读。
_CLOCKWISE_START: Final = 35.0

#: 逆时针那条与它互为镜像（起点关于竖直轴对称、扫向相反），两个图标因此只差这一行。
_COUNTERCLOCKWISE_START: Final = 180.0 - _CLOCKWISE_START


def _canvas(ratio: float) -> QPixmap:
    """一块 ``ICON_EDGE * ratio`` 见方的**空白画布**（不预设设备像素比）。

    **设备像素比是画完之后再贴上去的**，而不是建的时候 —— 因为 ``QPainter`` 会不会
    替你按它建好变换并不是可以想当然的事（实测：在带 DPR 的画布上，同一份代码
    ``drawLine`` 照画、``drawArc`` 却整段不画）。于是这里干脆什么都不假设：
    画布按设备像素开，坐标系由 :func:`_painter` 显式缩放，标签画完再贴。
    """
    pixmap = QPixmap(round(ICON_EDGE * ratio), round(ICON_EDGE * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def _painter(pixmap: QPixmap, color: QColor) -> QPainter:
    """在画布上起一个画笔，**坐标系是逻辑像素**（缩放显式做一次）。"""
    painter = QPainter(pixmap)
    painter.scale(pixmap.width() / ICON_EDGE, pixmap.height() / ICON_EDGE)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(_STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    return painter


def _draw(pixmap: QPixmap, color: QColor, draw: Callable[[QPainter], None]) -> QPixmap:
    painter = _painter(pixmap, color)
    try:
        draw(painter)
    finally:
        painter.end()  # 不关的话画布析构前一直占着设备资源
    # 画完之后才贴设备像素比：控件按它把画布缩回逻辑尺寸，于是 Retina 上是满分辨率的。
    pixmap.setDevicePixelRatio(pixmap.width() / ICON_EDGE)
    return pixmap


def _arc_with_head(painter: QPainter, *, clockwise: bool) -> None:
    """一段 270° 的圆弧 + 缺口处的一支箭头 —— 旋转那两个图标的全部内容。

    **圆弧是逐段连出来的，不用 ``drawArc``。** 实测那条路在当前的绘制路径下**整段不出**：
    同样的坐标、同样粗的笔，``drawLine`` 照画而 ``drawArc`` 什么都没有 —— 而图标少一段
    弧看起来就是「那个按钮坏了」。逐段连的代价是几十次 ``drawLine``（可忽略），
    换来的是在任何绘制路径下都画得出来。

    **顺逆时针只差把弧的起点与箭头的朝向反过来**，因此两个图标共用这一段：各画一次的
    话，它们迟早会差几度的弧度，而用户一眼就能看出来。
    """
    center = QPointF(ICON_EDGE / 2.0, ICON_EDGE / 2.0)
    radius = (ICON_EDGE - 4.0) / 2.0
    start = _CLOCKWISE_START if clockwise else _COUNTERCLOCKWISE_START
    sweep = -_ARC_SPAN if clockwise else _ARC_SPAN
    points = [
        _on_circle(center, radius, start + sweep * step / _ARC_STEPS)
        for step in range(_ARC_STEPS + 1)
    ]
    for first, second in zip(points, points[1:], strict=False):
        painter.drawLine(first, second)
    # 箭头长在**转过去的那一端**：顺时针取终点、逆时针取起点。
    head = start + sweep
    tip = points[-1]
    direction = -1.0 if clockwise else 1.0  # 沿弧前进的方向（角度逆时针为正）
    for offset in (_HEAD_SPREAD, -_HEAD_SPREAD):
        painter.drawLine(tip, _on_circle(center, radius, head + direction * offset))
    painter.drawLine(tip, _on_circle(center, radius + _HEAD_REACH, head + direction * 1.9))


def _on_circle(center: QPointF, radius: float, degrees: float) -> QPointF:
    """圆周上按角度取点。角度从三点钟方向起、逆时针为正 —— 与 ``QPainter.drawArc`` 同义。"""
    radians = math.radians(degrees)
    return QPointF(center.x() + radius * math.cos(radians), center.y() - radius * math.sin(radians))


def rotate_left(color: QColor, ratio: float) -> QPixmap:
    """逆时针转 90°。"""
    return _draw(_canvas(ratio), color, lambda painter: _arc_with_head(painter, clockwise=False))


def rotate_right(color: QColor, ratio: float) -> QPixmap:
    """顺时针转 90°。"""
    return _draw(_canvas(ratio), color, lambda painter: _arc_with_head(painter, clockwise=True))


def mirror(color: QColor, ratio: float) -> QPixmap:
    """左右镜像：中间一道虚线，两侧两个背对的三角。"""

    def draw(painter: QPainter) -> None:
        pen = painter.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([1.2, 1.6])
        painter.setPen(pen)
        middle = ICON_EDGE / 2.0
        painter.drawLine(QPointF(middle, 1.5), QPointF(middle, ICON_EDGE - 1.5))
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        top, bottom = 2.6, ICON_EDGE - 2.6
        gap = 2.0
        painter.drawLine(QPointF(2.6, middle), QPointF(middle - gap, top))
        painter.drawLine(QPointF(2.6, middle), QPointF(middle - gap, bottom))
        painter.drawLine(QPointF(ICON_EDGE - 2.6, middle), QPointF(middle + gap, top))
        painter.drawLine(QPointF(ICON_EDGE - 2.6, middle), QPointF(middle + gap, bottom))

    return _draw(_canvas(ratio), color, draw)


def crop(color: QColor, ratio: float) -> QPixmap:
    """裁剪：两条对角直角折线，像从画面上切下一块的那种角标。"""

    def draw(painter: QPainter) -> None:
        near = 5.0
        painter.drawLine(QPointF(1.2, near), QPointF(near, near))
        painter.drawLine(QPointF(near, near), QPointF(near, 1.2))
        far = ICON_EDGE - near
        painter.drawLine(QPointF(ICON_EDGE - 1.2, far), QPointF(far, far))
        painter.drawLine(QPointF(far, far), QPointF(far, ICON_EDGE - 1.2))

    return _draw(_canvas(ratio), color, draw)


#: 图标名字 → 画法。工具条与菜单共用这一份映射，于是「哪枚图标长什么样」只有一处。
GLYPHS: Final[dict[str, Callable[[QColor, float], QPixmap]]] = {
    "crop": crop,
    "mirror": mirror,
    "rotate_left": rotate_left,
    "rotate_right": rotate_right,
}


def glyph(name: str, color: QColor, ratio: float = 1.0) -> QPixmap:
    """按名字画一个图标。

    :param ratio: 设备像素比（``QWidget.devicePixelRatioF()``），由调用方给 —— 这一层
        不持有控件，也不该为了问一个数而收一个 ``QWidget`` 进来。
    :return: 未知名字给一张空图，而不是抛异常 —— 界面不该为一枚图标崩掉。
    """
    draw = GLYPHS.get(name)
    if draw is None:
        return QPixmap()
    return draw(color, max(ratio, 1.0))


__all__ = [
    "GLYPHS",
    "ICON_EDGE",
    "crop",
    "glyph",
    "mirror",
    "rotate_left",
    "rotate_right",
]
