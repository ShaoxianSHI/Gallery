"""菜单长什么样：样式表、快捷键列的排布、以及自绘的那几笔。

颜色一律从控件自己的调色板读，那几个角色由 :mod:`gallery.ui.theme` 钉死（ADR 0035）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPalette,
    QPolygonF,
)
from PySide6.QtWidgets import QMenu, QWidget

from gallery.ui import outline
from gallery.ui.theme import rgba

MENU_CORNER_RADIUS = 8

MENU_INSET = 8

MENU_HIGHLIGHT_RADIUS = 5

MENU_ARROW_SIZE = 8

MENU_ARROW_DRAWN_WIDTH = 3.5
MENU_ARROW_DRAWN_HEIGHT = 9.5

MENU_ITEM_PADDING = "5px 12px 5px 0px"

#: 弹出列表那一圈的边框宽度（点）。样式表那边写、摆位置那边算，立成常数是为了两处
#: 不会差一像素 —— 差一点就看见两个「#」错开。
POPUP_LIST_BORDER = 1

#: 弹出列表的行内边距：比菜单那一档（:data:`MENU_INSET`）少 2 —— 列表只有两列、
#: 没有对勾要避让，多留一档只会显得比菜单还松。
POPUP_LIST_PADDING = MENU_INSET - 2

#: 弹出列表里第一列文字离列表左沿多远（点）= 边框 + 内边距。行里那一行文字还要再让开
#: :data:`POPUP_LIST_ITEM_PAD`，两者加起来才是字真正落下的位置。
POPUP_LIST_INSET = POPUP_LIST_BORDER + POPUP_LIST_PADDING

#: 弹出列表那一行文字离行矩形左沿多远（点），决定候选列表里那个「#」落在哪。Qt 画
#: ``::item`` 的文字从行矩形左沿起算；菜单那一档是给「左边留了对勾 gutter」的菜单定的。
POPUP_LIST_ITEM_PAD = 8

#: 朴素下拉菜单里，图标那一侧的 ``margin-left``（点）—— 照真图标量出来的平衡点。**它不是
#: 「想要的间距 × 某个系数」**：``QMenu::item`` 的左内边距只推文字、图标纹丝不动，只有
#: ``QMenu::icon`` 的 ``margin-left`` 才真的移动图标。
POPUP_MENU_ICON_MARGIN = 13

#: 弹出列表与弹出菜单的字号（点）。**只有这一处**：搜索框的候选列表与标题条上那个
#: 下拉菜单是两种控件、两条样式表，而用户在两处之间来回走 —— 各存一份早晚差一档。
POPUP_FONT_PX = 12

MENU_CHECK_GUTTER = 19

MENU_SEPARATOR_HEIGHT = 1
MENU_SEPARATOR_MARGIN = 5

MENU_SEPARATOR_MIX_DARK = 0.2
MENU_SEPARATOR_MIX_LIGHT = 0.333


def separator_color(background: QColor, text: QColor) -> str:
    """分隔线那一档颜色，写成样式表认的 ``rgba(...)``；深浅的判据是底色的明度。"""
    mix = MENU_SEPARATOR_MIX_DARK if background.lightnessF() < 0.5 else MENU_SEPARATOR_MIX_LIGHT
    color = QColor(text)
    color.setAlphaF(mix)
    return rgba(color)


def popup_edge_color(palette: QPalette) -> QColor:
    """浮层那一圈轮廓的颜色，**已经与底色合成过、不透明**。

    两个来源都取自**调用方刚下发的那份调色板**，深浅色自动跟随。合成而不是留半透明的理由见
    :mod:`gallery.ui.outline`：半透明的一圈在八个等分点上会被画两遍、叠成更深的点。
    """
    background = palette.color(QPalette.ColorRole.Window)
    edge = QColor(palette.color(QPalette.ColorRole.WindowText))
    edge.setAlphaF(MENU_SEPARATOR_MIX_LIGHT)  # 与菜单的分隔线同一档
    return outline.opaque_over(edge, background)


def popup_list_style(list_widget: QWidget, *, item_padding: int = POPUP_LIST_ITEM_PAD) -> str:
    """弹出列表（搜索框的候选列表）用的样式表 —— 与弹出菜单同一套颜色，只复用颜色那一层。

    ``item_padding`` 是行里文字离行矩形左沿多远（QSS 调不出像素级对齐，这里是唯一入口）。
    """
    palette = list_widget.palette()
    background = palette.color(QPalette.ColorRole.Window)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    text = palette.color(QPalette.ColorRole.WindowText)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    return (
        f"QTreeWidget#searchPopup {{ background: {rgba(background)}; "
        f"border: {POPUP_LIST_BORDER}px solid {rgba(popup_edge_color(palette))}; "
        f"border-radius: {MENU_CORNER_RADIUS}px; padding: {POPUP_LIST_PADDING}px; }}\n"
        f"QTreeWidget#searchPopup::item {{ padding: 3px {item_padding}px; color: {rgba(text)}; }}\n"
        # 悬停与选中**合成一条规则**：候选列表是「鼠标移到哪一行就高亮哪一行」，两者本是同一
        # 件事。少写一条列表就完全不亮 —— 挂了样式表之后 Qt 不再自己挑高亮色。
        f"QTreeWidget#searchPopup::item:hover, "
        f"QTreeWidget#searchPopup::item:selected {{ background: {rgba(highlight)}; "
        f"color: {rgba(highlighted_text)}; border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}"
    )


def style_sheet(
    background: QColor,
    highlight: QColor,
    text: QColor,
    highlighted_text: QColor,
    *,
    check_gutter: int = 0,
    own_arrow: bool = False,
) -> str:
    """给弹出菜单的那份样式表。

    颜色都由调用方从调色板取原值（``palette(...)`` 读的是**应用**调色板，不是逐控件下发的那份）。
    ``check_gutter`` 只在菜单里没有可勾选项时补；``own_arrow`` 关掉 Qt 的右箭头。
    """
    return (
        f"QMenu {{ border-radius: {MENU_CORNER_RADIUS}px; "
        f"background: {rgba(background)}; padding: {MENU_INSET}px; }}\n"
        f"QMenu::item {{ padding: {MENU_ITEM_PADDING}; }}\n"
        # 悬停 / 当前项的高亮带。**不加这两行菜单就完全没有高亮**（样式表一挂上，系统样式那份
        # 高亮就不再画了）。字色**也要跟着换** —— Qt 不再按 ``State_Selected`` 挑
        # ``HighlightedText``，不写就是黑字压蓝底。置灰的行 Qt 压根不点亮，不必补 ``:disabled``。
        f"QMenu::item:hover {{ background: {rgba(highlight)}; "
        f"color: {rgba(highlighted_text)}; }}\n"
        f"QMenu::item:selected {{ background: {rgba(highlight)}; "
        f"color: {rgba(highlighted_text)}; "
        f"border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}\n"
        # 分隔线。**这条是深色下唯一看得见的那条线**：不给它，颜色由系统样式从应用调色板里挑
        # 一档偏暗的（深色下比底色还深），等于没画。它带 margin 与高亮那条禁令不冲突 ——
        # 那条禁的是**带状态**的规则里写 margin，这里与悬停无关。
        f"QMenu::separator {{ height: {MENU_SEPARATOR_HEIGHT}px; "
        f"margin: {MENU_SEPARATOR_MARGIN}px 0px; "
        f"background: {separator_color(background, text)}; }}\n"
        + (f"QMenu::item {{ padding-left: {check_gutter}px; }}" if check_gutter else "")
        + (
            "\nQMenu::right-arrow { width: 0; height: 0; }"
            if own_arrow
            else f"\nQMenu::right-arrow {{ width: {MENU_ARROW_SIZE}px; "
            f"height: {MENU_ARROW_SIZE}px; }}"
        )
    )


def _owned_menus(menu: QMenu) -> Iterator[QMenu]:
    """这个菜单自己拥有的那一串菜单：它，加上它的各级子菜单。

    判据是子菜单的 ``menuAction()`` 是不是当前 action（``QAction`` 共用，``menu()`` 可能指向别处）。
    """
    seen: set[int] = set()
    pending = [menu]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for action in current.actions():
            submenu = action.menu()
            if submenu is not None and submenu.menuAction() is action:
                pending.append(submenu)


MODIFIER_ORDER = (
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.ShiftModifier,
    Qt.KeyboardModifier.AltModifier,
    Qt.KeyboardModifier.MetaModifier,
)

_MODIFIER_PROBE = {
    Qt.KeyboardModifier.ControlModifier: "Ctrl+A",
    Qt.KeyboardModifier.ShiftModifier: "Shift+A",
    Qt.KeyboardModifier.AltModifier: "Alt+A",
    Qt.KeyboardModifier.MetaModifier: "Meta+A",
}

SHORTCUT_GAP = 3
SHORTCUT_RIGHT_INSET = 12

SHORTCUT_LABEL_GAP = 20


def _text_role(action: QAction, active: QAction | None) -> QPalette.ColorRole:
    """这一行的文字该用哪一档颜色 —— 悬停 / 正常 / 置灰。选中那一档必须与样式表的 ``:selected``
    字色同源，否则一行左半边是 Qt 画的、右半边是自绘的，两色对不上。
    """
    if action is active:
        return QPalette.ColorRole.HighlightedText
    return QPalette.ColorRole.Text if action.isEnabled() else QPalette.ColorRole.PlaceholderText


def _modifier_glyph(modifier: Qt.KeyboardModifier) -> str:
    """一个修饰符在系统写法下的符号（``⌘`` / ``⇧`` / ``⌥`` / ``⌃``）。"""
    text = QKeySequence(_MODIFIER_PROBE[modifier]).toString(QKeySequence.SequenceFormat.NativeText)
    return text[:-1] if text.endswith("A") else text


def _native(key: Qt.Key | int) -> str:
    """一个键在系统写法下长什么样（``⌫`` / ``⎋`` / ``C``）。"""
    return QKeySequence(int(key)).toString(QKeySequence.SequenceFormat.NativeText)


OPEN_KEY_HINT = _native(Qt.Key_Return)

CLOSE_KEY_HINT = _native(Qt.Key_Escape)


def split_shortcut(shortcut: QKeySequence) -> tuple[frozenset[Qt.KeyboardModifier], str]:
    """把快捷键拆成「有哪些修饰符」与「那个键本身」。

    只认第一个组合：其余的是同一快捷键的其他写法，一并显示只会互相打架。
    """
    if shortcut.isEmpty() or shortcut.count() == 0:
        return frozenset(), ""
    combination = shortcut[0]
    modifiers = combination.keyboardModifiers().value
    return (
        frozenset(bit for bit in MODIFIER_ORDER if modifiers & bit.value),
        _native(combination.key()),
    )


@dataclass(frozen=True, slots=True)
class ShortcutColumns:
    """快捷键那几列的尺寸：字母一列 + 各修饰符一列，整个菜单共用同一套列宽。
    落在哪一列由 :meth:`cells` 按「这一行用到的修饰符从里往外依次填」决定。
    """

    key_width: int
    modifier_widths: Mapping[Qt.KeyboardModifier, int]

    @classmethod
    def measure(cls, shortcuts: Iterable[QKeySequence], metrics: QFontMetrics) -> ShortcutColumns:
        return cls.measure_parts((split_shortcut(s) for s in shortcuts), metrics)

    @classmethod
    def measure_parts(
        cls,
        parts: Iterable[tuple[frozenset[Qt.KeyboardModifier], str]],
        metrics: QFontMetrics,
    ) -> ShortcutColumns:
        """按已经拆开的「修饰符 + 键位」量一次：:meth:`measure` 只认 ``QKeySequence``，
        只挂提示、没有快捷键的行只能从这里进。
        """
        parts = list(parts)
        return cls(
            key_width=max(
                (metrics.horizontalAdvance(key_text) for _, key_text in parts if key_text),
                default=0,
            ),
            # 只给这个菜单里真用到的修饰符留列：空白处那张菜单只有 ⌘ 与无修饰的 ⎋，
            # 硬留四列就是白占三十几像素。
            modifier_widths={
                bit: metrics.horizontalAdvance(_modifier_glyph(bit))
                for bit in MODIFIER_ORDER
                if any(bit in present for present, _ in parts)
            },
        )

    @property
    def width(self) -> int:
        """列块一共占多宽（菜单要为它预留这么多）。"""
        return (
            self.key_width
            + sum(self.modifier_widths.values())
            + SHORTCUT_GAP * (len(self.modifier_widths) + 1)
        )

    def key_rect(self, right: int, top: int, height: int) -> QRect:
        """字母那一格（最右，**所有行共用同一列**）。"""
        return QRect(right - SHORTCUT_RIGHT_INSET - self.key_width, top, self.key_width, height)

    def slot_rects(self, right: int, top: int, height: int) -> list[QRect]:
        """这一整块里的每一格：先字母，再各修饰符列，从右往左。位置对所有行都是同一套。"""
        rects = [self.key_rect(right, top, height)]
        edge = rects[0].left() - SHORTCUT_GAP
        for width in self.modifier_widths.values():
            edge -= width
            rects.append(QRect(edge, top, width, height))
            edge -= SHORTCUT_GAP
        return rects

    def cells(
        self,
        modifiers: frozenset[Qt.KeyboardModifier],
        right: int,
        top: int,
        height: int,
    ) -> tuple[QRect, dict[Qt.KeyboardModifier, QRect]]:
        """这一行里各格的矩形：字母一格，外加这一行用到的修饰符。

        **修饰符从字母那一侧往里依次填**（顺序见 :data:`MODIFIER_ORDER`），不是每个修饰符
        占自己那一列；每格按右对齐画，字宽不同也不参差。
        """
        rects = self.slot_rects(right, top, height)
        placed: dict[Qt.KeyboardModifier, QRect] = {}
        for index, modifier in enumerate(bit for bit in MODIFIER_ORDER if bit in modifiers):
            placed[modifier] = rects[index + 1]
        return rects[0], placed


class ShortcutColumnMenu(QMenu):
    """弹出菜单：快捷键按「字母一列 + 修饰符各占一列」自己画在行右沿。

    样式表一挂到 ``QMenu`` 上 Qt 就不再给快捷键算位置，它总是紧跟在文字后面。画之前得先
    :meth:`reserve_shortcut_columns` 预留宽度，否则列块压在标签上。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: 只画不生效的键位提示：动作 → 那一列要写的字。**不挂在动作上**，所以共用同一份
        #: ``QAction`` 的菜单栏那边看不到它（见 :meth:`show_key`）。
        self._key_hints: dict[QAction, str] = {}

    def show_key(self, action: QAction, text: str) -> None:
        """给某一行挂一枚**只画不生效**的键位提示，画在最右那一列（见 :data:`OPEN_KEY_HINT`）。

        刻意不去 ``setShortcut``：``QAction`` 是共用的，挂上去就成了整个窗口的快捷键。
        """
        self._key_hints[action] = text

    def key_cell(self, action: QAction) -> tuple[frozenset[Qt.KeyboardModifier], str] | None:
        """这一行要画进最右列的内容：修饰符集合 + 键位文字；没有就返回 ``None``。"""
        hint = self._key_hints.get(action)
        if hint is not None:
            return frozenset(), hint
        modifiers, key_text = split_shortcut(action.shortcut())
        return (modifiers, key_text) if key_text else None

    def key_cells(self) -> list[tuple[frozenset[Qt.KeyboardModifier], str]]:
        """整张菜单里要画进最右列的内容（分隔线与空白行自然被滤掉）。"""
        return [cell for cell in map(self.key_cell, self.actions()) if cell is not None]

    def has_submenu_arrows(self) -> bool:
        """这张菜单里有没有子菜单行。"""
        return any(action.menu() is not None for action in self.actions())

    def owns_submenu_arrows(self) -> bool:
        """子菜单的右箭头是否由本模块自绘：有子菜单行，**而且**最右列确实有东西（箭头才有地方对齐）。
        只满足前一个就自绘不行：:func:`apply_style` 会在同一判断上把 Qt 那个关掉，结果谁也不画。
        """
        return self.has_submenu_arrows() and bool(self.key_cells())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().paintEvent(event)
        rows = [action for action in self.actions() if not action.isSeparator()]
        metrics = QFontMetrics(self.font())
        cells = self.key_cells()
        if not cells:
            return
        columns = ShortcutColumns.measure_parts(cells, metrics)
        align = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        # 这三样**与哪一行无关**，放进循环就是白算：``owns_submenu_arrows()`` 会走到
        # ``split_shortcut()``（每个 action 一次平台字符串转换），``activeAction()`` 一悬停就变。
        owns_arrows = self.owns_submenu_arrows()
        active = self.activeAction()
        palette = self.palette()
        painter = QPainter(self)
        try:
            for action in rows:
                cell = self.key_cell(action)
                if cell is None:
                    if owns_arrows and action.menu() is not None:
                        self._paint_arrow(action, columns, painter, palette, active)
                    continue
                modifiers, key_text = cell
                rect = self.actionGeometry(action)
                key_rect, modifier_rects = columns.cells(
                    modifiers, rect.right(), rect.top(), rect.height()
                )
                painter.setPen(palette.color(_text_role(action, active)))
                painter.drawText(key_rect, align, key_text)
                for modifier, modifier_rect in modifier_rects.items():
                    painter.drawText(modifier_rect, align, _modifier_glyph(modifier))
        finally:
            painter.end()

    def _paint_arrow(
        self,
        action: QAction,
        columns: ShortcutColumns,
        painter: QPainter,
        palette: QPalette,
        active: QAction | None,
    ) -> None:
        """子菜单的右箭头画在**字母列**上 —— 原生菜单里它与快捷键的键位是同一列。
        Qt 那个位置在字母列右边且 QSS 挪不动它，所以关掉 Qt 的改由自绘；``painter`` 等
        参数由 :meth:`paintEvent` 递进来。
        """
        rect = self.actionGeometry(action)
        key_rect = columns.key_rect(rect.right(), rect.top(), rect.height())
        center = key_rect.center()
        half_width = MENU_ARROW_DRAWN_WIDTH / 2
        half_height = MENU_ARROW_DRAWN_HEIGHT / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.color(_text_role(action, active)))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center.x() - half_width, center.y() - half_height),
                    QPointF(center.x() + half_width, center.y()),
                    QPointF(center.x() - half_width, center.y() + half_height),
                ]
            )
        )
        # **这里不许 ``painter.end()``** —— 那支笔是 :meth:`paintEvent` 的，收尾归它的
        # ``finally``。一收，后面所有行都画不出来（``setPen`` / ``drawText`` 静默失效）。

    def reserve_shortcut_columns(self) -> None:
        """把菜单撑到「最长标签 + 最右列」那么宽（那一列空着就什么也不做）。"""
        cells = self.key_cells()
        if not cells:
            return
        metrics = QFontMetrics(self.font())
        columns = ShortcutColumns.measure_parts(cells, metrics)
        labels = max(
            metrics.horizontalAdvance(action.text())
            for action in self.actions()
            if not action.isSeparator()
        )
        # 文字左边距恒为 MENU_INSET + MENU_CHECK_GUTTER：有可勾选项时那 19 像素是 Qt 自己
        # 给对勾留的，没有时是样式表补的（见 style_sheet 的 check_gutter）—— **两种都得算**。
        left = MENU_INSET + MENU_CHECK_GUTTER
        # 这一段必须是**最小宽度**：标签越长菜单越宽，而列块是钉在右沿上的，宽度不够
        # 它就会压到标签上。
        self.setMinimumWidth(
            int(
                labels
                + SHORTCUT_LABEL_GAP
                + left
                + columns.width
                + SHORTCUT_RIGHT_INSET
                + MENU_INSET
            )
        )


