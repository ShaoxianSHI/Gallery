"""生成「墨水晕染」风格的背景图。

只有一条产出路径：先把配方（``Recipe``）算成一张固定 16:10 的主图，再按需要裁剪。
**配方就是缓存键** —— 同样的配方永远落成同一个文件名，改任何一项（包括调色板里一个
hex）都会换一张。产物只进应用级缓存目录，删掉随时能重新生成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, PngImagePlugin

from gallery.core.paths import app_cache_root

# 算法版本。**改了任何一处「像素是怎么算出来的」就要 +1** —— 它进了配方的哈希，
# 于是旧缓存自动失配，而不是静默地拿旧图冒充新算法。
VERSION = 2

FIELD_WIDTH = 320
FIELD_HEIGHT = 200
MASTER_WIDTH = 1920
MASTER_HEIGHT = 1200

GRAIN_TILE = 256

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


# ---------------------------------------------------------------- 噪声

_LATTICE = 256
_MASK = _LATTICE - 1


def stream(seed: int, name: str) -> int:
    """把「一个种子 + 一个用途名」派生出一个独立种子。

    用哈希而不是 ``seed + 11`` 这类偏移：加一条新流时不会把别的流整体挪位。
    """
    digest = hashlib.sha1(f"gallery-bg/v{VERSION}/{seed}/{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


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


# ---------------------------------------------------------------- 配方


@dataclass(frozen=True)
class Recipe:
    """一次生成所需的全部输入。**它就是缓存键**，也是能重放的最小信息。

    颜色整份写进来而不是只记预设名：否则以后调了一个 hex，旧缓存会静默对不上。
    """

    preset: str
    seed: int
    colors: tuple[tuple[float, str], ...]
    warp: float
    scale: float
    contrast: float
    plume: float
    detail: float
    octaves: int
    grain: float
    field: tuple[int, int] = (FIELD_WIDTH, FIELD_HEIGHT)
    master: tuple[int, int] = (MASTER_WIDTH, MASTER_HEIGHT)
    version: int = VERSION

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "version": self.version,
            "preset": self.preset,
            "seed": self.seed,
            "palette": [[position, color] for position, color in self.colors],
            "shape": {
                "warp": self.warp,
                "scale": self.scale,
                "contrast": self.contrast,
                "plume": self.plume,
                "detail": self.detail,
                "octaves": self.octaves,
            },
            "field": list(self.field),
            "master": list(self.master),
            "grain": self.grain,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Recipe:
        data = json.loads(text)
        shape = data["shape"]
        return cls(
            preset=data["preset"],
            seed=int(data["seed"]),
            colors=tuple((float(position), str(color)) for position, color in data["palette"]),
            warp=float(shape["warp"]),
            scale=float(shape["scale"]),
            contrast=float(shape["contrast"]),
            plume=float(shape["plume"]),
            detail=float(shape["detail"]),
            octaves=int(shape["octaves"]),
            grain=float(data["grain"]),
            field=tuple(data["field"]),
            master=tuple(data["master"]),
            version=int(data["version"]),
        )

    def digest(self) -> str:
        """配方内容的短哈希，用作缓存文件名。"""
        return hashlib.sha1(self.to_json().encode("utf-8")).hexdigest()[:12]


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
    grain: float = 1.6

    def recipe(self, seed: int) -> Recipe:
        return Recipe(
            preset=self.name,
            seed=seed,
            colors=self.colors,
            warp=self.warp,
            scale=self.scale,
            contrast=self.contrast,
            plume=self.plume,
            detail=self.detail,
            octaves=self.octaves,
            grain=self.grain,
        )


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

PRESETS_BY_NAME = {preset.name: preset for preset in INK_PRESETS}


# ---------------------------------------------------------------- 渲染


def render_ink(recipe: Recipe) -> Image.Image:
    """域扭曲的 fBm 当浓度场，再按调色板取色。

    三层位移场（q、r、f）是「墨」与「雾」的分界：位移越大，边界被拉得越细长。
    场总是算在固定的 16:10 分辨率上，于是同一个配方在任何窗口比例下都是同一幅画。
    """
    field_width, field_height = recipe.field
    master_width, master_height = recipe.master
    aspect = field_width / field_height
    scale = recipe.scale
    octaves = recipe.octaves
    seed = recipe.seed
    qx = Fbm(stream(seed, "warp_x"), octaves)
    qy = Fbm(stream(seed, "warp_y"), octaves)
    rx = Fbm(stream(seed, "warp_x2"), octaves)
    ry = Fbm(stream(seed, "warp_y2"), octaves)
    fz = Fbm(stream(seed, "shape"), octaves)
    detail = Fbm(stream(seed, "detail"), 4)
    plume = Fbm(stream(seed, "plume"), 2)
    warp = recipe.warp
    detail_strength = recipe.detail
    plume_strength = recipe.plume

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
    lut = _palette_lut(recipe.colors)
    last = len(lut) - 1
    spread = deviation * recipe.contrast

    pixels = bytearray()
    for row, row_density in zip(values, densities, strict=True):
        for value, density in zip(row, row_density, strict=True):
            t = _clamp01(0.5 + (value - mean) / (2 * spread))
            # 低频「墨团」掩膜：一处浓一处留白，免得整幅平均发力像壁纸贴图。
            density_gain = 1.0 - plume_strength + plume_strength * (0.15 + 1.05 * density)
            t = _clamp01(t**1.15 * density_gain)
            pixels += bytes(lut[min(last, int(t * last))])

    image = Image.frombytes("RGB", (field_width, field_height), bytes(pixels))
    image = image.resize((master_width, master_height), Image.Resampling.BICUBIC)
    image = image.filter(ImageFilter.GaussianBlur(0.5))
    return add_grain(image, recipe.grain, stream(seed, "grain"))


def add_grain(image: Image.Image, sigma: float, seed: int) -> Image.Image:
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


# ---------------------------------------------------------------- 落盘


def cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """居中裁剪到目标比例。**不做非等比拉伸** —— 那会把晕染的圆斑压成椭圆。"""
    scale = max(width / image.width, height / image.height)
    size = (max(width, round(image.width * scale)), max(height, round(image.height * scale)))
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def save_png(image: Image.Image, recipe: Recipe, path: Path) -> None:
    """把配方写进 PNG 的文本块，于是看文件就能重放，不必回头找命令行。"""
    info = PngImagePlugin.PngInfo()
    info.add_text("gallery-recipe", recipe.to_json())
    image.save(path, pnginfo=info)


def read_recipe(path: Path) -> Recipe:
    with Image.open(path) as image:
        text = image.text.get("gallery-recipe")
    if not text:
        raise SystemExit(f"{path} 里没有内嵌生成配方，无法从它重放")
    return Recipe.from_json(text)


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


def title_of(name: str) -> str:
    preset = PRESETS_BY_NAME.get(name)
    return preset.title if preset is not None else name


# ---------------------------------------------------------------- 命令行


def _parse_size(text: str) -> tuple[int, int]:
    width_text, _, height_text = text.partition("x")
    return int(width_text), int(height_text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成墨水晕染风格的背景图")
    parser.add_argument("--out", type=Path, default=None, help="输出目录，默认应用级缓存")
    parser.add_argument("--size", default="", help="裁剪到 WxH，默认输出 16:10 主图")
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--only", default="", help="逗号分隔的预设名前缀")
    parser.add_argument("--recipe", default="", help="配方 JSON，或 @文件名")
    parser.add_argument("--from", dest="source", type=Path, default=None, help="从既有 PNG 重放")
    parser.add_argument("--print-recipe", action="store_true", help="打印完整配方")
    parser.add_argument("--no-sheet", dest="sheet", action="store_false", help="不生成总览图")
    args = parser.parse_args(argv)

    output = args.out or app_cache_root() / "backgrounds"
    output.mkdir(parents=True, exist_ok=True)
    size = _parse_size(args.size) if args.size else None

    if args.recipe or args.source is not None:
        if args.recipe.startswith("@"):
            text = Path(args.recipe[1:]).read_text(encoding="utf-8")
        elif args.recipe:
            text = args.recipe
        else:
            text = read_recipe(args.source).to_json()  # type: ignore[union-attr]
        recipes = [Recipe.from_json(text)]
    else:
        prefixes = [item.strip() for item in args.only.split(",") if item.strip()]
        recipes = [
            preset.recipe(args.seed)
            for preset in INK_PRESETS
            if not prefixes or any(preset.name.startswith(prefix) for prefix in prefixes)
        ]

    rendered: list[tuple[str, Image.Image]] = []
    for recipe in recipes:
        started = time.perf_counter()
        master = render_ink(recipe)
        image = cover(master, *size) if size else master
        suffix = "" if image.size == recipe.master else f"-{image.width}x{image.height}"
        path = output / f"{recipe.preset}-{recipe.digest()}{suffix}.png"
        save_png(image, recipe, path)
        elapsed = time.perf_counter() - started
        print(f"{path.name:28s} {elapsed:5.2f}s  {title_of(recipe.preset)}")
        if args.print_recipe:
            print(recipe.to_json())
        rendered.append((title_of(recipe.preset), image))

    if args.sheet and len(rendered) > 1:
        sheet_path = output / "contact-sheet.png"
        contact_sheet(rendered).save(sheet_path)
        print(f"{sheet_path.name:28s}         {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
