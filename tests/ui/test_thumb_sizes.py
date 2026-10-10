"""``gallery.ui.thumb_sizes`` 的测试：档位与手势累加，都不需要 Qt。"""

from __future__ import annotations

import pytest

from gallery.core.constants import DEFAULT_THUMBNAIL_MAX_EDGE
from gallery.ui.thumb_sizes import (
    DEFAULT_EDGE,
    DEFAULT_INDEX,
    MIN_PINCH_INTERVAL_MS,
    PINCH_THRESHOLD,
    THUMBNAIL_LABELS,
    THUMBNAIL_SIZES,
    WHEEL_STEP_PIXELS,
    RateLimitedAccumulator,
    StepAccumulator,
    clamp_index,
    describe,
    edge_for_index,
    fit_columns,
    label_for_index,
    nearest_index,
    shifted_index,
    wheel_delta,
)


class FakeClock:
    """能自己往前拨的毫秒时钟。

    限速这种东西没法用真时钟测：等 220 毫秒既慢又不稳（跑得慢的机器上还要多等），
    而「间隔不足要丢」这条判据偏偏要在**毫秒之间**分辨。
    """

    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def test_three_sizes_in_ascending_order() -> None:
    assert len(THUMBNAIL_SIZES) == 3
    assert list(THUMBNAIL_SIZES) == sorted(THUMBNAIL_SIZES)
    assert len(set(THUMBNAIL_SIZES)) == 3
    assert len(THUMBNAIL_LABELS) == len(THUMBNAIL_SIZES)


def test_default_is_the_middle_size() -> None:
    assert DEFAULT_INDEX == 1
    assert THUMBNAIL_SIZES[1] == DEFAULT_EDGE


def test_largest_size_fits_the_disk_cache() -> None:
    """最大档超过缓存长边就等于把小图拉大，白糊一层。"""
    assert max(THUMBNAIL_SIZES) <= DEFAULT_THUMBNAIL_MAX_EDGE


def test_clamp_and_lookup() -> None:
    assert clamp_index(-5) == 0
    assert clamp_index(99) == 2
    assert edge_for_index(0) == THUMBNAIL_SIZES[0]
    assert edge_for_index(99) == THUMBNAIL_SIZES[-1]
    assert label_for_index(0) == "小"
    assert label_for_index(2) == "大"


def test_shifted_index_stops_at_both_ends() -> None:
    assert shifted_index(1, 1) == 2
    assert shifted_index(1, -1) == 0
    assert shifted_index(0, -1) == 0
    assert shifted_index(2, 1) == 2
    assert shifted_index(1, 0) == 1


def test_nearest_index_maps_remembered_values() -> None:
    assert nearest_index(None) == DEFAULT_INDEX
    assert nearest_index(112) == 0
    assert nearest_index(130) == 0  # 130 离 112（差 18）比离 176（差 46）更近
    assert nearest_index(150) == 1  # 越过 144 这个中点之后归到「中」
    assert nearest_index(200) == 1  # 232 是中与大的中点
    assert nearest_index(288) == 2
    assert nearest_index(9999) == 2


def test_describe_mentions_label_and_pixels() -> None:
    text = describe(2)

    assert "大" in text
    assert "288" in text


def test_accumulator_fires_only_after_threshold() -> None:
    accumulator = StepAccumulator(1.0)

    assert accumulator.feed(0.4) == 0
    assert accumulator.feed(0.4) == 0
    assert accumulator.feed(0.4) == 1
    assert accumulator.accumulated == pytest.approx(0.2)


def test_accumulator_counts_both_directions() -> None:
    accumulator = StepAccumulator(1.0)

    assert accumulator.feed(-2.5) == -2
    assert accumulator.accumulated == pytest.approx(-0.5)
    assert accumulator.feed(0.5) == 0
    assert accumulator.accumulated == pytest.approx(0.0)


def test_accumulator_ignores_nan() -> None:
    accumulator = StepAccumulator(1.0)

    assert accumulator.feed(float("nan")) == 0
    assert accumulator.feed(1.0) == 1


