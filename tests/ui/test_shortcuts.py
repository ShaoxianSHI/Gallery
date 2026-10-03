"""快捷键的分工。

**Esc 一层层往外退。** 「上一级」原来是 ⌘↑，但 macOS 上那个键已经被占着，于是改成 Esc。
Esc 同时还管着「大图页回网格」和「行内重命名取消」，所以这里把三者的边界钉死：

1. Esc 在大图页回网格、在网格页回上一层，两个动作同一时刻只有一个可用；
2. 改到一半按 Esc 只取消改名，不会顺手跳到上一层；
3. 连按 Esc，目录会一层层退到库根，到根上就不再动了。

**缩略图一档一档挪：⇧⌘↑ / ⇧⌘↓。** 捏合与 ⌘+滚轮要手搭在触控板上，这两条给的是键盘那一路。
它们只作用在**网格**上：大图页里 ⌘+ / ⌘- 才是缩放，那边只有倍率、没有「档」。

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
from waiting import wait_for_level, wait_until

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.browser.thumb_sizes import DEFAULT_INDEX, THUMBNAIL_SIZES  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.rename_edit import RenameEdit  # noqa: E402

#: ⇧⌘↑ / ⇧⌘↓。Qt 在 macOS 上把 Command 写作 Ctrl，不要手动换成 Meta。
_ZOOM_IN = Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
_ZOOM_OUT = (
    Qt.Key.Key_Down,
    Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier,
)

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


# ---- 缩略图一档一档挪（⇧⌘↑ / ⇧⌘↓）-------------------------------------------


def _enter_seaside(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> MainWindow:
    """开一个只放了一张照片的窗口，并进到那一层。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    window.enter_folder(library / "海边")
    wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
    return window


def _wait_for_no_transition(qt_app: QApplication, window: MainWindow) -> None:
    """转到网格那段切档过渡跑完。

    连按两下时第二下是**排在**第一段动画后面的（见
    ``main_window.MainWindow.step_thumbnail_size``），所以「一按挪一档」这个用例得自己
    把上一段等掉 —— 一句 ``processEvents()`` 转不满那 160ms。
    """
    wait_until(qt_app, lambda: not window._grid.is_transitioning)


