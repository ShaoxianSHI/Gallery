"""鼠标侧键：后侧键 = 「上一级」那一条命令（ADR 0043）。

钉的是四件事：

1. **接得住**。这一下必须落在网格的视口上才作数 —— 网格是 ``QListView``，会把侧键的按下收下
   并 accept、压根不让它冒到窗口（这正是它挂在 ``QApplication`` 上的理由，ADR 0043 背景那一节；
   大图页的视口与标题栏那种普通子控件本来就会冒上来，少了那一层它们照样过，网格不会）。
2. **退一层，且只退一层**，到库根就不再动 —— 与 ``Esc`` 同一件事。
3. **收掉改名的输入框的那一下不带着人退层**（那一按的意思已经是「点别处」），而**下一次**后侧键
   照旧退层 —— 记号不许留在那儿过一辈子。
4. **别的顶层不归它管**：编辑元数据那个模态对话框开着时，后侧键不动层级栈。顺带钉住前侧键
   不接（层级栈没有「前进」那一份可去的地方）。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402
from waiting import wait_for_level  # noqa: E402

from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    prepare_library,
)
from gallery.core.library.session import (  # noqa: E402
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 一个相册里放几张照片。**不能是 1**：库根本身只有「海边」那一行，于是「等 1 行」分不清
#: 「还在根上」与「已经进了那一层」（这一格等错的话，后面每一条断言都在根上跑，却照样绿）。
PHOTOS = 3


def open_window(qt_app: QApplication, root: Path) -> MainWindow:
    """开一个「相册」浏览模式的窗口，等第一层扫描完。

    库根放在 ``tmp_path/库``：会话目录和 ``.gallery/`` 都在库外，免得根目录里混进
    「状态」「.gallery」这些不是相册的文件夹，行数对不上。
    """
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
    return window


def make_library(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> tuple[MainWindow, Path, Path]:
    """开一个窗口、进去一层，库里有 :data:`PHOTOS` 张照片。"""
    library = tmp_path / "库"
    folder = library / "海边"
    folder.mkdir(parents=True)
    for number in range(PHOTOS):
        make_photo(f"IMG_{number:04d}.jpg", directory=folder)
    window = open_window(qt_app, library)
    window.enter_folder(folder)
    wait_for_level(window, qt_app, lambda: window._model.rowCount() == PHOTOS, what="扫描")
    assert window._current_folder() == folder
    return window, library, folder


def press_back_button(target: object) -> None:
    """在 ``target`` 上按一次后侧键。

    **发给视口而不是控件本身**：真实情况下光标底下是视口（``QListView`` / ``QGraphicsView``
    那一族），也正是这一族会把事件吃掉 —— 守卫的正是「那一族吃掉之后仍然接得住」。
    """
    viewport = getattr(target, "viewport", None)
    widget = viewport() if callable(viewport) else target
    QTest.mouseClick(widget, Qt.MouseButton.BackButton)


def test_the_back_button_walks_up_one_level_at_a_time(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """网格上按后侧键退一层；到库根就不再动 —— 与 ``Esc`` 同一件事。"""
    window, library, _folder = make_library(qt_app, tmp_path, make_photo)
    try:
        press_back_button(window._grid)
        qt_app.processEvents()
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")

        assert window._current_folder() == library
        assert window._up_action.isEnabled() is False

        press_back_button(window._grid)
        qt_app.processEvents()

        assert window._current_folder() == library, "已经在库根，不能再往上"
    finally:
        window.close()


def test_the_back_button_leaves_the_detail_view(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """大图页上按后侧键退回网格，**还停在原来那一层**（一次只弹栈顶一帧）。

    大图页是 ``QGraphicsView``：它的视口同样会把这一次按下收下，所以这一格与网格那一格
    守的是同一件事的两半 —— 少任何一半都会出现「在照片上按侧键没反应，在网格上按才有用」。
    """
    window, _library, folder = make_library(qt_app, tmp_path, make_photo)
    try:
        window._grid.select_row(0)
        window._open_detail(window._grid.currentIndex())
        assert window._stack.currentWidget() is window._detail_page

        press_back_button(window._detail)
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._grid_page
        assert window._current_folder() == folder
    finally:
        window.close()


def test_the_back_button_that_settles_a_rename_does_not_go_up(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改到一半按后侧键：**名字照提交（与点别处一样），但层不动**。

    Qt 是先把焦点给鼠标底下的控件、再把事件交给事件过滤器的（实测顺序：``focusOut`` → 改名提交
    → 过滤器），于是等侧键看见这一下时输入框已经收起、「上一级」也已经重新亮起来 —— 直接触发
    就是「既把名字提交了又退一层」，而这一按的意思明明只是「点别处」。
    """
    window, _library, folder = make_library(qt_app, tmp_path, make_photo)
    try:
        window._grid.select_row(0)
        window.rename_current_entry()
        editor = window._grid._rename_edit
        assert editor.is_editing()
        editor.setText("甲")

        press_back_button(window._grid)
        qt_app.processEvents()

        assert editor.is_editing() is False
        assert window._current_folder() == folder, "这一下只是把输入框收了，不该顺手退一层"
        assert (folder / "甲.jpg").exists(), "焦点一走就提交，与点别处一样（改名有 journal，可 ⌘Z）"
    finally:
        window.close()


