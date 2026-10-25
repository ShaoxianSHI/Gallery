"""生成「墨水晕染」与「极光」两种背景的样张，用来比对效果。

两条算法都是纯函数式的：给定尺寸、种子与调色板就地算出像素，不采样任何外部素材。
产物进应用级缓存目录（``<app cache>/backgrounds/``），删掉随时能重新生成。
"""

from __future__ import annotations

import argparse
import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from gallery.core.paths import app_cache_root

# ---------------------------------------------------------------- 色彩

_TO_BYTE = [round(i / 1023 * 255) for i in range(1024)]


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


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
    return tuple(_TO_BYTE[round(_clamp01(_linear_to_srgb(v)) * 1023)] for v in linear)  # type: ignore[return-value]


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


# ---------------------------------------------------------------- 噪声

_LATTICE = 256
_MASK = _LATTICE - 1


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


# ---------------------------------------------------------------- 墨


@dataclass(frozen=True)
class InkPreset:
    name: str
    title: str
    colors: tuple[tuple[float, str], ...]
    warp: float
    scale: float
    contrast: float
    plume: float = 0.5
    detail: float = 0.10
    octaves: int = 5


INK_PRESETS: tuple[InkPreset, ...] = (
    InkPreset(
        "ink-01-mist",
        "雾蓝 · 藕紫",
        (
            (0.00, "#fbfbf9"),
            (0.30, "#f3f4f2"),
            (0.52, "#dde6ec"),
            (0.74, "#c8d5e4"),
            (0.88, "#c3c3db"),
            (1.00, "#d3c7dc"),
        ),
        warp=1.10,
        scale=2.6,
        contrast=4.6,
    ),
    InkPreset(
        "ink-02-peach",
        "奶白 · 蜜桃",
        (
            (0.00, "#fdfaf6"),
            (0.28, "#f8f0e8"),
            (0.52, "#f2dfd2"),
            (0.74, "#e9cbb9"),
            (0.90, "#edc8ae"),
            (1.00, "#f0d6c0"),
        ),
        warp=1.20,
        scale=2.9,
        contrast=4.4,
    ),
    InkPreset(
        "ink-03-mint",
        "薄荷 · 湖水",
        (
            (0.00, "#fbfcfa"),
            (0.30, "#eef4ef"),
            (0.54, "#d6e8e1"),
            (0.76, "#bcdcd8"),
            (0.90, "#aed6d8"),
            (1.00, "#c2e0da"),
        ),
        warp=1.35,
        scale=2.4,
        contrast=4.2,
    ),
    InkPreset(
        "ink-04-many",
        "多色：青 / 藕 / 蜜",
        (
            (0.00, "#fcfaf6"),
            (0.22, "#eef2ee"),
            (0.44, "#d3e2e6"),
            (0.62, "#cdd8e8"),
            (0.76, "#e2d4de"),
            (0.88, "#ecd9c9"),
            (1.00, "#e8dfc6"),
        ),
        warp=1.55,
        scale=3.2,
        contrast=4.9,
    ),
    InkPreset(
        "ink-05-mono",
        "灰阶（对照）",
        (
            (0.00, "#fcfcfc"),
            (0.35, "#efefef"),
            (0.60, "#dcdcdc"),
            (0.80, "#c9c9c9"),
            (1.00, "#bcbcbc"),
        ),
        warp=1.15,
        scale=2.7,
        contrast=4.6,
    ),
    InkPreset(
        "ink-06-deep",
        "深一档（看对比度上限）",
        (
            (0.00, "#faf7f2"),
            (0.28, "#e2e4e2"),
            (0.50, "#b9c9d4"),
            (0.70, "#8ea8bf"),
            (0.86, "#8f8fb4"),
            (1.00, "#a89ac2"),
        ),
        warp=1.40,
        scale=2.5,
        contrast=4.0,
        detail=0.14,
    ),
    InkPreset(
        "ink-07-thread",
        "丝缕（强扭曲 + 高频细节）",
        (
            (0.00, "#fbfaf7"),
            (0.26, "#f4f2ee"),
            (0.48, "#dfe6e6"),
            (0.68, "#c6d4dd"),
            (0.84, "#c4c3cf"),
            (1.00, "#d6cbd4"),
        ),
        warp=2.30,
        scale=3.4,
        contrast=5.0,
        plume=0.35,
        detail=0.30,
    ),
    InkPreset(
        "ink-08-soft",
        "很淡（只留一层雾）",
        (
            (0.00, "#fdfdfb"),
            (0.40, "#f6f7f5"),
            (0.68, "#e6eef0"),
            (0.88, "#dae4ea"),
            (1.00, "#e2dbe6"),
        ),
        warp=1.00,
        scale=2.1,
        contrast=3.4,
        plume=0.6,
        detail=0.06,
    ),
)