def popup_menu_style(menu: QMenu) -> str:
    """一个**朴素的下拉菜单**该用的样式表 —— 与搜索框的候选列表是同一种东西。

    差别都来自「它只有一条两项」：不预留勾选槽、字号与行内边距取候选列表那一档、高亮带圆角、带边框。
    """
    palette = menu.palette()
    text = palette.color(QPalette.ColorRole.WindowText)
    return (
        style_sheet(
            palette.color(QPalette.ColorRole.Window),
            palette.color(QPalette.ColorRole.Highlight),
            text,
            palette.color(QPalette.ColorRole.HighlightedText),
            check_gutter=0,
        )
        + f"\nQMenu {{ font-size: {POPUP_FONT_PX}px;"
        f" border: {POPUP_LIST_BORDER}px solid {rgba(popup_edge_color(palette))}; }}"
        # 字色**必须自己写**：挂了样式表之后 Qt 不再按调色板给 item 上色（深色下调色板里明明是
        # ``#ffffff``，画出来却是近黑的 ``#000000``）。内边距**左右同档** —— 左内边距只管
        # 文字，图标那一侧归下面的 ``::icon`` 规则。
         + f"\nQMenu::item {{ padding: 3px {POPUP_LIST_ITEM_PAD}px;"
        f" color: {rgba(text)}; border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}"
        + f"\nQMenu::icon {{ margin-left: {POPUP_MENU_ICON_MARGIN}px; }}"
    )


