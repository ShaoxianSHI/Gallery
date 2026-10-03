"""切档过渡的回归测试。

过渡的**全部意义**就是「别跳变」，所以这里量的是它有没有真的走过中间值 ——
只断言首尾两端的话，一个「什么都不做、只是把两个端点各画一次」的实现也能全绿。

更要紧的是它**不许**做的事，那才是这个方案的全部约束所在（理由写在
:func:`grid._start_transition` 的文档里）：

- 几何（``gridSize`` / :attr:`PhotoGrid.icon_edge` / ``visualRect``）**一步到位**。
  跟着过渡走就意味着每帧一次全量 ``doItemsLayout``，几万条目的库会直接卡住；
  格子从旧位飞到新位是**委托侧画的时候平移**出来的，不是真的重排。
- ``icon_edge_changed`` 只能发**最终**的整数边长。跟着过渡走等于按几十个中间尺寸
  各要一遍缩略图，:mod:`gallery.ui.browser.thumb_sizes` 里那条「离散档位 ⇒ 缓存组合
  稳定」的不变量当场作废。
- 拖窗口**不**放过渡。那是连续事件，每次宽度变化放一次就是永远在重绘。

代价是有 160ms 画面几何 ≠ 逻辑几何。这条不一致由「任何交互都立刻落位」兜底
（见 :func:`grid._cancel_transition`），所以也测它。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import make_grid  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QAbstractAnimation,
    QPoint,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QStyleOptionViewItem,
)

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.browser.grid import (  # noqa: E402
    EDGE_ANIMATION_MIN_DELTA,
    EDGE_ANIMATION_MS,
    GRID_PADDING_X,
    GridTransition,
    PhotoDelegate,
    PhotoGrid,
)
from gallery.ui.browser.model import PhotoListModel  # noqa: E402
from gallery.ui.browser.thumb_sizes import THUMBNAIL_SIZES  # noqa: E402

#: 每一步转多少秒。分片是为了在动画跑到一半时取样，而不是一口跑到结束。
SLICE_SECONDS = 0.02

#: 跑完动画 + 去抖要多少秒。动画比去抖长，所以这个余量是实的。
SETTLE_SECONDS = EDGE_ANIMATION_MS / 1000 + 0.3


def samples_while_animating(grid: PhotoGrid, settle: Callable[[float], None]) -> list[float]:
    """跑完一次动画，把期间 :attr:`PhotoDelegate.draw_edge` 的取值按顺序记下来。"""
    seen: list[float] = []
    for _ in range(int(SETTLE_SECONDS / SLICE_SECONDS)):
        settle(SLICE_SECONDS)
        seen.append(grid._delegate.draw_edge)
    return seen


def test_the_thumbnail_grows_through_the_intermediate_sizes(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """切档之后，绘制尺寸确实**路过**中间值，而不是直接跳到终点。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)
    start = grid._delegate.draw_edge

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    samples = samples_while_animating(grid, settle)

    assert min(samples) == start, "第一帧必须还是旧尺寸"
    assert samples[-1] == float(grid.icon_edge), "最后一帧必须正好落在新尺寸上"
    assert len(set(samples)) > 3, f"只取到 {sorted(set(samples))}，这是一次跳变不是动画"
    assert min(samples) < max(samples), "尺寸得是单调长过去的"


