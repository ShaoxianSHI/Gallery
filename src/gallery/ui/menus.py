"""右键菜单：网格的四种落点各拼各的（见 ADR 0014），大图页是第五种（见 ADR 0021）。

**菜单在这里拼，`QAction` 在别处。** 菜单每次弹出时现建、用完即弃，但它用的都是菜单栏里
那一份 ``QAction`` —— 这样快捷键文字（⌘R / ⌘⌫ / ⌘I）与置灰状态只有一处逻辑
（``MainWindow._update_action_states``），两处界面不可能说出不一样的话。

于是有一条必须守住的规矩：**本模块只 ``addAction``，从不 ``setEnabled`` / ``setText``**
去改别人的动作。特别是不要把菜单栏的 ``QMenu`` 直接 ``addMenu`` 进这里 —— 那样会把
它从菜单栏里**搬走**，菜单栏上那一项就没了。要复用子菜单就复用里面的 ``QAction``，
子菜单自己现建。

**五张菜单共用同四个组**（ADR 0023）：组与组之间一道分隔线，组内不隔。四组按「这道命令
到底动什么」分，不按它是照片还是相册 ——

=============  ==========================================================
组             里面是什么
=============  ==========================================================
看一眼          打开 · 在默认应用中打开 · 在访达中显示
拿进拿出        拷贝 · 拷贝路径 · 复制到相册 · 移动到相册 · 粘贴
会动文件        重命名… · 移到废纸篓
它是什么        显示元数据信息 · 复制元数据
=============  ==========================================================

**某一行在某个落点上不出现**是常事（「粘贴」按落点定，见 ADR 0018 决策 7），缺了行但
**段还在** —— 分隔线照旧，段的位置不跳。

「会动文件」单独成组、离前面那几条远一点，是这张表唯一带风险的取舍：它俩是本项目里
仅有的两个会改用户文件的入口（红线 1），其余各条按错了原件都还在原地。

**某一组在某张菜单里整组都不出现时，那道分隔线也不出现**（否则菜单末尾挂一道空档）。
但**组内少几行时分隔线照旧** —— 多选时第一组只剩「在访达中显示」，分隔线留着，用户在
单选与多选之间切换时看到的是同一组块。

分组卡片的菜单里**没有「重命名」这一行**（改的是卡片，而它不是磁盘上的东西，ADR 0004）；
「移到废纸篓」那一行则在——那张卡片摊平之后就是一组照片，删得动的是照片（ADR 0020）。
两件事都在 ``MainWindow._update_action_states`` 那里算，本模块不碰置灰。

**外观两件事**：一份样式表，各落点共用（:func:`apply_style`；分隔线的颜色也在那份
样式表里，见 :data:`MENU_SEPARATOR_MIX_DARK`）；快捷键按列自己画
（:class:`ShortcutColumnMenu`）—— 样式表一挂上去，Qt 就不再给快捷键算位置了，它会把
快捷键紧跟在文字后面（见 :func:`prepare`）。macOS 样式自己画的那圈圆角
实测约 5 像素、每行只有 18 像素高，菜单挤得像 Windows 的。放大它们只能靠样式表 ——
Qt 没有「菜单圆角」「菜单行高」这样的度量可以改。

最右那一列除了快捷键，还能挂**只画不生效**的键位提示（:meth:`ShortcutColumnMenu.show_key`，
「打开」行的 :data:`OPEN_KEY_HINT` 就是一枚）。那仍然不违反「不改别人的动作」这条规矩：
提示只落在**这一次弹出**的这份菜单上，用完即弃，那一份共用的 ``QAction`` 一个字节都没动。

这里有一个必须记住的副作用（实测）：**只要给 ``QMenu`` 加了样式表，面板就不再由
macOS 样式画**，底色会掉回系统菜单色（浅色 ``#ececec``）。所以样式表里必须把底色
**写死成窗口那块底色**，否则刚统一好的「一整块底色」会在按下菜单的那一刻又破功。
样式表里写 ``palette(window)`` 也不行：它取的是**应用**调色板，不是我们逐控件下发的
那份，颜色会差一档。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
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
from PySide6.QtWidgets import QMenu

from gallery.ui.theme import rgba

#: 弹出菜单的圆角（像素）。macOS 样式自己画的那圈实测约 5，放大一档。
MENU_CORNER_RADIUS = 8

#: 菜单面板那一圈内缩，四边同值（像素）。
#:
#: 它是高亮带与面板边缘之间那圈留白：**高亮带就是 item 矩形**，而 item 矩形被这圈内缩
#: 框在面板中间，上下左右一样宽。只留左右的话，高亮带会顶着面板上下两条边，像贴上去的
#: 一块色斑（用户原话）。这圈留白与悬停状态无关，所以文字永远不会被带着挪位。
#:
#: 这里面有一处实测出来的分工，值得说清楚（全部是逻辑像素，抓图是 Retina 的两倍，
#: 读的时候要除掉倍率，否则每个数字都会大一倍）：
#:
#: | 改哪个 | 文字左沿 | 对勾左沿 |
#: |--------|----------|----------|
#: | ``QMenu::item`` 的左内边距 | **跟着动**（1:1） | 不动 |
#: | ``QMenu`` 自己的左内边距 | 跟着动（1:1） | **跟着动**（1:1） |
#:
#: 而 ``QMenu::indicator`` 的 margin 只加宽那一列、把文字推远，**对勾纹丝不动** ——
#: 所以想让对勾离开边缘，只有这一处内缩可用。
#:
#: 8 的来历：原生菜单里对勾几乎贴着边缘（0.5 逻辑像素），用户一眼就觉得挤；而高亮带
#: 若再靠 margin 内缩 6 像素，带子与面板之间就有 16 像素，被说成「离得太远」。改成
#: 靠这一处内缩之后，带子四边各留 8 像素、对勾落在 9.5、文字落在 27.5（原生 18.5）。
MENU_INSET = 8

#: 高亮带的圆角（像素）。访达里的高亮是一条内缩的圆角带，不是顶到两边的方块。
#:
#: **只有圆角，没有 margin**：``margin`` 是盒子模型的一部分，写在 ``:selected`` 里等于
#: 每悬停一次就换一次盒子，文字会跟着挪位置（用户实测）。高亮带离边缘多远，改由
#: :data:`MENU_INSET` 在菜单自身上决定 —— 与状态无关，文字就永远不动。
MENU_HIGHLIGHT_RADIUS = 5

#: 子菜单右箭头的大小（像素，正方形）。Qt 默认给的那一个偏大（实测约 8.5 宽），
#: 缩一档更像访达里的样子。
MENU_ARROW_SIZE = 8

#: 我们自己画的那个右箭头（三角）有多大。Qt 那个缩到 8 × 8 之后实测墨迹是 3.5 × 9.5，
#: 这里取同一个大小，画进**字母列**——原生菜单里子菜单箭头与快捷键的键位是同一列。
MENU_ARROW_DRAWN_WIDTH = 3.5
MENU_ARROW_DRAWN_HEIGHT = 9.5

#: 菜单项的内边距（上 右 下 左，像素）。行高由此从 18 变成 26。
#:
#: 左边写 **0**：文字的左沿交给 :data:`MENU_INSET` 与样式自带的边距共同决定，
#: 这里再叠一层会把文字推到 47 像素那么远（原生只有 18.5）。
#:
#: 右边这 12 像素是「快捷键不贴着高亮带右沿」那圈留白。样式表模式下快捷键由文字里的
#: 制表符定位（见 :func:`with_native_shortcuts`），**右内边距推不动它** —— 推的是高亮带
#: 那一条边（实测右内边距 0 / 6 / 10 / 12 / 16 时，快捷键右缘恒为 160~167，高亮带右沿
#: 随之内移，间距 2.5→13~20 像素）。写 0 的话快捷键会紧贴带子右沿，看着挤。
#:
#: 它一度写成 38，那是以为要自己给快捷键「腾位置」—— 那 38 像素同样是纯空地，与快捷键
#: 落点毫无关系。
MENU_ITEM_PADDING = "5px 12px 5px 0px"

#: **没有可勾选项的菜单**要补的左内边距（像素），用来让文字列与照片菜单对齐。
#:
#: Qt 只在菜单里**存在可勾选项**时才预留那一列对勾宽度，于是整列文字跟着右移：
#:
#: | 菜单 | 有没有可勾选项 | 文字左沿（实测） |
#: |------|----------------|----------------|
#: | 照片 | 有（「显示元数据信息」） | 28 |
#: | 相册 / 分组 / 空白处 | 没有 | 9 |
#:
#: 差的就是那一列，约 19 像素。菜单里根本没有对勾可画，留着这列空白没有道理，但文字
#: 忽左忽右更难看 —— 所以没有可勾选项的菜单统一补上这么多，让**全软件的文字列对齐**
#: （用户原话：保持一致比较美观）。
MENU_CHECK_GUTTER = 19

#: 分隔线本身多粗（像素），以及它上下各留多少空地（像素）。
#:
#: **两个都得写**：只给 ``background`` 的话那 12 像素会整块被填上（``QMenu::separator``
#: 的默认高度就是 12），深色下一道线看着像一条灰色的带子；只给 ``height: 1px`` 而不给
#: ``margin``，线就贴住上下两行，菜单还会凭空矮 11 像素（实测 132 → 121）。
#:
#: 留白取 5，与 :data:`MENU_ITEM_PADDING` 的上下内边距一致，于是线与文字的呼吸是对齐的。
MENU_SEPARATOR_HEIGHT = 1
MENU_SEPARATOR_MARGIN = 5

#: 分隔线是「正文色压在底色上」的透明度，深浅各一个。
#:
#: **不给这条规则，深色下那条线等于没画**：它由系统样式画，取的是**应用调色板**里偏暗
#: 的一档 —— 浅色下是 ``#aaaaaa``（一眼看得见），深色下是 ``#0f0f0f``，**比底色
#: ``#171717`` 还深**（实测线落在 y129~130，上下一片全是底色）。
#:
#: | | 透明度 | 合成后（实测） |
#: |---|---|---|
#: | 深色 | 0.2 | ``#171717`` → ``#454545``（比底色亮 46 一档，一眼看得见） |
#: | 浅色 | 0.333 | ``#ffffff`` → ``#ababab``（系统那条是 ``#aaaaaa``，差一档，看不出来） |
#:
#: 深浅两个值不能合成一个：按同一个差值去定，浅色下要的是 ``#cccccc``（比系统那条淡），
#: 深色下就变成 ``#6c6c6c``（比访达那种 ``#454545`` 亮得刺眼）。这里的深色值是照着访达
#: 深色菜单里那条线取的。
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


def style_sheet(
    background: QColor,
    highlight: QColor,
    text: QColor,
    *,
    check_gutter: int = 0,
    own_arrow: bool = False,
) -> str:
    """给弹出菜单的那份样式表。

    三个颜色都必须由调用方给**调色板里的原值**：底色是「一整块底色」那块（``Window``
    角色），高亮是系统强调色（``Highlight`` 角色），正文色是 ``WindowText`` 角色 ——
    分隔线的颜色由它按透明度压出来（见 :func:`separator_color`）。理由见模块文档那一段 ——
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
        # 只改底色、不改字色：那一行若是置灰的（比如分组卡片末尾的说明文字），
        # 高亮之下仍要保持灰字，不能看着能点。
        f"QMenu::item:hover {{ background: {rgba(highlight)}; }}\n"
        f"QMenu::item:selected {{ background: {rgba(highlight)}; "
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


