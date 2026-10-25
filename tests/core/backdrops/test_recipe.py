"""自定义配色：一个颜色起一套，或者一串颜色各占一段（ADR 0101 / 0103）。

锁四件事：一个颜色起手时是从浅到深的（否则生成出来是一块深色，正文立刻看不清）；
一串颜色**按用户给的次序均分**整条浓度轴、原样使用；至少两个颜色；以及颜色整份写进配方 ——
缓存被清掉之后要能靠它重算。
"""

from __future__ import annotations

import pytest

from gallery.core.backdrops import CUSTOM_PRESET, name_suggestion
from gallery.core.backdrops.recipe import palette_from_color, palette_from_colors


def test_a_colour_starts_a_light_to_dark_ramp() -> None:
    palette = palette_from_color("#8ea8bf")

    assert palette.name == CUSTOM_PRESET
    assert palette.title == "自定义 · #8ea8bf"
    assert [position for position, _ in palette.colors] == [0.0, 0.28, 0.5, 0.72, 0.88, 1.0]


def test_the_starter_ramp_is_light_and_keeps_the_hue() -> None:
    palette = palette_from_color("#8ea8bf")
    first = palette.colors[0][1]
    last = palette.colors[-1][1]

    assert all(int(first[index : index + 2], 16) >= 240 for index in (1, 3, 5))
    # 原色偏蓝：最后一档里蓝通道仍该比红通道高。
    assert int(last[5:7], 16) > int(last[1:3], 16)


def test_colours_are_used_as_given_at_even_positions() -> None:
    palette = palette_from_colors(["#336699", "#b06a3b", "#7a9e7e"], title="我的配色")

    assert palette.name == CUSTOM_PRESET
    assert palette.title == "我的配色"
    assert palette.colors == ((0.0, "#336699"), (0.5, "#b06a3b"), (1.0, "#7a9e7e"))


def test_the_recipe_carries_the_whole_palette() -> None:
    """配方必须自足 —— 清掉缓存之后要靠它把同一张图重算出来。"""
    palette = palette_from_colors(["#336699", "#b06a3b"])
    recipe = palette.recipe(7)

    assert recipe.preset == CUSTOM_PRESET
    assert recipe.colors == palette.colors
    assert palette_from_colors(["#336699", "#b06a3b"]).recipe(7).digest() == recipe.digest()
    # 次序也是配方的一部分：反过来就是另一幅画。
    assert palette_from_colors(["#b06a3b", "#336699"]).recipe(7).digest() != recipe.digest()


def test_one_colour_is_not_enough_for_a_ramp() -> None:
    with pytest.raises(ValueError, match="至少要两个颜色"):
        palette_from_colors(["#336699"])


def test_a_bad_colour_is_refused() -> None:
    with pytest.raises(ValueError, match="#rrggbb"):
        palette_from_color("#12345")
    with pytest.raises(ValueError, match="#rrggbb"):
        palette_from_colors(["#336699", "#12345"])


def test_a_custom_palette_has_no_name_of_its_own_in_the_preset_table() -> None:
    """自定义那套不在 ``PALETTES`` 里，所以默认名字由调用方递进来。"""
    palette = palette_from_color("#336699")
    recipe = palette.recipe(1)

    assert name_suggestion(recipe, fallback=palette.title) == palette.title
    assert name_suggestion(recipe) == "背景"
