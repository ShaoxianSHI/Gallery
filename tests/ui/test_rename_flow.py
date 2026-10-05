"""行内重命名的界面测试。

覆盖三件事：

1. 输入框本身的行为（回车提交、Esc 取消、点别处也提交、照片只选中主干）。
2. 「再次点击已选中的格子」才进重命名，而**双击仍然是打开** —— 这是最容易做反的一条。
3. 端到端：提交之后磁盘上的文件真的改名了，模型与提示也跟着更新。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS, wait_for_level

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    prepare_library,
)  # noqa: E402
from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.rename_edit import RenameEdit  # noqa: E402

#: 等待异步扫描完成的时限。


def make_grid(qt_app: QApplication, tmp_path: Path, names: list[str]) -> PhotoGrid:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append([PhotoRef(path=tmp_path / name, size=1024, mtime_ns=1) for name in names])
    grid.setModel(model)
    grid.resize(900, 600)
    grid.show()
    qt_app.processEvents()
    return grid


# ---- 输入框 -----------------------------------------------------------------


def test_return_commits(qt_app: QApplication) -> None:
    edit = RenameEdit()
    committed: list[str] = []
    edit.committed.connect(committed.append)
    edit.start(edit.rect(), "海边", keep_suffix=True)
    edit.setText("日落")

    QTest.keyClick(edit, Qt.Key.Key_Return)

    assert committed == ["日落"]
    assert edit.is_editing() is False


def test_escape_cancels(qt_app: QApplication) -> None:
    edit = RenameEdit()
    events: list[str] = []
    edit.committed.connect(lambda text: events.append(f"commit:{text}"))
    edit.cancelled.connect(lambda: events.append("cancel"))
    edit.start(edit.rect(), "海边", keep_suffix=True)
    edit.setText("别的名字")

    QTest.keyClick(edit, Qt.Key.Key_Escape)

    assert events == ["cancel"]


def test_focus_out_commits(qt_app: QApplication) -> None:
    """点到别处等于确认，和访达一样：默默丢掉用户敲的字更烦。"""
    edit = RenameEdit()
    other = RenameEdit()
    committed: list[str] = []
    edit.committed.connect(committed.append)
    edit.show()
    edit.setFocus()
    edit.setText("改好了")
    other.show()
    other.setFocus()
    qt_app.processEvents()

    assert committed == ["改好了"]


def test_signal_is_emitted_only_once(qt_app: QApplication) -> None:
    """回车之后控件自己收起来，收起来又会触发一次失焦 —— 不能提交两遍。"""
    edit = RenameEdit()
    committed: list[str] = []
    edit.committed.connect(committed.append)
    edit.start(edit.rect(), "海边", keep_suffix=True)
    edit.setText("改好了")

    QTest.keyClick(edit, Qt.Key.Key_Return)
    qt_app.processEvents()

    assert committed == ["改好了"]


def test_photo_keeps_the_extension_unselected(qt_app: QApplication) -> None:
    """只选中主干：直接打字是在改名字，扩展名不会被手滑敲掉。"""
    edit = RenameEdit()
    edit.start(edit.rect(), "海边日落.jpg", keep_suffix=True)

    assert edit.selectedText() == "海边日落"


def test_album_selects_the_whole_name(qt_app: QApplication) -> None:
    """相册名里的点不是扩展名，整段都要选中。"""
    edit = RenameEdit()
    edit.start(edit.rect(), "2025.07 苏州", keep_suffix=False)

    assert edit.selectedText() == "2025.07 苏州"


# ---- 再次点击 vs 双击 --------------------------------------------------------


def click_cell(grid: PhotoGrid, row: int) -> None:
    index = grid.model().index(row, 0)
    QTest.mouseClick(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        grid.visualRect(index).center(),
    )


def send_double_click_press(grid: PhotoGrid, row: int) -> None:
    """发一次双击的第二次**按下**：Qt 就是靠它判定双击并去打开照片的。"""
    _send(grid, row, QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton)


def send_release(grid: PhotoGrid, row: int) -> None:
    """发一次抬起。"""
    _send(grid, row, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)


def _send(grid: PhotoGrid, row: int, kind: QEvent.Type, buttons: Qt.MouseButton) -> None:
    viewport = grid.viewport()
    center = grid.visualRect(grid.model().index(row, 0)).center()
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            kind,
            QPointF(center),
            QPointF(viewport.mapToGlobal(center)),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_second_click_on_a_selected_cell_offers_rename(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    click_cell(grid, 0)
    assert grid.currentIndex().row() == 0
    assert requested == [], "第一次点击只是选中"

    settle(interval)
    click_cell(grid, 0)
    settle(interval)

    assert requested == [0], "对已选中的格子再点一次就该能改名"


def test_double_click_wins_over_rename(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """双击打开照片时**绝不能**顺带弹出重命名输入框。

    这是个真缺陷：Qt 按「第二次**按下**」的时机判定双击，而人的手并不稳 ——
    第二下按下去之后多按了一会儿才抬起（两次抬起的间隔就超过了双击阈值）。
    两边各判各的，于是这一次操作变成「照片打开了，同时还弹出一个重命名输入框」。

    修法是让双击先说话：第二次点击先等满一个双击阈值，这段时间里 Qt 认成双击，
    就去打开照片；一直没等到，才当用户确实想改名。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.1

    click_cell(grid, 0)  # 第一下：选中
    send_double_click_press(grid, 0)  # 第二下按下 —— Qt 判定为双击，去打开照片
    settle(interval)  # 手按久了一点才抬起来
    send_release(grid, 0)
    settle(interval)

    assert requested == [], "双击是打开照片，不是重命名"


