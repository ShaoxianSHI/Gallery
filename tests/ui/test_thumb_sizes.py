"""``gallery.ui.thumb_sizes`` 的测试：档位与手势累加，都不需要 Qt。"""

from __future__ import annotations

import pytest

from gallery.core.constants import DEFAULT_THUMBNAIL_MAX_EDGE
from gallery.ui.thumb_sizes import (
    DEFAULT_EDGE,
    DEFAULT_INDEX,
    PINCH_THRESHOLD,
    THUMBNAIL_LABELS,
    THUMBNAIL_SIZES,
    WHEEL_THRESHOLD,
    StepAccumulator,
    clamp_index,
    describe,
    edge_for_index,
    fit_columns,
    label_for_index,
    nearest_index,
    shifted_index,
)


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


def test_a_single_mouse_wheel_notch_switches_one_step() -> None:
    accumulator = StepAccumulator(WHEEL_THRESHOLD)

    assert accumulator.feed(WHEEL_THRESHOLD) == 1
    assert accumulator.feed(-WHEEL_THRESHOLD) == -1


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
