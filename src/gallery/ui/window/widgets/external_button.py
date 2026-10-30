"""标题条上那个「用外部应用打开」的入口：**两个按钮**，不是一个。

浮在窗口顶边那一行上、不进任何布局，且只存在于 macOS（ADR 0025 / 0034）。左半直接打开
默认那一项，右半才弹出一列应用。**药丸、边框、分隔线与箭头全由容器的 ``paintEvent`` 画**：
给 ``QToolButton`` 挂菜单，Qt 会画一个实心三角与我们的 V 叠在一起，而给 ``::menu-indicator``
写样式那个三角又整个消失。开哪一个、横幅写什么都由外面按 :attr:`ExternalEntry.id` 分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QEnterEvent,
    QIcon,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from gallery.core.external import App, apps, icon_png
from gallery.core.i18n import t
from gallery.core.library.session import EXTERNAL_PAGE_VIDEO, Preferences
from gallery.ui import theme
from gallery.ui.rename_edit import field_background
from gallery.ui.window import menu_style

#: 按钮的**高**跟着搜索胶囊走（不硬编码，见 :func:`button_rect`），这是它的**宽**。
#: **不是正方形**：里面放的是「图标 + 下拉箭头」两样东西 —— 22×22 只画得出一个孤零零的箭头。
BUTTON_WIDTH = 44

#: 圆角（点）。**不是正圆**（正圆就是 11，而它是 22 高的一半）—— 与弹出列表那一层的
#: :data:`~gallery.ui.window.menu_style.MENU_CORNER_RADIUS`（8）是同一个量级。
BUTTON_RADIUS = 6

#: 按钮里那个图标的边长（点）。**与搜索框那个放大镜（13）同一档**（给 16 就明显粗一圈）。
ICON_EDGE = 13

#: 按钮与搜索胶囊之间的缝（点）。挤在一起读起来像搜索框自带的一个零件，而它是另一样东西。
BUTTON_GAP = 9

#: 右半（箭头那半）的宽度（点）。**固定**：它只放一个箭头，跟着图标长宽就成了一个空方块。
MENU_EDGE = 18

#: 箭头那半个 V 的大小（点）。与 :data:`ICON_EDGE` 同一档。
CHEVRON_HALF = 2.8
CHEVRON_DROP = 1.6
CHEVRON_WIDTH = 1.2

#: 弹出菜单与按钮之间的缝（点）。**与搜索框那个候选列表一致**。
MENU_GAP = 2

#: 提示气泡上写的那一句。
TOOLTIP = t("widget.external_button.tooltip")

#: 视频页固定排在最前的那一段，按内置次序 —— 存下的次序里缺哪个就补哪个（ADR 0093 / 0095）。
VIDEO_LEADERS = (apps.QUICKTIME.id, apps.IINA.id)

#: 自绘图标有哪几样。**只认形状，不认品牌**（见 :func:`drawn_icon`）。
ICON_CALENDAR = "calendar"
ICON_FOLDER = "folder"
ICON_MAP = "map"
ICON_TERMINAL = "terminal"
ICON_KINDS = (ICON_CALENDAR, ICON_FOLDER, ICON_MAP, ICON_TERMINAL)


@dataclass(frozen=True)
class ExternalEntry:
    """:param id: 这一项的标识；点下去之后做什么由外面按它分派。
    :param name: **全称**，下拉框里显示的就是它。
    :param icon: 图标；``None`` 就是画的那个中性图标。
    :param enabled: 此刻该不该出现在下拉框里（这一页有没有它要的东西）。
    :param pages: 会出现在哪几页（detail / video / grid / calendar）；设置菜单按它分四份。
    :param available: 这台机器上装没装 —— 与「此刻这一页有没有东西可开」是两件事。
    """

    id: str
    name: str
    icon: QIcon | None
    enabled: Callable[[], bool]
    pages: tuple[str, ...] = ()
    available: Callable[[], bool] = lambda: True


def button_rect(field: QRect, width: int = BUTTON_WIDTH, gap: int = BUTTON_GAP) -> QRect:
    """按钮摆在搜索胶囊左边哪儿（**纯算术**，不必造一个真窗口就能测）。

    **从胶囊那一块往左长出来**，而不是各自拿标题条的高度算一遍 —— 于是两者必然同高、同一
    基线（各算各的差一两个点，肉眼就是「歪了」：胶囊为了字的中线被往下挪了
    ``search_field.TEXT_ALIGN_DROP``，按钮没挪）。
    """
    return QRect(field.left() - gap - width, field.top(), width, field.height())


def menu_origin(
    anchor: QPoint, anchor_width: int, menu_width: int, *, gap: int = MENU_GAP
) -> QPoint:
    """弹出菜单摆在哪儿（**纯算术**，不必造窗口就能测）。

    :paramref:`anchor` 是那一格（右半）的**屏幕**坐标左上角；菜单**右缘与它右缘对齐**，上沿
    离它 :paramref:`gap` 点（与搜索框那个候选列表同一个距离）。右对齐而不是左对齐：从左缘往
    右铺的话整块会跑到搜索框那一侧，而用户看的是右半那个箭头。
    """
    return QPoint(anchor.x() + anchor_width - menu_width, anchor.y() + gap)


def drawn_icon(kind: str, size: int = ICON_EDGE) -> QIcon:
    """画一个**中性的**轮廓（:data:`ICON_KINDS` 之一），跟着主题变色。

    这是**取不到真图标时的那一条退路**（非 macOS、或者那个包被挪走了）。访达与日历应用的图标
    都是别人的商标，**仓库里不放也不临摹**（ADR 0034 决策 6）—— 画得像反而更像坏了。取得到
    的时候走 :func:`calendar_icon` / :func:`folder_icon`。
    """
    accent = theme.current_theme().text
    if kind == ICON_FOLDER:
        return _draw_folder(size, accent)
    if kind == ICON_MAP:
        return _draw_map(size, accent)
    if kind == ICON_TERMINAL:
        return _draw_terminal(size, accent)
    return _draw_calendar(size, accent)


def _draw_calendar(size: int, accent: QColor) -> QIcon:
    """画一个中性的日历轮廓：**没装那个 PWA 时用它顶上**（装了就用应用包自带的那一份
    ``app.icns``，永远是最新的）。跟着主题变色（ADR 0034 决策 6）。
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
    """访达那一项的图标：**用系统里那个真的**（与日历那一项同一个规矩）。

    取不到（非 macOS、或者那个文件被挪走了）才退回自画的轮廓 —— 那时**宁可画一个中性的**，
    也不画一个「像访达但不是」的。
    """
    from gallery.core.external import finder_icon

    path = finder_icon()
    if path is not None:
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return drawn_icon(ICON_FOLDER, size)


