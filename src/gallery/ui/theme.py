"""窗口底色的唯一来源。

窗口里原本套了好几层，各铺各的底色：标题框（菜单栏）与面包屑跟着系统调色板的
``Window``，网格视口用 ``Base``，大图区与元数据面板则写死成 ``#1C1C1E``。浅色主题下
前三层是三种相近的浅灰、点开照片却突然变黑；深色主题下则是三四种不同的黑。

这里把「整个窗口是一整块底色」收成一份主题：

- **底色只有一个来源**，即 :data:`BACKGROUND_ROLE` 指的那个调色板角色。取 ``Window``
  而不是 ``Base``，是因为 macOS 的标题栏正是系统给 ``Window`` 的那个颜色 —— 改用
  ``Base`` 会让标题框那一段比正文浅一个色阶，最显眼的地方反而留着接缝。
- :func:`apply_theme` 把所有铺底色的角色都指向这一个颜色。于是标题框、面包屑、
  网格视口、欢迎页一路到照片区自动是同一块底色，系统切浅色 / 深色时一起变。
- 少数自己画底色的地方（大图区、元数据面板）不再写死颜色，改为从这里取。

要改颜色只改 :data:`BACKGROUND_ROLE` 那一处；要整体换色只改 :func:`apply_theme` 里
的那次赋值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

#: 统一底色取自调色板的哪个角色（见模块文档）。
BACKGROUND_ROLE = QPalette.ColorRole.Window

#: 所有「铺底色」的角色。``Window`` 是窗口与菜单栏，``Base`` 是列表与文本视口，
#: ``AlternateBase`` 是表格的隔行底色 —— 少改一个，就有一层仍按系统原来的颜色铺。
BACKGROUND_ROLES = (
    QPalette.ColorRole.Window,
    QPalette.ColorRole.Base,
    QPalette.ColorRole.AlternateBase,
)


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
    """按应用当前的调色板算出一份主题。"""
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


def _push_palette(app: QApplication, palette: QPalette) -> None:
    """把调色板逐个送到窗口里的每个控件。

    ``QApplication.setPalette`` 只影响**继承**调色板的控件。一旦某个控件或它的父控件
    带着样式表，``QStyleSheetStyle`` 就会给这一支塞一份「自己的」调色板（网格页上就有一份
    横幅样式），此后应用级的变化再也到不了它们 —— 实测改一次应用调色板，标题框变了、
    网格那一层还停在原处。所以这里逐个显式设置：换过主题之后，整棵控件树是同一份。
    """
    for top in app.topLevelWidgets():
        pending = [top]
        while pending:
            widget = pending.pop()
            widget.setPalette(palette)
            pending.extend(child for child in widget.children() if isinstance(child, QWidget))


def apply_theme(app: QApplication) -> AppTheme:
    """把所有铺底色的角色指向同一个颜色，返回统一之后的主题。"""
    theme = current_theme(app)
    palette = QPalette(app.palette())
    for role in BACKGROUND_ROLES:
        palette.setColor(role, theme.background)
        palette.setColor(QPalette.ColorGroup.Disabled, role, theme.background)
    app.setPalette(palette)
    _push_palette(app, palette)
    return theme


class ThemeWatcher(QObject):
    """系统换浅色 / 深色时重新统一底色，并通知自己画底色的控件。"""

    #: 底色已经换过一轮。关心颜色的控件连它，然后重读 :func:`current_theme`。
    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        # apply_theme 自己会再触发一次 paletteChanged；不挡住就绕回来了。
        self._applying = False
        app.paletteChanged.connect(self._on_palette_changed)

    def _on_palette_changed(self) -> None:
        if self._applying:
            return
        self._applying = True
        try:
            apply_theme(self._app)
        finally:
            self._applying = False
        self.changed.emit()


def ensure_installed(app: QApplication | None = None) -> ThemeWatcher:
    """装一次主题，返回监听器（幂等）。

    控件在构造时调它：绕开 ``ui.app.run`` 直接建窗口的测试与脚本，也就拿到同一份底色。
    """
    application = _application(app)
    watcher = application.findChild(ThemeWatcher)
    if watcher is not None:
        return cast(ThemeWatcher, watcher)
    apply_theme(application)
    return ThemeWatcher(application)


__all__ = [
    "BACKGROUND_ROLE",
    "BACKGROUND_ROLES",
    "AppTheme",
    "ThemeWatcher",
    "apply_theme",
    "current_theme",
    "ensure_installed",
    "rgba",
]
