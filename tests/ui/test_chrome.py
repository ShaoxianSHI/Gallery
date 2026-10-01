"""窗口外观的回归测试：面包屑浮在照片之上、底部没有状态条。

补的是两条**看得见、但很容易被改回去**的约定：

- 面包屑必须和网格**重叠**在同一个格子里。曾经它是垂直布局里的独立一行，网格被
  顶下去一截 —— 看着像「压在照片上面的标题」，而不是浮在照片之上。
- 主窗口不建状态条。``QMainWindow.statusBar()`` 是懒建的，所以断言「找不到任何
  ``QStatusBar`` 子控件」，而不是「它不可见」。

这层测试用的是 Qt 离屏平台：毛玻璃在离屏下拿不到，``Vibrancy`` 会走降级路径，
所以顺带也守住了「拿不到毛玻璃也不能变成一块看不见的东西」。
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

from gallery.core.config import prepare_library  # noqa: E402
from gallery.core.session import BROWSE_MODE_ALBUMS, SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描与首屏布局稳定的时限。
READY_TIMEOUT_SECONDS = 8.0


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


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


def test_breadcrumb_overlaps_the_grid_instead_of_being_pushed_above_it(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        grid = window._grid.geometry()
        crumb = window._breadcrumb.geometry()

        assert window._breadcrumb.isVisible(), "相册模式下应当显示面包屑"
        # 同一个父控件（网格页）下的坐标系，可以直接比。
        assert crumb.intersects(grid), (
            f"面包屑 {crumb} 没有压在网格 {grid} 上 —— 它多半又被放回独立一行了"
        )
        # 同一行的顶边：证明没有为面包屑预留行高，照片没有被顶下去。
        assert crumb.top() == grid.top(), f"面包屑顶边 {crumb.top()} 与网格顶边 {grid.top()} 不一致"
        # 只占左上角一小块：宽度贴合内容，不是横贯整个网格的标题条。
        assert crumb.left() == grid.left()
        assert crumb.width() < grid.width() // 2, (
            f"面包屑宽 {crumb.width()}，快顶满网格了 {grid.width()}"
        )
    finally:
        window.close()


def test_breadcrumb_hugs_its_content(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """路径只有一段时，面包屑应当就那么窄 —— 末尾的 stretch 不该把它撑满。"""
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        window.enter_folder(window._library.root)  # type: ignore[union-attr]
        qt_app.processEvents()
        crumb = window._breadcrumb.geometry()
        assert crumb.width() < window._grid.geometry().width() // 2
    finally:
        window.close()


def test_window_has_no_status_bar(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        assert window.findChildren(QStatusBar) == [], "底部状态条回来了；照片区会少掉一行的显示高度"
    finally:
        window.close()


def test_breadcrumb_stays_readable_without_vibrancy(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """离屏（也就是没有系统毛玻璃）时，必须退回半透明底色，而不是全透明。"""
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        crumb = window._breadcrumb
        assert not crumb._vibrancy.attached, "离屏平台下不该挂上系统毛玻璃"
        style = crumb.styleSheet()
        assert "background: transparent" not in style, (
            f"没有毛玻璃却把底色设成透明，照片会直接糊掉字：{style}"
        )
        assert "rgba(" in style, f"降级时应当铺一层半透明底色：{style}"
    finally:
        window.close()
