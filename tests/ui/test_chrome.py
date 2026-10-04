"""窗口外观的回归测试：底部不建状态条，面包屑独占一行、不压住缩略图，
横幅覆盖在内容上而不是把内容往下挤（ADR 0029）。

这几条都是**看得见、但很容易被改回去**的约定：

- 主窗口不建状态条。底部那一行只放扫描进度、照片尺寸、缩放百分比，换来的是照片区
  少二十来像素。``QMainWindow.statusBar()`` 是懒建的，所以断言「找不到任何
  ``QStatusBar`` 子控件」，而不是「它不可见」。
- 面包屑在垂直布局里独占一行。曾经试过让它浮在网格之上，结果缩略图铺到窗口最上沿、
  第一张照片正好被压在下面 —— 与其那样让人找路，不如老老实实给它留一行。
- **横幅同样不进布局**：它在竖排里占一行，于是每一条消息都把网格往下挤一段，照片区
  矮一截、滚动位置跟着动。它是挂在窗口上的一层，因此网格页与大图页都看得见。
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
from waiting import DEFAULT_TIMEOUT_SECONDS

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402


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

    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
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


def test_the_banner_covers_the_grid_instead_of_pushing_it_down(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**出一条消息不该动照片区一像素**（ADR 0029 决策 1）。

    判据是网格那块的几何：横幅曾经是竖排布局里的一个 ``addWidget``，于是每一条消息
    都让网格矮一截、跟着重排 —— 用户正要看的正是刚被挤走的那一屏。
    """
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        grid_before = window._grid.geometry()
        window._show_banner("已把 3 项移到废纸篓。⌘Z 可以撤销。")
        qt_app.processEvents()

        assert window._banner.isVisible(), "该看到这句话"
        assert window._grid.geometry() == grid_before, "网格被横幅挤动了"
        assert window._banner.geometry().intersects(window._grid.geometry()), (
            "横幅该盖在网格上，而不是待在网格外面"
        )
        assert window._banner.parent() is window, "横幅必须挂在窗口上（两页都看得见）"
    finally:
        window.close()


def test_the_banner_is_visible_over_the_photo_on_the_detail_page(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**大图页上出的事，说的话要看得见**（ADR 0029 决策 2）。

    以前横幅在网格页里，于是大图页上 ⌘Z、⌘C、⌘⌫ 说的那句话全落在背后 —— 用户得
    退回网格才知道刚才那一下到底做了什么。
    """
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        window._stack.setCurrentWidget(window._detail_page)
        qt_app.processEvents()
        window._show_banner("已撤销，恢复为「海边.jpg」。")
        qt_app.processEvents()

        assert window._banner.isVisible(), "大图页上不该看不见刚做的事"
        assert window._banner.geometry().intersects(window._detail.geometry()), "横幅该压在照片上"
    finally:
        window.close()


def test_only_a_one_off_confirmation_leaves_by_itself(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**做完一件事的确认说过就走，要动手处理的那几句留着**（ADR 0029 决策 3）。

    这里问的是「会不会自己收起来」，不真的等 :data:`AUTO_HIDE_SECONDS` —— 那一段的
    行为由 ``tests/ui/test_banner_overlay.py`` 拨着短定时器验。
    """
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        window._show_banner("已把 3 项移到废纸篓。⌘Z 可以撤销。", auto_hide=True)
        qt_app.processEvents()
        assert window._banner.auto_hide_pending, "确认类的那几句该自己收起来"

        window._show_banner("读取元数据失败，按默认顺序显示：磁盘读不出来。")
        qt_app.processEvents()
        assert not window._banner.auto_hide_pending, "用户还得处理的那几句不该自己消失"
    finally:
        window.close()
