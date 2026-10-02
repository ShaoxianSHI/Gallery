"""``gallery.ui.imaging`` 的测试：裸像素 → Qt 图像，以及缩放的位置（ADR 0015）。

这里钉的是两件事：

1. **交出去的 QImage 必须拥有自己的内存** —— 构造函数不复制缓冲区，源 bytes
   一被回收画面就是花屏。这条是模块开头那条铁律。
2. **缩放不放大**：只有当图比目标边长小才缩，小图按原样交出去 ——
   网格里有大量比 288 还小的源图，放大它们只是白白糊一层。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gallery.core.media.decode import RasterImage  # noqa: E402
from gallery.ui.imaging import to_qimage, to_scaled_qimage  # noqa: E402


def raster(width: int, height: int) -> RasterImage:
    return RasterImage(width=width, height=height, rgba=bytes(width * height * 4))


def test_to_qimage_keeps_the_raster_size() -> None:
    image = to_qimage(raster(64, 48))

    assert (image.width(), image.height()) == (64, 48)


def test_to_qimage_owns_its_buffer() -> None:
    """把源 bytes 丢掉之后图像仍然完好 —— 证明它没有借用那块内存。"""
    source = raster(32, 32)
    image = to_qimage(source)

    del source  # 源对象被回收，它那块 bytes 也就没了
    image = image.copy()  # 再过一道，绕开 PySide6 自己的引用计数

    assert (image.width(), image.height()) == (32, 32)


def test_scaled_to_the_requested_edge() -> None:
    image = to_scaled_qimage(raster(512, 384), 176)

    assert max(image.width(), image.height()) == 176


def test_small_images_are_not_enlarged() -> None:
    image = to_scaled_qimage(raster(64, 48), 176)

    assert (image.width(), image.height()) == (64, 48)


def test_zero_edge_means_no_scaling() -> None:
    image = to_scaled_qimage(raster(512, 384), 0)

    assert (image.width(), image.height()) == (512, 384)


def test_scaling_preserves_the_aspect_ratio() -> None:
    image = to_scaled_qimage(raster(400, 100), 200)

    assert (image.width(), image.height()) == (200, 50)


def test_scaled_image_owns_its_buffer() -> None:
    """缩放那条路同样不能借用源内存。"""
    source = raster(512, 512)
    image = to_scaled_qimage(source, 64)

    del source

    assert (image.width(), image.height()) == (64, 64)
