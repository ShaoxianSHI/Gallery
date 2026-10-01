"""``gallery.ui.viewer.zoom`` 的测试：缩放计算不需要 Qt。"""

from __future__ import annotations

import pytest

from gallery.ui.viewer.zoom import (
    FIT_MARGIN,
    MAX_FIT_SCALE,
    MAX_SCALE,
    MIN_SCALE,
    clamp_scale,
    fit_scale,
    is_fit,
    zoom_in,
    zoom_out,
)


def test_fit_landscape_image_into_landscape_viewport() -> None:
    scale = fit_scale((4000, 2000), (1000, 500))

    assert scale == pytest.approx(1000 / 4000 * FIT_MARGIN)


def test_fit_is_limited_by_the_tighter_axis() -> None:
    # 竖图放进横视口时，高度先到顶。
    scale = fit_scale((1000, 4000), (1000, 1000))

    assert scale == pytest.approx(1000 / 4000 * FIT_MARGIN)


def test_fit_upscales_small_images_by_default() -> None:
    assert fit_scale((100, 100), (1000, 1000)) > 1.0


def test_fit_can_refuse_to_upscale() -> None:
    assert fit_scale((100, 100), (1000, 1000), allow_upscale=False) == pytest.approx(1.0)


@pytest.mark.parametrize("viewport", [(0, 0), (1000, 0), (0, 1000), (-5, 100)])
def test_fit_with_degenerate_viewport(viewport: tuple[int, int]) -> None:
    assert fit_scale((100, 100), viewport) == 1.0


@pytest.mark.parametrize("image", [(0, 0), (100, 0)])
def test_fit_with_degenerate_image(image: tuple[int, int]) -> None:
    assert fit_scale(image, (1000, 1000)) == 1.0


def test_fit_is_not_capped_by_the_manual_zoom_limit() -> None:
    """「适应窗口」不能被手动缩放上限（MAX_SCALE）卡住。

    小图铺满视口需要的倍数天然大于 8：168 像素的缩略图放进 2400×1378 的视口要 12 倍。
    曾经被卡在 8 倍，于是缩略图缩在中间、全图铺满 —— 打开照片时看到的就是「先小一大截」。
    """
    viewport = (2400, 1378)
    thumbnail = (168, 111)

    scale = fit_scale(thumbnail, viewport)

    assert scale > MAX_SCALE, "小图铺满视口需要超过 8 倍，不该被手动缩放上限卡住"
    assert scale <= MAX_FIT_SCALE
    # 铺满：高度方向顶到视口（留出 FIT_MARGIN 的边距）
    assert thumbnail[1] * scale == pytest.approx(viewport[1] * FIT_MARGIN)


def test_placeholder_thumbnail_fills_exactly_like_the_full_image() -> None:
    """占位缩略图与真正的全图，占的地方必须一样大。

    这是用户能直接看到的那个症状：缓存未命中时先用缩略图顶着，全图到了不该「变大一圈」。
    """
    viewport = (2400, 1378)
    full = (2400, 1600)
    thumbnail = (168, 112)  # 与全图同比例的缩略图

    placeholder_height = thumbnail[1] * fit_scale(thumbnail, viewport)
    full_height = full[1] * fit_scale(full, viewport)

    assert placeholder_height == pytest.approx(full_height, rel=0.01)


def test_clamp_scale_bounds() -> None:
    assert clamp_scale(0.0001) == MIN_SCALE
    assert clamp_scale(100.0) == MAX_SCALE
    assert clamp_scale(1.0) == 1.0
    assert clamp_scale(100.0, maximum=None) == 100.0


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan")])
def test_clamp_scale_rejects_garbage(value: float) -> None:
    assert clamp_scale(value) == MIN_SCALE


def test_zoom_steps_are_clamped_and_reversible() -> None:
    assert zoom_in(1.0) > 1.0
    assert zoom_out(1.0) < 1.0
    assert zoom_out(zoom_in(1.0)) == pytest.approx(1.0)
    assert zoom_in(MAX_SCALE) == MAX_SCALE
    assert zoom_out(MIN_SCALE) == MIN_SCALE


def test_is_fit_tolerates_rounding() -> None:
    fit = 0.5

    assert is_fit(0.5, fit) is True
    assert is_fit(0.5001, fit) is True
    assert is_fit(0.6, fit) is False
    assert is_fit(1.0, 0.0) is False