#: 修饰符**在一行里的排列顺序**，也是填列的顺序：⌘ 最靠近字母，然后 ⇧、⌥、⌃ ——
#: 与原生菜单一致（访达的 ⇧⌘C 读作左 ⇧ 右 ⌘）。
#:
#: 注意第一个是 ``ControlModifier``：**macOS 上 Qt 把 Ctrl 译成 Command**（⌘R / ⌘⌫ /
#: ⇧⌘C 都是这么显示的），而 ``MetaModifier`` 才是控制键 ⌃。弄反的话画出来的是 ⌃R。
MODIFIER_ORDER = (
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.ShiftModifier,
    Qt.KeyboardModifier.AltModifier,
    Qt.KeyboardModifier.MetaModifier,
)

#: 取修饰符符号的办法：拼一个「该修饰符 + A」让 Qt 自己译，掐掉末尾那个 A。
#:
#: **不能自己查表**：符号是平台约定的（macOS 上 Ctrl→⌘、Meta→⌃），而 ``QKeyCombination``
#: 单独构造修饰符是不带修饰符的（实测 ``QKeyCombination(Key_A, ControlModifier)`` 仍译成
#: 「A」），所以唯一靠得住的来源就是 Qt 自己的字符串。
_MODIFIER_PROBE = {
    Qt.KeyboardModifier.ControlModifier: "Ctrl+A",
    Qt.KeyboardModifier.ShiftModifier: "Shift+A",
    Qt.KeyboardModifier.AltModifier: "Alt+A",
    Qt.KeyboardModifier.MetaModifier: "Meta+A",
}

