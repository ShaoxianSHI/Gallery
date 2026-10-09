"""暂存区：扫出来的条目到排好顺序之间的那一格（ADR 0058 阶段 5）。

**它不是「每层各自的暂存」** —— ADR 0058 原先猜错了，实测代码否掉了那个猜测：
七个写入点是**同一条链上的先后步骤**（扫描 / 收窄产出条目，然后排序算完顺序），
不是互相竞争 —— 界面是单线程的，一次只有一遍扫描在跑。
"""

from __future__ import annotations

from gallery.core.library.photos import GridEntry

__all__ = ["Staging"]


class Staging:
    """**扫出来的条目暂存在这里，等顺序定下来**（ADR 0058 阶段 5）。

    ``None`` = 已排好（界面上摆的就是当前顺序）；一个列表 = 这一层还没排好，
    顺序要等 :meth:`~gallery.ui.window.ordering.OrderingMixin._reorder_level` 算出来。
    """

    _staged: list[GridEntry] | None

    def __init__(self) -> None:
        self._staged = None

    def stage(self, entries: list[GridEntry]) -> None:
        """**这一层要重扫**：先把暂存区清空，别先摆一屏旧顺序。"""
        self._staged = entries

    def take(self) -> list[GridEntry] | None:
        """**取走暂存的那些**并回到「已排好」；没有就返回 ``None``。

        取走与置空要原子 —— 还回去的话下一次 :meth:`stage` 会把它叠在自己身上。
        """
        entries, self._staged = self._staged, None
        return entries

    def peek(self) -> list[GridEntry] | None:
        """**看一眼但不动它** —— 收窄要在暂存区上做，不能把它取走。"""
        return self._staged

    @property
    def is_staging(self) -> bool:
        """**现在这一层还没排好吗** —— 「这一层空不空」不该用暂存区判（见 ``library_flow``）。"""
        return self._staged is not None

    def extend(self, entries: list[GridEntry]) -> None:
        """**扫到一批就往暂存区里加** —— 流式出图，不等整层扫完。

        没在暂存（已经排好了）就**先开一个空的**：调用点判的是「这一层要不要重排」，
        而流式扫描来的时候答案一定是「要」。
        """
        if self._staged is None:
            self._staged = []
        self._staged.extend(entries)
