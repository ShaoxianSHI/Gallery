"""图像解码：把磁盘上的照片变成裸像素。

``core`` 不认识 Qt，所以这里返回的是 RGBA 字节流，由界面层包装成 ``QImage``。

四个容易踩的坑，集中记在这里：

1. Pillow 打开文件是**惰性**的，真正的解码发生在 ``load()`` / ``thumbnail()`` / ``tobytes()``；
   因此所有解码相关的异常都要包成 ``ImageDecodeError``，而不是让它裸奔到界面。
2. 竖拍照片带 EXIF Orientation，必须用 ``ImageOps.exif_transpose`` 摆正，否则界面里全是躺着的。
   这**只影响内存中的像素**，绝不写回文件。
3. ``Image.draft`` 能让 JPEG 按 1/2、1/4、1/8 直接解码，做缩略图和适配窗口的大图预览时
   能快一个量级；它必须在任何解码动作之前调用。
4. ``Image.thumbnail`` 只缩不放，所以这里不需要额外判断是否要放大。

GIF 只取第一帧；RAW 不支持（见 ADR 0001）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from gallery.core.errors import ImageDecodeError


def _register_heif_support() -> bool:
    try:
        import pillow_heif
    except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
        return False
    pillow_heif.register_heif_opener()
    return True


#: HEIC/HEIF 支持是否可用（依赖 pillow-heif 提供的解码插件）。
#: 在导入时确定一次，之后不再变化，属于能力探测而非可变状态。
HEIF_SUPPORT: Final = _register_heif_support()


@dataclass(frozen=True, slots=True)
class RasterImage:
    """已解码的图像：RGBA8888、行优先、无填充字节。"""

    width: int
    height: int
    rgba: bytes
    #: 源图是否带透明通道。决定缩略图落盘用 PNG（保留透明）还是 JPEG（体积小）。
    has_alpha: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def nbytes(self) -> int:
        return len(self.rgba)


def decode_rgba(path: Path, *, max_edge: int | None = None, use_draft: bool = False) -> RasterImage:
    """解码 ``path`` 为 RGBA 像素。

    :param max_edge: 若给出，长边会被缩放到不超过该值（只缩不放）。
    :param use_draft: 允许 JPEG 走 DCT 缩放快速解码。仅在 ``max_edge`` 远小于原图时有意义。
    :raises ImageDecodeError: 文件损坏、被截断，或格式不受支持。
    """
    path = Path(path)
    try:
        with Image.open(path) as image:
            if (
                use_draft
                and max_edge is not None
                and (image.format or "").upper() in {"JPEG", "MPO"}
            ):
                image.draft("RGB", (max_edge, max_edge))
            oriented = ImageOps.exif_transpose(image)
            if max_edge is not None and max(oriented.size) > max_edge:
                oriented = oriented.copy()
                oriented.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            has_alpha = "A" in oriented.getbands()
            rgba = oriented.convert("RGBA")
            try:
                return RasterImage(
                    width=rgba.width,
                    height=rgba.height,
                    rgba=rgba.tobytes(),
                    has_alpha=has_alpha,
                )
            finally:
                rgba.close()
    except UnidentifiedImageError as exc:
        raise ImageDecodeError(_unidentified_message(path), path=path, detail=str(exc)) from exc
    except Image.DecompressionBombError as exc:
        raise ImageDecodeError(
            f"这张图片的像素数大到不像照片，出于安全考虑没有打开：{path.name}。",
            path=path,
            detail=str(exc),
        ) from exc
    except (OSError, ValueError) as exc:
        raise ImageDecodeError(
            f"无法解码 {path.name}：文件可能已损坏或被截断。",
            path=path,
            detail=str(exc),
        ) from exc


def _unidentified_message(path: Path) -> str:
    if path.suffix.lower() in {".heic", ".heif"} and not HEIF_SUPPORT:
        return f"缺少 HEIC/HEIF 解码支持，无法打开 {path.name}（需要 pillow-heif）。"
    return f"这不是一张能识别的图片：{path.name}。文件内容可能与扩展名不符。"


__all__ = ["HEIF_SUPPORT", "RasterImage", "decode_rgba"]
