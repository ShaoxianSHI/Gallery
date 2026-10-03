"""缩略图尺寸档位，以及把连续手势攒成离散档位的累加器。

**为什么是三个离散档位而不是连续缩放**：缩略图磁盘缓存按固定长边生成，
离散档位能让「同一张照片」的缓存组合保持少量且稳定；连续缩放则会逼出
「同一个尺寸系数对应一份缓存」的爆炸，或者干脆永远糊一层。

**最大档不得超过缩略图缓存的长边**（``DEFAULT_THUMBNAIL_MAX_EDGE``），
否则界面等于把小图拉大。这条不变量由测试守着。

捏合与滚轮都是连续事件（一次手势会来十几个小增量），直接每来一个增量就切一档
会瞬间从最小跳到最大，所以用 :class:`StepAccumulator` 攒够阈值才切一档。

**光有阈值还不够，捏合还得多一道最小间隔**（:class:`RateLimitedAccumulator`）：
手势快的时候，两三个事件就能攒够一个阈值，于是切档频率比切档动画还快，
而网格是「上一段动画没跑完就不接第二段、直接落位」的（见
``grid.PhotoGrid._start_transition``）—— 于是每一次切档都掐断上一次的动画，
画面就是一连串的跳变，什么也看不出来。

这里刻意不导入 Qt：时钟是注入进来的（:func:`monotonic_ms`），好让这套逻辑能用
普通 pytest 覆盖。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

#: 三档缩略图长边（像素），从最小到最大。均不超过 512 的缓存长边。
THUMBNAIL_SIZES: Final[tuple[int, ...]] = (112, 176, 288)

#: 与 :data:`THUMBNAIL_SIZES` 一一对应的中文档位名。
THUMBNAIL_LABELS: Final[tuple[str, ...]] = ("小", "中", "大")

#: 默认档位：中间的「中」，也就是查看器一直以来的尺寸。
DEFAULT_INDEX: Final = 1

#: 默认缩略图长边。
DEFAULT_EDGE: Final = THUMBNAIL_SIZES[DEFAULT_INDEX]

#: 触控板捏合的切换阈值（原生手势事件的 value 累计到这个量就切一档）。
PINCH_THRESHOLD: Final = 0.1

#: 捏合两次切档之间的**最小间隔**（毫秒），不足则把这一下的增量整个丢掉。
#:
#: **必须不小于切档动画的时长**（``grid.EDGE_ANIMATION_MS``），否则限速形同虚设：
#: 手势快时两次切档之间仍可能短于一段动画，动画照样被掐断。这条不变量由
#: ``tests/ui/browser/test_edge_animation.py`` 守着。留出的余量是给「动画刚跑完、
#: 落位那一帧还没画出去」的一点空间。
MIN_PINCH_INTERVAL_MS: Final = 220

#: ⌘+滚轮的切换阈值：一格传统滚轮是 120，触控板则是一连串小增量。
WHEEL_THRESHOLD: Final = 120.0

#: 缩略图长边的下限。窗口小到离谱时也不至于缩成看不清的一点。
MIN_EDGE: Final = 48

#: 列数搜索的上限。超过这个列数再细也只是浪费循环。
MAX_COLUMNS: Final = 64


def fit_columns(available: int, target_cell: int, *, max_columns: int = MAX_COLUMNS) -> int:
    """算出「一行铺满可用宽度」时该分成几列。

    目标不是死守某个格子宽度，而是在铺满的前提下让格子宽度尽量贴近 ``target_cell``：
    格子会被拉伸到 ``available // columns``，因此只要列数选得好，右侧就不会留下空白。
    """
    if available <= 0 or target_cell <= 0:
        return 1
    limit = max(1, min(max_columns, available))
    best_columns = 1
    best_delta: int | None = None
    for columns in range(1, limit + 1):
        delta = abs(available // columns - target_cell)
        if best_delta is None or delta < best_delta:
            best_columns, best_delta = columns, delta
    return best_columns


def clamp_index(index: int) -> int:
    """把档位序号夹到合法范围。"""
    return max(0, min(len(THUMBNAIL_SIZES) - 1, index))


def edge_for_index(index: int) -> int:
    """档位序号 → 缩略图长边。"""
    return THUMBNAIL_SIZES[clamp_index(index)]


def label_for_index(index: int) -> str:
    """档位序号 → 中文档位名。"""
    return THUMBNAIL_LABELS[clamp_index(index)]


def shifted_index(index: int, step: int) -> int:
    """在档位之间移动 ``step`` 档，越界即停在两端。"""
    return clamp_index(clamp_index(index) + step)


def nearest_index(edge: int | None) -> int:
    """把任意长边（例如上次会话记下来的值）归到最近的档位。"""
    if edge is None:
        return DEFAULT_INDEX
    return min(range(len(THUMBNAIL_SIZES)), key=lambda index: abs(THUMBNAIL_SIZES[index] - edge))


def describe(index: int) -> str:
    """刚切档时的提示：说出档位与目标长边。"""
    return f"缩略图：{label_for_index(index)}（目标 {edge_for_index(index)} 像素）"


def describe_actual(index: int, edge: int) -> str:
    """自适应排完之后的提示：目标只是目标，实际长边由窗口宽度决定。"""
    return f"缩略图：{label_for_index(index)} · 实际 {edge} 像素"


class StepAccumulator:
    """把连续的手势/滚轮增量攒成离散的档位切换。

    正数表示「放大」（更大的缩略图），负数表示「缩小」。
    """

    __slots__ = ("_threshold", "_accumulated")

    def __init__(self, threshold: float) -> None:
        if threshold <= 0:
            raise ValueError("阈值必须为正数")
        self._threshold = threshold
        self._accumulated = 0.0

    @property
    def accumulated(self) -> float:
        return self._accumulated

    def feed(self, delta: float) -> int:
        """喂入一个增量，返回本次应切换的档数（通常为 0，也可能是 ±1 或更多）。"""
        if delta != delta:  # NaN：忽略，否则累加器会被永久污染
            return 0
        self._accumulated += delta
        steps = 0
        while self._accumulated >= self._threshold:
            self._accumulated -= self._threshold
            steps += 1
        while self._accumulated <= -self._threshold:
            self._accumulated += self._threshold
            steps -= 1
        return steps

    def reset(self) -> None:
        self._accumulated = 0.0


def monotonic_ms() -> int:
    """单调时钟的毫秒数。

    :class:`RateLimitedAccumulator` 默认就用它。做成函数是为了能注入 ——
    限速这种东西不塞一个假时钟就没法测（真等 220 毫秒的用例既慢又不稳）。
    """
    return time.monotonic_ns() // 1_000_000


class RateLimitedAccumulator(StepAccumulator):
    """攒器外面再套一道**最小间隔**：间隔不足的增量整个丢掉。

    捏合用手势是因为它本来就是一串连绵不断的增量，快起来时两三个事件就够一个
    阈值。而网格那一侧「上一段切档动画没跑完就不接第二段」（见
    ``grid.PhotoGrid._start_transition``），于是越快越糟：每一次切档都把上一次的
    动画掐断，画面就是一连串跳变 —— 慢速时看得到的那个动画，快起来一帧都看不见。

    **间隔内丢掉而不是攒着**，与攒器本身的取舍正好相反，也是这两条手势路径的分界：

    - 捏合走这里。捏合的意图是连续的（手停在哪就是哪），多出来的那几下是同一
      个意图的重复表达，丢掉不损失信息。
    - 快捷键与 ⌘+滚轮**不走这里** —— 那是每一下都独立的意图（见
      ``main_window.MainWindow.step_thumbnail_size`` 的排队），丢掉一下就是丢了一次
      明确的操作。

    首次喂入没有间隔（还没有「上一次」可与此刻比），所以打开界面直接捏一下立刻生效。
    """

    __slots__ = ("_clock", "_min_interval_ms", "_last_step_at")

    def __init__(
        self,
        threshold: float,
        min_interval_ms: int,
        clock: Callable[[], int] = monotonic_ms,
    ) -> None:
        super().__init__(threshold)
        if min_interval_ms < 0:
            raise ValueError("最小间隔不能为负数")
        self._min_interval_ms = min_interval_ms
        self._clock = clock
        #: 上一次**真的切了档**的时刻（毫秒）；还没切过时是 ``None``。
        self._last_step_at: int | None = None

    def feed(self, delta: float) -> int:
        """喂入一个增量，返回本次应切换的档数。

        与父类的唯一区别：**间隔不足时直接返回 0**，那一下的增量既不累加也不切档。
        """
        now = self._clock()
        if self._last_step_at is not None and now - self._last_step_at < self._min_interval_ms:
            return 0  # 间隔不足：这一下整个丢掉，既不切档也不进累加器
        steps = super().feed(delta)
        if steps:
            self._last_step_at = now
        return steps

    def reset(self) -> None:
        """忘掉累计量与上一次切档的时刻（回到「随时可以切第一档」的状态）。"""
        super().reset()
        self._last_step_at = None


__all__ = [
    "DEFAULT_EDGE",
    "DEFAULT_INDEX",
    "MAX_COLUMNS",
    "MIN_EDGE",
    "MIN_PINCH_INTERVAL_MS",
    "PINCH_THRESHOLD",
    "THUMBNAIL_LABELS",
    "THUMBNAIL_SIZES",
    "WHEEL_THRESHOLD",
    "RateLimitedAccumulator",
    "StepAccumulator",
    "clamp_index",
    "describe",
    "describe_actual",
    "edge_for_index",
    "fit_columns",
    "label_for_index",
    "monotonic_ms",
    "nearest_index",
    "shifted_index",
]
