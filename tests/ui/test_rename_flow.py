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

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.browser.rename_edit import RenameEdit  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

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


# ---- 端到端 -----------------------------------------------------------------


def open_window(
    qt_app: QApplication, root: Path, *, browse_mode: str = BROWSE_MODE_FLAT
) -> MainWindow:
    session_base = root / "状态"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=browse_mode),
        base=session_base,
    )
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
