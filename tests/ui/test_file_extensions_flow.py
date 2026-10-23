"""视图菜单里的「显示文件扩展名」：摆在元数据信息上面，立刻生效，并且不碰改名的输入框。

隐藏扩展名只是**显示**：排序仍按完整名字，改名时输入框里也必须是完整名字 —— 否则用户
直接回车就把 ``.jpg`` 改没了（那是在改用户的文件）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.library.config import BROWSE_MODE_FLAT, prepare_library  # noqa: E402
from gallery.core.library.session import SessionState, load_session, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 测试库里的照片名。
PHOTO = "海边日落.jpg"


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    make_photo(PHOTO, directory=root)
    prepare_library(root)
    session_base = tmp_path / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_FLAT)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    wait_for_idle(qt_app, instance)
    yield instance
    instance.close()


def _view_menu(window: MainWindow):
    for action in window.menuBar().actions():
        if action.text() == "视图":
            assert action.menu() is not None
            return action.menu()
    raise AssertionError("菜单栏里没有「视图」")


def _display(window: MainWindow, row: int) -> object:
    return window._model.data(window._model.index(row, 0), int(Qt.ItemDataRole.DisplayRole))


def test_the_switch_sits_just_above_show_metadata(window: MainWindow) -> None:
    labels = [action.text() for action in _view_menu(window).actions()]

    assert labels.index("显示文件扩展名") == labels.index("显示元数据信息") - 1


def test_extensions_are_shown_by_default(window: MainWindow) -> None:
    assert window._show_extensions_action.isChecked() is True


def test_unchecking_it_hides_the_extension_and_saves_the_choice(
    qt_app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    row = window._model.row_of_path(window._library.root / PHOTO)
    assert row >= 0
    assert _display(window, row) == PHOTO

    window._show_extensions_action.setChecked(False)
    qt_app.processEvents()

    assert _display(window, row) == "海边日落"
    assert load_session(base=window._session_base).preferences.show_file_extensions is False


def test_the_detail_title_follows_the_same_switch(qt_app: QApplication, window: MainWindow) -> None:
    if not window._owns_title_bar:
        pytest.skip("标题栏那一行只存在于 macOS（ADR 0025 决策 2）")
    row = window._model.row_of_path(window._library.root / PHOTO)
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image)
    assert window._title_bar.current_text == PHOTO

    window._show_extensions_action.setChecked(False)
    qt_app.processEvents()

    assert window._title_bar.current_text == "海边日落"


def test_renaming_in_the_title_bar_still_starts_from_the_real_name(
    qt_app: QApplication, window: MainWindow
) -> None:
    if not window._owns_title_bar:
        pytest.skip("标题栏那一行只存在于 macOS（ADR 0025 决策 2）")
    row = window._model.row_of_path(window._library.root / PHOTO)
    window._open_detail(window._model.index(row, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image)
    window._show_extensions_action.setChecked(False)

    window.rename_current_entry()

    assert window._title_bar._editor.text() == PHOTO, "直接回车会把扩展名改没"


def test_renaming_in_the_grid_still_starts_from_the_real_name(window: MainWindow) -> None:
    row = window._model.row_of_path(window._library.root / PHOTO)
    window._show_extensions_action.setChecked(False)

    assert window._grid.begin_rename(window._model.index(row, 0)) is True

    assert window._grid._rename_edit.text() == PHOTO, "直接回车会把扩展名改没"
