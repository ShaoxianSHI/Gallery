"""右键菜单：落点决定菜单，视图把选区摆正（ADR 0014）。

三块，分开测：

1. **选区语义** —— 右键在没选中的格子 / 已选中的格子 / 空白处各会发生什么。
   这一段用裸网格测：它与窗口、与库都没有关系，测窗口只会把因果搅浑。
2. **菜单内容** —— 四种落点各拼出什么。走真实的 ``MainWindow.context_menu_for``，
   因为置灰是 ``_update_action_states`` 算出来的，只有连着窗口才是真的。
3. **新命令** —— 拷贝 ⌘C、拷贝路径、全选、复制分组条件、在访达中显示、在默认应用中打开。

「在访达中显示」与「在默认应用中打开」在这里**必须换掉**：真跑会把访达与别的程序叫起来，
那是测试机上真实的副作用。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui import (
    desktop,  # noqa: E402
    menus,  # noqa: E402
)
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待后台扫描 / 元数据读取 / 地点解析跑完的时限。
WAIT_SECONDS = 10.0

#: 测试库里的两张照片，按扫描顺序（相册内按名称）。
PHOTO_NAMES = ("一张.jpg", "另一张.jpg")


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def empty_clipboard(qt_app: QApplication) -> Iterator[None]:
    """每条用例都从「剪贴板是空的」开始。

    照片与相册菜单里有没有「粘贴」这一条，取决于剪贴板里有没有文件（ADR 0018），
    而剪贴板是**进程级**的、会被同一个 worker 上跑完的用例留下来。不摆这一下，下面
    「菜单里有什么」的断言就成了执行顺序的函数。
    """
    QGuiApplication.clipboard().clear()
    yield
    QGuiApplication.clipboard().clear()


def _select(view: PhotoGrid, model: PhotoListModel, rows: Sequence[int]) -> None:
    """按代码选中若干行（不带任何修饰键，等价于用户按住 ⌘ 连点）。

    **先把当前项设好再选**：``setCurrentIndex`` 会把选区收成那一格（``PhotoGrid.select_row``
    里也是「先设当前、后选行」，同一个原因），顺序反了多选就只剩一格。
    """
    view.setCurrentIndex(model.index(rows[0], 0))
    selection = view.selectionModel()
    assert selection is not None
    selection.clearSelection()
    for row in rows:
        selection.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


# ---- 1. 选区语义（裸网格）---------------------------------------------------


def _bare_grid(qt_app: QApplication, count: int = 4) -> PhotoGrid:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append(
        [
            PhotoRef(path=Path(f"/tmp/IMG_{index}.jpg"), size=1, mtime_ns=index)
            for index in range(count)
        ]
    )
    grid.setModel(model)
    grid.resize(900, 600)
    grid.show()
    qt_app.processEvents()
    return grid


def test_right_click_on_unselected_row_selects_only_it(qt_app: QApplication) -> None:
    """右键一张没选中的照片 → 只选它一个，菜单作用于它。"""
    grid = _bare_grid(qt_app)
    grid.select_row(1)

    assert grid.select_for_context(3) == 3
    assert grid.selected_rows() == [3]


def test_right_click_on_selected_row_keeps_multi_selection(qt_app: QApplication) -> None:
    """右键一张**已经选中**的照片 → 多选一个都不能少。

    这是最容易做错的一条：重置成单选，用户就没法右键批量删自己选的那几张了。
    """
    grid = _bare_grid(qt_app)
    _select(grid, grid.model(), (1, 2))

    assert grid.select_for_context(1) == 1
    assert grid.selected_rows() == [1, 2]


def test_right_click_on_blank_clears_selection(qt_app: QApplication) -> None:
    """右键空白处 → 选区连同当前项一起清掉。

    只清选区是不够的：当前项还在，「重命名」的可用性判据里那个 ``current_row``
    就仍然有效，用户会拿到一个作用在看不见的东西上的按钮。
    """
    grid = _bare_grid(qt_app)
    grid.select_row(2)

    assert grid.select_for_context(-1) == -1
    assert grid.selected_rows() == []
    assert not grid.currentIndex().isValid()


def test_right_click_does_not_move_range_anchor(qt_app: QApplication) -> None:
    """右键选中**不挪扩选锚点**。

    锚点是「上一次完成的普通左键点选」，下一次 ⇧ 点从那里铺开。挪了它，
    「右键 A → ⇧ 点 D」就会从 A 铺开，而访达是从上一次左键那格铺开的。
    """
    grid = _bare_grid(qt_app)
    grid.select_row(1)

    assert grid.select_for_context(3) == 3
    assert grid._range_anchor == 1


def test_context_menu_event_reports_the_row(qt_app: QApplication) -> None:
    """真的把一个 ``ContextMenu`` 事件递进去：信号要带着那一行的行号出来。

    上面几条直接调 ``select_for_context``，绕过了事件入口；这一条钉住
    「Qt 的右键 → 视图 → 窗口」这段接线（包括 ``indexAt`` 与坐标换算）。
    """
    grid = _bare_grid(qt_app)
    seen: list[int] = []
    grid.context_requested.connect(seen.append)
    model = grid.model()
    assert model is not None
    at = grid.visualRect(model.index(2, 0)).center()
    # 三参数的构造器才不是废弃的那个（两参数的从 Qt 6.8 起就标了 Deprecated）。
    grid.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, at, at))

    assert seen == [2]
    assert grid.selected_rows() == [2]


# ---- 窗口里的菜单与命令 -----------------------------------------------------


def _settle(qt_app: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._scan_job is None and window._facet_job is None and window._place_job is None:
            qt_app.processEvents()
            return
        time.sleep(0.02)
    qt_app.processEvents()


def _open_library(qt_app: QApplication, root: Path, session_base: Path) -> MainWindow:
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    _settle(qt_app, window)
    return window


@pytest.fixture
def window(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> Iterator[MainWindow]:
    """一个「一个相册 + 两张照片」的库，网格停在库根。"""
    root = tmp_path / "照片"
    album = root / "京都"
    album.mkdir(parents=True, exist_ok=True)
    for name in PHOTO_NAMES:
        make_photo(name, directory=album)
    prepare_library(root)
    instance = _open_library(qt_app, root, tmp_path / "状态")
    yield instance
    instance.close()


def _photo(window: MainWindow) -> PhotoRef:
    """库里那张照片的引用（给分组卡片当组员用）。

    组卡片**必须带至少一张照片**：``build_groups`` 只从真照片建桶，空组在产品里根本
    不存在；而 ``PhotoListModel`` 找封面时按「``cover.path`` 或 ``entry.path``」取，
    空组会正好崩在「``GroupRef`` 没有 ``path``」这件事上。
    """
    return PhotoRef(path=window._library.root / "京都" / PHOTO_NAMES[0], size=1, mtime_ns=1)


def _enter_album(qt_app: QApplication, window: MainWindow) -> None:
    """切到相册模式并进入那个相册。

    换模式与进相册都会重新列一层，而列一层是**后台**跑的：转一圈事件循环不等于扫完了，
    必须等 ``_scan_job`` 落地，否则模型里一个格子都还没有（测试里表现为行号 ``-1``）。
    """
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    window.enter_folder(window._library.root / "京都")
    _settle(qt_app, window)


def _show_albums(qt_app: QApplication, window: MainWindow) -> None:
    """只切到相册模式，停在库根（那里能看到相册卡片）。"""
    window.set_browse_mode(BROWSE_MODE_ALBUMS)
    _settle(qt_app, window)


def _context_at(window: MainWindow, row: int) -> QMenu | None:
    """模拟一次真实的右键：视图先把选区摆正，窗口再拼菜单。

    这两步是分开的（``PhotoGrid.contextMenuEvent`` → ``MainWindow``），所以在窗口级
    的测试里也得照着来 —— 少了前一步，重命名 / 移到废纸篓会因为「什么都没选中」
    而正确地置灰，测的就不是真实场景了。
    """
    assert window._grid.select_for_context(row) == row
    return window.context_menu_for(row)


def _context_on_blank(window: MainWindow) -> QMenu | None:
    window._grid.select_for_context(-1)
    return window.context_menu_for(-1)


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions()]


def _blocks(menu: QMenu) -> list[list[str]]:
    """按分隔线把菜单切成块 —— 一块就是 ADR 0023 说的一个组。"""
    blocks: list[list[str]] = [[]]
    for action in menu.actions():
        if action.isSeparator():
            blocks.append([])
            continue
        blocks[-1].append(action.text())
    return blocks


def _enabled(menu: QMenu, label: str) -> bool:
    for action in menu.actions():
        if action.text() == label:
            return action.isEnabled()
    raise AssertionError(f"菜单里没有「{label}」，实际有：{_texts(menu)}")


def _key_cell(menu: QMenu, label: str) -> tuple[frozenset[Qt.KeyboardModifier], str]:
    """那一行最右列上的内容：有哪些修饰符 + 键位文字（没有这一列就报错）。

    走 :meth:`menus.ShortcutColumnMenu.key_cell`：快捷键与提示在这一列里是同一个出口，
    拼菜单的地方也不该分出两条路来（见 ``ui.menus`` 里 ``show_key``）。
    """
    assert isinstance(menu, menus.ShortcutColumnMenu)
    action = next(a for a in menu.actions() if a.text() == label)
    cell = menu.key_cell(action)
    assert cell is not None, f"「{label}」那一列是空的"
    return cell


def _key_hint(menu: QMenu, label: str) -> str:
    """那一行最右列上写着什么，且**不带修饰符**（空字符串 = 那一列什么都没有）。"""
    modifiers, text = _key_cell(menu, label)
    assert not modifiers, f"「{label}」不该带修饰符，实际有：{sorted(str(m) for m in modifiers)}"
    return text


def test_every_menu_is_split_into_the_same_four_groups(
    qt_app: QApplication, window: MainWindow
) -> None:
    """五张菜单共用同四个组（ADR 0023）：看一眼 · 拿进拿出 · 会动文件 · 它是什么。

    分组按「这道命令到底动什么」，**不按落点**：用户在照片上、在分组卡片上、在大图上
    看到的是同一组块，缺哪几条一眼就知道那张菜单少了什么。

    **剪贴板先摆满**，「粘贴」在哪儿才看得见：它只出现在**相册**那一份的「拿进拿出」组里
    （ADR 0018 决策 7 —— 按在照片上「粘到哪」说不清）。空剪贴板下这一条压根不出现，
    那样的对照等于把四组的形状少钉了一条。

    空白处那张不在其中 —— 它的第一组是「新建相册」与「粘贴」两条（ADR 0022 / 0018），
    后面才是视图级命令。
    """
    # 顺序有讲究：换浏览方式只在「真的是换了」时才重列一层，所以相册那张要在进相册之前拼；
    # 大图页拼完得先退回网格，右键菜单才拼得出来（见 ``context_menu_for``）。
    desktop.copy_files_to_clipboard([window._library.root / "京都" / PHOTO_NAMES[1]])
    _show_albums(qt_app, window)
    album_row = window._model.row_of_path(window._library.root / "京都")
    assert album_row >= 0
    album = _context_at(window, album_row)

    _enter_album(qt_app, window)
    photo_row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert photo_row >= 0
    photo = _context_at(window, photo_row)
    _select(window._grid, window._model, (0, 1))
    multi = _context_at(window, 0)

    _open_detail(qt_app, window, PHOTO_NAMES[0])
    detail = window.detail_context_menu()
    window.show_grid()
    qt_app.processEvents()

    member = _photo(window)
    window._model.reorder(
        [GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(member,))]
    )
    qt_app.processEvents()
    group = _context_at(window, 0)

    look = ["在访达中显示"]
    copy = ["拷贝", "拷贝路径", "复制到相册", "移动到相册"]
    write = ["移到废纸篓"]
    expected = {
        "照片": [
            ["打开", "在默认应用中打开", *look],
            copy,
            ["重命名…", *write],
            ["显示元数据信息", "复制元数据"],
        ],
        "多选照片": [[*look], copy, write],
        "大图": [
            ["退出", "在默认应用中打开", *look],
            copy,
            ["重命名…", *write],
            ["显示元数据信息", "复制元数据"],
        ],
        "相册": [["打开", *look], ["拷贝", "拷贝路径", "粘贴"], ["重命名…", *write]],
        "分组卡片": [["打开", *look], copy, write, ["复制分组条件"]],
    }
    menus = {
        "照片": photo,
        "多选照片": multi,
        "大图": detail,
        "相册": album,
        "分组卡片": group,
    }

    for name, built in menus.items():
        assert built is not None, f"{name}菜单没拼出来"
        blocks = _blocks(built)
        assert blocks == expected[name], f"{name}菜单的分组与 ADR 0023 不一致"
        # 组内不隔、组间必隔：所以既不该有两道挨着的分隔线，也不该在末尾留一道。
        assert all(block for block in blocks), f"{name}菜单里有一道空的组块"


def test_photo_menu_items(qt_app: QApplication, window: MainWindow) -> None:
    """照片：四组各占一段（ADR 0023）。

    分组按「这道命令到底动什么」：看一眼 · 拿进拿出 · 会动文件 · 它是什么。
    「会动文件」单独一段、离前面那几条远一点 —— 它俩是仅有的两个会改用户文件的入口。

    **「拿进拿出」那一段里没有「粘贴」**，而这一条与剪贴板里有没有文件无关（另见
    :func:`test_photo_menu_has_no_paste_even_with_files`）。
    """
    _enter_album(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert row >= 0

    menu = _context_at(window, row)

    assert menu is not None
    assert _texts(menu) == [
        "打开",
        "在默认应用中打开",
        "在访达中显示",
        "",
        "拷贝",
        "拷贝路径",
        "复制到相册",
        "移动到相册",
        "",
        "重命名…",
        "移到废纸篓",
        "",
        "显示元数据信息",
        "复制元数据",
    ]
    assert _enabled(menu, "重命名…")
    assert _enabled(menu, "移到废纸篓")


def test_photo_menu_has_no_paste_even_with_files(qt_app: QApplication, window: MainWindow) -> None:
    """按在照片上（单选与多选都是）**不摆「粘贴」**，剪贴板摆满了也不摆（ADR 0018 决策 7）。

    用户明确要求的：这一格未必属于当前这一层（全部照片、分组那几屏都是各相册混在
    一起的），摆出来只会让人猜「粘到哪」。**⌘V 照旧能用**（它粘进当前这一层）——
    菜单摆不摆与快捷键怎么用是两回事。少的是「拿进拿出」那一段里的**一行**，
    其余几行与段的位置原样留着（ADR 0023 决策 2）。
    """
    _enter_album(qt_app, window)
    desktop.copy_files_to_clipboard([window._library.root / "京都" / PHOTO_NAMES[1]])
    first = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert first >= 0

    single = _context_at(window, first)
    assert single is not None
    assert "粘贴" not in _texts(single)
    assert "移动到相册" in _texts(single)

    assert window._model.rowCount() >= 2
    _select(window._grid, window._model, (first, first + 1))
    multiple = _context_at(window, first)
    assert multiple is not None
    assert "粘贴" not in _texts(multiple)

    # 同一时刻 ⌘V 仍然是通的。
    assert window._can_paste() is True
    assert window._paste_action.isEnabled() is True


def test_album_menu_has_no_metadata_items(qt_app: QApplication, window: MainWindow) -> None:
    """相册：没有元数据那两条，也没有「在默认应用中打开」（目录不是照片）。

    后一条是刻意不给的：相册是文件夹，让系统用默认应用打开它就是让访达打开它 ——
    那与上面那行「打开」是同一件事，菜单里摆两行等于把一件事说两遍（ADR 0014 决策 7）。

    同样**没有**「复制到相册 / 移动到相册」：那两条搬的是照片，搬相册会动到整棵子树
    （ADR 0019 决策 5）。
    """
    _show_albums(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0

    menu = _context_at(window, row)

    assert menu is not None
    # 少的只是那两条：四组与分隔线的位置和照片菜单一样（ADR 0023）。
    assert _texts(menu) == [
        "打开",
        "在访达中显示",
        "",
        "拷贝",
        "拷贝路径",
        "",
        "重命名…",
        "移到废纸篓",
    ]


def test_album_menu_shows_the_paste_shortcut(qt_app: QApplication, window: MainWindow) -> None:
    """相册菜单里「粘贴」那一行最右列写着 **⌘V**（用户明确要求补上）。

    那一行**就是菜单栏那个 ⌘V**（ADR 0018 决策 7），所以这枚键不是画的装饰：菜单开着时
    按 ⌘V 与点它走的是同一个动作、同一个槽，粘进的**都是这个相册内部**。用户要的是
    「右键这个相册就粘进这个相册」，键位得说同一句话。
    """
    _show_albums(qt_app, window)
    desktop.copy_files_to_clipboard([window._library.root / "京都" / PHOTO_NAMES[1]])
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0

    menu = _context_at(window, row)

    assert menu is not None
    modifiers, key_text = _key_cell(menu, "粘贴")
    assert key_text == "V"
    assert Qt.KeyboardModifier.ControlModifier in modifiers
    assert next(a for a in menu.actions() if a.text() == "粘贴") is window._paste_action


def test_group_menu_acts_on_the_photos_inside(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片：中间那一段与「多选照片」**逐字相同**（ADR 0020）。

    摊平之后就是一组照片，所以「在访达中显示 / 拷贝 / 拷贝路径 / 复制到相册 / 移动到相册 /
    移到废纸篓」一条都不用改自己的实现，也不用分叉文案。

    **仍然没有**「重命名」（改的是卡片，而它不是磁盘上的东西，ADR 0004）与「粘贴」
    （它作用的是剪贴板，与这一组无关）；开头结尾是分组自己的两条。

    「移到废纸篓」**前面单独隔一道**：它是这张菜单里唯一会动文件的一条，前面几条点哪
    一个都只是把东西递出去（原件仍在原地）。
    """
    photo = PhotoRef(path=window._library.root / "京都" / PHOTO_NAMES[0], size=1, mtime_ns=1)
    group = GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    menu = _context_at(window, 0)

    assert menu is not None
    assert _texts(menu) == [
        "打开",
        "在访达中显示",
        "",
        "拷贝",
        "拷贝路径",
        "复制到相册",
        "移动到相册",
        "",
        "移到废纸篓",
        "",
        "复制分组条件",
    ]


