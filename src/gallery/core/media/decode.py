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
5. **``open()`` 与 ``load()`` 可能永远不回来** —— 见 :func:`_within_deadline`。

GIF 只取第一帧；RAW 不支持（见 ADR 0001）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from gallery.core.cancellation import never_cancel
from gallery.core.errors import ImageDecodeError

#: 读一张照片最多等多久（秒）。
#:
#: 给得不算小：50MP 的照片冷解码就要好几秒，云盘上第一次拉下来更慢。超过它就当
#: 「这张根本读不出来」，报一句**用户可修复**的话（见 :func:`_unreachable_message`），
#: 而不是继续占着工作线程 —— 那正是「退不出去」的成因（见 :func:`_within_deadline`）。
READ_TIMEOUT_SECONDS: Final = 20.0

#: 等解码时轮询的间隔（秒）。够细，取消才能立刻生效。
_READ_POLL_SECONDS: Final = 0.02

#: 同时最多允许有多少个「已经超了时限、只能丢在那儿」的读。
#:
#: 超时的那个**收不回来**：它多半正卡在 ``open()`` 里，我们只能丢下它。这个计数是
#: 那道闸 —— 没有它，一批读不出来的照片就能攒出成百上千个线程把机器拖垮。攒到这个
#: 数量时不再新开一个，直接报错：真到了那一步机器已经读不动了，多等那 20 秒没有意义。
MAX_STUCK_READS: Final = 8

