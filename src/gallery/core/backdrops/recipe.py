"""背景图的配方：颜色 + 形状 + 种子，写成一段可存、可重放的 JSON。

**配方就是缓存键**，也是「缓存里的图被删了还能长回来」的唯一依据。所以它必须自足 ——
调色板整份写进来，而不是只记一个预设名。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

#: 算法版本。改了「像素是怎么算出来的」就要 +1 —— 它进了配方的哈希，于是旧缓存自动失配，
#: 而不是静默地拿旧图冒充新算法。
VERSION: Final = 2

#: 浓度场的分辨率。**它决定生成耗时**（0.5 秒里九成在这里），与输出尺寸无关。
FIELD: Final = (320, 200)

#: 主图尺寸，固定 16:10。窗口比例只决定怎么裁，于是同一个配方在任何尺寸下都是同一幅画。
MASTER: Final = (1920, 1200)


@dataclass(frozen=True, slots=True)
class Recipe:
    """一次生成所需的全部输入。"""

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
    field: tuple[int, int] = FIELD
    master: tuple[int, int] = MASTER
    version: int = VERSION

    def to_mapping(self) -> dict[str, Any]:
        return {
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

    def to_json(self) -> str:
        return json.dumps(
            self.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @classmethod
    def from_mapping(cls, data: Any) -> Recipe:
        """读回一份配方。字段缺失或类型不对就抛 ``KeyError`` / ``TypeError``。

        **校验留给调用方**（``store`` 读清单时逐条兜）：一份坏配方只该让那一条背景消失，
        不该让整个背景库读不出来。
        """
        shape = data["shape"]
        return cls(
            preset=str(data["preset"]),
            seed=int(data["seed"]),
            colors=tuple((float(position), str(color)) for position, color in data["palette"]),
            warp=float(shape["warp"]),
            scale=float(shape["scale"]),
            contrast=float(shape["contrast"]),
            plume=float(shape["plume"]),
            detail=float(shape["detail"]),
            octaves=int(shape["octaves"]),
            grain=float(data["grain"]),
            field=_size(data["field"]),
            master=_size(data["master"]),
            version=int(data["version"]),
        )

    @classmethod
    def from_json(cls, text: str) -> Recipe:
        return cls.from_mapping(json.loads(text))

    def digest(self) -> str:
        """配方内容的短哈希，用作缓存文件名。"""
        return hashlib.sha1(self.to_json().encode("utf-8")).hexdigest()[:12]


def _size(value: Any) -> tuple[int, int]:
    width, height = value
    return (int(width), int(height))


@dataclass(frozen=True, slots=True)
class Palette:
    """一套配色与它配套的形状参数。用户在对话框里挑的就是这个。"""

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


PALETTES: Final[tuple[Palette, ...]] = (
    Palette(
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
    Palette(
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
    Palette(
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
    Palette(
        "ink-04-many",
        "多色 · 青 / 藕 / 蜜",
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
    Palette(
        "ink-05-mono",
        "灰阶",
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
    Palette(
        "ink-06-deep",
        "深蓝 · 灰紫",
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
    Palette(
        "ink-07-thread",
        "丝缕 · 青灰",
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
    Palette(
        "ink-08-soft",
        "雾 · 极淡",
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

PALETTES_BY_NAME: Final[dict[str, Palette]] = {palette.name: palette for palette in PALETTES}

#: 用户自己挑颜色那一套的预设名。**它不在 ``PALETTES`` 里**：颜色整份写进了配方，
#: 读回时不需要、也不该去预设表里找它。
CUSTOM_PRESET: Final = "ink-custom"

#: 从零开始那套配色的起点：一个颜色兑成由浅到深的六档（ADR 0103）。
#: 最深一档也只到 0.60 —— 再浓正文就压不住了（ADR 0098 的遮罩管亮度，不管「整张一个色」）。
_STARTER_STOPS: Final = (
    (0.00, 0.06),
    (0.28, 0.14),
    (0.50, 0.26),
    (0.72, 0.40),
    (0.88, 0.52),
    (1.00, 0.60),
)

_HEX_COLOR: Final = re.compile(r"^#[0-9a-fA-F]{6}$")


def palette_from_colors(colors: Sequence[str], *, title: str = "自定义") -> Palette:
    """一串颜色 → 一套配色：**颜色按次序均分整条浓度轴**，原样使用，不再兑白。

    用户是在编辑对话框里对着这几个色块改的，所以「看到什么就是什么」：增删之后停点重新均分，
    每个颜色各占一段（ADR 0103）。
    """
    if len(colors) < 2:
        raise ValueError("一套配色至少要两个颜色")
    for color in colors:
        _hex_channels(color)
    last = len(colors) - 1
    stops = tuple((index / last, color.lower()) for index, color in enumerate(colors))
    return Palette(
        name=CUSTOM_PRESET,
        title=title,
        colors=stops,
        warp=1.20,
        scale=2.8,
        contrast=4.4,
    )


def palette_from_color(color: str) -> Palette:
    """一个颜色 → 一套同色系、由浅到深的晕染配色。**新建配色时的起点**。"""
    red, green, blue = _hex_channels(color)
    stops = tuple(
        (position, _tint((red, green, blue), weight)) for position, weight in _STARTER_STOPS
    )
    return Palette(
        name=CUSTOM_PRESET,
        title="自定义 · " + color.lower(),
        colors=stops,
        warp=1.20,
        scale=2.8,
        contrast=4.4,
    )


def _hex_channels(color: str) -> tuple[int, int, int]:
    if not _HEX_COLOR.match(color):
        raise ValueError(f"颜色要写成 #rrggbb，收到的是 {color!r}")
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _tint(rgb: tuple[int, int, int], weight: float) -> str:
    """把颜色按 ``weight`` 兑进白色；停点越靠后越浓。"""
    channels = (round(255 - (255 - value) * weight) for value in rgb)
    return "#" + "".join(f"{value:02x}" for value in channels)


def palette_of(recipe: Recipe) -> Palette | None:
    """这条配方出自哪一套配色；认不出来（自定义、老版本、手改过）就是 ``None``。"""
    return PALETTES_BY_NAME.get(recipe.preset)


def palette_id() -> str:
    """「+」新增的那一套的 id。**不能由颜色算** —— 改名、改色之后还得是同一套（ADR 0105）。"""
    return "palette-" + uuid.uuid4().hex[:12]


__all__ = [
    "CUSTOM_PRESET",
    "FIELD",
    "MASTER",
    "PALETTES",
    "PALETTES_BY_NAME",
    "VERSION",
    "Palette",
    "Recipe",
    "palette_from_color",
    "palette_from_colors",
    "palette_id",
    "palette_of",
]