def apply_style(menu: QMenu) -> None:
    """给菜单与它的子菜单上同一份样式表。

    四个颜色都从菜单自己的调色板读（调用方刚把统一调色板下发到菜单上），深浅色自动跟随。
    """
    palette = menu.palette()
    background = palette.color(QPalette.ColorRole.Window)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    text = palette.color(QPalette.ColorRole.WindowText)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    # 没有可勾选项的菜单要自己补上那一列，样式表因此**逐个菜单**算 —— 子菜单里是
    # 「排序 / 分组 / 缩略图大小」那些单选项，它们有勾、就不补。
    for current in _owned_menus(menu):
        has_checkable = any(action.isCheckable() for action in current.actions())
        owns_arrows = isinstance(current, ShortcutColumnMenu) and current.owns_submenu_arrows()
        current.setStyleSheet(
            style_sheet(
                background,
                highlight,
                text,
                highlighted_text,
                check_gutter=0 if has_checkable else MENU_CHECK_GUTTER,
                own_arrow=owns_arrows,
            )
        )


def prepare(menu: QMenu) -> None:
    """接管快捷键：关掉 Qt 自己画的那一列，并给自绘的列块预留宽度。

    不关的话 Qt 会把快捷键跟在文字后面再画一遍；不预留宽度就压在标签上。
    """
    for current in _owned_menus(menu):
        for action in current.actions():
            if not action.isSeparator() and not action.shortcut().isEmpty():
                action.setShortcutVisibleInContextMenu(False)
        if isinstance(current, ShortcutColumnMenu):
            current.reserve_shortcut_columns()


