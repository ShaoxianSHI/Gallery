"""分区式的排版、绘制与切档过渡（ADR 0089）。

分区式与卡片式共用同一个 ``QListView``，差别只有两处：**关掉固定网格、按条目给尺寸**
（标题的尺寸是整行宽，于是它一定换到新的一行），以及**位置由布局快照算**（标题行夹在中间，
行高有两档，行号算术不成立）。这里锁定的就是这两处：

- 标题独占一行、整行宽，照片从下一行开头继续，一段的末尾不会被下一段补满；
- 量列数要量**一段够长的分区**，不能被「被标题截断的第一行」骗到；
- 标题不可选中、不可激活、不可改名，右键按空白处理；
- 切档过渡的第 0 帧仍是切档前那一屏（含标题），标题只平移、不换尺寸；
- 换回卡片式之后排版方式跟着切回去。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from grid_helpers import (  # noqa: E402
    fake_photos,
    settle_grid,
    step_transition,
    uniform_layout,
)
from PySide6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem  # noqa: E402

from gallery.core.library.groups import build_sections  # noqa: E402
from gallery.core.library.photos import GridEntry, GroupRef  # noqa: E402
from gallery.ui.browser.delegate import (  # noqa: E402
    SECTION_GAP,
    SECTION_HEIGHT,
    PhotoDelegate,
)
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.browser.transition import GridTransition  # noqa: E402
from gallery.ui.thumb_sizes import THUMBNAIL_SIZES  # noqa: E402

#: 每个分区放几张照片。**故意不是列数的整数倍**：一段的末尾本来就该留空。
PER_SECTION = 8


def _grouped_entries(tmp_path: Path, counts: tuple[int, ...] = (PER_SECTION,) * 4) -> list:
    """按给定张数造几段：每段一条标题 + 它的照片（顺序就是组的顺序）。"""
    photos = fake_photos(sum(counts), tmp_path)
    groups: list[GroupRef] = []
    cursor = 0
    for index, count in enumerate(counts):
        members = tuple(photos[cursor : cursor + count])
        cursor += count
        groups.append(
            GroupRef(
                facet="captured_month",
                key=f"2024-{index + 1:02d}",
                label=f"2024 年 {index + 1} 月",
                photos=members,
            )
        )
    return build_sections(groups)


def make_sectioned_grid(
    qt_app: QApplication,
    tmp_path: Path,
    *,
    counts: tuple[int, ...] = (PER_SECTION,) * 4,
    width: int = 1200,
    height: int = 800,
) -> tuple[PhotoGrid, PhotoListModel]:
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    grid.setModel(model)
    model.reorder(_grouped_entries(tmp_path, counts), sectioned=True)
    grid.resize(width, height)
    grid.show()
    qt_app.processEvents()
    grid._sync_layout_mode()
    settle_grid(qt_app, grid)
    assert grid._laid_out_sectioned, "前提没摆出来：这一层没有按分区式重排"
    return grid, model


def _header_rows(model: PhotoListModel) -> list[int]:
    return list(model.header_rows())


def test_a_header_takes_a_whole_row_and_the_photos_start_below_it(
    qt_app: QApplication, tmp_path: Path
) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    layout = grid._layout()
    headers = _header_rows(model)
    assert headers, "前提：这一层有分区"
    header = headers[0]
    first_photo = header + 1

    header_rect = grid.visualRect(model.index(header, 0))
    assert header_rect.width() == grid._section_size.width(), "标题不是整行宽"
    assert header_rect.height() == SECTION_HEIGHT
    assert header_rect.left() == 0
    # 标题后面那一段从新一行的开头接着摆。
    photo_rect = grid.visualRect(model.index(first_photo, 0))
    assert photo_rect.top() == header_rect.bottom() + 1
    assert photo_rect.left() == 0
    # 快照与 Qt 实际排出来的是同一套位置（过渡与锚点都信快照）。
    for row in range(min(model.rowCount(), 30)):
        assert layout.rect(row) == grid.visualRect(model.index(row, 0)), f"第 {row} 行对不上"


def test_a_section_does_not_borrow_the_next_section_s_cells(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """一段照片排不满一行时**留下空格**，下一段从新一行开始 —— 不能接着填。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(3, 9))
    columns = grid._laid_out_columns
    assert columns >= 3, "这个视图宽度下测不出来"
    headers = _header_rows(model)
    assert len(headers) == 2
    second_header = headers[1]

    last_of_first = grid.visualRect(model.index(second_header - 1, 0))
    header_rect = grid.visualRect(model.index(second_header, 0))
    assert header_rect.top() > last_of_first.top(), "标题没换行：它挤在上一段那一行里"
    # 第一段只有 3 张，于是它末尾那一行只摆了 3 格。
    same_row = [
        row
        for row in range(1, second_header)
        if grid.visualRect(model.index(row, 0)).top() == last_of_first.top()
    ]
    assert len(same_row) == 3, f"第一段末尾那一行摆了 {len(same_row)} 格"


