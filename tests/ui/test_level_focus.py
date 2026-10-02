"""返回上一层时，上一层要记得你刚从哪儿来。

`Esc` 与双击空白处退上去之后，网格上**原来那一格仍然选中** —— 不然每退一级都要重新
找一遍刚才在的那个相册，访达里用惯了的人会觉得软件把自己甩掉了。

「那一格」是什么，取决于刚离开的是哪一层：相册卡片、分组卡片，或者（回到刚看过的
那个相册 / 那个组时）一张照片。文件里的每一条都走同一套「记住 → 摆出来」。

**凭什么记得**：靠标识而不是行号（重扫、重排、换排序之后行号都会变），层与格的标识
由 ``ui.navigation`` 的纯函数算（见 ``tests/ui/test_navigation.py``）。

**记错了会怎样**：这一层上一次停着的那一条多半已经改名 / 移走 / 删掉了 —— 那时如实
地什么都不选，而不是挑一格错的装成记得。所以「串到别的相册去」也要挡住：同名相册是
两个不同的层，各记各的。

全部用离屏 Qt，照片都是 ``tmp_path`` 里当场造的。分组字段一律取「所在文件夹」
（``folder``）：只看路径、不读 EXIF，于是这些测试既不依赖本机装了 exiftool，
也不必再造一个假的批量读取器。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import GroupRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    save_session,
)
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描 / 排完的时限。
SCAN_TIMEOUT_SECONDS = 8.0

#: 分组字段：按「所在文件夹」分（只看路径，不读 EXIF，见模块文档）。
FOLDER_FACET = "folder"


def open_window(
    qt_app: QApplication,
    root: Path,
    *,
    browse_mode: str = BROWSE_MODE_ALBUMS,
    group_facet: str | None = None,
) -> MainWindow:
    """开一个窗口，等当前这一层列完。

    库根放在 ``tmp_path/库``：会话目录与 ``.gallery/`` 都在库外，免得根目录里混进
    「状态」「.gallery」这些不是相册的文件夹，行数对不上。
    """
    session_base = root.parent / "状态"
    save_session(
        SessionState(recent_roots=(root,), browse_mode=browse_mode, group_facet=group_facet),
        base=session_base,
    )
    # 没有配置目录时 MainWindow 会问「缓存放哪」，那个模态框在测试里没人点。
    prepare_library(root)
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    wait_until(window, qt_app, lambda: window._scan_job is None and window._facet_job is None)
    return window


def wait_until(
    window: MainWindow, qt_app: QApplication, ready: Callable[[], bool], *, what: str = "这一层"
) -> None:
    """转到 ``ready()`` 成立为止（扫描与排序都在后台线程上）。"""
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while True:
        qt_app.processEvents()
        if ready():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what}没有在时限内就绪（当前 {window._model.rowCount()} 行）")
        time.sleep(0.02)


def wait_rows(window: MainWindow, qt_app: QApplication, rows: int, *, what: str = "这一层") -> None:
    """等网格摆出**这么多行**，也就是这一层排完了、选中状态已经落定。

    刻意不等「路径变了」或「退出了分组」：那两样都是同步设上去的（``enter_folder`` 与
    ``enter_group`` 一进去就改），等它们等于一步都没等，条目还在路上，选中当然还没摆出来。
    """
    wait_until(window, qt_app, lambda: window._model.rowCount() == rows, what=what)


def current_name(window: MainWindow) -> str | None:
    """当前项（网格上那个带选中框的格子）的名字；没选中就是 ``None``。

    刻意去问**当前项**而不是 ``selected_rows()``：网格刚摆出来时 Qt 可能顺手把第 0 行
    设成当前项（「没有选中」不是一个可以依赖的状态），而用户看到的那一格是当前项。
    """
    entry = window._model.entry_at(window._grid.current_row())
    return entry.name if entry is not None else None


def double_click_blank(window: MainWindow, qt_app: QApplication) -> None:
    """在当前这一层的空白处双击一下（与访达里双击空白处往上走是同一个手势）。"""
    grid = window._grid
    rect = grid.viewport().rect()
    blank = next(
        (
            QPoint(x, y)
            for y in range(2, rect.height() - 2, 6)
            for x in range(2, rect.width() - 2, 6)
            if not grid.indexAt(QPoint(x, y)).isValid()
        ),
        rect.center(),
    )
    viewport = grid.viewport()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, blank)
    QTest.mouseDClick(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, blank, 20
    )
    qt_app.processEvents()


def test_escape_from_an_album_selects_that_album(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2, what="相册内")

        window.go_up()
        wait_until(window, qt_app, lambda: window._model.rowCount() == 2, what="退回后的这一层")

        assert window._current_dir == library
        assert current_name(window) == "海边", "退回来时该停在刚离开的那个相册上"
    finally:
        window.close()


def test_double_clicking_blank_out_of_an_album_selects_that_album(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """双击空白处与 `Esc` 是同一件事，所以选中状态也必须一样。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "山巅")
        wait_rows(window, qt_app, 1, what="相册内")

        double_click_blank(window, qt_app)
        wait_rows(window, qt_app, 2, what="退回库根")

        assert window._current_dir == library
        assert current_name(window) == "山巅"
    finally:
        window.close()


