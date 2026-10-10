"""切档过渡的回归测试：量的是它有没有真的走过中间值，只画首尾两端的实现也会全绿。

几何一步到位（跟着过渡走就是每帧一次全量重排），走过渡的只有绘制尺寸与格子位置；
``icon_edge_changed`` 只发最终边长，否则几十个中间尺寸会各要一遍缩略图。
不靠真实时钟：``settle_grid`` 等条件，``drive_transition`` 直接拨动画时钟。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import (  # noqa: E402
    QT_DEFERRED_RELAYOUT_SECONDS,
    drive_transition,
    fake_photos,
    largest_delta_that_keeps_columns,
    make_grid,
    settle_grid,
    step_transition,
)
from PySide6.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QCoreApplication,
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QNativeGestureEvent,
    QPainter,
    QPalette,
    QPixmap,
    QPointingDevice,
    QWheelEvent,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.browser.delegate import GRID_PADDING_X, PhotoDelegate  # noqa: E402
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.browser.transition import (  # noqa: E402
    EDGE_ANIMATION_CURVE,
    EDGE_ANIMATION_MIN_DELTA,
    EDGE_ANIMATION_MS,
    GridTransition,
)
from gallery.ui.thumb_sizes import (  # noqa: E402
    MIN_PINCH_INTERVAL_MS,
    PINCH_THRESHOLD,
    THUMBNAIL_SIZES,
)

#: 转一小会儿事件循环的时长。只用在「必须等满，否则测不到」的地方，其余一律等条件。
SLICE_SECONDS = 0.02


def test_the_thumbnail_grows_through_the_intermediate_sizes(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """切档之后，绘制尺寸确实**路过**中间值，而不是直接跳到终点。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    start = grid._delegate.draw_edge

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    samples = drive_transition(grid, lambda: grid._delegate.draw_edge)

    assert min(samples) == start, "第一帧必须还是旧尺寸"
    assert samples[-1] == float(grid.icon_edge), "最后一帧必须正好落在新尺寸上"
    assert len(set(samples)) > 3, f"只取到 {sorted(set(samples))}，这是一次跳变不是动画"
    assert min(samples) < max(samples), "尺寸得是单调长过去的"


def test_the_easing_starts_and_ends_gently(qt_app: QApplication, tmp_path: Path) -> None:
    """缓动曲线两头都缓：``OutCubic`` 的第一帧（约时间轴 10%）已经走完 27% 的路程，
    位置那一段总位移大，画面上就是一跳，尾部又几乎钉着不动。
    """
    curve = QEasingCurve(EDGE_ANIMATION_CURVE)
    assert curve.valueForProgress(0.1) <= 0.05, (
        f"时间轴走到 10% 就已经走完 {curve.valueForProgress(0.1):.1%} 的路程，第一帧就是一跳"
    )
    assert curve.valueForProgress(0.9) >= 0.95, (
        f"时间轴走到 90% 才走完 {curve.valueForProgress(0.9):.1%}，尾巴太长"
    )

    # 曲线是构造时就挂上去的，等它跑起来等不出更多东西来。
    grid = make_grid(qt_app, tmp_path, 40)
    assert grid._transition_anim.easingCurve().type() == EDGE_ANIMATION_CURVE, (
        "网格没有用上面这条曲线"
    )


def test_the_geometry_snaps_while_only_the_painting_moves(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """切档的那一刻几何就已经到位，动画期间它一步都不许再动。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    before_cell = grid.gridSize()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settled_edge = grid.icon_edge

    assert grid.gridSize() != before_cell, "格子总得真的变大"
    assert grid.gridSize().width() - GRID_PADDING_X == settled_edge
    assert grid._delegate.draw_edge != settled_edge, "绘制尺寸这时应该还在旧尺寸上"

    # 动画跑到一半再量一次：几何必须纹丝不动。
    step_transition(grid, 0.5)
    assert grid.icon_edge == settled_edge
    assert grid.gridSize().width() - GRID_PADDING_X == settled_edge


def test_the_animation_never_reaches_the_thumbnail_pipeline(
    qt_app: QApplication, tmp_path: Path, wait_for: Callable[..., bool]
) -> None:
    """``icon_edge_changed`` 只发最终边长：动画值顺着发出去会让后台按几十个尺寸各解码一遍。"""
    grid = make_grid(qt_app, tmp_path, 40)
    emitted: list[int] = []
    grid.icon_edge_changed.connect(emitted.append)
    # 建网格时那一次通知（120ms 的 ``_edge_timer``）要先等它发过并清空，否则会发两次。
    assert wait_for(lambda: bool(emitted), timeout=2.0), "建网格时那一次通知没来，前提没摆出来"
    emitted.clear()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle_grid(qt_app, grid)
    # 通知与动画并行地发（动画 160ms、通知 120ms），这里等它真的来了。
    assert wait_for(lambda: bool(emitted), timeout=2.0), "最终边长的通知压根没发出来"

    assert emitted == [grid.icon_edge], f"只该发一次最终边长，实际发了 {emitted}"
    assert all(isinstance(value, int) for value in emitted)


def test_resizing_the_window_does_not_animate(qt_app: QApplication, tmp_path: Path) -> None:
    """拖窗口是连续事件，不能每变一次宽度就放一次动画。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)

    # ``resizeEvent`` 同步送达（``QWidget::resize`` 直接发，不排队），转事件循环反而掩盖它。
    grid.resize(900, 800)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge), "窗口一变就该直接落位"