def google_maps_icon(size: int = ICON_EDGE) -> QIcon:
    """Google 地图那一项的图标：**装了 PWA 就用它包里那一份**，没装才画中性折页。

    与日历那一项同一个规矩（ADR 0034 决策 6）：别人的图标不随仓库分发，但系统里
    已经有一份现成的，用它永远比临摹得像。
    """
    from gallery.core.external import app_icon, find_chrome_pwa
    from gallery.core.external.maps import MAPS_HOST, MAPS_PATH

    app = find_chrome_pwa(MAPS_HOST, path_prefix=MAPS_PATH)
    if app is not None:
        path = app_icon(app)
        if path is not None:
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return drawn_icon(ICON_MAP, size)


def icon_for(app: App, size: int = ICON_EDGE, *, fallback: str = ICON_FOLDER) -> QIcon:
    """某一项外部应用的图标：**用系统画好的那一张**（与访达、日历那两项同一个规矩）。

    **别自己从应用包里那个 ``.icns`` 拆** —— 同一个包，macOS 画在 Dock 里的那一张与文件里存的
    那一份可以不一样（有的包把圆角外面那片填成不透明白色）。取不到才退回自画的轮廓：宁可画一个
    中性的，也不画一个「像它但不是」的。``fallback`` 由调用方点名那一样东西像什么。
    """
    data = icon_png(app)
    if data is not None:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            return QIcon(pixmap)
    return drawn_icon(fallback, size)


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


