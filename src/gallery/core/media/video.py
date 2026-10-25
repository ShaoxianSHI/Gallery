"""视频取第一帧：macOS 的 AVFoundation，经 PyObjC 动态加载（ADR 0090）。

**为什么不用 Qt**：``core`` 不导入 Qt，而缩略图必须在工作线程、没有事件循环的地方产出；
Qt 的播放器是异步的，还会把 4K 帧整幅解出来。这里只要「第 0 秒那一帧，缩到 512」。
**为什么不是新依赖**：``pyobjc-core`` 已在依赖里，``objc.loadBundle`` 能直接加载系统框架；
代价是拿不到 ObjC 方法的出参（``actualTime`` / ``error``）：只认返回值，空指针就是失败。
"""

from __future__ import annotations

import importlib
import io
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from PIL import Image

from gallery.core.errors import ImageDecodeError

#: 系统框架路径。**不能写 ``import AVFoundation``**：那要另一个 PyObjC 子包。
_FRAMEWORK: Final = "/System/Library/Frameworks/AVFoundation.framework"

#: ``NSBitmapImageFileTypePNG``。只要 PNG，Pillow 接着读它 —— 比手拆 CGImage 的像素格式稳。
_PNG_TYPE: Final = 4

#: ``CMTime`` 的布局：``value`` / ``timescale`` / ``flags`` / ``epoch``。
#: 0 时刻用 ``flags=1``（时间有效）。
_CMTIME_LAYOUT: Final = b"{CMTime=qiIq}"


@dataclass(frozen=True, slots=True)
class _Binding:
    """加载成功之后要用的那几样：两个类、时间结构、URL 与位图工厂。"""

    asset: Any
    generator: Any
    time: Any
    url: Any
    bitmap: Any


def _load() -> _Binding | None:
    """加载 AVFoundation 并取出要用的类；非 macOS 或框架缺失时返回 ``None``。"""
    if sys.platform != "darwin":
        return None
    try:
        objc = importlib.import_module("objc")
        foundation = importlib.import_module("Foundation")
        appkit = importlib.import_module("AppKit")
    except ImportError:  # pragma: no cover - 非 macOS 的正常分支
        return None
    try:
        objc.loadBundle("AVFoundation", {}, bundle_path=_FRAMEWORK)
        time_type = objc.createStructType(
            "CMTime", _CMTIME_LAYOUT, ["value", "timescale", "flags", "epoch"]
        )
        asset = objc.lookUpClass("AVURLAsset")
        generator = objc.lookUpClass("AVAssetImageGenerator")
    except Exception:  # pragma: no cover - 系统框架理论上不会缺
        return None
    # CGImageRef 没有 Quartz 包装，PyObjC 每次都会警告一句；那是已知且无害的。
    warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)
    return _Binding(
        asset=asset,
        generator=generator,
        time=time_type,
        url=foundation.NSURL,
        bitmap=appkit.NSBitmapImageRep,
    )


#: 加载一次就固定下来：能力探测不是可变状态。
_BINDING: Final = _load()

#: 这台机器能不能取视频画面。与 :data:`gallery.core.media.decode.HEIF_SUPPORT` 同一类能力探测。
VIDEO_SUPPORT: Final = _BINDING is not None


def first_frame(path: Path, *, max_edge: int | None) -> tuple[int, int, bytes]:
    """取 ``path`` 第 0 秒那一帧，返回 ``(宽, 高, RGBA 字节)``。

    系统在解码时就按 ``max_edge`` 缩小（``setMaximumSize``），不会先解出一整幅 4K；
    旋转按容器的方向标记摆正。**首帧可能是黑帧**：那是视频本身的样子，不是解码失败。
    :raises ImageDecodeError: 系统解不开这个文件，或者这台机器没有 AVFoundation。
    """
    path = Path(path)
    binding = _BINDING
    if binding is None:
        raise ImageDecodeError(
            f"这台机器上取不了视频画面：{path.name}。",
            path=path,
        )
    try:
        url = binding.url.fileURLWithPath_(str(path))
        asset = binding.asset.URLAssetWithURL_options_(url, None)
        generator = binding.generator.assetImageGeneratorWithAsset_(asset)
        generator.setAppliesPreferredTrackTransform_(True)
        if max_edge is not None:
            generator.setMaximumSize_((float(max_edge), float(max_edge)))
        image = generator.copyCGImageAtTime_actualTime_error_(
            binding.time(value=0, timescale=600, flags=1, epoch=0), None, None
        )
        bitmap = None if image is None else binding.bitmap.alloc().initWithCGImage_(image)
        data = None if bitmap is None else bitmap.representationUsingType_properties_(_PNG_TYPE, {})
    except Exception as exc:  # noqa: BLE001 - PyObjC 抛什么都只说明这一帧取不到
        raise ImageDecodeError(_unreadable_message(path), path=path, detail=repr(exc)) from exc
    if data is None:
        raise ImageDecodeError(_unreadable_message(path), path=path)
    with Image.open(io.BytesIO(bytes(data))) as frame:
        rgba = frame.convert("RGBA")
        try:
            return rgba.width, rgba.height, rgba.tobytes()
        finally:
            rgba.close()


def _unreadable_message(path: Path) -> str:
    """取不到帧时说的话。**不把「编码不支持」说死**：云盘上没下完也是同一个结果。"""
    return (
        f"无法读取 {path.name} 的第一帧：系统不支持其编码格式，"
        "或文件尚未下载完成。请等待下载完成后再打开。"
    )


__all__ = ["VIDEO_SUPPORT", "first_frame"]
