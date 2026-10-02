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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QAction, QColor, QFontMetrics, QKeySequence, QPalette
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


def style_sheet(background: QColor, highlight: QColor, *, check_gutter: int = 0) -> str:
    """给弹出菜单的那份样式表。

    两个颜色都必须由调用方给**调色板里的原值**：底色是「一整块底色」那块（``Window``
    角色），高亮是系统强调色（``Highlight`` 角色）。理由见模块文档那一段 ——
    样式表里的 ``palette(...)`` 取的是**应用**调色板，不是逐控件下发的那份。

    ``check_gutter`` 是「这一列对勾原本会被预留的宽度」，只有**菜单里没有可勾选项**时才
    补上（见 :data:`MENU_CHECK_GUTTER`）。它排在最后一条规则，靠覆盖前面那条
    ``QMenu::item`` 的左内边距生效。
    """
    return (
        f"QMenu {{ border-radius: {MENU_CORNER_RADIUS}px; "
        f"background: {rgba(background)}; padding: {MENU_INSET}px; }}\n"
        f"QMenu::item {{ padding: {MENU_ITEM_PADDING}; }}\n"
        f"QMenu::right-arrow {{ width: {MENU_ARROW_SIZE}px; height: {MENU_ARROW_SIZE}px; }}\n"
        # 悬停 / 当前项的高亮带。**不加这两行，菜单就完全没有高亮**：样式表一旦挂上，
        # 系统样式那份高亮就不再画了（实测原生菜单按方向键会点亮 #548fff，加了样式表
        # 之后按 ↓ 两次所有行仍是 #ffffff），用户就看不出鼠标在哪一行。
        #
        # 只改底色、不改字色：那一行若是置灰的（比如分组卡片末尾的说明文字），
        # 高亮之下仍要保持灰字，不能看着能点。
        f"QMenu::item:hover {{ background: {rgba(highlight)}; }}\n"
        f"QMenu::item:selected {{ background: {rgba(highlight)}; "
        f"border-radius: {MENU_HIGHLIGHT_RADIUS}px; }}\n"
        + (f"QMenu::item {{ padding-left: {check_gutter}px; }}" if check_gutter else "")
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


def with_native_shortcuts(menu: QMenu) -> Callable[[], None]:
    """把快捷键排成 macOS 那一列（右对齐），返回还原函数。

    为什么不能就这么放着：样式表一挂到 ``QMenu`` 上，Qt 就不再给快捷键算制表位列了 ——
    它被画在文字后面（实测右沿恒为 78.5，与右内边距毫无关系），而原生菜单里它是贴着右沿的
    （实测右沿 123.5 / 菜单宽 124）。调 QSS 的任何一条都改不动它（``padding-right``、
    ``::indicator`` 都试过）。

    正路是 Qt 为这件事留的口子：``setShortcutVisibleInContextMenu(False)`` 关掉它自动画的
    那一列，再把「标签 + 制表符 + 快捷键」写进条目文字 —— 文字里带制表符时，Qt 会把后半段
    排到那一列上，拿到的还是系统写法（``⌘R`` / ``⌘⌫`` / ``⇧⌘C`` / ``⎋``）。

    **还要补空格**：那一列是**左对齐**的（实测所有快捷键都从同一个 x 起），不补的话右缘会
    按字宽参差 12 像素。按字宽差补上空格之后实测降到 5.5 像素，代价是零（那一列的宽度本来就
    由最宽的那条决定，补空格不会让菜单变宽）。

    动作本身没动（``shortcut`` 还在，按键照常触发），改的只是**这一次弹出期间**的显示。
    而菜单栏与右键菜单共用同一批 ``QAction``，所以调用方必须在菜单关掉之后还原 ——
    不还原的话菜单栏上会出现「拷贝路径⇧⌘C」再加上它自己画的那一列。

    .. note:: 这里返回的是**还原函数**：``menu.exec()`` 是阻塞的，调用方在它返回之后立刻
        ``restore()``（放在 ``finally`` 里），异常路径也不会漏。
    """
    saved: list[tuple[QAction, str, bool]] = []
    for current in _owned_menus(menu):
        metrics = QFontMetrics(current.font())
        space = max(1, metrics.horizontalAdvance(" "))
        entries = [
            (action, action.shortcut().toString(QKeySequence.SequenceFormat.NativeText))
            for action in current.actions()
            if not action.isSeparator() and not action.shortcut().isEmpty()
        ]
        widest = max((metrics.horizontalAdvance(text) for _, text in entries), default=0)
        for action, text in entries:
            saved.append((action, action.text(), action.isShortcutVisibleInContextMenu()))
            action.setShortcutVisibleInContextMenu(False)
            padding = " " * max(0, round((widest - metrics.horizontalAdvance(text)) / space))
            action.setText(f"{action.text()}\t{padding}{text}")

    def restore() -> None:
        for action, text, visible in saved:
            action.setText(text)
            action.setShortcutVisibleInContextMenu(visible)

    return restore


def apply_style(menu: QMenu) -> None:
    """给菜单与它的子菜单上同一份样式表。

    两个颜色都**从菜单自己的调色板读**，不另外传进来：调用方刚把窗口那份统一调色板
    下发到菜单上（``MainWindow.context_menu_for``），于是这里读到的底色就是「一整块
    底色」那块，而高亮色是系统给的强调色 —— 也就是说这份样式表用的是系统自己那套配色，
    深浅色自动跟随（理由见模块文档那一段：样式表里的 ``palette(...)`` 取的是应用调色板，
    不是这份逐控件下发的）。

    子菜单那份样式发不发得下去，理由见 :func:`_owned_menus`（不继承、也不是子控件）。
    """
    palette = menu.palette()
    background = palette.color(QPalette.ColorRole.Window)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    # 没有可勾选项的菜单要自己补上那一列，样式表因此**逐个菜单**算 —— 子菜单里是
    # 「排序 / 分组 / 缩略图大小」那些单选项，它们有勾、就不补。
    sheets = {
        gutter: style_sheet(background, highlight, check_gutter=gutter)
        for gutter in (0, MENU_CHECK_GUTTER)
    }
    for current in _owned_menus(menu):
        has_checkable = any(action.isCheckable() for action in current.actions())
        current.setStyleSheet(sheets[0 if has_checkable else MENU_CHECK_GUTTER])


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
    "MENU_CHECK_GUTTER",
    "MENU_CORNER_RADIUS",
    "MENU_HIGHLIGHT_RADIUS",
    "MENU_ARROW_SIZE",
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
    "with_native_shortcuts",
]
