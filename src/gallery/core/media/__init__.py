"""像素：解码、编辑草稿与缩略图磁盘缓存。

解码一律落在这里，界面层拿到的永远是裸像素（:class:`RasterImage`），
自己包装成 Qt 图像——见 ``gallery.ui.imaging``。缓存可丢弃，重扫即可恢复。
:mod:`~gallery.core.media.edit` 里是编辑草稿的运算（纯函数，不碰磁盘），它由
:func:`~gallery.core.media.decode.decode_rgba` 在解码之后施加。
"""

from gallery.core.media.decode import HEIF_SUPPORT, RasterImage, decode_rgba, upright
from gallery.core.media.edit import (
    Crop,
    EditStep,
    Mirror,
    Rotate,
    apply_steps,
    compose_orientation,
    is_identity,
    orientation_of,
)
from gallery.core.media.thumbs import ThumbnailCache

__all__ = [
    "HEIF_SUPPORT",
    "Crop",
    "EditStep",
    "Mirror",
    "RasterImage",
    "Rotate",
    "ThumbnailCache",
    "apply_steps",
    "compose_orientation",
    "decode_rgba",
    "is_identity",
    "orientation_of",
    "upright",
]
