"""触控板两指横向拖动 = 翻页（与 ``←`` / ``→`` 同一件事）。

**往左划 = 往后一段**（把那一页纸往左拖的手感）。**一串增量只翻一页**：阈值、``_fired``、
回到起点附近才重新武装、停一阵算新一轮，四道闸缺一不可。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

#: 走过这么多**像素**算「翻一页」：用像素而不是角度增量，因为像素是手指实际走过的距离。
SWIPE_PIXEL_THRESHOLD: Final = 80.0

#: 拿不到像素增量时的那一份阈值（一格传统滚轮 = 120）：只给角度增量的设备（Magic Mouse）用它。
SWIPE_ANGLE_THRESHOLD: Final = 120.0

#: 停这么久之后再来的那一下算「新一轮手势」（见 :class:`SwipeDetector` 的第三道闸）。
SWIPE_IDLE_MS: Final = 400

#: 离起点近到这个比例之内就算「手回来了」，可以重新武装（不是回零，是回到起点附近）。
NEUTRAL_RATIO: Final = 0.25


def scroll_delta(pixel: float, angle: float) -> float:
    """这一轴上**说得准的那一份**增量：触控板两个字段都给，Magic Mouse 一类只给角度增量。"""
    return pixel if pixel else angle


def monotonic_ms() -> int:
    """单调时钟的毫秒数。做成函数是为了能注入 —— 停手 400 毫秒这种事没法真等。"""
    return time.monotonic_ns() // 1_000_000


class SwipeDetector:
    """把一串横向增量判成离散的翻页（``-1`` / ``0`` / ``1``）；时钟可注入。

    与网格那一侧的 ``StepAccumulator`` **不是同一件事，别拿它来替**：那一套把连续缩放攒成离散
    档位、间隔内的增量**丢掉**；这里恰好相反 —— 增量都留着，限的是「翻几页」。
    """

    __slots__ = ("_clock", "_fired", "_last_at", "_pending")

    def __init__(self, clock: Callable[[], int] = monotonic_ms) -> None:
        self._clock = clock
        #: 这一轮手势从起点算起的累计像素（**翻了页也不清零**，否则「一次手势只翻一页」是空话）。
        self._pending = 0.0
        #: 这一轮手势已经翻过了。
        self._fired = False
        #: 上一次收到增量的时刻（毫秒）；还没收到过是 ``None``。
        self._last_at: int | None = None

    def begin(self) -> None:
        """新的一轮手势（Qt 报了 ``ScrollPhase.BeginGesture``）：从零重新算。"""
        self._pending = 0.0
        self._fired = False

    def reset(self) -> None:
        """忘掉累计量与时刻（回到「随时可以翻第一页」的状态）。"""
        self._pending = 0.0
        self._fired = False
        self._last_at = None

    def feed(self, pixel: float, angle: float) -> int:
        """喂进一次横向滚动，返回该翻几页（**正数 = 往后一段**，也就是手指往左划）。"""
        if pixel != pixel or angle != angle:  # NaN：忽略，否则累加器会被永久污染
            return 0
        delta, threshold = (
            (pixel, SWIPE_PIXEL_THRESHOLD) if pixel else (angle, SWIPE_ANGLE_THRESHOLD)
        )
        if not delta:
            return 0
        now = self._clock()
        if self._last_at is not None and now - self._last_at > SWIPE_IDLE_MS:
            self._pending = 0.0  # 停了一阵：这是新的一次甩动，上一次的账不算
            self._fired = False
        self._last_at = now
        self._pending += delta
        if abs(self._pending) <= threshold * NEUTRAL_RATIO:
            # 手回到起点附近（或中途反向）：重新武装，翻下一页得重新甩一次
            self._fired = False
            return 0
        if self._fired or abs(self._pending) < threshold:
            return 0
        self._fired = True
        # **符号只在这一处翻**：手指往左（``_pending`` 为负）→ ``+1``，往后一段。
        return -1 if self._pending > 0 else 1


__all__ = [
    "NEUTRAL_RATIO",
    "SWIPE_ANGLE_THRESHOLD",
    "SWIPE_IDLE_MS",
    "SWIPE_PIXEL_THRESHOLD",
    "SwipeDetector",
    "monotonic_ms",
    "scroll_delta",
]
