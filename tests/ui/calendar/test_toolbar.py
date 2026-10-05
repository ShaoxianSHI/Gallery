"""日历工具条：换外观之后那一行的字色跟没跟过去。

工具条带着样式表，于是 ``QStyleSheetStyle`` 给「工具条连同它的子控件」这一支塞了
一份**自己的**调色板 —— :func:`gallery.ui.theme.apply_theme` 下发的调色板到不了它们。
而页面是在 ``apply_theme`` **之后**才重新铺样式表的，于是每次换外观它都在替这一支
**照上一次那份调色板**重画一遍。

**原先那一行只有按钮的字色写在样式表里，标题那个 QLabel 没写**（规则还错写成
``QPushButton#title``，从加进来那天起就没匹配上过），于是症状是：用户在深色下用着
Gallery，把系统切到浅色 —— 别处都白了，只有标题那一行整行看不见。用户原话
「浅色模式下……这些文字还是浅色，看不到了」。

两个要紧的地方，都是这条 bug 教出来的：

- **起手的外观必须先定下来，页面才拿得到对的那一份。** 这一支的调色板是在**建控件
  那一刻**取的；测试要是拿当前（浅色）那份建页面，再切深、再切浅，那么「切到浅色」
  那一轮里这一支本来就是浅色的那份 —— 症状压根不出现，用例一路绿。
- **量画出来的像素，不量 ``label.palette()``**：出问题时两者是**不一致的**
  （``palette()`` 已经是新那一档，画的却还是旧那一档），量调色板会一路绿。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterator

import pytest

from gallery.ui.calendar import CalendarPage

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

TODAY = datetime.date(2024, 5, 16)

#: macOS 深色那一档（``ui.theme`` 模块文档里量过的那两个系统色）。
DARK_WINDOW = QColor(0x32, 0x32, 0x32)
DARK_BASE = QColor(0x17, 0x17, 0x17)
#: 浅色：窗口那层 ``#ececec``，内容那层白。
LIGHT_WINDOW = QColor(0xEC, 0xEC, 0xEC)
LIGHT_BASE = QColor(0xFF, 0xFF, 0xFF)

#: 标题是 22px 的**大字**，WCAG 对大字（≥24px 常规 / ≥18.66px 粗体）要的是 3:1。
#: 取最低那一档而不是 AA 正文的 4.5:1：这一行量的是「跟没跟过去」，深浅两套配色
#: 真实都在 12:1 上下，真出问题时它掉到 1.0:1 那一头，离阈值远得很。
MIN_CONTRAST = 3.0


def _switch(qt_app: QApplication, *, window: QColor, base: QColor) -> None:
    """模拟系统换外观 —— 平台就是这么把新调色板写进应用的。

    **只 ``setPalette`` + 转事件循环，不自己调 ``page._on_theme_changed``**：换外观
    那一下平台发的是 ``paletteChanged``，``ThemeWatcher`` 接到之后排一个 0ms 定时器，
    等这一批事件走完才下发底色并发 ``changed`` —— 页面是被那个信号带上的。
    （``apply_theme`` 在前、页面重铺样式表在后，**这个先后就是那个 bug 的成因**。）
    手工调那两个方法等于把顺序抄了一遍，抄错了与没测一样。
    """
    palette = QPalette(qt_app.palette())
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, base)
    dark = base.lightnessF() < 0.5
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(role, QColor(0xEC, 0xEC, 0xEE) if dark else QColor(0x1A, 0x1A, 0x1A))
    qt_app.setPalette(palette)
    qt_app.processEvents()


@pytest.fixture
def page_in(qt_app: QApplication) -> Iterator[Callable[..., CalendarPage]]:
    """按「起手是什么外观」建日历那一页 —— 外观必须**先定下来**。跑完还原。"""
    original = QPalette(qt_app.palette())

    def _make(*, window: QColor, base: QColor) -> CalendarPage:
        _switch(qt_app, window=window, base=base)
        page = CalendarPage()
        page.set_today(TODAY)
        page.go_today()
        page.resize(1100, 700)
        page.show()
        qt_app.processEvents()
        return page

    yield _make
    qt_app.setPalette(original)
    qt_app.processEvents()


def _luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def _contrast(first: QColor, second: QColor) -> float:
    brighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (brighter + 5) / (darker + 5)


def _title_contrast(page: CalendarPage) -> tuple[float, str]:
    """工具条上**标题那一块**的字与底的对比度，附一句给人看的说明。

    取的是**整页**截图里标题 ``QLabel`` 覆盖的那一块：那一块里除了字就是底色，
    所以**最深的一端是字、铺得最多的一端是底**。

    **量整页，不单独 ``bar.grab()``**：工具条自己不铺底色（它是透的，底下是整页
    那一层），单独抓它会拿 ``QStyleSheetStyle`` 给这一支塞的那份调色板把底铺上 ——
    而那一份正是出问题的那一份，于是量出来的是「工具条自己以为的底色」，不是屏幕上
    那一块（浅色下它仍是深色那一档的底，量出 5.6:1 这种看着还行、其实量错了的数）。
    """
    label = page._bar._title
    image: QImage = page.grab().toImage()
    left, top = label.mapTo(page, label.rect().topLeft()).toTuple()
    pixels = [
        image.pixelColor(left + dx, top + dy)
        for dx in range(label.width())
        for dy in range(label.height())
    ]
    ink = min(pixels, key=lambda color: _luminance(color))
    background = max(pixels, key=lambda color: _luminance(color))
    return _contrast(ink, background), f"字 {ink.name()} 压在底 {background.name()} 上"


def test_dark_to_light_keeps_the_title_readable(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """深色起手 → 切浅色：标题那一行不许停在深色那一档（用户报的那一条）。"""
    page = page_in(window=DARK_WINDOW, base=DARK_BASE)
    _switch(qt_app, window=LIGHT_WINDOW, base=LIGHT_BASE)

    contrast, described = _title_contrast(page)
    assert contrast >= MIN_CONTRAST, (
        f"切到浅色之后标题只有 {contrast:.2f}:1（{described}）—— 浅底上的白字看不见。"
        "工具条带着样式表，下发的调色板到不了这一支，字色必须写在样式表里"
    )


def test_light_to_dark_keeps_the_title_readable(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """浅色起手 → 切深色：反过来也不许停在浅色那一档（同一个毛病的另一头）。"""
    page = page_in(window=LIGHT_WINDOW, base=LIGHT_BASE)
    _switch(qt_app, window=DARK_WINDOW, base=DARK_BASE)

    contrast, described = _title_contrast(page)
    assert contrast >= MIN_CONTRAST, (
        f"切到深色之后标题只有 {contrast:.2f}:1（{described}）—— 深底上的深字看不见"
    )


def test_the_title_keeps_the_default_font_size(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """标题是**系统默认那一档字号**，样式表不许碰它。

    那条 ``font-size: 22px`` 的规则写的是 ``QPushButton#title``，而标题是个
    ``QLabel`` —— 从加进来那天起就没匹配上过。也就是说「22px 的标题」**从来没
    存在过**，标题一直就是默认那一档。修字色的时候把它一并对上，看着是顺手，
    实际上是把字号放大了一号（用户原话「字体变大了，调回原本的字号」）。

    量的是「标签的字体与应用默认字体相等」，而不是某个具体数字 —— 默认字号跟平台
    与系统设置走，写死一个数只会在换机器时红。
    """
    page = page_in(window=LIGHT_WINDOW, base=LIGHT_BASE)
    label = page._bar._title

    assert label.font() == qt_app.font(), (
        f"标题是 {label.font().pointSize()}pt，应用默认是 {qt_app.font().pointSize()}pt ——"
        " 样式表不该给标题改字号（那条 22px 是按钮时代的遗留，从没生效过）"
    )
    rule = page._bar.styleSheet().split("QLabel#title")[1].split("}")[0]
    assert "font-size" not in rule, f"标题那条规则里不该出现 font-size，实际是：{rule.strip()}"