#: 快捷键各列之间的空隙，以及那一整块离行右沿（也就是高亮带右沿）的留白（像素）。
SHORTCUT_GAP = 3
SHORTCUT_RIGHT_INSET = 12

#: 列块与**最长那条标签**之间的留白（像素）。不给的话两块会贴在一起：相册菜单里
#: 「在访达中显示」那一行的文字右缘与 ⇧ 所在那列只差十几个像素，看着像串成一片。
#: 原生菜单在这一段是很宽的，这里取 20。
SHORTCUT_LABEL_GAP = 20


def _modifier_glyph(modifier: Qt.KeyboardModifier) -> str:
    """一个修饰符在系统写法下的符号（``⌘`` / ``⇧`` / ``⌥`` / ``⌃``）。"""
    text = QKeySequence(_MODIFIER_PROBE[modifier]).toString(QKeySequence.SequenceFormat.NativeText)
    return text[:-1] if text.endswith("A") else text


def _native(key: Qt.Key | int) -> str:
    """一个键在系统写法下长什么样（``⌫`` / ``⎋`` / ``C``）。"""
    return QKeySequence(int(key)).toString(QKeySequence.SequenceFormat.NativeText)


#: 「打开」那一行在最右列上写的键位提示（**只画不生效**，见
#: :meth:`ShortcutColumnMenu.show_key`）。
#:
#: **是 ``↵`` 这个符号，不是「回车」两个字**：那一列里其余的字样（⌘ / ⇧ / ⌫ / ⎋）都是
#: 平台约定的符号，没有哪一条写成汉字 —— 写成「回车」既对不齐，又把一个键撑成两个字的
#: 宽，整列跟着宽一截（用户原话：要的是那个符号）。
#:
#: 取值不写死，直接问 Qt 要（见 :func:`_native`）：符号是平台约定的，而 ``Qt.Key_Return``
#: 在 macOS 上正是 ``↵``，自己查表迟早认错（:func:`_modifier_glyph` 那段记的就是这个）。
OPEN_KEY_HINT = _native(Qt.Key_Return)