def test_a_change_too_small_to_see_does_not_animate(qt_app: QApplication, tmp_path: Path) -> None:
    """差几个像素肉眼看不见，却要付一整轮全视口重绘 —— 直接落位。

    增量由 :func:`~grid_helpers.largest_delta_that_keeps_columns` 找，不硬编码：换列
    本身就**该**过渡（那是另一前置条件），硬编码一个数会让这条用例在滚动条一改宽度、
    格子跨过换列边界时测的变成「换列该不该过渡」—— 那它一直是绿的，只是答错了题。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    cell = grid.gridSize()
    delta = largest_delta_that_keeps_columns(qt_app, grid)

    grid._set_cell(QSize(cell.width() + delta, cell.height() + 3), animate=True)

    assert delta < EDGE_ANIMATION_MIN_DELTA, "前提：这个增量本来就该看不见"
    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge)


def test_two_steps_in_a_row_do_not_jump_to_the_discarded_size(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """连点两档时不接第二段过渡：上一段没跑完时它的「旧布局」已经不是这一段的旧布局了。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    before = grid._delegate.draw_edge

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    grid.set_target_edge(THUMBNAIL_SIZES[0])

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge), "该直接落在最终那一档"
    assert grid.icon_edge < before, "而且该是用户最后要的那一档"


def test_the_delegate_draws_at_the_animated_size_but_keeps_its_geometry(
    qt_app: QApplication,
) -> None:
    """绘制尺寸与几何尺寸确实是两份 —— 这条方案就建立在这件事上。

    直接量 ``target_rect``（绘制真的落在这里）而不是复刻一遍算法。
    """
    delegate = PhotoDelegate(icon_edge=176)
    pixmap = QPixmap(288, 288)
    hint = delegate.sizeHint(None, None)  # type: ignore[arg-type]
    rect = QRect(QPoint(0, 0), hint)

    assert delegate.target_rect(rect, pixmap).width() == 176

    delegate.set_draw_edge(232.0)
    assert delegate.target_rect(rect, pixmap).width() == 232
    assert delegate.sizeHint(None, None) == hint  # type: ignore[arg-type]


def scroll_samples(grid: PhotoGrid) -> list[int]:
    """跑完一次过渡，把期间纵向滚动位置的取值按顺序记下来。"""
    return drive_transition(grid, lambda: grid.verticalScrollBar().value())


def scrollable_grid(qt_app: QApplication, tmp_path: Path, row: int = 20) -> PhotoGrid:
    """造一个「当前项就在屏内、但快到下沿」的网格。

    当前项必须落在 Batched 布局已排版的那一批里（默认一批 64 条）：超出批次时
    ``visualRect`` 返回空矩形，``scrollTo`` 静默什么都不做。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(row, ensure_visible=False)
    return grid


def test_the_grid_scrolls_into_place_instead_of_jumping(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """纵向滚动也是一段过渡 —— 它跳的是整屏高度，比缩略图那一下明显得多。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    samples = scroll_samples(grid)

    assert len(set(samples)) > 3, f"只取到 {sorted(set(samples))}，这是一次跳变不是过渡"
    assert samples == sorted(samples), f"回退了一下：{samples}"

    # 终点看**结果**（当前项在视口正中间）而不是 ``scrollTo``，那正是被换掉的东西；
    # 容 1 像素：算中线要除两次格高与视口高，整数除法落在哪一头就差这一像素。
    cell_rect = grid.visualRect(grid.model().index(grid.current_row(), 0))
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1


