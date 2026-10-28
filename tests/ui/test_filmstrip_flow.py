"""大图页底部的画廊条：跟着翻图走、点一下换过去。

图片顺序与当前行都归窗口；这里只看窗口把什么交给了那条面板，以及点下去之后到了哪一张。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import wait_for_idle, wait_until  # noqa: E402

from gallery.core.library.config import BROWSE_MODE_FLAT  # noqa: E402
from gallery.core.library.session import load_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.viewer.filmstrip import BOTTOM_MARGIN, cell_rect, centered_offset  # noqa: E402
from gallery.ui.window.viewer_flow import FILMSTRIP_RADIUS  # noqa: E402

#: 这一层的照片（名字决定网格里的先后）。**要多到一屏装不下**：有一张专门验
#: 「网格没显示过的那些，画廊条也会要」。
PHOTO_NAMES = tuple(f"p{index:02d}.jpg" for index in range(40))


@pytest.fixture
def window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    for name in PHOTO_NAMES:
        make_photo(name, directory=root)
    set_library_ui(root, browse_mode=BROWSE_MODE_FLAT, metadata_visible=False)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    instance.resize(1000, 700)
    instance.show()
    wait_for_idle(qt_app, instance, what="扫描")
    assert instance._model.rowCount() == len(PHOTO_NAMES)
    yield instance
    instance.close()


def _open(window: MainWindow, row: int) -> None:
    window._open_detail(window._model.index(row, 0))


def _view_menu(window: MainWindow) -> QMenu:
    for action in window.menuBar().actions():
        if action.text() == "显示":
            assert action.menu() is not None
            return action.menu()
    raise AssertionError("菜单栏里没有「显示」")


def test_the_switch_sits_just_above_show_extensions(window: MainWindow) -> None:
    labels = [action.text() for action in _view_menu(window).actions()]

    assert labels.index("显示画廊") == labels.index("显示文件扩展名") - 1


def test_the_strip_lists_the_photos_around_the_current_one(window: MainWindow) -> None:
    _open(window, 20)

    rows = window._detail.filmstrip_rows()

    assert rows == tuple(range(20 - FILMSTRIP_RADIUS, 20 + FILMSTRIP_RADIUS + 1))
    assert window._detail.filmstrip_current_row == 20
    assert window._detail.isVisible() is True

    area = window._detail.viewport().geometry()
    geometry = window._detail._filmstrip.geometry()
    assert area.contains(geometry)
    assert geometry.bottom() == area.bottom() - BOTTOM_MARGIN


def test_near_the_start_the_strip_begins_with_the_first_photo(window: MainWindow) -> None:
    _open(window, 2)

    rows = window._detail.filmstrip_rows()

    assert rows[0] == 0
    assert rows[-1] == 2 + FILMSTRIP_RADIUS
    assert window._detail.filmstrip_current_row == 2


def test_the_strip_is_hidden_on_the_grid_page(window: MainWindow, qt_app: QApplication) -> None:
    _open(window, 1)
    strip = window._detail._filmstrip

    window.show_grid()
    qt_app.processEvents()

    assert strip.isVisible() is False


def test_stepping_a_photo_moves_the_selection(window: MainWindow) -> None:
    _open(window, 1)

    window.step_detail(1)

    assert window._current_row == 2
    assert window._detail.filmstrip_current_row == 2


def test_the_switch_hides_the_strip_and_remembers_the_choice(
    window: MainWindow, qt_app: QApplication
) -> None:
    _open(window, 20)
    strip = window._detail._filmstrip
    assert strip.isVisible() is True

    window._filmstrip_action.setChecked(False)
    qt_app.processEvents()

    assert strip.isVisible() is False
    assert window._session.preferences.show_filmstrip is False
    assert load_session(base=window._session_base).preferences.show_filmstrip is False

    window._filmstrip_action.setChecked(True)
    qt_app.processEvents()

    assert strip.isVisible() is True
    assert window._session.preferences.show_filmstrip is True


def test_clicking_a_thumbnail_opens_that_photo(window: MainWindow, qt_app: QApplication) -> None:
    _open(window, 20)
    strip = window._detail._filmstrip
    offset = centered_offset(strip.width(), len(strip.rows()), FILMSTRIP_RADIUS)

    QTest.mouseClick(
        strip, Qt.MouseButton.LeftButton, pos=cell_rect(FILMSTRIP_RADIUS + 3, offset).center()
    )
    assert wait_until(qt_app, lambda: window._current_row == 23)

    ref = window._model.photo_at(window._current_row)
    assert ref is not None and ref.name == PHOTO_NAMES[23]
    assert window._detail.filmstrip_current_row == 23


def test_the_strip_asks_for_photos_the_grid_never_showed(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画廊条把当前那张两边都交给流水线，于是翻到旁边那张时不必等图。"""
    requested: list[Path] = []
    original = window._pipeline.set_window

    def spy(visible: object, prefetch: object = (), **kwargs: object) -> None:
        for entry in (*visible, *prefetch):  # type: ignore[arg-type]
            requested.append(entry.path)
        original(visible, prefetch, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(window._pipeline, "set_window", spy)
    _open(window, 20)
    far = window._model.photo_at(20 + FILMSTRIP_RADIUS)
    assert far is not None

    assert far.path in requested