def _draw_terminal(size: int, accent: QColor) -> QIcon:
    """一个中性的终端窗口：圆角框里一条 ``>`` 与一条下划线。"""
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

        margin = size * 0.15
        body = QRectF(margin, margin * 1.2, size - 2 * margin, size - margin * 2.4)
        painter.drawRoundedRect(body, size * 0.12, size * 0.12)

        prompt_left = body.left() + body.width() * 0.22
        prompt_middle = body.left() + body.width() * 0.42
        prompt_top = body.top() + body.height() * 0.32
        prompt_bottom = body.top() + body.height() * 0.52
        prompt_mid_y = (prompt_top + prompt_bottom) / 2
        painter.drawLine(QPointF(prompt_left, prompt_top), QPointF(prompt_middle, prompt_mid_y))
        painter.drawLine(QPointF(prompt_middle, prompt_mid_y), QPointF(prompt_left, prompt_bottom))
        painter.drawLine(
            QPointF(body.left() + body.width() * 0.52, prompt_bottom),
            QPointF(body.right() - body.width() * 0.22, prompt_bottom),
        )
    finally:
        painter.end()
    return QIcon(pixmap)


def _draw_map(size: int, accent: QColor) -> QIcon:
    """一张折成三折的地图。**中性的**：不临摹任何一家的商标，只画出「这是一张地图」。"""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(accent)
        pen.setWidthF(max(1.0, size * 0.06))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        margin = size * 0.15
        left, right = margin, size - margin
        top, bottom = margin, size - margin
        step = (right - left) / 3
        fold = (bottom - top) * 0.12

        path = QPainterPath()
        path.moveTo(left, top + fold)
        path.lineTo(left + step, top)
        path.lineTo(left + 2 * step, top + fold)
        path.lineTo(right, top)
        path.lineTo(right, bottom - fold)
        path.lineTo(left + 2 * step, bottom)
        path.lineTo(left + step, bottom - fold)
        path.lineTo(left, bottom)
        path.closeSubpath()
        painter.drawPath(path)

        # 两道折痕：每一道连它那一侧的上下两个折点。
        painter.drawLine(QPointF(left + step, top), QPointF(left + step, bottom - fold))
        painter.drawLine(QPointF(left + 2 * step, top + fold), QPointF(left + 2 * step, bottom))
    finally:
        painter.end()
    return QIcon(pixmap)