def test_multi_selection_hides_single_item_actions(
    qt_app: QApplication, window: MainWindow
) -> None:
    """多选：只作用在「某一个东西」上的命令全部藏起来（ADR 0004 与菜单设计）。

    「复制到相册」「移动到相册」**多选时仍然留着** —— 它们作用于整个选择，说的正是
    「把这一堆东西挪到那儿去」，正是多选最想干的事（ADR 0019）。

    **藏掉的只是那一行，组块与分隔线原样留着**（ADR 0023 决策 2）：于是第一组只剩
    「在访达中显示」、第三组只剩「移到废纸篓」，而用户在单选与多选之间切换时看到的是
    同一组块 —— 分隔线的位置不跳。

    唯独**第四组整组消失**（连同它前面那道分隔线）：「显示元数据信息」与「复制元数据」
    讲的都是**这一张**照片（面板显示的是光标那一格），选区里另外那一张没有被指定的
    理由。早先只藏了后者、把前者留着，那是在同一段里用两套判据。
    """
    _enter_album(qt_app, window)
    assert window._model.rowCount() == len(PHOTO_NAMES)
    _select(window._grid, window._model, (0, 1))

    menu = _context_at(window, 0)

    assert menu is not None
    assert _texts(menu) == [
        "在访达中显示",
        "",
        "拷贝",
        "拷贝路径",
        "复制到相册",
        "移动到相册",
        "",
        "移到废纸篓",
    ]


