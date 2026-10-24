"""菜单里该有哪些条目 —— 五张菜单各自的拼法，四组的分法与顺序见 ADR 0023。

每次弹出时现建、用完即弃，但用的都是菜单栏里那份 ``QAction``，所以快捷键文字与置灰状态只有一处逻辑。
「分组」「排序」两张子菜单由窗口现建（动作能共享、菜单本身不能），这样右键那两张与菜单栏逐行一致。
**本模块只 ``addAction``**：不要把菜单栏的 ``QMenu`` 直接 ``addMenu`` 进来（会把它搬走）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from gallery.ui.window.menu_style import (
    CLOSE_KEY_HINT,
    OPEN_KEY_HINT,
    ShortcutColumnMenu,
)


class TargetKind(Enum):
    """右键落在什么东西上。"""

    #: 一张照片。
    PHOTO = "photo"
    #: 一个相册（子文件夹）。
    ALBUM = "album"
    #: 一个分组卡片（不是磁盘上的东西）。
    GROUP = "group"
    #: 一条分区标题（分区式里的分组，ADR 0089）。
    SECTION = "section"
    #: 网格的空白处：不作用于任何条目。
    BLANK = "blank"
    #: 大图页里正看着的那张照片（ADR 0021）：菜单与网格那张只差第一行。
    DETAIL = "detail"


@dataclass(frozen=True, slots=True)
class MenuActions:
    """拼菜单要用到的动作集合，由 ``MainWindow`` 备好后递进来；只放共用的那些动作。"""

    #: 「打开」：照片开大图、相册与分组进去。
    open_entry: QAction
    #: 「退出」：大图页退回网格（只在 :func:`build_detail_menu` 里出现，ADR 0021）。
    #: 与「打开」一样**不给真快捷键** —— 大图页上的 ``Esc`` 归 ``DetailView``，这里只挂提示。
    close_entry: QAction
    #: 「在默认应用中打开」⇧⏎（ADR 0014）。**只有照片菜单用它**：相册是文件夹，
    #: 让系统用默认应用打开就是让访达打开它，那是「打开」自己的活。
    open_default: QAction
    #: 「在访达中显示」⌘⏎（ADR 0014）。
    reveal: QAction
    #: 「拷贝」⌘C（文件本身，访达语义，见 ADR 0017）。
    copy: QAction
    #: 「拷贝路径」⇧⌘C。
    copy_path: QAction
    #: 「复制」⌘D（ADR 0044）：在**原位置**造一份副本，``一张.jpg`` → ``一张的副本.jpg``。
    #: 与「拷贝」的差别是那一份**落在磁盘上**（不进剪贴板），与「复制到相册」的差别是
    #: **不用挑目标相册**。分组卡片那一份也有（摊平成组员），**只有空白处那张没有** ——
    #: 那里压根没有选中项。
    duplicate: QAction | None
    #: 「复制到相册」子菜单（ADR 0019）。**此刻没有可选的相册就是 ``None``**，
    #: 菜单里干脆不出现 —— 空子菜单点开是个空框，不如不出现。
    copy_to_album: QMenu | None
    #: 「移动到相册」子菜单（同上）。
    move_to_album: QMenu | None
    #: 这一份菜单里的「粘贴」。**``None`` 就是菜单里不出现这一条**（不是置灰）。它就是菜单栏
    #: 那个 ⌘V，点它与按 ⌘V 走同一个动作，粘到哪里由 ``MainWindow._paste_target`` 按选区算；
    #: 这个字段只决定摆不摆（照片、分组卡片、多个相册、大图页一律 ``None``，ADR 0018）。
    paste: QAction | None
    #: 「新建相册」⇧⌘N（ADR 0022）。**只有空白处那张菜单用它**，建不了就是 ``None``。
    new_album: QAction | None
    #: 「重命名…」⌘R（单选才有）。
    rename: QAction
    #: 「移到废纸篓」⌘⌫。
    trash: QAction
    #: 「显示元数据信息」⌘I（勾选态，跟着面板开合）。
    metadata: QAction
    #: 「复制元数据」。
    copy_metadata: QAction
    #: 「复制分组条件」。分组卡片上它是**第四组**那一段唯一的一行（ADR 0023）。
    copy_group: QAction
    #: 「全选」⌘A。
    select_all: QAction
    #: 「上一级」Esc —— **大图页与网格页共用这一条**（ADR 0041 决策 6）。
    up: QAction
    #: 现建一份「分组」子菜单，挂到给定的父菜单上。**与菜单栏那份逐行一致**
    #: （含「旅行 / 演出 / 影片」三层）：动作可以同时挂在两张菜单上，菜单本身不行，
    #: 所以这里递的是「怎么建」而不是菜单。
    group_menu: Callable[[QMenu], QMenu]
    #: 现建一份「排序」子菜单（字段一列，分隔线，升序 / 降序）。
    sort_menu: Callable[[QMenu], QMenu]
    #: 「缩略图大小」子菜单里的档位。
    size_choices: tuple[QAction, ...]
    #: 「设为相册封面」（ADR 0028）。**此刻没有可设的对象就是 ``None``**。
    #: 它属于第一组「看一眼」：改的是**相册卡片长什么样**，用户的照片一个字节都没动，
    #: 而第三组是留给会改用户文件的入口的（会动用户文件的写操作只许由用户亲手触发）。
    set_cover: QAction | None = None
    #: 「取消相册封面」（ADR 0028）：选中的**恰好是一个**相册、而且它真的被指定过封面，
    #: 否则不出现（用户看得见自己设过，也就没有需要撤销的东西）。
    clear_cover: QAction | None = None
    #: 「编辑元数据…」⇧⌘I（ADR 0030 / 0032）。**单选一张照片、且改得动时才有**，否则 ``None``。
    #: 与封面那两条一样**只活在右键菜单里**（需要一个对话框才说得清），快捷键照常有效
    #: （``MainWindow`` 把它挂到窗口上）。
    edit_metadata: QAction | None = None
    #: 「记录为…」那一个子菜单（旅行 / 演出……，ADR 0043）。``None`` = 这一行不出现：
    #: 事件库不可用（只读卷），或者压根没选中照片。**它内部是三层的**，
    #: 三层都得在下发样式表之前建出来（理由见 ``ui.window.event_flow`` 的模块文档）。
    record_as: QMenu | None = None
    #: 「编辑事件 →（这张照片所属的那些事件）」。与 :attr:`set_event_cover` 同一份清单
    #: （都是「这张照片属于哪些事件」），**只在它属于至少一个事件时才有**，否则 ``None``。
    #: 写的是 Gallery 自己的库，与「记录为…」同族，于是也摆在第四组。
    edit_photo_event: QMenu | None = None
    #: 「编辑事件…」——**只在事件分组卡片上出现**（ADR 0043 决策 8）：那一组就是某个事件。
    edit_event: QAction | None = None
    #: 「从这次事件移出」——同上，作用对象是卡片背后的那批照片。
    leave_event: QAction | None = None
    #: 「设为事件封面 →（它所属的那些事件）」（ADR 0048 决策 6）。**恰好选中一张照片、
    #: 而它属于至少一个事件时才有**，否则 ``None``（不出现，不是置灰）。
    #: 与 :attr:`set_cover` 同姿态（命令挂在照片上），但它是**子菜单**：一张照片可以同时
    #: 属于一次演出与一次旅行，「设为哪一个事件的封面」这个问题是真实的。
    #: 写的是 Gallery 自己的库，与「记录为…」同族，于是也摆在第四组。
    set_event_cover: QMenu | None = None
    #: 「清除事件封面」——**只在事件分组卡片上、且它真的被指定过封面时出现**。
    #: 没有封面就没有需要撤销的东西（与 :attr:`clear_cover` 同一处置）。
    clear_event_cover: QAction | None = None


def build_photo_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """照片（含多选照片）：四个组 看一眼 · 拿进拿出 · 会动文件 · 它是什么（ADR 0023）。

    ``multiple`` 时只藏掉「打开」「在默认应用中打开」「重命名」那几行；第四组「它是什么」
    整组不出现，且「拿进拿出」里没有「粘贴」（ADR 0018）。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=multiple, default_app=True)
    menu.addSeparator()
    _add_copy_group(menu, actions, album_targets=True)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=multiple)
    # 第四组自带它前面那道分隔线：多选时它整个不出现，那道线也跟着没有。
    _add_metadata_group(menu, actions, multiple=multiple)
    return menu


