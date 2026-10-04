"""日历这一侧的小幅缩略图仓库。

与网格那份（``browser/model.py`` 的 ``_pixmaps``）是同一个做法：按 ``(路径, 边长)``
记账的 LRU，缩略图由 :class:`~gallery.ui.thumbs.pipeline.ThumbnailPipeline` 在工作
线程里缩好送回来（ADR 0015），这里只存不缩。

**边长要记账，不能只按路径存**：日历的四个层要的大小差得很远（年视图一格二十几像素、
日视图一格一百多像素），而流水线会把在途的结果作废重取。若不记账，一格用 28 像素的
图去填 112 像素的格子会被糊掉，且糊得「很稳定」—— 那是最难发现的一种错。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtGui import QImage

from gallery.ui.imaging import scale_image

#: 内存里留多少张。日历一屏最多几百格，留一千张也就几十兆。
DEFAULT_CAPACITY = 1024


class ThumbStore:
    """按路径存的小幅缩略图，带边长记账与 LRU 淘汰。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._images: OrderedDict[Path, tuple[QImage, int]] = OrderedDict()
        self._capacity = max(1, capacity)
        self._edge = 0

    def set_edge(self, edge: int) -> None:
        """换档：清空旧的那一份。

        **必须清**而不是留着按边长挑：留着的那些在换档后立刻过时，而日历要的是
        「每一格都拿得出一张对得上尺寸的图」，宁可先空一帧再补齐。
        """
        edge = max(0, int(edge))
        if edge == self._edge:
            return
        self._edge = edge
        self._images.clear()

    @property
    def edge(self) -> int:
        return self._edge

    def set(self, path: Path, image: QImage, edge: int = 0) -> None:
        sized = edge if edge > 0 else self._edge
        if edge <= 0 and self._edge > 0:
            image = scale_image(image, self._edge)
        self._images[path] = (image, sized)
        self._images.move_to_end(path)
        while len(self._images) > self._capacity:
            self._images.popitem(last=False)

    def get(self, path: Path) -> QImage | None:
        found = self._images.get(path)
        if found is None:
            return None
        if found[1] != self._edge:
            # 尺寸对不上就是没出图（换档后在途的那几张）：宁可空着等重取。
            return None
        return found[0]

    def has(self, path: Path) -> bool:
        return self.get(path) is not None

    def forget(self, paths: object) -> None:
        """这些路径上的文件变了：忘掉手里那一份（与 ``ThumbnailPipeline.forget`` 配对）。"""
        for path in paths if isinstance(paths, (list, tuple, set, frozenset)) else (paths,):
            self._images.pop(Path(path), None)  # type: ignore[arg-type]

    def clear(self) -> None:
        self._images.clear()


__all__ = ["DEFAULT_CAPACITY", "ThumbStore"]