def test_the_columns_are_measured_from_a_long_enough_section(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """第一段只有两张时，量第一行会量到 2（被标题截断了）。

    量列数要跳过太短的分段，否则每一格都会按 2 列算宽 —— 排版、过渡与锚点一起错。
    """
    grid, _model = make_sectioned_grid(qt_app, tmp_path, counts=(2, 20))

    assert grid._first_row_columns() == grid._laid_out_columns
    assert grid._laid_out_columns > 2


def test_visible_rows_include_the_header_and_every_photo_on_screen(
    qt_app: QApplication, tmp_path: Path
) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(30, 30))
    layout = grid._layout()
    bar = grid.verticalScrollBar()
    bar.setValue(0)
    qt_app.processEvents()

    rows = set(grid.visible_rows())
    assert 0 in rows, "视口最上面就是标题，它得算进可见行"
    height = grid.viewport().height()
    for row in range(model.rowCount()):
        rect = layout.rect(row)
        top = rect.top() - bar.value()
        inside = top < height and rect.bottom() - bar.value() >= 0
        assert (row in rows) == inside, f"第 {row} 行的可见性判断错了"


def test_a_header_cannot_be_selected_activated_or_renamed(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """标题不是可操作的一格：选中、双击、⌘A、改名都碰不到它。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    header = _header_rows(model)[0]
    index = model.index(header, 0)
    before = grid.current_row()

    grid.select_row(header)
    assert grid.current_row() == before, "标题成了当前项"
    assert grid.genuinely_selected_rows() == [], "标题被选中了"

    activated: list[object] = []
    grid.activated_index.connect(activated.append)
    QTest.mouseDClick(
        grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(index).center()
    )
    qt_app.processEvents()
    assert activated == [], "双击标题发出了「打开」"

    grid.selectAll()
    selected = grid.genuinely_selected_rows()
    assert selected and header not in selected
    assert grid.begin_rename(index) is False
    # 右键标题返回它自己（窗口据此拼分区菜单），但**不动选区**。
    assert grid.select_for_context(header) == header
    assert grid.genuinely_selected_rows() == selected


def test_clicking_a_header_leaves_the_selection_alone(qt_app: QApplication, tmp_path: Path) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    header = _header_rows(model)[0]
    grid.select_row(header + 1)  # 先选一张照片，才看得出「没动」

    QTest.mouseClick(
        grid.viewport(),
        Qt.MouseButton.LeftButton,
        pos=grid.visualRect(model.index(header, 0)).center(),
    )
    qt_app.processEvents()

    assert grid.current_row() == header + 1, "点标题把当前项挪走了"
    assert grid.selected_rows() == [header + 1]


def test_the_delegate_gives_a_header_the_whole_row(qt_app: QApplication, tmp_path: Path) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    delegate = grid.itemDelegate()
    header = model.index(_header_rows(model)[0], 0)
    photo = model.index(_header_rows(model)[0] + 1, 0)

    hint = delegate.sizeHint(QStyleOptionViewItem(), header)
    assert hint == QSize(grid._section_size.width(), SECTION_HEIGHT)
    assert delegate.sizeHint(QStyleOptionViewItem(), photo).width() == grid._cell.width()


def _paint_header(
    delegate: PhotoDelegate,
    model: PhotoListModel,
    row: int,
    size: QSize,
    *,
    palette: QPalette,
) -> QImage:
    pixmap = QPixmap(size)
    pixmap.fill(QColor(255, 255, 255))
    painter = QPainter(pixmap)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(QPoint(0, 0), size)
        option.palette = palette
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()
    return pixmap.toImage()


def test_a_header_draws_a_separator_and_its_label(qt_app: QApplication, tmp_path: Path) -> None:
    """分隔线只在**不是第一行**的标题上方；标签与张数都画在那一行里。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(4, 4))
    delegate = grid.itemDelegate()
    size = QSize(600, SECTION_HEIGHT)
    first, second = _header_rows(model)

    top = _paint_header(delegate, model, first, size, palette=grid.palette())
    assert top.pixelColor(size.width() // 2, SECTION_GAP) == QColor(255, 255, 255), (
        "第一条标题上方不该有分隔线（上面没有东西可分）"
    )
    other = _paint_header(delegate, model, second, size, palette=grid.palette())
    assert other.pixelColor(size.width() // 2, SECTION_GAP) != QColor(255, 255, 255), (
        "第二条标题上方该有一条分隔线"
    )
    # 线与上一组照片之间留了空隙：线上方那几行还是底色。
    for y in range(0, SECTION_GAP):
        assert other.pixelColor(size.width() // 2, y) == QColor(255, 255, 255), (
            f"分隔线上方第 {y} 像素不是空隙"
        )
    # 左边有字（不是纯底色）—— 数一下比底色暗的像素。
    dark = sum(
        1
        for x in range(0, size.width() // 2)
        for y in range(size.height())
        if other.pixelColor(x, y).lightness() < 200
    )
    assert dark > 10, "标题的组名没画出来"


def test_the_header_keeps_its_size_and_only_slides_during_the_transition(
    qt_app: QApplication, tmp_path: Path
) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    header = _header_rows(model)[1]
    before = grid.visualRect(model.index(header, 0))

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition
    assert transition is not None, "这一档没有换行，测的不是这个场景"
    final = grid.visualRect(model.index(header, 0))

    frames = []
    for t in (0.0, 0.5, 1.0):
        rect = transition.frame_rect(final, header, t)
        frames.append(rect)
        assert rect.size() == before.size(), f"第 {t} 帧的标题换了尺寸"
    assert frames[0].top() == before.top(), "第 0 帧不在旧位置"
    assert frames[1].top() not in (frames[0].top(), frames[2].top()), "中间帧不在中间"


def test_the_first_frame_still_shows_the_old_screen_including_headers(
    qt_app: QApplication, tmp_path: Path
) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    assert _header_rows(model), "前提：这一层有分区"
    before = {
        row: grid.visualRect(model.index(row, 0)).topLeft() for row in range(model.rowCount())
    }

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition
    assert transition is not None

    for row, was in before.items():
        painted = grid.visualRect(model.index(row, 0)).topLeft() + transition.offset(row, 0.0)
        assert painted == was, f"第 {row} 行第 0 帧画在了 {painted}，原来在 {was}"


def test_the_last_frame_is_the_final_layout_and_the_header_moved(
    qt_app: QApplication, tmp_path: Path
) -> None:
    grid, model = make_sectioned_grid(qt_app, tmp_path)
    header = _header_rows(model)[-1]
    before = grid.visualRect(model.index(header, 0)).top()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition is not None
    step_transition(grid, 1.0)

    assert grid._transition is None
    after = grid.visualRect(model.index(header, 0)).top()
    assert after != before, "放大一档之后标题没挪，测的不是这个场景"
    for row in range(model.rowCount()):
        assert grid._layout().rect(row) == grid.visualRect(model.index(row, 0))


def test_the_scroll_range_covers_every_section(qt_app: QApplication, tmp_path: Path) -> None:
    """滚动范围要把标题行的高度算进去，否则末尾那几段永远滚不到。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(10,) * 12)
    bar = grid.verticalScrollBar()
    last = grid._layout().rect(model.rowCount() - 1)

    assert bar.maximum() == last.bottom() + 1 - grid.viewport().height()


def test_switching_back_to_cards_restores_the_fixed_grid(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """模型换回卡片式之后，排版方式也要跟着切回去（否则标题没了、格子还是逐条尺寸）。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path)

    entries: list[GridEntry] = [*fake_photos(6, tmp_path)]
    model.reorder(entries)
    qt_app.processEvents()
    grid._sync_layout_mode()
    settle_grid(qt_app, grid)

    assert grid._laid_out_sectioned is False
    assert grid.gridSize() == grid._cell, "卡片式该回到固定网格"
    assert grid._layout().spans[0].headed is False


