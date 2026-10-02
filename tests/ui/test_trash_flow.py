"""⌘⌫ 移到废纸篓的界面测试。

覆盖用户真正会经历的那条链：选中 → ⌘⌫ → 网格少了行、磁盘少了文件、横幅说清楚
能不能撤销 → ⌘Z 取回来 → 行插回原来的位置。

全部在 ``tmp_path`` 里进行，废纸篓由 ``fake_trash`` 指向临时目录（见 tests/conftest.py）：
**绝不能**把测试照片搬进用户真实的 ``~/.Trash``。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, QModelIndex  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描 / 缩略图完成的时限。


def open_window(
    qt_app: QApplication, root: Path, *, browse_mode: str = BROWSE_MODE_FLAT
) -> MainWindow:
    session_base = root / "状态"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=browse_mode),
        base=session_base,
    )
    # 没有配置目录时 MainWindow 会问「缓存放哪」，那个模态框在测试里没人点。
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    if not wait_until(qt_app, lambda: window._model.rowCount() > 0):
        raise AssertionError("扫描没有在时限内完成")
    return window


def select_rows(window: MainWindow, *rows: int) -> None:
    """选中若干行（等价于 ⌘ 点 / Shift 连选），并把它们设为当前行。"""
    model = window._model
    selection = window._grid.selectionModel()
    assert selection is not None
    # 顺序要紧：setCurrentIndex 会先把已有选择清掉，所以当前行必须先定下来。
    window._grid.setCurrentIndex(model.index(rows[0], 0))
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows:
        selection.select(model.index(row, 0), flags)
    QApplication.processEvents()


def clear_selection(window: MainWindow) -> None:
    """真的把选中清空。

    网格获得焦点时会自己选中第一格（访达也这样），所以「刚打开时没有选中」并不是
    一个可以依赖的状态 —— 要测「没选中就不能删」，就得真的把选中清掉。
    """
    selection = window._grid.selectionModel()
    assert selection is not None
    selection.clearSelection()
    window._grid.setCurrentIndex(QModelIndex())
    QApplication.processEvents()


def qt_app_process(window: MainWindow) -> None:
    QApplication.processEvents()


def row_of(window: MainWindow, path: Path) -> int:
    row = window._model.row_of_path(path)
    assert row >= 0, f"{path.name} 不在网格里"
    return row


# ---- 命令本身 ---------------------------------------------------------------


def test_trash_action_is_bound_to_cmd_backspace(qt_app: QApplication, tmp_path: Path) -> None:
    """⌘⌫ 触发「移到废纸篓」，菜单里能看到。"""
    window = MainWindow(session_base=tmp_path / "状态")
    try:
        assert window._trash_action.text() == "移到废纸篓"
        assert window._trash_action.shortcut() == QKeySequence("Ctrl+Backspace")
    finally:
        window.close()


def test_trash_is_disabled_without_a_selection(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        clear_selection(window)
        assert window._trash_action.isEnabled() is False
        select_rows(window, 0)
        assert window._trash_action.isEnabled() is True
    finally:
        window.close()


def test_undo_action_names_what_it_will_undo(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """用户在按 ⌘Z 之前就知道会撤掉什么。"""
    picture = make_photo("IMG_0001.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        assert window._undo_action.text() == "撤销"
        assert window._undo_action.isEnabled() is False

        select_rows(window, 0)
        window.trash_selected()
        assert window._undo_action.text() == "撤销 移到废纸篓"
        assert window._undo_action.isEnabled() is True

        window.undo_last_write()
        assert window._undo_action.text() == "撤销", "撤完了就没有可撤的了"
        assert window._undo_action.isEnabled() is False
        assert picture.exists()
    finally:
        window.close()


# ---- 端到端 -----------------------------------------------------------------


def test_trashing_one_photo_takes_it_off_the_grid_and_the_disk(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("保留.jpg", directory=tmp_path)
    picture = make_photo("删掉.jpg", directory=tmp_path)
    window = open_window(qt_app, tmp_path)
    try:
        select_rows(window, row_of(window, picture))

        window.trash_selected()
        qt_app_process(window)

        assert not picture.exists()
        assert (fake_trash / "删掉.jpg").exists(), "照片只是搬去了废纸篓"
        assert window._model.rowCount() == 1
        assert window._model.row_of_path(picture) == -1
        assert "已把「删掉.jpg」移到废纸篓" in window._banner.text()
        assert "⌘Z 可以撤销" in window._banner.text()
    finally:
        window.close()


def test_a_batch_is_one_undo(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    photos = [make_photo(f"{name}.jpg") for name in ("一", "二", "三")]
    window = open_window(qt_app, tmp_path)
    try:
        select_rows(window, *(row_of(window, photo) for photo in photos))

        window.trash_selected()
        qt_app_process(window)

        assert window._model.rowCount() == 0
        assert all(not photo.exists() for photo in photos)
        assert "已把 3 项移到废纸篓" in window._banner.text()

        window.undo_last_write()
        qt_app_process(window)

        assert all(photo.exists() for photo in photos)
        assert window._model.rowCount() == 3
        assert "已从废纸篓取回 3 项" in window._banner.text()
    finally:
        window.close()


def test_undo_puts_a_restored_photo_back_in_its_sorted_place(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """取回来之后，网格顺序仍然和扫描一致（相册在前、同类按名字）。"""
    for name in ("a", "b", "c"):
        make_photo(f"{name}.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        select_rows(window, row_of(window, tmp_path / "b.jpg"))
        window.trash_selected()
        qt_app_process(window)
        assert [entry.name for entry in window._model.entries()] == ["a.jpg", "c.jpg"]

        window.undo_last_write()
        qt_app_process(window)

        assert [entry.name for entry in window._model.entries()] == ["a.jpg", "b.jpg", "c.jpg"]
    finally:
        window.close()


def test_failed_items_stay_on_the_grid(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """动不了的那一张必须留在网格上，并说明为什么。"""
    keep = make_photo("留下.jpg")
    doomed = make_photo("移走.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        # 让其中一张移不动：把那一行指向一个已经不存在的文件。
        lost_row = row_of(window, doomed)
        window._model.replace_entry(
            lost_row, PhotoRef(path=tmp_path / "早就不在了.jpg", size=1, mtime_ns=1)
        )
        select_rows(window, row_of(window, keep), lost_row)

        # 留下来的那一行指向一个不存在的文件，解码它必然失败，而那条失败会在**之后**
        # 才报上来，与「没移走」抢同一个横幅。让后台消息给用户动作让位是产品该保证的事
        # （BANNER_STICKY_SECONDS），这条用例就照真实时序跑，不替它绕。
        window.trash_selected()
        qt_app_process(window)

        assert not keep.exists()
        assert window._model.rowCount() == 1, "没移走的那一行必须留着"
        assert "没移走" in window._banner.text()
        assert "早就不在了.jpg" in window._banner.text()
    finally:
        window.close()


def test_trashing_an_album_takes_the_whole_album(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("第一张.jpg", directory=tmp_path / "2025 苏州")
    window = open_window(qt_app, tmp_path, browse_mode=BROWSE_MODE_ALBUMS)
    try:
        assert isinstance(window._model.entry_at(0), FolderRef)
        select_rows(window, 0)

        window.trash_selected()
        qt_app_process(window)

        assert not (tmp_path / "2025 苏州").exists()
        assert (fake_trash / "2025 苏州" / "第一张.jpg").exists()
        assert "相册「2025 苏州」" in window._banner.text()

        window.undo_last_write()
        qt_app_process(window)

        assert (tmp_path / "2025 苏州" / "第一张.jpg").exists()
        assert isinstance(window._model.entry_at(0), FolderRef)
    finally:
        window.close()


def test_thumbnail_cache_survives_so_undo_is_instant(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """删掉的东西还在废纸篓里，缩略图缓存留着：取回来时不必重新解码。"""
    picture = make_photo("海边.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(row_of(window, picture))
        assert wait_until(qt_app, lambda: window._model.has_thumbnail(picture))

        window.trash_selected()
        cached_bytes = window._library.thumbnails.total_bytes()
        assert cached_bytes > 0, "磁盘缓存刻意留着"

        window.undo_last_write()
        # 内存里那份在删掉时丢了（那条路径上已经没有文件），但磁盘缓存还在，
        # 于是取回来之后立刻就画得出来，不必重新解码原图。
        assert window._library.thumbnails.total_bytes() == cached_bytes
        assert wait_until(qt_app, lambda: window._model.has_thumbnail(picture))
    finally:
        window.close()


def test_delete_is_refused_while_renaming(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """改名的过程中键盘归输入框，⌘⌫ 不该把正在编辑的那一张删掉。"""
    picture = make_photo("IMG_0001.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        window._grid.select_row(0)
        assert window._grid.begin_rename(window._grid.currentIndex()) is True
        qt_app_process(window)

        assert window._trash_action.isEnabled() is False
        window.trash_selected()  # 即使被别处触发也不动手
        assert picture.exists()
    finally:
        window.close()
