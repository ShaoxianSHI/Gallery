"""⌘R 的另一种用法：没有选中任何一格时刷新当前层（ADR 0087）。

这里钉四件事：

1. 入口在「显示」菜单最上面，键是 ⌘R；
2. ⌘R 与「重命名」不会同时可用（两条同一个键同时启用时 Qt 会判成歧义，两条都不触发）；
3. 刷新真的从磁盘重读，监听器没有报上来的新增 / 删除也能补上（分组与日历各一条）；
4. 检索索引跟着对账：刷新之后新照片搜得到、删掉的搜不到。

监听器换成**什么都不报**的假实现：这一组用例要复现的正是「监听漏了，用户手动刷新」。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import wait_for_idle, wait_for_level, wait_until  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    prepare_library,
)
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.search import make_condition, parse_input  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 一张带拍摄时间的照片；日历要按它分桶。
CAPTURED = {36867: "2024:05:16 10:00:00"}


class _SilentWatcher:
    """监听器的假实现：启动 / 停止都记着，但**一条事件也不报**。"""

    def __init__(self) -> None:
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def _silent_starter(_library, _on_change, _on_error=None):  # type: ignore[no-untyped-def]
    return _SilentWatcher()


def _open(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_FLAT,
    group_facet: str | None = None,
) -> MainWindow:
    session_base = root.parent / f"{root.name}-状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode, group_facet=group_facet)
    prepare_library(root)
    window = MainWindow(
        session_base=session_base,
        initial_library=root,
        watch_starter=_silent_starter,
    )
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    wait_for_idle(qt_app, window, also=("_index_job",))
    return window


def _menu(window: MainWindow, title: str) -> QMenu:
    for action in window.menuBar().actions():
        if action.text() == title and action.menu() is not None:
            return action.menu()
    raise AssertionError(f"菜单栏里没有「{title}」")


def _condition(text: str):  # type: ignore[no-untyped-def]
    condition = make_condition(parse_input(text))
    assert condition is not None
    return condition


def _clear_selection(window: MainWindow) -> None:
    window._grid.select_for_context(-1)
    window._update_action_states()


def test_refresh_sits_at_the_top_of_the_display_menu(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = tmp_path / "照片"
    make_photo("一张.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        display = _menu(window, "显示")
        actions = display.actions()
        assert actions[0].text() == "刷新"
        assert actions[0].shortcut() == QKeySequence("Ctrl+R")
        assert actions[1].isSeparator(), "刷新要单独一组，不能混进「上一级」那一组"
    finally:
        window.close()


def test_cmd_r_has_exactly_one_owner_at_a_time(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """没有选中 → 刷新；选中一格 → 重命名。**两条同时可用就是两条都不能用。**"""
    root = tmp_path / "照片"
    make_photo("一张.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        _clear_selection(window)
        assert window._refresh_action.isEnabled() is True
        assert window._rename_action.isEnabled() is False

        window._grid.select_row(0)
        qt_app.processEvents()
        window._update_action_states()
        assert window._rename_action.isEnabled() is True
        assert window._refresh_action.isEnabled() is False

        window._open_detail(window._grid.currentIndex())
        qt_app.processEvents()
        window._update_action_states()
        assert window._rename_action.isEnabled() is True, "大图页的 ⌘R 仍然是改标题栏"
        assert window._refresh_action.isEnabled() is False
    finally:
        window.close()


def test_cmd_r_without_a_selection_refreshes_the_grid(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """监听器没报上来的新文件：按 ⌘R 之后真的要出现在网格里，而且没有顺手改名。"""
    root = tmp_path / "照片"
    make_photo("第一张.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        _clear_selection(window)
        added = make_photo("后来的一张.jpg", directory=root)
        assert window._model.row_of_path(added) < 0, "前提：监听器没有报这条新增"

        QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        wait_for_idle(qt_app, window, also=("_index_job",))

        assert window._model.row_of_path(added) >= 0
        assert window._grid.is_renaming() is False, "没选中东西时 ⌘R 不该进重命名"
    finally:
        window.close()


def test_cmd_r_with_a_selection_still_renames(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = tmp_path / "照片"
    make_photo("第一张.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        window._grid.select_row(0)
        qt_app.processEvents()
        window._update_action_states()

        QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        qt_app.processEvents()

        assert window._grid.is_renaming() is True
    finally:
        window.close()


def test_refresh_removes_a_photo_deleted_behind_the_watcher(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = tmp_path / "照片"
    first = make_photo("第一张.jpg", directory=root)
    second = make_photo("第二张.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        _clear_selection(window)
        first.unlink()

        window._refresh_action.trigger()
        wait_for_idle(qt_app, window, also=("_index_job",))

        assert window._model.row_of_path(first) < 0
        assert window._model.row_of_path(second) >= 0
    finally:
        window.close()


def test_refresh_reconciles_the_search_index(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """新增的照片搜得到、删掉的搜不到 —— 索引也得跟着这次磁盘现状对一遍。"""
    root = tmp_path / "照片"
    old = make_photo("旧的.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        library = window._library
        assert library is not None
        assert [ref.name for ref in library.search_photos([_condition("旧的")]).refs] == [
            "旧的.jpg"
        ]

        _clear_selection(window)
        old.unlink()
        make_photo("新的.jpg", directory=root)
        window._refresh_action.trigger()
        wait_for_idle(qt_app, window, also=("_index_job",))

        assert [ref.name for ref in library.search_photos([_condition("新的")]).refs] == [
            "新的.jpg"
        ]
        assert library.search_photos([_condition("旧的")]).refs == []
    finally:
        window.close()


def test_refresh_inside_a_group_regroups_and_returns_to_it(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """分组里刷新：组员是快照，必须整库重算；算完还得回到原来那个组。"""
    root = tmp_path / "照片"
    make_photo("第一张.jpg", directory=root)
    make_photo("第二张.jpg", directory=root)
    window = _open(qt_app, root, group_facet="format")
    try:
        groups = [entry for entry in window._model.entries() if isinstance(entry, GroupRef)]
        assert len(groups) == 1, "前提：两张 JPEG 按格式分在一个组里"
        key = groups[0].key
        window.enter_group(groups[0])
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 2, what="分组")
        wait_for_idle(qt_app, window, also=("_index_job",))

        _clear_selection(window)
        added = make_photo("后来的一张.jpg", directory=root)
        assert window._refresh_action.isEnabled() is True

        window._refresh_action.trigger()
        wait_for_idle(qt_app, window, also=("_index_job",))
        wait_for_level(
            window, qt_app, lambda: window._model.row_of_path(added) >= 0, what="刷新后的分组"
        )

        assert window._group_view is not None
        assert window._group_view.key == key, "刷新之后该回到原来那个组"
        assert window._model.rowCount() == 3
    finally:
        window.close()


def test_refresh_on_the_calendar_updates_the_buckets(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """日历页刷新：删掉的那张也要从日历格子里走掉（空库也要报给日历）。"""
    root = tmp_path / "照片"
    picture = make_photo("五月.jpg", directory=root, exif_ifd=CAPTURED)
    window = _open(qt_app, root, browse_mode=BROWSE_MODE_CALENDAR)
    try:
        page = window._calendar_page
        page.set_today(datetime.date(2024, 5, 16))
        page.go_today()
        assert wait_until(qt_app, lambda: page.photo_count() == 1), "前提：日历上看得到那一张"

        picture.unlink()
        window._update_action_states()
        assert window._refresh_action.isEnabled() is True

        window._refresh_action.trigger()
        wait_for_idle(qt_app, window, also=("_index_job",))

        assert wait_until(qt_app, lambda: page.photo_count() == 0)
        assert window._model.rowCount() == 0
    finally:
        window.close()


def test_refresh_is_off_in_a_search_result_layer(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """搜索结果是查询、不是目录层：那里按 ⌘R 什么也不做。"""
    root = tmp_path / "照片"
    make_photo("海边.jpg", directory=root)
    window = _open(qt_app, root)
    try:
        _clear_selection(window)
        window._search_field._edit.setText("海边")
        window._search_field.submit()
        wait_until(qt_app, lambda: window._search_frame is not None)
        window._update_action_states()

        assert window._refresh_action.isEnabled() is False
    finally:
        window.close()