#: 「退出」那一行在最右列上写的键位提示，同样**只画不生效**（见
#: :meth:`ShortcutColumnMenu.show_key`）。大图页上 ``Esc`` 早由 ``DetailView.keyPressEvent``
#: 接走了，所以这枚提示不能变成真快捷键 —— 两个动作抢同一个键，Qt 里谁先被按下全看加载顺序。
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
        for action in rows:
            cell = self.key_cell(action)
            if cell is None:
                if self.owns_submenu_arrows() and action.menu() is not None:
                    self._paint_arrow(action, columns, metrics)
                continue
            modifiers, key_text = cell
            rect = self.actionGeometry(action)
            key_rect, modifier_rects = columns.cells(
                modifiers, rect.right(), rect.top(), rect.height()
            )
            if action is self.activeAction():
                role = QPalette.ColorRole.HighlightedText
            elif action.isEnabled():
                role = QPalette.ColorRole.Text
            else:
                role = QPalette.ColorRole.PlaceholderText
            painter = QPainter(self)
            painter.setPen(self.palette().color(role))
            painter.drawText(key_rect, align, key_text)
            for modifier, modifier_rect in modifier_rects.items():
                painter.drawText(modifier_rect, align, _modifier_glyph(modifier))
            painter.end()

    def _paint_arrow(
        self, action: QAction, columns: ShortcutColumns, metrics: QFontMetrics
    ) -> None:
        """子菜单的右箭头画在**字母列**上 —— 原生菜单里它与快捷键的键位是同一列。

        不画在 Qt 那个位置（行右沿内侧约 5 像素）：那在字母列右边，两者看着是两个不相干
        的东西。QSS 挪不动它（``padding-right`` 从 12 加到 28，实测箭头仍在 x=148~151.5），
        所以是关掉 Qt 那个（``::right-arrow`` 尺寸给 0 就完全不画）改由我们画。
        """
        rect = self.actionGeometry(action)
        key_rect = columns.key_rect(rect.right(), rect.top(), rect.height())
        center = key_rect.center()
        half_width = MENU_ARROW_DRAWN_WIDTH / 2
        half_height = MENU_ARROW_DRAWN_HEIGHT / 2
        if action is self.activeAction():
            role = QPalette.ColorRole.HighlightedText
        elif action.isEnabled():
            role = QPalette.ColorRole.Text
        else:
            role = QPalette.ColorRole.PlaceholderText
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(role))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center.x() - half_width, center.y() - half_height),
                    QPointF(center.x() + half_width, center.y()),
                    QPointF(center.x() - half_width, center.y() + half_height),
                ]
            )
        )
        painter.end()

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

    两个颜色都**从菜单自己的调色板读**，不另外传进来：调用方刚把窗口那份统一调色板
    下发到菜单上（``MainWindow.context_menu_for``），于是这里读到的底色就是「一整块
    底色」那块，而高亮色是系统给的强调色 —— 也就是说这份样式表用的是系统自己那套配色，
    深浅色自动跟随（理由见模块文档那一段：样式表里的 ``palette(...)`` 取的是应用调色板，
    不是这份逐控件下发的）。分隔线的颜色由底色与正文色配出来（见 :func:`separator_color`），
    因此深浅两套也跟着一起变。

    子菜单那份样式发不发得下去，理由见 :func:`_owned_menus`（不继承、也不是子控件）。
    """
    palette = menu.palette()
    background = palette.color(QPalette.ColorRole.Window)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    text = palette.color(QPalette.ColorRole.WindowText)
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
                check_gutter=0 if has_checkable else MENU_CHECK_GUTTER,
                own_arrow=owns_arrows,
            )
        )


class TargetKind(Enum):
    """右键落在什么东西上。"""

    #: 一张照片。
    PHOTO = "photo"
    #: 一个相册（子文件夹）。
    ALBUM = "album"
    #: 一个分组卡片（不是磁盘上的东西）。
    GROUP = "group"
    #: 网格的空白处：不作用于任何条目。
    BLANK = "blank"
    #: 大图页里正看着的那张照片（ADR 0021）。菜单与网格那张几乎一样，只把第一行换掉。
    DETAIL = "detail"


@dataclass(frozen=True, slots=True)
class MenuActions:
    """拼菜单要用到的动作集合，由 ``MainWindow`` 攒好递进来。

    只放**共用**的那些动作；上下文特有的命令（打开、复制元数据、复制分组条件…）在
    ``MainWindow`` 里各自连自己的槽，这里不关心它们做什么。
    """

    #: 「打开」：照片开大图、相册与分组进去。
    open_entry: QAction
    #: 「退出」：大图页退回网格（只在 :func:`build_detail_menu` 里出现，ADR 0021）。
    #: 与「打开」一样**不给真快捷键** —— 大图页上的 ``Esc`` 归 ``DetailView``，这里只挂提示。
    close_entry: QAction
    #: 「在默认应用中打开」⇧⏎（真快捷键，见 ADR 0014 决策 7）。**只有照片菜单用它**：
    #: 相册是文件夹，让系统用默认应用打开它就是让访达打开它，那是「打开」自己的活。
    open_default: QAction
    #: 「在访达中显示」⌘⏎（真快捷键，见 ADR 0014 决策 6）。
    reveal: QAction
    #: 「拷贝」⌘C（文件本身，访达语义，见 ADR 0017）。
    copy: QAction
    #: 「拷贝路径」⇧⌘C。
    copy_path: QAction
    #: 「复制到相册」子菜单（ADR 0019）。**此刻没有可选的相册就是 ``None``**，
    #: 菜单里干脆不出现这一条 —— 空子菜单点开是个空框，不如不出现。
    copy_to_album: QMenu | None
    #: 「移动到相册」子菜单（同上）。
    move_to_album: QMenu | None
    #: 这一份菜单里的「粘贴」。**``None`` 就是菜单里不出现这一条**（不是置灰）。
    #:
    #: 粘到哪、摆不摆，由 ``MainWindow`` 拼这一包之前按**落点**定好递进来
    #: （ADR 0018 决策 7）：空白处是菜单栏那一条（粘进当前这一层，⌘V 与它同一份）；单个
    #: 相册是**粘进那个相册内部**的那一条（它不带快捷键 —— ⌘V 说的是另一件事）；照片、
    #: 分组卡片、多个相册、大图页一律 ``None``。
    #:
    #: **摆在哪一段由各张菜单自己决定**（相册排在「拿进拿出」那一组的末尾，空白处与
    #: 「新建相册」同属一组，见 :func:`build_album_menu` 与 :func:`build_background_menu`）。
    paste: QAction | None
    #: 「新建相册」⇧⌘N（ADR 0022）。**只有空白处那张菜单用它**（那是「没有选中任何东西」
    #: 的落点）；此刻建不了就是 ``None`` —— 与「粘贴」同一个取向。
    new_album: QAction | None
    #: 「重命名…」⌘R（单选才有）。
    rename: QAction
    #: 「移到废纸篓」⌘⌫。
    trash: QAction
    #: 「显示元数据信息」⌘I（勾选态，跟着面板开合）。
    metadata: QAction
    #: 「复制元数据」。
    copy_metadata: QAction
    #: 「复制分组条件」。
    copy_group: QAction
    #: 「全选」⌘A。
    select_all: QAction
    #: 「上一级」Esc。
    up: QAction
    #: 「分组」子菜单里的单选项，顺序即菜单栏里的顺序（先是「不分组」）。
    group_choices: tuple[QAction, ...]
    #: 「排序」子菜单里的单选项。
    sort_choices: tuple[QAction, ...]
    #: 「逆序」。
    descending: QAction
    #: 「缩略图大小」子菜单里的档位。
    size_choices: tuple[QAction, ...]


def build_photo_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """照片（含多选照片）。

    四个组：看一眼 · 拿进拿出 · 会动文件 · 它是什么（ADR 0023）。组与组之间一道分隔线，
    别的菜单也是这四组，只是各有各缺哪几条。

    ``multiple`` 时藏掉「打开」「在默认中的应用打开」「重命名」「复制元数据」：它们都只对
    **某一个**东西成立 ——打开哪一张说不清、交给哪个应用打开同样说不清、批量改名是
    ADR 0004 明确排除的、拷谁的元数据同样说不清。其余几条作用于整个选择；**藏掉的只是
    那一行，组块与分隔线原样留着**（见 ADR 0023 决策 2）。

    **「拿进拿出」那一组里没有「粘贴」**（ADR 0018 决策 7，用户明确要求）：按在一张照片
    上时「粘到哪」说不清 —— 这一格未必属于当前这一层（全部照片、分组那几屏都是各相册
    混在一起的），摆出来只会让人猜。⌘V 照旧一直在「编辑」里，粘进当前这一层。组里少了
    这一行而**其余四行与分隔线原样留着**，正是 ADR 0023 决策 2 那个取向。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=multiple, default_app=True)
    menu.addSeparator()
    _add_copy_group(menu, actions, album_targets=True)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=multiple)
    menu.addSeparator()
    _add_metadata_group(menu, actions, multiple=multiple)
    return menu