__all__ = [
    "CLOSE_KEY_HINT",
    "MENU_ARROW_DRAWN_HEIGHT",
    "MENU_ARROW_DRAWN_WIDTH",
    "MENU_ARROW_SIZE",
    "MENU_CHECK_GUTTER",
    "MENU_CORNER_RADIUS",
    "MENU_HIGHLIGHT_RADIUS",
    "MENU_INSET",
    "MENU_ITEM_PADDING",
    "MENU_SEPARATOR_HEIGHT",
    "MENU_SEPARATOR_MARGIN",
    "MENU_SEPARATOR_MIX_DARK",
    "MENU_SEPARATOR_MIX_LIGHT",
    "MODIFIER_ORDER",
    "OPEN_KEY_HINT",
    "POPUP_LIST_BORDER",
    "POPUP_LIST_INSET",
    "POPUP_LIST_ITEM_PAD",
    "POPUP_LIST_PADDING",
    "POPUP_MENU_ICON_MARGIN",
    "SHORTCUT_GAP",
    "SHORTCUT_LABEL_GAP",
    "SHORTCUT_RIGHT_INSET",
    "ShortcutColumnMenu",
    "ShortcutColumns",
    "apply_style",
    "popup_edge_color",
    "popup_list_style",
    "prepare",
    "separator_color",
    "split_shortcut",
    "style_sheet",
]
