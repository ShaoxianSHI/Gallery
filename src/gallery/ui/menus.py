"""菜单**里该有哪些条目** —— 五张菜单各自的拼法（ADR 0014 / 0021），分组见 ADR 0023。

**菜单在这里拼，``QAction`` 在别处。** 菜单每次弹出时现建、用完即弃，但它用的都是菜单栏里
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
看一眼          打开 · 在默认应用中打开 · 在访达中显示 · 设为相册封面 / 取消相册封面
拿进拿出        拷贝 · 拷贝路径 · 复制到相册 · 移动到相册 · 粘贴
会动文件        重命名… · 移到废纸篓
它是什么        显示元数据信息 · 复制元数据（分组卡片：复制分组条件）
=============  ==========================================================

**某一行在某个落点上不出现**是常事（「粘贴」按落点定，见 ADR 0018 决策 7），缺了行但
**段还在** —— 分隔线照旧，段的位置不跳。

「会动文件」单独成组、离前面那几条远一点，是这张表唯一带风险的取舍：这一段里的每一条
都会改用户文件本身（红线 1），其余各条按错了原件都还在原地。

**某一组在某张菜单里整组都不出现时，那道分隔线也不出现**（否则菜单末尾挂一道空档）。

**菜单怎么画不在这里** —— 样式表、快捷键列、子菜单箭头都在
:mod:`gallery.ui.menu_style`。本模块只用它给出的 ``ShortcutColumnMenu`` 与两个键位提示。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from gallery.ui.menu_style import (
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
    #: **它就是菜单栏那个 ⌘V**（``MainWindow._paste_action`` 本身，不是另做一份），因此
    #: 空白处与相册那两份菜单里这一行都照常写着 ⌘V，点它与按 ⌘V 走的是同一个动作 ——
    #: 界面上写着什么，按下去就是什么。粘到哪里一律由 ``MainWindow._paste_target``
    #: **按选区**算（选中一个相册就粘进那个相册，否则粘进当前这一层）；这个字段只决定
    #: **摆不摆**：照片、分组卡片、多个相册、大图页一律 ``None``（ADR 0018 决策 7）。
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
    #: 「复制分组条件」。分组卡片上它是**第四组**那一段唯一的一行（ADR 0023 决策 3）。
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
    #: 「设为相册封面」（ADR 0028）。**此刻没有可设的对象就是 ``None``**，
    #: 菜单里干脆不出现这一条 —— 与「粘贴」同一个取向（ADR 0014 决策 4）。
    #:
    #: 它属于第一组「看一眼」：它改的是**相册卡片长什么样**，用户的照片一个字节都没动。
    #: 而第三组那一段是留给仅有的两个会改用户文件的入口的（红线 1），往那儿塞一条无害的
    #: 命令，恰好是那条分组最想避免的事。
    set_cover: QAction | None = None
    #: 「取消相册封面」（ADR 0028）。判据与上面那条对称：选中的**恰好是一个**相册、
    #: 而且它真的被指定过封面，否则不出现（用户看得见自己设过，也就没有需要撤销的东西）。
    clear_cover: QAction | None = None
    #: 「编辑元数据…」⇧⌘I（ADR 0030 / 0032）。**单选一张照片、且改得动时才有**，否则 ``None``
    #: —— 那一行不出现，而不是摆一条点下去只会报错的（ADR 0014 决策 4）。
    #:
    #: 与封面那两条一样**只活在右键菜单里**：它需要一个对话框才说得清，而菜单栏上摆一个
    #: 点了就弹框的条目不如不摆。快捷键 ⇧⌘I 照常有效（``MainWindow`` 把它挂到窗口上）。
    edit_metadata: QAction | None = None


def build_photo_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """照片（含多选照片）。

    四个组：看一眼 · 拿进拿出 · 会动文件 · 它是什么（ADR 0023）。组与组之间一道分隔线，
    别的菜单也是这四组，只是各有各缺哪几条。

    ``multiple`` 时藏掉「打开」「在默认中的应用打开」「重命名」，前两组各少一行、第三组
    少一行：它们都只对**某一个**东西成立 ——打开哪一张说不清、交给哪个应用打开同样说不
    清、批量改名是 ADR 0004 明确排除的。这三处**藏掉的只是那一行，组块与分隔线原样
    留着**（见 ADR 0023 决策 2）。

    而第四组「它是什么」是**整组不出现**（连同它前面那道分隔线）：那两条讲的都是**这一
    张**照片 —— 面板显示的是光标那一格，菜单里亮着的时候看着也正是那一格。多选时把
    「复制元数据」藏了、把「显示元数据信息」留着，等于在同一段里用两套判据；现在两条
    一起走（见 :func:`_add_metadata_group`）。

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
    # 第四组自带它前面那道分隔线：多选时它整个不出现，那道线也跟着没有。
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
    - **「重命名…」改的是窗口标题栏那一行**（ADR 0025）：这一页没有名字那一行，于是
      ⌘R 把标题栏那一行换成输入框。它在 ADR 0021 里一度是灰着的，现在亮了。
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
    # 与照片菜单同一份出口，因此那一行永远在（大图页不存在多选这件事）。
    _add_metadata_group(menu, actions, multiple=False)
    return menu