def build_detail_menu(actions: MenuActions) -> QMenu:
    """大图页里正看着的那一张（ADR 0021）。

    **与网格里单张照片那份逐行一致，只把最上面的「打开」换成「退出」**：照片已经开着，
    「打开」没有对象，而「退出」正是这里唯一该有的第一行。用户按在照片上的直觉与在缩略图上
    一模一样，菜单因此不该另起一套说法。

    差的不止一行，所以要说清楚：

    - **没有「粘贴」**：照片菜单那一侧同样不摆它（按在一张照片上「粘到哪」说不清，
      ADR 0018 决策 7），这一页连「当前这一层」都没有，更没有理由摆。
    - **写操作作用在正在看的那一张上**：判据由 ``MainWindow._selected_entries`` 给（大图页
      看 ``_current_row``，翻过图之后网格的选区早就不是这一张了）。
    - **「重命名…」摆着但置灰**：行内改名的输入框在网格里，大图页的改名字段还没有（ADR 0021）。
    - **「复制到相册」「移动到相册」照旧在**：它们作用于整个选择，而大图页的「整个选择」就是
      正在看的那一张（那两条命令问的是 :meth:`MainWindow._selected_paths`，那边已经按页面
      算过了）。移走的那张由监听器在回到网格时才从这一层摘掉（ADR 0013：变化在大图页攒着），
      所以这一页不会看着行号突然错位。
    """
    menu = ShortcutColumnMenu()
    # 「粘贴」整条没有：把剪贴板里的文件复制进去要回答「粘到哪一层」，而大图页没有层
    # （那一层随时可能被换掉）。于是这里换一份**不带粘贴**的动作包，行序仍与照片菜单共用
    # 同一组出口（``replace`` 出来的 ``QAction`` 还是菜单栏那几份，一个都没新建）。
    clipboard = replace(actions, paste=None)
    _add_look_group(menu, clipboard, multiple=False, default_app=True, closing=True)
    menu.addSeparator()
    _add_copy_group(menu, clipboard, album_targets=True)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=False)
    menu.addSeparator()
    _add_metadata_group(menu, actions, multiple=False)
    return menu


