"""还没落地的外部文件变化（ADR 0058 阶段 4）。

监听器一次报一批变化，而「现在这一层」可能正在被别的流程换掉 —— 收在这里，
等一个合适的时机（重排结束、回到网格页）再放出来。

原先那两个字段在 ``MainWindow`` / ``library_flow`` / ``watch_flow`` 里都被写过。
"""

from __future__ import annotations

from gallery.core.library.watcher import LibraryChange

__all__ = ["PendingWatch"]


class PendingWatch:
    """**暂存的外部变化**（ADR 0058 阶段 4）。

    两样东西：等一下再落地的那批变化、以及等重排做完再回过去的那个分组
    （``None`` = 没有要回去的地方）。
    """

    _watch_pending: tuple[LibraryChange, ...] | None
    _pending_group: str | None

    def __init__(self) -> None:
        self._watch_pending = None
        self._pending_group = None

    def take(self) -> tuple[LibraryChange, ...] | None:
        """**取走暂存的那批**并清空 —— 还没有就返回 ``None``。

        清空与返回要原子：还回去的话下一次 :meth:`put` 会把它叠在自己身上。
        """
        changes, self._watch_pending = self._watch_pending, None
        return changes

    def put(self, changes: tuple[LibraryChange, ...]) -> None:
        """**先存起来** —— 现在不是落地的时候（这一层正在被换掉）。"""
        waiting = self._watch_pending or ()
        self._watch_pending = (*waiting, *changes)

    def forget_group(self) -> None:
        """**别再回那个分组了** —— 它不在了就留在这一层，由横幅说明为什么。"""
        key, self._pending_group = self._pending_group, None
        return key  # type: ignore[return-value]

    def remember_group(self, key: str) -> None:
        """**重排完之后回到刚才那个组**。"""
        self._pending_group = key

    def has_pending(self) -> bool:
        """**现在暂存着东西吗** —— 换库前问一句，有就别带进下一个库。"""
        return self._watch_pending is not None

    def forget_all(self) -> None:
        """**全丢掉** —— 换库 / 关窗时调。上一个库的路径对下一个库没有意义。"""
        self._watch_pending = None
        self._pending_group = None
