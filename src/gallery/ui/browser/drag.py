"""拖动时跟着光标走的那张图。

它要回答「我正拿着什么」，所以画的是**整张卡片**（缩略图在上、文件名在下），多选时叠成
一小摞 —— 只画一块缩略图的话，多选与单选看起来一模一样（ADR 0062 决策 3）。
取景算术全部复用委托那几个方法，卡片的形状因此只有一处定义；本模块只碰像素。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPixmap

from gallery.ui.browser.delegate import PhotoDelegate

#: 拖动位图里卡片的长边。它与网格的档位无关 —— 拖动图是固定尺寸的，不跟着换档缩放。
DRAG_EDGE = 128

#: 多选时叠起来的那几层：层数、每层错开的像素、以及叠出来的那点斜角。
STACK_LAYERS = 3
STACK_STEP = 6
STACK_SKEW = 2


def card_size(edge: int = DRAG_EDGE) -> QSize:
    """一张卡片占多大（含文件名那一行与内边距，与网格里的格子同一套算术）。"""
    return QSize(
        edge + PhotoDelegate.PADDING * 2,
        edge + PhotoDelegate.PADDING * 2 + PhotoDelegate.LABEL_HEIGHT,
    )


def drag_pixmap(
    pixmap: QPixmap | None,
    label: str,
    palette: QPalette,
    *,
    edge: int = DRAG_EDGE,
    count: int = 1,
) -> QPixmap:
    """画一张（或一小摞）卡片。

    :param count: 拖了几项 —— 大于 1 时在后面叠出另外几层，让「多选」在拖动位图上可见。
    """
    card = card_size(edge)
    layers = min(max(count, 1), STACK_LAYERS)
    # 位图要比卡片大出那几层错开的位置，否则最上面那层会被裁掉。
    canvas = QSize(
        card.width() + (layers - 1) * STACK_STEP + (layers - 1) * STACK_SKEW,
        card.height() + (layers - 1) * STACK_STEP,
    )
    result = QPixmap(canvas)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # 从最底下那层画到最上面：先画的那几层于是被后画的压住一角，像一摞纸。
        for layer in reversed(range(layers)):
            offset = layer * STACK_STEP
            origin = QRect(offset + layer * STACK_SKEW, offset, card.width(), card.height())
            _draw_card(painter, origin, pixmap, label, palette, edge, dimmed=layer < layers - 1)
    finally:
        painter.end()
    return result


def _draw_card(
    painter: QPainter,
    rect: QRect,
    pixmap: QPixmap | None,
    label: str,
    palette: QPalette,
    edge: int,
    *,
    dimmed: bool,
) -> None:
    """一张卡片：底板 + 缩略图 + 文件名。形状与 :mod:`delegate` 一致。"""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_card_background(palette, dimmed))
    painter.drawRoundedRect(rect, PhotoDelegate.RADIUS, PhotoDelegate.RADIUS)

    image_rect = QRect(
        rect.left() + PhotoDelegate.PADDING,
        rect.top() + PhotoDelegate.PADDING,
        edge,
        edge,
    )
    if pixmap is not None and not pixmap.isNull():
        target = _fitted(pixmap.size(), image_rect.size())
        target.moveLeft(image_rect.center().x() - target.width() // 2)
        target.moveTop(image_rect.center().y() - target.height() // 2)
        painter.drawPixmap(target, pixmap)
    else:
        painter.setBrush(palette.color(QPalette.ColorRole.AlternateBase))
        painter.drawRoundedRect(image_rect, PhotoDelegate.THUMB_RADIUS, PhotoDelegate.THUMB_RADIUS)

    font = QFont(painter.font())
    font.setPixelSize(max(9, edge // 8))
    painter.setFont(font)
    painter.setPen(palette.color(QPalette.ColorRole.WindowText))
    metrics = QFontMetrics(font)
    label_rect = QRect(
        rect.left() + PhotoDelegate.PADDING,
        rect.bottom() - PhotoDelegate.LABEL_HEIGHT,
        rect.width() - PhotoDelegate.PADDING * 2,
        PhotoDelegate.LABEL_HEIGHT,
    )
    painter.drawText(
        label_rect,
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
        metrics.elidedText(label, Qt.TextElideMode.ElideMiddle, label_rect.width()),
    )


def _card_background(palette: QPalette, dimmed: bool) -> QColor:
    """底板：底下那几层压暗一点，叠起来才看得出是几层。"""
    color = QColor(palette.color(QPalette.ColorRole.Base))
    if dimmed:
        color.setAlpha(160)
    return color


def _fitted(source: QSize, target: QSize) -> QRect:
    """原比例缩到能放进 ``target`` 后占多大（与 :meth:`PhotoDelegate.target_rect` 同一套算术）。"""
    if source.isEmpty() or target.isEmpty():
        return QRect(QPoint(0, 0), QSize())
    scale = min(target.width() / source.width(), target.height() / source.height())
    width = max(1, round(source.width() * scale))
    height = max(1, round(source.height() * scale))
    return QRect(QPoint(0, 0), QSize(width, height))


__all__ = [
    "DRAG_EDGE",
    "card_size",
    "drag_pixmap",
]
