"""缩略图网格的滚动灵敏度。

**被测的是「滚一次走多少像素」这一个量。** Qt 自己那一档是「角度 ÷ 120 ×
``wheelScrollLines()``（macOS 上 3）× **一个格子高**」，格子高跟着档位与窗口宽度走
（1200×800 视口上是 171 / 220 / 317），于是滚轮**一格**是 513 / 660 / 951 像素 ——
视口才 800 高、格子高 220，每滚一格整整跳过三行，屏上正中的那张照片直接被翻过去，
「部分内容永远看不见」就是这么来的。所以这里钉的是**网格走自己的换算**：滚轮一格
:data:`~gallery.ui.browser.grid.WHEEL_NOTCH_PIXELS` 像素（240，约等于过中档那一行）。

触控板那一路另外锁定「用像素增量」：Qt 把 ``pixelDelta`` 整个丢掉了，于是 macOS 的
「1 像素 = 2 个角度单位」那一层折算又被 5.5 再乘一遍 —— 手指走 40 像素、画面走 110。

「自己走滚动条」这条路上最容易漏掉的是 :meth:`PhotoGrid.scrollContentsBy` 里那道
「用户自己滚了 → 叫停正在跑的切档过渡」的前置条件（那里还挂着行内改名输入框与可见区防抖），
所以它也钉一条：绕开基类的 ``wheelEvent`` 时最容易把那一段整个抄漏。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import make_grid, settle_grid  # noqa: E402
from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.browser.grid import (  # noqa: E402
    WHEEL_NOTCH_PIXELS,
    WHEEL_PIXEL_FACTOR,
    PhotoGrid,
)
from gallery.ui.browser.thumb_sizes import THUMBNAIL_SIZES  # noqa: E402

#: 累积够这么多条目滚动条才有得滚。少于此的话「滚一次走多少」量到的是夹取而不是灵敏度。
ROWS = 400


def _scroll_event(
    *,
    angle: float = 0.0,
    pixel: float = 0.0,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    """一次滚轮事件，角度与像素增量**分开给** —— 两种设备给的正是这两份。

    真机上的那一格：传统滚轮是 ``(角度 120, 像素 0)``，触控板与魔法鼠标是
    ``(角度 = 像素 ÷ 2, 像素)``（Qt 按「1 像素 = 2 个角度单位」折算，见
    ``qnsview_mouse.mm``）。两个字段混着写就看不出这个差别了 —— 而这正是
    :meth:`~gallery.ui.browser.grid.PhotoGrid.wheelEvent` 分成两路的原因。
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


def _middle(grid: PhotoGrid) -> int:
    """滚动条的中途。

    **不许硬编码**：条目的行数与每行几张都随档位变（``_scrolled`` 那条用例就是改档位跑
    的），硬编码的数字在某一档上会落到范围之外，而落在范围之外时 ``setValue`` 会当场夹住
    —— 量出来的就不是灵敏度，是夹取。
    """
    bar = grid.verticalScrollBar()
    assert bar.maximum() > 0, "滚动条压根没有范围，量出来的灵敏度是假的"
    return bar.maximum() // 2


def _scrolled(grid: PhotoGrid, **kwargs: float) -> int:
    """滚一次，返回画面**上移**了多少像素（正数是往下看，负数是往回看）。"""
    bar = grid.verticalScrollBar()
    start = _middle(grid)
    bar.setValue(start)
    assert bar.value() == start
    grid.wheelEvent(_scroll_event(**kwargs))
    return start - bar.value()


def test_a_mouse_notch_is_never_a_page_flip(qt_app: QApplication, tmp_path: Path) -> None:
    """滚轮**一格**不许变成「翻一整页」。

    这是整件事最硬的一条底线：Qt 那一档按 3 × 格子高算，格子一大就够翻一整页了，
    那一格滚轮直接成了翻页键。

    **为什么不是「一格不许跳过一整行」**：那一档是 :data:`WHEEL_NOTCH_PIXELS` = 240，
    而中档那一行才 220 —— 一格约等于过一行是**有意的**，比行高略大一点。
    真正要守住的是「一格仍然是一次滑动，不是一次翻页」。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)

    moved = _scrolled(grid, angle=120)

    assert moved == WHEEL_NOTCH_PIXELS
    assert moved < grid.viewport().height(), (
        f"一格滚轮滚过了 {moved} 像素，比一屏还高（视口 {grid.viewport().height()}）—— "
        "那已经是翻页了"
    )


@pytest.mark.parametrize("icon_edge", THUMBNAIL_SIZES)
def test_a_mouse_notch_is_the_same_at_every_size(
    qt_app: QApplication, tmp_path: Path, icon_edge: int
) -> None:
    """一格滚多少**与档位无关**：否则切一次缩略图尺寸，手感整个变掉。

    Qt 那一档是跟着格子高走的，格子再大一点它还会把一格滚轮当成翻页键 ——
    同一个滚轮，在三档里是三种完全不同的键。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    grid.set_target_edge(icon_edge)
    settle_grid(qt_app, grid)

    assert _scrolled(grid, angle=120) == WHEEL_NOTCH_PIXELS, (
        f"{icon_edge} 像素那一档滚一格走的不是 {WHEEL_NOTCH_PIXELS}"
    )


