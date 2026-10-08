"""日历这一侧的小幅缩略图仓库（LRU 记账，缩略图由流水线在工作线程里缩好送回来，ADR 0015）。

**按 ``(路径, 边长)`` 记账**：三层要的尺寸差得很远，同一张照片会在两个档位上各要一次；
**换档不清空** —— 清空会让 ``Y`` / ``M`` / ``W`` 来回翻时把同一批照片重新解码一遍。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtGui import QImage

from gallery.ui.imaging import scale_image

#: 每档留多少张。日历一屏最多几百格，一千张也就几十兆。
DEFAULT_CAPACITY = 1024

#: 同时留几个档位（三层各一个；再多几乎用不上，缩略图缓存还在磁盘上）。
DEFAULT_EDGES = 3

_Key = tuple[Path, int]


class ThumbStore:
    """按 ``(路径, 边长)`` 存的小幅缩略图，多档各一份，带 LRU 淘汰。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY, edges: int = DEFAULT_EDGES) -> None:
        self._images: OrderedDict[_Key, QImage] = OrderedDict()
        self._capacity = max(1, capacity) * max(1, edges)
        self._edges = max(1, edges)
        self._edge = 0

    def set_edge(self, edge: int) -> None:
        """换档：**只换看哪一份**，不清空（旧档那些仍然是对的，只是现在不用它）。"""
        edge = max(0, int(edge))
        if edge == self._edge:
            return
        self._edge = edge
        self._evict_extra_edges()

    def _evict_extra_edges(self) -> None:
        """留不下那么多档时，**只按边长留最小的 :attr:`_edges` 档**。

        **不看当前档，也不保正在用的那一档** —— 代码只按边长升序排、留前 N 档。次序上
        :meth:`set_edge` 也是**先**淘汰、**后**换当前档，所以刚切过去的那一档可能已经被上一轮
        淘汰掉了；要改这个策略得连着 :meth:`set_edge` 一起改。默认三档下走不到这里。
        """
        by_edge: dict[int, list[_Key]] = {}
        for key in self._images:
            by_edge.setdefault(key[1], []).append(key)
        for edge in sorted(by_edge, reverse=True)[self._edges :]:
            for key in by_edge[edge]:
                self._images.pop(key, None)

    @property
    def edge(self) -> int:
        return self._edge

    def set(self, path: Path, image: QImage, edge: int = 0) -> None:
        target = edge if edge > 0 else self._edge
        if edge <= 0 and self._edge > 0:
            image = scale_image(image, self._edge)
        key = (path, target)
        self._images[key] = image
        self._images.move_to_end(key)
        while len(self._images) > self._capacity:
            self._images.popitem(last=False)

    def get(self, path: Path) -> QImage | None:
        """当前档上有没有这一张；没有就是还没出图（宁可空着等重取）。"""
        return self._images.get((path, self._edge))

    def forget(self, paths: Iterable[Path]) -> None:
        """这些路径上的文件变了：所有档位上的那几份都忘掉（与流水线的 ``forget`` 配对）。

        收**真的** ``Iterable``，而不是靠 ``isinstance`` 猜来的是列表还是单个路径：传一个**生成器**
        （最自然的写法）时按类型分流会把它当成单个路径交给 ``Path()``。
        """
        dropped = {Path(path) for path in paths}
        # **一次遍历过滤掉，不要「每个路径扫一遍全表」**：后者是 O(路径数 × 仓库条目数)，而
        # 仓库容量是 1024 × 3 = 3072 项，代价只与**仓库有多大**成正比。
        if dropped:
            for key in [key for key in self._images if key[0] in dropped]:
                self._images.pop(key, None)

    def clear(self) -> None:
        self._images.clear()


__all__ = ["DEFAULT_CAPACITY", "DEFAULT_EDGES", "ThumbStore"]