def test_background_menu_has_no_file_actions(window: MainWindow) -> None:
    """空白处：「新建相册」在最上面，其余仍是视图级命令。

    剪贴板空着时「新建相册」是这条菜单里**唯一会动磁盘**的一条（ADR 0022），而它属于
    「只增不改」那一类 —— 建出来的是空文件夹，绝不覆盖任何东西。真正危险的那几条
    （重命名、移到废纸篓、在访达中显示）依然一条都不在这儿。

    「粘贴」是**唯一**会出现在这里的另一个文件操作，剪贴板里有文件时它会挨在「新建相册」
    下面（ADR 0018 决策 7），见 ``tests/ui/test_paste_flow.py``。
    """
    menu = _context_on_blank(window)

    assert menu is not None
    assert _texts(menu) == [
        "新建相册",
        "",
        "全选",
        "",
        "分组",
        "排序",
        "缩略图大小",
        "",
        "上一级",
    ]
    forbidden = {"重命名…", "移到废纸篓", "在访达中显示", "粘贴"}
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            for choice in submenu.actions():
                assert choice.text() not in forbidden


def test_outdated_row_yields_no_menu(window: MainWindow) -> None:
    """行号过期（扫描正在流式上屏）时宁可不弹，也不弹一份错的。"""
    assert window.context_menu_for(9999) is None


