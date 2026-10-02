"""右键菜单：网格的四种落点各拼各的（见 ADR 0014）。

**菜单在这里拼，`QAction` 在别处。** 菜单每次弹出时现建、用完即弃，但它用的都是菜单栏里
那一份 ``QAction`` —— 这样快捷键文字（⌘R / ⌘⌫ / ⌘I）与置灰状态只有一处逻辑
（``MainWindow._update_action_states``），两处界面不可能说出不一样的话。

于是有一条必须守住的规矩：**本模块只 ``addAction``，从不 ``setEnabled`` / ``setText``**
去改别人的动作。特别是不要把菜单栏的 ``QMenu`` 直接 ``addMenu`` 进这里 —— 那样会把
它从菜单栏里**搬走**，菜单栏上那一项就没了。要复用子菜单就复用里面的 ``QAction``，
子菜单自己现建。

分组卡片上那两项（重命名 / 移到废纸篓）的置灰也不在这里做：右键点中分组时，
视图已经把它选上了，``_update_action_states`` 自然会把它们关掉（它判据是「选区里有
没有 ``GroupRef``」）。本模块只负责在菜单末尾补一条说明文字。

**外观：一份样式表，四个落点共用**（:func:`apply_style`）。macOS 样式自己画的那圈圆角
实测约 5 像素、每行只有 18 像素高，菜单挤得像 Windows 的。放大它们只能靠样式表 ——
Qt 没有「菜单圆角」「菜单行高」这样的度量可以改。

这里有一个必须记住的副作用（实测）：**只要给 ``QMenu`` 加了样式表，面板就不再由
macOS 样式画**，底色会掉回系统菜单色（浅色 ``#ececec``）。所以样式表里必须把底色
**写死成窗口那块底色**，否则刚统一好的「一整块底色」会在按下菜单的那一刻又破功。
样式表里写 ``palette(window)`` 也不行：它取的是**应用**调色板，不是我们逐控件下发的
那份，颜色会差一档。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QAction, QColor, QPalette
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

#: 菜单项的内边距（上 右 下 左，像素）。行高由此从 18 变成 26。
#:
#: 左边写 **0**：文字的左沿交给 :data:`MENU_INSET` 与样式自带的边距共同决定，
#: 这里再叠一层会把文字推到 47 像素那么远（原生只有 18.5）。
#:
#: 右边给得宽不是随手写的：样式表的 padding 是**替换**样式自己的边距的，不是叠加。
#: 给窄了，右边那一列快捷键（⌘R / ⌘⌫）就会被挤出菜单。
MENU_ITEM_PADDING = "5px 38px 5px 0px"


def style_sheet(background: QColor, highlight: QColor) -> str:
    """给弹出菜单的那份样式表。

    两个颜色都必须由调用方给**调色板里的原值**：底色是「一整块底色」那块（``Window``
    角色），高亮是系统强调色（``Highlight`` 角色）。理由见模块文档那一段 ——
    样式表里的 ``palette(...)`` 取的是**应用**调色板，不是逐控件下发的那份。
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
        f"border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}"
    )


def apply_style(menu: QMenu) -> None:
    """给菜单与它的子菜单上同一份样式表。

    两个颜色都**从菜单自己的调色板读**，不另外传进来：调用方刚把窗口那份统一调色板
    下发到菜单上（``MainWindow.context_menu_for``），于是这里读到的底色就是「一整块
    底色」那块，而高亮色是系统给的强调色 —— 也就是说这份样式表用的是系统自己那套配色，
    深浅色自动跟随（理由见模块文档那一段：样式表里的 ``palette(...)`` 取的是应用调色板，
    不是这份逐控件下发的）。

    **子菜单既不从父菜单继承样式表，也不是它的子控件**（实测
    ``QMenu.addMenu(另一个 QMenu)`` 不会给后者设父对象，``findChildren`` 找不到它，
    ``sub.styleSheet()`` 是空的）。而空白处那份菜单带三个子菜单，所以这里顺着
    ``action.menu()`` 递归下去，一层都不漏。

    递归时**必须认所有权**：``QAction`` 是全局复用的（菜单栏与右键菜单共用同一份），
    ``some_action.menu()`` 可能指向别处的菜单。判据是「这个子菜单的 ``menuAction()``
    是不是当前这条 action」—— 只有那样它才真的长在这个菜单里。否则会把「视图」菜单
    下的排序 / 分组 / 缩略图大小一并改掉，而它们并不属于这次右键。
    """
    palette = menu.palette()
    sheet = style_sheet(
        palette.color(QPalette.ColorRole.Window),
        palette.color(QPalette.ColorRole.Highlight),
    )
    seen: set[int] = set()
    pending = [menu]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.setStyleSheet(sheet)
        for action in current.actions():
            submenu = action.menu()
            if submenu is not None and submenu.menuAction() is action:
                pending.append(submenu)


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