def build_album_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """相册（多选相册时同样没有「打开」与「重命名」）。

    四组的分法与照片菜单一致（ADR 0023），少的只是那几条本来就不给相册的命令。

    **「拿进拿出」那一组末尾的「粘贴」粘进这个相册内部**，不是它所在的这一层
    （ADR 0018 决策 7）—— 也就是访达的「粘贴项目」：按在哪个文件夹上就粘进哪个文件夹。
    传进来的 :attr:`MenuActions.paste` 已经是**这一格**相册的那一条了（目标挂在它的
    ``data()`` 上，由 ``MainWindow`` 拼这一包之前挂好），**它不是菜单栏那个 ⌘V，因此
    没有快捷键**：⌘V 说的是「粘进当前这一层」，两件事不在一处，不能共用一枚键。

    多选相册时 ``MainWindow`` 传的是 ``None``：粘进哪一个相册说不清，于是整条不出现
    （ADR 0014 决策 4 的取向），而组里其余那两行与分隔线照旧。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=multiple)
    menu.addSeparator()
    _add_copy_group(menu, actions, album_targets=False)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=multiple)
    return menu


def build_group_menu(actions: MenuActions) -> QMenu:
    """分组卡片：打开它、把**它代表的那批照片**交出去、复制分组条件（ADR 0020）。

    中间那一段与「多选照片」**逐字相同**：分组卡片摊平之后就是一组照片（摊平在
    ``MainWindow`` 一处做，见 ``_selected_paths``），因此这六条命令一条都不用改自己的
    实现，文案也不必分叉。

    「重命名」仍然**不出现**（ADR 0004 一次只改一条，而分组没有名字可改），「粘贴」
    也不出现 —— 一张卡片既不是文件夹也不是当前这一层，**粘到哪里都说不出一个具体目录**
    （ADR 0018 决策 7；决策 1 里那条「整组都在同一个相册时算它的目录」只管 ⌘V 的去向，
    菜单里这一条仍不摆）。至于「显示元数据信息」与「复制元数据」：面板讲的是**一张**照片，
    组里哪一张都没有理由被指定（ADR 0014 决策 4 挡下来的那半条仍然成立）。于是第四组在这里
    是空的，一整组都不出现（见 ADR 0023 决策 2）。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=False)
    menu.addSeparator()
    _add_copy_group(menu, actions)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=False, rename=False)
    menu.addSeparator()
    menu.addAction(actions.copy_group)
    return menu


def build_background_menu(actions: MenuActions) -> QMenu:
    """网格空白处：「新建相册」与「粘贴」在最上面，其余是视图级命令。

    **「新建相册」摆在最上面**（ADR 0022 决策 1），与访达一致：空白处右键的第一条就是
    「新建文件夹」。

    **紧跟着的是「粘贴」，两条同属一组**（ADR 0018 决策 7）：它们是这张菜单里仅有的两条
    会在磁盘上**新建**东西的命令，且都只增不改、绝不覆盖 —— 与「会动文件」那一族
    （重命名、移到废纸篓）不是一回事，但同样该离那些只读的视图命令远一点。粘进的是
    **当前这一层**（与 ⌘V 同一件事、同一枚 ``QAction``，所以那一列照常写着 ⌘V）：空白处
    没有「按在哪一张上」要猜，是除 ⌘V 之外最没有歧义的一个落点。剪贴板里没有文件时
    **整条不出现**（不是置灰，ADR 0018 决策 6）。

    建不了 / 粘不了时各自压根不出现（传进来的是 ``None``），**两道都不出现时那一组连同
    分隔线一起没有**，否则菜单开头会挂一道空档（ADR 0014 决策 4 的取向）。

    子菜单自己建（复用里面的 ``QAction``）——把菜单栏的 ``QMenu`` 直接塞进来会把它搬走。
    """
    menu = ShortcutColumnMenu()
    if actions.new_album is not None:
        menu.addAction(actions.new_album)
    if actions.paste is not None:
        menu.addAction(actions.paste)
    if actions.new_album is not None or actions.paste is not None:
        menu.addSeparator()
    menu.addAction(actions.select_all)
    menu.addSeparator()
    menu.addMenu(_submenu("分组", actions.group_choices))
    menu.addMenu(
        _submenu(
            "排序",
            (*actions.sort_choices, actions.descending),
            split_before=len(actions.sort_choices),
        )
    )
    menu.addMenu(_submenu("缩略图大小", actions.size_choices))
    menu.addSeparator()
    menu.addAction(actions.up)
    return menu