def test_escape_from_a_group_selects_that_group_card(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0002.jpg", directory=library / "山巅")
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT, group_facet=FOLDER_FACET)
    try:
        wait_rows(window, qt_app, 2, what="分组")
        target = window._model.group_at(0)
        assert isinstance(target, GroupRef)
        window.enter_group(target)
        wait_rows(window, qt_app, 1, what="进组")

        window.go_up()
        wait_rows(window, qt_app, 2, what="退出分组之后")

        selected = window._model.entry_at(window._grid.current_row())
        assert isinstance(selected, GroupRef), f"退回来该停在组卡片上，现在停的是 {selected!r}"
        assert selected.key == target.key
    finally:
        window.close()


def test_re_entering_an_album_keeps_the_photo_you_were_on(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """回来时那一格可能是一张照片：在相册里看的是哪一张，再进去还是那一张。"""
    library = tmp_path / "库"
    second = make_photo("IMG_0002.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_rows(window, qt_app, 2, what="相册内")
        window._grid.select_row(window._model.row_of_path(second))
        assert current_name(window) == "IMG_0002.jpg"

        window.go_up()
        wait_rows(window, qt_app, 2, what="退回库根")
        assert current_name(window) == "海边"

        window.enter_folder(library / "海边")
        wait_rows(window, qt_app, 2, what="再进相册")

        assert current_name(window) == "IMG_0002.jpg", "再进同一个相册时该停在刚才那张上"
    finally:
        window.close()


def test_re_entering_a_group_keeps_the_photo_you_were_on(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    library = tmp_path / "库"
    second = make_photo("IMG_0002.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "海边")
    make_photo("IMG_0001.jpg", directory=library / "山巅")
    window = open_window(qt_app, library, browse_mode=BROWSE_MODE_FLAT, group_facet=FOLDER_FACET)
    try:
        wait_rows(window, qt_app, 2, what="分组")
        target = next(
            group
            for group in (window._model.group_at(row) for row in range(window._model.rowCount()))
            if isinstance(group, GroupRef) and any(photo.path == second for photo in group.photos)
        )
        window.enter_group(target)
        wait_rows(window, qt_app, 2, what="进组")
        window._grid.select_row(window._model.row_of_path(second))
        assert current_name(window) == "IMG_0002.jpg"

        window.go_up()
        wait_rows(window, qt_app, 2, what="退出分组之后")
        window.enter_group(target)
        wait_rows(window, qt_app, 2, what="再进组")

        assert current_name(window) == "IMG_0002.jpg"
    finally:
        window.close()


def test_a_level_does_not_inherit_another_levels_photo(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """两个同名相册是两个不同的层：各自记各自的，不串。"""
    library = tmp_path / "库"
    make_photo("IMG_0001.jpg", directory=library / "旅行" / "2024")
    make_photo("IMG_0009.jpg", directory=library / "家庭" / "2024")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "旅行" / "2024")
        wait_rows(window, qt_app, 1, what="旅行/2024")
        window._grid.select_row(0)
        assert current_name(window) == "IMG_0001.jpg"

        window.go_up()  # → 旅行
        wait_rows(window, qt_app, 1, what="旅行")
        assert current_name(window) == "2024"
        window.go_up()  # → 库根
        wait_rows(window, qt_app, 2, what="库根")
        window.enter_folder(library / "家庭")
        wait_rows(window, qt_app, 1, what="家庭")
        window.enter_folder(library / "家庭" / "2024")
        wait_rows(window, qt_app, 1, what="家庭/2024")

        # 「没有选中」不是一个能依赖的状态（Qt 可能顺手把第 0 行设成当前项），
        # 所以只断言「不是刚才那张」。
        assert current_name(window) != "IMG_0001.jpg", "家庭/2024 不该想起旅行/2024 里的照片"
    finally:
        window.close()


def test_a_remembered_entry_that_is_gone_selects_nothing(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """记着的那一格已经不在了（这里是被移走）：如实什么都不选，也不报错。"""
    library = tmp_path / "库"
    picture = make_photo("IMG_0001.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_rows(window, qt_app, 1, what="相册内")
        window._grid.select_row(0)

        window.go_up()
        wait_rows(window, qt_app, 1, what="退回库根")
        assert current_name(window) == "海边"
        picture.unlink()
        window.enter_folder(library / "海边")
        wait_until(
            window,
            qt_app,
            lambda: window._model.rowCount() == 0 and window._scan_job is None,
            what="空相册",
        )

        assert current_name(window) != "IMG_0001.jpg"
    finally:
        window.close()


def test_changing_the_sort_keeps_the_selection(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """换排序字段会整层重排：选中的是那张照片，不是「第几行」。"""
    library = tmp_path / "库"
    # 顺序按创建时间排，于是「按名字」这一次真的会换位置。
    make_photo("c.jpg", directory=library / "海边")
    make_photo("a.jpg", directory=library / "海边")
    make_photo("b.jpg", directory=library / "海边")
    window = open_window(qt_app, library)
    try:
        window.enter_folder(library / "海边")
        wait_rows(window, qt_app, 3, what="相册内")
        window._grid.select_row(window._model.rowCount() - 1)
        assert current_name(window) == "b.jpg"

        window.set_sort_facet("name")
        wait_rows(window, qt_app, 3, what="换排序之后")

        assert [entry.name for entry in window._model.entries()] == ["a.jpg", "b.jpg", "c.jpg"]
        assert current_name(window) == "b.jpg", "重排之后该跟着照片走，而不是停在同一行"
    finally:
        window.close()