def test_accumulator_reset() -> None:
    accumulator = StepAccumulator(PINCH_THRESHOLD)
    accumulator.feed(PINCH_THRESHOLD / 2)

    accumulator.reset()

    assert accumulator.accumulated == 0.0
    assert accumulator.feed(PINCH_THRESHOLD / 2) == 0


def test_accumulator_rejects_non_positive_threshold() -> None:
    with pytest.raises(ValueError):
        StepAccumulator(0)


#: 一格滚轮的两种样子（Qt 的 ``qnsview_mouse.mm`` 给出的那几个数）。
CLASSIC_NOTCH = (0.0, 120.0)  # 传统滚轮：只有角度增量
PRECISE_NOTCH = (20.0, 4.0)  # 魔法鼠标 / 触控板：有像素增量，角度只有它的一半


class TestWheelDelta:
    """⌘+滚轮：两种设备给的是两套单位，换算到同一个单位之后一格就该切一档。

    这一组是用「滚一格」这件事本身写的，不是照着阈值写的：Qt 的 macOS 层对传统滚轮
    给角度增量（一格 120），对魔法鼠标 / 触控板给像素增量（一格约 20，角度只有 4）。
    只读角度增量、按 120 卡阈值的话，魔法鼠标要滚三十格才切一档。
    """

    @pytest.mark.parametrize(("pixel", "angle"), [CLASSIC_NOTCH, PRECISE_NOTCH])
    def test_one_notch_switches_one_step(self, pixel: float, angle: float) -> None:
        accumulator = StepAccumulator(WHEEL_STEP_PIXELS)

        assert accumulator.feed(wheel_delta(pixel, angle)) == 1
        assert accumulator.feed(wheel_delta(-pixel, -angle)) == -1

    @pytest.mark.parametrize(("pixel", "angle"), [CLASSIC_NOTCH, PRECISE_NOTCH])
    def test_the_delta_is_the_same_notch_on_both_devices(self, pixel: float, angle: float) -> None:
        """一格在两种设备上是同一个量 —— 阈值只有一份，手感才可能一致。"""
        assert wheel_delta(pixel, angle) == pytest.approx(wheel_delta(*CLASSIC_NOTCH))

    def test_the_pixel_delta_wins_when_both_are_given(self) -> None:
        """魔法鼠标与触控板两个字段都有值，而角度那一份被折算得极小。

        拿角度去卡阈值就是这一支迟钝的病根（Qt 按「1 像素 = 2 个角度单位」折算）。
        """
        assert wheel_delta(*PRECISE_NOTCH) == pytest.approx(20.0)

    def test_a_notch_worth_of_pixels_is_enough_to_fire(self) -> None:
        """阈值必须**小于**一格的像素量，否则高精度滚轮滚一格仍然没反应。"""
        assert wheel_delta(*PRECISE_NOTCH) > WHEEL_STEP_PIXELS

    def test_a_movement_below_the_threshold_is_not_a_notch(self) -> None:
        """暂存是为了连着来的一串小增量，不是为了把「没滚到位」也当一格。"""
        accumulator = StepAccumulator(WHEEL_STEP_PIXELS)

        assert accumulator.feed(wheel_delta(*PRECISE_NOTCH) / 3) == 0