def render_ink(preset: InkPreset, width: int, height: int, seed: int) -> Image.Image:
    """域扭曲的 fBm 当成浓度场，再按调色板取色。

    三层位移场（q、r、f）是「墨」与「雾」的分界：位移越大，边界被拉得越细长。
    """
    field_width = 320
    field_height = max(1, round(field_width * height / width))
    aspect = field_width / field_height
    scale = preset.scale
    octaves = preset.octaves
    qx = Fbm(seed + 11, octaves)
    qy = Fbm(seed + 23, octaves)
    rx = Fbm(seed + 41, octaves)
    ry = Fbm(seed + 53, octaves)
    fz = Fbm(seed + 71, octaves)
    detail = Fbm(seed + 97, 4)
    plume = Fbm(seed + 131, 2)
    warp = preset.warp
    detail_strength = preset.detail
    plume_strength = preset.plume

    values: list[list[float]] = []
    densities: list[list[float]] = []
    total = 0.0
    total_sq = 0.0
    for row_index in range(field_height):
        y = (row_index / field_height) * scale
        row: list[float] = []
        row_density: list[float] = []
        for column in range(field_width):
            x = (column / field_width) * scale * aspect
            ax = (qx(x, y) - 0.5) * warp
            ay = (qy(x, y) - 0.5) * warp
            bx = (rx(x + ax, y + ay) - 0.5) * warp
            by = (ry(x + ax, y + ay) - 0.5) * warp
            value = fz(x + bx * 1.6, y + by * 1.6)
            if detail_strength:
                value += detail_strength * (detail(x * 2.9 + bx, y * 2.9 + by) - 0.5)
            row.append(value)
            row_density.append(_clamp01((plume(x * 0.30, y * 0.30) - 0.26) / 0.46))
            total += value
            total_sq += value * value
        values.append(row)
        densities.append(row_density)

    count = field_width * field_height
    mean = total / count
    deviation = math.sqrt(max(0.0, total_sq / count - mean * mean)) or 1e-6
    lut = _palette_lut(preset.colors)
    last = len(lut) - 1
    spread = deviation * preset.contrast

    pixels = bytearray()
    for row, row_density in zip(values, densities, strict=True):
        for value, density in zip(row, row_density, strict=True):
            t = _clamp01(0.5 + (value - mean) / (2 * spread))
            # 低频「墨团」掩膜：一处浓一处留白，免得整幅平均发力像壁纸贴图。
            density_gain = 1.0 - plume_strength + plume_strength * (0.15 + 1.05 * density)
            t = _clamp01(t**1.15 * density_gain)
            pixels += bytes(lut[min(last, int(t * last))])

    image = Image.frombytes("RGB", (field_width, field_height), bytes(pixels))
    image = image.resize((width, height), Image.Resampling.BICUBIC)
    return image.filter(ImageFilter.GaussianBlur(0.5))


# ---------------------------------------------------------------- 极光


@dataclass(frozen=True)
class AuroraPreset:
    name: str
    title: str
    base_top: str
    base_bottom: str
    #: 竖直方向的颜色渐变，**从画面下沿往上是正的 t**。
    colors: tuple[tuple[float, str], ...]
    strength: float
    threshold: float
    stretch: float
    dark: bool = False


