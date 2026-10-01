"""缩放与适配的纯计算。

这里刻意**不导入 Qt**：适配比例、缩放上下限、是不是处于「适配窗口」状态，
恰恰是最容易算错、也最值得单测的部分。把它们抽出来，就能在没有屏幕、没有事件循环的
环境里用普通 pytest 覆盖。

尺寸一律用 ``(宽, 高)`` 元组表示，避免为了传参去构造 ``QSize``。
"""

from __future__ import annotations

from typing import Final

#: 缩放比例下限。再小就只剩马赛克了。
MIN_SCALE: Final = 0.02

#: 缩放比例上限。放大到 8 倍用于看清细节足够了。
MAX_SCALE: Final = 8.0

#: 每次「放大/缩小」的倍率。
ZOOM_STEP: Final = 1.25

#: 适配窗口时留出的边距比例，避免图片紧贴窗口边缘。
FIT_MARGIN: Final = 0.98

#: 判断「当前是否等于适配比例」的相对容差。
FIT_TOLERANCE: Final = 0.01


def clamp_scale(
    scale: float,
    *,
    minimum: float = MIN_SCALE,
    maximum: float = MAX_SCALE,
) -> float:
    """把缩放比例夹到合法区间。"""
    if scale != scale or scale <= 0:  # NaN 或非正值都视为非法输入
        return minimum
    return max(minimum, min(maximum, scale))


def fit_scale(
    image: tuple[int, int],
    viewport: tuple[int, int],
    *,
    margin: float = FIT_MARGIN,
    allow_upscale: bool = True,
) -> float:
    """计算把整张图放进视口所需的缩放比例。

    :param allow_upscale: 是否允许小图被放大到填满视口。默认允许 —— 看一张 200px 的
        缩略图时用户期待它铺满窗口，而不是缩在角落。
    """
    image_width, image_height = image
    viewport_width, viewport_height = viewport
    if image_width <= 0 or image_height <= 0 or viewport_width <= 0 or viewport_height <= 0:
        return 1.0
    scale = min(viewport_width / image_width, viewport_height / image_height) * margin
    if not allow_upscale:
        scale = min(scale, 1.0)
    return clamp_scale(scale)


def zoom_in(scale: float, *, step: float = ZOOM_STEP) -> float:
    """放大一档。"""
    return clamp_scale(scale * step)


def zoom_out(scale: float, *, step: float = ZOOM_STEP) -> float:
    """缩小一档。"""
    return clamp_scale(scale / step)


def is_fit(scale: float, fit: float, *, tolerance: float = FIT_TOLERANCE) -> bool:
    """当前比例是否等同于「适配窗口」。

    用相对容差比较：浮点乘除之后不会精确相等，而「用户是否手动缩放过」决定了
    窗口大小变化时要不要重新适配。
    """
    if fit <= 0:
        return False
    return abs(scale - fit) <= fit * tolerance


__all__ = [
    "FIT_MARGIN",
    "FIT_TOLERANCE",
    "MAX_SCALE",
    "MIN_SCALE",
    "ZOOM_STEP",
    "clamp_scale",
    "fit_scale",
    "is_fit",
    "zoom_in",
    "zoom_out",
]
