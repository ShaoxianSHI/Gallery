"""控件那一圈 1 点宽的圆角轮廓 —— **两种画法，各自的适用场合只有一处**。

**不要用样式表的 ``border`` 配半透明颜色**：Qt 把那一圈描成「四段直边 + 四段圆弧」，接缝处
两半的抗锯齿**重叠**，半透明色被调两遍就叠成更深的点。躲开它的办法按「那个控件的 ``paintEvent``
归不归我们管」选：归我们管就 :func:`draw_outline` **填**出这一圈；样式表画的控件（候选列表那份
浮层边框）用 :func:`opaque_over` 把颜色预先与已知底色合成成不透明的。带笔的描边一样坏。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath


def draw_outline(
    painter: QPainter, rect: QRectF, radius: float, color: QColor, width: float = 1.0
) -> None:
    """画那一圈 :paramref:`width` 点宽的圆角轮廓（第一种画法）。

    **外圆角矩形减内圆角矩形，一条路径 ``OddEven`` 填**：外框之内、内框之外那一圈被奇数条子路径
    包住而填上，中间那块被两条包住（偶数）而空出来。整条路径只有一条边要栅格化，接缝无重叠。
    """
    outer = QRectF(rect)
    inner = outer.adjusted(width, width, -width, -width)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addRoundedRect(outer, radius, radius)
    path.addRoundedRect(inner, max(0.0, radius - width), max(0.0, radius - width))
    painter.fillPath(path, color)


def opaque_over(foreground: QColor, background: QColor) -> QColor:
    """:paramref:`foreground` 压在 :paramref:`background` 上之后**该有的那个不透明颜色**。

    给样式表那份边框用（第二种画法）：预先合成掉半透明，接缝处那两半画两遍也是同一个不透明的
    颜色。:paramref:`background` **必须是紧挨着边框那一层的真实底色** —— 写错了那一圈会整体
    偏深或偏浅。``QPainter`` 的 ``CompositionMode_SourceOver`` 就是这个函数。
    """
    alpha = foreground.alphaF()
    mixed = QColor(background)
    mixed.setRedF(foreground.redF() * alpha + background.redF() * (1.0 - alpha))
    mixed.setGreenF(foreground.greenF() * alpha + background.greenF() * (1.0 - alpha))
    mixed.setBlueF(foreground.blueF() * alpha + background.blueF() * (1.0 - alpha))
    return mixed


__all__ = ["draw_outline", "opaque_over"]
