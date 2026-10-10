"""把照片拖到相册卡片上（ADR 0062）。

要盯的是四样：**落点只认相册**、**落空什么也不发生**、**相册自己拖不动**，
以及**开了拖放之后框选与扩选还在**（网格那套鼠标处理是这一族里最容易被踩坏的地方）。

**拖动本身用真实的鼠标事件走一遍**（``QTest.mousePress/move``），而不是直接调
``startDrag``：``QAbstractItemView`` 越过拖动距离之后先问模型那一行有没有
``ItemIsDragEnabled``，没有就压根不会调用 ``startDrag`` —— 直接调它的话，一整套
拖放都可以不存在而测试全绿（真踩过，见 ``test_dragging_a_photo_reaches_start_drag``）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import make_grid  # noqa: E402
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QPixmap,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.delegate import PhotoDelegate  # noqa: E402
from gallery.ui.browser.drag import DRAG_EDGE, card_size, drag_pixmap  # noqa: E402
from gallery.ui.browser.grid import PHOTO_MIME_TYPE, PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402


def _solid_image(width: int, height: int) -> QImage:
    """一张纯色的 ``QImage``（模型收的是它，不是 ``QPixmap``）。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(20, 90, 160))
    return image


ALBUM_ROW = 0


def album_and_photos(qt_app: QApplication, tmp_path: Path, photos: int = 6) -> PhotoGrid:
    """一个网格：第 0 行是相册，其后是照片。相册排在最前（见 ``_sort_key``）。"""
    grid = make_grid(qt_app, tmp_path, count=0)
    model = grid.model()
    assert isinstance(model, PhotoListModel)
    model.clear()
    model.append([FolderRef(path=tmp_path / "京都", mtime_ns=0)])
    model.append(
        [
            PhotoRef(path=tmp_path / f"IMG_{index:04d}.jpg", size=1024, mtime_ns=index)
            for index in range(photos)
        ]
    )
    qt_app.processEvents()
    return grid


def center_of(grid: PhotoGrid, row: int) -> QPoint:
    model = grid.model()
    assert model is not None
    return grid.visualRect(model.index(row, 0)).center()


def ours() -> QMimeData:
    """一份「我们自己发起的」拖动数据。"""
    mime = QMimeData()
    mime.setData(PHOTO_MIME_TYPE, b"/photos/IMG_0000.jpg")
    return mime


def theirs() -> QMimeData:
    """外面拖进来的数据（文件 URL，不是我们的格式）。"""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/tmp/whatever.jpg")])
    return mime


def _keep(event: object, mime: QMimeData) -> object:
    """让事件把那份数据**留到手边**。

    拖放事件的构造函数**不接管** ``QMimeData`` 的所有权，而这里传进去的是当场造出来的临时
    对象 —— 它当场就被回收，事件里剩下的指针指向已释放的内存，于是下一次 ``mimeData()``
    直接段错误（症状与 UI 测试进程退出时的 SIGSEGV 一模一样）。
    """
    event._mime = mime  # type: ignore[attr-defined]
    return event


