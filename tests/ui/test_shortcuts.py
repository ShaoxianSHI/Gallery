"""快捷键的分工。

**Esc 一层层往外退。** 「上一级」不用 ⌘↑（macOS 上那个键已经被占着），用 Esc。
**大图页也是栈上的一帧**，所以那一页的 Esc 与网格页的是同一条命令（不再有第二个「返回网格」）。
Esc 同时还管着「行内重命名取消」，所以这里把两者的边界固定：

1. Esc 在大图页退回网格、在网格页退上一层，全程只有「上一级」这一条命令；
2. 改到一半按 Esc 只取消改名，不会顺手跳到上一层；
3. 连按 Esc，目录会一层层退到库根，到根上就不再动了。

**缩略图一档一档挪：⇧⌘↑ / ⇧⌘↓。** 捏合与 ⌘+滚轮要手搭在触控板上，这两条给的是键盘那一路。
它们只作用在**网格**上：大图页里 ⌘+ / ⌘- 才是缩放，那边只有倍率、没有「档」。

**⌘W 关窗、⌘Q 退出。** 这一对是 macOS 的标准分工（ADR 0037）：⌘W 关掉这一个窗口而应用
还活着，⌘Q 才是退出。这里钉的是**按键那一侧**；「关掉最后一个窗口为什么不退出应用」
是应用层的事，在 ``tests/ui/test_app.py``。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcutEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_level, wait_until

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    prepare_library,
)  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.paths import app_state_file  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.rename_edit import RenameEdit  # noqa: E402
from gallery.ui.thumb_sizes import DEFAULT_INDEX, THUMBNAIL_SIZES  # noqa: E402

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
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
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
    """大图页的 Esc 是「退一层」——**同一条命令**，不是另立一个「返回网格」。

    大图页在栈上就是压着一帧（ADR 0041），所以这一格判据是「菜单里只有一条 `Esc` 命令」：
    两条同键的动作意味着同一个键有两个主人，而它们要做的其实是同一件事。
    """
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="扫描")
        window._grid.select_row(0)
        window._open_detail(window._grid.currentIndex())

        assert window._stack.currentWidget() is window._detail_page
        assert window._up_action.isEnabled() is True

        QTest.keyClick(window, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert window._stack.currentWidget() is window._grid_page
        assert window._current_folder() == library / "海边"
        # 退回网格之后**还在原来那一层**（不是顺带退到库根）：Esc 一次只弹一帧。
        assert window._up_action.isEnabled() is True
        assert window._current_folder() == library / "海边"
    finally:
        window.close()


def test_the_view_menu_has_exactly_one_escape_command(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """「视图」菜单里**只有一条 `Esc` 命令**，文案是「上一级」。

    **守卫的是「同一个键不能有两个主人」**：合并不是省了一行菜单，而是因为大图页退一层与
    网格页退一层本来就是同一件事（栈上都是弹顶帧，ADR 0041 决策 1）。哪天又添一条 `Esc`
    命令，症状是「有时按 Esc 没反应」—— 加载顺序决定谁先触发，界面看不出错。
    """
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        view_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "视图" and action.menu() is not None
        )

        escape_rows = [
            action.text() for action in view_menu.actions() if not action.shortcut().isEmpty()
        ]
        assert escape_rows.count("上一级") == 1
        assert window._up_action.shortcut() == QKeySequence("Esc")
        # 「返回网格」这条已经不存在了：大图页退一层走的就是「上一级」。
        assert "返回网格" not in [action.text() for action in view_menu.actions()]
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

        assert window._current_folder() == library
        assert window._up_action.isEnabled() is False

        QTest.keyClick(window, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert window._current_folder() == library, "已经在库根，不能再往上"
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
        assert window._current_folder() == library / "海边", "不该顺手跳上一层"
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


# ---- 关窗与退出（⌘W / ⌘Q，ADR 0037）------------------------------------------
# 两者的键都刻意硬编码：Qt 在 macOS 上把 ``StandardKey.Close`` 解析成 ⌘F4、把
# ``StandardKey.Quit`` 解析成**空序列**，照抄等于装两个按不到的键。


def test_cmd_w_closes_the_window(qt_app: QApplication, tmp_path: Path) -> None:
    """⌘W 关掉窗口。**「不退出应用」的那一半在应用层**（见 ``tests/ui/test_app.py``）。

    这里发的是真按键事件（走 Qt 的快捷键表）而不是直接调槽 —— 只 ``setShortcut``
    而没挂进任何菜单的那种错，按键发过去什么都不会发生，测试却照样绿。
    """
    library = tmp_path / "库"
    (library / "海边").mkdir(parents=True)
    window = open_window(qt_app, library)
    try:
        assert window._close_action.shortcut() == QKeySequence("Ctrl+W")

        QTest.keyClick(window, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
        qt_app.processEvents()

        assert window.isVisible() is False
    finally:
        window.close()


def test_cmd_q_closes_the_window_and_saves_the_session(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """⌘Q 关窗之后还要说一声退出（:meth:`MainWindow.quit_app`）。

    **判据是「窗口关掉 + 会话落盘」**：那两步是 ``closeEvent`` 做的，而
    ``app.quit()`` 压根不发 ``closeEvent`` —— 只看窗口有没有关掉的话，
    把整个 ``quit_app`` 缩成一行 ``app.quit()`` 这条用例照样绿。

    **先把那份文件删掉再断言**：``open_window`` 摆窗口之前就写过一份，
    于是「文件在不在」证明不了什么 —— 看着绿的那一次其实什么都没验。
    """
    library = tmp_path / "库"
    (library / "海边").mkdir(parents=True)
    window = open_window(qt_app, library)
    try:
        assert window._quit_action.shortcut() == QKeySequence("Ctrl+Q")
        state_file = app_state_file(base=tmp_path / "状态")
        state_file.unlink()
        assert not state_file.exists(), "前提不成立：那份文件还在，验不到有没有重新写"

        window._quit_action.trigger()
        qt_app.processEvents()

        assert window.isVisible() is False
        assert state_file.is_file(), "会话没有落盘"
        assert str(library) in state_file.read_text(encoding="utf-8")
    finally:
        window.close()


def test_close_and_quit_sit_next_to_each_other_in_the_file_menu(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """菜单里挨着摆：这两条是同一族的两个动作，中间隔着的正是不该被顺手点掉的那一步。"""
    library = tmp_path / "库"
    (library / "海边").mkdir(parents=True)
    window = open_window(qt_app, library)
    try:
        file_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "文件" and action.menu() is not None
        )

        labels = [action.text() for action in file_menu.actions()]
        assert labels.count("关闭窗口") == 1
        assert labels.count("退出 Gallery") == 1
        assert labels.index("关闭窗口") == labels.index("退出 Gallery") - 1
    finally:
        window.close()


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
