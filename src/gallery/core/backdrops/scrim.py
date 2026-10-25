"""背景图的遮罩：按平均亮度算一层与那一档同色的半透明底。

**只换底不换字**（ADR 0097）：正文色取的是系统那一套，所以合成之后的亮度必须落回这一档
该有的位置 —— 否则浅字压在一张深色照片上，界面不会有任何地方报错。
"""

from __future__ import annotations

from typing import Final

from PIL import Image

from gallery.core.media.decode import RasterImage

#: 浅色那一档合成后的目标亮度下限、深色那一档的上限。系统那一档的底色约在 0.95 / 0.1。
LIGHT_TARGET: Final = 0.86
DARK_TARGET: Final = 0.42

#: 遮罩最多压到多厚。再厚就只剩一个色块了，不如直接拒绝这张图。
MAX_ALPHA: Final = 0.88

#: 求平均亮度时先缩到这么大 —— 2.3M 像素逐个过 Python 循环要一秒，1440 个不够准但够用。
_SAMPLE: Final = (48, 32)


def mean_lightness(raster: RasterImage) -> float:
    """整幅图的平均亮度（0–1，按 sRGB 值加权）。"""
    image = Image.frombytes("RGBA", raster.size, raster.rgba).convert("RGB")
    data = image.resize(_SAMPLE, Image.Resampling.BILINEAR).tobytes()
    total = 0.0
    for index in range(0, len(data), 3):
        total += 0.2126 * data[index] + 0.7152 * data[index + 1] + 0.0722 * data[index + 2]
    return total / (len(data) / 3) / 255


def scrim_alpha(mean: float, background_lightness: float, *, dark: bool) -> float:
    """要把这张图压回这一档，得多厚的一层。

    解的是 ``mean * (1 - a) + background * a = target``。图本来就在正确的一侧时返回 0 ——
    生成出来的那几张很淡的图因此不会被压，等于没加。
    """
    target = DARK_TARGET if dark else LIGHT_TARGET
    if dark:
        if mean <= target:
            return 0.0
        span = mean - background_lightness
        if span <= 0.02:
            return MAX_ALPHA
        return min(MAX_ALPHA, max(0.0, (mean - target) / span))
    if mean >= target:
        return 0.0
    span = background_lightness - mean
    if span <= 0.02:
        return MAX_ALPHA
    return min(MAX_ALPHA, max(0.0, (target - mean) / span))


__all__ = ["DARK_TARGET", "LIGHT_TARGET", "MAX_ALPHA", "mean_lightness", "scrim_alpha"]
