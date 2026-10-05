"""``gallery.core`` 的裸像素 → Qt 图像。

``QImage`` 的构造函数**不复制**传入的缓冲区，所以这里一律 ``.copy()``；``QImage`` 可以在工作线程
里构造而 ``QPixmap`` 只能在 GUI 线程构造，因此缩放与解码一起留在后台（ADR 0015）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from gallery.core.media.decode import RasterImage

#: RGBA8888 每像素字节数。
_RGBA_BYTES_PER_PIXEL = 4


def to_qimage(raster: RasterImage) -> QImage:
    """把 ``RasterImage`` 包装成 Qt 图像（拥有独立缓冲区）。"""
    return _wrap(raster).copy()


def to_scaled_qimage(raster: RasterImage, edge: int) -> QImage:
    """把 ``RasterImage`` 缩到长边不超过 ``edge``（只缩不放），并包装成 Qt 图像。

    **合成一步是有意的**：``QImage(bytes, …)`` 不复制缓冲区，而 ``scaled()`` 返回的新图自己
    拥有内存，中间那张只要活过这一次调用就行，不必再整幅 ``copy()`` 一遍。

    :param edge: 目标长边；``<= 0`` 表示不缩放。
    """
    return _scale(_wrap(raster), edge)


def scale_image(image: QImage, edge: int) -> QImage:
    """把 ``QImage`` 缩到长边不超过 ``edge``（只缩不放），与 :func:`scale_pixmap` 同一个实现。

    日历那一侧存的是 ``QImage``，换档时那次兜底缩放也走这里 —— 不另写一份，免得换算法漏掉。
    """
    return _scale(image, edge)


def scale_pixmap(pixmap: QPixmap, edge: int) -> QPixmap:
    """把 ``QPixmap`` 缩到长边不超过 ``edge``（只缩不放），并包装成 Qt 图像。

    「只缩不放、保持比例、平滑」这三条曾经在这里、``browser.model`` 与 ``browser.grid``
    各写一遍：换一次缩放算法要改三处，而其中两处在主线程上 —— 正是 ADR 0015 决策 3
    当初要搬走的那类活。
    """
    return _scale(pixmap, edge)


def _scale(image: QPixmap, edge: int) -> QPixmap:
    """缩放的唯一实现：只缩不放、保持比例、平滑。``QImage`` 与 ``QPixmap`` 同名方法。"""
    if edge <= 0 or (image.width() <= edge and image.height() <= edge):
        # 原图本来就够小 —— 不放大。交出去的那张必须拥有自己的内存
        # （``QImage(bytes, …)`` 不复制缓冲区，Python 侧一回收画面就花屏）。
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


__all__ = ["scale_image", "to_qimage", "to_scaled_qimage"]
