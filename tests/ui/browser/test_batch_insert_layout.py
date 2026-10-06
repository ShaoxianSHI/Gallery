"""监听器往库里丢一批新文件时，网格与模型**不许按张数付钱**。

「一批变化只排一次版」那条规矩在这里的落法：监听器一次报一批新文件，模型一条一条地插
（每条一次 ``rowsInserted``），而插入引起的重排**每次都是全量排版** —— 于是那一批的代价
变成「张数 × 全量排版」（三万张的库里插 20 行就是 23.9 ms/行，访达丢 200 张近 5 秒的
主线程冻死），而其中绝大部分是重复劳动：那一批要排的版是**同一份**。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

# ``qt_app`` / ``settle`` 来自 ``conftest``，**别在这里 import**（共享基础设施只有一份，
# 见 tests/ui/conftest.py 里那两条守护）。
from grid_helpers import fake_photos, make_grid, settle_grid  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def test_a_batch_of_inserts_lays_out_once_not_once_per_photo(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """**一批插入只排一次版。**

    监听器报一批新文件时，模型是**一条一条**插的（每条一次 ``rowsInserted``）—— 合并的是
    那一批，不是「批与批之间」。所以这里也逐条插：一批里插 20 条而只排一次版，
    改前是 20 次全量排版（三万张的库里 23.9 ms/行，访达丢 200 张近 5 秒冻死）。

    断的是「排版被调了几次」，不是排出来对不对（后者由
    ``test_edge_animation.py::test_new_photos_arriving_do_not_lose_the_scroll_position``
    守着）。用计数器而不断言时间：量时间会随机器飘。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    settle_grid(qt_app, grid)
    assert bar.value() > 0, "前提不成立：停在最顶上时不花排版这笔钱"

    calls: list[int] = []
    real = grid._relayout_after_insert

    def counted() -> None:
        calls.append(1)
        real()

    grid._relayout_after_insert = counted  # type: ignore[method-assign]

    for photo in fake_photos(20, tmp_path / "新来的"):
        grid.model().insert_entry(photo, whole_level=True)
    settle(0.02)  # 只够让那个 0 ms 定时器触发一次，不必等 Qt 自己的推迟重排

    assert calls, "前提不成立：重排压根没触发（0 ms 定时器没等到事件循环）"
    assert len(calls) == 1, f"一批里逐条插了 20 次，排了 {len(calls)} 次版（该是 1 次）"
    assert grid.model().rowCount() == 420, "前提不成立：条目没都进来"


def test_the_relayout_anchor_is_taken_when_the_insert_happens(
    qt_app: QApplication, tmp_path: Path, settle: Callable[[float], None]
) -> None:
    """**基准在插入那一刻读**，不是定时器里读。

    Qt 紧接着就会从第 0 条重排一次，那时 ``bar.value()`` 已经是那个被夹小的值
    （400 张的库里 7037 → 1642）。拿它去重排就是拿错的基准去修 —— 而错基准与
    「压根没修」在断言上完全一样，所以只能靠钉住取值的时机来防。

    断的是 :meth:`_apply_geometry` **收到哪个数**，不是滚动条最后落在哪儿 ——
    后者由上面那条用例守着。
    """
    grid = make_grid(qt_app, tmp_path, 400)
    settle_grid(qt_app, grid)
    bar = grid.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    settle_grid(qt_app, grid)
    expected = bar.value()

    seen: list[int] = []
    real = grid._apply_geometry

    def spy(cell: object, scroll: int) -> None:
        seen.append(scroll)
        real(cell, scroll)  # type: ignore[arg-type]

    grid._apply_geometry = spy  # type: ignore[method-assign]

    grid.model().insert_entry(fake_photos(1, tmp_path / "新来的")[0], whole_level=True)
    settle(0.02)

    assert seen == [expected], (
        f"重排用的是 {seen}，插入那一刻的位置是 {expected}"
        f" —— 基准取晚了（Qt 已把滚动条夹成 {bar.value()}）"
    )
