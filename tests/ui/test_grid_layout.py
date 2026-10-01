"""网格列数自适应的回归测试。

补的是一个真实缺陷：**条目数不够一行时，网格会把「条目总数」误当成「实际列数」**。
相册模式下根目录常常只有两三个相册，于是两个相册被撑成整屏，必须手动切一次
缩略图尺寸才恢复正常。

这类问题只有把视图真正排出来才看得见，所以测试用 Qt 的离屏平台。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.config import prepare_library  # noqa: E402
from gallery.core.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.grid_view import PhotoGrid  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.models import PhotoListModel  # noqa: E402

#: 等待异步扫描完成的时限。
SCAN_TIMEOUT_SECONDS = 8.0


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def fake_photos(count: int, tmp_path: Path) -> list[PhotoRef]:
    return [
        PhotoRef(path=tmp_path / f"IMG_{index:04d}.jpg", size=1024, mtime_ns=index)
        for index in range(count)
    ]


def make_grid(qt_app: QApplication, tmp_path: Path, count: int, *, width: int = 1200) -> PhotoGrid:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append(fake_photos(count, tmp_path))
    grid.setModel(model)
    grid.resize(width, 800)
    grid.show()
    qt_app.processEvents()
    return grid


def test_examiner_ignores_a_partial_row(qt_app: QApplication, tmp_path: Path) -> None:
    """只有 2 个条目时不能把列数改成 2 —— 那是条目数，不是 Qt 摆不下的列数。"""
    grid = make_grid(qt_app, tmp_path, count=2)
    before = grid.gridSize()

    grid._examine_columns()
    qt_app.processEvents()

    assert grid.gridSize() == before
    assert grid._layout_fix == {}
    assert grid.gridSize().width() < grid.viewport().width() // 3, "两个条目就把窗口铺满了"
    grid.deleteLater()


def test_examiner_corrects_a_genuinely_short_row(
    qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目足够多、而 Qt 真的少摆了一列时，修正逻辑仍然生效。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    intended = grid._laid_out_columns
    assert intended >= 2
    monkeypatch.setattr(grid, "_first_row_columns", lambda: intended - 1)

    grid._examine_columns()
    qt_app.processEvents()

    assert grid._layout_fix, "少摆一列时应当记录修正"
    assert (
        grid.gridSize().width()
        > grid._cell_size_for_width(grid.viewport().width() // intended).width()
    )
    grid.deleteLater()


def test_albums_mode_startup_keeps_normal_tile_size(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册模式重启后，布局应与手动确认过的一样 —— 这正是缺陷的复现路径。"""
    root = tmp_path / "测试相册"
    for album, count in (("2022-复活节波尔多", 2), ("2023-京都", 3)):
        for index in range(count):
            make_photo(f"{index}.jpg", directory=root / album)
    prepare_library(root)
    session_base = tmp_path / "session"
    save_session(
        SessionState(recent_roots=(root,), thumbnail_edge=176, browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )

    window = MainWindow(session_base=session_base)
    window.resize(1200, 800)
    window.show()

    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._model.rowCount() >= 2 and window._grid._laid_out_columns:
            break
        time.sleep(0.01)
    # 让「核对列数」的定时器有机会跑起来（真有缺陷时就是它把格子撑大的）
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)

    grid = window._grid
    assert window._browse_mode == BROWSE_MODE_ALBUMS
    assert window._model.rowCount() == 2
    assert isinstance(window._model.entry_at(0), FolderRef)
    assert grid._layout_fix == {}, "不该因为条目少就改写字数"
    assert grid.gridSize().width() < grid.viewport().width() // 3, (
        f"两个相册占满了窗口：格子 {grid.gridSize().width()} / 视口 {grid.viewport().width()}"
    )
    window.close()
