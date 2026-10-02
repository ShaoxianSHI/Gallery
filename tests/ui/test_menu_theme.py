"""菜单面板的底色必须跟窗口是同一块（README 设计原则第 6 条）。

背景：统一底色是在 ``_build_ui`` 里下发的，而菜单是**之后**才建出来的 ——
菜单栏里那几个在 ``_build_actions`` 里建，右键菜单更晚，按一次才建。它们因此一直用
系统菜单色（浅色 ``#ececec`` / 深色 ``#2b2b2b``），与窗口的纯色底差一档：按一下菜单
的那一刻，界面就从「一整块底色」掉出去了。

这里钉住两件事：菜单栏里的菜单（含子菜单）与右键菜单都拿到窗口那份调色板，且系统
换浅色 / 深色时菜单栏里的菜单会跟着一起变。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.ui import menus, theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.theme import rgba  # noqa: E402

#: 「假装系统换成了深色」用的底色。真深色下 Qt 给的是别的值，这里要的是
#: 「换一个和当前不同的值」，具体是哪个数与这条测试无关。
FLIPPED_BACKGROUND = QColor(23, 23, 23)

#: 等待扫描完成的时限。
SCAN_TIMEOUT_SECONDS = 10.0


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(root / "一张.jpg")
    prepare_library(root)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    _wait_for_scan(qt_app, instance)
    yield instance
    instance.close()


def _wait_for_scan(qt_app: QApplication, window: MainWindow) -> None:
    """转到扫描结束：网格里一个格子都没有时，右键菜单那几条无从断言。"""
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._scan_job is None and window._model.rowCount() > 0:
            return
        time.sleep(0.01)
    qt_app.processEvents()


def _bar_menu(window: MainWindow, title: str) -> QMenu:
    for action in window.menuBar().actions():
        if action.text() == title:
            assert action.menu() is not None
            return action.menu()
    raise AssertionError(f"菜单栏里没有「{title}」")


def _submenu(parent: QMenu, title: str) -> QMenu:
    for action in parent.actions():
        if action.text() == title:
            assert action.menu() is not None
            return action.menu()
    raise AssertionError(f"「{parent.title()}」里没有子菜单「{title}」")


def _background(menu: QMenu) -> str:
    return menu.palette().color(QPalette.ColorRole.Window).name()


def test_menu_bar_menus_share_window_background(window: MainWindow) -> None:
    """菜单栏里的菜单与它们的子菜单，都得是窗口那块底色。"""
    window_background = window.palette().color(QPalette.ColorRole.Window).name()

    menus = (
        _bar_menu(window, "文件"),
        _bar_menu(window, "视图"),
        _submenu(_bar_menu(window, "文件"), "最近打开"),
    )
    for menu in menus:
        assert _background(menu) == window_background


def test_context_menu_shares_window_background(qt_app: QApplication, window: MainWindow) -> None:
    """右键菜单是按下那一刻才建的，拿不到任何一次主题下发，得自己照抄一份。"""
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    assert _background(menu) == window.palette().color(QPalette.ColorRole.Window).name()


def test_context_menu_carries_our_look(qt_app: QApplication, window: MainWindow) -> None:
    """圆角与行距在样式表里，而**底色必须一起写进去**。

    这是实测出来的坑：给 ``QMenu`` 加了样式表之后，面板就不再由 macOS 样式画，
    底色会掉回系统菜单色（浅色 ``#ececec``）。样式表里若不写底色，上一条测试
    断言的「与窗口同色」就会当场作废 —— 两条必须一起成立。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    sheet = menu.styleSheet()
    assert f"border-radius: {menus.MENU_CORNER_RADIUS}px" in sheet
    assert f"padding: {menus.MENU_INSET}px" in sheet
    assert menus.MENU_ITEM_PADDING in sheet
    assert rgba(window.palette().color(QPalette.ColorRole.Window)) in sheet


def test_context_menu_has_hover_highlight(qt_app: QApplication, window: MainWindow) -> None:
    """菜单必须有悬停 / 当前项的高亮带。

    实测过的坑：只要给 ``QMenu`` 挂了样式表，系统样式那份高亮就**不再画**了 ——
    原生菜单按方向键会把当前行点亮成强调色，加了样式表之后按 ↓ 两次所有行仍是底色，
    用户根本看不出鼠标在哪一行。所以这两条规则是补回来的，不能删。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)

    assert menu is not None
    sheet = menu.styleSheet()
    highlight = rgba(menu.palette().color(QPalette.ColorRole.Highlight))
    assert "QMenu::item:hover" in sheet
    assert "QMenu::item:selected" in sheet
    assert sheet.count(highlight) >= 2, "两条规则都得用调色板里的高亮色"
    # 高亮带是内缩的圆角带。**不许写 margin**：margin 属于盒子模型，写在 :selected 里
    # 等于每悬停一次换一次盒子，文字会跟着挪位置（用户实测）。内缩由菜单自身的内边距
    # 决定，与状态无关。
    assert f"border-radius: {menus.MENU_HIGHLIGHT_RADIUS}px" in sheet
    assert "margin" not in sheet
    # 内缩的那一处在菜单上，**四边同值**：只留左右的话，高亮带会顶着面板上下两条边
    assert f"padding: {menus.MENU_INSET}px" in sheet


def test_shortcuts_become_a_right_aligned_column(qt_app: QApplication, window: MainWindow) -> None:
    """快捷键要排成右对齐的一列，菜单关掉后原样还原。

    样式表模式下 Qt 不再给快捷键算制表位列（它被画在文字后面，右沿恒为 78.5），QSS 的
    ``padding-right`` / ``::indicator`` 都调不动；正路是关掉自动绘制、把「标签 + 制表符 +
    快捷键」写进文字，让 Qt 自己排那一列（见 ``menus.with_native_shortcuts``）。

    动作是菜单栏与右键菜单**共用**的，所以必须还原 —— 否则菜单栏上会出现「拷贝路径⇧⌘C」
    再加上它自己画的那一列。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    menu = window.context_menu_for(row)
    assert menu is not None
    original = {id(a): a.text() for a in menu.actions()}
    flagged = {id(a): a.isShortcutVisibleInContextMenu() for a in menu.actions()}
    with_shortcut = [a for a in menu.actions() if not a.shortcut().isEmpty()]
    assert with_shortcut, "这张菜单上至少有一条带快捷键"

    restore = menus.with_native_shortcuts(menu)
    try:
        for action in with_shortcut:
            native = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            assert "\t" in action.text()
            assert action.text().endswith(native)
            assert not action.isShortcutVisibleInContextMenu()
    finally:
        restore()

    for action in menu.actions():
        assert action.text() == original[id(action)]
        assert action.isShortcutVisibleInContextMenu() == flagged[id(action)]