def test_open_says_return_on_every_level_that_has_it(
    qt_app: QApplication, window: MainWindow
) -> None:
    """「打开」出现在哪一级，最右列就都要写着回车那个符号（``↵``）。

    网格里回车本来就开着当前项（``PhotoGrid.keyPressEvent``），而照片 / 相册 / 分组这三张
    菜单此前一个字都没写 —— 用户对着「打开」不知道还能这么按。开大图、进相册、进分组是
    同一个键，所以三处是同一句提示，不是各写各的。

    **这枚提示只画不生效**（``ShortcutColumnMenu.show_key``），所以那个共用的 ``QAction``
    上的快捷键仍然必须空着：挂上去它就成了整个窗口的回车，与网格自己的处理打架。
    """
    # 相册要在**库根**看（进了京都就只剩照片），所以这一段排在进相册之前 —— 换浏览方式
    # 不会退回上一层，顺序写反了相册那一行就找不到（实测 row -1）。
    _show_albums(qt_app, window)
    album_row = window._model.row_of_path(window._library.root / "京都")
    assert album_row >= 0
    album = _context_at(window, album_row)
    assert album is not None

    assert _texts(album)[0] == "打开"
    assert _key_hint(album, "打开") == menus.OPEN_KEY_HINT

    _enter_album(qt_app, window)
    photo_row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert photo_row >= 0
    photo = _context_at(window, photo_row)
    assert photo is not None

    assert _texts(photo)[0] == "打开"
    assert _key_hint(photo, "打开") == menus.OPEN_KEY_HINT

    photo_ref = _photo(window)
    window._model.reorder(
        [GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo_ref,))]
    )
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))
    group = _context_at(window, 0)
    assert group is not None

    assert _texts(group)[0] == "打开"
    assert _key_hint(group, "打开") == menus.OPEN_KEY_HINT

    # 界面上的字面就钉死成**那个符号**：常量是从 Qt 要来的（``QKeySequence(Qt.Key_Return)``），
    # 万一它在别的平台上译成了别的字样，这里红一次总比菜单上写着「回车」两个字强。
    assert menus.OPEN_KEY_HINT == "↵"
    assert len(menus.OPEN_KEY_HINT) == 1, "是一个符号，不是「回车」两个字"

    # 提示挂在这一次弹出的菜单上，共用的动作一个字节都没动：三张菜单用的都是**同一个**
    # ``QAction``，所以查这一处就够（挂上去它就成了整个窗口的回车）。
    assert window._open_entry_action.shortcut().isEmpty(), "「打开」不该被真的注册成回车"


