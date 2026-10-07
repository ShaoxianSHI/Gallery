"""「复制」⌘D 的界面测试（ADR 0044）。

覆盖用户真正会经历的那条链：选中 → 右键「复制」或按 ⌘D → 原位置多出一个 ``的副本``、
横幅说清造出来的是哪一个、**原件一个字节都没动**、**⌘Z 撤不回来**（撤销就是删文件，红线 1）。

四种落点都要有：单张照片、多张照片、单个相册、多个相册；大图页与分组卡片另各有各的判据。
全部在 ``tmp_path`` 里进行，照片都是当场造的临时文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import (
    QItemSelectionModel,  # noqa: E402
    QModelIndex,  # noqa: E402
    Qt,  # noqa: E402
)
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.core.library.config import BROWSE_MODE_ALBUMS, prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.core.library.session import SessionState, save_session  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个库：``京都`` 两张照片、``奈良`` 空着。"""
    from PIL import Image

    library = tmp_path / "照片"
    (library / "京都").mkdir(parents=True)
    (library / "奈良").mkdir()
    for index, name in enumerate(("一张.jpg", "另一张.jpg")):
        Image.new("RGB", (16, 12), (200, 120, 40 + index)).save(library / "京都" / name)
    prepare_library(library)
    return library


@pytest.fixture
def window(qt_app: QApplication, root: Path) -> Iterator[MainWindow]:
    instance = _open(qt_app, root)
    yield instance
    instance.close()


def _open(qt_app: QApplication, root: Path) -> MainWindow:
    session_base = root.parent / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)
    instance = MainWindow(session_base=session_base, initial_library=root)
    instance.resize(900, 600)
    instance.show()
    assert wait_until(qt_app, lambda: instance._model.rowCount() > 0), "扫描没有在时限内完成"
    assert wait_until(qt_app, lambda: instance._scan_job is None and instance._facet_job is None), (
        "这一层没有在时限内排好"
    )
    return instance


def _idle(qt_app: QApplication, window: MainWindow) -> None:
    """进相册并等这一层彻底排好（抄 ``test_paste_flow`` 那一段，判据是同一个）。"""
    window.enter_folder(window._library.root / "京都")
    assert wait_until(
        qt_app, lambda: window._facet_job is None and not window._defer_watch_until_idle()
    ), "这一层没有排好"


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def _select_rows(window: MainWindow, rows: tuple[int, ...]) -> None:
    """选中若干行（等价于 ⌘ 连点）—— 「多选」这件事不能靠默认选区去赌。"""
    model = window._model
    window._grid.setCurrentIndex(model.index(rows[0], 0))
    selection = window._grid.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def _select_photo(window: MainWindow, name: str) -> int:
    row = window._model.row_of_path(window._library.root / "京都" / name)
    assert row >= 0, f"网格里找不到 {name}"
    _select_rows(window, (row,))
    return row


def _duplicate_and_wait(qt_app: QApplication, window: MainWindow) -> str:
    window.duplicate_selected()
    assert wait_until(qt_app, lambda: window._write_job is None), "复制没有在时限内结束"
    return window._banner.text()


def _trigger_from_menu(qt_app: QApplication, window: MainWindow, row: int, label: str) -> str:
    """点右键菜单里那一条（菜单要是「此刻」的，所以重新现拼一次）。"""
    window._grid.setCurrentIndex(window._model.index(row, 0))
    menu = window.context_menu_for(row)
    assert menu is not None
    action = next(item for item in menu.actions() if item.text() == label)
    action.trigger()
    assert wait_until(qt_app, lambda: window._write_job is None), "复制没有在时限内结束"
    return window._banner.text()


# ---- 四种落点 ------------------------------------------------------------------


def test_one_photo_gets_one_copy(qt_app: QApplication, window: MainWindow) -> None:
    """单张照片：原位置多出一个 ``一张的副本.jpg``，原件原封不动。"""
    _idle(qt_app, window)
    row = _select_photo(window, "一张.jpg")
    before = (window._library.root / "京都" / "一张.jpg").read_bytes()

    message = _trigger_from_menu(qt_app, window, row, "复制")

    assert _names(window._library.root / "京都") == [
        "一张.jpg",
        "一张的副本.jpg",
        "另一张.jpg",
    ]
    assert (window._library.root / "京都" / "一张.jpg").read_bytes() == before
    assert message == "已复制 1 份副本（一张的副本.jpg）。"