def build_menu(actions: MenuActions, kind: TargetKind, *, multiple: bool = False) -> QMenu:
    """按落点挑一份菜单。**只拼不弹**，测试因此可以直接调它看内容。"""
    if kind is TargetKind.PHOTO:
        return build_photo_menu(actions, multiple=multiple)
    if kind is TargetKind.DETAIL:
        return build_detail_menu(actions)
    if kind is TargetKind.ALBUM:
        return build_album_menu(actions, multiple=multiple)
    if kind is TargetKind.GROUP:
        return build_group_menu(actions)
    return build_background_menu(actions)


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


def _add_open(menu: ShortcutColumnMenu, actions: MenuActions) -> None:
    """「打开」那一行，并在最右列挂上**回车**这枚提示。

    三个落点（照片 / 相册 / 分组）都从这里走，所以提示也是**每一级都有**：开大图、进相册、
    进分组都是同一个键，不必按落点去记两套。多选时这一行压根不出现（见
    :func:`_add_open_and_reveal`），自然也没有提示可挂。
    """
    menu.addAction(actions.open_entry)
    menu.show_key(actions.open_entry, OPEN_KEY_HINT)


def _add_close(menu: ShortcutColumnMenu, actions: MenuActions) -> None:
    """「退出」那一行，并在最右列挂上 ``Esc`` 这枚提示（ADR 0021）。

    与 :func:`_add_open` 完全对称：换掉的只是「这一下做完之后人在哪儿」，键位提示同样
    只画不生效（大图页上的 ``Esc`` 由 ``DetailView`` 接走了，见 :data:`CLOSE_KEY_HINT`）。
    """
    menu.addAction(actions.close_entry)
    menu.show_key(actions.close_entry, CLOSE_KEY_HINT)


def _add_look_group(
    menu: ShortcutColumnMenu,
    actions: MenuActions,
    *,
    multiple: bool,
    default_app: bool = False,
    closing: bool = False,
) -> None:
    """第一组「看一眼」：打开 · 在默认应用中打开 · 在访达中显示（ADR 0023）。

    三条都是**把东西摆到眼前**，而且一个字节都不改：Gallery 里开大图、交给系统默认
    应用、在访达里挑出来。放在同一组是因为它们的代价相同 —— 按错了什么也没发生。

    最右那一列里挨着的三枚回车不是一回事：「打开」的是**只画不生效**的 ↵ 提示
    （见 :meth:`ShortcutColumnMenu.show_key`），「在默认应用中打开」的 ⇧⏎ 与
    「在访达中显示」的 ⌘⏎ 是**真快捷键** —— 画它们的是同一个出口
    （:meth:`ShortcutColumnMenu.key_cell`），用户看到的却分得清哪个能按。

    ``default_app`` 只给照片与大图页开：相册是文件夹，让系统用默认应用打开它就是让
    访达打开它，那与「打开」是同一件事，菜单里摆两行等于把一件事说两遍（ADR 0014
    决策 7）。分组卡片那一行不出现同理：那批照片一次有 128 张，说不清打开哪一张。

    ``closing`` 只给大图页开：第一行换成「退出」，其余一行不差（ADR 0021）。**它不是
    另写一张菜单**，正是为了让两处的行序只有这一份。

    多选时第一行压根不出现（打开哪一张说不清），这一组只剩「在访达中显示」一行，
    **分隔线照旧**（见 ADR 0023 决策 2）。
    """
    if not multiple:
        if closing:
            _add_close(menu, actions)
        else:
            _add_open(menu, actions)
        if default_app:
            menu.addAction(actions.open_default)
    menu.addAction(actions.reveal)


def _add_copy_group(
    menu: ShortcutColumnMenu, actions: MenuActions, *, album_targets: bool = True
) -> None:
    """第二组「拿进拿出」：拷贝 · 拷贝路径 · 复制到相册 · 移动到相册 · 粘贴。

    **「拷贝」排在「拷贝路径」上面、「粘贴」排在它下面**（ADR 0017 / 0018）：三个挨在
    一起是访达的那一组。拷贝与拷贝路径作用于整个选择，多选时都留着；**「粘贴」不作用
    于选择**（它粘的是剪贴板），粘不了时压根不出现 —— 大图页则由 :func:`build_detail_menu`
    整条不摆（那里连「当前这一层」都没有）。

    **「复制到相册」「移动到相册」摆在「拷贝路径」正下方、「粘贴」上面**（ADR 0019）：
    三者都是「把这一堆东西放到别处去」，挨在一起才是一族；它们作用于整个选择，
    多选时同样留着。库里一个相册都没有时传进来的是 ``None``，那两条干脆不出现 ——
    空子菜单点开是个空框（ADR 0019 决策 7）。

    ``album_targets=False`` 是相册菜单：搬的是**照片**，把相册整个搬走会牵动整棵子树
    （ADR 0019 决策 5）。
    """
    menu.addAction(actions.copy)
    menu.addAction(actions.copy_path)
    if album_targets:
        if actions.copy_to_album is not None:
            menu.addMenu(actions.copy_to_album)
        if actions.move_to_album is not None:
            menu.addMenu(actions.move_to_album)
    if actions.paste is not None:
        menu.addAction(actions.paste)


