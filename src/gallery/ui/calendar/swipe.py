"""触控板两指横向拖动 = 翻页（与 ``←`` / ``→`` 同一件事）。

macOS 上「两指横向划」**不走按键也不走手势**，走的是**横向的滚轮事件**：一串
``angleDelta().x()`` 非零、且同时带 ``pixelDelta().x()`` 的 ``QWheelEvent``。所以判据
只在一处：够不够算「一次用力的甩动」。

**一串增量只翻一页，攒够了才翻。** 两个数字与三道闸，缺一道就是一次误翻或一次不翻：

- **攒够阈值才翻**（80 像素）。两指在触控板上「轻轻推一下」是几十像素，够不上；
  用力甩一下是一两百像素，一下就过。
- **一次手势只翻一页**（``_fired``）。快甩时那一串增量轻松上四五百像素，照「过阈值就翻」
  会连翻四页 —— 用户想要的是「回到上个月」，不是「回到去年」。
- **手指回到起点附近才重新武装**。手停在原地不动时 ``_pending`` 只增不减，于是
  慢慢拖过整个屏幕也只翻一页；要翻第二页得先把手拉回来（或者先反向）。
- **停了一阵算新的一轮**（``SWIPE_IDLE_MS``，兜底）。macOS 会报
  ``ScrollPhase.ScrollBegin``（手指按上板子），那一下已经重置过了（见
  ``base.LevelView.wheelEvent``）；不报阶段信息的平台上，「停 400 毫秒再动一下」
  就是新手势的最合理判据。

**惯性那一段（手指抬起来之后 macOS 自己补的那一截）不算**（阶段
``ScrollPhase.ScrollMomentum``），否则手一离板子还会再翻一页。阶段是 Qt 的枚举，
那一道闸在 :meth:`~gallery.ui.calendar.base.LevelView.wheelEvent` 里；这里只管数字，
所以**不导入 Qt**，用普通 pytest 就能覆盖（不占一个 QApplication）。

与网格那一侧的手势攒法（``browser/thumb_sizes.StepAccumulator``）**不是同一件事**，
别拿它来替：那一套把连续缩放攒成离散档位，限速是为了不掐断切档动画、间隔内的增量
**丢掉**；这里要的恰好相反 —— 增量**都留着**（下次接着算），限的是「翻几页」。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

#: 横向走过这么多**像素**算「翻一页」。两指轻轻推一下是几十像素，用力甩是一两百。
#:
#: **为什么用像素而不是角度增量**：角度增量是「几格滚轮」（一格 = 120），各设备给得
#: 一样；像素增量是**手指实际走过的距离**，阈值才有手感可言。
SWIPE_PIXEL_THRESHOLD: Final = 80.0

#: 拿不到像素增量时的那一份阈值（一格传统滚轮 = 120，Qt 以八分之一度为单位）。
#:
#: 这条路径服务的是**只给角度增量**的设备（Magic Mouse 一类），它没有手指距离可依，
#: 于是「一格滚轮翻一页」是最接近的语义。
SWIPE_ANGLE_THRESHOLD: Final = 120.0

#: 停这么久之后再来的那一下算「新一轮手势」。见 :class:`SwipeDetector` 的第三道闸。
SWIPE_IDLE_MS: Final = 400

#: 离起点近到这个比例之内就算「手回来了」，可以重新武装（不是回零，是回到起点附近）。
NEUTRAL_RATIO: Final = 0.25


def scroll_delta(pixel: float, angle: float) -> float:
    """这一轴上**说得准的那一份**增量（有像素增量就用它）。

    两个字段不是都有值的：触控板两个都给，Magic Mouse 一类只给角度增量，
    所以「有像素就用像素」这条判据只在**有值**时成立。
    """
    return pixel if pixel else angle


def monotonic_ms() -> int:
    """单调时钟的毫秒数。做成函数是为了能注入 —— 停手 400 毫秒这种事没法真等。"""
    return time.monotonic_ns() // 1_000_000


class SwipeDetector:
    """把一串横向增量判成离散的翻页（``-1`` / ``0`` / ``1``）。

    时钟是注入进来的（:func:`monotonic_ms`），理由与 ``browser/thumb_sizes`` 里那一对
    攒器一样：那种「等一会儿」的时间差用真时钟既慢又不稳。
    """

    __slots__ = ("_clock", "_fired", "_last_at", "_pending")

    def __init__(self, clock: Callable[[], int] = monotonic_ms) -> None:
        self._clock = clock
        #: 这一轮手势从起点算起累计走过的像素（**翻了页之后不清零** —— 手回没回来是
        #: 另一道闸在管，清零了「一次手势只翻一页」就成了空话）。
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
        """喂进一次横向滚动，返回该翻几页（``0`` / ``1`` / ``-1``）。

        正数表示手指向右划 —— 与 ``→`` 一致，屏幕上的内容是往回走（上一段日子）。
        """
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
            # 手回到起点附近（或者中途反向）：重新武装，翻下一页得重新甩一次
            self._fired = False
            return 0
        if self._fired or abs(self._pending) < threshold:
            return 0
        self._fired = True
        return 1 if self._pending > 0 else -1


__all__ = [
    "NEUTRAL_RATIO",
    "SWIPE_ANGLE_THRESHOLD",
    "SWIPE_IDLE_MS",
    "SWIPE_PIXEL_THRESHOLD",
    "SwipeDetector",
    "monotonic_ms",
    "scroll_delta",
]