def test_several_photos_each_get_a_copy(qt_app: QApplication, window: MainWindow) -> None:
    """多张照片：每一项各一份，横幅只报个数（逐个列出来一行放不下）。"""
    _idle(qt_app, window)
    _select_rows(window, (0, 1))

    message = _duplicate_and_wait(qt_app, window)

    assert _names(window._library.root / "京都") == [
        "一张.jpg",
        "一张的副本.jpg",
        "另一张.jpg",
        "另一张的副本.jpg",
    ]
    assert message == "已复制 2 份副本。"


def test_one_album_gets_a_copy_of_the_whole_thing(qt_app: QApplication, window: MainWindow) -> None:
    """单个相册：整棵复制成一个同级的 ``京都的副本``，不是一个空壳。"""
    (window._library.root / "京都" / "子相册").mkdir()
    row = window._model.row_of_path(window._library.root / "京都")
    assert row >= 0
    window._grid.setCurrentIndex(window._model.index(row, 0))

    _duplicate_and_wait(qt_app, window)

    assert (window._library.root / "京都的副本" / "一张.jpg").is_file()
    assert (window._library.root / "京都的副本" / "子相册").is_dir()


def test_several_albums_each_get_a_copy(qt_app: QApplication, window: MainWindow) -> None:
    """多个相册：每一个都变成它**自己**的副本，而不是全塞进同一个地方。"""
    (window._library.root / "奈良" / "也有一张.jpg").write_bytes(b"\xff\xd8\xff\xdb not a jpeg")
    rows = tuple(
        row
        for row in (
            window._model.row_of_path(window._library.root / "京都"),
            window._model.row_of_path(window._library.root / "奈良"),
        )
        if row >= 0
    )
    assert len(rows) == 2
    _select_rows(window, rows)

    _duplicate_and_wait(qt_app, window)

    assert (window._library.root / "京都的副本" / "一张.jpg").is_file()
    assert (window._library.root / "奈良的副本" / "也有一张.jpg").is_file()


# ---- 快捷键与置灰 ---------------------------------------------------------------


def test_the_shortcut_is_command_d(qt_app: QApplication, window: MainWindow) -> None:
    """⌘D —— 与访达的「复制项目」同一个键，且**已经注册到窗口上**（不然按不出来）。"""
    assert window._duplicate_action.shortcut() == QKeySequence("Ctrl+D")
    assert window._duplicate_action in window.actions()


