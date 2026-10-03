"""缩略图网格的滚动惯性。

这一份测试量的**不是**「有没有动画」，而是动画一旦缺席就一定会坏掉的那几条契约：

- 滚轮增量**不许**当帧写进滚动条。写进去就是「每格跳一下」，正是要改掉的旧行为
  （``QAbstractItemView`` 默认就是这么处理滚轮的）。
- 一格滚轮最终要落在 :data:`WHEEL_NOTCH_PIXELS` 像素上 —— 位移由我们自己换算，
  就不再是「基类碰巧怎么算的」，得有一根钉子钉住。
- **别人**动了滚动条（方向键、``scrollTo``、拖滑块、换层重排）时惯性必须立刻让路。
  抢起手来的后果不是「抖一下」，而是用户明明按了方向键、画面还在自己滑。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from grid_helpers import fake_photos, make_grid  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.browser.scroll_kinetics import WHEEL_NOTCH_PIXELS  # noqa: E402

#: 够跑完一次滑行（时间常数 0.08 秒，几倍之后收敛到不足半个像素）。
GLIDE_SECONDS = 0.5

#: 采样滑行过程时每一步转多少秒。分片是为了取中间值，而不是一口跑到结束。
SLICE_SECONDS = 0.02

#: 建好网格时那一次视口去抖要先走完，否则它在断言中途插进来重算可见区。
SETTLE_SECONDS = 0.2

#: 触发一次滚轮的位置：网格正中央。
WHEEL_POINT = QPointF(600, 400)


def send_wheel(
    grid: PhotoGrid, *, notches: int = 0, pixels: int = 0, inverted: bool = False
) -> None:
    """发一次**向下**滚：``notches`` 是传统滚轮的格数，``pixels`` 是触控板的像素增量。

    方向在这里就定死，免得每个用例都要自己记得 Qt 的符号约定：``angleDelta`` 与
    ``pixelDelta`` 为正表示「向上滚」（内容往上、滚动条数值变小），向下滚要发负的。
    """
    grid.wheelEvent(
        QWheelEvent(
            WHEEL_POINT,
            WHEEL_POINT,
            QPoint(0, -pixels),
            QPoint(0, -notches * 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            inverted,
        )
    )


def test_one_notch_glides_exactly_its_own_distance(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """一格滚轮最终停在 15 像素上 —— 位移是我们自己算的，就得由测试钉住。

    改之前这里量到的是 **0**：``QAbstractItemView`` 在这个 offscreen 环境里对合成的
    滚轮事件直接不滚。所以这条断言在改动前是红的（见提交信息），改完才绿。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)

    send_wheel(grid, notches=1)
    settle(GLIDE_SECONDS)

    assert grid.verticalScrollBar().value() == WHEEL_NOTCH_PIXELS


