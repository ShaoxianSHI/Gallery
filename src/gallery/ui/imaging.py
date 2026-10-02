"""``gallery.core`` 的裸像素 → Qt 图像。

两条规则写在这里，避免以后重犯：

1. ``QImage`` 的构造函数**不复制**传入的缓冲区。Python ``bytes`` 一旦被回收，
   画面就是花屏或直接崩溃 —— 所以这里一律 ``.copy()``，让 QImage 拥有自己的内存。
2. ``QImage`` 可以在工作线程里构造，``QPixmap`` 只能在 GUI 线程构造。
   因此后台任务只产出 ``QImage``，转 ``QPixmap`` 的动作一律留在主线程。

**同一条规则还决定了缩放该待在哪**（ADR 0015）：``QImage.scaled`` 与
``QPixmap.scaled`` 一样快（实测都是 0.08 ms），但只有前者能在工作线程上跑 ——
所以缩放属于后台那半，界面线程只留下一次 ``QPixmap.fromImage``。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from gallery.core.media.decode import RasterImage

#: RGBA8888 每像素字节数。
_RGBA_BYTES_PER_PIXEL = 4


def to_qimage(raster: RasterImage) -> QImage:
    """把 ``RasterImage`` 包装成 Qt 图像（拥有独立缓冲区）。"""
    return _wrap(raster).copy()


def to_scaled_qimage(raster: RasterImage, edge: int) -> QImage:
    """把 ``RasterImage`` 缩到长边不超过 ``edge``（只缩不放），并包装成 Qt 图像。

    **合成一步是有意的**：直接 ``to_qimage`` 再 ``scaled``，中间那张
    ``QImage.copy()``（几百 KB 的整幅拷贝）还没等任何人引用就被丢掉了。
    ``QImage(bytes, …)`` 不复制缓冲区，而 ``scaled()`` 返回的是自己拥有内存的
    新图 —— 中间那张只要活过这一次调用就行，不必 ``copy()``。

    :param edge: 目标长边；``<= 0`` 表示不缩放，按原尺寸交出去。
    """
    image = _wrap(raster)
    if edge <= 0 or (image.width() <= edge and image.height() <= edge):
        # 原图本来就够小 —— 不放大。交出去的那张必须拥有自己的内存。
        return image.copy()
    return image.scaled(
        edge,
        edge,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _wrap(raster: RasterImage) -> QImage:
    """借用 ``raster`` 的缓冲区包一层 QImage（**不复制**，只在上面待一瞬）。"""
    return QImage(
        raster.rgba,
        raster.width,
        raster.height,
        raster.width * _RGBA_BYTES_PER_PIXEL,
        QImage.Format.Format_RGBA8888,
    )


__all__ = ["to_qimage", "to_scaled_qimage"]