def test_multi_selection_has_no_open_row_to_hint(qt_app: QApplication, window: MainWindow) -> None:
    """多选时「打开」整行都不出现，那枚提示也跟着没 —— 打开哪一张说不清。

    这里查的是**那一列里不带修饰符的** ↵：提示是只画不生效的（``show_key``），按定义就没有
    修饰符，而「在访达中显示」的 ⌘⏎ 是真快捷键 —— 多选时它照旧在，不该被这条断言捎带砍掉。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0, 1))

    menu = _context_at(window, 0)

    assert menu is not None
    assert "打开" not in _texts(menu)
    assert isinstance(menu, menus.ShortcutColumnMenu)
    bare = [text for modifiers, text in menu.key_cells() if not modifiers]
    assert menus.OPEN_KEY_HINT not in bare
    assert _key_cell(menu, "在访达中显示")[1] == menus.OPEN_KEY_HINT


# ---- 命令 -------------------------------------------------------------------


def test_copy_paths_puts_one_per_line(qt_app: QApplication, window: MainWindow) -> None:
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0, 1))

    window.copy_selected_paths()

    clipboard = QGuiApplication.clipboard().text()
    assert clipboard.splitlines() == [
        str(window._library.root / "京都" / PHOTO_NAMES[0]),
        str(window._library.root / "京都" / PHOTO_NAMES[1]),
    ]


def test_copy_puts_the_files_themselves_on_the_clipboard(
    qt_app: QApplication, window: MainWindow
) -> None:
    """⌘C 放的是**文件**（ADR 0017），不是路径文字、也不是像素。

    与访达一致：在访达里 ⌘V 得到一份拷贝。为此剪贴板里必须有**文件 URL** ——
    纯文本的剪贴板粘到访达里什么也不会发生，下面把 ⇧⌘C 那条拿来对照。

    **别去断言 ``clipboard().text()`` 是空的**：Qt 在 macOS 上会从 uri-list 自己合成一份
    文字（实测 ``file:///…``），那是平台行为，不是我们放的。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0, 1))

    window.copy_selected_files()

    clipboard = QGuiApplication.clipboard()
    assert [url.toLocalFile() for url in clipboard.mimeData().urls()] == [
        str(window._library.root / "京都" / PHOTO_NAMES[0]),
        str(window._library.root / "京都" / PHOTO_NAMES[1]),
    ]
    assert window._banner.isVisible()
    assert window._banner.text() == "已拷贝 2 项到剪贴板。"

    # 对照：「拷贝路径」放的是纯文本，剪贴板里一个文件也没有。
    QGuiApplication.clipboard().setText("原样")
    window.copy_selected_paths()
    assert QGuiApplication.clipboard().mimeData().urls() == []


def test_copy_names_the_single_file_it_copied(qt_app: QApplication, window: MainWindow) -> None:
    """只拷一个时把**文件名**说出来：横幅要说明做了什么，而不是只报一个数字。"""
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))

    window.copy_selected_files()

    assert [url.toLocalFile() for url in QGuiApplication.clipboard().mimeData().urls()] == [
        str(window._library.root / "京都" / PHOTO_NAMES[0])
    ]
    assert window._banner.text() == f"已拷贝「{PHOTO_NAMES[0]}」到剪贴板。"


def test_copy_works_for_an_album_folder(qt_app: QApplication, window: MainWindow) -> None:
    """相册是文件夹，访达拷文件夹是同一件事，因此它也照拷不误。"""
    _show_albums(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0
    _select(window._grid, window._model, (row,))

    window.copy_selected_files()

    assert [url.toLocalFile() for url in QGuiApplication.clipboard().mimeData().urls()] == [
        str(window._library.root / "京都")
    ]


def test_copy_covers_the_photos_of_a_group_card(qt_app: QApplication, window: MainWindow) -> None:
    """⌘C 作用于**组里的照片**：卡片不是磁盘上的东西，它代表的那批照片是（ADR 0020）。"""
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    window.copy_selected_files()

    assert [url.toLocalFile() for url in QGuiApplication.clipboard().mimeData().urls()] == [
        str(window._library.root / "京都" / PHOTO_NAMES[0])
    ]
    assert window._copy_action.isEnabled() is True, "⌘C 与菜单里的那一行说的是同一件事"


def test_copy_lives_in_the_edit_menu_too(qt_app: QApplication, window: MainWindow) -> None:
    """只活在右键菜单里的命令是发现不了的（ADR 0014 决策 5），⌘C 与 ⇧⌘C 挨着摆。"""
    edit = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "编辑" and action.menu() is not None
    )

    assert edit is not None
    labels = [action.text() for action in edit.actions()]
    assert "拷贝" in labels
    assert labels.index("拷贝") < labels.index("拷贝路径")
    assert window._copy_action.shortcut() == QKeySequence("Ctrl+C")


