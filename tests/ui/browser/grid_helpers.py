"""网格测试共用的造法与等待方式。

**只有一份** —— :func:`make_grid` 把「有模型、有条目、已排过版」的网格造出来，
好几处测试都要它。各写一份的问题与等待助手一样：改了一处不会改到其余几处，
于是「同一件事有两种写法」。

放在这里而不是 ``tests/ui/conftest.py``：它是**普通函数**而不是 fixture（调用方要
自己给 ``qt_app`` / ``tmp_path``），而且只服务 ``tests/ui/browser/`` 这一层。

另外两样（:func:`settle_grid` 与 :func:`drive_transition`）是「把网格拨到某个状态」的
办法，理由都写在各自的文档里。一句话版本：**网格上的每一样等待都有确切的条件，
于是就不该靠睡够多少秒来等**。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QSize
from PySide6.QtWidgets import QApplication
from waiting import wait_until

from gallery.core.library.photos import PhotoRef
from gallery.ui.browser.grid import EDGE_ANIMATION_MIN_DELTA, EDGE_ANIMATION_MS, PhotoGrid
from gallery.ui.browser.model import PhotoListModel

#: 一次过渡切几帧。12 帧 ≈ 每 13ms 一帧，与真实 60Hz 刷新同量级，够把
#: 「一次跳变」与「一段过渡」在取样上分得开。
TRANSITION_FRAMES = 12

#: Qt **自己**那次推迟重排要等多久才落地（``ResizeMode.Adjust``，约 100ms）。
#:
#: :func:`settle_grid` 等不到它 —— 那不是网格的定时器，是 Qt 视图内部那个。盯
#: 「滚动位置不许被它夹走」的那两条（拖窗口、加条目）必须在它落地**之后**才断言，
#: 否则等于在重排还没发生时就宣布「没被夹走」，用例是空的。
QT_DEFERRED_RELAYOUT_SECONDS = 0.15


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


def largest_delta_that_keeps_columns(
    qt_app: QApplication, grid: PhotoGrid, *, limit: int = EDGE_ANIMATION_MIN_DELTA
) -> int:
    """最大的「边缘增量小于 ``limit`` 且不换列」的格宽增量。

    **为什么不能写死一个数**：格子宽度一变，排在第一行的格子数就可能跟着变，而列数一变
    就该走切档过渡（:attr:`~gallery.ui.browser.grid.GridTransition.reflows`）—— 那与
    边缘差多小无关。于是「变化太小所以不该有过渡」那条用例会在滚动条一改宽度、格子跨过
    换列边界时突然变红，而它量的根本不是自己声明的那一闸。

    那个边界有多贴脸：实测网格宽 1200 时 6 列恰好是「视口 ÷ 6」那么宽，``+3`` 还排得下
    6 列、``+4`` 就掉到 5 列了 —— 写死 ``+4`` 的那条用例正好骑在上面。把滚动条从 18
    收到 12（格宽 192 → 194）就把结论整个翻了过来。

    **返回之前网格已经复原**，所以调用方接着 :meth:`PhotoGrid._set_cell` 就行。
    """
    cell = grid.gridSize()
    columns = grid._first_row_columns()
    scroll = grid.verticalScrollBar().value()

    def columns_with(width: int) -> int:
        grid._apply_geometry(QSize(width, cell.height()), scroll)
        qt_app.processEvents()
        return grid._first_row_columns()

    for delta in range(limit - 1, 0, -1):
        kept = columns_with(cell.width() + delta) == columns
        columns_with(cell.width())  # 复原
        if kept:
            return delta
    raise AssertionError(
        f"宽度 {limit - 1} 到 1 之间没有一个增量能保住 {columns} 列 —— "
        f"这条守护没法表达「变化太小」这个前提"
    )


def grid_is_laid_out(grid: PhotoGrid) -> bool:
    """网格的**几何与版面**都落定了：没有过渡在跑，两个防抖定时器也都没在等。

    「在等」指的是 ``QTimer.isActive()`` —— 它们全是单次的，于是 ``isActive()``
    为假就等于「已经发过了」或「压根没发」。这里要等的是：
    :data:`grid.COLUMN_CHECK_MS` 的列数核对（50ms，它真的会改 ``_laid_out_columns``）
    与 ``_debounce`` 的可见区去抖（60ms）。

    **刻意不含 ``_edge_timer``**（:data:`grid.EDGE_SETTLE_MS` = 120ms，最长的那个）：
    它只影响 ``icon_edge_changed`` 这一个信号，而 ``tests/ui/browser/`` 里只有
    :func:`test_the_animation_never_reaches_the_thumbnail_pipeline` 在听那个信号，
    那边自己等（连着信号一起等，见那条用例）。把它算进来，等于让另外四十多条用例
    各自白等 70ms —— 而这七十毫秒买到的「初始那一次通知已经发过」对它们毫无意义。
    """
    return (
        grid._transition_anim.state() == QAbstractAnimation.State.Stopped
        and not grid._debounce.isActive()
        and not grid._examiner.isActive()
    )


def settle_grid(qt_app: QApplication, grid: PhotoGrid, *, timeout: float = 8.0) -> None:
    """把事件循环转到 :func:`grid_is_laid_out` 成立为止。

    **用来顶掉 ``settle(0.46)``。** 造好网格之后其实还有定时器在排队，50ms 就到头；
    睡满 0.46s 里有 0.4s 是在等一件早就发生完的事 —— 而这一份「等够 0.46s」在
    三十多个用例里各写了一遍，是这个目录最大的一笔空转。

    轮询循环本身不在这里重写：用 :func:`waiting.wait_until`，与全套测试同一份。
    """
    assert wait_until(qt_app, lambda: grid_is_laid_out(grid), timeout=timeout), (
        "网格在时限内没有落定（过渡仍在跑，或有防抖定时器还挂着）"
    )


def drive_transition[T](
    grid: PhotoGrid,
    read: Callable[[], T],
    *,
    frames: int = TRANSITION_FRAMES,
    spin: Callable[[], None] | None = None,
) -> list[T]:
    """把一次切档过渡按**确定的时间轴**跑完，逐帧记下 ``read()`` 的取值。

    **不靠真实时钟等动画。** ``QVariantAnimation`` 的时钟可以自己拨：
    ``setCurrentTime(ms)`` 会同步地算出那一帧的值并发 ``valueChanged``，而
    ``_on_transition_step`` 是直连的，于是 :attr:`PhotoGrid._delegate` 与滚动条
    当场就摆好了 —— 一整段 160ms 的过渡在测试里花不到一毫秒。

    这么做买到的不只是快：**每一帧都精确落在同一个进度上**。等真实时钟的话，
    取到几帧、落在哪里全看机器快慢（慢机器上可能一帧都取不到中间值），于是
    ``len(set(samples)) > 3`` 这类断言其实是在赌时序。

    首帧（``t=0``）在 :meth:`grid._start_transition` 里就已经摆出来了，所以取样从
    它开始；末帧拨到 ``duration`` 会让动画真的结束，于是 ``finished`` → 落位那一步
    也一并跑掉了 —— 记下来的最后一帧就是「跑完之后」的状态。

    **``spin`` 是给「过渡途中另有事件要送达」的场景准备的**（多半是
    ``qt_app.processEvents``）。不转事件循环的话，排着的事件一个也不会来 ——
    滚动条开关带来的那次 ``resizeEvent`` 就在那里面，于是「过没有渡被掐掉」这条
    断言会**空转绿**：掐它的那个事件压根没送到。给了 ``spin``，事件在确定的帧与帧
    之间送达，既保住了那条断言，又不必回去等真实时钟。

    **但转事件循环时必须先 ``pause()``：否则真实时钟会从缝里钻进来。**
    ``setCurrentTime`` 会把动画的内部定时器重新按在「还剩多少」上，于是「一帧」在
    真实时间里只剩 13ms；而 ``processEvents()`` 在并行跑（10 个 worker 抢 10 个核）
    时一次几十上百毫秒是常事。定时器于是照样到点，把这一帧直接推到终点再收尾 ——
    下一句 ``setCurrentTime`` 又把它拽回来，于是取到的序列里**混进一个不属于这一帧
    的值**，单调性之类的断言随机变红（实测 ``test_toggling_the_scrollbar_does_not_
    cut_the_transition_short`` 正是这么偶发失败的：某一帧是 130，下一帧又回到 137）。
    ``pause`` 之后真实时钟完全停摆，``setCurrentTime`` 照样能拨（Qt 对暂停中的动画
    也同步算值并发 ``valueChanged``），于是帧与帧之间只差**排着的事件**，时间轴回到
    确定。

    恢复只在这一段真的是自己 ``pause`` 出来的前提下做：``spin`` 里若恰好把过渡收了
    （那正是被测的行为），状态已经是 ``Stopped``，此时 ``resume()`` 会把动画**重新
    起跑**，反而把前提改掉了。
    """
    animation = grid._transition_anim
    assert animation.state() == QAbstractAnimation.State.Running, (
        "过渡没在跑，那就没有帧可取（前提没摆出来）"
    )

    def spin_once() -> None:
        if spin is None:
            return
        paused_here = animation.state() == QAbstractAnimation.State.Running
        if paused_here:
            animation.pause()
        try:
            spin()
        finally:
            if paused_here and animation.state() == QAbstractAnimation.State.Paused:
                animation.resume()

    seen = [read()]
    for index in range(1, frames):
        animation.setCurrentTime(EDGE_ANIMATION_MS * index // frames)
        spin_once()
        seen.append(read())
    animation.setCurrentTime(EDGE_ANIMATION_MS)
    spin_once()
    seen.append(read())
    return seen


def step_transition(grid: PhotoGrid, fraction: float) -> None:
    """把过渡的时钟拨到全程的 ``fraction`` 处（0.0 = 起手，1.0 = 跑完）。

    用来替代 ``settle(EDGE_ANIMATION_MS / 1000 / 3)`` 那种「等三分之一」——
    「撞上核对」要的是**动画恰好走到三分之一**，等出来的是「至少三分之一」，
    后者在慢机器上会落到更靠后的地方，撞的是另一段时序。
    """
    animation = grid._transition_anim
    animation.setCurrentTime(round(EDGE_ANIMATION_MS * fraction))


__all__ = [
    "QT_DEFERRED_RELAYOUT_SECONDS",
    "TRANSITION_FRAMES",
    "drive_transition",
    "fake_photos",
    "grid_is_laid_out",
    "largest_delta_that_keeps_columns",
    "make_grid",
    "settle_grid",
    "step_transition",
]