def build_album_menu(actions: MenuActions, *, multiple: bool) -> QMenu:
    """相册（多选相册时同样没有「打开」与「重命名」）。

    四组的分法与照片菜单一致（ADR 0023），少的只是那几条本来就不给相册的命令。

    **「拿进拿出」那一组末尾的「粘贴」粘进这个相册内部**，不是它所在的这一层
    （ADR 0018 决策 7）—— 也就是访达的「粘贴项目」：按在哪个文件夹上就粘进哪个文件夹。

    递进来的 :attr:`MenuActions.paste` 就是菜单栏那个 **⌘V 本身**，因此这一行照常写着 ⌘V，
    而且**菜单开着时按 ⌘V 与点它走的是同一个动作**：两处不可能说出两个地方。粘到哪里
    也不用谁替菜单记着 —— 右键这一格**已经把它选中了**（``select_for_context``，
    ADR 0014 决策 2），而 ``MainWindow._paste_target`` 一律**按选区**算：恰好选中一个
    相册就粘进那个相册，否则粘进当前这一层（ADR 0018 决策 7）。

    多选相册时 ``MainWindow`` 传的是 ``None``：粘进哪一个相册说不清，于是整条不出现
    （ADR 0014 决策 4 的取向），而组里其余那两行与分隔线照旧。
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

    中间那一段与「多选照片」**逐字相同**：分组卡片摊平之后就是一组照片（摊平在
    ``MainWindow`` 一处做，见 ``_selected_paths``），因此这六条命令一条都不用改自己的
    实现，文案也不必分叉。

    「重命名」仍然**不出现**（ADR 0004 一次只改一条，而分组没有名字可改），「粘贴」
    也不出现 —— 一张卡片既不是文件夹也不是当前这一层，**粘到哪里都说不出一个具体目录**
    （ADR 0018 决策 7；决策 1 里那条「整组都在同一个相册时算它的目录」只管 ⌘V 的去向，
    菜单里这一条仍不摆）。

    **第四组在这里是「复制分组条件」**：「显示元数据信息 / 复制元数据」那两条不出现
    —— 面板讲的是**一张**照片，组里哪一张都没有理由被指定（ADR 0014 决策 2 挡下来的那
    半条仍然成立）—— 但第四段的位子不空着。它同样是一句只读问答，说的正是**这张卡片
    是什么**（按哪个字段分的、组叫什么、几张），与「复制元数据」同族，于是就是第四组
    （ADR 0023 决策 3）。

    摆出来的效果与原先那个「第四组之后另起一块」**一模一样**（渲染上它就是第四段），
    区别只是从此**五张菜单里没有第五块**：ADR 0023 说的那四个组，落到每一张上都不多不少。
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
    **当前这一层**：它就是菜单栏那个 ⌘V 本身（与相册那一份同一枚动作，只差目标），
    那一列照常写着 ⌘V。空白处没有「按在哪一张上」要猜，是最没有歧义的一个落点。
    剪贴板里没有文件时**整条不出现**（不是置灰，ADR 0018 决策 6）。

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

    封面那两条（ADR 0028）也归这一组，同样是「此刻有对象才递进来」，没有就 ``None``：
    「设为相册封面」只给照片与大图页（对象是一张照片），「取消相册封面」只给相册
    （对象是一个相册）。它们改的是**这个相册在网格上长什么样**，用户的照片一个字节都没动。

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
    # 封面那两条（ADR 0028）摆在「看一眼」这一组的末尾：它们改的是**这个相册在网格上
    # 长什么样**，与「打开」「在访达中显示」是同一族 —— 都只改变用户眼前的样子。
    for cover_action in (actions.set_cover, actions.clear_cover):
        if cover_action is not None:
            menu.addAction(cover_action)


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
    menu: ShortcutColumnMenu,
    actions: MenuActions,
    *,
    multiple: bool,
    rename: bool = True,
    place: bool = True,
) -> None:
    """第三组「会动文件」：重命名… · 编辑元数据… · 移到废纸篓（ADR 0023 / 0030）。

    这一组里的每一条都会改磁盘上的东西，所以它单独成组、离前面那几条「只是把东西摆出去 /
    递出去」的远一点。**「编辑元数据」也在这里**：它改的是照片里的字节，与改名、删除是同
    一族 —— 摆在「它是什么」那一段（只读问答）会让那一段的承诺说谎。

    **前两条都只在单选时出现**：批量改名是 ADR 0004 明确排除的，而元数据编辑一次只改一张
    —— 一次动几百张照片的 GPS 是不可想象的操作。多选时这一组只剩「移到废纸篓」一行，
    **分隔线照旧**（见 ADR 0023 决策 2）。

    ``rename=False`` / ``place=False`` 是分组卡片与相册：改的是卡片或文件夹，而卡片不是
    磁盘上的东西、没有名字可改，文件夹也没有位置（ADR 0020）。于是那一组只剩「移到废纸篓」，
    分隔线照旧。
    """
    if rename and not multiple:
        menu.addAction(actions.rename)
    if place and not multiple and actions.edit_metadata is not None:
        menu.addAction(actions.edit_metadata)
    menu.addAction(actions.trash)


def _add_metadata_group(menu: ShortcutColumnMenu, actions: MenuActions, *, multiple: bool) -> None:
    """第四组「它是什么」：显示元数据信息 · 复制元数据（ADR 0023）。

    两条都是**只读**的问答，摆在最后 —— 它们与前面三组都不是一族，混进去只会让
    「要动手的那几条」变得不那么显眼。

    **多选时整组不出现，连同它前面那道分隔线一起**（ADR 0023 决策 2）。两条讲的都
    是**这一张**照片：面板显示的是光标那一格，菜单里亮着的时候看着也正是那一格 ——
    选区里另外那十几张并没有理由被指定，一条留一条藏只会让人以为「多选时还能拷
    整份选区」。

    > **已改**：ADR 0014 决策 2 早先只藏了「复制元数据」，把「显示元数据信息」留着，
    > 理由是「面板是个开关」—— 那是把**整段**的判据写成了其中一行的。两条作用的
    > 是同一个对象（都是当前项，见 ``MainWindow._focus_row``），一起走才自洽。

    相册与分组卡片上这一组压根不出现：面板讲的是**一张**照片，组里哪一张都没有理由被
    指定（ADR 0014 决策 2、ADR 0020）。那两张菜单压根不调这里，分组卡片的第四段由
    「复制分组条件」占着（见 :func:`build_group_menu`）。

    **本函数自带它前面那道分隔线**，这是它与另外三个出口唯一的不同：它是唯一会整个
    消失的一段，那道线必须与它同生共死（菜单末尾挂一道谁也看不懂的空档比少一段更糟）。
    前三组「少几行但段还在」，分隔线的位置恒定，摆在调用处即可。
    """
    if multiple:
        return
    menu.addSeparator()
    menu.addAction(actions.metadata)
    menu.addAction(actions.copy_metadata)


def build_album_picker(title: str, albums: Sequence[tuple[str, Path]]) -> QMenu:
    """「复制到相册 / 移动到相册」的子菜单：相册清单（ADR 0019）。

    一项一个相册，标签是**相对库根的路径**（``2025/苏州``）—— 只写名字的话，两个年份
    下面各有一个「苏州」，用户分不清自己要挪到哪去。目标路径挂在 ``QAction.data()`` 上，
    由 ``MainWindow`` 在被触发时取走，因此这个模块仍然不关心「移动」具体怎么做。

    **用完即弃**：调用方每次拼菜单都现建一份，所以用户在访达里新建的相册立刻可选，
    菜单里也不会留着上一次的旧清单。

    清单要花一整趟目录树来列，所以调用方多半会走 :func:`build_lazy_album_picker`
    （清单挪到用户**真的展开这个子菜单**时再列）—— 那才是右键时的常态。

    :param albums: ``(标签, 路径)`` 一一对应，已排好序。空清单请不要调它 ——
        空子菜单点开是个空框，``MainWindow`` 会在那种情况下干脆不建这条菜单。
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

    **为什么值得单开一个。** 列清单是递归走一遍整棵目录树（``iter_albums`` 的
    ``scandir`` 加上每个相册一次相对路径换算）：3000 个相册约 120 ms，全在**主线程**上，
    而且是在「用户在照片上右键一下」的那一刻付的 —— 而他未必会去看那两个子菜单里有什么。
    云盘上更慢，每一层都是一次网络往返。

    **新鲜度一点没丢**：``aboutToShow`` 每次展开都重新问一次，所以用户在访达里新建的
    相册下一次展开照样能选到（ADR 0019 决策 1 要的就是这个）。倒过来说，
    「每次弹出时现列」那句承诺也保住了 —— 它说的是**每次展开**现列，不是
    **构造菜单时**现列。

    展开时一份相册都没有就把自己收起来（``menu.hide()``）：与 :func:`build_album_picker`
    那条「空清单干脆不建这条菜单」是同一个结果，只是这一刻才知道而已。
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
]