def test_the_geometry_snaps_while_only_the_painting_moves(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """切档的那一刻几何就已经到位，动画期间它一步都不许再动。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)
    before_cell = grid.gridSize()

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settled_edge = grid.icon_edge

    assert grid.gridSize() != before_cell, "格子总得真的变大"
    assert grid.gridSize().width() - GRID_PADDING_X == settled_edge
    assert grid._delegate.draw_edge != settled_edge, "绘制尺寸这时应该还在旧尺寸上"

    # 动画跑到一半再量一次：几何必须纹丝不动。
    settle(EDGE_ANIMATION_MS / 1000 / 2)
    assert grid.icon_edge == settled_edge
    assert grid.gridSize().width() - GRID_PADDING_X == settled_edge


def test_the_animation_never_reaches_the_thumbnail_pipeline(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """``icon_edge_changed`` 只发最终边长这一个整数 —— 流水线看不到中间值。

    这是这个方案最容易写坏的一条：动画值一旦顺着发出去，后台就会按几十个尺寸
    各解码一遍，而磁盘上的缓存键也跟着多几十种。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)  # 让建好网格时那一次去抖先走完
    emitted: list[int] = []
    grid.icon_edge_changed.connect(emitted.append)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    assert emitted == [grid.icon_edge], f"只该发一次最终边长，实际发了 {emitted}"
    assert all(isinstance(value, int) for value in emitted)


def test_resizing_the_window_does_not_animate(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """拖窗口是连续事件，不能每变一次宽度就放一次动画。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)

    grid.resize(900, 800)
    settle(SLICE_SECONDS)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge), "窗口一变就该直接落位"


def test_a_change_too_small_to_see_does_not_animate(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """差几个像素肉眼看不见，却要付一整轮全视口重绘 —— 直接落位。"""
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)
    cell = grid.gridSize()

    grid._apply_cell(
        QSize(cell.width() + EDGE_ANIMATION_MIN_DELTA - 2, cell.height() + 3), animate=True
    )

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge)


def test_two_steps_in_a_row_do_not_jump_to_the_discarded_size(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """连点两档时不接第二段过渡 —— 画面会先蹿去用户已经放弃的那一档。

    上一段没跑完时，它的「旧布局」已经不是这一段的旧布局了，拿它当起点就是往回跳。
    直接落到最终那档，反而更贴近连按两下的人想要的结果。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)
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
    # 几何一步都没动：格子还是原来那么大。
    assert delegate.sizeHint(None, None) == hint  # type: ignore[arg-type]


# ---- 纵向滚动 ------------------------------------------------------------


def scroll_samples(grid: PhotoGrid, settle: Callable[[float], None]) -> list[int]:
    """跑完一次过渡，把期间纵向滚动位置的取值按顺序记下来。"""
    seen: list[int] = []
    for _ in range(int(SETTLE_SECONDS / SLICE_SECONDS)):
        settle(SLICE_SECONDS)
        seen.append(grid.verticalScrollBar().value())
    return seen


def scrollable_grid(qt_app: QApplication, tmp_path: Path, row: int = 20) -> PhotoGrid:
    """造一个「当前项就在屏内、但快到下沿」的网格 —— 切档时 ``scrollTo`` 必定要挪一大段。

    **当前项必须在 ``scrollTo`` 已经排过版的那批里**（Batched 布局默认一批 64 条）。
    超出批次时 ``visualRect`` 返回 ``QRect(-1, -1, 0, 0)``，``scrollTo`` 静默什么都不做
    —— 那是既有限制，见 ``_apply_cell`` 里的说明，不是这里要测的东西。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(row, ensure_visible=False)
    return grid


def test_the_grid_scrolls_into_place_instead_of_jumping(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """纵向滚动也是一段过渡 —— 它跳的是整屏高度，比缩略图那一下明显得多。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    samples = scroll_samples(grid, settle)

    assert len(set(samples)) > 3, f"只取到 {sorted(set(samples))}，这是一次跳变不是过渡"
    assert samples == sorted(samples), f"回退了一下：{samples}"

    # 终点直接看**结果**：跑完之后当前项那一行就该在视口正中间。
    # 不拿 ``scrollTo`` 当基准 —— 那正是被换掉的东西。
    # 容 1 像素：算中线要除两次（格高、视口高），整数除法落在哪一头就差这一像素，
    # 肉眼完全看不出来；拿它当严格相等只会得到一条测不出毛病的红。
    cell_rect = grid.visualRect(grid.model().index(grid.current_row(), 0))
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1