def test_a_scroll_start_beyond_the_new_range_is_clamped(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """交给动画的滚动起点必须落在滚动条自己的范围里：不夹的话插值中途会被 ``maximum()``
    削平（动画先平一段再动）。这个毛病从取样里看不出来，只能直接查存下来的那个起点。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)
    grid.set_target_edge(THUMBNAIL_SIZES[2])  # 过渡正在跑
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running

    bar = grid.verticalScrollBar()
    grid._start_transition(grid._delegate.draw_edge, bar.maximum() + 5000, animate=True)

    assert grid._scroll_from <= bar.maximum()


def test_a_scroll_of_your_own_cancels_the_transition(qt_app: QApplication, tmp_path: Path) -> None:
    """过渡跑着时用户自己滚了：动画要立刻让位并停在滚到的地方，别在收尾时拽回去。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running

    bar = grid.verticalScrollBar()
    chosen = bar.value() - 40
    # ``setValue`` 同步走进 ``scrollContentsBy`` → ``_cancel_transition``，不用转事件循环。
    bar.setValue(chosen)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert bar.value() == chosen, "用户滚到哪儿就得留在哪儿"


def test_resizing_does_not_scroll_the_grid_around(qt_app: QApplication, tmp_path: Path) -> None:
    """拖窗口是连续事件，不能每变一次宽度就放一次滚动过渡。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.resize(900, 800)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped


def test_a_reflow_moves_the_cells_and_their_offsets_differ_per_row(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """换行重排时每一格的位移不同，插值得按行算：6 列 → 4 列下同一行里的格子横向与纵向都不同。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition
    assert transition is not None
    assert transition.reflows, "6 列变 4 列就是重排"

    offsets = {row: transition.offset(row, 0.0) for row in range(0, 24)}
    assert len(set(offsets.values())) > 1, "整屏平移一个数那是错的，位移逐行都不同"
    assert offsets[1] != offsets[5], "同一行里的两格横向位移也不一样"


def test_the_first_frame_lands_exactly_where_the_cells_used_to_be(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """第 0 帧必须**正好**是切换前的位置：两端的列数是量出来的而非算出来的，逐行比对，
    量错导致的整体漂移（漂 1 像素也算）也照样红。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)
    model = grid.model()
    assert model is not None
    before = [grid.visualRect(model.index(row, 0)).topLeft() for row in range(24)]

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition
    assert transition is not None

    for row, was in enumerate(before):
        painted = grid.visualRect(model.index(row, 0)).topLeft() + transition.offset(row, 0.0)
        assert painted == was, f"第 {row} 格第 0 帧画在了 {painted}，原来在 {was}"


def test_the_last_frame_is_the_final_layout_and_then_nothing(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """跑完之后画面与逻辑几何完全重合，过渡状态清干净。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    assert grid._transition is None
    assert grid._delegate.cell_offset(0) == QPoint()


def test_a_press_during_the_transition_makes_the_picture_agree_again(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """过渡中按一下，画面必须立刻落位，否则那一按选中的是「画得好看的那一格」之外的另一格。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1 / 3)  # 停进行中中，按钮按的是「正在飞」的那一帧
    assert grid._transition is not None

    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(20, 20))

    assert grid._transition is None
    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge)


def test_new_rows_arriving_during_the_transition_end_it(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """插进来的新行没有「旧位置」可言，过渡必须作废。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition is not None

    grid.model().append([PhotoRef(path=tmp_path / "NEW.jpg", size=1024, mtime_ns=99)])

    assert grid._transition is None


#: 渲染用的那张卡片的缩略图颜色。
COVER = QColor(200, 40, 40)

#: 卡片自身相对格子的内缩（``PhotoDelegate.paint`` 里的 ``adjusted(3, 3, -3, -3)``）。
CARD_INSET = 3


def _painted_top(
    model: PhotoListModel,
    row: int,
    cell: QSize,
    transition: GridTransition,
    t: float,
    canvas: QSize,
) -> int:
    """把第 ``row`` 行按过渡的第 ``t`` 帧画到全白画布上，返回照片顶边所在的行。

    直接走 :meth:`PhotoDelegate.paint` 量「真的画在哪儿」：只查 ``cell_offset`` 的话，
    「绘制压根没用这个偏移」这种改动不会有测试变红。绘制尺寸也要跟着 ``t`` 拨。
    """
    delegate = PhotoDelegate(icon_edge=cell.width() - GRID_PADDING_X)
    old_edge = transition.old_cell.width() - GRID_PADDING_X
    new_edge = cell.width() - GRID_PADDING_X
    delegate.set_draw_edge(old_edge + (new_edge - old_edge) * t)
    delegate.set_transition(transition, t)
    painted = QPixmap(canvas)
    painted.fill(QColor(255, 255, 255))
    painter = QPainter(painted)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, cell.width(), cell.height())
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()

    image = painted.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y) == COVER:
                return y
    return -1


def _painted_card_rect(
    model: PhotoListModel,
    row: int,
    cell: QSize,
    transition: GridTransition,
    t: float,
    canvas: QSize,
) -> QRect:
    """把选中状态的第 ``row`` 行画到画布上，返回**卡片自己**占的那块矩形。

    量的是选中描边（2 像素的高亮色）的包围盒：它是卡片矩形本身画出来的东西，
    量照片（居中）量不出卡片有没有跟着一起长。
    """
    delegate = PhotoDelegate(icon_edge=cell.width() - GRID_PADDING_X)
    old_edge = transition.old_cell.width() - GRID_PADDING_X
    new_edge = cell.width() - GRID_PADDING_X
    delegate.set_draw_edge(old_edge + (new_edge - old_edge) * t)
    delegate.set_transition(transition, t)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, cell.width(), cell.height())
    option.palette = QApplication.palette()
    option.state = QStyle.StateFlag.State_Selected

    painted = QPixmap(canvas)
    painted.fill(QColor(255, 255, 255))
    painter = QPainter(painted)
    try:
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()

    stroke = QApplication.palette().color(QPalette.ColorRole.Highlight)
    image = painted.toImage()
    hits = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y) == stroke
    ]
    assert hits, "没找到选中描边，测试没搭对"
    xs = [x for x, _ in hits]
    ys = [y for _, y in hits]
    bounds = QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    # 描边宽 2 像素、骑在矩形边界上（内外各占一像素），所以量到的包围盒比卡片大一圈。
    return bounds.adjusted(1, 1, -1, -1)


def test_the_card_really_is_drawn_along_the_way(tmp_path: Path, qt_app: QApplication) -> None:
    """整张卡片跟着插值走，不是只有缩略图在动、边框在原地等着。"""
    del qt_app  # QPixmap 要有一个 QGuiApplication 才活得下来
    photos = [PhotoRef(path=tmp_path / f"P{i}.jpg", size=10, mtime_ns=i) for i in range(8)]
    model = PhotoListModel()
    model.append(photos)
    cover = QImage(64, 64, QImage.Format.Format_RGB32)
    cover.fill(COVER)
    for photo in photos:
        model.set_thumbnail(photo.path, cover)

    # 2 列 → 4 列：第 4 格从第 2 行挪到第 1 行，纵向正好差一个旧行高，横向还在画布里。
    transition = GridTransition(2, QSize(300, 320), 4, QSize(180, 200))
    cell, canvas, row = QSize(180, 200), QSize(400, 800), 4

    start = _painted_top(model, row, cell, transition, 0.0, canvas)
    middle = _painted_top(model, row, cell, transition, 0.5, canvas)
    end = _painted_top(model, row, cell, transition, 1.0, canvas)

    assert end != -1, "没画出来，测试没搭对"
    # 量的是**差值**：卡片自身有内边距，画在哪儿不等于格子左上角在哪儿。
    assert start - end == 440, f"起点该在旧位置上（差一整个旧行高），实际差 {start - end}"
    assert middle - end == 220, f"中间帧没在中间：{middle - end}"


def test_the_card_itself_grows_with_the_photo(tmp_path: Path, qt_app: QApplication) -> None:
    """整张卡片（不是只有照片）在切档途中**长大**：第 0 帧必须正好是切换前那一格的大小，
    否则画面上就是格子已经站好、只有照片在框里慢慢长。
    """
    del qt_app
    photos = [PhotoRef(path=tmp_path / f"P{i}.jpg", size=10, mtime_ns=i) for i in range(8)]
    model = PhotoListModel()
    model.append(photos)

    # 2 列 → 4 列、格子同时变大：位置与尺寸两头都在动。
    old_cell, new_cell = QSize(300, 320), QSize(180, 200)
    transition = GridTransition(2, old_cell, 4, new_cell)
    canvas, row = QSize(760, 1000), 4

    start = _painted_card_rect(model, row, new_cell, transition, 0.0, canvas)
    middle = _painted_card_rect(model, row, new_cell, transition, 0.5, canvas)
    end = _painted_card_rect(model, row, new_cell, transition, 1.0, canvas)

    assert end.size() == QSize(
        new_cell.width() - 2 * CARD_INSET, new_cell.height() - 2 * CARD_INSET
    ), f"最后一帧该是新的格子大小，量到 {end.size()}"
    assert start.size() == QSize(
        old_cell.width() - 2 * CARD_INSET, old_cell.height() - 2 * CARD_INSET
    ), f"第 0 帧该正好是切换前那一格的大小，量到 {start.size()}"
    # 中间帧真的在中间（留 2 像素给取整）。
    half = (start.width() + end.width()) / 2
    assert abs(middle.width() - half) <= 2, f"中间帧宽 {middle.width()}，两头的中点是 {half}"
    assert start.top() - end.top() == 440, f"第 0 帧不在旧位置上：差 {start.top() - end.top()}"


