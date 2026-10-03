"""裁剪浮层的几何（ADR 0027）。

**这三条是纯函数，因此不必起一个窗口。** 框的判定与拖动是这一档里最容易「差一个像素」
的地方，而差一个像素在照片上就是「框线与裁出来的东西对不上」。
"""

from __future__ import annotations

from gallery.ui.viewer.crop import (
    HANDLE_HIT,
    MIN_VIEW_EDGE,
    Handle,
    handle_at,
    move_rect,
    rect_from_drag,
    resize_rect,
)

#: 一张 800×600 的照片。
BOUNDS = (0.0, 0.0, 800.0, 600.0)
#: 中间那个框。
RECT = (100.0, 50.0, 700.0, 550.0)


def test_a_corner_is_that_handles_handle() -> None:
    assert handle_at(RECT, 100.0, 50.0) is Handle.NORTH_WEST
    assert handle_at(RECT, 700.0, 50.0) is Handle.NORTH_EAST
    assert handle_at(RECT, 700.0, 550.0) is Handle.SOUTH_EAST
    assert handle_at(RECT, 100.0, 550.0) is Handle.SOUTH_WEST


def test_the_mid_sides_are_handles_too() -> None:
    assert handle_at(RECT, 400.0, 50.0) is Handle.NORTH
    assert handle_at(RECT, 400.0, 550.0) is Handle.SOUTH
    assert handle_at(RECT, 700.0, 300.0) is Handle.EAST
    assert handle_at(RECT, 100.0, 300.0) is Handle.WEST


def test_handles_win_over_moving_the_whole_box() -> None:
    """角上那一小块既是手柄也是框 —— 不判优先级的话，用户想缩框却拖动了整块。"""
    assert handle_at(RECT, 100.0 + HANDLE_HIT - 1, 50.0) is Handle.NORTH_WEST
    assert handle_at(RECT, 400.0, 300.0) is Handle.MOVE


def test_outside_the_box_means_start_a_new_one() -> None:
    assert handle_at(RECT, 20.0, 20.0) is Handle.NEW
    assert handle_at(RECT, 790.0, 590.0) is Handle.NEW


def test_dragging_the_right_edge_widens_the_box() -> None:
    assert resize_rect(RECT, BOUNDS, 760.0, 300.0, Handle.EAST) == (100.0, 50.0, 760.0, 550.0)


def test_dragging_past_the_other_edge_mirrors_it() -> None:
    """把右边缘拖过左边缘，框就镜像过去了 —— 访达与预览都这样，也省掉了「换手柄」那套分支。"""
    left, top, right, bottom = resize_rect(RECT, BOUNDS, 40.0, 300.0, Handle.EAST)
    assert (left, right) == (40.0, 100.0)
    assert (top, bottom) == (50.0, 550.0)


def test_the_box_never_leaves_the_photo() -> None:
    left, top, right, bottom = resize_rect(RECT, BOUNDS, -500.0, -500.0, Handle.NORTH_WEST)
    assert (left, top) == (0.0, 0.0)
    assert (right, bottom) == (700.0, 550.0)


def test_the_box_never_gets_smaller_than_the_floor() -> None:
    left, _, right, _ = resize_rect(RECT, BOUNDS, 700.0, 300.0, Handle.EAST)
    assert right - left >= MIN_VIEW_EDGE
    left, _, right, _ = resize_rect(RECT, BOUNDS, 99.0, 50.0, Handle.WEST)
    assert right - left >= MIN_VIEW_EDGE


def test_moving_the_box_shifts_all_four_edges() -> None:
    assert move_rect(RECT, BOUNDS, 50.0, 25.0) == (150.0, 75.0, 750.0, 575.0)


def test_moving_the_box_stops_at_the_photo_edge() -> None:
    """拖到边就不再往那边走 —— 整块平移不夹住的话，框会被拖出照片之外。"""
    left, top, right, bottom = move_rect(RECT, BOUNDS, 9999.0, 9999.0)
    assert (left, top) == (200.0, 100.0)
    assert (right, bottom) == (800.0, 600.0)
    left, top, right, bottom = move_rect(RECT, BOUNDS, -9999.0, -9999.0)
    assert (left, top) == (0.0, 0.0)


def test_dragging_a_new_box_normalizes_the_direction() -> None:
    """从右下往左上拖与从左上往右下拖得到同一个框。"""
    assert rect_from_drag(BOUNDS, 700.0, 550.0, 100.0, 50.0) == (100.0, 50.0, 700.0, 550.0)
    assert rect_from_drag(BOUNDS, 100.0, 50.0, 700.0, 550.0) == (100.0, 50.0, 700.0, 550.0)


def test_a_click_without_a_drag_still_leaves_a_usable_box() -> None:
    """点一下（没有拖）也要留下一个框 —— 上来就一个 0×0 的框，用户没法拖它的角。"""
    left, top, right, bottom = rect_from_drag(BOUNDS, 400.0, 300.0, 400.0, 300.0)
    assert right - left >= MIN_VIEW_EDGE
    assert bottom - top >= MIN_VIEW_EDGE


def test_a_new_box_is_clipped_to_the_photo() -> None:
    left, top, right, bottom = rect_from_drag(BOUNDS, -100.0, -100.0, 9999.0, 9999.0)
    assert (left, top, right, bottom) == BOUNDS
