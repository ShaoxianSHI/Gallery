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
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

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