def build_detail_menu(actions: MenuActions) -> QMenu:
    """大图页里正看着的那一张（ADR 0021）：与网格里单张照片那份逐行一致，只把「打开」换成「退出」。
    另外三处差别是没有「粘贴」、写操作作用在正在看的那一张上、「重命名」改的是标题栏那一行。
    """
    menu = ShortcutColumnMenu()
    # 「粘贴」整条没有（大图页没有「当前这一层」），于是换一份**不带粘贴**的动作包；行序仍
    # 与照片菜单共用同一组出口（``replace`` 出来的 ``QAction`` 还是菜单栏那几份）。
    clipboard = replace(actions, paste=None)
    _add_look_group(menu, clipboard, multiple=False, default_app=True, closing=True)
    menu.addSeparator()
    _add_copy_group(menu, clipboard, album_targets=True)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=False)
    # 与照片菜单同一份出口，因此那一行永远在（大图页不存在多选这件事）。
    _add_metadata_group(menu, actions, multiple=False)
    return menu


def build_album_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """相册（多选相册时同样没有「打开」与「重命名」）：四组的分法与照片菜单一致（ADR 0023）。

    「粘贴」粘进这个相册内部（就是菜单栏那个 ⌘V）；粘到哪里由 ``_paste_target`` 按选区算。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=multiple)
    menu.addSeparator()
    _add_copy_group(menu, actions, album_targets=False)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=multiple, place=False)
    return menu


def build_group_menu(actions: MenuActions) -> QMenu:
    """分组卡片：打开它、把**它代表的那批照片**交出去、复制分组条件（ADR 0020）。

    中间那段与「多选照片」**逐字相同**（ADR 0023 决策 3）；卡片本身不是磁盘上的东西，但它代表
    的那批照片是，所以与同段其余几条同一套摊平语义（ADR 0044）。**事件分组卡片**额外多几行
    （ADR 0043 决策 8 / ADR 0048 决策 6）：改名、移出成员、清除封面都在卡片上直接做得到。
    """
    menu = ShortcutColumnMenu()
    _add_look_group(menu, actions, multiple=False)
    menu.addSeparator()
    _add_copy_group(menu, actions)
    menu.addSeparator()
    _add_write_group(menu, actions, multiple=False, rename=False)
    menu.addSeparator()
    if actions.edit_event is not None:
        menu.addAction(actions.edit_event)
    if actions.leave_event is not None:
        menu.addAction(actions.leave_event)
    if actions.clear_event_cover is not None:
        menu.addAction(actions.clear_event_cover)
    menu.addAction(actions.copy_group)
    return menu


def build_section_menu(actions: MenuActions) -> QMenu:
    """分区标题：只摆**这一组**的组级命令（ADR 0089）。

    标题不进选区，所以作用在选区上的命令一概不出现 —— 否则它们会作用在用户看不见的
    上一次选区上。事件分组时多三行，与卡片那张是同一套文案（动作由窗口按这条标题现绑）。
    """
    menu = ShortcutColumnMenu()
    if actions.edit_event is not None:
        menu.addAction(actions.edit_event)
    if actions.leave_event is not None:
        menu.addAction(actions.leave_event)
    if actions.clear_event_cover is not None:
        menu.addAction(actions.clear_event_cover)
    if actions.edit_event is not None or actions.leave_event is not None:
        menu.addSeparator()
    menu.addAction(actions.copy_group)
    return menu


def build_background_menu(actions: MenuActions) -> QMenu:
    """网格空白处：「新建相册」与「粘贴」在最上面，其余是视图级命令。

    两条都会在磁盘上新建东西（只增不改），粘贴的目标是**当前这一层**；两道都不出现时那一组连同分隔线一起没有。
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
    menu.addMenu(actions.group_menu(menu))
    menu.addMenu(actions.sort_menu(menu))
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
    if kind is TargetKind.SECTION:
        return build_section_menu(actions)
    return build_background_menu(actions)


