"""``gallery.ui.zoom`` 的测试：缩放计算不需要 Qt。"""

from __future__ import annotations

import pytest

from gallery.ui.zoom import (
    FIT_MARGIN,
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


def test_clamp_scale_bounds() -> None:
    assert clamp_scale(0.0001) == MIN_SCALE
    assert clamp_scale(100.0) == MAX_SCALE
    assert clamp_scale(1.0) == 1.0


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
