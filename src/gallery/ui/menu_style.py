"""菜单**长什么样** —— 那一件东西的样式、快捷键列的排布、以及我们自己画的那几笔。

与 :mod:`gallery.ui.menus` 是同一件事的两半，判据是**「改它会不会牵连另一个包」**：

- 这里答的是「菜单怎么画」。菜单栏、右键菜单、菜单里的子菜单**全都是同一副样子**，
  所以它不属于 ``browser/`` 也不属于 ``viewer/`` —— 与 ``theme.py`` 同类，
  是整个界面共用的那一层外观。
- :mod:`gallery.ui.menus` 答的是「菜单里该有哪些条目」，那是产品行为，
  由 ADR 0014 / 0018 / 0021 / 0023 钉死。

过去这两半在同一个文件里（``menus.py`` 1148 行），于是「菜单里有什么」与「菜单怎么画」
互相牵连：改一个分组顺序要读一整段 ``paintEvent``，改一次圆角要去数 ``_add_*`` 有几处。

三条要守着的：

- **颜色一律从控件自己的调色板读**，不另外传进来（见 :func:`apply_style`）。
- **符号问 Qt 要，不查表**（见 :func:`_native`）。
- :func:`ShortcutColumnMenu` 自绘快捷键列，因此**箭头要自己画**且画在字母列上
  （见 :meth:`ShortcutColumnMenu.owns_submenu_arrows` 与 :func:`apply_style` 里那段注释）。
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

from gallery.ui.theme import rgba

MENU_CORNER_RADIUS = 8

MENU_INSET = 8

MENU_HIGHLIGHT_RADIUS = 5

MENU_ARROW_SIZE = 8

MENU_ARROW_DRAWN_WIDTH = 3.5
MENU_ARROW_DRAWN_HEIGHT = 9.5

MENU_ITEM_PADDING = "5px 12px 5px 0px"

MENU_CHECK_GUTTER = 19

MENU_SEPARATOR_HEIGHT = 1
MENU_SEPARATOR_MARGIN = 5

MENU_SEPARATOR_MIX_DARK = 0.2
MENU_SEPARATOR_MIX_LIGHT = 0.333


def separator_color(background: QColor, text: QColor) -> str:
    """分隔线那一档颜色，写成样式表认的 ``rgba(...)``（见 :data:`MENU_SEPARATOR_MIX_DARK`）。

    深浅的判据是底色的明度，与 :attr:`gallery.ui.theme.AppTheme.is_dark` 同一个。
    """
    mix = MENU_SEPARATOR_MIX_DARK if background.lightnessF() < 0.5 else MENU_SEPARATOR_MIX_LIGHT
    color = QColor(text)
    color.setAlphaF(mix)
    return rgba(color)


def popup_list_style(list_widget: QWidget) -> str:
    """弹出列表（搜索框的候选列表）用的样式表 —— **与弹出菜单同一套颜色**。

    四个角色与 :func:`style_sheet` 取自同一个地方（控件自己的调色板，见模块文档头一条），
    于是候选列表与右键菜单是同一种底色、同一种悬停高亮、同一种圆角：用户从菜单移到
    搜索框，看到的不是「另一种东西」。

    **列表不是菜单**，因此不共用那一整份样式表：菜单的行有快捷键列、有分隔线、有子菜单
    箭头（``ShortcutColumnMenu`` 自己画那几笔），列表两样都没有。这里复用的只是**颜色**
    与圆角那一层，行的内边距另给。

    :param list_widget: 那个列表本身。颜色从**它自己的**调色板读（系统换浅色 / 深色时
        :mod:`gallery.ui.theme` 会逐控件下发），所以调用方只要把控件给过来。
    """
    palette = list_widget.palette()
    background = palette.color(QPalette.ColorRole.Window)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    text = palette.color(QPalette.ColorRole.WindowText)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    edge = QColor(text)
    edge.setAlphaF(MENU_SEPARATOR_MIX_LIGHT)  # 边框与菜单的分隔线同一档
    return (
        f"QTreeWidget#searchPopup {{ background: {rgba(background)}; "
        f"border: 1px solid {rgba(edge)}; "
        f"border-radius: {MENU_CORNER_RADIUS}px; padding: {MENU_INSET - 2}px; }}\n"
        f"QTreeWidget#searchPopup::item {{ padding: 3px 8px; color: {rgba(text)}; }}\n"
        # 悬停与选中**合成一条规则**：候选列表是「鼠标移到哪一行就高亮哪一行」
        # （``search_field.CandidateList.mouseMoveEvent`` 把那一行设成当前项），
        # 两者本是同一件事。少写一条的症状是列表完全不亮 —— 挂了样式表之后 Qt 不再
        # 按 ``State_MouseOver`` 自己去挑高亮色（与 :func:`style_sheet` 里那条菜单的
        # 注释是同一件事）。
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

    四个颜色都必须由调用方给**调色板里的原值**：底色是「一整块底色」那块（``Window``
    角色），高亮是系统强调色（``Highlight`` 角色），正文色是 ``WindowText`` 角色，
    高亮之下那一档字色是 ``HighlightedText`` —— 分隔线的颜色由正文色按透明度压出来
    （见 :func:`separator_color`）。理由见模块文档那一段 ——
    样式表里的 ``palette(...)`` 取的是**应用**调色板，不是逐控件下发的那份。

    ``check_gutter`` 是「这一列对勾原本会被预留的宽度」，只有**菜单里没有可勾选项**时才
    补上（见 :data:`MENU_CHECK_GUTTER`）。它排在最后一条规则，靠覆盖前面那条
    ``QMenu::item`` 的左内边距生效。

    ``own_arrow`` 表示「子菜单的右箭头由我们自己画」（见 :class:`ShortcutColumnMenu`），
    于是把 Qt 那一个**关掉**。关是能关掉的：``::right-arrow`` 只对 ``width`` / ``height``
    有反应（实测把 ``padding-right`` 从 12 加到 28，箭头纹丝不动，仍在 x=148~151.5），
    尺寸给 0 之后它就完全不画了。
    """
    return (
        f"QMenu {{ border-radius: {MENU_CORNER_RADIUS}px; "
        f"background: {rgba(background)}; padding: {MENU_INSET}px; }}\n"
        f"QMenu::item {{ padding: {MENU_ITEM_PADDING}; }}\n"
        # 悬停 / 当前项的高亮带。**不加这两行，菜单就完全没有高亮**：样式表一旦挂上，
        # 系统样式那份高亮就不再画了（实测原生菜单按方向键会点亮 #548fff，加了样式表
        # 之后按 ↓ 两次所有行仍是 #ffffff），用户就看不出鼠标在哪一行。
        #
        # 字色**也要跟着换**，否则浅色下是黑字压蓝底：挂了样式表之后 Qt 不再按
        # ``State_Selected`` 去挑 ``HighlightedText``（实测未选中行与选中行都是 #000000，
        # 而调色板里的 ``HighlightedText`` 是 #ffffff）。我们自己画的快捷键列早就是按
        # ``_text_role`` 跟着换的，于是那一行左半边白、右半边黑 —— 用户报的就是这个。
        #
        # 置灰的行不受影响：**Qt 压根不点亮它们**（实测 ``setActiveAction`` 直接把置灰行
        # 拒掉，鼠标悬过去底色也仍是原来的白色），所以这里不必再补一条 ``:disabled``
        # 把字色按回去 —— 那条规则永远不会被命中。
        f"QMenu::item:hover {{ background: {rgba(highlight)}; "
        f"color: {rgba(highlighted_text)}; }}\n"
        f"QMenu::item:selected {{ background: {rgba(highlight)}; "
        f"color: {rgba(highlighted_text)}; "
        f"border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}\n"
        # 分隔线。**这条是深色下唯一看得见的那条线**：不给它，颜色由系统样式从应用
        # 调色板里挑一档偏暗的（深色下 #0f0f0f，比底色还深），于是等于没画。
        #
        # 它带 margin —— 与高亮带那条禁令不冲突：那条禁的是**带状态**的规则里写 margin
        # （每悬停一次换一次盒子，文字跟着挪），这里的状态与悬停无关。
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
    """这个菜单**自己**拥有的那一串菜单：它，加上它的各级子菜单。

    **必须认所有权**：``QAction`` 是全局复用的（菜单栏与右键菜单共用同一份），
    ``some_action.menu()`` 可能指向别处的菜单。判据是「这个子菜单的 ``menuAction()``
    是不是当前这条 action」—— 只有那样它才真的长在这个菜单里。否则会把「视图」菜单
    下的排序 / 分组 / 缩略图大小一并算进来，而它们并不属于这次右键。

    **子菜单也不是它的子控件**（实测 ``QMenu.addMenu(另一个 QMenu)`` 不给后者设父对象，
    ``findChildren`` 找不到它，``sub.styleSheet()`` 是空的），所以只能顺着 action 找。
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
    """这一行的文字该用哪一档颜色 —— 悬停 / 正常 / 置灰。

    原本在 :meth:`ShortcutColumnMenu.paintEvent` 与 :meth:`_paint_arrow` 里各写一遍。

    选中时那一档**必须与样式表里 ``:selected`` 的字色同源**（都是 ``HighlightedText``，
    见 :func:`style_sheet`）：一行左半边是 Qt 画的条目文字、右半边是我们自己画的快捷键，
    两边不同色就是「一半白一半黑」那一眼。
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

    Qt 6 把修饰符与按键合进了同一个 ``QKeyCombination``，但它自己分得很干净
    （``keyboardModifiers()`` 与 ``key()``），这里不必去解析「⇧⌘C」这样的字符串。

    只认**第一个**组合：菜单里显示的是这一个能按出来的键组，``QKeySequence`` 里其余的
    是同一快捷键的其他写法（选单键 / 选全键），一并显示只会互相打架。
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
    """快捷键那几列的尺寸：字母一列 + 各种修饰符各一列，**整个菜单共用同一套列宽**。

    列宽只按整个菜单里最宽的量一次，所有行都按它摆 —— ⌘R / ⌘⌫ / ⌘I 的 ⌘ 落在同一列上，
    ⇧⌘C 的 ⇧ 与 ⌥ 也落在同一列上，字母则一律贴着右沿。

    **符号落在哪一列，是按这一行用到的修饰符「从里往外依次填」决定的**（见 :meth:`cells`）：
    位置不是每个修饰符钉死的，只有那一组列本身钉死。
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
        """按**已经拆开**的「修饰符 + 键位」量一次（拆法见 :func:`split_shortcut`）。

        :meth:`measure` 只认 ``QKeySequence``，而菜单里还可能有只挂了一枚提示、**压根没有
        快捷键**的行（见 :meth:`ShortcutColumnMenu.show_key`）—— 那些量不到，只能从这里进。
        """
        parts = list(parts)
        return cls(
            key_width=max(
                (metrics.horizontalAdvance(key_text) for _, key_text in parts if key_text),
                default=0,
            ),
            # **只给这个菜单里真用到的修饰符留列**：空白处那张菜单只有 ⌘ 与无修饰的 ⎋，
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
        """这一整块里的每一格：**先字母，再各修饰符列，从右往左**。

        这几格的**位置对所有行都是同一套**，钉死的也是它们 —— 每一行落到哪一格由
        :meth:`cells` 决定。菜单要给整块留宽时也用它（见 :meth:`width`）。
        """
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

        **修饰符从字母那一侧往里依次填**（填的顺序是 :data:`MODIFIER_ORDER`：⌘ 最靠里，
        然后 ⇧、⌥、⌃），而不是「每个修饰符占自己那一列」。于是：

        - ``⌘C`` 与 ``⇧⏎`` 的修饰符**都贴在字母旁边** —— 它们各只有一个修饰符，
          孤零零地待在外侧那一列，中间隔着一格空白，看着像多出来一列；
        - ``⇧⌘C`` 两个都要，⌘ 靠里、⇧ 退到外一格，仍然列列对齐。

        代价是同一张菜单里同一个符号可能出现在两列上（照片菜单：``⇧⏎`` 的 ⇧ 与 ``⇧⌘C``
        的 ⇧）。这与访达那种「整个键组贴着右沿」的排法是同一件事，而菜单栏那一侧仍是
        原生的右对齐。

        每格都按右对齐画而不是按字宽推位置：同一列里的字形宽窄不同（C / R / I / ⌫），
        按字宽推必然参差，而右对齐时 Qt 把文字贴到格子右沿，一像素不差。
        """
        rects = self.slot_rects(right, top, height)
        placed: dict[Qt.KeyboardModifier, QRect] = {}
        for index, modifier in enumerate(bit for bit in MODIFIER_ORDER if bit in modifiers):
            placed[modifier] = rects[index + 1]
        return rects[0], placed


class ShortcutColumnMenu(QMenu):
    """弹出菜单：快捷键按「字母一列 + 修饰符各占一列」自己画在行右沿。

    **为什么不用 Qt 自己画**：样式表一挂到 ``QMenu`` 上，Qt 就不再给快捷键算位置了 ——
    实测无论有没有 ``QMenu::item`` 规则、右内边距取多少，它都紧跟在文字后面（右沿恒为
    78.5），而原生菜单里它贴着右沿。``setShortcut`` 一直都在设，问题是出在**画**的那一步，
    QSS 调不动，只能自己画。

    **哪一行落到哪一列**由 :meth:`ShortcutColumns.cells` 决定：这一行用到的修饰符从字母
    那一侧往里依次填（见那里的说明）。

    画之前得先给这一块**预留宽度**（见 :meth:`reserve_shortcut_columns`）：快捷键不在条目
    文字里，Qt 不会替它腾地方，不预留就压在标签上（实测菜单宽 143、列块要画到 x≈100，
    而「在访达中显示」一直伸到 98；预留之后 218）。

    那一列里还可能有**只画不生效**的提示（见 :meth:`show_key`）：它一样要占位置、一样要
    参与量列宽，只是不来自 ``QAction``。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: 只画不生效的键位提示：动作 → 那一列要写的字。**不挂在动作上**，所以共用同一份
        #: ``QAction`` 的菜单栏那边看不到它（见 :meth:`show_key`）。
        self._key_hints: dict[QAction, str] = {}

    def show_key(self, action: QAction, text: str) -> None:
        """给某一行挂一枚**只画不生效**的键位提示，画在最右那一列（见 :data:`OPEN_KEY_HINT`）。

        **刻意不去 ``setShortcut``**：``QAction`` 是菜单栏与右键菜单共用的，挂在动作上
        就成了整个窗口的快捷键 —— 而回车网格自己已经处理了（``PhotoGrid.keyPressEvent``），
        再抢一遍就要和它打架。菜单是**用完即弃**的，提示挂在这一次的那份菜单上，正合适。

        因此那一行**没有** ``QAction`` 快捷键，:func:`prepare` 也就没什么要关的 ——
        Qt 本来就不会画它（它画的是 ``QAction`` 上的快捷键），两个版本不会重叠。
        """
        self._key_hints[action] = text

    def key_cell(self, action: QAction) -> tuple[frozenset[Qt.KeyboardModifier], str] | None:
        """这一行要画进最右列的内容：修饰符集合 + 键位文字。没有就返回 ``None``。

        带快捷键的按快捷键拆；只挂了一枚提示的没有修饰符，键位就是提示本身。
        """
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
        """子菜单的右箭头是否**由我们画**。

        两个条件缺一不可：有子菜单行（有箭头可画），**而且**这张菜单里最右列确实有东西
        （字母列存在，箭头才有地方对齐）。只满足前一个就自己画是不行的 —— :func:`apply_style`
        会在同一个判断上把 Qt 那个关掉，结果箭头谁也不画，整块消失。
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
        # 这三样**与哪一行无关**，却原来在循环里一遍遍重算：
        # ``owns_submenu_arrows()`` 一路走到 ``key_cells()`` 与 ``split_shortcut()``，
        # 也就是每个 action 都要做一次平台字符串转换（``QKeySequence.toString``）；
        # ``activeAction()`` 同理，而它一悬停就变。
        # 一次 ``QPainter`` 覆盖整个菜单，而不是一行一个（每行都要重新问一次设备、
        # 重新置一次变换）。
        #
        # **它因此也是整张菜单唯一的笔**：中途 ``end()`` 掉，后面的行就全成了哑的（见
        # :meth:`_paint_arrow`），而症状只是「有几行没画」—— 所以收尾只在这一处。
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

        不画在 Qt 那个位置（行右沿内侧约 5 像素）：那在字母列右边，两者看着是两个不相干
        的东西。QSS 挪不动它（``padding-right`` 从 12 加到 28，实测箭头仍在 x=148~151.5），
        所以是关掉 Qt 那个（``::right-arrow`` 尺寸给 0 就完全不画）改由我们画。

        ``painter`` / ``palette`` / ``active`` 由 :meth:`paintEvent` 递进来：这个菜单
        一次绘制只建一个 ``QPainter``、只取一次调色板、只问一次当前项。
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
        # ``finally``（它建它收）。这里一收，后面所有行就都画不出来了：``QPainter`` 一旦
        # ``end()``，``setPen`` / ``drawText`` / ``drawPolygon`` 全部静默失效（只在 stderr
        # 留一句 ``QPainter::setPen: Painter not active``，界面上看不出来）。实测那正是
        # 「排序 / 缩略图大小 / 移动到相册没有右箭头」的成因：同一张菜单里**第一个**子菜单
        # 行（分组 / 复制到相册）画得出箭头，它之后的每一行都画不出 —— 连排在它们后面的
        # 快捷键（⌘R / ⌘⌫ / ⌘I / ⎋）也一起没了。

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
        # 文字左边距恒为 MENU_INSET + MENU_CHECK_GUTTER：有可勾选项时那 19 像素是 Qt
        # 自己给对勾留的，没有时是样式表补的（见 style_sheet 的 check_gutter）——
        # **两种情况都得算**，只算一种的话列块会贴着文字（实测空白处菜单只差 2 像素）。
        left = MENU_INSET + MENU_CHECK_GUTTER
        # 这一段必须是**最小宽度**：标签越长菜单越宽，而列块是钉在右沿上的，宽度不够
        # 它就会压到标签上（实测空白处菜单自然宽 124，列块左沿 62，而「缩略图大小」
        # 的文字一直伸到 90 —— 两者几乎同一条 x）。
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


def apply_style(menu: QMenu) -> None:
    """给菜单与它的子菜单上同一份样式表。

    四个颜色都**从菜单自己的调色板读**，不另外传进来：调用方刚把窗口那份统一调色板
    下发到菜单上（``MainWindow.context_menu_for``），于是这里读到的底色就是「一整块
    底色」那块，而高亮色是系统给的强调色 —— 也就是说这份样式表用的是系统自己那套配色，
    深浅色自动跟随（理由见模块文档那一段：样式表里的 ``palette(...)`` 取的是应用调色板，
    不是这份逐控件下发的）。分隔线的颜色由底色与正文色配出来（见 :func:`separator_color`），
    高亮之下那一档字色则直接取配对的那个 ``HighlightedText``，因此深浅两套也跟着一起变。

    子菜单那份样式发不发得下去，理由见 :func:`_owned_menus`（不继承、也不是子控件）。
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
    """接管快捷键：关掉 Qt 自己画的那一列，并给我们的列块预留宽度。

    两件事都得做，缺一不可：

    * **关掉它自己画的**：不关的话 Qt 会把快捷键紧跟在文字后面再画一遍，和我们那几列
      重叠。这个标志是**长期生效**的（动作是菜单栏与右键菜单共用的），但只影响弹出菜单
      的显示 —— 菜单栏那边原生的照旧，``shortcut`` 本身也没动，按键照常触发。
    * **预留宽度**：快捷键不在条目文字里，Qt 不会替它腾地方，不预留就压在标签上。
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
    "SHORTCUT_GAP",
    "SHORTCUT_LABEL_GAP",
    "SHORTCUT_RIGHT_INSET",
    "ShortcutColumnMenu",
    "ShortcutColumns",
    "apply_style",
    "popup_list_style",
    "prepare",
    "separator_color",
    "split_shortcut",
    "style_sheet",
]
