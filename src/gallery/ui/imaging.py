"""``gallery.core`` 的裸像素 → Qt 图像。

两条规则写在这里，避免以后重犯：

1. ``QImage`` 的构造函数**不复制**传入的缓冲区。Python ``bytes`` 一旦被回收，
   画面就是花屏或直接崩溃 —— 所以这里一律 ``.copy()``，让 QImage 拥有自己的内存。
2. ``QImage`` 可以在工作线程里构造，``QPixmap`` 只能在 GUI 线程构造。
   因此后台任务只产出 ``QImage``，转 ``QPixmap`` 的动作一律留在主线程。
"""

from __future__ import annotations

from PySide6.QtGui import QImage

from gallery.core.media.decode import RasterImage

#: RGBA8888 每像素字节数。
_RGBA_BYTES_PER_PIXEL = 4


def to_qimage(raster: RasterImage) -> QImage:
    """把 ``RasterImage`` 包装成 Qt 图像（拥有独立缓冲区）。"""
    image = QImage(
        raster.rgba,
        raster.width,
        raster.height,
        raster.width * _RGBA_BYTES_PER_PIXEL,
        QImage.Format.Format_RGBA8888,
    )
    return image.copy()


__all__ = ["to_qimage"]
