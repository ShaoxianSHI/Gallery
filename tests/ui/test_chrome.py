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
- **横幅按整个窗口居中**，哪怕元数据面板摆着 —— 它是窗口上的一层，不是照片区的一层。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QStatusBar  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS, wait_until  # noqa: E402

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
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
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)

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


def _viewer_window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> MainWindow:
    """开一个**站在大图页上**的窗口：三张照片，所以「下一张」翻得到。

    等的是条件（照片扫出来、大图真的上屏），不是秒数 —— 画面还空着的时候
    ``step_detail`` 与 ``show_grid`` 那几条走的并不是用户走的那条路。
    """
    root = tmp_path / "库"
    for name in ("一.jpg", "二.jpg", "三.jpg"):
        make_photo(name, directory=root)
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1200, 800)
    window.show()

    assert wait_until(qt_app, lambda: window._model.rowCount() >= 3), "照片没能在时限内扫出来"
    window._open_detail(window._model.index(0, 0))
    assert wait_until(qt_app, lambda: window._detail.has_image), "大图没能在时限内显示出来"
    assert window._stack.currentWidget() is window._detail_page
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


def test_the_banner_is_centred_on_the_window_with_the_metadata_panel_open(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**元数据面板摆着时，居中的是整个窗口**（用户反馈）。

    原先 ``_banner_area`` 把面板减掉，于是大图页上横幅落在照片那一半的正中间 ——
    看着像只对照片说话，而它这一句说的是整个窗口里刚发生的事。**位置照旧，宽度让开**：
    面板正显示当前这张的 EXIF，不该被盖住。
    """
    window = _albums_window(qt_app, tmp_path, make_photo)
    try:
        window._stack.setCurrentWidget(window._detail_page)
        window.toggle_metadata(True)
        qt_app.processEvents()
        assert window._panel.isVisible(), "这一条测的就是面板摆着的时候"

        window._show_banner("已撤销，恢复为「海边.jpg」。")
        qt_app.processEvents()

        rect = window._banner.geometry()
        area = window.centralWidget().geometry()
        assert abs(rect.center().x() - area.center().x()) <= 1, (
            f"横幅 {rect} 没有按整个窗口居中（窗口内容区 {area}）"
        )
        photo_area = QRect(area)
        photo_area.setRight(window._panel.geometry().left() - 1)
        assert rect.center().x() - photo_area.center().x() >= window._panel.width() // 2, (
            "这还是落在照片区正中，不是窗口正中"
        )
        assert not rect.intersects(window._panel.geometry()), (
            f"横幅 {rect} 压到了元数据面板 {window._panel.geometry()} 上"
        )
    finally:
        window.close()


# ---- 大图页上「走到别处去了」，横幅跟着走 -------------------------------------


def test_the_banner_leaves_when_the_viewer_moves_on_to_another_photo(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**大图页上翻一张照片，横幅就走**（用户反馈）。

    那句话说的是**刚才那一张**（「已复制它的路径」「已把它移到废纸篓」）。人已经在看
    下一张了，它还压在顶边就成了挡路的 —— 而它多半又是那句「一直留着」的不自动消失的
    出错提示，于是它能一直压到用户退回去为止。
    """
    window = _viewer_window(qt_app, tmp_path, make_photo)
    try:
        window._show_banner("已把「一.jpg」移到废纸篓。⌘Z 可以撤销。")
        qt_app.processEvents()
        assert window._banner.isVisible(), "这一条要有一句正在显示的话"

        window.step_detail(1)
        qt_app.processEvents()

        assert window._current_row == 1, "照片没换成功，那这条测的不是翻图这条路"
        assert not window._banner.isVisible(), "换了一张照片之后不该还留着刚才那一句"
    finally:
        window.close()


def test_the_banner_leaves_when_you_go_back_to_the_grid(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**退回网格也走** —— 连出错与后台进度那一类（默认留着的那种）一起走。

    留着的那几句本来是为了「用户还得动手处理」，可他已经不在这一页上了：那句话指的
    是大图页上的那一张，回到网格里它既挡着面包屑又对不上眼前的东西。
    """
    window = _viewer_window(qt_app, tmp_path, make_photo)
    try:
        window._show_banner("读取元数据失败，按默认顺序显示：磁盘读不出来。")
        qt_app.processEvents()
        assert window._banner.isVisible() and not window._banner.auto_hide_pending

        window.show_grid()
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._grid_page, "确实退回网格了"
        assert not window._banner.isVisible(), "不在这一页上了，那句话就该收掉"
    finally:
        window.close()


def test_a_banner_that_belongs_to_the_grid_is_not_taken_away_with_the_viewer(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """**只有大图页上那句会被顺带收掉**：网格页上自己出的提示原样留着。

    「离开大图页就清」写成「``show_grid`` 一律清」的话，网格上刚出的那一句会被下一次
    ``show_grid``（Esc 连按、删掉一张之后）无声无息地抹掉 —— 用户压根没请求过。
    """
    window = _viewer_window(qt_app, tmp_path, make_photo)
    try:
        window.show_grid()
        window.show_grid()  # 本来就在网格页上：这是一次空操作
        window._show_banner("这个分组里没有照片了。")
        qt_app.processEvents()

        window.show_grid()
        qt_app.processEvents()

        assert window._banner.isVisible(), "网格页上自己的那句话不该被顺带收走"
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