def test_reveal_selection_hands_paths_to_desktop(
    qt_app: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真跑会叫起访达 —— 测试里换成记录。"""
    seen: list[list[Path]] = []

    def fake_reveal(paths: Sequence[Path]) -> str:
        seen.append([Path(path) for path in paths])
        return "已在访达中显示。"

    monkeypatch.setattr(desktop, "reveal_in_finder", fake_reveal)
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))

    window.reveal_selection()

    assert seen == [[window._library.root / "京都" / PHOTO_NAMES[0]]]


def test_reveal_on_a_group_reveals_its_photos(
    qt_app: QApplication, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⌘⏎ 对分组卡片摊平：那批照片在访达里实实在在地摆着（ADR 0020）。"""
    seen: list[list[Path]] = []

    def fake_reveal(paths: Sequence[Path]) -> str:
        seen.append([Path(path) for path in paths])
        return "已在访达中显示。"

    monkeypatch.setattr(desktop, "reveal_in_finder", fake_reveal)
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    window.reveal_selection()

    assert seen == [[window._library.root / "京都" / PHOTO_NAMES[0]]]
    assert window._reveal_action.isEnabled() is True


def test_reveal_says_command_return_and_the_key_really_opens_finder(
    qt_app: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「在访达中显示」是 ⌘⏎，而且这条**要能真的按出来**，不是只写在菜单上的字。

    ⏎ 归「打开」（网格自己处理，见 ``PhotoGrid.keyPressEvent``），所以这条要的是
    「加 ⌘ 的回车」—— 回车行与 ⌘ 回车行挨着，画的是同一列，用户才认得出是同一族键。

    按键这一段不能省：``QAction`` 的快捷键只有被 ``addAction`` 加进某个控件之后才会注册进
    Qt 的快捷键表，而右键菜单是用完即弃的 —— 只挂菜单的话，菜单上写着 ⌘⏎、按下去没反应。
    """
    seen: list[list[Path]] = []

    def fake_reveal(paths: Sequence[Path]) -> str:
        seen.append([Path(path) for path in paths])
        return "已在访达中显示。"

    monkeypatch.setattr(desktop, "reveal_in_finder", fake_reveal)
    _enter_album(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert row >= 0
    menu = _context_at(window, row)

    # 显示的那一半：⌘ 与 ↵ 各占一列，键位就是「打开」那一行的同一个符号。
    assert menu is not None
    modifiers, key_text = _key_cell(menu, "在访达中显示")
    assert modifiers == frozenset({Qt.KeyboardModifier.ControlModifier})
    assert key_text == menus.OPEN_KEY_HINT == "↵"

    # 生效的那一半：真的按一次 ⌘⏎。
    window._grid.setFocus()
    QTest.keyClick(window._grid, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qt_app.processEvents()

    assert seen == [[window._library.root / "京都" / PHOTO_NAMES[0]]]
    assert window._reveal_action.shortcut() == QKeySequence("Ctrl+Return")


def test_reveal_shortcut_yields_to_the_rename_editor(
    qt_app: QApplication, window: MainWindow
) -> None:
    """改到一半那一下归输入框：⌘⏎ 不该顺手把访达叫起来。

    窗口级快捷键在按键到达控件之前就被 Qt 取走了，所以这里必须**主动置灰**，
    否则「敲完名字顺手 ⌘⏎」提交不了改名，只会弹出一个访达。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))
    assert window._reveal_action.isEnabled() is True

    window.rename_current_entry()

    assert window._grid._rename_edit.is_editing()
    assert window._reveal_action.isEnabled() is False

    # 收尾：Esc 取消，测试不该顺手把 tmp 里的照片真改了名。
    QTest.keyClick(window._grid._rename_edit, Qt.Key.Key_Escape)


def _record_default_app(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """把「用默认应用打开」换成记录用的假实现，返回收路径的那个列表。"""
    seen: list[Path] = []

    def fake_open(path: Path) -> str | None:
        seen.append(Path(path))
        return None

    monkeypatch.setattr(desktop, "open_with_default_app", fake_open)
    return seen


def test_open_default_app_is_shift_return_and_the_key_really_opens_it(
    qt_app: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「在默认应用中打开」是 ⇧⏎，而且这条**要能真的按出来**，不是只写在菜单上的字。

    ⏎ 归「打开」、⌘⏎ 归「在访达中显示」，每一层加一个修饰符，于是三个「打开」挨着摆、
    画的也是同一列，用户才认得出是同一族键。

    按键这一段不能省：``QAction`` 的快捷键只有被 ``addAction`` 加进某个控件之后才会注册进
    Qt 的快捷键表，而右键菜单是用完即弃的 —— 只挂菜单的话，菜单上写着 ⇧⏎、按下去没反应。
    """
    seen = _record_default_app(monkeypatch)
    _enter_album(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert row >= 0
    menu = _context_at(window, row)

    # 显示的那一半：⇧ 与 ↵ 各占一列，键位就是「打开」那一行的同一个符号。
    assert menu is not None
    modifiers, key_text = _key_cell(menu, "在默认应用中打开")
    assert modifiers == frozenset({Qt.KeyboardModifier.ShiftModifier})
    assert key_text == menus.OPEN_KEY_HINT == "↵"

    # 生效的那一半：真的按一次 ⇧⏎。
    window._grid.setFocus()
    QTest.keyClick(window._grid, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qt_app.processEvents()

    assert seen == [window._library.root / "京都" / PHOTO_NAMES[0]]
    assert window._open_default_action.shortcut() == QKeySequence("Shift+Return")


def test_open_default_app_is_silent_when_it_works_and_speaks_when_it_fails(
    qt_app: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """打开成功时横幅一个字都不说；系统不接时必须说出来。

    安静的那一半是故意的：那个应用自己会弹到前台，横幅再抢一句只是噪音；而静默**失败**
    会被当成程序卡住了。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))

    monkeypatch.setattr(desktop, "open_with_default_app", lambda path: None)
    window.open_current_in_default_app()
    assert window._banner.isVisible() is False

    monkeypatch.setattr(
        desktop,
        "open_with_default_app",
        lambda path: f"没能用默认应用打开「{Path(path).name}」，请检查它是否还在原处。",
    )
    window.open_current_in_default_app()

    assert window._banner.isVisible()
    assert PHOTO_NAMES[0] in window._banner.text()


