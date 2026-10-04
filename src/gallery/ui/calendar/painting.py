"""日历绘制用的小工具：字体、文字、圆角、照片。

四个视图画的东西差别很大，但「怎么把一行字画在一个矩形里」「怎么把一张照片按原比例
放进一个格子里」这几件事完全一样 —— 与其每处复刻一遍（复刻的那份迟早会走样），
不如收在这里，日历包内外都能用。

**照片一律按原比例缩放后居中放进去**（:func:`paint_photo`），与网格视图
:meth:`gallery.ui.browser.grid.Cell.target_rect` 同一手法：格子比例是定的，
照片不裁、不拉。

## 字体、度量与画笔都记一份

**``paintEvent`` 里不许现造 ``QFont`` / ``QFontMetrics`` / ``QPen``。**
年视图一帧要写 612 处文字（12 张小月 × (2 个标题 + 7 个表头 + 42 个日期格)），
而现造一次 ``QFont`` + ``QFontMetrics`` + ``QPen(QColor(...))`` 各是几百纳秒 ——
乘上 612 就是每帧好几毫秒，而这三种东西整轮里其实**只有个位数的取值**。
于是这里按 ``(字号, 字重)`` 记字体、按字体记度量、按颜色值记画笔。

**这些缓存不随主题变化**：``QFont()`` 取的是**应用默认字体**，而本仓库从头到尾
没有调过 ``QApplication.setFont`` —— 浅色 / 深色换的是调色板
（见 :func:`gallery.ui.theme.apply_theme`），系统默认字体在整个进程里不变。
真变了的话，:func:`reset_caches` 是留给那个场景的开关。

**只在这条路上用**：三份缓存都由 ``paintEvent`` 读，而那永远在界面线程上。

**缓存里的东西一律只读**：``QFont`` / ``QFontMetrics`` / ``QPen`` 三样都是可变的，
而 PySide6 按值返回它们 —— 拿到一份就地改，改掉的就是缓存里那一份。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from gallery.ui.calendar.layout import CARD_RADIUS, fitted_size

#: 三份缓存各自的上限。真正用到的取值只有个位数（``year_view`` 里那几个常量），
#: 上限只为挡住「拿算出来的数当键」那种用法：年视图的日期徽标字号是按格子大小算的，
#: 拖一次窗口就换一档。它是兜底，不是预期路径。
_CACHE_LIMIT = 64

#: ``(字号, 字重)`` → 字体。
_fonts: dict[tuple[int, int], QFont] = {}
#: 字体 → 度量。``QFont`` 在 PySide6 里按值可哈希，所以等值的字体落在同一格。
_metrics: dict[QFont, QFontMetrics] = {}
#: 颜色 → 画笔，见 :func:`_solid_pen`（键为什么是 ``id``，那里写清楚了）。
_pens: dict[int, tuple[QColor | str, QPen]] = {}


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

    **同一个 ``(字号, 字重)`` 永远返回同一个对象**（见模块文档里那份缓存）。
    代价是**这份对象是共享的：只许直接交给 :func:`draw_text`，不许就地改**。
    PySide6 的 ``QFont`` 是按值返回的（不像 C++ 那边隐式共享），所以就地改一次
    就会把缓存里那一份一起改掉，整轮的字号都跟着变。真要改就 ``QFont(font_px(n))``
    拷一份 —— ``draw_text`` 自己就是那么干的（``QPainter.setFont`` 拷的是值）。
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

    **键为什么是 ``id`` 而不是颜色值**：``QColor`` 在 PySide6 里不可哈希，退而求其次
    按 ``name(HexArgb)`` 拼字符串当键，那次格式化本身就要 0.3 µs —— 恰好把
    「造一支笔」省下的时间又花回去了一大半。而传进来的颜色**是稳定的几个对象**：
    :class:`~gallery.ui.calendar.colors.CalendarColors` 在每次换主题时算一遍，
    之后每帧都是同一批 ``QColor``，于是按对象身份记就够。

    **这样记为什么安全**：值里存着那个颜色对象**本身**（于是它的 ``id`` 不会被回收后
    复用给别人），取用时再比一次 ``is`` —— 就算真撞上同一个 ``id``，对不上就重造一支。
    浅色 / 深色换色时 :func:`~gallery.ui.calendar.colors.CalendarColors.derive`
    造的是**新对象**，自然落到新键上，不会拿到旧颜色那支笔。
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
    """把三份缓存清空 —— **只在应用默认字体真的变了时用**。

    本仓库没有调过 ``QApplication.setFont``，所以正常路径上一次都不该调它；
    留着这个开关是为了「哪天真的变了」时不必再去改这三处的键。
    """
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

    **三层（年 / 月 / 周）共用这一份**，不是各画一遍：原先周视图在照片下面压一道
    黑色渐变再写标题字，于是那一格比同一套界面的另两层多一块颜色，而「这一格是
    几点」左窄条本来就写着 —— 渐变是在给一个不需要的标签垫底。

    张数写成「N 张」而不是「还有 N 张」：整格里只有一张照片，看的人要问的是
    「这里一共几张」，不是「除了这张还有几张」。
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
