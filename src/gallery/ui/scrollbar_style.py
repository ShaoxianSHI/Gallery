"""滚动条长什么样：**没有轨道，只剩滑块**。

网格页最右缘、日历周视图的时间轴、大图页的元数据面板 —— 三条滚动条分属三个包，
用户在它们之间来回走，各走各的样式早晚差一档，所以颜色与尺寸只从这一处解析
（与 :mod:`gallery.ui.menu_style` 同一个理由）。

**为什么不给原生那一份**：原生滚动条常驻一条实心轨道，颜色由系统当前外观决定，
与本应用统一下发的那份底色（:mod:`gallery.ui.theme`）对不上 —— 深色下是一条更深的
灰底竖条，看起来像内容上压了一块东西。这里把轨道（``::add-page`` / ``::sub-page``）
与两端的按钮（``::add-line`` / ``::sub-line``）都拿掉，只留一条按主题着色的滑块：
**9 点粗的胶囊**（:data:`HANDLE_WIDTH`），圆角取宽度的一半，离窗口右缘
:data:`HANDLE_GAP` = 2 点。用户原话连着三轮：「滚动条下面的背景层能去掉吗……让用户只能
看到滚动条」→「窄一点，圆角加大最大（宽度的一半），位置放到最右侧紧贴窗口」→
「再细一点，宽度改到 9 吧，然后和窗口右侧的距离调到 2 个 px」（上一版留 0，
贴着边的那一条看着像内容被切掉一截，用户说「没有边缘，很奇怪」）。

**写 ``width`` 会改动网格排版，而那是该改的**：``PhotoGrid._scrollbar_reserve`` 问的
正是 ``sizeHint().width()``，于是格子宽一点 —— 少掉的那几像素原本是照片的。
**但它确实会顶翻两条既有守护**（格宽一变列数就可能变，列数一变就该走切档过渡），
那两条已经改成自己找一个「不换列」的增量，见 ``tests/ui/browser/grid_helpers.py``。

**别用 ``QApplication.setStyleSheet`` 一把梭**：那会让每个控件都改走
``QStyleSheetStyle``，macOS 原生绘制随之失效（本仓库已经吃过一次亏，见
:mod:`gallery.ui.menu_style` 的模块文档）。要哪条滚动条就往哪条上下发。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QScrollBar

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 滑块右缘到**窗口**右缘留几像素（点）。
#:
#: 上一版留 0，滑块直接压在窗口边上 —— 用户原话：「你的截图上没有边缘，很奇怪」。
#: 贴着边的那一条看着像内容被切掉了一截，而两像素的缝才让它读得出「这是一根浮在
#: 页面上的东西」。**这一档是给眼睛的，不是给排版的**：它含在 :data:`SCROLLBAR_WIDTH`
#: 里面，所以网格照旧从那一档里扣。
HANDLE_GAP = 2

#: 这一条总共占多宽（点）= 滑块 + 上面那道缝。比原生那一档（实测 18）细得多：
#: 轨道已经不要了，留一条宽地只是把网格右边那一列内容缩掉，而缩掉的像素是照片自己的。
SCROLLBAR_WIDTH = 11

#: 滑块自己多宽（点），也就是**看得见的那一根**有多粗。
HANDLE_WIDTH = SCROLLBAR_WIDTH - HANDLE_GAP

#: 滑块最短多少像素（点）。内容只比视口高一点点时，Qt 会把滑块压成一条几乎看不见的线，
#: 而那正是用户最需要知道「还剩一点没看完」的时候 —— 短到这个下限就不再更短。
HANDLE_MIN = 28

#: 滑块圆角（点）= **宽度的一半**，于是两头是标准胶囊（用户要的最大值，再大就变成
#: 椭圆之外的东西）。跟着 :data:`HANDLE_WIDTH` 走：宽度改了这一档自动跟着改，
#: 别把它当成一个可以单独挑的数。
#:
#: **是 4.5 而不是 4**：整数除法会把 9 砍成 8，滑块就差一点不到胶囊 —— 那种
#: 「明明是胶囊却看着有点方」的差别就是这么来的。Qt 的样式表认小数 px。
HANDLE_RADIUS = HANDLE_WIDTH / 2

#: 滑块常态与悬停态的不透明度。**用正文色按透明度取**而不是挑一个中性灰：
#: 浅底上是深灰、深底上是浅灰，两套合成同一个公式（:meth:`AppTheme.text_at`），
#: 换外观时不用在这里分叉一次。
HANDLE_ALPHA = 0.32
HANDLE_ALPHA_HOVER = 0.55


def scrollbar_style(app_theme: AppTheme) -> str:
    """按当前主题生成滚动条的样式表。颜色全从主题派生，不在这里挑中性灰。

    **滑块只在下/右之外的那一侧留 :data:`HANDLE_GAP`**：竖着的那道缝在右边（挨着窗口
    右缘），横着的那道在下边（挨着下缘）—— 都是「尾端」，于是竖着与横着是同一条算式。
    """
    handle = rgba(app_theme.text_at(HANDLE_ALPHA))
    handle_hover = rgba(app_theme.text_at(HANDLE_ALPHA_HOVER))
    # 两端那两个按钮：宽度与高度都归零，它们占的那一截就并回轨道里，滑块因而能走满整条。
    zero = "height: 0px; width: 0px;"
    return f"""
QScrollBar {{ background: transparent; border: none; margin: 0px; }}
QScrollBar:vertical {{ width: {SCROLLBAR_WIDTH}px; }}
QScrollBar:horizontal {{ height: {SCROLLBAR_WIDTH}px; }}
QScrollBar::handle:vertical {{
    background: {handle};
    border: none;
    border-radius: {HANDLE_RADIUS}px;
    min-height: {HANDLE_MIN}px;
    margin: 0px {HANDLE_GAP}px 0px 0px;
}}
QScrollBar::handle:vertical:hover {{ background: {handle_hover}; }}
QScrollBar::handle:horizontal {{
    background: {handle};
    border: none;
    border-radius: {HANDLE_RADIUS}px;
    min-width: {HANDLE_MIN}px;
    margin: {HANDLE_GAP}px 0px 0px 0px;
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
    "HANDLE_GAP",
    "HANDLE_MIN",
    "HANDLE_RADIUS",
    "HANDLE_WIDTH",
    "SCROLLBAR_WIDTH",
    "apply_scrollbar_style",
    "scrollbar_style",
]
