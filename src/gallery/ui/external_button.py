"""标题条上那个「用外部应用打开」的入口：一个图标，点开是一列应用。

**摆位与 ``SearchField``、``TitleBar`` 同一套手法**：浮在窗口顶边那一行上，
不进任何布局（ADR 0025、ADR 0034）。这一条本来就只存在于 macOS——它的高度来自
安全区，别的平台上压根没有这一条，硬摆一个上去只会盖住内容。

**下拉框里每一项是「图标 + 全称」，按钮上只有图标**：图标是用来认的（几个应用的
图标长得都不一样），全称是用来读的——图标下拉之后那几行字要是也只剩图标，用户得
一个个试过去才知道点下去会发生什么。

**这个模块只管「摆」与「收上来点的那一下」**：开哪一个、打开之后横幅上写什么，
都由外面按 :attr:`ExternalEntry.id` 分派（它才知道此刻是哪一页、哪一天，
以及这一项要不要进后台线程）。**加一个应用不用改这里**。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QToolButton

from gallery.ui import theme

#: 按钮是方的，边长（点）。与搜索胶囊那个高度（22）取同一个数量级，摆在一起不打架。
BUTTON_SIZE = 22

#: 按钮与搜索胶囊之间的缝（点）。
BUTTON_GAP = 6

#: 提示气泡上写的那一句。
TOOLTIP = "用外部应用打开当前这一页"

#: 自绘图标有哪几样。**只认形状，不认品牌**（见 :func:`drawn_icon`）。
ICON_CALENDAR = "calendar"
ICON_FOLDER = "folder"
ICON_KINDS = (ICON_CALENDAR, ICON_FOLDER)


@dataclass(frozen=True)
class ExternalEntry:
    """下拉框里的一项。

    :param id: 这一项的标识。**点下去之后做什么由外面按它分派**，不在这里写 ——
        日历那一项要等浏览器起来（好几秒），必须进后台线程，而访达那一项是当场
        一条 ``open -R`` 就完事。把两种节奏不同的东西塞进同一个回调里，
        「这一项要不要等」这件事就只好藏在实现里。
    :param name: **全称**，下拉框里显示的就是它。
    :param icon: 图标；``None`` 就是画的那个中性图标。
    :param enabled: 此刻该不该出现在下拉框里 —— 判据是「这一页有没有它要的东西」：
        日历那一项要一段日子，访达那一项要一个当前文件夹。
    """

    id: str
    name: str
    icon: QIcon | None
    enabled: Callable[[], bool]


def button_rect(search_x: int, strip_height: int, size: int = BUTTON_SIZE) -> QRect:
    """按钮摆在搜索胶囊左边哪儿（**纯算术**，不必造一个真窗口就能测）。

    贴着胶囊左缘往左让出 :data:`BUTTON_GAP`，纵向与胶囊**同一条中线**——
    两个不同高的东西并排站着时，中线对齐才像是同一行里的。
    """
    return QRect(search_x - BUTTON_GAP - size, (strip_height - size) // 2, size, size)


def drawn_icon(kind: str, size: int = BUTTON_SIZE) -> QIcon:
    """画一个中性的轮廓（:data:`ICON_KINDS` 之一），跟着主题变色。

    **不冒充任何一个应用**：访达那个「两半脸」的图标与日历应用的品牌色都是别人的
    商标，仓库里不放、也不临摹（ADR 0034 决策 6）。一个中性的文件夹轮廓足够让人
    认出「这是个文件夹」。
    """
    accent = theme.current_theme().text
    if kind == ICON_FOLDER:
        return _draw_folder(size, accent)
    return _draw_calendar(size, accent)


def _draw_calendar(size: int, accent: QColor) -> QIcon:
    """画一个中性的日历轮廓。

    **没有装那个 PWA 时用它顶上，而仓库里不放第三方图标**：装了就直接用应用包
    自带的那一份 ``app.icns``（永远是最新的），没装就画一个**不冒充任何人**的
    轮廓（ADR 0034 决策 6）。跟着主题变色，于是浅色深色下都看得清。
    """
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(accent)
        pen.setWidthF(max(1.0, size * 0.06))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        margin = size * 0.17
        body = QRectF(margin, margin * 1.35, size - 2 * margin, size - margin * 2.6)
        radius = size * 0.16
        painter.drawRoundedRect(body, radius, radius)
        # 顶上那一条：横穿身体，把「哪一段是挂耳之上」说出来。
        painter.drawLine(
            int(body.left()),
            int(body.top() + body.height() * 0.28),
            int(body.right()),
            int(body.top() + body.height() * 0.28),
        )
        # 挂耳：两根短短的竖线。
        for fraction in (0.3, 0.7):
            x = body.left() + body.width() * fraction
            painter.drawLine(int(x), int(body.top()), int(x), int(body.top() - margin * 0.55))
    finally:
        painter.end()
    return QIcon(pixmap)


def calendar_icon(size: int = BUTTON_SIZE) -> QIcon:
    """日历那一项的图标：装了 PWA 就用应用自己的，没装就画一个。"""
    from gallery.core.external import HOST, app_icon, find_chrome_pwa

    app = find_chrome_pwa(HOST)
    if app is not None:
        path = app_icon(app)
        if path is not None:
            return QIcon(str(path))
    return drawn_icon(ICON_CALENDAR, size)


def folder_icon(size: int = BUTTON_SIZE) -> QIcon:
    """访达那一项的图标：一个中性的文件夹轮廓（不临摹访达自己的那个）。"""
    return drawn_icon(ICON_FOLDER, size)


def _draw_folder(size: int, accent: QColor) -> QIcon:
    """一个文件夹：上面一个小挂头，下面一个圆角身子。"""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(accent)
        pen.setWidthF(max(1.0, size * 0.06))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        margin = size * 0.17
        width = size - 2 * margin
        body_top = margin + size * 0.16
        body = QRectF(margin, body_top, width, size - body_top - margin)
        painter.drawRoundedRect(body, size * 0.13, size * 0.13)
        # 挂头：左上角那一小块，文件夹的形状全靠它认出来。
        tab = QRectF(margin, margin, width * 0.45, size * 0.2)
        painter.drawLine(int(tab.left()), int(tab.top()), int(tab.left()), int(body.top()))
        painter.drawLine(int(tab.left()), int(tab.top()), int(tab.right()), int(tab.top()))
        painter.drawLine(int(tab.right()), int(tab.top()), int(tab.right()), int(body.top()))
    finally:
        painter.end()
    return QIcon(pixmap)


class ExternalAppsButton(QToolButton):
    """那个图标按钮。点开是一列 :class:`ExternalEntry`。"""

    #: 用户选了哪一项（带的是那一项，具体的动作与横幅交给外面）。
    entry_activated = Signal(object)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._entries: tuple[ExternalEntry, ...] = ()
        #: 自绘图标要跟着主题重画，所以记着当前那一套图标（换主题时重画一遍）。
        self._drawn_icons: list[QIcon] = []
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setAutoRaise(True)
        self.setToolTip(TOOLTIP)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(False)
        #: 菜单自己建好并一直用着。``InstantPopup`` 本来也会建一个，而
        #: :meth:`refresh` 要的是「同一个菜单反复清空重建」—— 现取现建就得每次
        #: 应付「菜单还没有」那一种情况。
        self._menu = QMenu(self)
        self.setMenu(self._menu)

    def set_entries(self, entries: Sequence[ExternalEntry]) -> None:
        """换掉下拉框里那几项。**不立刻建菜单** —— 建菜单要先问一遍 ``enabled``，
        那要读界面状态，只在 :meth:`refresh` 的时候做。"""
        self._entries = tuple(entries)
        self.refresh()

    def refresh(self) -> None:
        """按此刻的可用性重建菜单。

        **每次翻页、切层都要问一遍**：日历那一项在网格页上不该出现，访达那一项在
        没有当前文件夹时也不该出现 —— 摆一个点下去没反应的灰条目只会让人以为坏了。
        """
        self._menu.clear()
        self._drawn_icons = []
        for entry in self._entries:
            if not entry.enabled():
                continue
            icon = entry.icon
            if icon is None:
                icon = calendar_icon()
                self._drawn_icons.append(icon)
            action = QAction(icon, entry.name, self._menu)
            action.triggered.connect(lambda _checked=False, e=entry: self._activate(e))
            self._menu.addAction(action)

    def is_available(self) -> bool:
        """下拉框里此刻**有没有东西**。

        **空着的按钮不该占位置**：日历那一页要一段日子、访达那一项要一个当前文件夹，
        没有它们的页面（还没选库的欢迎页）上按钮就整个收起来 —— 摆一个点开是空菜单
        的图标，用户只会以为坏了。
        """
        return any(entry.enabled() for entry in self._entries)

    def _activate(self, entry: ExternalEntry) -> None:
        self.entry_activated.emit(entry)


__all__ = [
    "BUTTON_GAP",
    "BUTTON_SIZE",
    "ICON_CALENDAR",
    "ICON_FOLDER",
    "ICON_KINDS",
    "TOOLTIP",
    "ExternalAppsButton",
    "ExternalEntry",
    "button_rect",
    "calendar_icon",
    "drawn_icon",
    "folder_icon",
]
