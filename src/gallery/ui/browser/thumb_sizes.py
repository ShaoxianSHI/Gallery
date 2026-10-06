"""缩略图尺寸档位，以及把连续手势攒成离散档位的累加器。

**三个离散档位而不是连续缩放**：磁盘缓存按固定长边生成，离散档位让同一张照片的缓存
组合保持少量稳定，最大档也不得超过缓存的长边，否则等于把小图拉大（测试守着）。

捏合与滚轮都是连续事件，用 :class:`StepAccumulator` 攒够阈值才切一档；捏合还多一道
最小间隔（:class:`RateLimitedAccumulator`），否则切档比动画还快、每次都被掐断。
时钟注入（:func:`monotonic_ms`），刻意不导入 Qt，好让这套逻辑能用普通 pytest 覆盖。
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
#: **必须不小于切档动画的时长**（``grid.EDGE_ANIMATION_MS``），否则限速形同虚设 ——
#: 手势快时两次切档之间仍可能短于一段动画。这条不变量由测试守着。
MIN_PINCH_INTERVAL_MS: Final = 220

#: 只给角度增量的设备上，**一个角度单位等于多少像素**：取 Qt 自己那一处换算（非精确
#: 滚轮一格 = 120 个角度单位，而 Qt 把一格估成 20 像素），两种设备于是落在同一个单位上。
ANGLE_UNITS_PER_PIXEL: Final = 120.0 / 20.0

#: ⌘+滚轮的切换阈值，单位是**像素**。**为什么不用角度增量**：Qt 在 macOS 上按「设备有没有
#: 精确滚动」给两套单位，魔法鼠标与触控板给的是像素增量、一格只折算出 4 上下，拿 120 去卡它
#: 要滚三十格才切一档。按换算一格 20 像素，取 12 留点余量。多攒的那点不会多切 ——
#: 网格一次滚轮事件只发一个 ±1（ADR 0001 决策 9）。
WHEEL_STEP_PIXELS: Final = 12.0

#: 缩略图长边的下限。窗口小到离谱时也不至于缩成看不清的一点。
MIN_EDGE: Final = 48

#: 列数搜索的上限。超过这个列数再细也只是浪费循环。
MAX_COLUMNS: Final = 64


def fit_columns(available: int, target_cell: int, *, max_columns: int = MAX_COLUMNS) -> int:
    """算出「一行铺满可用宽度」时该分成几列。

    在铺满的前提下让格子宽度尽量贴近 ``target_cell``（格子会被拉伸到
    ``available // columns``），列数选得好右侧就不会留下空白。
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


def wheel_delta(pixel: float, angle: float) -> float:
    """一次滚轮事件里**说得准的那一份**增量，统一换算成像素。

    **有像素就用像素**，只给角度的那条按 ``角度单位 ÷ 6`` 折过来，于是两种设备落在
    同一个单位上（数值与判据见 :data:`WHEEL_STEP_PIXELS` 与 :data:`ANGLE_UNITS_PER_PIXEL`）。
    """
    return pixel if pixel else angle / ANGLE_UNITS_PER_PIXEL


class StepAccumulator:
    """把连续的手势/滚轮增量攒成离散的档位切换（正数放大，负数缩小）。"""

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
    """单调时钟的毫秒数（:class:`RateLimitedAccumulator` 默认用它）。

    做成函数是为了能注入：限速不塞一个假时钟就没法测（真等 220 毫秒既慢又不稳）。
    """
    return time.monotonic_ns() // 1_000_000


class RateLimitedAccumulator(StepAccumulator):
    """攒器外面再套一道**最小间隔**：间隔不足的增量整个丢掉。

    网格那一侧「上一段切档动画没跑完就不接第二段」，所以越快越糟：每次切档都把上一次的
    动画掐断，快起来一帧也看不见。**间隔内丢掉而不是攒着**，与攒器本身的取舍相反，也是
    两条手势路径的分界：捏合的意图是连续的（手停在哪就是哪），多出来的那几下是同一个
    意图的重复表达；而快捷键与 ⌘+滚轮每一下都是独立的意图，不走这里。

    首次喂入没有间隔，所以打开界面直接捏一下立刻生效。
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
        """喂入一个增量，返回本次应切换的档数；**间隔不足时直接返回 0**，增量也不累加。"""
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
    "ANGLE_UNITS_PER_PIXEL",
    "DEFAULT_EDGE",
    "DEFAULT_INDEX",
    "MAX_COLUMNS",
    "MIN_EDGE",
    "MIN_PINCH_INTERVAL_MS",
    "PINCH_THRESHOLD",
    "THUMBNAIL_LABELS",
    "THUMBNAIL_SIZES",
    "WHEEL_STEP_PIXELS",
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
    "wheel_delta",
]
