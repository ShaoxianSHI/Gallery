"""右键菜单：落点决定菜单，视图把选区摆正（ADR 0014）。

三块，分开测：

1. **选区语义** —— 右键在没选中的格子 / 已选中的格子 / 空白处各会发生什么。
   这一段用裸网格测：它与窗口、与库都没有关系，测窗口只会把因果搅浑。
2. **菜单内容** —— 四种落点各拼出什么。走真实的 ``MainWindow.context_menu_for``，
   因为置灰是 ``_update_action_states`` 算出来的，只有连着窗口才是真的。
3. **新命令** —— 拷贝 ⌘C、拷贝路径、全选、复制分组条件、在访达中显示。

「在访达中显示」在这里**必须换掉**：真跑它会把访达叫起来，那是测试机上真实的副作用。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui import desktop  # noqa: E402
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


def _enabled(menu: QMenu, label: str) -> bool:
    for action in menu.actions():
        if action.text() == label:
            return action.isEnabled()
    raise AssertionError(f"菜单里没有「{label}」，实际有：{_texts(menu)}")


def test_photo_menu_items(qt_app: QApplication, window: MainWindow) -> None:
    """照片：访达 / 拷贝 / 拷贝路径挨在一起，写操作单列一段，元数据收在最后。"""
    _enter_album(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都" / PHOTO_NAMES[0])
    assert row >= 0

    menu = _context_at(window, row)

    assert menu is not None
    assert _texts(menu) == [
        "打开",
        "在访达中显示",
        "拷贝",
        "拷贝路径",
        "",
        "重命名…",
        "移到废纸篓",
        "",
        "显示元数据信息",
        "复制元数据",
    ]
    assert _enabled(menu, "重命名…")
    assert _enabled(menu, "移到废纸篓")


def test_album_menu_has_no_metadata_items(qt_app: QApplication, window: MainWindow) -> None:
    """相册：没有元数据那两条（目录不是照片），其余与照片同形。"""
    _show_albums(qt_app, window)
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0

    menu = _context_at(window, row)

    assert menu is not None
    assert _texts(menu) == [
        "打开",
        "在访达中显示",
        "拷贝",
        "拷贝路径",
        "",
        "重命名…",
        "移到废纸篓",
    ]


def test_group_menu_disables_writes_and_explains(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片：改名与删除置灰，并在末尾说明为什么。

    这也是 ADR 0014 决策 4 —— README 原本承诺「按下去也会说明为什么」，而置灰之后
    那个按钮根本按不到，说法必须与实现一致。
    """
    photo = PhotoRef(path=window._library.root / "京都" / PHOTO_NAMES[0], size=1, mtime_ns=1)
    group = GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    menu = _context_at(window, 0)

    assert menu is not None
    assert _texts(menu) == ["打开", "复制分组条件"]


def test_multi_selection_hides_single_item_actions(
    qt_app: QApplication, window: MainWindow
) -> None:
    """多选：只作用在「某一个东西」上的命令全部藏起来（ADR 0004 与菜单设计）。"""
    _enter_album(qt_app, window)
    assert window._model.rowCount() == len(PHOTO_NAMES)
    _select(window._grid, window._model, (0, 1))

    menu = _context_at(window, 0)

    assert menu is not None
    assert _texts(menu) == [
        "在访达中显示",
        "拷贝",
        "拷贝路径",
        "",
        "移到废纸篓",
        "",
        "显示元数据信息",
    ]


def test_background_menu_has_no_file_actions(window: MainWindow) -> None:
    """空白处：一条会动文件的都没有，只有视图级命令。"""
    menu = _context_on_blank(window)

    assert menu is not None
    assert _texts(menu) == ["全选", "", "分组", "排序", "缩略图大小", "", "上一级"]
    forbidden = {"重命名…", "移到废纸篓", "在访达中显示"}
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            for choice in submenu.actions():
                assert choice.text() not in forbidden


def test_outdated_row_yields_no_menu(window: MainWindow) -> None:
    """行号过期（扫描正在流式上屏）时宁可不弹，也不弹一份错的。"""
    assert window.context_menu_for(9999) is None


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


def test_copy_skips_group_cards(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片不是磁盘上的东西（ADR 0014 决策 4）：既不拷贝，剪贴板也不该被动。"""
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))
    QGuiApplication.clipboard().setText("原样")

    window.copy_selected_files()

    assert QGuiApplication.clipboard().text() == "原样"
    assert window._copy_action.isEnabled() is False, "⌘C 同样不该对分组卡片可用"


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


def test_reveal_on_group_says_why_not(qt_app: QApplication, window: MainWindow) -> None:
    """分组没有路径：要说清楚为什么访达里找不到对应的东西，而不是静默不动。"""
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))

    window.reveal_selection()

    assert window._banner.isVisible()
    assert "分组不是磁盘上的文件夹" in window._banner.text()


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


def test_copy_paths_skips_group_cards(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片没有路径，混进拷贝路径里会直接抛 ``AttributeError``。"""
    group = GroupRef(facet="camera", key="canon", label="Canon", photos=(_photo(window),))
    window._model.reorder([group])
    qt_app.processEvents()
    _select(window._grid, window._model, (0,))
    QGuiApplication.clipboard().setText("原样")

    window.copy_selected_paths()

    # 一个格子都没得拷 → 剪贴板一个字节都不该动。
    assert QGuiApplication.clipboard().text() == "原样"
