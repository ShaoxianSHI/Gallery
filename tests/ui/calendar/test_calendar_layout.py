"""日历的排版计算（纯像素数学，不起窗口）。"""

from __future__ import annotations

import pytest

from gallery.ui.calendar.layout import (
    CARD_RADIUS,
    CELL_ASPECT,
    CELL_GAP,
    HOURS_PER_DAY,
    MIN_ROW_HEIGHT,
    cell_rect,
    cell_size,
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
    row = row_height(column)
    width, height = cell_size(row)
    assert height == pytest.approx((column - 2 * CELL_GAP) / CELL_ASPECT)
    assert width == pytest.approx(column - 2 * CELL_GAP)
    # 再塞一格就超出这一列了
    assert 2 * width + 3 * CELL_GAP > column


def test_a_narrow_week_column_still_gets_one_cell() -> None:
    """行高有下限，窄列下「按比例该有的那一格」会比列还宽 —— 那就收窄，不溢出到隔壁。"""
    row = row_height(40.0)
    assert row >= MIN_ROW_HEIGHT
    x, _y, width, height = cell_rect(40.0, row)
    assert x + width <= 40.0 - CELL_GAP + 0.01
    assert width / height == pytest.approx(CELL_ASPECT)


@pytest.mark.parametrize("column", [80.0, 181.0, 300.0, 1200.0])
def test_a_week_column_never_holds_two_cells(column: float) -> None:
    row = row_height(column)
    width, _height = cell_size(row)
    assert 2 * width + 3 * CELL_GAP > column


@pytest.mark.parametrize("column", [40.0, 60.0, 181.0, 600.0])
def test_the_cell_is_centred_in_its_column(column: float) -> None:
    """左右一样宽：多出来的宽度对半分，而不是全堆到一边去。"""
    row = row_height(column)
    x, y, width, height = cell_rect(column, row)
    assert y == CELL_GAP
    assert x == pytest.approx(column - (x + width))
    assert x >= CELL_GAP - 0.01
    assert height <= row - 2 * CELL_GAP + 0.01
    assert width / height == pytest.approx(CELL_ASPECT)


def test_a_very_narrow_column_still_centres_what_is_left() -> None:
    x, _y, width, _height = cell_rect(40.0, row_height(40.0))
    assert x >= CELL_GAP
    assert 40.0 - (x + width) >= CELL_GAP - 0.01


def test_the_cell_keeps_its_own_ratio_as_the_column_grows() -> None:
    """格子是定的 3:2；列变宽只是让它周围的白更多，不是格子自己横向展开。"""
    small_w, small_h = cell_size(row_height(200.0))
    large_w, large_h = cell_size(row_height(600.0))
    assert large_w > small_w
    assert large_w / large_h == pytest.approx(small_w / small_h, rel=0.01)


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


def test_the_hour_axis_is_long_but_not_endless() -> None:
    """24 行按格子比例推出来大约两千像素：滚得动，也不至于长到没有尽头。"""
    total = HOURS_PER_DAY * row_height(180.0)
    assert 1000 < total < 4000


def test_the_radius_is_a_corner_not_a_circle() -> None:
    assert 0 < CARD_RADIUS < 20
