"""把每一套配色各生成一张样张，摆成一张总览图，用来比效果。

真正的算法在 ``gallery.core.backdrops`` 里 —— 这个脚本只是它的一个批量入口，
不另存一份实现（否则改了配色或噪声，脚本与界面会悄悄分叉）。
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gallery.core.backdrops import PALETTES, Recipe
from gallery.core.backdrops.generate import render
from gallery.core.paths import app_cache_root

THUMB_WIDTH = 440
GAP = 14
LABEL_HEIGHT = 26

#: Pillow 打不开 PingFang（它是 .ttc 集合，退到默认字体就是一排豆腐块），换冬青黑。
FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)


def _to_image(recipe: Recipe) -> Image.Image:
    raster = render(recipe)
    return Image.frombytes("RGBA", raster.size, raster.rgba).convert("RGB")


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """居中裁到目标比例。**不做非等比拉伸** —— 那会把晕染的圆斑压成椭圆。"""
    scale = max(width / image.width, height / image.height)
    size = (max(width, round(image.width * scale)), max(height, round(image.height * scale)))
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def contact_sheet(items: Sequence[tuple[str, Image.Image]], columns: int = 4) -> Image.Image:
    rows = math.ceil(len(items) / columns)
    thumbs = [
        image.resize(
            (THUMB_WIDTH, round(THUMB_WIDTH * image.height / image.width)),
            Image.Resampling.LANCZOS,
        )
        for _, image in items
    ]
    thumb_height = max(thumb.height for thumb in thumbs)
    sheet = Image.new(
        "RGB",
        (
            columns * THUMB_WIDTH + (columns + 1) * GAP,
            rows * (thumb_height + LABEL_HEIGHT) + (rows + 1) * GAP,
        ),
        (246, 246, 246),
    )
    draw = ImageDraw.Draw(sheet)
    font = None
    for candidate in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, 14, index=0)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    for index, (title, _) in enumerate(items):
        row, column = divmod(index, columns)
        left = GAP + column * (THUMB_WIDTH + GAP)
        top = GAP + row * (thumb_height + LABEL_HEIGHT + GAP)
        sheet.paste(thumbs[index], (left, top))
        draw.text((left + 2, top + thumb_height + 5), title, fill=(60, 60, 60), font=font)
    return sheet


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成每一套配色的样张")
    parser.add_argument("--out", type=Path, default=None, help="输出目录，默认应用级缓存")
    parser.add_argument("--size", default="", help="裁剪到 WxH，默认 16:10 主图")
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--only", default="", help="逗号分隔的配色名前缀")
    args = parser.parse_args(argv)

    output = args.out or app_cache_root() / "backgrounds"
    output.mkdir(parents=True, exist_ok=True)
    prefixes = [item.strip() for item in args.only.split(",") if item.strip()]

    rendered: list[tuple[str, Image.Image]] = []
    for palette in PALETTES:
        if prefixes and not any(palette.name.startswith(prefix) for prefix in prefixes):
            continue
        started = time.perf_counter()
        recipe = palette.recipe(args.seed)
        image = _to_image(recipe)
        if args.size:
            width_text, _, height_text = args.size.partition("x")
            image = _cover(image, int(width_text), int(height_text))
        path = output / f"{palette.name}-{recipe.digest()}.png"
        image.save(path)
        elapsed = time.perf_counter() - started
        print(f"{path.name:32s} {elapsed:5.2f}s  {palette.title}")
        rendered.append((palette.title, image))

    if len(rendered) > 1:
        sheet_path = output / "contact-sheet.png"
        contact_sheet(rendered).save(sheet_path)
        print(f"{sheet_path.name:32s}         {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