AURORA_PRESETS: tuple[AuroraPreset, ...] = (
    AuroraPreset(
        "aurora-01-pastel",
        "淡彩 · 绿 / 青 / 紫",
        "#fbfcfe",
        "#f3f0f8",
        (
            (0.00, "#8fdcae"),
            (0.35, "#86cfd6"),
            (0.65, "#9bb8e4"),
            (1.00, "#bfa8e2"),
        ),
        strength=0.85,
        threshold=0.46,
        stretch=0.55,
    ),
    AuroraPreset(
        "aurora-02-rose",
        "淡彩 · 金 / 粉 / 紫",
        "#fdfbf9",
        "#f6eff2",
        (
            (0.00, "#eec39b"),
            (0.35, "#eba9b4"),
            (0.65, "#d5a3d1"),
            (1.00, "#ae9fd8"),
        ),
        strength=0.85,
        threshold=0.48,
        stretch=0.75,
    ),
    AuroraPreset(
        "aurora-03-dawn",
        "淡彩 · 金 / 珊瑚 / 青",
        "#fffdfa",
        "#f5f2ee",
        (
            (0.00, "#f5cf90"),
            (0.35, "#f0ac96"),
            (0.65, "#cfa5cc"),
            (1.00, "#9ab4dc"),
        ),
        strength=0.85,
        threshold=0.47,
        stretch=0.95,
    ),
    AuroraPreset(
        "aurora-04-night",
        "夜空（深色对照）",
        "#070a12",
        "#141c2c",
        (
            (0.00, "#38e08c"),
            (0.35, "#2fc8d8"),
            (0.65, "#3f8fe0"),
            (1.00, "#8c6cf0"),
        ),
        strength=1.00,
        threshold=0.44,
        stretch=0.55,
        dark=True,
    ),
)


def _smoothstep(edge_low: float, edge_high: float, x: float) -> float:
    t = _clamp01((x - edge_low) / (edge_high - edge_low))
    return t * t * (3.0 - 2.0 * t)


def render_aurora(preset: AuroraPreset, width: int, height: int, seed: int) -> Image.Image:
    """把域扭曲的 fBm 在竖直方向拉长，取它的等值线当帘幕。

    **拉伸比就是「极光」与「雾」的分界**：x 走得快、y 走得慢，等值线才连成竖条。
    """
    field_width = 320
    field_height = max(1, round(field_width * height / width))
    shape = Fbm(seed, 5)
    warp_x = Fbm(seed + 11, 3)
    warp_y = Fbm(seed + 23, 3)
    fine = Fbm(seed + 37, 3)
    ramp = _palette_lut(preset.colors)
    last = len(ramp) - 1
    top = _hex_to_rgb01(preset.base_top)
    bottom = _hex_to_rgb01(preset.base_bottom)
    strength = preset.strength
    threshold = preset.threshold
    stretch = preset.stretch

    canvas: list[list[tuple[float, float, float]]] = []
    for row_index in range(field_height):
        y = row_index / max(1, field_height - 1)
        base = (
            top[0] + (bottom[0] - top[0]) * y,
            top[1] + (bottom[1] - top[1]) * y,
            top[2] + (bottom[2] - top[2]) * y,
        )
        envelope = _smoothstep(0.04, 0.70, y) * (1.0 - 0.72 * _smoothstep(0.90, 1.0, y))
        colour = ramp[min(last, int((1.0 - y) * last))]
        tint = (colour[0] / 255, colour[1] / 255, colour[2] / 255)
        row: list[tuple[float, float, float]] = []
        for column in range(field_width):
            x = column / field_width
            dx = (warp_x(x * 4.0, y * 1.2) - 0.5) * 0.85
            dy = (warp_y(x * 4.0, y * 1.2) - 0.5) * 0.30
            value = shape(x * 6.0 + dx, y * stretch + dy)
            value += 0.10 * (fine(x * 9.0 + dx, y * stretch * 2.6) - 0.5)
            mask = _smoothstep(threshold, threshold + 0.13, value)
            if mask <= 0.001:
                row.append(base)
                continue
            alpha = min(1.0, mask * envelope * strength)
            if preset.dark:
                row.append(
                    (
                        min(1.0, base[0] + tint[0] * alpha),
                        min(1.0, base[1] + tint[1] * alpha),
                        min(1.0, base[2] + tint[2] * alpha),
                    )
                )
            else:
                row.append(
                    (
                        base[0] + (tint[0] - base[0]) * alpha,
                        base[1] + (tint[1] - base[1]) * alpha,
                        base[2] + (tint[2] - base[2]) * alpha,
                    )
                )
        canvas.append(row)

    pixels = bytearray()
    for row in canvas:
        for r, g, b in row:
            pixels += bytes(
                (
                    _TO_BYTE[round(r * 1023)],
                    _TO_BYTE[round(g * 1023)],
                    _TO_BYTE[round(b * 1023)],
                )
            )

    image = Image.frombytes("RGB", (field_width, field_height), bytes(pixels))
    image = image.resize((width, height), Image.Resampling.BICUBIC)
    return image.filter(ImageFilter.GaussianBlur(1.0 if preset.dark else 1.6))


