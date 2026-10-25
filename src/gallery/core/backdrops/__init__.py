"""背景图：按配方生成、把用户挑的照片转存进缓存，以及那一份不进缓存的清单。

「背景色」是 :mod:`gallery.ui.theme` 里的事（ADR 0097），这里只管**图**。产物是缓存，
但清单不是 —— 名字是用户填的，重扫重建不出来；图的丢失靠配方与来源路径自愈。
"""

from gallery.core.backdrops.recipe import PALETTES, PALETTES_BY_NAME, Palette, Recipe
from gallery.core.backdrops.store import (
    KIND_INK,
    KIND_PHOTO,
    MAX_NAME_LENGTH,
    Backdrop,
    BackdropError,
    BackdropStore,
    name_suggestion,
)

__all__ = [
    "KIND_INK",
    "KIND_PHOTO",
    "MAX_NAME_LENGTH",
    "PALETTES",
    "PALETTES_BY_NAME",
    "Backdrop",
    "BackdropError",
    "BackdropStore",
    "Palette",
    "Recipe",
    "name_suggestion",
]
