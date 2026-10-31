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

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QItemSelectionModel, QModelIndex  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until

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
from gallery.ui import desktop  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描 / 缩略图完成的时限。


def open_window(
    qt_app: QApplication, root: Path, *, browse_mode: str = BROWSE_MODE_FLAT
) -> MainWindow:
    session_base = root / "状态"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=browse_mode)
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
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    system_sounds: list[int],
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
        assert system_sounds == [desktop.TRASH_SOUND_ID]

        window.undo_last_write()
        assert window._undo_action.text() == "撤销", "撤完了就没有可撤的了"
        assert window._undo_action.isEnabled() is False
        assert picture.exists()
        assert system_sounds == [desktop.TRASH_SOUND_ID], "取回来不是删除，不该再响一次"
    finally:
        window.close()


# ---- 端到端 -----------------------------------------------------------------


def test_trashing_one_photo_takes_it_off_the_grid_and_the_disk(
    qt_app: QApplication,
    tmp_path: Path,
    fake_trash: Path,
    make_photo: Callable[..., Path],
    system_sounds: list[int],
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
        assert system_sounds == [desktop.TRASH_SOUND_ID], "照片移进废纸篓时响一声"
        assert "已将「删掉.jpg」移到废纸篓" in window._banner.text()
        assert "⌘Z 可撤销" in window._banner.text()
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
        assert "已将 3 项移到废纸篓" in window._banner.text()

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
    """取回来之后，网格顺序仍然与当前排序一致（默认修改时间升序，ADR 0049）。"""
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

        # 留下来的那一行指向一个不存在的文件，解码它必然失败，而那条失败会在**之后**才报
        # 上来，与「没移走」抢同一个横幅。让后台消息给用户动作让位是产品该保证的事
        # （BANNER_STICKY_SECONDS），所以这里照真实时序跑，不替它绕。
        window.trash_selected()
        qt_app_process(window)

        assert not keep.exists()
        assert window._model.rowCount() == 1, "没移走的那一行必须留着"
        assert "未能移走" in window._banner.text()
        assert "早就不在了.jpg" in window._banner.text()
    finally:
        window.close()


def test_no_sound_when_nothing_moved(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    system_sounds: list[int],
) -> None:
    """一张都没移走时不该响 —— 那一声在用户听来就是「删掉了」。"""
    make_photo("留下.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        row = window._model.row_of_path(tmp_path / "留下.jpg")
        window._model.replace_entry(
            row, PhotoRef(path=tmp_path / "早就不在了.jpg", size=1, mtime_ns=1)
        )
        select_rows(window, row)

        window.trash_selected()
        qt_app_process(window)

        assert system_sounds == []
        assert window._model.rowCount() == 1, "动不了的那一行必须留着"
    finally:
        window.close()


def test_trashing_an_album_takes_the_whole_album(
    qt_app: QApplication,
    tmp_path: Path,
    fake_trash: Path,
    make_photo: Callable[..., Path],
    system_sounds: list[int],
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
        assert system_sounds == [desktop.TRASH_SOUND_ID], "相册走的是同一条删除入口"
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


# ---- 大图页上删掉之后按 ⌘Z --------------------------------------------------


def _open_detail_of(window: MainWindow, photo: Path) -> int:
    """选中那张照片并打开大图，返回它的行号。"""
    row = row_of(window, photo)
    select_rows(window, row)
    window.open_current_entry()
    qt_app_process(window)
    assert window._stack.currentWidget() is window._detail_page, "未能进大图页"
    return row


def _showing(window: MainWindow) -> str:
    """大图页上正显示的是哪一张（``""`` = 这一层一张照片都没有）。"""
    photo = window._model.photo_at(window._current_row)
    return photo.name if photo is not None else ""


def _window_title(window: MainWindow) -> str:
    """窗口顶边那一行现在显示的名字（大图页是正在看的那张照片的文件名，ADR 0025）。

    **断言它是因为它是「画面上那一格真的换了」的凭据**：``_current_row`` 指对了不等于
    画面换了 —— 撤销会让整层重排，行号常常**碰巧**又落回刚取回来的那一张，而大图区
    里仍然是上一张的像素、标题也还写着上一张的名字。真正换画面时 ``_load_detail``
    会刷新标题栏。
    """
    return window._title_bar.current_text if window._owns_title_bar else window.windowTitle()


def test_undo_brings_the_deleted_photo_back_into_view(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """**大图页上删掉一张之后按 ⌘Z，要跳回刚删掉的那一张**（ADR 0021）。

    删掉之后界面会接着看下一张（看照片时最顺手的动作就是「这张不要，下一张」），于是
    撤回来时人正站在**另一张**上；而撤销会整层重排，刚取回来的那张完全可能落在视野
    之外。看照片时按 ⌘Z，期望是看回刚才那张。

    """
    for name in ("a", "b", "c"):
        make_photo(f"{name}.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        doomed = tmp_path / "b.jpg"
        _open_detail_of(window, doomed)
        assert _showing(window) == "b.jpg"

        window.trash_selected()
        qt_app_process(window)
        assert _showing(window) == "c.jpg", "删掉一张之后接着看下一张"

        window.undo_last_write()
        qt_app_process(window)

        assert doomed.exists(), "照片确实从废纸篓取回来了"
        assert window._stack.currentWidget() is window._detail_page, "撤销不该把人踢回网格"
        assert _showing(window) == "b.jpg", "⌘Z 之后要回到刚删掉的那一张"
        assert _window_title(window) == "b.jpg", (
            "画面上换的必须是刚删掉的那一张 —— 整层重排会让行号碰巧指对，标题不会骗人"
        )
        assert window._grid.current_row() == window._current_row, (
            "网格与大图从此指着同一格（见 _show_next_after_trash 那条约定）"
        )
    finally:
        window.close()


def test_the_confirmation_survives_the_jump_to_the_next_photo(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """**删掉正在看的那一张之后自动跳下一张，那句确认仍然看得见**。

    「大图页上翻一张照片就把横幅收掉」不能误伤这里：那一跳与那句话的次序是
    **先跳、后说**（见 ``write_ops.trash_selected`` —— ``_show_next_after_trash`` 在
    ``_show_banner`` 前面）。次序一旦反过来，用户看到的就是一句刚说完就消失的话：
    「已把「b.jpg」移到废纸篓」再也没人知道发生过。
    """
    for name in ("a", "b", "c"):
        make_photo(f"{name}.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        _open_detail_of(window, tmp_path / "b.jpg")

        window.trash_selected()
        qt_app_process(window)

        assert _showing(window) == "c.jpg", "删掉一张之后接着看下一张"
        assert window._banner.isVisible(), "跳到下一张不该把刚说的那句话一起带走"
        assert "已将「b.jpg」移到废纸篓" in window._banner.text()
        assert "⌘Z 可撤销" in window._banner.text()
    finally:
        window.close()


def test_undo_reopens_the_photo_when_the_level_emptied_out(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """删掉这一层最后一张时界面退回了网格 —— 撤销要把它**重新打开**。

    删除是在大图页上做的：取回来之后还把人留在网格上，等于让他自己再找一次刚删掉的那张。
    """
    only = make_photo("只剩这张.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        _open_detail_of(window, only)

        window.trash_selected()
        qt_app_process(window)
        assert window._stack.currentWidget() is window._grid_page, "这一层空了，网格放得出它"
        assert window._model.rowCount() == 0

        window.undo_last_write()
        qt_app_process(window)

        assert only.exists()
        assert window._stack.currentWidget() is window._detail_page, "撤销要放回大图页"
        assert _showing(window) == "只剩这张.jpg"
        assert _window_title(window) == "只剩这张.jpg"
    finally:
        window.close()


def test_undo_from_the_grid_stays_on_the_grid(
    qt_app: QApplication, tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """网格页上删的，撤销之后**留在网格**上 —— ⌘Z 不该把人甩进大图页。

    这一条与上面两条是一对：「跳回去」只在删除确实发生在大图页上时才做。
    """
    doomed = make_photo("删掉.jpg")
    keep = make_photo("留下.jpg")
    window = open_window(qt_app, tmp_path)
    try:
        select_rows(window, row_of(window, doomed))
        window.trash_selected()
        qt_app_process(window)
        assert window._stack.currentWidget() is window._grid_page

        window.undo_last_write()
        qt_app_process(window)

        assert doomed.exists()
        assert window._stack.currentWidget() is window._grid_page, "网格上按 ⌘Z 该留在网格"
        assert row_of(window, keep) >= 0
    finally:
        window.close()


def test_delete_is_refused_while_renaming(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    system_sounds: list[int],
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
        assert system_sounds == []
    finally:
        window.close()