def test_quick_double_click_also_never_renames(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """干脆利落的双击同样只是打开。"""
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))

    click_cell(grid, 0)
    send_double_click_press(grid, 0)
    send_release(grid, 0)
    settle(QApplication.doubleClickInterval() / 1000 + 0.1)

    assert requested == []


def test_clicking_another_cell_only_selects_it(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """点别的格子只是换选中项 —— 只有「对同一格再点一次」才是重命名。"""
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    click_cell(grid, 0)
    settle(interval)
    click_cell(grid, 1)
    settle(interval)

    assert grid.currentIndex().row() == 1
    assert requested == []


def test_pending_rename_is_dropped_when_the_user_changes_mind(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """等双击阈值的那段时间里改主意点别的格子，不该在那边冒出一个输入框。"""
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    click_cell(grid, 0)
    settle(interval)
    click_cell(grid, 0)  # 已经在等双击阈值了
    click_cell(grid, 1)  # 立刻改主意
    settle(interval)

    assert requested == []


def test_editor_covers_the_file_name(qt_app: QApplication, tmp_path: Path) -> None:
    """输入框必须正好盖住名字那一行，否则用户会看到「框在别处、字在这儿」。"""
    grid = make_grid(qt_app, tmp_path, ["a.jpg"])
    index = grid.model().index(0, 0)

    assert grid.begin_rename(index) is True
    editor = grid._rename_edit
    assert editor.is_editing() is True
    assert editor.geometry().intersects(grid.visualRect(index))
    assert editor.geometry().bottom() >= grid.visualRect(index).bottom() - 30


def test_editor_stays_inside_the_selected_cell(qt_app: QApplication, tmp_path: Path) -> None:
    """输入框不许越出那一格的选中框 —— 下半截挂在框外看着像错位。

    ``adjusted(3, 3, -3, -3)`` 是选中时铺的那层淡色底的边界，``PhotoDelegate.paint`` 与
    ``begin_rename`` 用的是同一个矩形；名字那一行的下沿正好贴着它，所以输入框只能往上长，
    往下长就越界了。输入框有了圆角边框之后这一点尤其显眼（ADR 0025）。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg"])
    index = grid.model().index(0, 0)
    selected = grid.visualRect(index).adjusted(3, 3, -3, -3)

    assert grid.begin_rename(index) is True
    editor = grid._rename_edit

    assert selected.contains(editor.geometry()), (
        f"输入框 {editor.geometry()} 越出了选中框 {selected}"
    )
    assert editor.geometry().intersects(selected.adjusted(0, -6, 0, 0)), (
        "仍然要盖住名字那一行：往上一挪就不能把名字露出去"
    )


# ---- 「再点一次改名」的成立条件 ----------------------------------------------


def test_clicking_again_after_escape_offers_rename(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """⌘R → Esc 之后，再点那一格就该能改名 —— 用户看到的仍然是选中态。

    这是上一条 Esc 修复的**另一半**，症状是：⌘R 能进，Esc 也退得出来，可左键再点
    那一格毫无反应，非要点第二下。

    根因不在 Esc 上，在 :attr:`PhotoGrid._armed_row` 的记法上：它答的是「这一格
    **再点一下**是不是改名」，却只由「上次完成的普通左键点选」维护。于是**凡是
    不是用左键选中那一格的路径**，这一格对它就是没 arm 过的 —— 刚打开时 Qt 顺手设的
    当前项、代码里的 ``select_row``、⌘R 本身，都算。用户看见的是高亮着的选中态，
    代码看见的是「从来没点过这一格」，于是这次点击被当成「第一次：只选中」。

    前置条件摆出来：**这一格从来没有被左键点过**（``armed`` 还是 -1、真选区是空的），
    网格刚打开就是这个样子。于是接下来那次点击之所以能改名，只可能是因为 ⌘R 这一路
    把它 arm 上 —— 换成「点两下」那条老路本来就会过，与本次修复无关。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    assert grid.genuinely_selected_rows() == [], "谁也没选中"
    assert grid._armed_row == -1, "还没点过，这一格没被 arm 过"

    grid.begin_rename(grid.currentIndex())  # ⌘R
    settle(0.05)
    assert grid.is_renaming() is True
    QTest.keyClick(grid._rename_edit, Qt.Key.Key_Escape)  # Esc
    settle(0.05)
    assert grid.is_renaming() is False

    row = grid.current_row()
    assert row == 0, "Esc 不该动当前项"

    click_cell(grid, row)
    settle(interval)

    assert requested == [row], "Esc 之后那一格仍然选着，再点一次就该改名"


def test_the_first_click_after_opening_still_only_selects(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """反过来也得守住：刚打开时第一下点击只能选中，不能一碰就弹出输入框。

    这是上面那条修复的红线。Qt 在行插进来的那一轮事件循环里会把第 0 行设成当前项，
    用户看到的已经是一个高亮的格子；若把判据写成「点当前项就是改名」，那么**打开
    应用后随手点一下照片就直接改名**了 —— 连选中都不选中。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    assert grid.current_row() == 0, "Qt 顺手把第 0 行设成了当前项"
    assert grid.genuinely_selected_rows() == [], "谁也没选中"

    click_cell(grid, 0)
    settle(interval)

    assert requested == [], "第一次点击只是选中"
    assert grid.genuinely_selected_rows() == [0]


# ---- 除了左键，还有哪些路把某一格「选中」了 ----------------------------------
#
# 三条都是同一个症状：界面上那一格是**选中**的（画着选中框，⌘R 也亮着），点它一下
# 却没有反应，非要点第二下。根因是同一个：``_armed_row`` 只认「上次完成的普通左键
# 点选」，于是凡是没走左键的选中路径，那一格对它都是没 arm 过的。


def test_a_programmatically_selected_cell_can_be_renamed_by_one_click(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """``select_row`` 选中的那一格，点一下就该改名 —— 不该要点两下。

    这一条是底下两条（大图返回、返回上级）的共同机制：两条都靠
    ``MainWindow._restore_level_focus`` / ``show_grid`` 调 ``select_row`` 把该选中的
    那一格摆出来。选区是真的（``sel`` 里有它）、当前项也是它，用户看到的就是一个
    选中的格子 —— 于是「再点一下改名」必须认它。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    grid.select_row(1)
    settle(0.05)
    assert grid.genuinely_selected_rows() == [1], "前置条件：它真的是选中的"

    click_cell(grid, 1)
    settle(interval)

    assert requested == [1]


def test_a_cell_reached_with_the_arrow_keys_can_be_renamed_by_one_click(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """方向键挪过去的那一格，点一下就该改名。

    网格要挤到一行放不下才会换行，方向键才有的可挪 —— 所以这里摆 12 张并把视口收窄。
    """
    grid = make_grid(qt_app, tmp_path, [f"p{n:02d}.jpg" for n in range(12)])
    grid.resize(520, 420)
    settle(0.1)
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    QTest.keyClick(grid.viewport(), Qt.Key.Key_Down)
    settle(0.1)
    row = grid.current_row()
    assert row != 0, "前置条件：方向键真的挪动了当前项（视口得窄到能换行）"
    assert grid.genuinely_selected_rows() == [row], "前置条件：方向键也把选区挪过去了"

    click_cell(grid, row)
    settle(interval)

    assert requested == [row]


def test_an_arrow_key_that_does_not_move_the_cursor_arms_nothing(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """方向键没挪动当前项时**不许** arm —— 守住「刚打开时随手点一下只是选中」。

    方向键那一路是「当前项变了就 arm」。可要是无条件 arm，按一个按不动的方向键
    （已经在第 0 行还按 ↑）就会把 Qt 顺手设的那个当前项 arm 上，于是打开应用后随手
    点一下照片直接弹输入框。这条钉的是那个「不许」。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    settle(0.1)
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    assert grid.current_row() == 0 and grid.genuinely_selected_rows() == []
    QTest.keyClick(grid.viewport(), Qt.Key.Key_Up)  # 已经在最上面，挪不动
    settle(0.1)
    assert grid.current_row() == 0

    click_cell(grid, 0)
    settle(interval)

    assert requested == [], "没挪动过的那一格不该被 arm"


def test_changing_the_level_drops_the_armed_cell(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """换一层之后，旧的那一格就不再「再点一下改名」了。

    这是上面几条的反面，也是同一个状态机上的另一个洞：``_armed_row`` 记的是**行号**，
    而换一层（``PhotoListModel.clear``）摆上去的是**另一批条目**。旧行号若还留着，
    用户在新一层里点那一格，第一下就直接改名 —— 而他什么都没指过。
    """
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    settle(0.1)
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    grid.begin_rename(grid.model().index(2, 0))  # arm 第 2 格
    grid.cancel_rename()
    settle(0.05)
    assert grid._armed_row == 2, "前置条件：确实 arm 上了"

    model = grid.model()
    model.clear()  # 换一层：整体重置
    model.append([PhotoRef(path=tmp_path / f"n{n}.jpg", size=1, mtime_ns=1) for n in range(4)])
    settle(0.1)

    click_cell(grid, 2)
    settle(interval)

    assert requested == [], "新一层里的第一下点击只能选中"


def test_clicking_a_different_cell_after_escape_still_only_selects(
    qt_app: QApplication, settle: Callable[[float], None], tmp_path: Path
) -> None:
    """Esc 之后点**别的**格子仍然是「只是换选中」—— arm 只认正在改的那一格。"""
    grid = make_grid(qt_app, tmp_path, ["a.jpg", "b.jpg"])
    requested: list[int] = []
    grid.rename_requested.connect(lambda index: requested.append(index.row()))
    interval = QApplication.doubleClickInterval() / 1000 + 0.05

    grid.begin_rename(grid.currentIndex())  # ⌘R 作用在第 0 行
    settle(0.05)
    QTest.keyClick(grid._rename_edit, Qt.Key.Key_Escape)
    settle(0.05)

    click_cell(grid, 1)  # 改主意了，点另一张
    settle(interval)

    assert requested == [], "点别的格子只是换选中"
    assert grid.current_row() == 1


# ---- 端到端 -----------------------------------------------------------------


def open_window(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_FLAT,
    session_base: Path | None = None,
) -> MainWindow:
    # 会话目录默认**放在库里**：相册浏览模式下它自己就是一个文件夹，会在网格上多出一张
    # 相册卡片，于是行数对不上（与 ``tests/ui/test_level_focus.py`` 同一个理由，那里把它
    # 放在 ``root.parent``）。要按相册数行的用例得自己把它挪到库外。
    session_base = root / "状态" if session_base is None else session_base
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode)
    # 先把配置目录准备好：没有它时 MainWindow 会问「缓存放哪」，
    # 那个模态框在测试里没人点，窗口就永远卡在构造中。
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._model.rowCount() > 0:
            return window
        time.sleep(0.02)
    raise AssertionError("扫描没有在时限内完成")


def test_rename_through_the_grid_actually_renames_the_file(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    picture = make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        assert window._grid.begin_rename(window._grid.currentIndex()) is True

        editor = window._grid._rename_edit
        editor.setText("海边日落")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        qt_app.processEvents()

        assert (tmp_path / "海边日落.jpg").exists()
        assert not picture.exists()
        entry = window._model.entry_at(0)
        assert entry is not None and entry.name == "海边日落.jpg"
        assert window._model.row_of_path(tmp_path / "海边日落.jpg") == 0
        assert "已重命名为" in window._banner.text()
    finally:
        window.close()


def test_illegal_name_leaves_the_file_alone_and_explains_why(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    picture = make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        window._grid.begin_rename(window._grid.currentIndex())
        editor = window._grid._rename_edit
        editor.setText("海边/日落")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        qt_app.processEvents()

        assert picture.exists(), "名字非法时绝不能动文件"
        assert "不能带" in window._banner.text()
        assert window._model.entry_at(0).name == "IMG_0001.jpg"
    finally:
        window.close()


def test_undo_puts_the_name_back(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    picture = make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        window.rename_current_entry()
        editor = window._grid._rename_edit
        editor.setText("海边日落")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        qt_app.processEvents()
        assert (tmp_path / "海边日落.jpg").exists()

        window.undo_last_write()
        qt_app.processEvents()

        assert picture.exists()
        assert window._model.entry_at(0).name == "IMG_0001.jpg"
        assert "已撤销" in window._banner.text()
    finally:
        window.close()


def test_album_rename_in_the_window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("第一张.jpg", directory=tmp_path / "海边")
    window = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        entry = window._model.entry_at(0)
        assert isinstance(entry, FolderRef)
        window._grid.select_row(0)
        window.rename_current_entry()
        editor = window._grid._rename_edit
        assert editor.is_editing()
        editor.setText("2025 苏州")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        qt_app.processEvents()

        assert (tmp_path / "2025 苏州" / "第一张.jpg").exists()
        assert window._model.entry_at(0).name == "2025 苏州"
    finally:
        window.close()


def test_rename_action_is_disabled_without_a_selection(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        # 「刚打开时没有选中」不是一个可以依赖的状态：行插进来的那一轮事件循环里，
        # 网格可能顺手把第 0 行设成当前项（访达也是这么做的）。要测「没选中就不能改名」，
        # 就得**真的**把选中清掉，而不是拿开头的默认状态去赌时序
        # （`tests/ui/test_trash_flow.py` 里的 `clear_selection` 记着同一条）。
        window._grid.select_for_context(-1)

        assert window._rename_action.isEnabled() is False
        window._grid.select_row(0)
        qt_app.processEvents()
        assert window._rename_action.isEnabled() is True
    finally:
        window.close()


def test_escape_cancels_and_leaves_rename_usable_again(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """Esc 取消之后仍然是选中的那一格，于是 ⌘R 必须还能再进一次重命名。

    这是个真缺陷，而且症状很怪：**第一次** ⌘R 好好地能进，Esc 之后第二次却彻底没反应，
    菜单里的「重命名…」也一起变灰 —— 照片明明还选着，别的命令（拷贝、废纸篓）都还亮着。
    灰掉的只有「重命名」这一条，正好是 :meth:`MainWindow._update_action_states` 里那个
    ``idle`` 判据：改名的这段时间它禁用，收起来之后**必须**有人把它点回亮。

    而「Esc 取消」这条路上没人点。``RenameEdit._cancel`` 先 ``hide()`` 再发
    ``cancelled``，网格那边的 ``cancel_rename`` 到场时输入框**早已藏好**，于是它读到的
    ``was_editing`` 是 False，「输入框收起来了」这条信号压根没发出去。
    （回车提交那条路没事：``_on_rename_committed`` 是无条件发那条信号的。）

    钉的是窗口这一侧的接线：输入框自己没问题，坏掉的是「输入框收起来 → 窗口把菜单
    点回亮」。断言也只从窗口看 —— 输入框自己那条 Esc 用例（``test_escape_cancels``）
    一直是对的。
    """
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        qt_app.processEvents()

        window.rename_current_entry()  # 第一次 ⌘R
        editor = window._grid._rename_edit
        assert editor.is_editing() is True
        assert window._rename_action.isEnabled() is False, "改名的这段时间不该抢 ⌘R"

        QTest.keyClick(editor, Qt.Key.Key_Escape)
        qt_app.processEvents()
        assert editor.is_editing() is False

        # 前置条件摆出来：选中的确实还是那一格，且它确实是一张照片（不是分组卡片）。
        assert window._grid.currentIndex().row() == 0
        assert window._grid.selected_rows() == [0]
        assert window._grid.current_row() == 0

        assert window._rename_action.isEnabled() is True, (
            "Esc 之后「重命名…」必须重新可用，否则 ⌘R 没反应"
        )
        window._rename_action.trigger()  # 第二次 ⌘R 走的就是这条 QAction
        assert window._grid._rename_edit.is_editing() is True, "第二次 ⌘R 该能进重命名"
    finally:
        window.close()


def test_escape_leaves_the_other_actions_usable_too(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """与上一条同一根因，但看的是另一侧：Esc 之后 ⌘⌫ / ⇧⌘N / ⌘⏎ 也得一起回来。

    ``idle`` 是这几条动作**共用**的判据，所以 Esc 之后一起失灵，只是「重命名」
    最先被用户撞上而已 —— 把这一条钉住，才能说明修的是那条接线而不是某一条动作。
    """
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        qt_app.processEvents()
        window.rename_current_entry()

        QTest.keyClick(window._grid._rename_edit, Qt.Key.Key_Escape)
        qt_app.processEvents()

        assert window._grid.is_renaming() is False
        for action in (
            window._rename_action,
            window._trash_action,
            window._new_album_action,
            window._reveal_action,
        ):
            assert action.isEnabled() is True, f"Esc 之后 {action.text()} 该重新可用"
    finally:
        window.close()


def test_click_after_escape_renames_again_in_the_window(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """端到端：⌘R → Esc → 左键点那一格 → 真的又进了重命名。

    与同文件里那条 ``test_escape_cancels_and_leaves_rename_usable_again`` 是同一个
    「Esc 之后还能不能改名」的两种入口（键盘与鼠标）。分开钉是因为它们断的地方不同：
    ⌘R 走的是 QAction 的可用性，左键走的是网格里那个「再点一次」的判据 ——
    后者与 Esc 无关，改名一开始就是坏的，只是要走到「⌘R → Esc」这条路上才显形。
    """
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        # 「刚打开时当前项是第 0 行」不是一个可以依赖的状态（行插进来的那一轮事件
        # 循环里，Qt 可能顺手设，也可能没设 —— 串行单跑时它总是设的，并行下就未必）。
        # 所以显式摆出来，跟 `test_rename_action_is_disabled_without_a_selection` 一样。
        window._grid.select_for_context(-1)
        window._grid.select_row(0)
        qt_app.processEvents()
        row = window._grid.current_row()
        assert row == 0

        window.rename_current_entry()  # ⌘R
        editor = window._grid._rename_edit
        assert editor.is_editing() is True
        QTest.keyClick(editor, Qt.Key.Key_Escape)  # Esc
        qt_app.processEvents()
        assert editor.is_editing() is False

        click_cell(window._grid, row)

        assert wait_for_renaming(window, qt_app) is True, "Esc 之后左键点那一格就该再进重命名"
    finally:
        window.close()


def wait_for_renaming(window: MainWindow, qt_app: QApplication) -> bool:
    """转到「已经进了行内重命名」为止（要等双击阈值那个定时器）。"""
    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not window._grid.is_renaming():
        qt_app.processEvents()
        time.sleep(0.005)
    return window._grid.is_renaming()


def test_click_after_going_up_a_level_renames(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """返回上一级之后，点那个被重新选中的相册就该改名。

    「退上去时上一层停在刚离开的那一格」是刻意做的（见 ``test_level_focus.py``）：
    不然每退一级都要重新找一遍刚才在的那个相册。用户看到它是选中的，
    点一下却要等第二下才改名 —— 于是「记住了停在哪」这件事白记了一半。
    """
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(
        qt_app, library, browse_mode=BROWSE_MODE_ALBUMS, session_base=tmp_path / "状态"
    )
    try:
        window.enter_folder(library / "海边")
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 1, what="相册内")
        window.go_up()
        wait_for_level(window, qt_app, lambda: window._model.rowCount() == 2, what="退回后的这一层")
        row = window._grid.current_row()
        assert row >= 0, "退回来时该停在刚离开的那个相册上"
        assert window._grid.genuinely_selected_rows() == [row], "前置条件：它真的是选中的"

        click_cell(window._grid, row)

        assert wait_for_renaming(window, qt_app) is True, "返回上级之后点一下就该改名"
    finally:
        window.close()


def test_click_after_leaving_the_photo_renames(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """从大图退回网格之后，点那张照片就该改名。

    与上一条同一个机制（都走 ``show_grid`` → ``select_row``），但换了个入口：
    大图是看照片时最常待的地方，退回来顺手改个名是很自然的一下。
    """
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_for_context(-1)
        window._grid.select_row(0)
        qt_app.processEvents()
        window.open_current_entry()  # 进大图
        window.show_grid()  # 退回网格（Esc / ⌘↑ 都走这里）
        row = window._grid.current_row()
        assert row == 0
        assert window._grid.genuinely_selected_rows() == [0], "前置条件：照片仍然选着"

        click_cell(window._grid, row)

        assert wait_for_renaming(window, qt_app) is True, "大图退回之后点一下就该改名"
    finally:
        window.close()


def test_switching_away_still_restores_the_actions(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """外部强制收起那条路（切层 / 切页）一直是好的 —— 别把 Esc 也修坏。

    ``MainWindow`` 自己在 :meth:`cancel_rename` 之前就调 :meth:`_update_action_states`，
    所以那条路没经过网格的 ``cancel_rename``。这条守住两侧的行为差异。
    """
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        qt_app.processEvents()
        window.rename_current_entry()
        assert window._rename_action.isEnabled() is False

        window._grid.cancel_rename()
        qt_app.processEvents()

        assert window._grid.is_renaming() is False
        assert window._rename_action.isEnabled() is True
    finally:
        window.close()


# ---- 模型 -------------------------------------------------------------------


def test_replace_entry_keeps_the_thumbnail(qt_app: QApplication, tmp_path: Path) -> None:
    """改名不该让这一格闪回占位框：已经缩放好的图直接搬到新键上。"""
    from PySide6.QtGui import QColor, QImage

    model = PhotoListModel(icon_edge=64)
    old = tmp_path / "a.jpg"
    new = tmp_path / "b.jpg"
    model.append([PhotoRef(path=old, size=1, mtime_ns=1)])
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    model.set_thumbnail(old, image)

    assert model.replace_entry(0, PhotoRef(path=new, size=1, mtime_ns=1)) is True

    assert model.has_thumbnail(new) is True
    assert model.has_thumbnail(old) is False
    assert model.row_of_path(new) == 0
    assert model.row_of_path(old) == -1
    assert model.needs_thumbnail(new) is False


def test_replace_entry_out_of_range_is_refused(qt_app: QApplication, tmp_path: Path) -> None:
    model = PhotoListModel()
    assert model.replace_entry(5, PhotoRef(path=tmp_path / "x.jpg", size=1, mtime_ns=1)) is False