# ---------------------------------------------------------------- 收尾


def add_grain(image: Image.Image, sigma: float) -> Image.Image:
    """加一层零均值细噪声。低对比渐变在 8 bit 下必然出现色带，这层专门盖它。"""
    noise = Image.effect_noise(image.size, sigma)
    return ImageChops.add(image, Image.merge("RGB", (noise, noise, noise)), 1.0, -128)


def contact_sheet(images: Sequence[tuple[str, Image.Image]], columns: int = 4) -> Image.Image:
    thumb_width = 440
    gap = 14
    label_height = 26
    rows = math.ceil(len(images) / columns)
    thumbs = [
        image.resize(
            (thumb_width, round(thumb_width * image.height / image.width)),
            Image.Resampling.LANCZOS,
        )
        for _, image in images
    ]
    thumb_height = max(thumb.height for thumb in thumbs)
    sheet_width = columns * thumb_width + (columns + 1) * gap
    sheet_height = rows * (thumb_height + label_height) + (rows + 1) * gap
    sheet = Image.new("RGB", (sheet_width, sheet_height), (246, 246, 246))
    draw = ImageDraw.Draw(sheet)
    # PingFang 是 .ttc 集合，Pillow 打不开它（退到默认字体就是一排豆腐块），换冬青黑。
    font = None
    for candidate in (
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 14, index=0)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    for index, (title, _) in enumerate(images):
        row, column = divmod(index, columns)
        left = gap + column * (thumb_width + gap)
        top = gap + row * (thumb_height + label_height + gap)
        sheet.paste(thumbs[index], (left, top))
        draw.text((left + 2, top + thumb_height + 5), title, fill=(60, 60, 60), font=font)
    return sheet


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成背景样张")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--size", default="1536x1024")
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--only", default="", help="逗号分隔的名字前缀")
    args = parser.parse_args(argv)

    width_text, _, height_text = args.size.partition("x")
    width, height = int(width_text), int(height_text)
    output = args.out or app_cache_root() / "backgrounds"
    output.mkdir(parents=True, exist_ok=True)

    prefixes = [item.strip() for item in args.only.split(",") if item.strip()]

    def wanted(name: str) -> bool:
        return not prefixes or any(name.startswith(prefix) for prefix in prefixes)

    rendered: list[tuple[str, Image.Image]] = []
    for preset in INK_PRESETS:
        if not wanted(preset.name):
            continue
        started = time.perf_counter()
        image = add_grain(render_ink(preset, width, height, args.seed), 1.6)
        path = output / f"{preset.name}.png"
        image.save(path)
        elapsed = time.perf_counter() - started
        print(f"{path.name:24s} {elapsed:5.2f}s  {preset.title}")
        rendered.append((preset.title, image))

    for preset in AURORA_PRESETS:
        if not wanted(preset.name):
            continue
        started = time.perf_counter()
        image = add_grain(render_aurora(preset, width, height, args.seed), 1.4)
        path = output / f"{preset.name}.png"
        image.save(path)
        elapsed = time.perf_counter() - started
        print(f"{path.name:24s} {elapsed:5.2f}s  {preset.title}")
        rendered.append((preset.title, image))

    if len(rendered) > 1:
        sheet_path = output / "contact-sheet.png"
        contact_sheet(rendered).save(sheet_path)
        print(f"{sheet_path.name:24s}         {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