def test_the_notch_does_not_jump_on_the_frame_it_arrives(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """滚轮事件当帧滚动条不许动 —— 这一条正是「惯性」与「每格跳一下」的分界。

    没有它，一个把 ``setValue`` 直接写在 ``wheelEvent`` 里的实现能全绿。
    这里只转**一轮**事件循环：0 毫秒的定时器来得及跑，16 毫秒的还来不及。
    """
    grid = make_grid(qt_app, tmp_path, 400)

    send_wheel(grid, notches=1)
    qt_app.processEvents()

    assert grid.verticalScrollBar().value() == 0


def test_a_trackpad_swipe_scrolls_by_its_own_pixels(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """触控板那一路**按像素走**，不经过滚轮那个「除以 8」的换算。

    这一条不是小事：macOS 上真正发 ``pixelDelta`` 的是触控板与 Magic Mouse，
    写错就是「快速滑动只走八分之一」。只测滚轮的话这条整个漏掉。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)

    send_wheel(grid, pixels=45)
    settle(GLIDE_SECONDS)

    assert grid.verticalScrollBar().value() == 45


def test_an_inverted_wheel_scrolls_the_other_way(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """系统把方向反过来时（``inverted``）跟着反过来 —— 与 Qt 自己的滚动区同一套读法。

    从中段起滚：停在顶部时「往上滚」量到的也是 0，什么都没发生与翻对了分不开。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)
    bar = grid.verticalScrollBar()
    bar.setValue(400)
    settle(SETTLE_SECONDS)

    send_wheel(grid, notches=2, inverted=True)
    settle(GLIDE_SECONDS)

    assert bar.value() == 400 - 2 * WHEEL_NOTCH_PIXELS


def test_the_view_walks_through_the_gap_instead_of_teleporting(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """一格滚轮走的是一串中间值，不是一步到位。

    只断言首尾两端的话，一个「先跳到终点再滑回来」的实现也能过；这里量的是**路径**。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)

    send_wheel(grid, notches=4)
    seen = [grid.verticalScrollBar().value()]
    for _ in range(int(GLIDE_SECONDS / SLICE_SECONDS)):
        settle(SLICE_SECONDS)
        seen.append(grid.verticalScrollBar().value())

    assert seen == sorted(seen), f"位置一路往回退过：{seen}"
    assert len(set(seen)) > 3, f"只取到 {sorted(set(seen))}，这是一次跳变不是滑行"
    assert seen[0] < seen[-1] == 4 * WHEEL_NOTCH_PIXELS


def test_several_notches_in_a_row_add_up(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """连着滚五格要走五格那么多 —— 而且**不能**让第二格把第一格顶掉。

    「顶掉」是这里真正要防的：若每次来事件都把目标重新锚到当前值，五格滚下来
    只走得动一格的距离（每一格都在把上一格还没滑完的部分扔掉）。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)

    for _ in range(5):
        send_wheel(grid, notches=1)
        settle(0.01)
    settle(GLIDE_SECONDS)

    assert grid.verticalScrollBar().value() == 5 * WHEEL_NOTCH_PIXELS


def test_the_glide_never_goes_past_the_end_of_the_list(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """已经在末尾还往下滚，多出来的那几格不许把内容推出合法范围。"""
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum())
    settle(SETTLE_SECONDS)

    send_wheel(grid, notches=20)
    settle(GLIDE_SECONDS)

    assert bar.value() == bar.maximum()


def test_the_timer_stops_once_the_glide_is_over(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """滑完了就停表 —— 一根永远转下去的 16 毫秒定时器是白烧 CPU。"""
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)

    send_wheel(grid, notches=2)
    assert grid._momentum_timer.isActive()
    settle(GLIDE_SECONDS)

    assert not grid._momentum_timer.isActive()


def test_someone_else_moving_the_scrollbar_stops_the_glide(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """滑行途中滚动条被别人动了：惯性立刻让路，不许再把画面拖回去。

    「别人」就是方向键、``scrollTo``、拖滑块、换层重排。抢起手来的后果不是抖一下，
    而是用户明明已经看���下一屏了，画面还在替上一段滑行往回追。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)
    bar = grid.verticalScrollBar()

    send_wheel(grid, notches=10)
    settle(0.05)
    landing = bar.value() + 400
    bar.setValue(landing)
    settle(GLIDE_SECONDS)

    assert bar.value() == landing
    assert not grid._momentum_timer.isActive()


def test_changing_the_sort_order_stops_the_glide(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """换排序时正在滑行的惯性必须收掉 —— 那一段路是**上一层**要走的。

    这一条不能用「换相册」来测：换相册会把模型清空，滚动条于是归零、顺带把惯性也停了，
    测出来的绿灯是**顺带**的。换排序不挪动滚动条，于是只剩
    :meth:`PhotoGrid._on_model_reset` 里那一行兜着 —— 去掉它这条就红。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)
    bar = grid.verticalScrollBar()

    send_wheel(grid, notches=10)
    settle(0.05)
    landing = bar.value()

    grid.model().reorder(list(reversed(fake_photos(400, tmp_path))))
    settle(GLIDE_SECONDS)

    assert bar.value() == landing, "换排序之后惯性还替上一层在滑"
    assert not grid._momentum_timer.isActive()


def test_a_glide_still_triggers_the_thumbnail_prefetch(
    qt_app: QApplication, tmp_path: Path, wait_for: Callable[..., bool]
) -> None:
    """滑行结束时仍然要发 ``viewport_changed``，否则这一段滑过去的格子永远没有缩略图。

    惯性改的是「谁在写滚动条的值」，下游那条「滚动 → 去抖 → 重算可见区 → 排缩略图」
    的链路一个字都不该动（见 :meth:`PhotoGrid.scrollContentsBy`）。

    **先等滑行走完再等信号**：去抖只保证「最后一次滚动之后 60ms」，而 60ms 足够
    滑完剩下那几像素，于是信号既可能在滑行途中发、也可能在收尾时发 —— 断言它的
    时刻必须定在「走完之后」，否则测的是运气。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    seen: list[int] = []
    grid.viewport_changed.connect(lambda: seen.append(1))
    bar = grid.verticalScrollBar()

    send_wheel(grid, notches=3)

    assert wait_for(lambda: bar.value() == 3 * WHEEL_NOTCH_PIXELS), "滑行没有走完"
    assert wait_for(lambda: bool(seen)), "滑完了还没通知界面重算可见区"


def test_cmd_wheel_still_resizes_instead_of_scrolling(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """⌘+滚轮仍然只切档，一像素都不许滚 —— 那是另一个手势。

    方向照旧：向上滚（``angleDelta`` 为正）= 放大，所以发正的。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle(SETTLE_SECONDS)
    steps: list[int] = []
    grid.size_step_requested.connect(steps.append)

    grid.wheelEvent(
        QWheelEvent(
            WHEEL_POINT,
            WHEEL_POINT,
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )
    settle(GLIDE_SECONDS)

    assert steps == [1]
    assert grid.verticalScrollBar().value() == 0
    assert not grid._momentum_timer.isActive()
