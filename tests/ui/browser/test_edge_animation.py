"""切档动画的回归测试。

动画的**全部意义**就是「别跳变」，所以这里量的是它有没有真的走过中间值 ——
只断言首尾两端的话，一个「什么都不做、只是把两个端点各画一次」的实现也能全绿。

更要紧的是它**不许**做的事，那才是这个方案的全部约束所在（理由写在
:func:`grid._animate_draw_edge` 的文档里）：

- 几何（``gridSize`` / :attr:`PhotoGrid.icon_edge`）**一步到位**。跟着动画走就意味着
  每帧一次全量 ``doItemsLayout``，几万条目的库会直接卡住。
- ``icon_edge_changed`` 只能发**最终**的整数边长。跟着动画走等于按几十个中间尺寸
  各要一遍缩略图，:mod:`gallery.ui.browser.thumb_sizes` 那条「离散档位 ⇒ 缓存组合
  稳定」的不变量当场作废。
- 拖窗口**不**放动画。那是连续事件，每次宽度变化放一次就是永远在重绘。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import make_grid  # noqa: E402
from PySide6.QtCore import QAbstractAnimation, QPoint, QRect, QSize  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

from gallery.ui.browser.grid import (  # noqa: E402
    EDGE_ANIMATION_MIN_DELTA,
    EDGE_ANIMATION_MS,
    GRID_PADDING_X,
    PhotoDelegate,
    PhotoGrid,
)
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

    assert grid._draw_animation.state() == QAbstractAnimation.State.Stopped
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

    assert grid._draw_animation.state() == QAbstractAnimation.State.Stopped
    assert grid._delegate.draw_edge == float(grid.icon_edge)


def test_two_steps_in_a_row_do_not_jump_to_the_discarded_size(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """连点两档时，绘制尺寸从**当前**那一帧接着走，不先跳去被放弃的那一档。

    动画起点取的是「上一帧画出来的」尺寸而不是几何尺寸，就是为这个场景：上一段
    动画多半还没跑完，两个值不等。
    """
    grid = make_grid(qt_app, tmp_path, 40)
    settle(SETTLE_SECONDS)
    before = grid._delegate.draw_edge

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    grid.set_target_edge(THUMBNAIL_SIZES[0])

    assert grid._delegate.draw_edge == before, "不该先跳去中间那一档"
    assert grid.icon_edge < float(before), "几何一步到位，直接变成最小的��一档"


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

    # 终点**独立地问一次 Qt**，不读 ``endValue``（那是被测代码自己的输出，拿它当
    # 期望值就是自己验自己）。过渡已跑完、布局已定，``PositionAtCenter`` 是绝对
    # 定位，再要一次拿到的就是它认可的位置。
    grid.scrollTo(grid.currentIndex(), QAbstractItemView.ScrollHint.PositionAtCenter)
    settle(SLICE_SECONDS)
    assert grid.verticalScrollBar().value() == samples[-1], "没落在 scrollTo 认可的位置上"


def test_a_scroll_start_beyond_the_new_range_is_clamped(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """交给动画的起点必须落在滚动条自己的范围里。

    列数变多时内容变矮、旧位置超出新范围，不夹的话插值中途会被 ``maximum()`` 削平 ——
    动画先平一段再动，比不动画还难看。而这个毛病**从取样里看不出来**（Qt 会替你夹，
    取到的值永远合法），所以只能直接查喂给动画的那个起点。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)
    grid.set_target_edge(THUMBNAIL_SIZES[2])
    settle(SETTLE_SECONDS)

    bar = grid.verticalScrollBar()
    grid._animate_scroll(bar.maximum() + 5000, animate=True)

    assert float(grid._scroll_animation.startValue()) <= bar.maximum()
    assert grid._scroll_animation.state() == QAbstractAnimation.State.Running


def test_a_scroll_of_your_own_cancels_the_transition(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """过渡跑着的时候用户自己滚了，动画必须立刻让位。

    不让位的话，动画会一边往终点走、一边被这次滚动推着改终点，最后落到一个谁也
    没指定的位置上。
    """
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.set_target_edge(THUMBNAIL_SIZES[2])
    assert grid._scroll_animation.state() == QAbstractAnimation.State.Running

    grid.verticalScrollBar().setValue(grid.verticalScrollBar().value() - 40)
    settle(SLICE_SECONDS)

    assert grid._scroll_animation.state() == QAbstractAnimation.State.Stopped


def test_resizing_does_not_scroll_the_grid_around(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """拖窗口是连续事件，不能每变一次宽度就放一次滚动过渡。"""
    grid = scrollable_grid(qt_app, tmp_path)
    settle(SETTLE_SECONDS)

    grid.resize(900, 800)
    settle(SLICE_SECONDS)

    assert grid._scroll_animation.state() == QAbstractAnimation.State.Stopped
