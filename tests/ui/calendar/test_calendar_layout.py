"""日历的排版计算（纯像素数学，不起窗口）。"""

from __future__ import annotations

import pytest

from gallery.ui.calendar.layout import (
    CARD_RADIUS,
    CELL_ASPECT,
    CELL_GAP,
    DAY_ROW_CAP,
    HOURS_PER_DAY,
    MIN_ROW_HEIGHT,
    cell_size,
    cells_and_offset,
    cells_in_row,
    fitted_size,
    month_cells,
    row_height,
)


def test_the_cell_ratio_is_a_constant_not_something_read_off_the_photo() -> None:
    """格子比例是定的；照片按**自己**的比例缩进去（与网格视图同一手法）。"""
    assert CELL_ASPECT == 1.5


def test_a_week_row_is_exactly_one_cell_tall() -> None:
    """周视图：行高 = 列宽 ÷ 比例 + 留白，于是每小时恰好一格。

    这条是「一周只放一张照片」的**来源**：不是一条规则，而是放不下第二格的结果。
    """
    column = 181.0
    row = row_height("week", column)
    width, height = cell_size(row)
    assert height == pytest.approx((column - 2 * CELL_GAP) / CELL_ASPECT)
    assert width == pytest.approx(column - 2 * CELL_GAP)
    assert cells_in_row(column, row) == 1


def test_a_narrow_week_column_still_gets_one_cell() -> None:
    row = row_height("week", 40.0)
    assert row >= MIN_ROW_HEIGHT
    assert cells_in_row(40.0, row) == 1


@pytest.mark.parametrize("column", [80.0, 181.0, 300.0, 1200.0])
def test_a_week_column_never_holds_two_cells(column: float) -> None:
    assert cells_in_row(column, row_height("week", column)) == 1


def test_a_day_row_is_capped_so_an_hour_stays_wider_than_tall() -> None:
    """日视图的行高封顶 —— 否则一行按列宽算出来高到比整屏还高。"""
    row = row_height("day", 1200.0)
    assert row == DAY_ROW_CAP
    width, _height = cell_size(row)
    assert width > row  # 每一格都宽于高


def test_a_day_row_fits_several_cells_side_by_side() -> None:
    count, _offset = cells_and_offset(1200.0, DAY_ROW_CAP)
    assert count >= 5


def test_cells_are_centred_in_the_row() -> None:
    """用户要的：一小时放几张都居中，不靠左。"""
    count, offset = cells_and_offset(1200.0, DAY_ROW_CAP)
    width, _height = cell_size(DAY_ROW_CAP)
    used = count * width + (count - 1) * CELL_GAP
    free = 1200.0 - 2 * CELL_GAP - used
    assert offset == pytest.approx(free / 2)
    assert offset > 0
    # 左边留的空白与右边留的一样多
    left = CELL_GAP + offset
    right = 1200.0 - (CELL_GAP + offset + used)
    assert left == pytest.approx(right)


def test_a_row_too_narrow_for_two_still_centres_one() -> None:
    count, offset = cells_and_offset(200.0, DAY_ROW_CAP)
    assert count == 1
    width, _height = cell_size(DAY_ROW_CAP)
    assert offset == pytest.approx((200.0 - 2 * CELL_GAP - width) / 2)


@pytest.mark.parametrize(
    ("source", "box", "expected"),
    [
        ((3000, 2000), (100.0, 100.0), (100.0, 66.7)),  # 横图放进方格：上下留边
        ((2000, 3000), (100.0, 100.0), (66.7, 100.0)),  # 竖图放进方格：左右留边
        ((1500, 1000), (90.0, 60.0), (90.0, 60.0)),  # 同比例：正好铺满
        ((1500, 1000), (30.0, 60.0), (30.0, 20.0)),  # 只缩不放
    ],
)
def test_fitted_size_keeps_the_photo_ratio(
    source: tuple[int, int], box: tuple[float, float], expected: tuple[float, float]
) -> None:
    width, height = fitted_size(source[0], source[1], box[0], box[1])
    assert width == pytest.approx(expected[0], abs=0.1)
    assert height == pytest.approx(expected[1], abs=0.1)
    assert width / height == pytest.approx(source[0] / source[1], rel=1e-6)


@pytest.mark.parametrize("source", [(0, 0), (0, 100), (100, 0)])
def test_fitting_a_broken_image_does_not_divide_by_zero(source: tuple[int, int]) -> None:
    assert fitted_size(source[0], source[1], 50.0, 50.0) == (50.0, 50.0)


def test_fitting_into_an_empty_box_does_not_divide_by_zero() -> None:
    assert fitted_size(100, 50, 0.0, 0.0) == (0.0, 0.0)


def test_the_month_grid_is_six_by_seven() -> None:
    assert month_cells() == 42
    assert month_cells() * HOURS_PER_DAY > 0  # 别把这两个常量写串了


def test_the_radius_is_a_corner_not_a_circle() -> None:
    assert 0 < CARD_RADIUS < 20