def _add_write_group(
    menu: ShortcutColumnMenu, actions: MenuActions, *, multiple: bool, rename: bool = True
) -> None:
    """第三组「会动文件」：重命名… · 移到废纸篓（ADR 0023）。

    这一组里的每一条都会改磁盘上的东西（红线 1 说的就是它们两个入口），所以它单独
    成组、离前面那几条「只是把东西摆出去 / 递出去」的远一点。

    **「重命名」只在单选时出现**：批量改名是 ADR 0004 明确排除的。多选时这一组只剩
    「移到废纸篓」一行，**分隔线照旧**（见 ADR 0023 决策 2）。

    ``rename=False`` 是分组卡片：改的是卡片，而卡片不是磁盘上的东西，没有名字可改；
    删得掉的是它代表的那批照片（ADR 0020）。于是那一组只剩「移到废纸篓」，分隔线照旧。
    """
    if rename and not multiple:
        menu.addAction(actions.rename)
    menu.addAction(actions.trash)


def _add_metadata_group(menu: ShortcutColumnMenu, actions: MenuActions, *, multiple: bool) -> None:
    """第四组「它是什么」：显示元数据信息 · 复制元数据（ADR 0023）。

    两条都是**只读**的问答，摆在最后 —— 它们与前面三组都不是一族，混进去只会让
    「要动手的那几条」变得不那么显眼。

    「复制元数据」只在单选时出现：拷谁的元数据说不清（ADR 0014 决策 2）。相册与分组
    卡片上两条都不出现：面板讲的是**一张**照片，组里哪一张都没有理由被指定。
    """
    menu.addAction(actions.metadata)
    if not multiple:
        menu.addAction(actions.copy_metadata)


def build_album_picker(title: str, albums: Sequence[tuple[str, Path]]) -> QMenu:
    """「复制到相册 / 移动到相册」的子菜单：相册清单（ADR 0019）。

    一项一个相册，标签是**相对库根的路径**（``2025/苏州``）—— 只写名字的话，两个年份
    下面各有一个「苏州」，用户分不清自己要挪到哪去。目标路径挂在 ``QAction.data()`` 上，
    由 ``MainWindow`` 在被触发时取走，因此这个模块仍然不关心「移动」具体怎么做。

    **用完即弃**：调用方每次拼菜单都现建一份，所以用户在访达里新建的相册立刻可选，
    菜单里也不会留着上一次的旧清单。

    :param albums: ``(标签, 路径)`` 一一对应，已排好序。空清单请不要调它 ——
        空子菜单点开是个空框，``MainWindow`` 会在那种情况下干脆不建这条菜单。
    """
    menu = QMenu(title)
    menu.setToolTipsVisible(True)
    for label, path in albums:
        action = menu.addAction(label)
        action.setData(path)
    return menu


def _submenu(title: str, actions: tuple[QAction, ...], *, split_before: int = 0) -> QMenu:
    """用已有动作现建一个子菜单。"""
    menu = QMenu(title)
    menu.setToolTipsVisible(True)
    for position, action in enumerate(actions):
        if position == split_before:
            menu.addSeparator()
        menu.addAction(action)
    return menu


__all__ = [
    "CLOSE_KEY_HINT",
    "MENU_CHECK_GUTTER",
    "MENU_CORNER_RADIUS",
    "MENU_HIGHLIGHT_RADIUS",
    "MENU_ARROW_DRAWN_HEIGHT",
    "MENU_ARROW_DRAWN_WIDTH",
    "MENU_ARROW_SIZE",
    "MENU_SEPARATOR_HEIGHT",
    "MENU_SEPARATOR_MARGIN",
    "MENU_SEPARATOR_MIX_DARK",
    "MENU_SEPARATOR_MIX_LIGHT",
    "MODIFIER_ORDER",
    "OPEN_KEY_HINT",
    "SHORTCUT_LABEL_GAP",
    "MENU_INSET",
    "MENU_ITEM_PADDING",
    "MenuActions",
    "TargetKind",
    "apply_style",
    "build_album_menu",
    "build_album_picker",
    "build_background_menu",
    "build_detail_menu",
    "build_group_menu",
    "build_menu",
    "build_photo_menu",
    "separator_color",
    "style_sheet",
    "ShortcutColumnMenu",
    "ShortcutColumns",
]
