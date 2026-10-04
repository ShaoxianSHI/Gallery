"""标题条上那个「用外部应用打开」的入口：一个图标，点开是一列应用。

**摆位与 ``SearchField``、``TitleBar`` 同一套手法**：浮在窗口顶边那一行上，
不进任何布局（ADR 0025、ADR 0034）。这一条本来就只存在于 macOS——它的高度来自
安全区，别的平台上压根没有这一条，硬摆一个上去只会盖住内容。

**下拉框里每一项是「图标 + 全称」，按钮上只有图标**：图标是用来认的（几个应用的
图标长得都不一样），全称是用来读的——图标下拉之后那几行字要是也只剩图标，用户得
一个个试过去才知道点下去会发生什么。

**下拉箭头用的是 Qt 原生的那个，不要给它写样式表。** 实测：**只要 ``::menu-indicator``
上挂了任何一条规则（哪怕是一条空的），Qt 就不再画那个默认箭头** —— 于是右半格空着，
整个控件读起来「点不开」。想照 DeepSeek Harness 那样在图标与箭头之间加一条分隔线，
得连箭头一起自己画（``image: url(...)``），代价是一份要跟着主题走的素材；这一条不划算，
于是没有那条线。

**这个模块只管「摆」与「收上来点的那一下」**：开哪一个、打开之后横幅上写什么，
都由外面按 :attr:`ExternalEntry.id` 分派（它才知道此刻是哪一页、哪一天，
以及这一项要不要进后台线程）。**加一个应用不用改这里**。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QMenu, QToolButton

from gallery.ui import theme
from gallery.ui.rename_edit import field_background
from gallery.ui.theme import AppTheme, rgba

#: 按钮的**高**跟着搜索胶囊走（不写死，见 :func:`button_rect`），这是它的**宽**。
#:
#: **不是正方形**：里面放的是「图标 + 下拉箭头」两样东西（与 DeepSeek Harness 那个
#: 组件同一套画法）。原来那个 22×22 的正方形只画得出一个孤零零的箭头 —— 视觉检查
#: 抓到的症状就是它：屏幕上是一个方框加一个三角。
BUTTON_WIDTH = 40

#: 按钮里那个图标的边长（点）。比高度小一圈，四周才留得住边。
ICON_EDGE = 16

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


def button_rect(field: QRect, width: int = BUTTON_WIDTH, gap: int = BUTTON_GAP) -> QRect:
    """按钮摆在搜索胶囊左边哪儿（**纯算术**，不必造一个真窗口就能测）。

    **从胶囊那一块往左长出来**，而不是各自拿标题条的高度算一遍 —— 于是两者必然
    同高、同一基线。各算各的差一两个点，肉眼就是「歪了」：胶囊为了字的中线被
    往下挪了 :data:`~gallery.ui.search_field.TEXT_ALIGN_DROP`，按钮没挪。
    """
    return QRect(field.left() - gap - width, field.top(), width, field.height())


def button_style(app_theme: AppTheme, radius: int) -> str:
    """按钮长什么样 —— **与搜索胶囊同一套视觉语言**。

    底色、边框、圆角全部取自胶囊那一处（``rename_edit.field_background`` 与
    ``search_field_style`` 是同一份口径）：两个并排的控件各画各的边框与圆角，
    早晚会在某个主题下差一档，而那时没人说得清是哪一档（ADR 0025）。

    ``radius`` 由摆它的那一处按实际高度给过来（默认 = 一半高度，也就是正圆）。
    **写死不成立**：胶囊的高度跟着窗口让出来的那一条走（实测安全区不足 28 点时是
    18），而 Qt 样式表里半径一旦超过高度的一半就不画 —— 按钮于是变成一个方框，
    正是视觉检查里抓到的第二个症状。
    """
    corner = max(0, radius or ICON_EDGE)
    return f"""
    QToolButton {{
        background: {rgba(field_background(app_theme))};
        border: 1px solid {rgba(app_theme.text_at(0.28))};
        border-radius: {corner}px;
        padding: 0px;
    }}
    QToolButton:hover {{ border-color: palette(highlight); }}
    """


def drawn_icon(kind: str, size: int = ICON_EDGE) -> QIcon:
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


def calendar_icon(size: int = ICON_EDGE) -> QIcon:
    """日历那一项的图标：装了 PWA 就用应用自己的，没装就画一个。"""
    from gallery.core.external import HOST, app_icon, find_chrome_pwa

    app = find_chrome_pwa(HOST)
    if app is not None:
        path = app_icon(app)
        if path is not None:
            return QIcon(str(path))
    return drawn_icon(ICON_CALENDAR, size)


def folder_icon(size: int = ICON_EDGE) -> QIcon:
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
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        # **不 autoRaise**：那一档是「浮在内容上、默认什么都不画」，而我们要的是
        # 一个看得见的圆角容器 —— 与胶囊并排的那个（视觉检查抓到的第二个症状）。
        self.setAutoRaise(False)
        self.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        self.setToolTip(TOOLTIP)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(False)
        #: 菜单自己建好并一直用着。``InstantPopup`` 本来也会建一个，而
        #: :meth:`refresh` 要的是「同一个菜单反复清空重建」—— 现取现建就得每次
        #: 应付「菜单还没有」那一种情况。
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新下发边框与圆角（``SearchField`` 同一个做法）。

        **自绘图标也要重画一遍**：它们是跟着 :attr:`AppTheme.text` 变色的，而
        :func:`~gallery.ui.external_button.calendar_icon` 每次都现画 —— 换主题时
        不重画，那两个图标还停在旧主题的颜色上。
        """
        self.setStyleSheet(button_style(theme.current_theme(), self.height() // 2))
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        # **半径跟着实际高度走**：高度是被 :func:`button_rect` 带着胶囊一起给的，
        # 而样式表里的半径一旦超过高度的一半就不画（四角会变成方的）。
        self.setStyleSheet(button_style(theme.current_theme(), self.height() // 2))

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
        face: QIcon | None = None
        for entry in self._entries:
            if not entry.enabled():
                continue
            icon = entry.icon or calendar_icon()
            # **按钮面上是第一个可用的那一项的图标**。曾经只给菜单项设了图标而忘了
            # 按钮自己，于是屏幕上是一个方框加一个三角（Qt 给空图标画的占位符）。
            if face is None:
                face = icon
            action = QAction(icon, entry.name, self._menu)
            action.triggered.connect(lambda _checked=False, e=entry: self._activate(e))
            self._menu.addAction(action)
        self.setIcon(face or QIcon())

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
    "BUTTON_WIDTH",
    "ICON_CALENDAR",
    "ICON_EDGE",
    "ICON_FOLDER",
    "ICON_KINDS",
    "TOOLTIP",
    "ExternalAppsButton",
    "ExternalEntry",
    "button_rect",
    "button_style",
    "calendar_icon",
    "drawn_icon",
    "folder_icon",
]