def test_the_transition_uses_the_measured_columns_not_the_intended_ones(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两端必须用**量出来**的列数：``_columns_for`` 给的是打算摆几列，Qt 实际可能少摆一列，
    用错会让每格整体漂一整列的位移。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, grid)  # 等列数核对（COLUMN_CHECK_MS）跑完，稳定下来之后才量
    measured = grid._first_row_columns()
    assert measured == grid._laid_out_columns, "这个场景本来就该两者一致，否则测的不是它"

    real = grid._first_row_columns
    calls: list[int] = []

    def faked() -> int:
        value = real()
        calls.append(value)
        return value - 1 if len(calls) == 1 else value

    monkeypatch.setattr(grid, "_first_row_columns", faked)

    grid.set_target_edge(THUMBNAIL_SIZES[2])

    transition = grid._transition
    assert transition is not None
    assert len(calls) == 2, f"该只量两次（改布局的前/后），实际量了 {len(calls)} 次"
    assert transition.old_columns == calls[0] - 1, "旧端该用第一次量到的那个值"
    assert transition.new_columns == calls[1], "新端该用第二次量到的那个值"


def test_a_selection_far_down_still_gets_centered(qt_app: QApplication, tmp_path: Path) -> None:
    """选中项在第 80 行（远超 Batched 排版的那 64 条）也要摆到正中：
    ``scrollTo`` 对超出批次的行静默什么都不做，用户选中的那一行会被留在屏幕外。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(80, ensure_visible=False)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    assert grid.current_row() == 80, "选中项本身不该被动过"
    cell_rect = grid.visualRect(grid.model().index(80, 0))
    assert cell_rect.isValid(), "选中项得在视口里"
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1


def test_a_selection_on_the_last_row_is_centered_not_pinned_to_the_end(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """内容比视口短时「摆到正中」算出负数，必须夹住：越界目标会让末几帧被 Qt 压平，
    看着像卡住。滚动条自己也会夹，所以只能查目标值本身。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(0, ensure_visible=False)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    bar = grid.verticalScrollBar()
    assert grid._scroll_to == bar.minimum(), f"目标值 {grid._scroll_to} 越界了"
    assert bar.value() == bar.minimum()
    assert grid.visualRect(grid.model().index(0, 0)).top() >= 0, "第一行该完整可见"


