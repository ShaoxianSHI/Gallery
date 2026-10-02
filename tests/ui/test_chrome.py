"""窗口外观的回归测试：底部不建状态条，面包屑独占一行、不压住缩略图。

这两条都是**看得见、但很容易被改回去**的约定：

- 主窗口不建状态条。底部那一行只放扫描进度、照片尺寸、缩放百分比，换来的是照片区
  少二十来像素。``QMainWindow.statusBar()`` 是懒建的，所以断言「找不到任何
  ``QStatusBar`` 子控件」，而不是「它不可见」。
- 面包屑在垂直布局里独占一行。曾经试过让它浮在网格之上，结果缩略图铺到窗口最上沿、
  第一张照片正好被压在下面 —— 与其那样让人找路，不如老老实实给它留一行。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QStatusBar  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描与首屏布局稳定的时限。
READY_TIMEOUT_SECONDS = 8.0


def _albums_window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> MainWindow:
    """开一个相册模式的窗口：根目录下放两个相册，面包屑才可见。"""
    root = tmp_path / "库"
    for album in ("2022-复活节波尔多", "2023-京都"):
        for index in range(2):
            make_photo(f"{index}.jpg", directory=root / album)
    prepare_library(root)
    session_base = tmp_path / "session"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )

    window = MainWindow(session_base=session_base)
    window.resize(1200, 800)
    window.show()

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._model.rowCount() >= 2 and window._breadcrumb.isVisible():
            break
        time.sleep(0.01)
    return window


def test_window_has_no_status_bar(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        assert window.findChildren(QStatusBar) == [], "底部状态条回来了；照片区会少掉一行的显示高度"
    finally:
        window.close()


def test_breadcrumb_sits_above_the_grid_instead_of_covering_it(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        crumb = window._breadcrumb.geometry()
        grid = window._grid.geometry()
        assert window._breadcrumb.isVisible(), "相册模式下应当显示面包屑"
        assert crumb.bottom() <= grid.top(), (
            f"面包屑 {crumb} 压到了网格 {grid} 上；网格不该铺到窗口最上沿"
        )
        assert crumb.height() < 40, f"面包屑不该高成一块：{crumb}"
    finally:
        window.close()
