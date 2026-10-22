"""放映中的位置：当前这一层是哪一批照片、正播到第几张。

**这一份只记「放到哪儿了」**，自动播放的计时器与画面切换都在
:mod:`gallery.ui.window.slideshow_flow` 里。照片按**路径**记而不是行号：放映途中
列表可能因为外部改动重新排过，行号会指向另一张照片。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["SlideshowState"]


@dataclass(slots=True)
class SlideshowState:
    """一次放映的范围与进度。"""

    paths: tuple[Path, ...] = ()
    index: int = 0
    active: bool = False
    paused: bool = False

    def start(self, paths: tuple[Path, ...], index: int = 0) -> None:
        """从这一批照片的第 ``index`` 张开始。"""
        if not paths:
            self.stop()
            return
        self.paths = paths
        self.index = min(max(index, 0), len(paths) - 1)
        self.active = True
        self.paused = False

    def stop(self) -> None:
        """结束放映并清掉进度。"""
        self.paths = ()
        self.index = 0
        self.active = False
        self.paused = False

    def current(self) -> Path | None:
        """正放到哪一张。"""
        if not self.active or not self.paths:
            return None
        return self.paths[self.index]

    def step(self, delta: int) -> Path | None:
        """往前 / 往后走一张，**到头会绕回另一端**。"""
        if not self.active or not self.paths:
            return None
        self.index = (self.index + delta) % len(self.paths)
        return self.paths[self.index]
