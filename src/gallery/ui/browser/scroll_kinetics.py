"""滚轮增量到「滑过去」之间的那点算术。

**为什么自己算而不用 Qt 现成的**：`QScroller` 是 Qt 官方的惯性滚动器，但 PySide6
6.11 没有暴露它的工厂方法 ``QScroller.create()``，类本身也不能直接构造，于是根本
取不到实例（见 ADR 0026）。``QAbstractItemView`` 自己也没有任何惯性开关 ——
滚动模式只有 ``ScrollPerItem`` 与 ``ScrollPerPixel`` 两个枚举。

**这里只管两个数**：目标位置与实际位置。滚轮增量往目标上加，实际位置按指数逼近
追过去。输入停下来的那一刻两者还差着一截，画面就自己滑完了那一截 —— 这就是
「惯性」的全部。**永远不越过目标**（没有回弹，那是第二步的事，见 ADR 0026）。

刻意不导入 Qt，好让这套算术能用普通 pytest 覆盖：定时器、滚动条、事件换算都在
:mod:`gallery.ui.browser.grid` 那边。
"""

from __future__ import annotations

import math
from typing import Final

#: 实际位置追上目标位置的时间常数（秒）。
#:
#: 这一帧走完整段剩余距离的 ``1 - e^(-dt/τ)``。τ 越小越跟手，越大越「滑」。
#: 0.08 秒的实感是：滚轮离手之后还滑约半秒，正好一段，肉眼能看出是滑而不是跳，
#: 又不至于让人觉得界面在等它。手感不好时**只调这一个数**，其余都是结构。
CATCH_UP_SECONDS: Final = 0.08

#: 传统滚轮一格（``angleDelta = 120``）走多少像素。
#:
#: 沿用 Qt 每像素滚动的换算（120 ÷ 8）。**不是**随手挑的整数：换算规则一旦由我们
#: 接管，它就是个契约，得有一根钉子钉住（``tests/ui/browser/test_scroll_momentum.py``）。
WHEEL_NOTCH_PIXELS: Final = 15.0

#: 差多少像素以内就算「到了」。
#:
#: 位置是浮点，滚动条是整数，所以总有那么一点永远补不上的零头。不认它，动画会在
#: 「差 0.3 像素」的地方一帧一帧空转到天荒地老。
SNAP_PIXELS: Final = 0.5

#: 一帧最多挪多少（视口高度的倍数）。
#:
#: 正常手势一步也就几十像素，撞不上这个上限；它挡的是病态输入（例如合成事件一次
#: 塞进上万像素）——那种一帧就该滑过半屏，看起来是跳变而不是滑动。
MAX_STEP_RATIO: Final = 0.5


class ScrollKinetics:
    """滚轮增量 → 目标位置，实际位置按指数逼近追过去。

    只记两个浮点数，不碰任何控件：谁在动滚动条、什么时候起停表，由调用方决定
    （:class:`gallery.ui.browser.grid.PhotoGrid` 用一根 16 毫秒的定时器）。
    """

    __slots__ = ("_catch_up", "_target", "_value")

    def __init__(self, value: float = 0.0, *, catch_up_seconds: float = CATCH_UP_SECONDS) -> None:
        if catch_up_seconds <= 0:
            raise ValueError("时间常数必须为正数")
        self._value = value
        self._target = value
        self._catch_up = catch_up_seconds

    @property
    def value(self) -> float:
        """当前实际位置（浮点，可以是「目标减去不到一个像素」的那种没到位状态）。"""
        return self._value

    @property
    def target(self) -> float:
        """内容**应该**在的位置，滚轮往这里加。"""
        return self._target

    @property
    def catch_up_seconds(self) -> float:
        return self._catch_up

    @property
    def settled(self) -> bool:
        """已经没有待走的距离了 —— 该停表了。"""
        return abs(self._target - self._value) <= SNAP_PIXELS

    def reanchor(self, value: float) -> None:
        """**别人**动了滚动条：实际位置与目标一起挪到那儿，重新开始。

        方向键、``scrollTo``、拖滑块、换层重排走的是这条路。忘了这一句的后果是
        惯性与用户同时在写同一个值：用户按方向键，界面还在替上一段滑行往回追。
        """
        self._value = value
        self._target = value

    def feed(self, delta: float) -> None:
        """一次滚动增量：**只**往目标上加，实际位置一步都不动。

        「当帧不动」就是全部手感的起点 —— 动了就变成每格跳一下。
        """
        if delta != delta:  # NaN：忽略，否则目标会被永久污染成 NaN
            return
        self._target += delta

    def clamp_target(self, low: float, high: float) -> None:
        """把目标夹进滚动条的合法范围。

        内容变高/变矮、模型清空重排，都会在惯性还没跑完时改掉范围；夹一下，
        否则会朝着一个已经不存在的位置滑。
        """
        self._target = max(low, min(high, self._target))

    def advance(self, seconds: float, *, max_step: float = math.inf) -> float | None:
        """走一帧，返回该滚到哪儿；**返回 ``None`` 表示已经到位、该停表了**。

        ``seconds`` 为 0 时原地不动并继续返回位置（**不**返回 ``None``）：
        同一毫秒里来了两次的事件不该被当成「已经到位」。
        """
        if self.settled:
            self._value = self._target
            return None
        if seconds > 0 and max_step > 0:
            step = (self._target - self._value) * (1.0 - math.exp(-seconds / self._catch_up))
            self._value += max(-max_step, min(max_step, step))
        if self.settled:
            self._value = self._target
            return None
        return self._value


__all__ = [
    "CATCH_UP_SECONDS",
    "MAX_STEP_RATIO",
    "SNAP_PIXELS",
    "WHEEL_NOTCH_PIXELS",
    "ScrollKinetics",
]