def _add_open(menu: ShortcutColumnMenu, actions: MenuActions) -> None:
    """「打开」那一行，并在最右列挂上**回车**这枚提示（三个落点都从这里走，提示每一级都有）。"""
    menu.addAction(actions.open_entry)
    menu.show_key(actions.open_entry, OPEN_KEY_HINT)


def _add_close(menu: ShortcutColumnMenu, actions: MenuActions) -> None:
    """「退出」那一行，并在最右列挂上 ``Esc`` 这枚提示（ADR 0021）；提示只画不生效。"""
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

    ``default_app`` 只给照片与大图页开；封面那两条（ADR 0028）也归这一组；``closing`` 只给大图页开。
    """
    if not multiple:
        if closing:
            _add_close(menu, actions)
        else:
            _add_open(menu, actions)
        if default_app:
            menu.addAction(actions.open_default)
    menu.addAction(actions.reveal)
    # 封面那两条（ADR 0028）摆在「看一眼」这一组的末尾：它们改的是**这个相册在网格上
    # 长什么样**，与「打开」「在访达中显示」是同一族 —— 都只改变用户眼前的样子。
    for cover_action in (actions.set_cover, actions.clear_cover):
        if cover_action is not None:
            menu.addAction(cover_action)


def _add_copy_group(
    menu: ShortcutColumnMenu, actions: MenuActions, *, album_targets: bool = True
) -> None:
    """第二组「拿进拿出」：拷贝 · 拷贝路径 · **复制** · 复制到相册 · 移动到相册 · 粘贴。

    「复制」⌘D（ADR 0044）与两条「拷贝」同族（多一份出来），而第三组动的是既有那一份。
    「粘贴」粘的是剪贴板，粘不了时不出现；``album_targets=False`` 是相册菜单（ADR 0019）。
    """
    menu.addAction(actions.copy)
    menu.addAction(actions.copy_path)
    if actions.duplicate is not None:
        menu.addAction(actions.duplicate)
    if album_targets:
        if actions.copy_to_album is not None:
            menu.addMenu(actions.copy_to_album)
        if actions.move_to_album is not None:
            menu.addMenu(actions.move_to_album)
    if actions.paste is not None:
        menu.addAction(actions.paste)


def _add_write_group(
    menu: ShortcutColumnMenu,
    actions: MenuActions,
    *,
    multiple: bool,
    rename: bool = True,
    place: bool = True,
) -> None:
    """第三组「会动文件」：重命名… · 编辑元数据… · 移到废纸篓（ADR 0023 / 0030）。

    每条都会改磁盘上的东西；前两条只在单选时出现，``rename`` / ``place`` 为假时是卡片与相册。
    """
    if rename and not multiple:
        menu.addAction(actions.rename)
    if place and not multiple and actions.edit_metadata is not None:
        menu.addAction(actions.edit_metadata)
    menu.addAction(actions.trash)


def _add_metadata_group(menu: ShortcutColumnMenu, actions: MenuActions, *, multiple: bool) -> None:
    """第四组「它是什么」：记录为… · 编辑事件… · 设为事件封面 · 显示元数据信息 · 复制元数据
    （ADR 0023、ADR 0043、ADR 0048）。

    后两条是只读问答；前三条写的都是 Gallery 自己的库，但按「这几张照片是什么」来理解，
    所以归这一组。多选时后两条不出现、分隔线留着；「编辑事件…」紧跟「记录为…」——
    一条把它记进去，一条改它。
    """
    if actions.record_as is not None or actions.edit_photo_event is not None or not multiple:
        menu.addSeparator()
    if actions.record_as is not None:
        # guard: allow-long-comment（addAction(menuAction()) 不接管所有权，临时包回收后菜单项悬空）
        # **用 ``addMenu`` 而不是 ``addAction(menuAction())``**：后者不接管那个 QMenu 的所有权，
        # 它在 ``MenuActions`` 这份临时包里一被回收就析构，留在菜单里的就成了悬空的 menuAction
        # （点开时报 ``Internal C++ object already deleted``）。相册那两个清单走的也是 addMenu。
        menu.addMenu(actions.record_as)
    if actions.edit_photo_event is not None:
        menu.addMenu(actions.edit_photo_event)
    if actions.set_event_cover is not None:
        menu.addMenu(actions.set_event_cover)
    if multiple:
        return
    menu.addAction(actions.metadata)
    menu.addAction(actions.copy_metadata)


def build_album_picker(title: str, albums: Sequence[tuple[str, Path]]) -> QMenu:
    """「复制到相册 / 移动到相册」的子菜单：相册清单（ADR 0019）。

    标签是相对库根的路径（只写名字的话两个年份下的同名相册分不清）；路径挂在
    ``QAction.data()`` 上由 ``MainWindow`` 取走 —— 清单要花一整趟目录树，多半走 lazy 那份。
    """
    menu = QMenu(title)
    menu.setToolTipsVisible(True)
    for label, path in albums:
        action = menu.addAction(label)
        action.setData(path)
    return menu


def build_lazy_album_picker(
    title: str, supplier: Callable[[], Sequence[tuple[str, Path]]]
) -> QMenu:
    """同一个子菜单，但**清单挪到用户真的展开它的时候才列**。

    列清单是递归走一遍整棵目录树（3000 个相册约 120 ms，全在主线程上）。
    ``aboutToShow`` 每次展开重新问，所以新鲜度一点没丢（ADR 0019）。
    """
    menu = QMenu(title)
    menu.setToolTipsVisible(True)
    menu.aboutToShow.connect(lambda: _fill_album_picker(menu, supplier))
    return menu


def _fill_album_picker(menu: QMenu, supplier: Callable[[], Sequence[tuple[str, Path]]]) -> None:
    """展开那一刻把清单填进去（先清掉上一次填的，免得重复）。"""
    menu.clear()
    albums = supplier()
    if not albums:
        menu.hide()  # 一份相册都没有：空框点开只会让人以为软件坏了
        return
    for label, path in albums:
        action = menu.addAction(label)
        action.setData(path)


def _submenu(title: str, actions: tuple[QAction, ...]) -> QMenu:
    """用已有动作现建一个子菜单。"""
    menu = QMenu(title)
    menu.setToolTipsVisible(True)
    for action in actions:
        menu.addAction(action)
    return menu


__all__ = [
    "CLOSE_KEY_HINT",
    "OPEN_KEY_HINT",
    "MenuActions",
    "TargetKind",
    "build_album_menu",
    "build_album_picker",
    "build_background_menu",
    "build_detail_menu",
    "build_group_menu",
    "build_lazy_album_picker",
    "build_menu",
    "build_photo_menu",
    "build_section_menu",
]