def test_a_scroll_start_beyond_the_new_range_is_clamped(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """交给动画的滚动起点必须落在滚动条自己的范围里。

    列数变多时内容变矮、旧位置超出新范围，不夹的话插值中途会被 ``maximum()`` 削平 ——
    动画先平一段再动，比不动画还难看。而这个毛病**从取样里看不出来**（Qt 会替你夹，
    取到的值永远合法），所以只能直接查 ``_start_transition`` 存下来的那个起点。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
    grid.set_target_edge(THUMBNAIL_SIZES[2])  # 过渡正在跑
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running

    bar = grid.verticalScrollBar()
    grid._start_transition(grid._delegate.draw_edge, bar.maximum() + 5000, animate=True)

    assert grid._scroll_from <= bar.maximum()


def test_a_scroll_of_your_own_cancels_the_transition(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """过渡跑着的时候用户自己滚了，动画必须立刻让位，而且**尊重他滚到的那儿**。

    不让位的话，动画会一边往终点走、一边被这次滚动推着改终点，最后落到一个谁也
    没指定的位置上。而收尾时要是把滚动拽回 ``_scroll_to``，用户刚滚的地方就白滚了。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running

    bar = grid.verticalScrollBar()
    chosen = bar.value() - 40
    bar.setValue(chosen)
    settle(SLICE_SECONDS)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert bar.value() == chosen, "用户滚到哪儿就得留在哪儿"


def test_resizing_does_not_scroll_the_grid_around(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """拖窗口是连续事件，不能每变一次宽度就放一次滚动过渡。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.resize(900, 800)
    settle(SLICE_SECONDS)

    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped


# ---- 格子的位置插值 --------------------------------------------------------


def test_a_reflow_moves_the_cells_and_their_offsets_differ_per_row(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """换行重排时每一格的位移不同，插值得按行算而不是整屏平移一个数。

    用 6 列 → 4 列这个真实场景：同一行的第 0 格只往右挪，第 1 格往右上，
    而换行的那几格要跨一整行。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    transition = grid._transition
    assert transition is not None
    assert transition.reflows, "6 列变 4 列就是重排"

    offsets = {row: transition.offset(row, 0.0) for row in range(0, 24)}
    assert len(set(offsets.values())) > 1, "整屏平移一个数那是错的，位移逐行都不同"
    assert offsets[1] != offsets[5], "同一行里的两格横向位移也不一样"


def test_the_first_frame_lands_exactly_where_the_cells_used_to_be(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """第 0 帧必须**正好**是切换前的位置，一个像素都不许差。

    两端的列数是量出来的（不是按公式算的），所以这条同时也在盯着「量错了就整体
    漂移」那个很难查的毛病。逐行比对，漂 1 像素也照样红。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
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
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """跑完之后画面与逻辑几何完全重合，过渡状态清干净。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    assert grid._transition is None
    assert grid._delegate.cell_offset(0) == QPoint()


def test_a_press_during_the_transition_makes_the_picture_agree_again(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """过渡中按一下 —— 画面立刻落位，命中才对得上画出来的那一格。

    不这么做的话，那一按会选中「画得好看的那一格」之外的另一格。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SLICE_SECONDS)
    assert grid._transition is not None

    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(20, 20))

    assert grid._transition is None
    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge)


def test_new_rows_arriving_during_the_transition_end_it(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """插进来的新行没有「旧位置」可言，过渡必须作废。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._transition is not None

    grid.model().append([PhotoRef(path=tmp_path / "NEW.jpg", size=1024, mtime_ns=99)])

    assert grid._transition is None


#: 渲染用的那张卡片的缩略图颜色。
COVER = QColor(200, 40, 40)


def _painted_top(
    model: PhotoListModel,
    row: int,
    cell: QSize,
    transition: GridTransition,
    t: float,
    canvas: QSize,
) -> int:
    """把第 ``row`` 行按过渡的第 ``t`` 帧画到全白画布上，返回卡片顶边所在的行。

    直接走 :meth:`PhotoDelegate.paint`，量到的就是**真的画在哪儿** —— 只查
    :attr:`PhotoDelegate.cell_offset` 的话，「绘制压根没用这个偏移」这种改动
    根本不会有测试变红。
    """
    delegate = PhotoDelegate(icon_edge=cell.width() - GRID_PADDING_X)
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


def test_the_transition_uses_the_measured_columns_not_the_intended_ones(
    qt_app: QApplication,
    tmp_path: Path,
    settle: Callable[[float], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两端必须用**量出来**的列数，不能用「打算摆几列」。

    ``_columns_for`` 给的是打算摆几列，Qt 实际摆出来的可能差一列（样式边距吃掉的
    那一列，靠 ``_layout_fix`` 事后修正）。用错的那一个，每格会整体漂一整列的位移
    —— 那个量级的毛病很难看出是哪来的。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
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


# ---- 切档后把选中项摆到正中 ------------------------------------------------


def test_a_selection_far_down_still_gets_centered(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """选中项在第 80 行（远超 Batched 那一批）也要摆到正中。

    这是换成自己算的原因：``scrollTo`` 要问 Qt 问 ``visualRect``，而 Batched 布局
    一批只排 64 条，超出批次的行它静默什么都不做 —— 用户明明选中了第 80 行，
    切一档之后它被留在屏幕外。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(80, ensure_visible=False)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    assert grid.current_row() == 80, "选中项本身不该被动过"
    cell_rect = grid.visualRect(grid.model().index(80, 0))
    assert cell_rect.isValid(), "选中项得在视口里"
    middle = grid.viewport().height() // 2
    assert abs(cell_rect.center().y() - middle) <= 1


def test_a_selection_on_the_last_row_is_centered_not_pinned_to_the_end(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """内容比视口短时，「摆到正中」算出来是个负数 —— 必须夹到范围里。

    夹不住的话过渡会照着一个越界的目标插值，末几帧被 Qt 压平：动画先走一段、
    末段突然不动，看着像卡住了。**滚动条自己也会夹**，所以从 ``value()`` 上完全
    看不出来 —— 只能查那个目标值本身。
    """
    grid = make_grid(qt_app, tmp_path, 200)
    grid.select_row(0, ensure_visible=False)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    bar = grid.verticalScrollBar()
    assert grid._scroll_to == bar.minimum(), f"目标值 {grid._scroll_to} 越界了"
    assert bar.value() == bar.minimum()
    assert grid.visualRect(grid.model().index(0, 0)).top() >= 0, "第一行该完整可见"


def test_a_selection_on_the_last_row_is_not_pushed_off_screen(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """末行选不中时，夹到下界就好 —— 别把它硬按到屏幕外面。"""
    grid = make_grid(qt_app, tmp_path, 6)
    grid.select_row(5, ensure_visible=False)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    bar = grid.verticalScrollBar()
    assert grid._scroll_to <= bar.maximum(), "目标值越界了"
    assert grid.visualRect(grid.model().index(5, 0)).bottom() <= grid.viewport().height()


def test_the_layout_is_exactly_the_same_whether_or_not_it_animates(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """**动画只改「画在哪儿」，不改变任何布局结果。**

    这条是整件事的地基：几何、列数、每格的逻辑位置在切换那一刻就是终态，动画
    只是委托 ``paint`` 里多出来的一个偏移。于是「关掉动画比一遍」永远是安全的
    A/B 手法，也意味着修布局缺陷（比如 :func:`grid._center_on_row`）与动画无关。
    """
    animated = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
    animated.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    plain = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
    plain._transition_anim.setDuration(0)  # 0 毫秒 = 立即落位，等于没有过渡
    plain.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    assert animated.gridSize() == plain.gridSize()
    assert animated._first_row_columns() == plain._first_row_columns()
    assert animated.icon_edge == plain.icon_edge
    assert animated.verticalScrollBar().value() == plain.verticalScrollBar().value()