def test_open_default_app_skips_group_cards_and_does_not_fall_back_to_open(
    qt_app: QApplication,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """选中分组卡片时：这一条不可用，⇧⏎ 也不该偷偷变成「在 Gallery 里打开」。

    后半句是网格那一侧的判据（``PhotoGrid.keyPressEvent``）：⇧⏎ 属于窗口级的动作，
    不可用时按键会落到网格上，而网格只把**不带修饰符**的回车当成「打开」。要让用户以为
    「换个地方打开」最后打开的还是 Gallery，不如什么都不做。
    """
    seen = _record_default_app(monkeypatch)
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))
    assert window._open_default_action.isEnabled() is False

    window._grid.setFocus()
    QTest.keyClick(window._grid, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qt_app.processEvents()

    assert seen == []
    # 判据是「有没有真的把组打开」而不是「还在不在网格页」：进组同样会把网格重新列一遍
    # （``_reload_level``），看着页面没变，其实在 GroupRef 上白跑了一趟。
    assert window._group_view is None, "不该退回「打开」把组打开"


def test_open_default_app_yields_to_the_rename_editor(
    qt_app: QApplication, window: MainWindow
) -> None:
    """改到一半那一下归输入框：⇧⏎ 不该顺手把别的程序叫起来。

    与 ⌘⏎ 同一个理由 —— 窗口级快捷键在按键到达控件之前就被 Qt 取走了，不置灰的话
    「敲完名字顺手 ⇧⏎」提交不了改名，只会弹出一个预览。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))
    assert window._open_default_action.isEnabled() is True

    window.rename_current_entry()

    assert window._grid._rename_edit.is_editing()
    assert window._open_default_action.isEnabled() is False

    # 收尾：Esc 取消，测试不该顺手把 tmp 里的照片真改了名。
    QTest.keyClick(window._grid._rename_edit, Qt.Key.Key_Escape)


def test_bare_return_still_opens_in_gallery_after_the_shift_guard(
    qt_app: QApplication, window: MainWindow
) -> None:
    """加了「带修饰符就不算打开」这条判据之后，**光秃秃的 ⏎ 必须照旧打开大图**。

    判据写在网格的 ``keyPressEvent`` 上，方向很容易写反 —— 写成「有修饰符才算打开」的话，
    整个软件最常用的那个键就没了。
    """
    _enter_album(qt_app, window)
    _select(window._grid, window._model, (0,))

    window._grid.setFocus()
    QTest.keyClick(window._grid, Qt.Key.Key_Return)
    qt_app.processEvents()

    assert window._stack.currentWidget() is window._detail_page


def test_select_all_selects_whole_level(qt_app: QApplication, window: MainWindow) -> None:
    _enter_album(qt_app, window)
    assert window._model.rowCount() == len(PHOTO_NAMES)

    window.select_all_entries()

    assert window._grid.selected_rows() == [0, 1]


def test_copy_group_condition(qt_app: QApplication, window: MainWindow) -> None:
    photo = PhotoRef(path=window._library.root / "京都" / PHOTO_NAMES[0], size=1, mtime_ns=1)
    group = GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo, photo))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    window.copy_current_group()

    # 「按相机分组：Canon EOS R5 · 2 张」：粘到别处也说得清它是哪一组。
    assert QGuiApplication.clipboard().text() == "按「相机」分组：Canon EOS R5 · 2 张"


def test_copy_paths_covers_the_photos_of_a_group_card(
    qt_app: QApplication, window: MainWindow
) -> None:
    """⇧⌘C 对分组卡片摊平，而且**一张照片只出现一次**（ADR 0020）。

    去重是必要的：组卡片与它的一张组员被一起选中时，那张照片只该算一次 —— 否则粘到
    别处会多出一张「一张 2.jpg」。
    """
    member = _photo(window)
    other = PhotoRef(path=window._library.root / "京都" / PHOTO_NAMES[1], size=1, mtime_ns=1)
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(member,))
    # 第 1 行就是那张组员本人：选区里它出现两次（一次作为组员、一次作为一格）。
    window._model.reorder([group, member, other])
    qt_app.processEvents()
    _select(window._grid, window._model, (0, 1, 2))

    window.copy_selected_paths()

    assert QGuiApplication.clipboard().text().splitlines() == [
        str(window._library.root / "京都" / PHOTO_NAMES[0]),
        str(window._library.root / "京都" / PHOTO_NAMES[1]),
    ]


# ---- 大图页的右键菜单（ADR 0021）--------------------------------------------


def wait_until(qt_app: QApplication, predicate: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    qt_app.processEvents()
    return predicate()


def _open_detail(qt_app: QApplication, window: MainWindow, name: str) -> int:
    """打开某张照片的大图，等预览落屏，返回它的行号。

    **必须等解码**再往下走：画面还空着的时候右键根本不会有菜单（``DetailView`` 按
    ``has_image`` 挡着），菜单内容与作用对象也就无从断言。
    """
    _enter_album(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都" / name)
    assert row >= 0
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image), f"「{name}」没有在时限内显示出来"
    return row


def test_detail_menu_is_the_photo_menu_with_close_on_top(
    qt_app: QApplication, window: MainWindow
) -> None:
    """大图页：与网格里单张照片那份逐行一致，只把最上面的「打开」换成「退出」。

    差的不止第一行：「粘贴」整条没有（那一页没有「当前这一层」可粘），其余一行不差。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[0])

    menu = window.detail_context_menu()

    assert menu is not None
    assert _texts(menu) == [
        "退出",
        "在默认应用中打开",
        "在访达中显示",
        "",
        "拷贝",
        "拷贝路径",
        "复制到相册",
        "移动到相册",
        "",
        "重命名…",
        "移到废纸篓",
        "",
        "显示元数据信息",
        "复制元数据",
    ]


def test_detail_menu_differs_from_the_photo_menu_only_on_the_first_row(
    qt_app: QApplication, window: MainWindow
) -> None:
    """与网格那张**逐行对照**：只差第一行，其余一模一样。

    这条比「把行抄一遍」更能防退化：将来网格那张加了命令，这张忘了加，这里就红了。

    剪贴板先摆满，免得两张菜单的内容取决于执行顺序 —— 「粘贴」只出现在**相册**与
    **空白处**那两张里（ADR 0018 决策 7），照片与这一张都不摆，所以这里两边的行序
    本来就应当一字不差。
    """
    _enter_album(qt_app, window)
    desktop.copy_files_to_clipboard([window._library.root / "京都" / PHOTO_NAMES[1]])
    row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert row >= 0
    photo = _context_at(window, row)
    assert photo is not None
    photo_texts = _texts(photo)
    assert photo_texts[0] == "打开"
    assert "粘贴" not in photo_texts, "按在照片上不摆这一条（ADR 0018 决策 7）"

    _open_detail(qt_app, window, PHOTO_NAMES[0])
    menu = window.detail_context_menu()
    assert menu is not None
    detail_texts = _texts(menu)

    assert detail_texts[0] == "退出"
    assert detail_texts == ["退出", *photo_texts[1:]]


