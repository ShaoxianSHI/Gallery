"""窗口底色的唯一来源。

窗口里原本套着好几层，各铺各的底色：标题栏那一段与菜单栏、面包屑跟着系统调色板的
``Window``（深色下 ``#323232``），网格视口用 ``Base``（``#171717``），大图区与元数据
面板则写死成 ``#1C1C1E``。于是浅色主题下点开照片会突然变黑，深色主题下则是三四种
不同的黑。

这里把「整个窗口是一整块底色」收成一份主题：

- **底色只有一个来源**，即 :data:`BACKGROUND_ROLE` 指的那个调色板角色。取系统调色板里的
  ``Base``（内容区色）：浅色下是白，深色下是 ``#171717`` —— 比 ``Window`` 的 ``#323232``
  深得多，照片才沉得下去。
- :func:`apply_theme` 把 ``Window`` / ``Base`` / ``AlternateBase`` 三个铺底色的角色都指向
  这一个颜色，再逐个控件下发（见 :func:`_push_palette`）。
- 标题栏那一段由 macOS 自己画，而且是一层半透明材质（实测深色下 ``#3b3e3f``、浅色下
  ``#f2f4f7``，还会随壁纸变），调色板既改不到它、也匹配不准它。所以改成把内容铺到标题栏
  底下、让标题栏不再画自己的底（见 :func:`unify_title_bar`），那一段于是也成了我们的底色。
- 少数自己画底色的地方（大图区、元数据面板）不写死颜色，改为从这里取。

**三个 Qt 上的坑，都在这里绕开**：

1. ``QApplication.setPalette()`` 会把应用调色板标成「显式设置」。此后系统换浅色 / 深色时
   Qt 不再更新它，表现就是「切到浅色，只有 macOS 画的标题栏变浅了，其余还是黑的」。
   所以这里**绝不碰应用调色板**，只在控件上下发；系统怎么变，我们照单全收。
2. 带样式表的控件会被 ``QStyleSheetStyle`` 塞一份「自己的」调色板，应用级的变化到不了
   它们，所以 :func:`_push_palette` 逐个控件显式设置一遍。
3. 样式表里的 ``palette(...)`` **只在样式表装上去的那一刻解析一次**，之后改调色板它不会
   重来 —— 症状是「切了深浅色，那一枚胶囊的字色停在原处，要点一下才对」（点一下会重建
   它，于是重新解析了一遍）。所以 :func:`_push_one` 把**原样的样式表再设一遍**：
   颜色仍由 Qt 自己按那一档调色板挑，浅色深色各自是什么就是什么，这里修的是「不立刻变」。

要换底色只改 :data:`BACKGROUND_ROLE` 一处。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

#: 统一底色取自调色板的哪个角色（见模块文档）。
#: ``Base`` 是系统的「内容区色」：浅色下是白，深色下是 ``#171717``。
BACKGROUND_ROLE = QPalette.ColorRole.Base

#: 所有「铺底色」的角色。``Window`` 是窗口与菜单栏，``Base`` 是列表与文本视口，
#: ``AlternateBase`` 是表格的隔行底色 —— 少改一个，就有一层仍按系统原来的颜色铺。
BACKGROUND_ROLES = (
    QPalette.ColorRole.Window,
    QPalette.ColorRole.Base,
    QPalette.ColorRole.AlternateBase,
)

#: 样式表里写了它才需要「重新解析一遍」（见 :func:`_push_one`）。**只看这一个词**，
#: 别改成「凡是带样式表的都重设」：外面那一大圈控件是自绘的（胶囊、横幅、日历），
#: 重设它们的样式表纯属白费力气，而自绘控件另有各自的 ``_apply_theme`` 收拾。
_PALETTE_KEYWORD = "palette("


def rgba(color: QColor) -> str:
    """样式表用的颜色写法（``rgba(r, g, b, a)``）。

    Qt 样式表认这个写法；``QColor.name()`` 在带透明度时给的是六位十六进制，
    会把半透明的颜色悄悄画成不透明。
    """
    alpha = round(color.alphaF(), 3)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


@dataclass(frozen=True)
class AppTheme:
    """一份界面主题。底色只有一个，其余颜色由它与系统调色板派生。"""

    #: 整块窗口共用的底色。
    background: QColor
    #: 正文色（底色相反的一端）。
    text: QColor
    #: 链接色（面包屑、可点的那几段）。
    link: QColor
    #: 提示信息（exiftool 缺失之类）。
    notice: QColor
    #: 出错信息。
    error: QColor

    @property
    def is_dark(self) -> bool:
        return self.background.lightnessF() < 0.5

    def text_at(self, alpha: float) -> QColor:
        """正文色按透明度取。

        深色主题下等于「浅字压深底」，浅色主题下等于「深字压浅底」—— 深浅两套字色
        因此合成一个公式，不必各写一份。
        """
        color = QColor(self.text)
        color.setAlphaF(alpha)
        return color


def _application(app: QApplication | None = None) -> QApplication:
    instance = QApplication.instance() if app is None else app
    if instance is None:
        raise RuntimeError("还没有 QApplication，界面主题无从取起")
    return cast(QApplication, instance)


def current_theme(app: QApplication | None = None) -> AppTheme:
    """按应用当前的调色板算出一份主题。

    读的必须是**平台刚写进来的**那份应用调色板，所以本模块不调用
    ``QApplication.setPalette``（那会把这份颜色钉死，见模块文档）。
    """
    palette = _application(app).palette()
    background = QColor(palette.color(BACKGROUND_ROLE))
    dark = background.lightnessF() < 0.5
    return AppTheme(
        background=background,
        text=QColor(palette.color(QPalette.ColorRole.WindowText)),
        link=QColor(palette.color(QPalette.ColorRole.Link)),
        # 提示色在两种底色上都得够清楚：浅底上压暗，深底上提亮。
        notice=QColor(255, 207, 112) if dark else QColor(138, 90, 0),
        error=QColor(255, 155, 138) if dark else QColor(163, 49, 42),
    )


def _push_one(widget: QWidget, palette: QPalette) -> None:
    """把调色板给一个控件，并让它的样式表**重新解析一次**。

    **为什么样式表要再设一遍**（模块文档第 3 条）：``palette(...)`` 只在样式表装上去的
    那一刻被解析成具体颜色，之后无论控件还是应用的调色板改了，它都不会重来。症状是
    「切深浅色要等一下才对，点一下就对」—— 点一下之所以管用，是因为那一下重建了控件。

    重新解析之后拿到的颜色**与当初建它时同一档调色板解析出来的完全一样**：这一句修的是
    「不立刻变」，不是「换个颜色」。真要改某处颜色，那是那个控件自己的 ``_apply_theme``
    与样式表函数的事。
    """
    widget.setPalette(palette)
    sheet = widget.styleSheet()
    if _PALETTE_KEYWORD in sheet:
        widget.setStyleSheet(sheet)


def _push_palette(app: QApplication, palette: QPalette) -> None:
    """把调色板逐个送到窗口里的每个控件。

    样式表是个绕不开的坎：某个控件或它的父控件一旦带着样式表，``QStyleSheetStyle`` 就会
    给这一支塞一份「自己的」调色板（横幅那一块就带着一份，ADR 0029），此后连应用级的
    变化也到不了它们 —— 实测改一次应用调色板，标题栏那一段变了、网格那一层还停在原处。
    所以逐个显式设置：换过主题之后，整棵控件树是同一份。
    """
    for top in app.topLevelWidgets():
        pending = [top]
        while pending:
            widget = pending.pop()
            _push_one(widget, palette)
            pending.extend(child for child in widget.children() if isinstance(child, QWidget))


def apply_theme(app: QApplication) -> AppTheme:
    """把统一底色下发到所有窗口，返回这一轮的主题。

    刻意**不调用** ``QApplication.setPalette``：那会把应用调色板钉死，系统再换浅色 /
    深色时 Qt 就不更新它了（见模块文档）。这里只改控件自己的调色板。
    """
    theme = current_theme(app)
    palette = QPalette(app.palette())
    for role in BACKGROUND_ROLES:
        palette.setColor(role, theme.background)
        palette.setColor(QPalette.ColorGroup.Disabled, role, theme.background)
    _push_palette(app, palette)
    return theme


def push_palette(widget: QWidget, *, app: QApplication | None = None) -> None:
    """把当前主题的调色板下发到**一棵**控件树上（:func:`apply_theme` 面向整个应用那一份的单棵版）。

    **为什么需要它**：:func:`apply_theme` 走的是「此刻已经存在的顶层控件」，而有些控件是
    之后才建的 —— 搜索框的候选列表就是一个（它在 ``_build_ui`` 的最后一步建，主题早就
    下发过了）。不补这一下，它就一直用系统默认那一档颜色：于是候选列表与右键菜单
    明明「取自同一个地方」，取到的却不是同一份（实测列表 ``#ffffff``、菜单 ``#efefef``）。
    """
    instance = _application(app) if app is None else app
    theme = current_theme(instance)
    palette = QPalette(instance.palette())
    for role in BACKGROUND_ROLES:
        palette.setColor(role, theme.background)
        palette.setColor(QPalette.ColorGroup.Disabled, role, theme.background)
    pending = [widget]
    while pending:
        current = pending.pop()
        _push_one(current, palette)
        pending.extend(child for child in current.children() if isinstance(child, QWidget))


def unify_title_bar(window: QWidget) -> bool:
    """让标题栏那一段也用窗口自己的底色，返回**那一段是不是已经归我们了**（只在 macOS 上动手）。

    原生标题栏是一层半透明材质，跟着桌面壁纸走：实测深色下 ``#3b3e3f``、浅色下
    ``#f2f4f7``，与系统给内容区的颜色（``#323232`` / ``#ececec``）本来就差一档 ——
    调色板既改不到它，也匹配不准它。

    这两个窗口标志把这层材质去掉（``NoTitleBarBackgroundHint``），并把客户区扩到整个
    窗口（``ExpandedClientAreaHint``）：那一段于是由窗口自己的底色来画，和下面连成一块。

    两条边界都实测过：

    - 红黄绿三个按钮仍由系统画在最上层，窗口照旧能按标题栏拖动（拖动由
      :meth:`MainWindow.mousePressEvent` 接管，见那里）；
    - 内容区会留在安全区内（实测顶部空出 28 点），面包屑不会挤到按钮下面 —— 所以
      **不要**顺手关掉 ``WA_ContentsMarginsRespectsSafeArea``，那正是它干的活。

    其它平台返回 ``False`` 原样放过：这两个标志在别处含义不同，而这里要解决的只是 macOS
    标题栏的颜色。返回值是给 :class:`~gallery.ui.title_bar.TitleBar` 用的 —— 那一段既然
    归我们了，**标题文字也该由我们自己画**（ADR 0025 决策 2），别处照旧 ``setWindowTitle``。
    """
    if sys.platform != "darwin":
        return False
    flags = window.windowFlags()
    window.setWindowFlags(
        flags | Qt.WindowType.ExpandedClientAreaHint | Qt.WindowType.NoTitleBarBackgroundHint
    )
    return True


class ThemeWatcher(QObject):
    """系统换浅色 / 深色时重新下发底色，并通知自己画底色的控件。"""

    #: 底色已经换过一轮。关心颜色的控件连它，然后重读 :func:`current_theme`。
    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._applying = False
        app.paletteChanged.connect(self._on_system_theme_changed)
        # macOS 换外观时还会单独报一次 colorSchemeChanged。两个都接：谁先谁后、是不是
        # 都发，各平台不一样，少接一个就可能漏掉一整轮。
        app.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)

    def _on_system_theme_changed(self, *_args: object) -> None:
        if self._applying:
            return
        self._applying = True
        try:
            apply_theme(self._app)
        finally:
            self._applying = False
        self.changed.emit()


def ensure_installed(app: QApplication | None = None) -> ThemeWatcher:
    """装一次主题，并把当前底色重新下发一遍，返回监听器（幂等）。

    控件在构造时调它：绕开 ``ui.app.run`` 直接建窗口的测试与脚本，也就拿到同一份底色。

    **每次都要重新下发**，不能因为「监听器已经在了」就早退：调色板是逐控件设的，一个刚建
    出来的窗口不会自动带上别的窗口里那份 —— 早退的话，新窗口的窗口那层仍是系统原来的
    ``#efefef``，而它的网格视口已被上一个窗口带着走成了 ``#ffffff``，接缝又回来了。
    """
    application = _application(app)
    watcher = application.findChild(ThemeWatcher)
    if watcher is None:
        watcher = ThemeWatcher(application)
    apply_theme(application)
    return cast(ThemeWatcher, watcher)


__all__ = [
    "BACKGROUND_ROLE",
    "BACKGROUND_ROLES",
    "AppTheme",
    "ThemeWatcher",
    "apply_theme",
    "current_theme",
    "ensure_installed",
    "rgba",
    "unify_title_bar",
]
