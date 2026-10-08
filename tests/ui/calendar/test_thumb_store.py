"""``ThumbStore`` 的记账与淘汰策略。

它按 ``(路径, 边长)`` 存小幅缩略图，日历三层各要一档（年 48 / 周 192 / 月 288），
而**多档各留一份、换档不清空** —— 清掉的话 ``Y``/``M``/``W`` 来回翻时会把同一批照片
重新解码一遍。

**每条用例都要 ``qt_app``**，哪怕一个控件都不摆：``QImage`` 仍然要 ``QApplication`` 在，
而 ``conftest`` 里那个 autouse 的 ``qt_teardown`` **依赖 ``qt_app``** —— 不声明它的用例
拿不到确定性的收尾（控件会活到解释器收尾去碰运气，症状是用例全绿而进程最后 SIGSEGV）。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.calendar.thumbs import DEFAULT_EDGES, ThumbStore  # noqa: E402

#: 日历三层各自的档位。
YEAR_EDGE, WEEK_EDGE, MONTH_EDGE = 48, 192, 288


def _image() -> QImage:
    return QImage(8, 6, QImage.Format.Format_RGB32)


def test_every_edge_keeps_its_own_copy(qt_app: QApplication, tmp_path: Path) -> None:
    """换档不清空：三个档位各留一份，来回翻不用重新解码。"""
    store = ThumbStore()
    photo = tmp_path / "一张.jpg"

    for edge in (YEAR_EDGE, WEEK_EDGE, MONTH_EDGE):
        store.set_edge(edge)
        store.set(photo, _image(), edge)
        assert store.get(photo) is not None, f"{edge} 那一档没出图"

    store.set_edge(YEAR_EDGE)
    assert store.get(photo) is not None


def test_the_default_keeps_exactly_as_many_edges_as_the_calendar_has(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**默认配置下淘汰分支走不到**：日历三层三档，而 :data:`DEFAULT_EDGES` 正是 3。

    锁定这个前提 —— 一旦有人把日历的档位数改了（比如加一档），这一档就该跟着动，
    而 :meth:`ThumbStore._evict_extra_edges` 那个「不看当前档」的策略就会开始咬人
    （见下一条）。
    """
    assert DEFAULT_EDGES == 3
    store = ThumbStore()
    photo = tmp_path / "一张.jpg"

    for edge in (YEAR_EDGE, WEEK_EDGE, MONTH_EDGE):
        store.set_edge(edge)
        store.set(photo, _image(), edge)
        assert store.get(photo) is not None, f"{edge} 那一档被淘汰掉了"


def test_eviction_drops_the_largest_edges_and_may_drop_the_one_in_use(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**淘汰只按边长，留最小的几档 —— 不看当前档，也不保正在用的那一档。**

    原先那句 docstring 写的是「扔掉离当前档最远的、仍在用的最后才扔」，那是一个
    **没有实现**的策略：代码只按边长升序排、留前 N 档。照它去改代码的人会以为已有保护。

    顺带锁定次序问题：:meth:`set_edge` 是**先**淘汰、**后**换当前档，所以刚切过去的
    那一档可能已经被上一轮淘汰掉了（这里存 48/192/288、只留 2 档，切到 48 时它已经不在）。
    """
    store = ThumbStore(capacity=1, edges=2)  # 只留两档
    photo = tmp_path / "一张.jpg"
    for edge in (YEAR_EDGE, WEEK_EDGE, MONTH_EDGE):
        store.set_edge(edge)
        store.set(photo, _image(), edge)

    store.set_edge(YEAR_EDGE)  # 切到最小那一档

    assert store.get(photo) is None, "刚切过去的那一档已被上一轮淘汰掉了"
    # 被留下的是边长中等的几档 —— **不是「离当前档最远的那几档」**。
    store.set_edge(WEEK_EDGE)
    assert store.get(photo) is not None


def test_forget_takes_any_iterable_not_just_the_four_container_types(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**收真的 ``Iterable``** —— 传生成器（最自然的写法）不许抛异常。

    缺陷现场：原先签名是 ``paths: object``，用 ``isinstance(paths, (list, tuple, set,
    frozenset))`` 分流「一批」还是「单个」。而 :meth:`page.CalendarPage.forget_thumbnails`
    的签名承诺 ``Iterable[Path]`` 并原样透传，于是传一个生成器时两个分支都不成立，
    那个生成器被当成单个路径交给 ``Path()`` —— **直接抛 TypeError**。
    """
    store = ThumbStore()
    store.set_edge(WEEK_EDGE)  # ``get`` 只查当前档，所以要先把当前档定下来
    image = _image()
    photos = [tmp_path / f"{index}.jpg" for index in range(3)]
    for photo in photos:
        for edge in (YEAR_EDGE, WEEK_EDGE):
            store.set(photo, image, edge)

    store.forget(photo for photo in photos if photo != photos[-1])  # 生成器，不是 list

    assert store.get(photos[0]) is None, "生成器里的路径没有被作废"
    assert store.get(photos[-1]) is not None, "不在生成器里的那个不该被动"


class _CountingStore(OrderedDict):  # type: ignore[type-arg]
    """数「仓库被完整遍历了几次」。

    继承 ``OrderedDict``（不是 ``dict``）：``ThumbStore.set`` 要用 ``move_to_end`` 与
    ``popitem(last=False)`` 做 LRU 淘汰，换成普通 dict 就 ``AttributeError``。

    ``ThumbStore.forget`` 的两种写法在这上面差别是**整数倍**、而不是「快一点慢一点」：

    - 改前：外层遍历路径、内层扫全表 → 传 N 条就把 ``_images`` 走 N 遍；
    - 改后：路径先收成集合、单趟过滤 → 恒走 1 遍。

    所以量**遍历次数**（形状），不量耗时。上一版这里量的是耗时比，于是并行跑的时候
    ``forget(1)`` 碰上一次调度抖动就误红 —— 明明形状没变，机器一忙就挂。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.scans = 0

    def __iter__(self) -> Iterator[Any]:
        self.scans += 1
        return super().__iter__()


def test_forget_walks_the_store_once_no_matter_how_many_paths(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """**一次遍历过滤掉，不「每个路径扫一遍全表」。**

    缺陷现场：原先是「外层遍历路径、内层扫全表」，代价是 O(路径数 × 仓库条目数)。
    而仓库容量是 1024 × 3 = 3072 项，于是云盘同步落一批（200 张）要在主线程上走
    200 × 3072 ≈ 61 万次比较（实测 53 ms）。收齐成集合一次过滤之后，代价只与**仓库
    有多大**成正比 —— 实测同一个满仓库 200 条从 53 ms 降到 0.76 ms。

    断的是**形状**：无论给 1 条还是 200 条，仓库都只许被走一遍。
    """
    del qt_app
    store = ThumbStore()
    store._images = _CountingStore()  # type: ignore[assignment]
    image = _image()
    for index in range(1000):
        store.set(tmp_path / f"已有{index}.jpg", image, WEEK_EDGE)

    def scans_for(count: int) -> int:
        targets = [tmp_path / f"新来{index}.jpg" for index in range(count)]
        before = store._images.scans
        store.forget(iter(targets))  # 传生成器：契约收的是 Iterable
        return store._images.scans - before

    one = scans_for(1)
    many = scans_for(200)
    assert one == 1, f"给 1 条路径却把仓库走了 {one} 遍"
    assert many == 1, (
        f"给 200 条路径把仓库走了 {many} 遍 —— 代价还在随路径数线性涨（每个路径扫了一遍全表）"
    )
