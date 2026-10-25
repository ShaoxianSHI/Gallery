"""把一份配方算成像素：域扭曲的 fBm 当浓度场，再按调色板取色。

三层位移场（q、r、f）是「墨」与「雾」的分界：位移越大，边界被拉得越细长。场总在固定的
16:10 分辨率上算，主图再由它放大 —— 于是同一个配方在任何窗口比例下都是同一幅画。

**这里不碰任何文件**：输入是 :class:`Recipe`，输出是 :class:`RasterImage`。
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from PIL import Image, ImageChops, ImageFilter

from gallery.core.backdrops.recipe import VERSION, Recipe
from gallery.core.media.decode import RasterImage

_LATTICE: Final = 256
_MASK: Final = _LATTICE - 1

#: 颗粒的平铺尺寸。**256 见方重复一次**在 ±1.6 的幅度上肉眼看不出来，而它让颗粒与输出尺寸无关。
GRAIN_TILE: Final = 256

#: 色彩换算用的 1024 档查表：三个通道各一次乘除换成查表。
_TO_BYTE: Final = [round(i / 1023 * 255) for i in range(1024)]


@dataclass(frozen=True, slots=True)
class Field:
    """一张算好的浓度场。**与配色无关**，所以换颜色不必重算它。"""

    width: int
    height: int
    values: tuple[float, ...]
    densities: tuple[float, ...]
    mean: float
    deviation: float


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def stream(seed: int, name: str) -> int:
    """把「一个种子 + 一个用途名」派生出一个独立种子。

    用哈希而不是 ``seed + 11`` 这类偏移：加一条新流时不会把别的流整体挪位。
    """
    digest = hashlib.sha1(f"gallery-bg/v{VERSION}/{seed}/{name}".encode())
    return int.from_bytes(digest.digest()[:8], "big")


class ValueNoise:
    """一张 256×256 的随机灰度格，按格点坐标双线性取值，边界处回绕。"""

    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self.grid = [[rng.random() for _ in range(_LATTICE)] for _ in range(_LATTICE)]

    def at(self, x: float, y: float) -> float:
        xi = math.floor(x)
        yi = math.floor(y)
        fx = x - xi
        fy = y - yi
        u = fx * fx * (3.0 - 2.0 * fx)
        v = fy * fy * (3.0 - 2.0 * fy)
        x0 = xi & _MASK
        y0 = yi & _MASK
        x1 = (xi + 1) & _MASK
        y1 = (yi + 1) & _MASK
        top_row = self.grid[y0]
        bottom_row = self.grid[y1]
        top = top_row[x0] + (top_row[x1] - top_row[x0]) * u
        bottom = bottom_row[x0] + (bottom_row[x1] - bottom_row[x0]) * u
        return top + (bottom - top) * v


class Fbm:
    """分形噪声：每层各自旋转一点，绕开值噪声的轴向纹路。输出大致落在 [0, 1]。"""

    def __init__(self, seed: int, octaves: int = 5, base_freq: float = 1.0) -> None:
        self._noise = ValueNoise(seed)
        rng = random.Random(seed ^ 0x5EEDBEEF)
        self._layers: list[tuple[float, float, float, float, float]] = []
        amplitude = 1.0
        frequency = base_freq
        for _ in range(octaves):
            angle = rng.uniform(-math.pi, math.pi)
            self._layers.append(
                (
                    amplitude,
                    math.cos(angle) * frequency,
                    math.sin(angle) * frequency,
                    rng.uniform(0.0, _LATTICE),
                    rng.uniform(0.0, _LATTICE),
                )
            )
            amplitude *= 0.5
            frequency *= 2.0
        self._norm = sum(layer[0] for layer in self._layers)

    def __call__(self, x: float, y: float) -> float:
        noise_at = self._noise.at
        total = 0.0
        for amplitude, cos, sin, offset_x, offset_y in self._layers:
            total += amplitude * noise_at(
                cos * x - sin * y + offset_x, sin * x + cos * y + offset_y
            )
        return total / self._norm


@lru_cache(maxsize=8)
def _field_of(
    seed: int,
    field_width: int,
    field_height: int,
    scale: float,
    warp: float,
    octaves: int,
    detail_strength: float,
    plume_strength: float,
) -> Field:
    """算一张浓度场。**缓存按参数**：换个颜色不必重算，来回切换是瞬时的。"""
    aspect = field_width / field_height
    qx = Fbm(stream(seed, "warp_x"), octaves)
    qy = Fbm(stream(seed, "warp_y"), octaves)
    rx = Fbm(stream(seed, "warp_x2"), octaves)
    ry = Fbm(stream(seed, "warp_y2"), octaves)
    fz = Fbm(stream(seed, "shape"), octaves)
    detail = Fbm(stream(seed, "detail"), 4)
    plume = Fbm(stream(seed, "plume"), 2)

    values: list[float] = []
    densities: list[float] = []
    total = 0.0
    total_sq = 0.0
    for row_index in range(field_height):
        y = (row_index / field_height) * scale
        for column in range(field_width):
            x = (column / field_width) * scale * aspect
            ax = (qx(x, y) - 0.5) * warp
            ay = (qy(x, y) - 0.5) * warp
            bx = (rx(x + ax, y + ay) - 0.5) * warp
            by = (ry(x + ax, y + ay) - 0.5) * warp
            value = fz(x + bx * 1.6, y + by * 1.6)
            if detail_strength:
                value += detail_strength * (detail(x * 2.9 + bx, y * 2.9 + by) - 0.5)
            values.append(value)
            densities.append(_clamp01((plume(x * 0.30, y * 0.30) - 0.26) / 0.46))
            total += value
            total_sq += value * value

    count = field_width * field_height
    mean = total / count
    deviation = math.sqrt(max(0.0, total_sq / count - mean * mean)) or 1e-6
    return Field(
        width=field_width,
        height=field_height,
        values=tuple(values),
        densities=tuple(densities),
        mean=mean,
        deviation=deviation,
    )


def render_field(recipe: Recipe) -> Field:
    """这条配方的浓度场（有缓存，换颜色不重算）。"""
    return _field_of(
        recipe.seed,
        recipe.field[0],
        recipe.field[1],
        recipe.scale,
        recipe.warp,
        recipe.octaves,
        recipe.detail,
        recipe.plume,
    )


def colorize(recipe: Recipe, field: Field) -> RasterImage:
    """浓度场 → 主图。配色、放大、颗粒都在这一步。"""
    lut = _palette_lut(recipe.colors)
    last = len(lut) - 1
    spread = field.deviation * recipe.contrast
    plume_strength = recipe.plume

    pixels = bytearray()
    for value, density in zip(field.values, field.densities, strict=True):
        t = _clamp01(0.5 + (value - field.mean) / (2 * spread))
        # 低频「墨团」掩膜：一处浓一处留白，免得整幅平均发力像壁纸贴图。
        density_gain = 1.0 - plume_strength + plume_strength * (0.15 + 1.05 * density)
        t = _clamp01(t**1.15 * density_gain)
        pixels += bytes(lut[min(last, int(t * last))])

    image = Image.frombytes("RGB", (field.width, field.height), bytes(pixels))
    image = image.resize(recipe.master, Image.Resampling.BICUBIC)
    image = image.filter(ImageFilter.GaussianBlur(0.5))
    image = _add_grain(image, recipe.grain, stream(recipe.seed, "grain"))
    return RasterImage.from_pil(image, has_alpha=False)


def render(recipe: Recipe) -> RasterImage:
    """配方 → 主图。"""
    return colorize(recipe, render_field(recipe))


def _add_grain(image: Image.Image, sigma: float, seed: int) -> Image.Image:
    """加一层零均值细噪声。低对比渐变在 8 bit 下必然出现色带，这层专门盖它。

    **不借助 Pillow 的 ``effect_noise``**：它用的是进程内不受控的 C 随机数，同一套参数
    第二次调用结果就变了，重放会失败。这里用一块自己播种的小噪声平铺。
    """
    rng = random.Random(seed)
    tile = bytes(
        128 + max(-127, min(127, round(rng.gauss(0.0, sigma)))) for _ in range(GRAIN_TILE**2)
    )
    patch = Image.frombytes("L", (GRAIN_TILE, GRAIN_TILE), tile)
    canvas = Image.new("L", image.size)
    for y in range(0, image.height, GRAIN_TILE):
        for x in range(0, image.width, GRAIN_TILE):
            canvas.paste(patch, (x, y))
    return ImageChops.add(image, Image.merge("RGB", (canvas, canvas, canvas)), 1.0, -128)


# ---------------------------------------------------------------- 色彩


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value: float) -> float:
    return value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def _hex_to_rgb01(text: str) -> tuple[float, float, float]:
    digits = text.lstrip("#")
    return (
        int(digits[0:2], 16) / 255,
        int(digits[2:4], 16) / 255,
        int(digits[4:6], 16) / 255,
    )


def _hex_to_oklab(text: str) -> tuple[float, float, float]:
    """sRGB 十六进制 → OKLab。**插值一律在这个空间里做**：RGB 里两种淡色插中间会发灰。"""
    r, g, b = (_srgb_to_linear(v) for v in _hex_to_rgb01(text))
    light = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    medium = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    short = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * light + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * light - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * light + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _oklab_to_byte(lab: tuple[float, float, float]) -> tuple[int, int, int]:
    light, a, b = lab
    l_ = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )
    return tuple(  # type: ignore[return-value]
        _TO_BYTE[round(_clamp01(_linear_to_srgb(v)) * 1023)] for v in linear
    )


def _palette_lut(stops: Sequence[tuple[float, str]], size: int = 512) -> list[tuple[int, int, int]]:
    points = sorted((position, _hex_to_oklab(color)) for position, color in stops)
    lut: list[tuple[int, int, int]] = []
    segment = 0
    for index in range(size):
        t = index / (size - 1)
        while segment < len(points) - 2 and t > points[segment + 1][0]:
            segment += 1
        left, lab_left = points[segment]
        right, lab_right = points[segment + 1]
        f = 0.0 if right <= left else _clamp01((t - left) / (right - left))
        lut.append(
            _oklab_to_byte(
                (
                    lab_left[0] + (lab_right[0] - lab_left[0]) * f,
                    lab_left[1] + (lab_right[1] - lab_left[1]) * f,
                    lab_left[2] + (lab_right[2] - lab_left[2]) * f,
                )
            )
        )
    return lut


__all__ = ["GRAIN_TILE", "Field", "colorize", "render", "render_field", "stream"]
