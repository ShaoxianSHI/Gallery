"""快捷键的分工：Esc 一层层往外退。

「上一级」原来是 ⌘↑，但 macOS 上那个键已经被占着，于是改成 Esc。Esc 同时还管着
「大图页回网格」和「行内重命名取消」，所以这里把三者的边界钉死：

1. Esc 在大图页回网格、在网格页回上一层，两个动作同一时刻只有一个可用；
2. 改到一半按 Esc 只取消改名，不会顺手跳到上一层；
3. 连按 Esc，目录会一层层退到库根，到根上就不再动了。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcutEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_level

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.browser.rename_edit import RenameEdit  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描完成的时限。


def open_window(qt_app: QApplication, root: Path) -> MainWindow:
    """开一个「相册」浏览模式的窗口，等第一层扫描完。

    库根放在 ``tmp_path/库``：会话目录和 ``.gallery/`` 都在库外，免得根目录里混进
    「状态」「.gallery」这些不是相册的文件夹，行数对不上。
    """
    session_base = root.parent / "状态"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )
    # 先把配置目录准备好：没有它时 MainWindow 会问「缓存放哪」，
    # 那个模态框在测试里没人点，窗口就永远卡在构造中。
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
    return window


def test_go_up_is_bound_to_escape(qt_app: QApplication, tmp_path: Path) -> None:
    library = tmp_path / "库"
    (library / "海边").mkdir(parents=True)
    window = open_window(qt_app, library)
    try:
        assert window._up_action.shortcut() == QKeySequence("Esc")
    finally:
        window.close()


def test_escape_leaves_the_detail_view_first(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """大图页的 Esc 是「返回网格」，那一刻「上一级」必须是关着的 —— 同一个键不能有两个主人。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
        window._grid.select_row(0)
        window._open_detail(window._grid.currentIndex())

        assert window._stack.currentWidget() is window._detail_page
        assert window._up_action.isEnabled() is False

        QTest.keyClick(window, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._grid_page
        assert window._current_dir == library / "海边"
        assert window._up_action.isEnabled() is True
    finally:
        window.close()


def test_escape_walks_up_one_level_at_a_time(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """网格下按 Esc 上一层；到了库根就没有上一层了，Esc 也不再有动作。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")

        QTest.keyClick(window, Qt.Key.Key_Escape)
        qt_app.processEvents()
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")

        assert window._current_dir == library
        assert window._up_action.isEnabled() is False

        QTest.keyClick(window, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert window._current_dir == library, "已经在库根，不能再往上"
    finally:
        window.close()


def test_escape_cancels_rename_instead_of_going_up(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改到一半按 Esc：取消改名、留在原地，文件一个字都不能动。"""
    library = tmp_path / "库"
    picture = make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
        window._grid.select_row(0)
        window.rename_current_entry()
        editor = window._grid._rename_edit
        assert editor.is_editing()
        editor.setText("别的名字")
        assert window._up_action.isEnabled() is False, "改名的 Esc 归输入框，「上一级」要让开"

        QTest.keyClick(editor, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert editor.is_editing() is False
        assert window._current_dir == library / "海边", "不该顺手跳上一层"
        assert picture.exists()
    finally:
        window.close()


def test_rename_editor_claims_escape_from_the_window(qt_app: QApplication) -> None:
    """Qt 会先问焦点控件要不要接管按键：输入框接了 Esc，它就不再落到窗口的「上一级」上。"""
    editor = RenameEdit()
    editor.start(editor.rect(), "海边", keep_suffix=True)

    override = QShortcutEvent(QKeySequence("Esc"), 0, False)
    QCoreApplication.sendEvent(editor, override)

    assert override.isAccepted() is True
