"""像素：解码与缩略图磁盘缓存。

解码一律落在这里，界面层拿到的永远是裸像素（:class:`RasterImage`），
自己包装成 Qt 图像——见 ``gallery.ui.imaging``。缓存可丢弃，重扫即可恢复。
"""

from gallery.core.media.decode import HEIF_SUPPORT, RasterImage, decode_rgba
from gallery.core.media.thumbs import ThumbnailCache

__all__ = [
    "HEIF_SUPPORT",
    "RasterImage",
    "ThumbnailCache",
    "decode_rgba",
]
