"""滚动条长什么样：**没有轨道，只剩滑块**。

网格页最右缘、日历周视图的时间轴、大图页的元数据面板 —— 三条滚动条分属三个包，
用户在它们之间来回走，各走各的样式早晚差一档，所以颜色与尺寸只从这一处解析
（与 :mod:`gallery.ui.menu_style` 同一个理由）。

**为什么不给原生那一份**：原生滚动条常驻一条实心轨道，颜色由系统当前外观决定，
与本应用统一下发的那份底色（:mod:`gallery.ui.theme`）对不上 —— 深色下是一条更深的
灰底竖条，看起来像内容上压了一块东西。这里把轨道（``::add-page`` / ``::sub-page``）
与两端的按钮（``::add-line`` / ``::sub-line``）都拿掉，只留一条按主题着色的滑块。

**别用 ``QApplication.setStyleSheet`` 一把梭**：那会让每个控件都改走
``QStyleSheetStyle``，macOS 原生绘制随之失效（本仓库已经吃过一次亏，见
:mod:`gallery.ui.menu_style` 的模块文档）。要哪条滚动条就往哪条上下发。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QScrollBar

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 滑块左右各让开多少（点）：留一条与底色齐平的窄边，滑块才像「浮在内容上」而不是
#: 「贴在边上」。
HANDLE_SIDE_INSET = 2

#: 滑块最短多少像素（点）。内容只比视口高一点点时，Qt 会把滑块压成一条几乎看不见的线，
#: 而那正是用户最需要知道「还剩一点没看完」的时候 —— 短到这个下限就不再更短。
HANDLE_MIN = 28

#: 滑块圆角（点）：高度的一半附近，取值让短滑块也还是一根圆头小条。
HANDLE_RADIUS = 3

#: 滑块常态与悬停态的不透明度。**用正文色按透明度取**而不是挑一个中性灰：
#: 浅底上是深灰、深底上是浅灰，两套合成同一个公式（:meth:`AppTheme.text_at`），
#: 换外观时不用在这里分叉一次。
HANDLE_ALPHA = 0.32
HANDLE_ALPHA_HOVER = 0.55


def scrollbar_style(app_theme: AppTheme) -> str:
    """按当前主题生成滚动条的样式表。颜色全从主题派生，不在这里挑中性灰。

    **刻意不写 ``width``**：一写，``sizeHint().width()`` 就归样式表说了算，而网格的排版
    宽度正是从那里扣掉滚动条的（``PhotoGrid._scrollbar_reserve``）—— 一次「让这条看着
    轻一点」就会顺带改掉每一格有多大，还把两条钉着过渡判据的用例顶翻（格宽一变列数就
    可能变，列数一变就该过渡，与边缘差多小无关）。不写时 Qt 交回原生那一档（实测 18，
    与挂样式表之前分毫不差），排版一个像素都不动。
    """
    handle = rgba(app_theme.text_at(HANDLE_ALPHA))
    handle_hover = rgba(app_theme.text_at(HANDLE_ALPHA_HOVER))
    # 两端那两个按钮：宽度与高度都归零，它们占的那一截就并回轨道里，滑块因而能走满整条。
    zero = "height: 0px; width: 0px;"
    return f"""
QScrollBar {{ background: transparent; border: none; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {handle};
    border: none;
    border-radius: {HANDLE_RADIUS}px;
    min-height: {HANDLE_MIN}px;
    margin: 0px {HANDLE_SIDE_INSET}px;
}}
QScrollBar::handle:vertical:hover {{ background: {handle_hover}; }}
QScrollBar::handle:horizontal {{
    background: {handle};
    border: none;
    border-radius: {HANDLE_RADIUS}px;
    min-width: {HANDLE_MIN}px;
    margin: {HANDLE_SIDE_INSET}px 0px;
}}
QScrollBar::handle:horizontal:hover {{ background: {handle_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ {zero} }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: none; }}
"""


class _ScrollBarStyler(QObject):
    """把 :func:`scrollbar_style` 按当前外观反复下发到一条滚动条上。

    **父对象取滚动条自己**：这样控件没了它也跟着没，那条 ``changed`` 连接随之断开 ——
    连接到一个 lambda 上时那条 lambda 抓着已析构的控件，换外观那一轮会抛
    ``RuntimeError``。
    """

    def __init__(self, bar: QScrollBar) -> None:
        super().__init__(bar)
        self._bar = bar
        theme.ensure_installed().changed.connect(self.refresh)
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        # 颜色是拼进样式表的字面量，所以换外观必须**重新下发**一次才会换色 ——
        # 只重设调色板对它没有影响。
        self._bar.setStyleSheet(scrollbar_style(theme.current_theme()))


def apply_scrollbar_style(bar: QScrollBar) -> QScrollBar:
    """给一条滚动条下发这份样式，并接上换外观后重下发；返回它自己，方便就地一行写完。"""
    _ScrollBarStyler(bar)
    return bar


__all__ = [
    "HANDLE_ALPHA",
    "HANDLE_ALPHA_HOVER",
    "HANDLE_MIN",
    "HANDLE_RADIUS",
    "HANDLE_SIDE_INSET",
    "apply_scrollbar_style",
    "scrollbar_style",
]
