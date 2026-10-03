"""图像解码：把磁盘上的照片变成裸像素。

``core`` 不认识 Qt，所以这里返回的是 RGBA 字节流，由界面层包装成 ``QImage``。

四个容易踩的坑，集中记在这里：

1. Pillow 打开文件是**惰性**的，真正的解码发生在 ``load()`` / ``thumbnail()`` / ``tobytes()``；
   因此所有解码相关的异常都要包成 ``ImageDecodeError``，而不是让它裸奔到界面。
2. 竖拍照片带方向标记，必须摆正，否则界面里全是躺着的。这**只影响内存中的像素**，
   绝不写回文件。**注意别用 ``ImageOps.exif_transpose``**：它只读 EXIF 标签，而
   ``pillow-heif`` 在解码 HEIF 时会把那个标签重置成 1、把真值挪进
   ``info["original_orientation"]`` —— 于是经它摆正的竖拍 HEIC 全都是躺着的（ADR 0027 决策 5）。
   方向由 :func:`~gallery.core.media.edit.orientation_of` 读，那两个来源都认。
3. ``Image.draft`` 能让 JPEG 按 1/2、1/4、1/8 直接解码，做缩略图和适配窗口的大图预览时
   能快一个量级；它必须在任何解码动作之前调用。
4. ``Image.thumbnail`` 只缩不放，所以这里不需要额外判断是否要放大。

GIF 只取第一帧；RAW 不支持（见 ADR 0001）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PIL import Image, UnidentifiedImageError

from gallery.core.errors import ImageDecodeError
from gallery.core.media.edit import EditStep, apply_steps, orientation_of


def _register_heif_support() -> bool:
    try:
        import pillow_heif
    except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
        return False
    pillow_heif.register_heif_opener()
    return True


def _prepare_pillow() -> None:
    """在任何工作线程启动之前跑完 Pillow 的初始化。

    ``Image.open`` / ``Image.save`` 会惰性导入解码器，而那个「初始化过没有」的全局
    标志不是线程安全的：多个解码线程同时第一次用到 Pillow 时会并发走进导入逻辑，
    实测**直接段错误**（不是抛异常，是整个进程挂掉），而不是偶尔报个错。
    在导入本模块时（主线程）先初始化一遍即可根除。
    """
    Image.init()


#: HEIC/HEIF 支持是否可用（依赖 pillow-heif 提供的解码插件）。
#: 在导入时确定一次，之后不再变化，属于能力探测而非可变状态。
HEIF_SUPPORT: Final = _register_heif_support()

_prepare_pillow()


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

    @classmethod
    def from_pil(cls, image: Image.Image, *, has_alpha: bool) -> RasterImage:
        """把一张 PIL 图像转成 RGBA8888 并拷成 ``bytes``。

        转换出来的那张中间图随即被关掉：它可能是整幅全分辨率的缓冲区，不关就是
        一次内存泄漏。**收尾只在这一处**，两条解码路径（直接解码、读缓存条目）
        共用它 —— 那段「convert → tobytes → finally close」不该有两份。

        **已经是 RGBA 的图不转换。** ``convert("RGBA")`` 对一张 RGBA 图仍然会整幅复制，
        而编辑草稿那一路（见 :func:`decode_rgba` 的 ``steps``）本来就已经是 RGBA ——
        多这一次复制，全分辨率时就是多 128 MB 的峰值。
        """
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        try:
            return cls(
                width=rgba.width,
                height=rgba.height,
                rgba=rgba.tobytes(),
                has_alpha=has_alpha,
            )
        finally:
            if rgba is not image:
                rgba.close()


def has_transparency(image: Image.Image) -> bool:
    """这张图有没有需要保留的透明。

    **不能只看 ``getbands()`` 里有没有 ``A``**：调色板（模式 ``P``）的 PNG 把透明
    记在 ``info["transparency"]`` 里，``getbands()`` 返回的是 ``('P',)`` ——
    少了这一条，那些图会被当成不透明：``convert("RGBA")`` 明明给出了 alpha 通道，
    我们却判成没有，于是缩略图按 JPEG 存盘，透明像素**永久**变成黑色背景
    （不是显示问题，是写进缓存之后再也回不来）。
    """
    return "A" in image.getbands() or "transparency" in image.info


#: 存着的方向 → 摆正它要做的变换。1（正常）是恒等，单独处理。
_TRANSPOSE_BY_ORIENTATION: Final[dict[int, Image.Transpose]] = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def upright(image: Image.Image) -> Image.Image:
    """按存着的方向把图摆正。方向不是 1 时返回**新图**，``image`` 本身不动。

    方向由 :func:`~gallery.core.media.edit.orientation_of` 读：EXIF 标签与
    ``info["original_orientation"]`` 两个来源都认，后者是 ``pillow-heif`` 藏起 HEIF
    方向的地方（见模块文档那条坑）。

    方向本来就是 1 时**原样返回同一个对象**而不是复制一份：那正是绝大多数照片，
    而一次全分辨率复制对一张 32 MP 的照片就是 128 MB。
    """
    method = _TRANSPOSE_BY_ORIENTATION.get(orientation_of(image))
    return image if method is None else image.transpose(method)


def decode_rgba(
    path: Path,
    *,
    max_edge: int | None = None,
    use_draft: bool = False,
    steps: tuple[EditStep, ...] = (),
) -> RasterImage:
    """解码 ``path`` 为 RGBA 像素。

    :param max_edge: 若给出，长边会被缩放到不超过该值（只缩不放）。
    :param use_draft: 允许 JPEG 走 DCT 缩放快速解码。仅在 ``max_edge`` 远小于原图时有意义。
    :param steps: 编辑草稿（ADR 0027）。**在摆正之后、缩放之前**施加：草稿记的是
        「用户看着的那张图」上的坐标，而那一���正是摆正后的图；放到缩放之后虽然像素
        一样，但多一份全分辨率缓冲 —— 32 MP 的照片那是 128 MB。
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
            # ``exif_transpose`` 无论转不转都返回一张新图，因此下面这一步改的是它
            # 自己，不会动到 ``image`` 的像素 —— 而草稿要叠加在摆正**之后**。
            oriented = upright(image)
            edited = apply_steps(oriented, steps)
            try:
                if max_edge is not None and max(edited.size) > max_edge:
                    edited.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                return RasterImage.from_pil(edited, has_alpha=has_transparency(edited))
            finally:
                if edited is not oriented:
                    edited.close()
                if oriented is not image:
                    oriented.close()
    except UnidentifiedImageError as exc:
        raise ImageDecodeError(_unidentified_message(path), path=path, detail=str(exc)) from exc
    except Image.DecompressionBombError as exc:
        raise ImageDecodeError(
            f"这张图片的像素数大到不像照片，出于安全考虑没有打开：{path.name}。",
            path=path,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        # 「文件不在了」与「文件坏了」是两件事，混成一句话会吓人：用户刚把照片移到
        # 别处（或删掉）时，一次迟到的解码失败会告诉他「文件可能已损坏」——
        # 于是一次成功的移动被说成了数据损坏，用户很可能就此去抢救根本没坏的文件。
        raise ImageDecodeError(
            f"无法解码 {path.name}：文件已经不在原处。",
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


__all__ = ["HEIF_SUPPORT", "RasterImage", "decode_rgba", "has_transparency", "upright"]