def test_detail_menu_says_escape_on_the_close_row(qt_app: QApplication, window: MainWindow) -> None:
    """「退出」那一行最右列写着 ``⎋``，界面上的字面就钉死成**那个符号**。

    与网格那张菜单的「打开 ↵」是同一件事的两面（``ShortcutColumnMenu.show_key``）：
    只画不生效的那枚提示，也让用户知道退出不必去菜单栏里找。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[0])

    menu = window.detail_context_menu()

    assert menu is not None
    assert _texts(menu)[0] == "退出"
    assert _key_hint(menu, "退出") == menus.CLOSE_KEY_HINT
    assert menus.CLOSE_KEY_HINT == "⎋"
    assert len(menus.CLOSE_KEY_HINT) == 1, "是一个符号，不是「Esc」三个字母"

    # 提示没变成真快捷键：大图页上的 Esc 归 DetailView，两个动作抢同一个键，
    # 谁先触发全看加载顺序（见 ``CLOSE_KEY_HINT`` 那段）。
    assert window._close_entry_action.shortcut().isEmpty()
    assert "打开" not in _texts(menu)


def test_detail_menu_has_no_paste_even_when_the_clipboard_has_files(
    qt_app: QApplication, window: MainWindow
) -> None:
    """剪贴板里**真有文件**时大图页也不摆「粘贴」—— 粘到哪还没有答案（ADR 0018）。

    这一条得先把剪贴板摆满再断言：默认那个 fixture 是空的，而网格那张菜单里
    「粘贴」出不出现完全取决于剪贴板，断言就成了执行顺序的函数。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[0])
    desktop.copy_files_to_clipboard([window._library.root / "京都" / PHOTO_NAMES[1]])
    assert window._can_paste() is True, "剪贴板里已经有文件，这一页却不粘"

    menu = window.detail_context_menu()

    assert menu is not None
    assert "粘贴" not in _texts(menu)


def test_detail_menu_is_enabled_for_the_viewed_photo(
    qt_app: QApplication, window: MainWindow
) -> None:
    """菜单里那些行该亮就亮，「重命名…」**摆着但置灰**。

    置灰是因为行内改名的输入框在网格里，而大图页的改名字段还没有 —— ADR 0021 明确
    先把菜单摆好、后端另一步接。一条亮着却不接线的行，点下去什么也不发生，比灰着更糟。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[0])

    menu = window.detail_context_menu()

    assert menu is not None
    for label in ("退出", "在默认应用中打开", "在访达中显示", "拷贝", "拷贝路径", "移到废纸篓"):
        assert _enabled(menu, label), f"「{label}」这张照片上有对象，就该亮着"
    assert _enabled(menu, "重命名…") is False
    assert window._trash_action.isEnabled() is True, "⌘⌫ 在大图页同样删得动这一张"


def test_detail_menu_acts_on_the_photo_you_are_looking_at(
    qt_app: QApplication, window: MainWindow
) -> None:
    """翻过图之后，菜单作用的是**正在看的那一张**，不是网格选区里那张。

    网格的选区停在大图打开时的第一张，而用户已经翻到第二张了 —— 命令作用到看不见的
    照片上，是这一页最容易出的错（ADR 0021）。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[0])
    # 双击进入大图时网格仍停在那格上（``_open_detail`` 自己不改选区，这里补上真实那一步）。
    window._grid.select_row(
        window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    )
    window.step_detail(1)
    assert wait_until(qt_app, lambda: window._current_row == 1)

    window.copy_selected_paths()

    assert QGuiApplication.clipboard().text() == str(window._library.root / "京都" / PHOTO_NAMES[1])
    assert window._grid.selected_rows() == [0], "网格的选区一动不动，这正是要防的那种错"


def test_no_detail_menu_outside_the_viewer(window: MainWindow) -> None:
    """不在大图页时就没有这份菜单（网格那一份归 ``context_menu_for`` 管）。"""
    assert window.detail_context_menu() is None


def test_trashing_the_photo_you_are_looking_at_shows_the_next_one(
    qt_app: QApplication, window: MainWindow
) -> None:
    """大图页里 ⌘⌫：删掉正在看的那张，**接着看下一张**，不退回网格。

    这是 ADR 0021 里那笔当时算过的账：删完之后人得留在一个说得清的地方，而看照片时
    最顺手的动作就是「这张不要，下一张」。
    """
    row = _open_detail(qt_app, window, PHOTO_NAMES[0])
    first = window._library.root / "京都" / PHOTO_NAMES[0]
    second = window._library.root / "京都" / PHOTO_NAMES[1]

    window.trash_selected()
    qt_app.processEvents()

    assert not first.exists(), "照片应当真的移进了废纸篓"
    assert window._stack.currentWidget() is window._detail_page, "不该退回网格"
    # 摘掉第 0 行之后，「下一张」正好顶在同一个行号上（见 _select_after_removal）。
    assert window._model.rowCount() == 1
    shown = window._model.photo_at(window._current_row)
    assert shown is not None and shown.path == second
    assert window._current_row == row
    assert window._detail.has_image is True
    # 网格与大图从此指着同一格：⌘Z 取回来的照片要插回它原来的位置。
    assert window._grid.selected_rows() == [row]


def test_trashing_the_last_photo_you_are_looking_at_falls_back_to_the_previous_one(
    qt_app: QApplication, window: MainWindow
) -> None:
    """正在看的是这一层的**最后一张**：没有下一张就看上一张，同样不退回网格。

    与「没有照片可看才退回网格」是两件事（见下一条），别混成一条规则。
    """
    _open_detail(qt_app, window, PHOTO_NAMES[1])

    window.trash_selected()
    qt_app.processEvents()

    assert window._stack.currentWidget() is window._detail_page
    shown = window._model.photo_at(window._current_row)
    assert shown is not None and shown.path.name == PHOTO_NAMES[0]


def test_trashing_the_last_photo_falls_back_to_the_grid(
    qt_app: QApplication, window: MainWindow
) -> None:
    """这一层再没有照片可看时退回网格 —— 显示一张已经不在的照片说不过去。"""
    _enter_album(qt_app, window)
    # 先从网格上删掉一张，让这一层只剩一张。
    _select(window._grid, window._model, (1,))
    window.trash_selected()
    qt_app.processEvents()
    assert window._model.rowCount() == 1

    _open_detail(qt_app, window, PHOTO_NAMES[0])
    window.trash_selected()
    qt_app.processEvents()

    assert window._stack.currentWidget() is window._grid_page
    assert window._model.rowCount() == 0