@dataclass(frozen=True, slots=True)
class MenuActions:
    """拼菜单要用到的动作集合，由 ``MainWindow`` 攒好递进来。

    只放**共用**的那些动作；上下文特有的命令（打开、复制元数据、复制分组条件…）在
    ``MainWindow`` 里各自连自己的槽，这里不关心它们做什么。
    """

    #: 「打开」：照片开大图、相册与分组进去。
    open_entry: QAction
    #: 「在访达中显示」。
    reveal: QAction
    #: 「拷贝路径」⇧⌘C。
    copy_path: QAction
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

    ``multiple`` 时藏掉「打开」「重命名」「复制元数据」：它们都只对**某一个**东西成立
    ——打开哪一张说不清、批量改名是 ADR 0004 明确排除的、拷谁的元数据同样说不清。
    其余几条作用于整个选择。
    """
    menu = QMenu()
    _add_open_and_reveal(menu, actions, multiple=multiple)
    menu.addSeparator()
    menu.addAction(actions.metadata)
    if not multiple:
        menu.addAction(actions.copy_metadata)
    return menu


def build_album_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """相册（多选相册时同样没有「打开」与「重命名」）。"""
    menu = QMenu()
    _add_open_and_reveal(menu, actions, multiple=multiple)
    menu.addAction(actions.trash)
    return menu


def build_group_menu(actions: MenuActions) -> QMenu:
    """分组卡片：**只有打开与复制分组条件**。

    「重命名」与「移到废纸篓」在这里干脆**不出现**（ADR 0014 决策 4 曾是「置灰 + 一行
    说明」，后按用户意见收窄成不出现）。分组不是磁盘上的东西，这两条永远不可用；菜单里
    摆两个点不动的条目，再加一句解释为什么点不动，等于把一个功能缺失讲了三遍。
    """
    menu = QMenu()
    menu.addAction(actions.open_entry)
    menu.addAction(actions.copy_group)
    return menu


def build_background_menu(actions: MenuActions) -> QMenu:
    """网格空白处：只有视图级命令，一条会动文件的都没有。

    子菜单自己建（复用里面的 ``QAction``）——把菜单栏的 ``QMenu`` 直接塞进来会把它搬走。
    """
    menu = QMenu()
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
    """按落点挑一份菜单。"""
    if kind is TargetKind.PHOTO:
        return build_photo_menu(actions, multiple=multiple)
    if kind is TargetKind.ALBUM:
        return build_album_menu(actions, multiple=multiple)
    if kind is TargetKind.GROUP:
        return build_group_menu(actions)
    return build_background_menu(actions)


def _add_open_and_reveal(menu: QMenu, actions: MenuActions, *, multiple: bool) -> None:
    """照片与相册共用的上半截：打开 → 在访达中显示 → 拷贝路径 → 重命名 → 废纸篓。

    中间只留一道分隔线：多选时「打开」与「重命名」都不出现，若在那里各插一道，
    「拷贝路径」与「移到废纸篓」之间就会多出一道谁也看不懂的空档。
    """
    if not multiple:
        menu.addAction(actions.open_entry)
    menu.addAction(actions.reveal)
    menu.addAction(actions.copy_path)
    menu.addSeparator()
    if not multiple:
        menu.addAction(actions.rename)
    menu.addAction(actions.trash)


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
    "MENU_CORNER_RADIUS",
    "MENU_HIGHLIGHT_RADIUS",
    "MENU_INSET",
    "MENU_ITEM_PADDING",
    "MenuActions",
    "TargetKind",
    "apply_style",
    "build_album_menu",
    "build_background_menu",
    "build_group_menu",
    "build_menu",
    "build_photo_menu",
    "style_sheet",
]
