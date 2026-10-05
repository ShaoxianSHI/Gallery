"""缩略图请求的排队策略（不依赖 Qt）。

``QThreadPool`` 是 FIFO 的，但用户的期待不是：滚动到哪儿，哪儿就该先出图。这里维护两档
优先级（可见 / 预取）与按路径去重，``set_window`` 把队列裁剪到「当前看得见 + 即将看见」的
范围 —— 快速滚动时不会积压几千个过时的请求。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

#: 优先级的数值越小越先出图。
VISIBLE: Final = 0
PREFETCH: Final = 1


class ThumbnailQueue:
    """当前窗口内的缩略图请求。

    同一路径只会存在一个请求；重复加入会把它提升到给定优先级，并保持原加入顺序。
    """

    def __init__(self) -> None:
        self._pending: dict[Path, int] = {}

    def add(self, path: Path, *, priority: int = PREFETCH) -> bool:
        """加入一个请求。返回 ``True`` 表示这是新请求，``False`` 表示已存在（仅更新优先级）。"""
        existing = self._pending.get(path)
        if existing is not None:
            if priority < existing:
                self._pending[path] = priority
            return False
        self._pending[path] = priority
        return True

    def set_window(self, visible: Sequence[Path], *, prefetch: Iterable[Path] = ()) -> None:
        """把队列替换成当前窗口：``visible`` 优先，``prefetch`` 次之，其余全部丢弃。"""
        window: dict[Path, int] = {}
        for path in visible:
            window[path] = VISIBLE
        for path in prefetch:
            window.setdefault(path, PREFETCH)
        self._pending = window

    def pop_next(self) -> Path | None:
        """取出下一个该处理的路径：先可见、后预取，同档内按加入顺序。"""
        best: Path | None = None
        best_priority = PREFETCH + 1
        for path, priority in self._pending.items():
            if priority < best_priority:
                best, best_priority = path, priority
                if priority == VISIBLE:
                    break
        if best is None:
            return None
        del self._pending[best]
        return best

    def discard(self, path: Path) -> None:
        """丢弃某个请求（例如它的任务已经在跑，或用户已经滚过去了）。"""
        self._pending.pop(path, None)

    def clear(self) -> None:
        self._pending.clear()

    def __len__(self) -> int:
        return len(self._pending)

    def __contains__(self, path: object) -> bool:
        return path in self._pending


__all__ = ["PREFETCH", "VISIBLE", "ThumbnailQueue"]
