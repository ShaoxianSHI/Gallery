"""日历绘制用的小工具：字体、文字、圆角、照片 —— 照片一律按原比例缩放后居中。

**``paintEvent`` 里不许现造 ``QFont`` / ``QFontMetrics`` / ``QPen``**（这里按取值记账缓存）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from gallery.ui.calendar.layout import CARD_RADIUS, fitted_size

#: 三份缓存各自的上限（用到的取值只有个位数）；它只为挡住「拿算出来的数当键」那种用法。
_CACHE_LIMIT = 64

#: ``(字号, 字重)`` → 字体。**不随主题变化**：``QFont()`` 取的是应用默认字体，而本仓库从不调
#: ``QApplication.setFont``（换外观换的是调色板）；真变了走 :func:`reset_caches`。
_fonts: dict[tuple[int, int], QFont] = {}
#: 字体 → 度量。``QFont`` 在 PySide6 里按值可哈希，所以等值的字体落在同一格。
_metrics: dict[QFont, QFontMetrics] = {}
#: 颜色 → 画笔，见 :func:`_solid_pen`（键为什么是 ``id``，那里写清楚了）。
_pens: dict[int, tuple[QColor | str, QPen]] = {}


def begin(widget: QWidget, background: QColor | str) -> QPainter:
    """开一个新画笔：抗锯齿开、平滑缩放开、整块先铺上底色。

    日历包里有**两种**要画的东西 —— 四层视图与它们内部那些不是视图的部件，两者都这么开。
    """
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.fillRect(widget.rect(), QColor(background))
    return painter


def font_px(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """一个按**像素**定大小的字体（跟着格子走，写死了也不会在 2x 屏上偏小一档）。

    **同一个 ``(字号, 字重)`` 永远返回同一个对象：只许直接交给 :func:`draw_text`，不许就地改**
    —— PySide6 的 ``QFont`` 按值返回，改一次就把缓存里那一份一起改了，整轮字号跟着变。
    """
    key = (int(size), int(weight))
    font = _fonts.get(key)
    if font is None:
        if len(_fonts) >= _CACHE_LIMIT:
            _fonts.clear()
        font = QFont()
        font.setPixelSize(key[0])
        font.setWeight(weight)
        _fonts[key] = font
    return font


def _font_metrics(font: QFont) -> QFontMetrics:
    """按字体取度量。**不导出去**：``QFontMetrics`` 是可变的（``setWidth`` 之类），
    拿到一份去改就会把缓存里那一份一起改了 —— 它只在 :func:`draw_text` 里被读。"""
    metrics = _metrics.get(font)
    if metrics is None:
        if len(_metrics) >= _CACHE_LIMIT:
            _metrics.clear()
        metrics = QFontMetrics(font)
        _metrics[font] = metrics
    return metrics


def _solid_pen(color: QColor | str) -> QPen:
    """按颜色取一支实心画笔。同样**不导出去**：``QPen`` 是可变的。

    **键是 ``id`` 而不是颜色值**：``QColor`` 不可哈希，按 ``name()`` 拼字符串当键，那一次格式化
    就要 0.3 µs，把「造一支笔」省下的又花回去。值里存着颜色对象本身、取用时再比一次 ``is``，
    所以 ``id`` 被复用也不会拿错笔；换主题时 ``derive`` 造的是新对象，自然落到新键上。
    """
    key = id(color)
    entry = _pens.get(key)
    if entry is not None and entry[0] is color:
        return entry[1]
    if len(_pens) >= _CACHE_LIMIT:
        _pens.clear()
    pen = QPen(QColor(color))
    _pens[key] = (color, pen)
    return pen


def reset_caches() -> None:
    """把三份缓存清空 —— **只在应用默认字体真的变了时用**（本仓库从不调 ``setFont``）。"""
    _fonts.clear()
    _metrics.clear()
    _pens.clear()


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
    painter.setPen(_solid_pen(color))
    shown = text
    if elide:
        shown = _font_metrics(font).elidedText(
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
    """把一张照片按**原比例**缩放后放进 ``rect`` 并居中，四个角裁圆（没有图就铺一块浅色）。

    **用 clip 裁圆角，不拿蒙版抠**：后者每画一格都要多产出一张同尺寸的位图，慢四倍且边有锯齿。
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


def paint_cell_photo(
    painter: QPainter,
    rect: QRectF,
    image: QImage | None,
    *,
    count: int,
    chip: QColor | str,
    text: QColor | str,
    radius: float = CARD_RADIUS,
    chip_font_px: int = 10,
) -> None:
    """日历里的一格：一张圆角照片，不止一张时右下角写「N 张」。

    **月 / 周两层共用这一份**：不压渐变、不写标题（「这一格是几点」左窄条已经写着）；
    张数写「N 张」是因为看的人要问「一共几张」，不是「除了这张还有几张」。

    **年视图不走它** —— 那一层的格子只有二十几像素，走 :func:`paint_photo` 再自己叠一个
    日期徽标。原先这里写着「年 / 月 / 周三层共用」，读的人会以为年视图也调它。
    """
    paint_photo(painter, rect, image, radius=radius)
    if count <= 1:
        return
    badge = QRectF(rect.right() - 34.0, rect.bottom() - 18.0, 30.0, 14.0)
    fill_rounded(painter, badge, chip, 7)
    draw_text(
        painter,
        badge,
        f"{count} 张",
        font_px(chip_font_px),
        text,
        Qt.AlignmentFlag.AlignCenter,
        elide=False,
    )


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
    "paint_cell_photo",
    "paint_photo",
    "reset_caches",
]