def test_thumbnail_size_moves_one_step_per_keystroke(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """⇧⌘↑ / ⇧⌘↓ 一按挪一档，网格的档位目标与菜单勾选一起跟着变。

    这里发的是**真按键事件**（走 Qt 的快捷键表），而不是直接调槽 —— 只 ``setShortcut``
    而没 ``addAction`` 的那种错，按键发过去什么都不会发生，测试却是绿的。

    两下之间要等上一段过渡跑完：连按会排队而不是丢（见下一条用例），用例自己得等。
    """
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        assert window._size_index == DEFAULT_INDEX
        assert window._thumb_zoom_in_action.shortcut() == QKeySequence("Ctrl+Shift+Up")
        assert window._thumb_zoom_out_action.shortcut() == QKeySequence("Ctrl+Shift+Down")

        QTest.keyClick(window, *_ZOOM_IN)
        qt_app.processEvents()
        assert window._size_index == DEFAULT_INDEX + 1
        assert window._grid.target_edge == THUMBNAIL_SIZES[DEFAULT_INDEX + 1]
        assert window._size_actions[DEFAULT_INDEX + 1].isChecked()

        _wait_for_no_transition(qt_app, window)
        QTest.keyClick(window, *_ZOOM_OUT)
        qt_app.processEvents()
        assert window._size_index == DEFAULT_INDEX
        assert window._grid.target_edge == THUMBNAIL_SIZES[DEFAULT_INDEX]
        assert window._size_actions[DEFAULT_INDEX].isChecked()
    finally:
        window.close()


def test_the_second_keystroke_waits_for_the_first_animation(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """连按两下：第二下排在第一段动画**后面**，而不是把第一段掐掉。

    不排就会变成一步落位（网格那边「上一段没跑完就不接第二段」），画面上是一次跳变
    —— 快按时一帧动画都看不见。排着之后两下都是看得见的动画。
    """
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        window.set_thumbnail_size(len(THUMBNAIL_SIZES) - 1)  # 先到顶那一档
        _wait_for_no_transition(qt_app, window)

        QTest.keyClick(window, *_ZOOM_OUT)  # 大 → 中：过渡跑起来
        qt_app.processEvents()
        assert window._grid.is_transitioning, "前提：第一下应当起一段过渡"
        assert window._size_index == len(THUMBNAIL_SIZES) - 2

        QTest.keyClick(window, *_ZOOM_OUT)  # 排着
        qt_app.processEvents()
        assert window._size_index == len(THUMBNAIL_SIZES) - 2, "第二下不能立刻跳档"
        assert window._pending_size_step == -1

        _wait_for_no_transition(qt_app, window)
        _wait_for_no_transition(qt_app, window)  # 排着的那一下自己又起了一段
        assert window._size_index == len(THUMBNAIL_SIZES) - 3, "排着的那一下最后要兑现"
        assert window._pending_size_step == 0, "兑现过就该把排队清空"
    finally:
        window.close()


def test_keystrokes_in_opposite_directions_during_one_animation_cancel_out(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """动画期间 ⇧⌘↑ 再 ⇧⌘↓ 等于没按 —— 排队的档数是**累加**出来的。

    累加而不是记最后一档：连按三下同方向要能一次跨到头（见下一条），而反方向的连按
    是真的互相抵销。
    """
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        QTest.keyClick(window, *_ZOOM_IN)  # 中 → 大：过渡跑起来
        qt_app.processEvents()

        QTest.keyClick(window, *_ZOOM_OUT)
        QTest.keyClick(window, *_ZOOM_IN)
        qt_app.processEvents()
        assert window._pending_size_step == 0, "一去一回抵销了"

        _wait_for_no_transition(qt_app, window)
        assert window._size_index == DEFAULT_INDEX + 1, "该留在第一下要的那一档"
    finally:
        window.close()


def test_three_keystrokes_in_one_direction_reach_the_end(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """连按三下要能一次跨到头档，不是「一下一下慢慢挪」。

    这是「排队而不是丢」的意义：丢掉的每一下都是一次明确的操作。
    """
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        window.set_thumbnail_size(len(THUMBNAIL_SIZES) - 1)  # 先到顶那一档
        _wait_for_no_transition(qt_app, window)

        for _ in range(3):
            QTest.keyClick(window, *_ZOOM_OUT)
        qt_app.processEvents()
        # 第一下立刻执行（大 → 中）。第二下排着；第三下算上排着的已经没得可缩了，
        # 抵在最小档上 —— 判据是「算上排着的那些之后还会不会换档」，所以只有 -1。
        assert window._pending_size_step == -1, "第二下要排着，不能只当没听见"

        _wait_for_no_transition(qt_app, window)
        _wait_for_no_transition(qt_app, window)
        assert window._size_index == 0, "到头就停住"
    finally:
        window.close()


def test_thumbnail_size_stops_at_both_ends(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """头尾各顶一下：越界就停住，不越界、不回绕（与 ``shifted_index`` 同一个判据）。"""
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        window.set_thumbnail_size(len(THUMBNAIL_SIZES) - 1)
        QTest.keyClick(window, *_ZOOM_IN)
        qt_app.processEvents()

        assert window._size_index == len(THUMBNAIL_SIZES) - 1, "到顶就该停住"
        assert window._grid.target_edge == THUMBNAIL_SIZES[-1]

        window.set_thumbnail_size(0)
        QTest.keyClick(window, *_ZOOM_OUT)
        qt_app.processEvents()

        assert window._size_index == 0, "到底就该停住"
        assert window._grid.target_edge == THUMBNAIL_SIZES[0]
    finally:
        window.close()


def test_thumbnail_size_keys_are_off_in_the_detail_page(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """大图页里这两条是关着的：那边按 ⇧⌘↑ 该归大图的缩放，而不是悄悄换缩略图档位。"""
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        window._grid.select_row(0)
        window._open_detail(window._grid.currentIndex())
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._detail_page
        assert window._thumb_zoom_in_action.isEnabled() is False
        assert window._thumb_zoom_out_action.isEnabled() is False
        assert window._zoom_in_action.isEnabled() is True, "大图页自己的 ⌘+ 才是亮的那个"

        QTest.keyClick(window, *_ZOOM_IN)
        qt_app.processEvents()

        assert window._size_index == DEFAULT_INDEX, "关着的动作按下去什么也不该发生"
    finally:
        window.close()


def test_thumbnail_size_keys_are_on_in_the_grid_page(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """与上一条正好相反：回到网格页这两条就亮，且真的挂在窗口上。"""
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        window._grid.select_row(0)
        window._open_detail(window._grid.currentIndex())
        window.show_grid()
        qt_app.processEvents()

        assert window._thumb_zoom_in_action.isEnabled() is True
        assert window._thumb_zoom_out_action.isEnabled() is True
        # **挂在窗口上**：注册不该只靠「它们碰巧也在菜单里」。抽掉这两行，下面那条
        # 按键测试在菜单还在的情况下仍然会绿 —— 所以这条断言才是真正盯着它的。
        assert window._thumb_zoom_in_action in window.actions()
        assert window._thumb_zoom_out_action in window.actions()
    finally:
        window.close()


def test_thumbnail_size_keys_live_next_to_the_size_submenu(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """菜单里也得看得见：挨着「缩略图大小」摆，用户按 ⇧⌘↑ 时不用先想它在哪一栏。"""
    window = _enter_seaside(qt_app, tmp_path, make_photo)
    try:
        view_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "视图" and action.menu() is not None
        )

        labels = [action.text() for action in view_menu.actions()]
        assert labels.count("放大缩略图") == 1
        assert labels.count("缩小缩略图") == 1
        # 「缩略图大小」子菜单在「视图」里只出现一次（右键菜单那一份是现搭的，不在这里）。
        assert labels.count("缩略图大小") == 1
        assert labels.index("缩小缩略图") < labels.index("缩略图大小")
    finally:
        window.close()
