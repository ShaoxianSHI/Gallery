"""``Esc`` 在退不动的层上取消选中（ADR 0070）。

退得回去时 ``Esc`` 仍然只退一层，选中一个对象不能把它从导航里抢走（ADR 0040 决策 11）。
因此在「全部照片」、相册模式的库根、日历页这些没有上一层的地方，它才有第二份工作：
把当前点中的文件夹 / 分组 / 照片 / 日历格收掉。

按键一律发**真实的 ``QTest.keyClick``**，不直接调方法：方法在而键没挂上、动作被置灰，
症状都是「按了没反应」，而直接调方法的那几条照样绿。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_level, wait_until  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    prepare_library,
)
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 分组字段：按「所在文件夹」分（只看路径，不读 EXIF）。
FOLDER_FACET = "folder"


def open_window(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_ALBUMS,
    group_facet: str | None = None,
) -> MainWindow:
    """开一个窗口，等当前这一层列完。"""
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode, group_facet=group_facet)
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    wait_for_level(window, qt_app, lambda: window._scan_job is None and window._facet_job is None)
    return window


def press_escape(window: MainWindow, qt_app: QApplication) -> None:
    """在当前页面按一下 ``Esc``，并等那枚 0 毫秒的动作状态定时器跑完。"""
    target = window._calendar_page.current_view() if window._on_calendar_page() else window._grid
    QTest.keyClick(target, Qt.Key.Key_Escape)
    wait_until(qt_app, lambda: not window._action_states_timer.isActive())


def test_escape_on_the_flat_root_deselects_a_photo(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """「全部照片」没有上一层：Esc 收掉选中的那张照片。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library)
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT)
    try:
        assert window._can_go_up() is False, "前提不成立：这一层其实退得回去"
        window._grid.select_row(0)
        wait_until(qt_app, window._up_action.isEnabled)
        assert window._up_action.text() == "取消选中", "退不动时那一行该说它此刻做什么"

        press_escape(window, qt_app)

        assert window._grid.genuinely_selected_rows() == [], "Esc 没有取消选中"
        assert window._grid.current_row() == -1, "当前项还留着，命令会指向一个没高亮的格子"
        assert window._up_action.isEnabled() is False, "没有对象可取消了，它该跟着关掉"
        assert window._stack.currentWidget() is window._grid_page, "退不动的时候不该换页"
    finally:
        window.close()


def test_escape_on_the_album_root_deselects_a_folder(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册模式的库根：Esc 收掉选中的相册卡片，不把它打开。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 2, what="库根")
        window._grid.select_row(0)
        wait_until(qt_app, window._up_action.isEnabled)

        press_escape(window, qt_app)

        assert window._grid.genuinely_selected_rows() == [], "Esc 没有取消选中"
        assert window._current_folder() == library, "Esc 把相册打开了"
    finally:
        window.close()


def test_escape_on_the_album_root_deselects_a_group_card(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """分组卡片也算选中的对象：Esc 一样把它收掉。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "山巅")
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT, group_facet=FOLDER_FACET)
    try:
        wait_for_level(
            window,
            qt_app,
            lambda: window._model.rowCount() == 2 and window._facet_job is None,
            what="分组",
        )
        assert isinstance(window._model.entry_at(0), GroupRef), "前提不成立：第一格不是组卡片"
        window._grid.select_row(0)
        wait_until(qt_app, window._up_action.isEnabled)

        press_escape(window, qt_app)

        assert window._grid.genuinely_selected_rows() == [], "Esc 没有取消选中分组卡片"
        assert window._group_view is None, "Esc 把分组打开了"
    finally:
        window.close()


def test_escape_still_walks_up_when_it_can(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """退得回去时它仍然只退一层：选中一个对象不能把导航抢走。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="相册内")
        window._grid.select_row(0)
        wait_until(qt_app, window._up_action.isEnabled)
        assert window._up_action.text() == "上一级", "退得动的时候这一行仍是「上一级」"

        press_escape(window, qt_app)
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="退回库根")

        assert window._current_folder() == library, "Esc 没有退上一层"
        assert window._grid.genuinely_selected_rows() == [0], "退回来该停在刚离开的那一格"
    finally:
        window.close()


def test_escape_on_the_calendar_root_clears_the_selected_day(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """日历页也在最外层：Esc 收掉点过的那一格高亮。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library)
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_CALENDAR)
    try:
        assert window._can_go_up() is False, "前提不成立：这一层其实退得回去"
        window._calendar_page.set_selected(datetime.date.today())
        wait_until(qt_app, window._up_action.isEnabled)
        assert window._up_action.text() == "取消选中"

        press_escape(window, qt_app)

        assert window._calendar_page.selected_day() is None, "Esc 没有取消日历的选中"
        assert window._up_action.isEnabled() is False
    finally:
        window.close()
