"""日历这一侧的小幅缩略图仓库。

与网格那份（``browser/model.py`` 的 ``_pixmaps``）是同一个做法：LRU 记账，
缩略图由 :class:`~gallery.ui.thumbs.pipeline.ThumbnailPipeline` 在工作线程里缩好
送回来（ADR 0015），这里只存不缩。

**按 ``(路径, 边长)`` 记账**：日历三层要的大小差得很远（年视图一格二十几像素、
周视图一格一百七十像素），同一张照片会在两个档位上各要一次 —— 尺寸对不上就是没出图。

**换档不清空**：曾经换一档就把整份扔掉，于是 ``Y`` / ``M`` / ``W`` 来回按几遍，
每次都把同一批照片重新解码一遍。实测那正是「日历缩略图出得慢」的一半原因
（另一半是刚开库时线程池正被全库元数据占着，而解码本身只要一毫秒）。
现在两个档位各自留一份，翻回上一层立刻就有图；内存那点差别（每档几百张
几百 KB）换来的是「翻层不用等」。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtGui import QImage

from gallery.ui.imaging import scale_image

#: 每档留多少张。日历一屏最多几百格，一千张也就几十兆。
DEFAULT_CAPACITY = 1024

#: 同时留几个档位。三个层各一个，够了；再多的几乎用不上（缩略图缓存还在磁盘上）。
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
        """换档：**只换看哪一份**，不清空。

        清空是「宁可空一帧再补齐」的老做法，代价是每次翻层都要重解码一遍；
        而按 ``(路径, 边长)`` 记着账之后，补齐其实根本不必 —— 旧档那些仍然是对的，
        只是现在不用它。
        """
        edge = max(0, int(edge))
        if edge == self._edge:
            return
        self._edge = edge
        self._evict_extra_edges()

    def _evict_extra_edges(self) -> None:
        """留不下那么多档时，扔掉**离当前档最远**的那几档（仍在用的最后才扔）。"""
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

    def has(self, path: Path) -> bool:
        return (path, self._edge) in self._images

    def forget(self, paths: object) -> None:
        """这些路径上的文件变了：所有档位上的那几份都忘掉。

        与 :meth:`~gallery.ui.thumbs.pipeline.ThumbnailPipeline.forget` 配对。
        """
        for path in paths if isinstance(paths, (list, tuple, set, frozenset)) else (paths,):
            target = Path(path)  # type: ignore[arg-type]
            for key in [key for key in self._images if key[0] == target]:
                self._images.pop(key, None)

    def clear(self) -> None:
        self._images.clear()


__all__ = ["DEFAULT_CAPACITY", "DEFAULT_EDGES", "ThumbStore"]
