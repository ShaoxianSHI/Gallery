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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

#: 分组卡片为什么不能改名 / 不能删。放在菜单末尾当一句说明，而不是让用户按了才知道。
GROUP_NOT_FILE_HINT = "分组不是磁盘上的文件夹，改不了名也删不掉。"


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
    """分组卡片。

    「重命名」与「移到废纸篓」会由 ``_update_action_states`` 置灰（选区里有 ``GroupRef``
    就关掉），这里在菜单末尾补一句说明，免得置灰看起来像是功能缺失。
    """
    menu = QMenu()
    menu.addAction(actions.open_entry)
    menu.addAction(actions.copy_group)
    menu.addSeparator()
    menu.addAction(actions.rename)
    menu.addAction(actions.trash)
    menu.addSeparator()
    menu.addAction(_hint_action(GROUP_NOT_FILE_HINT, menu))
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


def _hint_action(text: str, parent: QMenu) -> QAction:
    """一条不可点的说明文字。

    置灰是唯一能表达「这里有话要说，但你按不了」的办法 —— 加个分隔线再放一行灰字，
    用户知道这不是个坏掉的按钮。
    """
    hint = QAction(text, parent)
    hint.setEnabled(False)
    return hint


__all__ = [
    "GROUP_NOT_FILE_HINT",
    "MenuActions",
    "TargetKind",
    "build_album_menu",
    "build_background_menu",
    "build_group_menu",
    "build_menu",
    "build_photo_menu",
]
