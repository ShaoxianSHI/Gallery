"""网格列数自适应的回归测试。

补的是一个真实缺陷：**条目数不够一行时，网格会把「条目总数」误当成「实际列数」**。
相册模式下根目录常常只有两三个相册，于是两个相册被撑成整屏，必须手动切一次
缩略图尺寸才恢复正常。

这类问题只有把视图真正排出来才看得见，所以测试用 Qt 的离屏平台。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待异步扫描完成的时限。
SCAN_TIMEOUT_SECONDS = 8.0

#: 第一行至少要铺到视口的这个比例才算「铺满」。剩下的几像素是网格页本来就有的
#: 左右留白加上滚动条预留，肉眼看不出来；掉一整列则是 15% 以上，界线很清楚。
FILL_RATIO = 0.95


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def fake_photos(count: int, tmp_path: Path) -> list[PhotoRef]:
    return [
        PhotoRef(path=tmp_path / f"IMG_{index:04d}.jpg", size=1024, mtime_ns=index)
        for index in range(count)
    ]


def make_grid(qt_app: QApplication, tmp_path: Path, count: int, *, width: int = 1200) -> PhotoGrid:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append(fake_photos(count, tmp_path))
    grid.setModel(model)
    grid.resize(width, 800)
    grid.show()
    qt_app.processEvents()
    return grid


def row_fill_ratio(grid: PhotoGrid) -> float:
    """第一行实际铺到了视口宽度的几成。"""
    model = grid.model()
    assert model is not None
    top = grid.visualRect(model.index(0, 0)).top()
    columns = 0
    for row in range(model.rowCount()):
        if grid.visualRect(model.index(row, 0)).top() != top:
            break
        columns += 1
    return columns * grid.gridSize().width() / grid.viewport().width()


def test_examiner_ignores_a_partial_row(qt_app: QApplication, tmp_path: Path) -> None:
    """只有 2 个条目时不能把列数改成 2 —— 那是条目数，不是 Qt 摆不下的列数。"""
    grid = make_grid(qt_app, tmp_path, count=2)
    before = grid.gridSize()

    grid._examine_columns()
    qt_app.processEvents()

    assert grid.gridSize() == before
    assert grid._layout_fix == {}
    assert grid.gridSize().width() < grid.viewport().width() // 3, "两个条目就把窗口铺满了"
    grid.deleteLater()


def test_examiner_corrects_a_genuinely_short_row(
    qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目足够多、而 Qt 真的少摆了一列时，修正逻辑仍然生效。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    intended = grid._laid_out_columns
    assert intended >= 2
    monkeypatch.setattr(grid, "_first_row_columns", lambda: intended - 1)

    grid._examine_columns()
    qt_app.processEvents()

    assert grid._layout_fix, "少摆一列时应当记录修正"
    assert (
        grid.gridSize().width()
        > grid._cell_size_for_width(grid.viewport().width() // intended).width()
    )
    grid.deleteLater()


def test_albums_mode_startup_keeps_normal_tile_size(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """相册模式重启后，布局应与手动确认过的一样 —— 这正是缺陷的复现路径。"""
    root = tmp_path / "测试相册"
    for album, count in (("2022-复活节波尔多", 2), ("2023-京都", 3)):
        for index in range(count):
            make_photo(f"{index}.jpg", directory=root / album)
    prepare_library(root)
    session_base = tmp_path / "session"
    save_session(
        SessionState(recent_roots=(root,), thumbnail_edge=176, browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )

    window = MainWindow(session_base=session_base)
    window.resize(1200, 800)
    window.show()

    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._model.rowCount() >= 2 and window._grid._laid_out_columns:
            break
        time.sleep(0.01)
    # 让「核对列数」的定时器有机会跑起来（真有缺陷时就是它把格子撑大的）
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)

    grid = window._grid
    assert window._browse_mode == BROWSE_MODE_ALBUMS
    assert window._model.rowCount() == 2
    assert isinstance(window._model.entry_at(0), FolderRef)
    assert grid._layout_fix == {}, "不该因为条目少就改写字数"
    assert grid.gridSize().width() < grid.viewport().width() // 3, (
        f"两个相册占满了窗口：格子 {grid.gridSize().width()} / 视口 {grid.viewport().width()}"
    )
    window.close()


# ---- 第一行铺不满：右边空出一整列 ------------------------------------------

#: 缺陷现场：刚打开 GUI，进一个相册，照片没有铺满窗口，右边空出一大片；
#: 放大再缩小一次才铺满。根因是排版时按视口宽算格子，而 Qt 换行用的宽度比视口窄
#: （纵向滚动条 + 样式默认边距），于是 ``1260 // 6`` 那个格子宽正好卡在 Qt 摆不下
#: 6 个的边界上，它只摆 5 个 —— 右边整整空出一列。


def test_rows_arriving_late_still_fill_the_row(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """条目是扫库时分批塞进来的，**塞进来之后**那一版排版才是对的。

    网格刚摆出来时模型还是空的，那一版格子宽度是照着「什么都没有」算的；等条目到齐，
    应当重排一次。相册里的照片常常只有一两屏，冒不出滚动条，也就没有别的机会把它纠正回来。

    所以条数取 12 —— 排成两三行、内容装得下，正是那个「没人来纠正」的情形。
    """
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    grid.setModel(model)
    grid.resize(1260, 800)
    grid.show()
    settle(0.3)  # 让初始那一版排版与它的核对都先跑完

    model.append(fake_photos(12, tmp_path))
    settle(0.3)

    assert row_fill_ratio(grid) >= FILL_RATIO, (
        f"第一行只铺到 {row_fill_ratio(grid):.0%}，右边空了一大块"
    )
    grid.deleteLater()


def test_entering_an_album_fills_the_row(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    settle: Callable[[float], None],
    wait_for: Callable[..., bool],
) -> None:
    """按用户说的那条路复现：打开相册 → 照片铺满窗口。

    照片特意只放十几张：排出来只有两三行、冒不出滚动条，于是「视口不会变化」，
    也就没有任何东西来把「按空模型算出来的格子宽度」纠正回来 —— 这正是缺陷现场。
    """
    root = tmp_path / "测试相册"
    album = root / "2024-旅行"
    for index in range(12):
        make_photo(f"{index}.jpg", directory=album)
    prepare_library(root)
    session_base = tmp_path / "session"
    save_session(
        SessionState(recent_roots=(root,), thumbnail_edge=176, browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )

    window = MainWindow(session_base=session_base)
    window.resize(1280, 860)
    window.show()
    try:
        # 先把相册列表扫出来、显示稳定（= 用户看到的启动画面）
        assert wait_for(lambda: window._model.rowCount() >= 1, timeout=SCAN_TIMEOUT_SECONDS)
        settle(0.3)

        window.enter_folder(album)
        assert wait_for(lambda: window._model.rowCount() >= 12, timeout=SCAN_TIMEOUT_SECONDS)
        settle(0.3)  # 给「核对列数」的定时器留出机会跑完

        assert row_fill_ratio(window._grid) >= FILL_RATIO, (
            f"第一行只铺到 {row_fill_ratio(window._grid):.0%}，右边空了一大块"
        )
    finally:
        window.close()


# ---- 多选：手抖不该变成框选 ------------------------------------------------


def _selected_rows(grid: PhotoGrid) -> list[int]:
    selection = grid.selectionModel()
    assert selection is not None
    return sorted(index.row() for index in selection.selectedIndexes())


def _click_with_drift(
    qt_app: QApplication,
    grid: PhotoGrid,
    *,
    modifiers: Qt.KeyboardModifier,
    anchor: int,
    target: int,
    drift: int,
) -> list[int]:
    """点选 ``anchor``，再带着修饰键点 ``target``，中途把鼠标挪 ``drift`` 像素。

    那一两像素的位移是真人点选时的常态：手在动。按下与抬起之间只要发生移动，
    图标视图就会改用橡皮筋框选，把「A 到 B 的连续区间」换成一个矩形。
    """
    model = grid.model()
    assert model is not None
    grid.selectionModel().clearSelection()
    first = grid.visualRect(model.index(anchor, 0)).center()
    second = grid.visualRect(model.index(target, 0)).center()
    QTest.mouseClick(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first
    )
    qt_app.processEvents()
    QTest.mousePress(grid.viewport(), Qt.MouseButton.LeftButton, modifiers, second)
    for step in range(1, drift + 1):
        QTest.mouseMove(grid.viewport(), second + QPoint(step, 0))
        qt_app.processEvents()
    QTest.mouseRelease(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        modifiers,
        second + QPoint(drift, 0),
    )
    qt_app.processEvents()
    return _selected_rows(grid)


@pytest.mark.parametrize("drift", [0, 1, 2, 4, 9])
def test_shift_click_selects_the_whole_range_however_the_hand_drifts(
    qt_app: QApplication, tmp_path: Path, drift: int
) -> None:
    """⇧ 点选是「从 A 到 B 的连续区间」，不是橡皮筋框出来的矩形。

    缺陷现场：第一行第一个 →（手抖两像素）⇧ 第三行第三个，选中的却是每行最边上
    的那一两个格子 —— 网格分行排，矩形落到屏幕上就是「隔一个跳一个」。
    """
    grid = make_grid(qt_app, tmp_path, count=40)
    columns = grid._laid_out_columns
    assert columns >= 3, "这个缺陷要三列以上才看得出来"
    target = 2 * columns + 2  # 第三行的第三个

    selected = _click_with_drift(
        qt_app,
        grid,
        modifiers=Qt.KeyboardModifier.ShiftModifier,
        anchor=0,
        target=target,
        drift=drift,
    )

    assert selected == list(range(target + 1))
    grid.deleteLater()


def test_command_click_toggles_one_item_however_the_hand_drifts(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """⌘ 点选是「加一个 / 减一个」，同样不该被手抖变成框选。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    columns = grid._laid_out_columns

    selected = _click_with_drift(
        qt_app,
        grid,
        modifiers=Qt.KeyboardModifier.ControlModifier,
        anchor=0,
        target=columns + 1,
        drift=3,
    )

    assert selected == [0, columns + 1]
    grid.deleteLater()