class TestRateLimitedAccumulator:
    """捏合：间隔不足的增量整个丢掉。

    这条规矩是为了动画而存在的 —— 网格那边「上一段过渡没跑完就不接第二段、直接落位」，
    手势快起来切档比动画还快，于是每一次切档都掐断上一次，画面只是一连串跳变。
    所以这里量的不是「累积得对不对」，而是「该丢的有没有丢」。
    """

    def test_the_first_step_is_not_delayed(self) -> None:
        """还没有「上一次」可与此刻比，打开界面直接捏一下就该立刻生效。"""
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)

        assert accumulator.feed(PINCH_THRESHOLD) == 1

    def test_a_step_inside_the_interval_is_dropped(self) -> None:
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD)

        clock.advance(MIN_PINCH_INTERVAL_MS - 1)
        assert accumulator.feed(PINCH_THRESHOLD) == 0, "差一毫秒也不行：动画还没跑完"

    def test_a_dropped_step_does_not_accumulate(self) -> None:
        """丢掉的是这一下，不是「累积起来等下次」——否则限速只是把跳变推迟到了后面。"""
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD)

        for _ in range(20):
            clock.advance(1)
            assert accumulator.feed(PINCH_THRESHOLD) == 0
        assert accumulator.accumulated == 0.0

    def test_a_step_at_the_interval_boundary_is_accepted(self) -> None:
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD)

        clock.advance(MIN_PINCH_INTERVAL_MS)
        assert accumulator.feed(PINCH_THRESHOLD) == 1

    def test_the_interval_only_starts_from_the_last_step_not_the_last_call(self) -> None:
        """累积阈值的那几下不该把间隔重新推后 —— 推后就永远累积不满一次切档。"""
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD / 2)

        clock.advance(MIN_PINCH_INTERVAL_MS - 10)
        assert accumulator.feed(PINCH_THRESHOLD / 2) == 1, "攒够阈值就切档，间隔从这一刻起算"

    def test_a_slow_gesture_is_not_rate_limited(self) -> None:
        """慢捏时每一次切档都隔得开限速，行为与不带限速的累积器一样。"""
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)

        for _ in range(5):
            clock.advance(MIN_PINCH_INTERVAL_MS)
            assert accumulator.feed(PINCH_THRESHOLD) == 1

    def test_reset_forgets_the_interval_too(self) -> None:
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD)

        accumulator.reset()

        assert accumulator.feed(PINCH_THRESHOLD) == 1, "重置之后第一下不该被上一次的时刻挡住"

    def test_it_rejects_a_negative_interval(self) -> None:
        with pytest.raises(ValueError):
            RateLimitedAccumulator(PINCH_THRESHOLD, -1, FakeClock())

    def test_the_interval_is_a_real_limit_and_not_merely_a_smaller_threshold(self) -> None:
        """限速与「累积够阈值」是两件事：一次事件量再大也不该突破最小间隔。"""
        clock = FakeClock()
        accumulator = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS, clock)
        accumulator.feed(PINCH_THRESHOLD * 100)

        clock.advance(1)
        assert accumulator.feed(PINCH_THRESHOLD * 100) == 0


class TestFitColumns:
    """一行铺满：列数选得好，右侧就不留空白。"""

    def test_picks_the_column_count_closest_to_the_target(self) -> None:
        # 1180 // 8 = 147（离 140 差 7），1180 // 9 = 131（差 9）→ 8 列更贴近目标
        assert fit_columns(1180, 140) == 8
        # 1198 // 9 = 133（差 7）比 1198 // 8 = 149（差 9）更贴近
        assert fit_columns(1198, 140) == 9

    def test_slack_is_always_less_than_one_pixel_per_column(self) -> None:
        for available in range(320, 2600, 7):
            for target in (140, 204, 316):
                columns = fit_columns(available, target)
                cell = available // columns
                assert columns * cell <= available
                assert available - columns * cell < columns

    def test_cell_width_stays_near_the_target(self) -> None:
        for available in range(600, 2600, 13):
            for target in (140, 204, 316):
                cell = available // fit_columns(available, target)
                # 允许多铺一列导致的小幅缩小，也允许少铺一列导致的小幅放大
                assert target * 0.6 <= cell <= target * 1.6, (available, target, cell)

    def test_degenerate_inputs_fall_back_to_one_column(self) -> None:
        assert fit_columns(0, 140) == 1
        assert fit_columns(-100, 140) == 1
        assert fit_columns(1000, 0) == 1

    def test_never_exceeds_the_column_cap(self) -> None:
        assert fit_columns(100_000, 140, max_columns=10) <= 10


def test_describe_actual_reports_the_measured_size() -> None:
    from gallery.ui.thumb_sizes import describe_actual

    text = describe_actual(2, 285)

    assert "大" in text
    assert "285" in text