def enter_at(point: QPoint, mime: QMimeData) -> QDragEnterEvent:
    return _keep(  # type: ignore[return-value]
        QDragEnterEvent(
            point,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
        mime,
    )


def move_at(point: QPoint, mime: QMimeData) -> QDragMoveEvent:
    return _keep(  # type: ignore[return-value]
        QDragMoveEvent(
            point,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
        mime,
    )


def drop_at(point: QPoint, mime: QMimeData) -> QDropEvent:
    return _keep(  # type: ignore[return-value]
        QDropEvent(
            point,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
        mime,
    )


def drag_out_of(grid: PhotoGrid, row: int, qt_app: QApplication) -> bool:
    """从第 ``row`` 行真的拖出去一段，返回 ``startDrag`` 有没有被调用。

    走真实的鼠标事件：``QAbstractItemView`` 是**自己**在 ``mouseMoveEvent`` 里越过拖动
    距离之后才去问模型要不要起拖的，直接调 ``startDrag`` 绕过了这一段。
    """
    called: list[object] = []
    original = grid.startDrag
    grid.startDrag = lambda supported: called.append(supported)  # type: ignore[method-assign]
    try:
        grid.selectionModel().clearSelection()
        start = center_of(grid, row)
        QTest.mousePress(
            grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
        )
        qt_app.processEvents()
        for step in range(1, 8):
            QTest.mouseMove(grid.viewport(), QPoint(start.x() + 30 * step, start.y() + 15 * step))
            qt_app.processEvents()
        QTest.mouseRelease(
            grid.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(start.x() + 210, start.y() + 105),
        )
        qt_app.processEvents()
    finally:
        grid.startDrag = original  # type: ignore[method-assign]
    return bool(called)


# ---- 拖得起来 -------------------------------------------------------------


def test_dragging_a_photo_reaches_start_drag(qt_app: QApplication, tmp_path: Path) -> None:
    """从照片上拖出去，Qt 真的会来问 ``startDrag``。

    这一条钉的是 ``PhotoListModel.flags`` 里那个 ``ItemIsDragEnabled``：少了它，
    ``QAbstractItemView`` 越过拖动距离后直接回去画橡皮筋，**整套拖放都不会发生**，
    而症状只是「拖起来没反应」。
    """
    grid = album_and_photos(qt_app, tmp_path)

    assert drag_out_of(grid, 1, qt_app), "从照片上拖出去，Qt 没有调用 startDrag"
    grid.deleteLater()


def test_an_album_card_cannot_be_dragged(qt_app: QApplication, tmp_path: Path) -> None:
    """相册卡片拖不动 —— 「把相册拖进另一个相册」要动整棵子树，是另一件事。"""
    grid = album_and_photos(qt_app, tmp_path)

    assert not drag_out_of(grid, ALBUM_ROW, qt_app), "相册卡片被算成了可拖的条目"
    grid.deleteLater()


# ---- 落点只认相册 ---------------------------------------------------------


def test_dropping_on_an_album_reports_that_album(qt_app: QApplication, tmp_path: Path) -> None:
    grid = album_and_photos(qt_app, tmp_path)
    seen: list[Path] = []
    grid.album_dropped.connect(seen.append)

    grid.dropEvent(drop_at(center_of(grid, ALBUM_ROW), ours()))

    assert seen == [tmp_path / "京都"]
    grid.deleteLater()


def test_dropping_anywhere_but_an_album_does_nothing(qt_app: QApplication, tmp_path: Path) -> None:
    """落到照片上、落到空白处都必须**一个信号都不发**（需求：除此之外都不触发）。"""
    grid = album_and_photos(qt_app, tmp_path)
    seen: list[Path] = []
    grid.album_dropped.connect(seen.append)

    blank = QPoint(5, grid.viewport().height() - 2)
    for point in (center_of(grid, 1), blank):
        grid.dropEvent(drop_at(point, ours()))

    assert seen == [], "落到非相册上却触发了移动"
    grid.deleteLater()


def test_the_drop_never_reorders_the_model(qt_app: QApplication, tmp_path: Path) -> None:
    """模型里没有 ``dropMimeData``：落空回原处是**无事发生**，不需要撤销逻辑。

    这一条是「松开后回到原地」的实现方式 —— 断言的是**没有任何一行动过**。
    """
    grid = album_and_photos(qt_app, tmp_path)
    model = grid.model()
    assert model is not None
    before = [entry.path for entry in model.entries()]  # type: ignore[attr-defined]

    for row in (ALBUM_ROW, 1):
        grid.dragMoveEvent(move_at(center_of(grid, row), ours()))
        grid.dropEvent(drop_at(center_of(grid, row), ours()))

    assert [entry.path for entry in model.entries()] == before  # type: ignore[attr-defined]
    grid.deleteLater()


def test_a_foreign_drag_is_refused_at_the_door(qt_app: QApplication, tmp_path: Path) -> None:
    """外面拖进来的文件不接 —— 移动用户文件的触发条件必须是我们自己发起的。"""
    grid = album_and_photos(qt_app, tmp_path)
    enter = enter_at(center_of(grid, ALBUM_ROW), theirs())

    grid.dragEnterEvent(enter)

    assert not enter.isAccepted(), "别处拖进来的文件被当成了我们自己的拖动"
    grid.deleteLater()


def test_our_own_drag_is_accepted(qt_app: QApplication, tmp_path: Path) -> None:
    grid = album_and_photos(qt_app, tmp_path)
    enter = enter_at(center_of(grid, ALBUM_ROW), ours())

    grid.dragEnterEvent(enter)

    assert enter.isAccepted()
    grid.deleteLater()


def test_the_highlight_follows_the_cursor_and_clears_when_it_leaves(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """落点高亮是「这里能不能放」的唯一说明，移开就必须收掉。"""
    grid = album_and_photos(qt_app, tmp_path)
    delegate = grid.itemDelegate()
    assert isinstance(delegate, PhotoDelegate)

    grid.dragMoveEvent(move_at(center_of(grid, ALBUM_ROW), ours()))
    assert delegate._drop_target == ALBUM_ROW

    grid.dragMoveEvent(move_at(center_of(grid, 1), ours()))
    assert delegate._drop_target == -1, "光标已经不在相册上了，落点却还亮着"
    grid.deleteLater()


# ---- 拖动位图 -------------------------------------------------------------


def test_the_drag_image_is_a_whole_card_not_just_a_thumbnail(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """拖动位图画的是整张卡片：缩略图 + 文件名那一行（ADR 0062 决策 3）。"""
    thumb = QPixmap(200, 150)
    thumb.fill()
    card = card_size(DRAG_EDGE)

    image = drag_pixmap(thumb, "IMG_0001.jpg", qt_app.palette())

    assert image.width() >= card.width() and image.height() >= card.height()
    # 只画缩略图时这两处会相等 —— 卡片必须给文件名留出高度。
    assert card.height() > DRAG_EDGE


def test_selecting_several_photos_draws_a_stack(qt_app: QApplication) -> None:
    """多选拖起来要看得见是「好几张」：位图比一张卡片大。"""
    thumb = QPixmap(200, 150)
    thumb.fill()

    one = drag_pixmap(thumb, "a.jpg", qt_app.palette(), count=1)
    many = drag_pixmap(thumb, "a.jpg", qt_app.palette(), count=4)

    assert many.width() > one.width() and many.height() > one.height()


def test_the_grid_can_draw_its_own_drag_image(qt_app: QApplication, tmp_path: Path) -> None:
    """网格自己那张拖动位图要画得出来（缩略图还没到时也要有）。"""
    grid = album_and_photos(qt_app, tmp_path)
    model = grid.model()
    assert model is not None
    selection = grid.selectionModel()
    assert selection is not None
    selection.select(model.index(1, 0), selection.SelectionFlag.Select)
    qt_app.processEvents()

    entries = grid._draggable_entries()
    assert [entry.name for entry in entries] == ["IMG_0000.jpg"]
    assert not grid._drag_image(entries).isNull()
    grid.deleteLater()


def test_a_group_card_drag_image_comes_from_its_cover(qt_app: QApplication, tmp_path: Path) -> None:
    """分组卡片的拖动位图要画**封面那张**。

    它的缩略图存在组员那一条的路径下（``browser/model.py::_cover_of``），按卡片自己的
    路径去问会拿到空的那一份 —— 于是拖起来是一个占位框，而格子里明明有图。
    """
    grid = make_grid(qt_app, tmp_path, count=0)
    model = grid.model()
    assert model is not None
    member = PhotoRef(path=tmp_path / "组员.jpg", size=1024, mtime_ns=1)
    # 分组卡片只能经 ``reorder`` 进模型（``append`` 只收磁盘上的东西）。
    model.reorder([GroupRef(facet="camera", key="canon", label="Canon", photos=(member,))])
    qt_app.processEvents()
    model.set_thumbnail(member.path, _solid_image(64, 48), edge=64)
    grid.select_row(0)
    qt_app.processEvents()

    entries = grid._draggable_entries()
    assert len(entries) == 1, "分组卡片应当可以拖（它的组员就是照片）"
    assert model.cover_pixmap(entries[0]) is not None, "封面那张的缩略图没被找到"
    assert not grid._drag_image(entries).isNull()
    grid.deleteLater()


# ---- 开了拖放之后，框选与扩选还在 -----------------------------------------


def test_rubber_band_selection_still_works_with_dragging_enabled(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """开了拖放之后框选仍然是框选。

    ``PhotoGrid.mouseMoveEvent`` 整段存在意义就是**不让 Qt 在越过拖动阈值时乱来**。
    加了 ``setDragEnabled`` 之后这条最容易被踩坏，而 ``test_grid.py`` 那几条从空白处起拖，
    钉不住「从照片旁边起拖」这一种。
    """
    grid = album_and_photos(qt_app, tmp_path, photos=40)
    grid.selectionModel().clearSelection()

    blank = QPoint(3, grid.viewport().height() - 3)
    QTest.mousePress(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, blank
    )
    for step in range(1, 10):
        QTest.mouseMove(grid.viewport(), QPoint(blank.x() + 40 * step, blank.y() - 20 * step))
        qt_app.processEvents()
    QTest.mouseRelease(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(blank.x() + 360, blank.y() - 180),
    )
    qt_app.processEvents()

    assert len(grid.selectionModel().selectedIndexes()) > 1, "拖过这么远，框选却只选中一格"
    grid.deleteLater()


def test_a_plain_click_still_selects_only_that_cell(qt_app: QApplication, tmp_path: Path) -> None:
    """开了拖放之后，点一下仍然只是选中（没被当成一次拖动的开头）。"""
    grid = album_and_photos(qt_app, tmp_path)
    grid.selectionModel().clearSelection()

    QTest.mouseClick(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        center_of(grid, 1),
    )
    qt_app.processEvents()

    selected = [index.row() for index in grid.selectionModel().selectedIndexes()]
    assert selected == [1]
    grid.deleteLater()


def test_the_hand_drifting_by_a_few_pixels_is_still_a_click(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """手抖不算拖动 —— 那一两个像素不该起一次拖动（``_drag_threshold`` 那一族）。"""
    grid = album_and_photos(qt_app, tmp_path)
    grid.selectionModel().clearSelection()
    point = center_of(grid, 1)

    QTest.mousePress(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    QTest.mouseMove(grid.viewport(), point + QPoint(3, 2))
    QTest.mouseRelease(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point + QPoint(3, 2),
    )
    qt_app.processEvents()

    assert [index.row() for index in grid.selectionModel().selectedIndexes()] == [1]
    assert not grid._rename_edit.is_editing(), "抖了两像素就去改名了"
    grid.deleteLater()


def test_shift_click_still_extends_the_range(qt_app: QApplication, tmp_path: Path) -> None:
    """带 `⇧` 点选仍然是扩选，而不是「起一次拖动」。"""
    grid = album_and_photos(qt_app, tmp_path, photos=40)
    grid.selectionModel().clearSelection()

    anchor = center_of(grid, 1)
    QTest.mouseClick(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, anchor
    )
    qt_app.processEvents()
    last = center_of(grid, 4)
    QTest.mouseClick(
        grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, last
    )
    qt_app.processEvents()

    selected = {index.row() for index in grid.selectionModel().selectedIndexes()}
    assert {1, 2, 3, 4} <= selected, f"`⇧` 点选没有铺开成区间：{sorted(selected)}"
    grid.deleteLater()


def test_dragging_with_a_modifier_extends_instead_of_starting_a_drag(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**带修饰键拖出去仍然是扩选，一个拖动都不起。**

    这是 :meth:`PhotoGrid.mouseMoveEvent` 那一段提前 ``return`` 现在最要紧的用途：
    修饰键在网格里只表示扩选，而开了拖放之后「越过阈值」这件事本身不再意味着拖动 ——
    那一路不拦住，Qt 会顺手把扩选手势变成一次拖动（实测选区从 ``{1, 4}`` 变成
    ``{4, 5, 10, 11}``，而 ``startDrag`` 被调了 1 次）。
    """
    grid = album_and_photos(qt_app, tmp_path, photos=40)
    called: list[object] = []
    original = grid.startDrag
    grid.startDrag = lambda supported: called.append(supported)  # type: ignore[method-assign]
    try:
        grid.selectionModel().clearSelection()
        QTest.mouseClick(
            grid.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            center_of(grid, 1),
        )
        qt_app.processEvents()

        start = center_of(grid, 4)
        QTest.mousePress(
            grid.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start,
        )
        qt_app.processEvents()
        for step in range(1, 12):
            QTest.mouseMove(grid.viewport(), QPoint(start.x() + 25 * step, start.y() + 12 * step))
            qt_app.processEvents()
    finally:
        grid.startDrag = original  # type: ignore[method-assign]

    assert not called, "带 `⌘` 拖出去却起了拖动 —— 修饰键在网格里只表示扩选"
    assert {1, 4} <= {i.row() for i in grid.selectionModel().selectedIndexes()}
    grid.deleteLater()
