"""两指甩一下要不要翻页、往哪翻（纯数字，不起窗口 —— 判据全在
:mod:`gallery.ui.calendar.swipe`）。

**方向在用例里只按「手指往哪边走」说话**：往左走 = 往后一段，往右走 = 往前一段
（拖动那一页纸的方向，理由见那个模块的开头）。翻页的正负号由被测代码去定，
这里不替它说。
"""

from __future__ import annotations

import pytest

from gallery.ui.calendar.swipe import (
    NEUTRAL_RATIO,
    SWIPE_ANGLE_THRESHOLD,
    SWIPE_IDLE_MS,
    SWIPE_PIXEL_THRESHOLD,
    SwipeDetector,
    scroll_delta,
)

#: 手指往左划（往后一段）／往右划（往前一段）。
LEFT = -1.0
RIGHT = 1.0

#: 两次阈值中，喂哪一种由「有没有像素增量」决定（见 :func:`_feed`）。
_THRESHOLDS = [
    pytest.param(SWIPE_PIXEL_THRESHOLD, True, id="像素"),
    pytest.param(SWIPE_ANGLE_THRESHOLD, False, id="角度"),
]


class FakeClock:
    """假时钟：甩一下的节奏得能一个数一个数地摆出来。"""

    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _feed(detector: SwipeDetector, clock: FakeClock, pixels: float) -> int:
    """喂一次横向滚动（十六毫秒一次，够快也不算连着同一瞬间）。"""
    clock.advance(16)
    return detector.feed(pixels, 0.0)


def _flick(detector: SwipeDetector, clock: FakeClock, direction: float, count: int) -> list[int]:
    """连着喂 ``count`` 个同样大小的横向增量，返回每一次的判决。"""
    return [_feed(detector, clock, direction * 40.0) for _ in range(count)]


def test_a_nudge_is_not_a_swipe() -> None:
    """两指**轻轻推一下**不该翻页：真机上那也就二三十像素。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    assert [_feed(detector, clock, LEFT * 8.0) for _ in range(6)] == [0] * 6  # 一共 48 像素


def test_swiping_left_pages_forward_once_and_no_further() -> None:
    """**往左划 = 往后一段，而且一次只翻一页**（后面同一串增量不再翻）。

    方向是这一条的一半：手指往左走，屏幕上的内容跟着走，右边那一份露出来。
    """
    clock = FakeClock()
    detector = SwipeDetector(clock)
    steps = _flick(detector, clock, LEFT, 12)  # 480 像素，够狠
    assert steps.count(1) == 1, f"往左划一次翻了 {steps.count(1)} 页：{steps}"
    assert steps[1] == 1, "第二个增量就够 80 像素了，那一下就该翻"


def test_swiping_right_pages_back() -> None:
    """往右划 = 往前一段（往回走）。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    assert _flick(detector, clock, RIGHT, 5).count(-1) == 1


def test_a_page_needs_the_finger_to_come_back_before_the_next_one() -> None:
    """一次手势只翻一页之后，**手回到起点附近**才能再翻。

    这是「慢慢拖过整个屏幕」与「连甩两下」的分界：前者要的是一页，不是五页。
    """
    clock = FakeClock()
    detector = SwipeDetector(clock)

    assert _flick(detector, clock, LEFT, 6).count(1) == 1  # 240 像素往左
    assert _flick(detector, clock, LEFT, 3) == [0, 0, 0], "手还压在左边，凭什么再翻"
    assert _flick(detector, clock, RIGHT, 9)[-1] == 0, "回到起点那一刻只是重新武装"
    assert _flick(detector, clock, LEFT, 2).count(1) == 1, "重新武装之后该再翻一页"


def test_a_pause_starts_a_fresh_swipe() -> None:
    """停一阵再动 = 新的一轮手势（**回退**：不报滚动阶段信息的平台上只有这一条路）。

    没有它就是「连着往左甩两下，第二下没反应」—— 因为上一次那次还占着「这一轮已经翻过」。
    """
    clock = FakeClock()
    detector = SwipeDetector(clock)
    assert _flick(detector, clock, LEFT, 5).count(1) == 1

    clock.advance(SWIPE_IDLE_MS + 50)
    assert _flick(detector, clock, LEFT, 3).count(1) == 1, "停过之后的那一下没有当成新手势"


def test_beginning_a_gesture_clears_what_was_half_done() -> None:
    """Qt 报了 ``ScrollBegin``（真机上一定会报）就该从零算：上一轮的账不算。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    _flick(detector, clock, LEFT, 5)

    detector.begin()
    assert _flick(detector, clock, LEFT, 2) == [0, 1]


def test_a_device_without_pixel_deltas_pages_per_wheel_step() -> None:
    """拿不到像素增量时按角度增量算（Magic Mouse 一类）：一格滚轮 = 翻一页。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    clock.advance(16)
    assert detector.feed(0.0, -SWIPE_ANGLE_THRESHOLD / 2) == 0  # 往左还不到一格
    clock.advance(16)
    assert detector.feed(0.0, -SWIPE_ANGLE_THRESHOLD / 2) == 1


def test_the_pixels_win_when_a_device_sends_both() -> None:
    """两个字段都给时**用像素**：角度增量是「几格滚轮」，像素增量才是手指走了多远。

    混着用的话阈值就没有意义了 —— 一格滚轮 120 与 80 像素根本不是一回事。
    """
    assert scroll_delta(12.0, 900.0) == 12.0
    assert scroll_delta(0.0, 900.0) == 900.0


def test_a_zero_delta_leaves_the_accumulator_alone() -> None:
    """零增量什么也不做（也不会重新武装 —— 手没动过就没动过）。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    _flick(detector, clock, LEFT, 2)
    clock.advance(16)
    assert detector.feed(0.0, 0.0) == 0
    assert detector._pending == pytest.approx(-80.0), "零增量不该改变累计量"


def test_a_broken_delta_does_not_poison_the_accumulator() -> None:
    """NaN 必须忽略：累加进去之后 ``abs(nan) >= 阈值`` 永远是假，之后什么都不发生。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)
    clock.advance(16)
    assert detector.feed(float("nan"), 0.0) == 0
    assert _flick(detector, clock, LEFT, 2).count(1) == 1


def test_the_neutral_zone_is_a_fifth_of_the_threshold() -> None:
    """「手回来了」是起点附近，不是非得精确回到 0 —— 后者是做不到的。"""
    assert 0 < NEUTRAL_RATIO <= 0.5
    assert SWIPE_PIXEL_THRESHOLD * NEUTRAL_RATIO < SWIPE_PIXEL_THRESHOLD


@pytest.mark.parametrize(("threshold", "with_pixels"), _THRESHOLDS)
def test_a_hair_short_of_the_threshold_still_does_not_page(
    threshold: float, with_pixels: bool
) -> None:
    """差那一点也在「暂存」那一侧 —— 少 2% 就不是「一次甩动」。"""
    clock = FakeClock()
    detector = SwipeDetector(clock)

    def feed(pixels: float) -> int:
        clock.advance(16)
        return detector.feed(pixels if with_pixels else 0.0, 0.0 if with_pixels else pixels)

    assert feed(-threshold * 0.98) == 0
    assert feed(-threshold * 0.02) == 1