def test_a_mouse_notch_scrolls_the_same_way_back(qt_app: QApplication, tmp_path: Path) -> None:
    """往回滚与往下滚一样多。

    符号反了不是「慢一点」，而是「往上滚反而看得更远」—— 滚轮有一半时间在对着干。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)

    assert _scrolled(grid, angle=-120) == -WHEEL_NOTCH_PIXELS


def test_the_trackpad_follows_the_fingers(qt_app: QApplication, tmp_path: Path) -> None:
    """触控板按**像素增量**走：手指走多少，画面跟着走多少（再乘一个系数）。

    Qt 丢掉 ``pixelDelta``、只按角度增量算，于是 macOS 的「1 像素 = 2 个角度单位」被
    5.5 再乘一遍：手指走 40 像素，画面走 110 —— 那就是「太灵敏」。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)

    for finger in (20, 40, 80):
        assert _scrolled(grid, angle=finger / 2, pixel=finger) == pytest.approx(
            finger * WHEEL_PIXEL_FACTOR
        )


def test_the_trackpad_is_not_faster_than_the_fingers(qt_app: QApplication, tmp_path: Path) -> None:
    """画面**不许跑得比手指快**。

    这一条是给「灵敏度」回退的：系数调反了或调大了，两种设备都会变成「动一下手指翻
    一页」，而上面那条只锁定了一小段位移，量不出来。
    """
    assert WHEEL_PIXEL_FACTOR <= 1.0

    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)

    assert _scrolled(grid, angle=20, pixel=40) <= 40


def test_a_slow_nudge_is_not_swallowed(qt_app: QApplication, tmp_path: Path) -> None:
    """轻推一下（1 像素的增量）不能一动不动：没走满一个像素的那点零头要留着。

    直接取整的话，每次 0.5 像素都变成 0，而零头也没人累积 —— 慢速推十下纹丝不动，
    一下推猛了却动一大截。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()

    start = _middle(grid)
    bar.setValue(start)
    for _ in range(10):
        grid.wheelEvent(_scroll_event(angle=0.5, pixel=1))

    # 10 × 0.5 = 5 像素：十次里有五次把上一次剩下的那一点带上，不多不少。
    assert bar.value() == start - 5


def test_an_event_with_no_delta_at_all_moves_nothing(qt_app: QApplication, tmp_path: Path) -> None:
    """两个字段都是 0 的事件（有些合成设备会发）不许变成一整格，也不许忽略掉零头。

    顺带锁定零增量上不回退到 Qt 那一档 —— 那一档在零增量上是「翻页」。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()

    start = _middle(grid)
    bar.setValue(start)
    for _ in range(10):
        grid.wheelEvent(_scroll_event(angle=0.5, pixel=1))
    grid.wheelEvent(_scroll_event())
    grid.wheelEvent(_scroll_event())

    # 零增量既没动画面，也没把暂存的那点零头清掉：仍然正好是 5 像素。
    assert bar.value() == start - 5


def test_a_wheel_notch_still_cancels_a_running_transition(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """滚一下仍然要让正在跑的切档过渡落位（用户自己接手了）。

    这一条盯的是 :meth:`~gallery.ui.browser.grid.PhotoGrid.scrollContentsBy` 那条路
    没被绕过去：走 ``setValue`` 而不是直接调 ``scrollContentsBy``，靠的就是它照样经过
    那里，而那道前置条件就在里面。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)
    # **先摆到中途**：停最顶上时往下滚会被 ``setValue`` 夹住，压根没有滚动发生，
    # 而这道前置条件只在真的滚了的时候才该触发 —— 那样量出来的是「没滚」，不是「没让位」。
    grid.verticalScrollBar().setValue(_middle(grid))
    grid._start_transition(  # noqa: SLF001 - 锁定那条私有路径（与 edge_animation 同一处）
        float(THUMBNAIL_SIZES[2]), grid.verticalScrollBar().value(), animate=True
    )
    assert grid._transition_anim.state() == QAbstractAnimation.State.Running

    grid.wheelEvent(_scroll_event(angle=-120))

    assert grid.verticalScrollBar().value() != _middle(grid), "前置条件：这一格真的滚走了"
    assert grid._transition_anim.state() == QAbstractAnimation.State.Stopped


def test_the_rename_box_travels_with_the_cell_under_it(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """行内改名的输入框要跟着它盖着的那一格走 —— 位移要**一模一样**。

    ``scrollContentsBy`` 里手动挪一次是不对的：基类滚的是 ``viewport->scroll``，
    而 ``QWidget::scroll`` **自己就会挪子控件** —— 两次正好抵消，输入框钉在原地不动，
    格子走了它不走，于是盖在别的照片的名字上。

    量的是**输入框的位移 == 那一格 ``visualRect`` 的位移**，而不是「输入框动了没有」：
    移错了方向照样算「跟着」，那种 bug 只有这样才量得出来。
    """
    grid = make_grid(qt_app, tmp_path, ROWS)
    settle_grid(qt_app, grid)
    index = grid.model().index(1, 0)
    grid.scrollTo(index)
    assert grid.begin_rename(index)
    qt_app.processEvents()
    grid.verticalScrollBar().setValue(_middle(grid))

    box_before = grid._rename_edit.pos().y()  # noqa: SLF001
    cell_before = grid.visualRect(index).top()
    assert _scrolled(grid, angle=120) == WHEEL_NOTCH_PIXELS, "前置条件：这一格真的滚走了"

    assert grid._rename_edit.pos().y() - box_before == grid.visualRect(index).top() - cell_before, (  # noqa: SLF001
        f"格子走了 {cell_before - grid.visualRect(index).top()} 像素，"
        f"输入框只走了 {grid._rename_edit.pos().y() - box_before} 像素"  # noqa: SLF001
    )