def test_the_next_back_button_after_a_rename_goes_up_as_usual(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """上一条那一格不许「记住」：收掉输入框之后，**下一次**后侧键照旧退层。

    记号如果留在那儿过一辈子，症状是「改完名字之后按返回没反应，要按两次」—— 而那条命令此刻
    是亮着的，按下去没反应只会被当成界面卡了。
    """
    window, library, folder = make_library(qt_app, tmp_path, make_photo)
    try:
        window._grid.select_row(0)
        window.rename_current_entry()
        editor = window._grid._rename_edit
        editor.setText("甲")
        # **回车提交**（不是鼠标）：这一格量的是「记号只对收掉输入框的那一下有效」。
        QTest.keyClick(editor, Qt.Key.Key_Return)
        qt_app.processEvents()
        assert (folder / "甲.jpg").exists()
        assert window._up_action.isEnabled() is True

        press_back_button(window._grid)
        qt_app.processEvents()
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")

        assert window._current_folder() == library
    finally:
        window.close()


def test_the_back_button_stays_out_of_another_top_level(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """别的顶层（编辑元数据那个模态对话框）开着时，后侧键不动层级栈。

    **判据是「这一下落在哪个顶层窗口里」**：那扇对话框有自己的 ``Esc``，后侧键不该越俎代庖
    去弹栈顶 —— 那会当着模态对话框的面把用户身后的那一层换掉。
    """
    window, _library, folder = make_library(qt_app, tmp_path, make_photo)
    try:
        dialog = QDialog(window)
        inside = QLabel("改元数据", dialog)
        dialog.setModal(True)
        dialog.resize(320, 200)
        dialog.show()
        qt_app.processEvents()

        press_back_button(inside)
        qt_app.processEvents()

        assert window._current_folder() == folder
        dialog.close()
    finally:
        window.close()


def test_the_forward_button_is_not_bound_to_anything(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """前侧键不接：层级栈只有 push / pop，没有「前进」那一份可去的地方。

    钉住它是为了免得日后有人顺手把它绑到什么命令上 —— 而按下去什么也不发生之外，
    用户还得另外去弄明白为什么这一枚键是哑的。
    """
    window, _library, folder = make_library(qt_app, tmp_path, make_photo)
    try:
        QTest.mouseClick(window._grid.viewport(), Qt.MouseButton.ForwardButton)
        qt_app.processEvents()

        assert window._current_folder() == folder
    finally:
        window.close()
