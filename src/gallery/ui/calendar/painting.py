"""日历绘制用的小工具：字体、文字、圆角、照片。

四个视图画的东西差别很大，但「怎么把一行字画在一个矩形里」「怎么把一张照片按原比例
放进一个格子里」这几件事完全一样 —— 与其每处复刻一遍（复刻的那份迟早会走样），
不如收在这里，日历包内外都能用。

**照片一律按原比例缩放后居中放进去**（:func:`paint_photo`），与网格视图
:meth:`gallery.ui.browser.grid.Cell.target_rect` 同一手法：格子比例是定的，
照片不裁、不拉。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from gallery.ui.calendar.layout import CARD_RADIUS, fitted_size


def begin(widget: QWidget, background: QColor | str) -> QPainter:
    """开一个新画笔：抗锯齿开、平滑缩放开、整块先铺上底色。

    日历包里有**两种**要画的东西 —— 四层视图与它们内部那些不是视图的部件
    （表头、时间轴本体），两者都要这么开一次，所以收在这里。
    """
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.fillRect(widget.rect(), QColor(background))
    return painter


def font_px(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """一个按**像素**定大小的字体。

    日历要的是「一个格子多大、文字就多大」那种跟着格子走的关系，而 ``QLabel``
    那种按点定大小的字体跟着屏幕缩放换算，写死了会在 2x 屏上偏小一档。
    """
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def draw_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    font: QFont,
    color: QColor | str,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    *,
    elide: bool = True,
) -> None:
    """在 ``rect`` 里画一行字；``elide`` 为真时按框宽截断并补省略号。"""
    painter.setFont(font)
    painter.setPen(QPen(QColor(color)))
    shown = text
    if elide:
        shown = QFontMetrics(font).elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, int(rect.width()))
        )
    painter.drawText(rect, align, shown)


def fill_rounded(painter: QPainter, rect: QRectF, color: QColor | str, radius: float) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(rect, radius, radius)


def outline_rounded(
    painter: QPainter,
    rect: QRectF,
    color: QColor | str,
    width: float = 1.0,
    radius: float = CARD_RADIUS,
) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(
        rect.adjusted(width / 2, width / 2, -width / 2, -width / 2), radius, radius
    )


def paint_photo(
    painter: QPainter,
    rect: QRectF,
    image: QImage | None,
    *,
    radius: float = CARD_RADIUS,
    placeholder: QColor | str = "#cfd6dd",
) -> None:
    """把一张照片按**原比例**缩放后放进 ``rect`` 并居中，四个角裁圆。

    没有图就铺一块浅色：占位不是破绽，是「这里确实有一张、只是还没读出来」。

    **用 clip 裁圆角，不拿蒙版抠。** 与 ``browser/grid.py`` 的 ``_draw_thumbnail``
    同一个理由：后者每画一格都要额外产出一张同尺寸的位图，实测慢四倍且边是锯齿的。
    """
    painter.save()
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    if image is None or image.isNull():
        painter.fillRect(rect, QColor(placeholder))
    else:
        width, height = fitted_size(image.width(), image.height(), rect.width(), rect.height())
        target = QRectF(
            rect.left() + (rect.width() - width) / 2,
            rect.top() + (rect.height() - height) / 2,
            width,
            height,
        )
        painter.drawImage(target, image)
    painter.restore()


def scrim(painter: QPainter, rect: QRectF, color: QColor | str, strength: float = 0.6) -> None:
    """在 ``rect`` 底部压一道竖直渐变，压深 —— 标签才在深浅照片上都读得出来。

    一格一步地填（而不是一次 ``QLinearGradient``）是为了能落在已有的 ``clip`` 上：
    照片已经用圆角裁过了，渐变必须跟着裁，否则会在四角漏出去。
    """
    painter.save()
    painter.setClipRect(rect)
    height = min(rect.height() * 0.7, 40.0)
    top = rect.bottom() - height
    for step in range(int(height)):
        ratio = step / max(1.0, height - 1)
        shade = QColor(color)
        shade.setAlpha(round(shade.alpha() * strength * ratio * ratio))
        painter.fillRect(QRectF(rect.left(), top + step, rect.width(), 1.4), shade)
    painter.restore()


def dot(painter: QPainter, center: QPointF, radius: float, color: QColor | str) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(center, radius, radius)


__all__ = [
    "begin",
    "dot",
    "draw_text",
    "fill_rounded",
    "font_px",
    "outline_rounded",
    "paint_photo",
    "scrim",
]
