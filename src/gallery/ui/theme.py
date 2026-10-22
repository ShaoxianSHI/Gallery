"""窗口底色的唯一来源：``Window`` / ``Base`` / ``AlternateBase`` 都指向一个颜色，再逐控件下发。

不碰应用调色板；浮层那几档颜色由 :data:`RESOLVED_ROLES` 固定（ADR 0035）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget

#: 统一底色取自调色板的哪个角色：``Base`` 是内容区色，浅色下白、深色下 ``#171717``。
BACKGROUND_ROLE = QPalette.ColorRole.Base

#: 所有「铺底色」的角色：窗口与菜单栏、列表与文本视口、表格隔行底色 —— 少改一个
#: 就有一层仍按系统原来的颜色铺。
BACKGROUND_ROLES = (
    QPalette.ColorRole.Window,
    QPalette.ColorRole.Base,
    QPalette.ColorRole.AlternateBase,
)

#: 铺底色之外**也要固定**的那几个角色，正是弹出菜单与弹出列表读的（正文色、高亮色、
#: 高亮之下的字色、置灰行的字色）。三处浮层拿的是同一份下发的调色板（ADR 0035）。
RESOLVED_ROLES = (
    QPalette.ColorRole.Highlight,
    QPalette.ColorRole.HighlightedText,
    QPalette.ColorRole.WindowText,
    QPalette.ColorRole.Text,
    QPalette.ColorRole.PlaceholderText,
)

#: 逐组各钉各的，**不是统一钉 ``Active``**：置灰那一组是给「这一行点不得」用的。
COLOR_GROUPS = (
    QPalette.ColorGroup.Active,
    QPalette.ColorGroup.Inactive,
    QPalette.ColorGroup.Disabled,
)

#: 样式表里写了它才需要重设一遍（见 :func:`_push_one`）。**只看这一个词**：自绘控件
#: （胶囊、横幅、日历）各有自己的 ``_apply_theme``，重设它们的样式表是白费力气。
_PALETTE_KEYWORD = "palette("


def rgba(color: QColor) -> str:
    """样式表用的颜色写法（``rgba(r, g, b, a)``）：``QColor.name()`` 带透明度时会悄悄丢掉 alpha。"""
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
        """正文色按透明度取：深色下是浅字压深底，浅色下反过来，两套合成一个公式。"""
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

    **不能**调用 ``QApplication.setPalette``：那会把这份颜色固定。
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
    """把调色板给一个控件，并让它的样式表重新解析一次：``palette(...)`` 只在装上去时解析。"""
    widget.setPalette(palette)
    sheet = widget.styleSheet()
    if _PALETTE_KEYWORD in sheet:
        widget.setStyleSheet(sheet)


def _push_palette(app: QApplication, palette: QPalette) -> None:
    """把调色板逐个送到窗口里的每个控件：带样式表的控件会被 ``QStyleSheetStyle`` 塞一份自己的。"""
    for top in app.topLevelWidgets():
        pending = [top]
        while pending:
            widget = pending.pop()
            _push_one(widget, palette)
            pending.extend(child for child in widget.children() if isinstance(child, QWidget))


def _probe_palette(background: QColor) -> QPalette:
    """给「问 Qt 要那几档」用的调色板：除底色外一个角色都不带。应用调色板里那几档有时带着
    「已解析」标记，那时问出来的是它自己那一档，浮层颜色再也回不来。
    """
    palette = QPalette()
    for role in BACKGROUND_ROLES:
        palette.setColor(role, background)
        palette.setColor(QPalette.ColorGroup.Disabled, role, background)
    return palette


def _resolve_roles(palette: QPalette, background: QColor) -> QPalette:
    """把 :data:`RESOLVED_ROLES` 里的几档解析成一档钉进这份调色板；不钉的话 Qt 会按控件类型
    各填各的（``QMenu`` 与 ``QTreeWidget`` 给出两种蓝）。
    """
    probe = QMenu()
    probe.setPalette(_probe_palette(background))
    resolved = probe.palette()
    for role in RESOLVED_ROLES:
        for group in COLOR_GROUPS:
            palette.setColor(group, role, resolved.color(group, role))
    return palette


def pushed_palette(app: QApplication) -> QPalette:
    """要下发到每个控件上的那一份：底色统一，浮层那几档钉成同一档。"""
    current = current_theme(app)
    palette = QPalette(app.palette())
    for role in BACKGROUND_ROLES:
        palette.setColor(role, current.background)
        palette.setColor(QPalette.ColorGroup.Disabled, role, current.background)
    return _resolve_roles(palette, current.background)


def apply_theme(app: QApplication) -> AppTheme:
    """把统一底色下发到所有窗口，返回这一轮的主题。刻意不调用 ``QApplication.setPalette``。"""
    _push_palette(app, pushed_palette(app))
    return current_theme(app)


def apply_appearance(mode: str, app: QApplication | None = None) -> None:
    """强制浅色 / 深色，或交还系统（``system``）。

    走 ``QStyleHints.setColorScheme``（Qt 6.8+），之后由 :class:`ThemeWatcher` 收
    ``colorSchemeChanged`` 再下发一轮 —— **这里不手动调** :func:`apply_theme`，否则会把
    信号发出那一刻的半路调色板钉进整棵控件树。
    """
    scheme = {
        "light": Qt.ColorScheme.Light,
        "dark": Qt.ColorScheme.Dark,
    }.get(mode, Qt.ColorScheme.Unknown)
    _application(app).styleHints().setColorScheme(scheme)


def push_palette(widget: QWidget, *, app: QApplication | None = None) -> None:
    """把当前主题的调色板下发到一棵控件树上：主题下发之后才建的控件（搜索框的候选列表）靠它补。"""
    instance = _application(app) if app is None else app
    palette = pushed_palette(instance)
    pending = [widget]
    while pending:
        current = pending.pop()
        _push_one(current, palette)
        pending.extend(child for child in current.children() if isinstance(child, QWidget))


def unify_title_bar(window: QWidget) -> bool:
    """让标题栏那一段也用窗口自己的底色，返回那一段是不是已经由本窗口自绘（只在 macOS 上动手）。

    去掉原生标题栏的半透明材质并把客户区扩到整窗；内容区留在安全区内，
    别关掉 ``WA_ContentsMarginsRespectsSafeArea``。
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
        self._pending = False
        app.paletteChanged.connect(self._on_system_theme_changed)
        # macOS 换外观时还会单独报一次 colorSchemeChanged。两个都接：谁先谁后各平台不同，
        # 少接一个就可能漏掉一整轮。
        app.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)

    def _on_system_theme_changed(self, *_args: object) -> None:
        """收到「系统换外观了」，把真正那一次下发排到这一批事件走完之后：回调触发时应用调色板还是
        半路状态，就地下发会把那份半路的调色板钉进整棵控件树。一轮要发好几个信号，所以只排一次。
        """
        if self._applying or self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self._apply_now)

    def _apply_now(self) -> None:
        self._pending = False
        self._applying = True
        try:
            apply_theme(self._app)
        finally:
            self._applying = False
        self.changed.emit()


def ensure_installed(app: QApplication | None = None) -> ThemeWatcher:
    """装一次主题，把当前底色重新下发一遍，返回监听器（幂等）。每次都要重新下发：调色板是逐控件
    设的，新窗口不会自动带上别的窗口那份。
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
    "COLOR_GROUPS",
    "RESOLVED_ROLES",
    "AppTheme",
    "ThemeWatcher",
    "apply_appearance",
    "apply_theme",
    "current_theme",
    "ensure_installed",
    "push_palette",
    "pushed_palette",
    "rgba",
    "unify_title_bar",
]