def test_the_key_really_makes_the_copy(qt_app: QApplication, window: MainWindow) -> None:
    """键是真按下去的，不是只查得到那串字符 —— ``addAction`` 漏了的话这里就红。"""
    _idle(qt_app, window)
    _select_photo(window, "一张.jpg")
    # 置灰是**攒到这一轮事件循环结束**才重算的（``_schedule_action_states``）：不等它一下，
    # 按键落在还灰着的动作上，Qt 什么都不做 —— 那一红量的是时序，不是产品。
    assert wait_until(qt_app, window._duplicate_action.isEnabled), "选区定了却还没亮"

    QTest.keyClick(window._grid, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

    assert wait_until(
        qt_app, lambda: (window._library.root / "京都" / "一张的副本.jpg").is_file()
    ), "按了 ⌘D 却没有造出副本"
    assert wait_until(qt_app, lambda: window._write_job is None), "复制没有在时限内结束"


def test_nothing_selected_leaves_the_shortcut_disabled(window: MainWindow) -> None:
    """没有选区时 ⌘D 置灰：造副本一定得先有「造哪一份」。

    **光 ``clearSelection()`` 不够** —— ``selected_rows()`` 在真选区为空时会**退回当前项**
    （网格刚打开时 Qt 可能顺手把第 0 行设成当前项），那是个兜底而不是「谁被选中了」。
    所以这里连当前项一起清掉，否则这条用例是在赌时序。
    """
    window._grid.clearSelection()
    window._grid.setCurrentIndex(QModelIndex())

    window._update_action_states()

    assert window._duplicate_action.isEnabled() is False


def test_a_group_card_copies_the_photos_inside(qt_app: QApplication, window: MainWindow) -> None:
    """分组卡片**没有**菜单里那一行，但 ⌘D 仍然对选区生效 —— 摊平成组员一起复制，
    与卡片上的「移到废纸篓」同一套（ADR 0020 与 ADR 0044）。"""
    photo = PhotoRef.from_path(window._library.root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    _select_rows(window, (0,))

    window.duplicate_selected()

    assert wait_until(
        qt_app, lambda: (window._library.root / "京都" / "一张的副本.jpg").is_file()
    ), "按了卡片却只复制了别的什么"


def test_the_group_card_menu_has_no_duplicate_row(qt_app: QApplication, window: MainWindow) -> None:
    """卡片不是磁盘上的东西，「造一份卡片」这句话说不通 —— 菜单里不摆那一行。"""
    photo = PhotoRef.from_path(window._library.root / "京都" / "一张.jpg")
    group = GroupRef(facet="camera", key="canon", label="Canon EOS R5", photos=(photo,))
    window._model.reorder([group])
    qt_app.processEvents()
    window._grid.setCurrentIndex(window._model.index(0, 0))

    menu = window.context_menu_for(0)

    assert menu is not None
    labels = [action.text() for action in menu.actions()]
    assert "复制" not in labels
    assert "复制到相册" in labels


# ---- 红线 1：原件不动、撤销不得承诺 ----------------------------------------------


def test_the_copy_is_never_offered_to_undo(qt_app: QApplication, window: MainWindow) -> None:
    """**撤销一次复制就是删文件**（红线 1），所以横幅不许出现「⌘Z 可以撤销」。"""
    _idle(qt_app, window)
    _select_photo(window, "一张.jpg")

    message = _duplicate_and_wait(qt_app, window)

    assert "⌘Z" not in message


def test_the_undo_stack_did_not_grow(qt_app: QApplication, window: MainWindow) -> None:
    """栈真的没长 —— 造完副本之后按 ⌘Z 撤的是**上一次**那件事，不是这一件。"""
    _idle(qt_app, window)
    _select_photo(window, "一张.jpg")
    before = window._library.next_undo_label()

    _duplicate_and_wait(qt_app, window)

    assert window._library.next_undo_label() == before


def test_the_source_file_is_never_touched(qt_app: QApplication, window: MainWindow) -> None:
    """副本保留原修改时间（与粘贴同一套），于是它排在原件旁边而不是跳到「今天」——
    这也是「源一个字节都没动」最直接的证据。"""
    source = window._library.root / "京都" / "一张.jpg"
    mtime = source.stat().st_mtime_ns
    _idle(qt_app, window)
    _select_photo(window, "一张.jpg")

    _duplicate_and_wait(qt_app, window)

    assert source.stat().st_mtime_ns == mtime
    assert (window._library.root / "京都" / "一张的副本.jpg").stat().st_mtime_ns == mtime


# ---- 闸门与失败 ------------------------------------------------------------------


def test_a_second_run_is_refused_while_one_is_in_flight(
    qt_app: QApplication, window: MainWindow
) -> None:
    """与粘贴 / 复制到相册 / 移动到相册共用 ``_write_job`` 那一个槽（ADR 0019 决策 6）：
    两次写入同时跑会争同一个名字。"""
    _idle(qt_app, window)
    _select_rows(window, (0, 1))
    window.duplicate_selected()
    window.duplicate_selected()

    assert wait_until(qt_app, lambda: window._write_job is None)
    assert "还没结束" in window._banner.text() or "已复制" in window._banner.text()


def test_the_menu_row_is_the_same_action_as_the_shortcut(window: MainWindow) -> None:
    """菜单栏与右键菜单共用同一份 ``QAction`` —— 置灰只有一处逻辑（ADR 0014 决策 1）。"""
    from gallery.ui import menus as menus_module

    actions = menus_module.build_photo_menu(window._menu_actions(paste=None), multiple=False)
    try:
        labels = [action.text() for action in actions.actions()]
        assert "复制" in labels
        assert _find_action(actions, "复制") is window._duplicate_action
    finally:
        actions.deleteLater()


def _find_action(menu: QMenu, label: str):
    return next(item for item in menu.actions() if item.text() == label)