class _Segment(QToolButton):
    """分段按钮里那一个。**自己是透明的**，底色、分隔线与箭头都由外层那个容器画。

    ``WA_TranslucentBackground`` 不够 —— QToolButton 自己那一层照样会画底色，与容器那个药丸
    对不上。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAutoRaise(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        owner = self.parent()
        if isinstance(owner, ExternalAppsButton):
            owner._hovered = self
            owner.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        owner = self.parent()
        if isinstance(owner, ExternalAppsButton) and owner._hovered is self:
            owner._hovered = None
            owner.update()
        super().leaveEvent(event)


def order_for_page(preferences: Preferences, page: str) -> tuple[str, ...]:
    """这一页的次序。**视频页最前那一段是播放器**（ADR 0093 / 0095）：用户存下的旧次序里没有
    它们，不补在最前的话，新装的播放器会排到最后。用户自己排过之后以存下的为准。
    """
    ids = preferences.order_for(page)
    if page == EXTERNAL_PAGE_VIDEO:
        missing = tuple(app_id for app_id in VIDEO_LEADERS if app_id not in ids)
        return (*missing, *ids)
    return ids


class ExternalAppsButton(QWidget):
    """标题条上那个入口。**它是两个按钮**，不是一个。

    **左半（图标）**直接打开默认的那一项；**右半（箭头）**弹出那一列 :class:`ExternalEntry`。
    做成一个按钮就挤在一格里，所以它们是两个控件、各自接各自的鼠标事件。容器自己画整个药丸
    （底色、边框、分隔线、箭头），两个分段都透明，于是两段共享同一个圆角轮廓。
    """

    #: 用户选了哪一项（带的是那一项，具体的动作与横幅交给外面）。
    entry_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: tuple[ExternalEntry, ...] = ()
        #: 这一页的次序（应用 id）。**只由 :meth:`set_order` 改**，:meth:`refresh` 照着它排。
        self._order: tuple[str, ...] = ()
        self._hovered: QWidget | None = None

        self._primary = _Segment(self)
        self._primary.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._primary.setIconSize(QSize(ICON_EDGE, ICON_EDGE))
        self._primary.clicked.connect(self._activate_default)

        self._menu_button = _Segment(self)
        self._menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._menu_button.setToolTip(TOOLTIP)
        self._menu_button.clicked.connect(self._popup_menu)
        #: 菜单自己建好并一直用着，:meth:`refresh` 要的是「同一个菜单反复清空重建」。
        self._menu = QMenu(self)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(False)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    # ---- 几何 --------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._place_segments()

    def _place_segments(self) -> None:
        """两半分家。**右半固定那么宽**（:data:`MENU_EDGE`）—— 它只放一个箭头，
        跟着图标走宽了就成了一个空荡荡的方块。"""
        split = max(self.width() - MENU_EDGE, self.width() // 2)
        height = self.height()
        self._primary.setGeometry(0, 0, split, height)
        self._menu_button.setGeometry(split, 0, self.width() - split, height)

    # ---- 外观 --------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """整个药丸：底色 → 悬停的那半提亮 → 边框 → 分隔线 → 箭头。

        **自己画而不是用样式表**：两半要共享一个圆角轮廓与一条分隔线，而样式表里的
        ``border-radius`` 只能各自圆各自的，中间那条线也没有对应的子控件。
        """
        app_theme = theme.current_theme()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            radius = min(BUTTON_RADIUS, self.height() / 2)
            body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

            path = QPainterPath()
            path.addRoundedRect(body, radius, radius)
            painter.fillPath(path, field_background(app_theme))

            # 悬停的那半再凸一点点 —— **只提亮那一半**，于是用户能看出点下去是哪一半。
            if self._hovered is not None:
                split_at = self._primary.width()
                half = QRectF(body)
                if self._hovered is self._primary:
                    half.setRight(min(split_at, body.right()))
                else:
                    half.setLeft(split_at)
                hover = QColor(app_theme.background)
                # **按药丸那条路径裁一下**再填，否则那一块会盖到外侧的两个圆角上。
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(
                    half, hover.lighter(112) if app_theme.is_dark else hover.darker(104)
                )
                painter.restore()

            painter.setPen(QPen(app_theme.text_at(0.28), 1))
            painter.drawPath(path)

            # 分隔线：顶到边框内侧。
            split = self._primary.width()
            pen = QPen(app_theme.text_at(0.22), 1)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(split, 3), QPointF(split, self.height() - 4))
            self._draw_chevron(painter, app_theme.text_at(0.45))
        finally:
            painter.end()

    def _draw_chevron(self, painter: QPainter, color: QColor) -> None:
        """右半那个向下的箭头。**自己画**：Qt 那个菜单指示器一旦被样式表碰过就没了
        （见模块文档），而这里要的是「半格里居中的一个小 V」，不是一个实心三角。"""
        center = self._primary.width() + (self.width() - self._primary.width()) / 2
        middle = self.height() / 2
        half = min(CHEVRON_HALF, (self.width() - self._primary.width()) / 3)
        drop = CHEVRON_DROP
        pen = QPen(color, CHEVRON_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        left, right = QPointF(center - half, middle - drop / 2), QPointF(center, middle + drop / 2)
        painter.drawLine(left, right)
        painter.drawLine(right, QPointF(center + half, middle - drop / 2))

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重画一遍。

        **自绘图标也要重画**：它们跟着 :attr:`AppTheme.text` 变色。菜单那份样式表里硬编码了颜色，
        换主题时不重下发就还是上一套深浅。
        """
        # 菜单是**主题下发之后才建的控件**（``theme.apply_theme`` 只走那一刻已存在的顶层控件），
        # 不补一次调色板就一直用系统默认那一档颜色 —— 深色下菜单里的字在近黑底上几乎看不见。
        theme.push_palette(self._menu)
        self._menu.setStyleSheet(menu_style.popup_menu_style(self._menu))
        self.refresh()
        self.update()

    # ---- 内容 --------------------------------------------------------------

    def set_entries(self, entries: Sequence[ExternalEntry]) -> None:
        """换掉下拉框里那几项。**不立刻建菜单** —— 那要先问一遍 ``enabled``，只在
        :meth:`refresh` 里做。"""
        self._entries = tuple(entries)
        self.refresh()

    def entries(self) -> tuple[ExternalEntry, ...]:
        """建好之后那一整份条目。设置菜单按页挑，用不着再建一份带图标的。"""
        return self._entries

    def set_order(self, ids: Sequence[str]) -> None:
        """这一页的次序（应用 id）。**没提到的项保持内置次序排在后面**，下一次
        :meth:`refresh` 才生效。"""
        self._order = tuple(ids)

    def _ordered(self) -> tuple[ExternalEntry, ...]:
        """按当前次序排好的条目；排序是稳定的，于是没提到的项相对位置不变。"""
        rank = {entry_id: index for index, entry_id in enumerate(self._order)}
        return tuple(sorted(self._entries, key=lambda entry: rank.get(entry.id, len(rank))))

    def default_entry(self) -> ExternalEntry | None:
        """左半那一下打开的是哪一项 —— **按这一页的次序，第一个此刻可用的**。

        用户可以在「设置 › 外部应用顺序」里挪次序，于是左半的图标也跟着换。
        """
        for entry in self._ordered():
            if entry.enabled():
                return entry
        return None

    def default_icon(self) -> QIcon:
        """左半那个图标。**一条都没有时是空图标**（Qt 会画一个占位方框，而那时整个控件已经
        被 :meth:`is_available` 收起来了）。"""
        entry = self.default_entry()
        return (entry.icon or calendar_icon()) if entry is not None else QIcon()

    def refresh(self) -> None:
        """按此刻的可用性重建菜单与左半的图标。**每次翻页、切层都要问一遍**：摆一个点下去
        没反应的灰条目只会让人以为坏了。"""
        self._menu.clear()
        self._primary.setIcon(self.default_icon())
        for entry in self._ordered():
            if not entry.enabled():
                continue
            action = QAction(entry.icon or calendar_icon(), entry.name, self._menu)
            action.triggered.connect(lambda _checked=False, e=entry: self._activate(e))
            self._menu.addAction(action)
        self._primary.setEnabled(self.default_entry() is not None)

    def is_available(self) -> bool:
        """下拉框里此刻**有没有东西**。

        **空着的按钮不该占位置**：没有它们的页面（还没选库的欢迎页）上按钮就整个收起来。
        """
        return any(entry.enabled() for entry in self._entries)

    def _popup_menu(self) -> None:
        """右半那一下弹菜单。

        **不把菜单挂到按钮上**：挂了 Qt 就会在那一格里再画一个它自己的菜单三角，与我们画的
        那个 V 叠在一起。**位置：右缘与按钮右缘对齐，下沿离按钮 :data:`MENU_GAP` 点** ——
        左对齐的话菜单整个跑到搜索框那一侧去了，而用户看的正是右半那个箭头。
        """
        menu = self._menu
        menu.adjustSize()
        corner = self._menu_button.mapToGlobal(QPoint(0, self._menu_button.height()))
        # 屏幕边缘由 ``QMenu.popup`` 自己避开。
        menu.popup(menu_origin(corner, self._menu_button.width(), menu.width()))

    def _activate_default(self) -> None:
        entry = self.default_entry()
        if entry is not None:
            self._activate(entry)

    def _activate(self, entry: ExternalEntry) -> None:
        self.entry_activated.emit(entry)


__all__ = [
    "BUTTON_GAP",
    "BUTTON_WIDTH",
    "ICON_CALENDAR",
    "ICON_EDGE",
    "ICON_FOLDER",
    "ICON_KINDS",
    "ICON_MAP",
    "ICON_TERMINAL",
    "MENU_EDGE",
    "TOOLTIP",
    "ExternalAppsButton",
    "ExternalEntry",
    "button_rect",
    "calendar_icon",
    "drawn_icon",
    "folder_icon",
    "google_maps_icon",
    "icon_for",
    "menu_origin",
    "order_for_page",
]