def test_a_selection_on_the_last_row_is_not_pushed_off_screen(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """末行选不中时，夹到下界就好 —— 别把它硬按到屏幕外面。"""
    grid = make_grid(qt_app, tmp_path, 6)
    grid.select_row(5, ensure_visible=False)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    bar = grid.verticalScrollBar()
    assert grid._scroll_to <= bar.maximum(), "目标值越界了"
    assert grid.visualRect(grid.model().index(5, 0)).bottom() <= grid.viewport().height()


def test_the_layout_is_exactly_the_same_whether_or_not_it_animates(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**动画只改「画在哪儿」，不改变任何布局结果。** 这是整件事的地基：几何、列数与
    每格的逻辑位置在切换那一刻就是终态，动画只是 ``paint`` 里多出来的一个偏移。
    """
    animated = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, animated)
    animated.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(animated, 1.0)

    plain = scrollable_grid(qt_app, tmp_path)
    settle_grid(qt_app, plain)
    plain._transition_anim.setDuration(0)  # 0 毫秒 = 立即落位，等于没有过渡
    plain.set_target_edge(THUMBNAIL_SIZES[2])
    # 时长为 0 的动画得等它自己那一趟 0ms 定时器，这里只能等条件 ——
    # 拨时钟是按 160ms 拨的，而那个动画的时长是 0。
    settle_grid(qt_app, plain)

    assert animated.gridSize() == plain.gridSize()
    assert animated._first_row_columns() == plain._first_row_columns()
    assert animated.icon_edge == plain.icon_edge
    assert animated.verticalScrollBar().value() == plain.verticalScrollBar().value()


def test_the_scroll_glides_from_where_it_was_to_where_the_selection_is(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """选中项在屏外时放大：从「此刻看着的那一片」一路滑到选中项，中间不许倒退。

    起点得是「看着的那一片」而不是某个像素值 —— 格子高矮与每行张数都变了，同一个数在
    新布局里指的是另一批照片。Batched 布局的滚动条范围要 12–131ms（200–3000 张）才涨到位，
    所以范围得靠 ``_layout_everything`` 同步算对，不能指望重试几轮轮过去。
    """
    grid = make_grid(qt_app, tmp_path, 800)
    settle_grid(qt_app, grid)
    grid.select_row(795, ensure_visible=False)  # 选中它（Qt 会顺手把它滚进视野）
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)  # 自己滚回中段：选中项于是落到屏外
    settle_grid(qt_app, grid)
    selected = grid.visualRect(grid.model().index(795, 0))
    assert selected.top() > grid.viewport().height(), "选中项该在屏外，测的不是这个场景"

    middle = QPoint(grid.viewport().width() // 2, grid.viewport().height() // 2)
    anchor = grid.indexAt(middle)
    assert anchor.isValid() and anchor.row() < 700, "视图该还在相册中段"
    anchor_y = grid.visualRect(anchor).center().y()

    grid.set_target_edge(THUMBNAIL_SIZES[2])

    first_rect = grid.visualRect(anchor)
    assert abs(first_rect.center().y() - anchor_y) <= 1, (
        f"第 0 帧该还停在原处（y={anchor_y}），实际 y={first_rect.center().y()}"
    )

    samples = scroll_samples(grid)
    pairs = list(zip(samples, samples[1:], strict=False))
    backwards = [(a, b) for a, b in pairs if b < a]
    assert not backwards, f"滚动倒退了：{backwards}"
    assert len(set(samples)) > 5, f"只取到 {sorted(set(samples))}，这是一次跳变不是过渡"

    # 终点必须是「选中项在正中」，而不是「够得着的最远处」。
    cell_rect = grid.visualRect(grid.model().index(795, 0))
    assert abs(cell_rect.center().y() - grid.viewport().height() // 2) <= 1


def test_the_scrollbar_range_is_already_right_at_the_moment_of_the_change(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """切档刚做完，滚动条范围就该覆盖全部条目：范围不对时「摆到正中」只能算到那一小块以内，
    选中项会被摆到「够得着的最远处」（3000 张时差 23 万多像素）。
    """
    grid = make_grid(qt_app, tmp_path, 800)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])

    bar = grid.verticalScrollBar()
    rows = -(-800 // grid._first_row_columns())
    expected = rows * grid.gridSize().height() - grid.viewport().height()
    assert bar.maximum() == expected, f"范围 {bar.maximum()}，正确应为 {expected}"


def test_two_quick_level_changes_still_end_centered_on_the_selection(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """连按两下换档，终点仍是选中项在正中：第二段不接而直接落位，落到的正是
    ``_center_on_row`` 刚摆好的第二档居中位置。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(80, ensure_visible=False)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    grid.set_target_edge(THUMBNAIL_SIZES[0])  # 第二段根本不会跑起来，直接落位
    settle_grid(qt_app, grid)

    cell_rect = grid.visualRect(grid.model().index(80, 0))
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1, f"选中项在 y={cell_rect.center().y()}"


def test_resizing_the_window_never_recenters(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拖窗口（以及横幅出现/消失带来的自动 resize）不该把视图拽到选中项那里。

    踩过的坑：Qt 在视口大小变化时会顺手把当前项设成第 0 行，居中一旦挂在几何变化上
    无条件跑，每 resize 一次就回一次顶部 —— 而横幅是会自己弹的。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    grid.select_row(300, ensure_visible=False)
    settle_grid(qt_app, grid)

    calls: list[int] = []
    real = grid._center_on_row

    def counted(row: int, columns: int) -> None:
        calls.append(row)
        real(row, columns)

    monkeypatch.setattr(grid, "_center_on_row", counted)

    grid.resize(1100, 800)
    settle_grid(qt_app, grid)
    grid.resize(1300, 800)
    settle_grid(qt_app, grid)

    assert calls == [], f"拖窗口不该居中，却调了 {calls} 次"


def test_dragging_the_window_never_lets_the_scroll_position_go(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """拖窗口时滚动条不许跳 —— **哪怕列数根本没变**，而且每一帧都不许跳。

    每帧格子宽度都变一点点，Batched 布局就按「已排版的那一批」重算范围、把当前位置夹到
    那一批末尾（400 张能从 5930 掉到 1290）。夹取与恢复
    发生在同一帧内的先后两步，只在 ``resize`` 之后量一次量不到那一段；末尾还得等过
    ``QT_DEFERRED_RELAYOUT_SECONDS`` —— 那是 Qt 内部的定时器，``settle_grid`` 等不到它。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    settle_grid(qt_app, grid)
    start = bar.value()
    columns = grid._first_row_columns()
    assert columns >= 2, "列数太少，测的不是这个场景"

    def full_range() -> int:
        """此刻这个几何下滚动条范围该有的样子（全量排版之后 Qt 算的就是它）。"""
        rows = -(-grid.model().rowCount() // columns)
        return rows * grid.gridSize().height() - grid.viewport().height()

    seen: list[int] = []
    ranges: list[tuple[int, int]] = []  # (此刻的 maximum, 该有的 maximum)
    # 相邻两档里有格子尺寸没变的那些：旧实现里那几帧连几何都不改，位置就是被那次延迟重排夹走的。
    for offset in (8, 4, 0, -4, -8, 0, 8, 4, 0, -4):
        grid.resize(1200 + offset, 800)
        for _ in range(4):  # 拖动的每一轮事件循环
            qt_app.processEvents()
            seen.append(bar.value())
            ranges.append((bar.maximum(), full_range()))
        settle(SLICE_SECONDS)
    settle(QT_DEFERRED_RELAYOUT_SECONDS)  # 连 Qt 那个延迟重排也跑掉
    seen.append(bar.value())
    ranges.append((bar.maximum(), full_range()))

    assert grid._first_row_columns() == columns, "这个用例要的就是列数不变"
    drift = max(abs(value - start) for value in seen)
    assert drift == 0, f"滚动位置在拖动过程中动了 {drift} 像素：{sorted(set(seen))}"
    collapsed = sorted({actual for actual, expected in ranges if actual != expected})
    assert not collapsed, (
        f"滚动条范围缩到了 {collapsed}（该有的大小见 {sorted({e for _, e in ranges})}）"
        "—— 范围只覆盖了已排版的那一批，位置就是被它夹掉的"
    )


def test_new_photos_arriving_do_not_lose_the_scroll_position(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """扫描 / 监听加条目时滚动位置同样不许被夹走：加条目会让 Qt 从第 0 条重排一次，
    范围在排完第一批那一刻只剩一小块（400 张的库里 6970 → 1620）。
    走的是 ``rowsInserted``，与拖窗口那条不是同一个入口。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    settle_grid(qt_app, grid)
    start = bar.value()
    columns = grid._first_row_columns()
    assert start > 0 and columns >= 2, "没滚下去 / 列数太少，测的不是这个场景"

    for step in range(3):
        grid.model().append(fake_photos(200, tmp_path / f"新来的{step}"))
        settle(QT_DEFERRED_RELAYOUT_SECONDS)
        assert bar.value() == start, (
            f"第 {step} 批新条目进来之后位置从 {start} 变成了 {bar.value()}"
        )

    rows = -(-grid.model().rowCount() // columns)
    expected = rows * grid.gridSize().height() - grid.viewport().height()
    assert bar.maximum() == expected, f"范围 {bar.maximum()}，正确应为 {expected}"


def test_changing_the_thumbnail_size_keeps_the_cell_the_user_is_looking_at(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """换档时摆在正中的是**用户正看着的那一格**：以前取 ``currentIndex``，而 Qt 在重排之后会
    顺手把当前项设成第 0 行，于是在相册中间放大一档整屏就弹回顶部。只比纵向 ——
    列数一换，那一格从一行里的第 3 个挪到第 1 个，横向没有可比性。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    grid.clear_current()  # 显式摆出前置条件：谁也没选中（不留 Qt 顺手设的当前项）
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    settle_grid(qt_app, grid)

    middle = QPoint(grid.viewport().width() // 2, grid.viewport().height() // 2)
    anchor = grid.indexAt(middle).row()
    assert anchor > 0, f"没滚到相册中间（正中是第 {anchor} 格），测的不是这个场景"
    before_columns = grid._first_row_columns()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1.0)

    after_columns = grid._first_row_columns()
    assert after_columns != before_columns, "这一档没换行，测的不是这个场景"
    rect = grid.visualRect(grid.model().index(anchor, 0))
    assert rect.isValid(), f"第 {anchor} 格被甩出视口了"
    middle_y = grid.viewport().height() // 2
    assert abs(rect.center().y() - middle_y) <= 1, (
        f"用户正看着的第 {anchor} 格停在了 y={rect.center().y()}，视口正中是 {middle_y}"
    )


def test_the_column_check_never_aborts_a_running_transition(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「核对实际列数」那次修正（50ms 的 ``COLUMN_CHECK_MS`` 撞在 160ms 的过渡上）不许把
    过渡打断在半路：它一落位，那一趟滑向选中项的滚动就停住了，选中项再也没被摆到正中。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    grid.select_row(300, ensure_visible=False)
    settle_grid(qt_app, grid)
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._laid_out_columns >= 2, "列数太少，测的不是这个场景"
    step_transition(grid, 1 / 3)  # 过渡跑到三分之一就撞上核对

    # 必须**少**于当前列数，否则 `_examine_columns` 判「没少」而什么都不做，用例就空了。
    monkeypatch.setattr(grid, "_first_row_columns", lambda: grid._laid_out_columns - 1)
    corrected = grid._laid_out_columns - 1
    grid._examine_columns()
    monkeypatch.undo()
    assert grid._laid_out_columns == corrected, "修正没发生，测的不是这个场景"
    step_transition(grid, 1.0)  # 剩下的那三分之二照跑

    cell_rect = grid.visualRect(grid.model().index(300, 0))
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1, (
        f"选中项停在了 y={cell_rect.center().y()}，视口正中是 {middle}"
    )


def test_a_level_change_never_flashes_to_another_part_of_the_album(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """换档的**第 0 帧**必须还是用户原来看着的那一片。

    滚动位置是内容坐标，换档又同时改了格子高矮与每行张数 —— 照搬旧的像素值当起点，
    同一个数在新布局里指的是**另一批照片**（第 795 张从 y=469 变成 y=34244）。
    判据是「原来那一片还在不在屏上」而不是滚动条那个数字。
    """
    for at_bottom in (True, False):
        for target in (THUMBNAIL_SIZES[2], THUMBNAIL_SIZES[0]):
            where = "视图在底部" if at_bottom else "视图在相册中间"
            grid = make_grid(qt_app, tmp_path, 800)
            settle_grid(qt_app, grid)
            last = grid.model().rowCount() - 1
            grid.select_row(last, ensure_visible=False)
            bar = grid.verticalScrollBar()
            bar.setValue(bar.maximum() if at_bottom else bar.maximum() // 2)
            settle_grid(qt_app, grid)

            middle = QPoint(grid.viewport().width() // 2, grid.viewport().height() // 2)
            anchor = grid.indexAt(middle)
            assert anchor.isValid(), f"{where}：视口正中什么都没有，测的不是这个场景"
            before = set(grid.visible_rows())
            assert len(before) > 4, f"{where}：一张都看不见，测的不是这个场景"

            grid.set_target_edge(target)

            first = set(grid.visible_rows())
            # 取样密度跟着格子大小走（大格取样点少），所以按**少的那一边**算占比：
            # 全都在，就是 100%。
            kept = len(before & first) / min(len(before), len(first))
            assert kept > 0.5, (
                f"{where}、切到 {target} 像素：首帧只剩 {kept:.0%} 是原来那一片"
                f"（{sorted(before)[:4]}… → {sorted(first)[:4]}…）"
            )
            rect = grid.visualRect(anchor)
            assert rect.isValid() and 0 <= rect.center().y() < grid.viewport().height(), (
                f"{where}、切到 {target} 像素：首帧里刚才正中的第 {anchor.row()} 张"
                f"已经不在屏上了（{rect}）"
            )
            grid.close()


def hosted_grid(
    qt_app: QApplication, tmp_path: Path, count: int, *, width: int = 1100, height: int = 700
) -> tuple[QWidget, PhotoGrid]:
    """把网格放进一个**真的布局**里（像窗口里那样），返回 ``(容器, 网格)``。

    网格在布局里时，Qt 会因为纵向滚动条的开关改掉网格控件自己的宽度，于是多送一个
    ``resizeEvent``；顶层网格收不到同样的事件。切档把内容高度推过视口时就会出现这一下。
    """
    holder = QWidget()
    layout = QVBoxLayout(holder)
    grid = PhotoGrid(icon_edge=176)
    model = PhotoListModel()
    model.append(fake_photos(count, tmp_path))
    grid.setModel(model)
    layout.addWidget(grid, 1)
    holder.resize(width, height)
    holder.show()
    qt_app.processEvents()
    # 再摆一次：布局第一轮是按 sizeHint 摆的，这一步之后网格才真的铺满容器。
    holder.resize(width, height)
    qt_app.processEvents()
    return holder, grid


def test_toggling_the_scrollbar_does_not_change_the_cell_size(
    qt_app: QApplication,
    tmp_path: Path,
    wait_for: Callable[..., bool],
) -> None:
    """滚动条自己开关，**不许**改变这一档的格子尺寸与列数：排版宽度里那条滚动条是无条件扣的，
    拿当刻的 ``viewport().width()`` 去排会让同一个窗口算出不同的列数（1100 宽、12 个
    条目下大档是 4 列 268 像素对 3 列 352），那一次重排既是一跳又会把正在跑的过渡掐掉。
    """
    holder, grid = hosted_grid(qt_app, tmp_path, 12)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    assert bar.isVisible(), "前提没摆出来：这一层开头应当有滚动条"

    grid.set_target_edge(THUMBNAIL_SIZES[0])
    settled_cell = grid.gridSize()
    settled_columns = grid._laid_out_columns
    # 缩小一档之后内容比视口矮了，滚动条该收回去 —— 这正是会送来 resizeEvent 的那一下。
    assert wait_for(lambda: not bar.isVisible(), timeout=1.0), (
        "前提没摆出来：缩小一档之后滚动条应当收回去"
    )
    settle_grid(qt_app, grid)

    assert grid.gridSize() == settled_cell, (
        f"滚动条开关把格子从 {settled_cell} 改成了 {grid.gridSize()}"
    )
    assert grid._laid_out_columns == settled_columns, "滚动条开关把列数也改了"
    holder.close()


def test_toggling_the_scrollbar_does_not_cut_the_transition_short(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """缩小一档让滚动条收回去时过渡照旧跑完：排版宽度若直接取当刻的视口宽度，这一次切档会
    再触发一次重排（列数与格宽都变），而那次重排会把过渡判成「局面变了」而落位。
    """
    holder, grid = hosted_grid(qt_app, tmp_path, 12)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    assert bar.isVisible(), "前提没摆出来：这一层开头应当有滚动条"

    model = grid.model()
    assert model is not None
    row = 11  # 旧布局里在最后一行，缩档时位移与尺寸都动得最多
    start = grid._delegate.cell_rect(grid.visualRect(model.index(row, 0)), row)

    grid.set_target_edge(THUMBNAIL_SIZES[0])
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, "过渡该在跑"

    # ``spin`` 让滚动条开关带来的那次 ``resizeEvent`` 在帧与帧之间送达；不转事件循环的话
    # 那个事件压根没送到，这条用例会**空转绿**。
    samples = drive_transition(
        grid,
        lambda: grid._delegate.cell_rect(grid.visualRect(model.index(row, 0)), row).size(),
        spin=qt_app.processEvents,
    )

    assert not bar.isVisible(), "前提没摆出来：缩小一档之后滚动条应当收回去"

    settled = grid._delegate.cell_rect(grid.visualRect(model.index(row, 0)), row)
    assert samples[0] == start.size(), f"第 0 帧该还是旧尺寸，取到 {samples[0]}"
    assert samples[-1] == settled.size(), "最后一帧该正好落在新尺寸上"
    assert len(set(samples)) > 3, (
        f"整段时间里只量到 {len(set(samples))} 种尺寸，过渡被滚动条开关掐掉了"
    )
    assert samples == sorted(samples, key=lambda size: size.width(), reverse=True), (
        f"尺寸该一路缩下去，实际 {samples}"
    )
    holder.close()


def test_a_real_window_resize_still_ends_the_transition(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """真的把窗口拖宽拖窄仍然立刻落位：``resizeEvent`` 里不再无条件落位之后，这条靠
    ``_recompute_layout`` 发现宽度真变了、走 ``_set_cell`` 自己收尾。
    """
    holder, grid = hosted_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, "前提：过渡在跑"

    holder.resize(holder.width() + 300, holder.height())  # resizeEvent 同步送达

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge), "拖窗口就该直接落位"
    holder.close()


def test_the_column_check_does_not_flash_the_frame_to_the_final_size(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核对列数那次修正**不许**让绘制尺寸闪到终点：``_apply_geometry`` 顺手把绘制尺寸对齐到
    新几何（对刚起头的过渡那就是终点），不按动画自己的 ``t`` 摆回去就是「闪一下」。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    grid.select_row(300, ensure_visible=False)
    settle_grid(qt_app, grid)
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._laid_out_columns >= 2, "列数太少，测的不是这个场景"
    step_transition(grid, 1 / 3)  # 过渡跑到三分之一就撞上核对

    monkeypatch.setattr(grid, "_first_row_columns", lambda: grid._laid_out_columns - 1)
    expected = grid._delegate.draw_edge
    grid._examine_columns()
    monkeypatch.undo()

    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, "前提：过渡还在跑"
    assert expected != float(grid.icon_edge), "前提：这一帧本来还没长到终点"
    assert grid._delegate.draw_edge == pytest.approx(expected), (
        f"核对之后绘制尺寸变成了 {grid._delegate.draw_edge}，本该还停在动画的那一帧（{expected}）"
    )


def test_a_resize_that_does_not_change_the_layout_does_not_kill_the_transition(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """只改高度的那种 resize（横幅出现/消失）不该掐掉切档过渡：版面**宽度**没变，列数与格宽
    照旧，只是视口矮了几十像素。切档之外的杂事不该把动画掐掉。「还在往大处长」是把时钟
    往前拨一格量的，量的是「同一段动画确实在推进」。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 0.25)
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, "前提：过渡在跑"
    before = grid._delegate.draw_edge

    # 只矮 40 像素：宽度没变，版面用不着重排，滚动条范围只会变大（不会把位置夹掉）。
    grid.resize(grid.width(), grid.height() - 40)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, (
        "只改高度的 resize 把过渡掐掉了"
    )
    assert grid._delegate.draw_edge == before, "画面不该被这一步动过"
    assert grid._delegate.draw_edge != float(grid.icon_edge), (
        f"画面被直接拨到了终点（{grid._delegate.draw_edge}），过渡没了"
    )

    step_transition(grid, 0.75)
    assert grid._delegate.draw_edge > before, (
        f"过渡该继续往大处长（{before} → {grid._delegate.draw_edge}）"
    )


def _pinch(grid: PhotoGrid, value: float, device: QPointingDevice) -> None:
    """送一个合成的原生捏合手势事件：真的双指没法在测试里做，
    而 ``PhotoGrid.event`` 只认 ``QEvent.NativeGesture`` + ``ZoomNativeGesture``。
    """
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        2,
        QPoint(0, 0),
        QPoint(0, 0),
        QPoint(0, 0),
        value,
        QPoint(0, 0),
    )
    QCoreApplication.sendEvent(grid, event)


def _wheel(
    angle: float,
    pixel: float,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.ControlModifier,
) -> QWheelEvent:
    """一个 ⌘+滚轮事件，角度与像素增量**分开给**：传统滚轮是 ``(角度 120, 像素 0)``，
    魔法鼠标 / 触控板是 ``(角度 4, 像素 20)``（Qt 按 1 像素 = 2 个角度单位折算）。
    """
    return QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        QPoint(0, int(pixel)),
        QPoint(0, int(angle)),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_the_pinch_interval_is_never_shorter_than_the_animation() -> None:
    """限速的全部意义是「让上一段过渡跑完」：比动画短就等于没限。"""
    assert MIN_PINCH_INTERVAL_MS >= EDGE_ANIMATION_MS


def test_a_fast_pinch_switches_only_once(qt_app: QApplication, tmp_path: Path) -> None:
    """手快时连着来的捏合只切一档：判据是**上一次切档的时刻**而不是上一次事件。"""
    grid = make_grid(qt_app, tmp_path, 40)
    device = QPointingDevice()
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    _pinch(grid, PINCH_THRESHOLD, device)
    for _ in range(5):  # 连着来，全都在限速窗口内
        _pinch(grid, PINCH_THRESHOLD, device)

    assert steps == [1], "间隔内的捏合该丢掉：一次切档都发不出去的画面只是一闪"


def test_a_slow_pinch_still_switches_every_time(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """限速不能把慢捏也一起限掉 —— 慢捏本来就该一档一档地过去。"""
    grid = make_grid(qt_app, tmp_path, 40)
    device = QPointingDevice()
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    for _ in range(3):
        _pinch(grid, PINCH_THRESHOLD, device)
        settle(MIN_PINCH_INTERVAL_MS / 1000 + 0.02)

    assert steps == [1, 1, 1]


def test_the_wheel_is_not_rate_limited(qt_app: QApplication, tmp_path: Path) -> None:
    """⌘+滚轮**不**限速：那一格一格是用户明确的意图，限速只会让它变迟钝。

    连按的那几下由 MainWindow 排队补上（见 ``tests/ui/test_shortcuts.py``）。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    for _ in range(3):
        QTest.keyClick(grid, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
        grid.wheelEvent(_wheel(120.0, 0.0))

    assert steps == [1, 1, 1]


def test_ctrl_wheel_does_not_switch_the_size(qt_app: QApplication, tmp_path: Path) -> None:
    """只留 ⌘ + 滚轮切档，⌃ + 滚轮不切。

    macOS 上 Qt 把 Command 报成 ``ControlModifier``、⌃ 报成 ``MetaModifier``；
    两者都记录会让 ⌃ + 滚轮也切档。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    grid.wheelEvent(_wheel(120.0, 0.0, modifiers=Qt.KeyboardModifier.MetaModifier))
    assert steps == [], "⌃ + 滚轮不该切档"

    grid.wheelEvent(_wheel(120.0, 0.0))
    assert steps == [1], "⌘ + 滚轮该切档"


def test_a_magic_mouse_notch_switches_one_step(qt_app: QApplication, tmp_path: Path) -> None:
    """魔法鼠标滚**一格**就该切一档：它的角度增量只有 4，照角度卡阈值会迟钝到不行。
    这里量的是**信号真的发出去了**，不是那个阈值常量本身。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    grid.wheelEvent(_wheel(4.0, 20.0))
    grid.wheelEvent(_wheel(-4.0, -20.0))

    assert steps == [1, -1]


def test_transition_ended_is_emitted_once_per_started_transition(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """「可以安排下一件事了」这个信号，每发出去一段过渡就正好发一次。

    少发了会卡住（等着的切档永远不执行），多发了会把一下按成两下。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    ended: list[bool] = []
    grid.transition_ended.connect(lambda: ended.append(True))

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid.is_transitioning, "前提：过渡真的跑起来了"

    step_transition(grid, 1.0)

    assert not grid.is_transitioning
    assert len(ended) == 1


def test_transition_ended_is_emitted_even_when_the_user_interrupts(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """用户中途滚一下掐断过渡时动画的 ``finished`` 不会来，那个信号也得发，
    否则排在后面等着的那一次切档永远卡住。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    ended: list[bool] = []
    grid.transition_ended.connect(lambda: ended.append(True))

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    step_transition(grid, 1 / 3)
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running, "前提：过渡在跑"

    bar = grid.verticalScrollBar()
    bar.setValue(min(bar.maximum(), bar.value() + 60))  # 用户自己滚一下（同步送达）

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert len(ended) == 1, "被掐断的那一趟也要报一声结束"
    settle_grid(qt_app, grid)
    assert len(ended) == 1, "多发会让排队的切档一次变两下"


def test_a_transition_that_never_ran_does_not_report_itself_as_ended(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """同步落位的那一段不算「跑起来过」：差几个像素、拖窗口与各种重排都会走到那里，
    发了这个信号就会提前叫醒排队等着的那一下，而它的上一段动画其实还在跑。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle_grid(qt_app, grid)
    ended: list[bool] = []
    grid.transition_ended.connect(lambda: ended.append(True))
    cell = grid.gridSize()
    delta = largest_delta_that_keeps_columns(qt_app, grid)

    grid._set_cell(QSize(cell.width() + delta, cell.height() + 3), animate=True)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert not grid.is_transitioning
    assert ended == [], "没跑起来过的过渡不该报结束"