#: 已经超了时限、还活着的读的个数。只由 :func:`_within_deadline` 改，且都在锁下改 ——
#: 它是本模块内部的一个计数，不是用来传递库路径 / 配置的全局状态。
_stuck_reads = 0
_stuck_lock = threading.Lock()


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
        """
        rgba = image.convert("RGBA")
        try:
            return cls(
                width=rgba.width,
                height=rgba.height,
                rgba=rgba.tobytes(),
                has_alpha=has_alpha,
            )
        finally:
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


def decode_rgba(
    path: Path,
    *,
    max_edge: int | None = None,
    use_draft: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> RasterImage:
    """解码 ``path`` 为 RGBA 像素。

    :param max_edge: 若给出，长边会被缩放到不超过该值（只缩不放）。
    :param use_draft: 允许 JPEG 走 DCT 缩放快速解码。仅在 ``max_edge`` 远小于原图时有意义。
    :param should_cancel: 读的过程中问一句「还要继续吗」（切库、关窗时用）。
    :raises ImageDecodeError: 文件损坏、被截断、读不出来，或格式不受支持。
    """
    path = Path(path)
    return _within_deadline(
        path,
        lambda: _decode_now(path, max_edge=max_edge, use_draft=use_draft),
        cancelled=should_cancel or never_cancel,
    )


def _within_deadline(
    path: Path, work: Callable[[], RasterImage], *, cancelled: Callable[[], bool]
) -> RasterImage:
    """跑 ``work``，但最多只等 :data:`READ_TIMEOUT_SECONDS`。

    **为什么非得有这一层**：``open()`` 与 ``load()`` 都是**不可打断**的系统调用。
    云盘里的占位照片（OneDrive 之类）没有下完时，读它会让内核一直等文件提供器把数据
    拉回来 —— 没有网络就永远不返回。于是一个解码任务会把一个工作线程**永久**占住：
    占满之后整条缩略图流水线停摆（什么照片都不再出图），关窗时 ``closeEvent`` 等满 2 秒
    放弃，而 Qt 的线程池在析构时又要 join 这些线程 —— **进程永远退不出去**。
    现场就是「丢进来一张读不出来的照片，然后连 ⌘Q 都按不动，只能强制退出」。

    做法是把 ``work`` 放到一个**守护线程**上等：到了时限就抛错走人，那个线程留在原地
    （它多半正卡在 ``open()`` 里）。守护线程不会被 ``threading`` 收尾时 join，因此它不会
    把进程拖住 —— 这正是「读不出来」与「退不出去」必须在这里一起解决的原因。

    实测开销可以忽略：4000×3000 的 JPEG 冷解码约 45 ms，多开一次线程约 0.3 ms。
    """
    global _stuck_reads

    outcome = _Outcome()
    finished = threading.Event()

    def run() -> None:
        try:
            outcome.value = work()
        except BaseException as exc:  # noqa: BLE001 - 原样转交给调用方
            outcome.error = exc
        finally:
            finished.set()

    with _stuck_lock:
        if _stuck_reads >= MAX_STUCK_READS:
            raise ImageDecodeError(_unreachable_message(path), path=path)
    threading.Thread(target=run, name="gallery-decode", daemon=True).start()

    deadline = time.monotonic() + READ_TIMEOUT_SECONDS
    while not finished.wait(_READ_POLL_SECONDS):
        if cancelled():
            # 结果反正没人要（切库、关窗），立刻放手：继续等下去只会拖慢退出。
            raise ImageDecodeError(f"已取消读取：{path.name}。", path=path)
        if time.monotonic() >= deadline:
            with _stuck_lock:
                _stuck_reads += 1
            raise ImageDecodeError(_unreachable_message(path), path=path)

    if outcome.error is not None:
        raise outcome.error
    if outcome.value is None:  # pragma: no cover - run() 只会写出这两者之一
        raise ImageDecodeError(_unreachable_message(path), path=path)
    return outcome.value


class _Outcome:
    """``run`` 交给等待方的那一份结果：两个字段恰好互斥。"""

    __slots__ = ("error", "value")

    def __init__(self) -> None:
        self.value: RasterImage | None = None
        self.error: BaseException | None = None


def _decode_now(path: Path, *, max_edge: int | None, use_draft: bool) -> RasterImage:
    """真正去解码：``open`` → ``draft`` → 摆正 → 缩放 → 取像素。

    这里**没有时限** —— 时限由外面那一层 :func:`_within_deadline` 负责。

    **文件由我们自己打开**，而不是交给 ``Image.open(path)``，因为那样才分得清两件事：
    我们打不开（没权限 / 云盘占位还没下完 / 已经被移走）与**打开了却读不出像素**
    （损坏、截断、编码器不认）。前者绝不能被说成「已损坏」—— 用户很可能就此去抢救
    一个根本没坏的文件（这个仓库一直守着这条，见下面 ``FileNotFoundError`` 那段注释）。
    顺带的好处是失败时句柄一定被关掉。
    """
    try:
        stream = path.open("rb")
    except FileNotFoundError as exc:
        raise ImageDecodeError(
            f"无法解码 {path.name}：文件已经不在原处。",
            path=path,
            detail=str(exc),
        ) from exc
    except OSError as exc:
        # 打不开：无权限、云盘占位还没下完、把目录当成了文件……
        raise ImageDecodeError(_unreachable_message(path), path=path, detail=str(exc)) from exc

    try:
        with stream, Image.open(stream) as image:
            if (
                use_draft
                and max_edge is not None
                and (image.format or "").upper() in {"JPEG", "MPO"}
            ):
                image.draft("RGB", (max_edge, max_edge))
            oriented = _oriented(image)
            if max_edge is not None and max(oriented.size) > max_edge:
                oriented.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            return RasterImage.from_pil(oriented, has_alpha=has_transparency(oriented))
    except UnidentifiedImageError as exc:
        raise ImageDecodeError(_unidentified_message(path), path=path, detail=str(exc)) from exc
    except Image.DecompressionBombError as exc:
        raise ImageDecodeError(_bomb_message(path), path=path, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        # 打开了，但像素读不出来：损坏、截断、编码器不认。
        raise ImageDecodeError(
            f"无法解码 {path.name}：文件可能已损坏或被截断。",
            path=path,
            detail=str(exc),
        ) from exc


#: EXIF Orientation 里「不用转」的那几个值（缺省、正常、以及只带镜像标记的 0/1）。
_NO_ROTATION = frozenset({None, 0, 1})


def _oriented(image: Image.Image) -> Image.Image:
    """按 EXIF Orientation 把图摆正。**转不动时原样返回，不复制。**

    :func:`PIL.ImageOps.exif_transpose` 不管有没有 Orientation 都会返回一张**新图**
    （不转时是 ``image.copy()``），于是每解一张没标记方向的照片就白白付一次全分辨率
    拷贝：一张 50MP 的原图那是约 200 MB 的分配 + 一次 memcpy，而大图预览这条路上
    ``use_draft`` 常常关着，付的就是整幅。

    这里先读一次 Orientation（只解析 APP1 那一段，不触发解码），转不动就直接把
    原图交出去 —— 下游 ``thumbnail`` 改的就是它自己，而 ``image`` 是我们自己
    ``open`` 出来的局部对象，由 ``with`` 负责关掉，没有别处共享它。

    转得动的那几张仍然交给 ``exif_transpose``：它顺手把 Orientation 标记摘掉，
    而「这一格到底摆没摆正」是解码这层的责任，不该留给调用方各自判断。
    """
    try:
        orientation = image.getexif().get(ExifTags.Base.Orientation)
    except (OSError, ValueError, SyntaxError):
        # 坏掉的 EXIF 不该让整张照片解不出来（那属于「像素读不出」，由下面统一报）。
        return image
    if orientation in _NO_ROTATION:
        return image
    return ImageOps.exif_transpose(image)


def _bomb_message(path: Path) -> str:
    return f"这张图片的像素数大到不像照片，出于安全考虑没有打开：{path.name}。"


def _unreachable_message(path: Path) -> str:
    """读不出内容（多半是没下完）时说的话 —— 必须让用户知道该做什么。

    这与「文件损坏」是**两回事**，措辞也不同：云盘同步中的照片等一会儿就好了，
    照着「已损坏」去抢救文件只会白忙一场。
    """
    return (
        f"读不出 {path.name}：文件可能还没下载完（云盘同步中的照片），"
        f"或者正被别的程序占用。等它下载完再打开即可。"
    )


def _unidentified_message(path: Path) -> str:
    if path.suffix.lower() in {".heic", ".heif"} and not HEIF_SUPPORT:
        return f"缺少 HEIC/HEIF 解码支持，无法打开 {path.name}（需要 pillow-heif）。"
    return f"这不是一张能识别的图片：{path.name}。文件内容可能与扩展名不符。"


__all__ = [
    "HEIF_SUPPORT",
    "MAX_STUCK_READS",
    "READ_TIMEOUT_SECONDS",
    "RasterImage",
    "decode_rgba",
    "has_transparency",
]
