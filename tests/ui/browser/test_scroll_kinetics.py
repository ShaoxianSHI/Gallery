"""滚动惯性那点算术的单元测试（不需要 Qt，也不需要界面）。

这一层刻意与控件分开，就是为了让「什么时候到位」「会不会越位」「别人动了怎么办」
这几件事能被普通 pytest 直接覆盖 —— 挂到界面上再测就只能靠肉眼与等时间。
"""

from __future__ import annotations

import math

import pytest

from gallery.ui.browser.scroll_kinetics import (
    CATCH_UP_SECONDS,
    MAX_STEP_RATIO,
    SNAP_PIXELS,
    WHEEL_NOTCH_PIXELS,
    ScrollKinetics,
)

#: 走一帧用多长时间（毫秒）。与 :data:`gallery.ui.browser.grid.MOMENTUM_FRAME_MS` 同值，
#: 这里只关心「一帧」这个粒度，不关心定时器本身。
FRAME_SECONDS = 0.016


def run_frames(kinetics: ScrollKinetics, seconds: float) -> list[float]:
    """按帧推进若干秒，返回每一帧之后的位置（``None`` 表示那一帧宣布到位）。"""
    seen: list[float] = []
    for _ in range(int(seconds / FRAME_SECONDS)):
        value = kinetics.advance(FRAME_SECONDS)
        if value is None:
            seen.append(kinetics.value)
            break
        seen.append(value)
    return seen


def test_a_wheel_notch_is_fifteen_pixels() -> None:
    """一格滚轮 = 120 除以 8。这条换算从此由我们说了算，得钉住。"""
    assert WHEEL_NOTCH_PIXELS == 120 / 8


def test_feeding_a_delta_does_not_move_the_view() -> None:
    """滚轮只往目标上加，实际位置一步都不动 —— 「当帧不跳」的全部起点。"""
    kinetics = ScrollKinetics()

    kinetics.feed(WHEEL_NOTCH_PIXELS)

    assert kinetics.value == 0
    assert kinetics.target == WHEEL_NOTCH_PIXELS
    assert not kinetics.settled


def test_it_walks_the_whole_gap_without_overshooting() -> None:
    """一串中间值走完剩下的距离，中途与终点都不许越过目标。"""
    kinetics = ScrollKinetics()
    kinetics.feed(200)

    seen = run_frames(kinetics, 0.5)

    assert seen == sorted(seen), f"往回退过：{seen}"
    assert all(value <= 200 for value in seen), "越过目标了"
    assert len(set(seen)) > 3, f"只有 {seen}，这是一步到位不是滑行"
    assert seen[-1] == 200


def test_it_announces_done_only_once_it_has_landed() -> None:
    """``advance`` 返回 ``None`` 的那一刻，位置必须**正好**在目标上。"""
    kinetics = ScrollKinetics()
    kinetics.feed(30)

    for _ in range(200):
        value = kinetics.advance(FRAME_SECONDS)
        if value is None:
            break
    else:  # pragma: no cover - 走不到：见下一条「多久会停表」
        pytest.fail("两秒了还没停表")

    assert kinetics.value == kinetics.target == 30
    assert kinetics.settled


def test_a_frame_of_zero_length_is_not_the_end_of_the_glide() -> None:
    """同一毫秒里来了两次的事件：不算到位，也不许停表。"""
    kinetics = ScrollKinetics()
    kinetics.feed(15)

    assert kinetics.advance(0.0) == 0.0
    assert not kinetics.settled


def test_the_glide_is_over_within_half_a_second() -> None:
    """手感的一半是「多久停下来」。半秒还滑不完就会显得界面在等它。"""
    kinetics = ScrollKinetics()
    kinetics.feed(WHEEL_NOTCH_PIXELS)

    run_frames(kinetics, 0.5)

    assert kinetics.settled, "滑行了半秒还没到位"
    assert kinetics.value == WHEEL_NOTCH_PIXELS


def test_reanchoring_drops_the_pending_distance() -> None:
    """别人动了滚动位置：目标与实际一起挪过去，不再追那段没走完的路。

    这是「用户按了方向键，界面还在自己滑」那件事的解药。
    """
    kinetics = ScrollKinetics()
    kinetics.feed(400)
    kinetics.advance(FRAME_SECONDS)

    kinetics.reanchor(500)

    assert kinetics.value == 500
    assert kinetics.target == 500
    assert kinetics.settled


def test_the_target_is_clamped_into_the_scrollbar_range() -> None:
    """内容变矮/清空重排会把范围改小；朝着一个已经不存在的位置滑是滑不到头的。"""
    kinetics = ScrollKinetics(500)
    kinetics.feed(10_000)

    kinetics.clamp_target(0, 800)

    assert kinetics.target == 800


def test_a_patologically_large_step_is_capped() -> None:
    """一帧最多挪一屏（这里按调用方给的 ``max_step`` 量）：跨过半个屏幕看着是跳变。"""
    kinetics = ScrollKinetics()
    kinetics.feed(10_000)

    value = kinetics.advance(1.0, max_step=100.0)

    assert value is not None
    assert value == pytest.approx(100.0)


def test_a_nan_delta_is_ignored() -> None:
    """喂进 NaN 就永久污染目标，之后每一帧都什么也不做 —— 当作没发生。"""
    kinetics = ScrollKinetics(10)

    kinetics.feed(math.nan)

    assert kinetics.target == 10
    assert kinetics.settled


def test_the_time_constant_must_be_positive() -> None:
    """τ 必须为正：为 0 会在除法处炸成 ZeroDivisionError，远不如早说清楚。"""
    with pytest.raises(ValueError):
        ScrollKinetics(catch_up_seconds=0.0)


def test_the_step_cap_is_a_whole_viewport_at_most() -> None:
    """上限按视口高度给（网格那边传的是 ``viewport().height() * MAX_STEP_RATIO``）。

    半个视口是一眼里就能看出「跳变」的量，所以这里只钉住它是这样一个量级。
    """
    assert 0 < MAX_STEP_RATIO <= 1


def test_the_snap_threshold_is_a_fraction_of_a_pixel() -> None:
    """位置是浮点、滚动条是整数：差不到半个像素就认它到了，否则会空转到天荒地老。

    认定与落位都在 :meth:`ScrollKinetics.advance` 里发生 —— 认定之后那一帧仍然要宣布
    「到位」，于是目标与实际是对齐的。
    """
    kinetics = ScrollKinetics()
    kinetics.feed(SNAP_PIXELS / 2)

    assert kinetics.settled
    assert kinetics.advance(FRAME_SECONDS) is None
    assert kinetics.value == kinetics.target


def test_the_catch_up_constant_is_the_one_thing_to_tune() -> None:
    """手感不对时只调 τ。其余两个数是结构，不是观感。"""
    assert 0 < CATCH_UP_SECONDS < 0.2
