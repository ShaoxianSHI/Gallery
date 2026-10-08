"""缩放与适配的纯计算。

这里刻意不导入 Qt：适配比例、缩放上下限、是不是处于「适配窗口」状态，恰恰是最容易算错也
最值得单测的部分。尺寸一律用 ``(宽, 高)`` 元组表示，不必构造 ``QSize``。
"""

from __future__ import annotations

from typing import Final

MIN_SCALE: Final = 0.02

#: 手动缩放的比例上限（避免点到几百倍变成抽象画）。
MAX_SCALE: Final = 8.0

#: 「适应窗口」自身的上限。它管的不是「用户能放多大」，而是「一张极小的图为了铺满视口
#: 需要放大多少倍」—— 与 :data:`MAX_SCALE` 不能混用，理由见 :func:`fit_scale`。
MAX_FIT_SCALE: Final = 64.0

ZOOM_STEP: Final = 1.25

FIT_MARGIN: Final = 0.98

FIT_TOLERANCE: Final = 0.01


def clamp_scale(
    scale: float,
    *,
    minimum: float = MIN_SCALE,
    maximum: float | None = MAX_SCALE,
) -> float:
    """把缩放比例夹到合法区间。``maximum`` 传 ``None`` 表示不设上限。"""
    if scale != scale or scale <= 0:  # NaN 或非正值都视为非法输入
        return minimum
    value = max(minimum, scale)
    return value if maximum is None else min(maximum, value)


def fit_scale(
    image: tuple[int, int],
    viewport: tuple[int, int],
    *,
    margin: float = FIT_MARGIN,
    allow_upscale: bool = True,
) -> float:
    """计算把整张图放进视口所需的缩放比例。

    这里不能拿 :data:`MAX_SCALE` 当上限：它是给手动放大用的，而「适应窗口」要回答的是
    「这张图铺满视口需要几倍」—— 168 像素的缩略图在 2400 像素宽的窗口里要 12 倍以上。
    卡在 8 倍的话，缓存未命中时先用缩略图顶着显示、全分辨率图到了再「变大一圈」。
    """
    image_width, image_height = image
    viewport_width, viewport_height = viewport
    if image_width <= 0 or image_height <= 0 or viewport_width <= 0 or viewport_height <= 0:
        return 1.0
    scale = min(viewport_width / image_width, viewport_height / image_height) * margin
    if not allow_upscale:
        scale = min(scale, 1.0)
    return clamp_scale(scale, maximum=MAX_FIT_SCALE)


def zoom_in(scale: float, *, step: float = ZOOM_STEP) -> float:
    """放大一档。"""
    return clamp_scale(scale * step)


def zoom_out(scale: float, *, step: float = ZOOM_STEP) -> float:
    """缩小一档。"""
    return clamp_scale(scale / step)


def is_fit(scale: float, fit: float, *, tolerance: float = FIT_TOLERANCE) -> bool:
    """当前比例是否等同于「适配窗口」。

    用相对容差比较：浮点乘除之后不会精确相等，而「用户是否手动缩放过」决定了窗口大小变化
    时要不要重新适配。
    """
    if fit <= 0:
        return False
    return abs(scale - fit) <= fit * tolerance


__all__ = [
    "FIT_MARGIN",
    "FIT_TOLERANCE",
    "MAX_FIT_SCALE",
    "MAX_SCALE",
    "MIN_SCALE",
    "ZOOM_STEP",
    "clamp_scale",
    "fit_scale",
    "is_fit",
    "zoom_in",
    "zoom_out",
]
