"""控件那一圈 1 点宽的圆角轮廓 —— **两种画法，各自的适用场合只有一处**。

**为什么不用样式表的 ``border`` 配半透明颜色**：Qt 把那一圈描成「四段直边 + 四段圆弧」，
接缝处两半的抗锯齿**重叠**；半透明的颜色被调两遍就叠成**更深的点** —— 落在圆弧与直边相接的
四处与每段圆弧的 45°，正好是轮廓上的**八个等分点**（实测：轮廓中位 184、那八处 133，
而 255 × 0.72² = 132，正是同一层边框被调了两遍；Retina 上更明显）。

于是有两种躲开它的办法，**按「那个控件的 paintEvent 归不归我们管」选**：

1. :func:`draw_outline` —— 轮廓**自己填**：控件是我们自己的类（``paintEvent`` 里画）。
   输入面那一族走这条：搜索胶囊、切换按钮、改名的输入框。
2. **把边框颜色预先与底色合成成不透明的**（:func:`opaque_over`）—— 样式表照旧，只是那个颜色
   不再半透明：不透明的颜色被画两遍还是它自己，接缝处的重叠就**看不出来**。**只能用在
   样式表画的控件上**（候选列表那份浮层边框），而且要求紧挨着边框的那一层底色是已知的。

**带笔的 ``drawPath`` / ``drawRoundedRect`` 一样坏**：笔画的接缝照样重叠（实测交界 -31、
45° -19，比样式表还糟），所以「不用样式表、自己画」并不等于躲开 —— 得**填**。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath


def draw_outline(
    painter: QPainter, rect: QRectF, radius: float, color: QColor, width: float = 1.0
) -> None:
    """画那一圈 :paramref:`width` 点宽的圆角轮廓（第一种画法）。

    **外圆角矩形减内圆角矩形，一条路径 ``OddEven`` 填**：``QPainterPath`` 里放两个子路径，
    ``OddEven`` 的判据是「被奇数条子路径包住」，于是外框之内、内框之外那一圈正好被填上，
    中间那块被两条包住（偶数）而**空出来** —— 这就是「减」。整条路径只有一条边要栅格化，
    接缝处没有重叠可言。
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

    给样式表那份边框用（第二种画法）：把半透明的颜色预先合成掉，接缝处那两半画两遍也是同一个
    不透明的颜色，八个等分点就看不出加深了 —— 而且**外观与合成一次完全一样**（同一种颜色
    画两遍还是它自己）。

    :paramref:`background` **必须是紧挨着边框那一层的真实底色**：写错了不是「多八个点」，
    而是那一圈整体偏深或偏浅。

    注意 ``QPainter`` 也能做到这件事（``CompositionMode_SourceOver`` 就是这个函数），
    写成函数是为了让调用处一眼看出「这里在预先合成，不是在挑颜色」。
    """
    alpha = foreground.alphaF()
    mixed = QColor(background)
    mixed.setRedF(foreground.redF() * alpha + background.redF() * (1.0 - alpha))
    mixed.setGreenF(foreground.greenF() * alpha + background.greenF() * (1.0 - alpha))
    mixed.setBlueF(foreground.blueF() * alpha + background.blueF() * (1.0 - alpha))
    return mixed


__all__ = ["draw_outline", "opaque_over"]