def test_submenu_arrow_is_shrunk(qt_app: QApplication, window: MainWindow) -> None:
    """子菜单的右箭头缩一档（Qt 默认给的偏大）。"""
    window._stack.setCurrentWidget(window._grid_page)

    menu = window.context_menu_for(-1)

    assert menu is not None
    assert f"width: {menus.MENU_ARROW_SIZE}px" in menu.styleSheet()
    assert f"height: {menus.MENU_ARROW_SIZE}px" in menu.styleSheet()


def test_menus_without_checkable_items_keep_the_text_column(
    qt_app: QApplication, window: MainWindow
) -> None:
    """没有可勾选项的菜单，文字也要与照片菜单对齐。

    Qt 只在菜单里**存在可勾选项**时才预留那一列对勾宽度（实测 19 像素），整列文字
    跟着右移：照片菜单文字在 28，相册 / 分组 / 空白处只有 9。菜单里根本没有对勾可画，
    留着那列空白没有道理，但文字忽左忽右更难看 —— 所以统一补上，全软件的文字列对齐。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0

    photo = window.context_menu_for(row)
    blank = window.context_menu_for(-1)

    assert photo is not None and blank is not None
    gutter = f"padding-left: {menus.MENU_CHECK_GUTTER}px"
    assert gutter not in photo.styleSheet(), "照片菜单有可勾选项，那一列本来就预留了"
    assert gutter in blank.styleSheet(), "空白处菜单没有可勾选项，必须自己补上那一列"


def test_submenus_get_the_same_look(qt_app: QApplication, window: MainWindow) -> None:
    """空白处那份菜单带三个子菜单：子菜单不继承样式表，也不是子控件，得显式发。"""
    window._stack.setCurrentWidget(window._grid_page)

    menu = window.context_menu_for(-1)

    assert menu is not None
    owned = [
        action.menu()
        for action in menu.actions()
        if action.menu() is not None and action.menu().menuAction() is action
    ]
    assert [sub.title() for sub in owned] == ["分组", "排序", "缩略图大小"]
    # 三份子菜单彼此同一份样式；它们里面全是单选项（有勾），所以都不补那一列
    gutter = f"padding-left: {menus.MENU_CHECK_GUTTER}px"
    assert len({submenu.styleSheet() for submenu in owned}) == 1
    for submenu in owned:
        assert gutter not in submenu.styleSheet()


def test_menu_bar_menus_are_left_native(window: MainWindow) -> None:
    """菜单栏那边**不该**被右键菜单的样式波及。

    ``QAction`` 是全局复用的，``some_action.menu()`` 可能指向菜单栏里的子菜单；
    递归时若不认「这个子菜单的 ``menuAction()`` 是不是当前这条 action」，
    「视图 → 排序 / 分组 / 缩略图大小」会被顺带改掉 —— 它们并不属于这次右键。
    """
    window._stack.setCurrentWidget(window._grid_page)
    row = window._model.row_of_path(window._library.root / "一张.jpg")
    assert row >= 0
    assert window.context_menu_for(row) is not None

    for title in ("文件", "编辑", "视图"):
        assert _bar_menu(window, title).styleSheet() == ""
    view = _bar_menu(window, "视图")
    for title in ("浏览方式", "分组", "排序", "缩略图大小"):
        assert _submenu(view, title).styleSheet() == ""


def test_menu_bar_menus_follow_theme_change(qt_app: QApplication, window: MainWindow) -> None:
    """系统换浅色 / 深色之后重下一次底色，菜单要跟着一起变。

    **只有这个测试会去动应用调色板** —— 它要的就是「系统换了一套颜色」这个前提，
    而那正是产品代码永远不做的一步（见 ``ui.theme`` 模块文档里那两个坑）。
    """
    original = QPalette(qt_app.palette())
    before = window.palette().color(QPalette.ColorRole.Window).name()
    try:
        flipped = QPalette(original)
        flipped.setColor(QPalette.ColorRole.Base, FLIPPED_BACKGROUND)
        flipped.setColor(QPalette.ColorRole.Window, FLIPPED_BACKGROUND)
        flipped.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        qt_app.setPalette(flipped)

        theme.apply_theme(qt_app)

        after = window.palette().color(QPalette.ColorRole.Window).name()
        assert after != before, "测试前提不成立：底色根本没换成功，这条断言测不到东西"
        assert _background(_bar_menu(window, "文件")) == after
    finally:
        qt_app.setPalette(original)
        theme.apply_theme(qt_app)
