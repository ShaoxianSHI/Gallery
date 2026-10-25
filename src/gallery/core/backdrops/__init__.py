"""背景图：按配方生成、把用户挑的照片转存进缓存，以及那一份不进缓存的清单。

「背景色」是 :mod:`gallery.ui.theme` 里的事（ADR 0097），这里只管**图**。产物是缓存，
但清单不是 —— 名字是用户填的，重扫重建不出来；图的丢失靠配方与来源路径自愈。
"""

from gallery.core.backdrops.recipe import (
    CUSTOM_PRESET,
    PALETTES,
    PALETTES_BY_NAME,
    Palette,
    Recipe,
    palette_from_color,
    palette_from_colors,
    palette_id,
)
from gallery.core.backdrops.store import (
    KIND_INK,
    KIND_PHOTO,
    MAX_NAME_LENGTH,
    PALETTE_CUSTOM,
    PALETTE_PRESET,
    Backdrop,
    BackdropError,
    BackdropStore,
    PaletteEntry,
    default_palette_row,
    name_suggestion,
)

__all__ = [
    "CUSTOM_PRESET",
    "KIND_INK",
    "KIND_PHOTO",
    "MAX_NAME_LENGTH",
    "PALETTES",
    "PALETTES_BY_NAME",
    "PALETTE_CUSTOM",
    "PALETTE_PRESET",
    "Backdrop",
    "BackdropError",
    "BackdropStore",
    "Palette",
    "PaletteEntry",
    "Recipe",
    "default_palette_row",
    "name_suggestion",
    "palette_from_color",
    "palette_from_colors",
    "palette_id",
]