def test_the_scroll_anchor_is_a_photo_not_the_top_of_the_level(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """在分区中间切档：视口正中那一格要留在原处，随后摆到正中（它才是用户在看的那一格）。"""
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(40,) * 6)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    qt_app.processEvents()
    middle = QPoint(grid.viewport().width() // 2, grid.viewport().height() // 2)
    anchor = grid.indexAt(middle)
    assert anchor.isValid()
    anchor_y = grid.visualRect(anchor).center().y()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition is not None, "前提：这一档换了行"
    first = grid.visualRect(model.index(anchor.row(), 0))
    assert abs(first.center().y() - anchor_y) <= 1, "第 0 帧里锚点挪位了"

    step_transition(grid, 1.0)
    rect = grid.visualRect(model.index(anchor.row(), 0))
    assert rect.isValid(), "锚点那一行被甩出了视口"
    assert abs(rect.center().y() - grid.viewport().height() // 2) <= 2


def test_a_clamped_start_still_shows_the_old_screen_in_the_first_frame(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """缩小一档后新范围比当前滚动位置短时，起点会被夹到末尾。

    **视口正中落在分区最后一行没铺满的空处**时没有锚点可摆回去，这一夹就没人补 ——
    第 0 帧画的是往上跳了一大截的另一屏。在末段看「这一组最后几张」正是这个样子。
    """
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(8,) * 60)
    layout = grid._layout()
    headers = _header_rows(model)
    last = headers[-2] - 1  # 倒数第二段的最后一张，它那一行只摆了两格
    bar = grid.verticalScrollBar()
    bar.setValue(
        layout.rect(last).top() + layout.rect(last).height() // 2 - grid.viewport().height() // 2
    )
    settle_grid(qt_app, grid)
    grid.select_row(last, ensure_visible=False)
    settle_grid(qt_app, grid)

    middle = QPoint(grid.viewport().width() // 2, grid.viewport().height() // 2)
    assert not grid.indexAt(middle).isValid(), "前提：视口正中落在分区末尾那一行的空处"
    start = bar.value()
    before = {
        row: grid.visualRect(model.index(row, 0)).top()
        for row in range(model.rowCount())
        if grid.visualRect(model.index(row, 0)).isValid()
    }
    before = {row: top for row, top in before.items() if 0 <= top < grid.viewport().height()}

    grid.set_target_edge(THUMBNAIL_SIZES[0])
    transition = grid._transition
    assert transition is not None, "前提：这一档换了行，走的是过渡"
    assert start > bar.maximum(), "前提：起点超出新范围，会被夹住"
    assert transition.scroll_shift != 0, "这一夹没被补进过渡"

    for row, was in before.items():
        painted = grid.visualRect(model.index(row, 0)).top() + transition.offset(row, 0.0).y()
        assert painted == was, f"第 {row} 行第 0 帧画在了 {painted}，原来在 {was}"


def test_a_transition_without_sections_still_reads_the_old_fields() -> None:
    """``old_columns`` / ``new_cell`` 这些读法是派生属性 —— 既有测试与锚点算术都靠它们。"""
    transition = GridTransition(
        uniform_layout(3, QSize(200, 220), 10),
        uniform_layout(5, QSize(160, 180), 10),
    )

    assert (transition.old_columns, transition.new_columns) == (3, 5)
    assert transition.old_cell == QSize(200, 220)
    assert transition.new_cell == QSize(160, 180)
    assert transition.reflows


def test_every_section_being_short_still_gets_the_zoom_animation(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """按天分组时每一段都可能只有一两张 —— 量不出列数，但切档照样要动。

    这条是「量列数」那个改动的反面：量不出来时若把列数当成 0，整段过渡会被判成
    「没有旧布局」而直接落位，用户看到的就是一下跳变。
    """
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(2,) * 8)
    assert grid._first_row_columns() == 0, "前提：每一段都短于一行，量不出列数"

    before = {
        row: grid.visualRect(model.index(row, 0)).topLeft() for row in range(model.rowCount())
    }
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition

    assert transition is not None, "量不出列数时过渡整段失效了"
    for row, was in before.items():
        painted = grid.visualRect(model.index(row, 0)).topLeft() + transition.offset(row, 0.0)
        assert painted == was, f"第 {row} 行第 0 帧画在了 {painted}，原来在 {was}"


def test_a_short_section_still_centers_the_selection_when_zooming(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """按天分组（每段两张）时切到大档：选中那张仍要摆到正中。

    列数如果去数「一段的第一行有几格」，数到的是这一段有几张（2），而实际一行能摆三格。
    过渡与锚点按 2 列算，选中项就会被滚到屏幕外面 —— 列数只能由视口宽与格子宽算。
    """
    grid, model = make_sectioned_grid(qt_app, tmp_path, counts=(2,) * 80, width=1000)
    row = 121  # 中段某一段的第一张照片（每段 = 标题 + 两张）
    assert not model.is_header(row)
    grid.select_row(row)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(0)
    qt_app.processEvents()
    assert grid.visualRect(model.index(row, 0)).top() > grid.viewport().height(), (
        "前提没摆出来：选中项该在屏外"
    )

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    rect = grid.visualRect(model.index(row, 0))
    assert rect.top() >= 0 and rect.bottom() < grid.viewport().height(), (
        f"选中项被滚到了屏幕外：{rect}"
    )
    middle = grid.viewport().height() // 2
    assert abs(rect.center().y() - middle) <= 2, (
        f"选中项停在了 y={rect.center().y()}，视口正中是 {middle}"
    )