def test_plain_click_selects_only_that_item_however_the_hand_drifts(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """挪动不足拖动距离就还算「点」，不该中途改主意成框选。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    grid.selectionModel().clearSelection()

    point = grid.visualRect(model.index(2, 0)).center()
    drift = 9  # 平台自己的拖动距离是 10
    QTest.mousePress(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    QTest.mouseMove(grid.viewport(), point + QPoint(drift, 1))
    qt_app.processEvents()
    QTest.mouseRelease(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point + QPoint(drift, 1),
    )
    qt_app.processEvents()

    assert _selected_rows(grid) == [2]
    grid.deleteLater()


def test_a_real_drag_still_rubber_band_selects(qt_app: QApplication, tmp_path: Path) -> None:
    """空着手拖过一整行仍然是框选 —— 上一条不能把功能一起干掉。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns
    grid.selectionModel().clearSelection()

    start = grid.visualRect(model.index(0, 0)).center()
    end = grid.visualRect(model.index(columns + 1, 0)).center()
    QTest.mousePress(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    for step in range(1, 6):
        position = QPoint(
            start.x() + (end.x() - start.x()) * step // 5,
            start.y() + (end.y() - start.y()) * step // 5,
        )
        QTest.mouseMove(grid.viewport(), position)
        qt_app.processEvents()
    QTest.mouseRelease(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )
    qt_app.processEvents()

    selected = _selected_rows(grid)
    assert len(selected) > 1, "真的拖过这么远，框选却只选中一个格子"
    assert 0 in selected and columns + 1 in selected
    grid.deleteLater()


def test_shift_pressed_after_the_button_still_extends_the_range(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """先按下鼠标、再补上 ⇧（很常见的手势）也要算扩选。

    这一下按下时还没有修饰键，基类会以为是在拖动，中途还可能画出一个矩形；
    抬起时带着 `⇧`，我们就按「锚点到落点的区间」重算 —— 不能指望基类恰好老实。
    """
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns
    grid.selectionModel().clearSelection()

    anchor_point = grid.visualRect(model.index(0, 0)).center()
    QTest.mouseClick(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, anchor_point
    )
    qt_app.processEvents()
    target_point = grid.visualRect(model.index(2 * columns + 2, 0)).center()
    # 按下时没有修饰键，并且拖动得足够远：让基类真的走一遍框选逻辑。
    QTest.mousePress(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target_point
    )
    for step in range(1, 5):
        QTest.mouseMove(
            grid.viewport(),
            QPoint(anchor_point.x(), target_point.y() * step // 4),
        )
        qt_app.processEvents()
    QTest.mouseRelease(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        target_point,
    )
    qt_app.processEvents()

    assert _selected_rows(grid) == list(range(2 * columns + 3))
    grid.deleteLater()


def test_range_anchors_on_the_last_plain_click_not_the_last_clicked(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """`⌘` 点只是加一个格子，它不该成为下一次 `⇧` 铺开的起点。

    「点 0 → ⌘点 5 → ⇧点 21」应该得到 0..21：从 5 铺开会把 1~4 漏在区间外，
    而访达是从上一次**普通**点选的 0 铺开的。
    """
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns
    target = 3 * columns + 3
    grid.selectionModel().clearSelection()

    def click(row: int, modifiers: Qt.KeyboardModifier) -> None:
        point = grid.visualRect(model.index(row, 0)).center()
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, modifiers, point)
        qt_app.processEvents()

    click(0, Qt.KeyboardModifier.NoModifier)
    click(5, Qt.KeyboardModifier.ControlModifier)
    assert _selected_rows(grid) == [0, 5]
    click(target, Qt.KeyboardModifier.ShiftModifier)

    assert _selected_rows(grid) == list(range(target + 1))
    grid.deleteLater()


def test_repeated_shift_clicks_keep_the_same_anchor(qt_app: QApplication, tmp_path: Path) -> None:
    """连按几次 `⇧`，每一次都从最初那一格铺开（而不是从上一次 `⇧` 的落点）。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns
    first, second = 2 * columns + 2, 3 * columns + 3
    grid.selectionModel().clearSelection()

    def click(row: int, modifiers: Qt.KeyboardModifier) -> None:
        point = grid.visualRect(model.index(row, 0)).center()
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, modifiers, point)
        qt_app.processEvents()

    click(0, Qt.KeyboardModifier.NoModifier)
    click(first, Qt.KeyboardModifier.ShiftModifier)
    assert _selected_rows(grid) == list(range(first + 1))
    click(second, Qt.KeyboardModifier.ShiftModifier)

    assert _selected_rows(grid) == list(range(second + 1))
    grid.deleteLater()


def test_range_is_rebuilt_from_the_press_state_even_if_the_band_won(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """直接钉住「自己算」那一层：不管中间被谁改过，抬起时按锚点重建区间。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns
    target = 2 * columns + 2

    grid.select_row(0)
    # 假装按下时记下的是「只选了 0、当前行 0」，中途基类把选择改成了矩形。
    grid._press_selection = (0,)
    grid._press_anchor = 0
    grid.select_row(target)  # 篡改成「只有落点」
    selection = grid.selectionModel()
    assert selection is not None
    selection.select(model.index(1, 0), selection.SelectionFlag.Select)

    grid._reassert_extended_selection(target, shift=True)

    assert _selected_rows(grid) == list(range(target + 1))
    grid.deleteLater()


def test_command_click_reassert_toggles_one_item(qt_app: QApplication, tmp_path: Path) -> None:
    """「自己算」那一层对 ⌘ 点同样成立：加一个 / 减一个，不是区间。"""
    grid = make_grid(qt_app, tmp_path, count=40)
    model = grid.model()
    assert model is not None
    columns = grid._laid_out_columns

    grid.select_row(0)
    grid._press_selection = (0,)
    grid._press_anchor = 0
    grid._reassert_extended_selection(columns + 1, shift=False)
    assert _selected_rows(grid) == [0, columns + 1]

    grid._press_selection = (0, columns + 1)
    grid._reassert_extended_selection(columns + 1, shift=False)
    assert _selected_rows(grid) == [0], "再点一次同一个格子是减掉它"
    grid.deleteLater()
