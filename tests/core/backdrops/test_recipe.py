"""自定义配色：一到几个颜色 → 一套由浅到深的晕染（ADR 0101 / 0102）。

锁四件事：停点是**由浅到深**的（否则生成出来是一块深色，正文立刻看不清）、起手那几档
接近白色、几个颜色确实是**穿过彼此**的（不是只用最后一个），以及颜色整份写进了配方 ——
缓存被清掉之后要能靠它重算。
"""

from __future__ import annotations

import pytest

from gallery.core.backdrops import CUSTOM_PRESET, name_suggestion
from gallery.core.backdrops.recipe import palette_from_color, palette_from_colors


def test_a_colour_becomes_a_light_to_dark_ramp() -> None:
    palette = palette_from_color("#8ea8bf")

    assert palette.name == CUSTOM_PRESET
    assert palette.title == "自定义 · #8ea8bf"
    assert [position for position, _ in palette.colors] == [0.0, 0.28, 0.5, 0.72, 0.88, 1.0]


def test_the_ramp_starts_near_white_and_keeps_the_hue() -> None:
    palette = palette_from_color("#8ea8bf")
    first = palette.colors[0][1]
    last = palette.colors[-1][1]

    assert all(int(first[index : index + 2], 16) >= 240 for index in (1, 3, 5))
    # 原色偏蓝：最后一档里蓝通道仍该比红通道高。
    assert int(last[5:7], 16) > int(last[1:3], 16)


def test_one_colour_is_the_same_ramp_as_the_single_colour_helper() -> None:
    """``palette_from_color`` 只是 ``palette_from_colors`` 的一个颜色。"""
    assert palette_from_color("#336699") == palette_from_colors(["#336699"])


def _rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def test_two_colours_go_through_both_hues() -> None:
    blue_only = palette_from_color("#336699")
    orange_only = palette_from_color("#b06a3b")
    mixed = palette_from_colors(["#336699", "#b06a3b"])

    assert mixed.title == "自定义 · #336699 → #b06a3b"
    # 起点与中段偏蓝、后段偏橙：两个颜色各占一段浓度。
    assert _rgb(mixed.colors[0][1])[2] > _rgb(mixed.colors[0][1])[0]
    assert _rgb(mixed.colors[1][1])[2] > _rgb(mixed.colors[1][1])[0]
    assert _rgb(mixed.colors[-1][1])[0] > _rgb(mixed.colors[-1][1])[2]
    # 两端都是淡色：末档与「只有橙色」那一套的末档同色，起点也还在可读的浅色里。
    assert mixed.colors[-1][1] == orange_only.colors[-1][1]
    assert min(_rgb(mixed.colors[0][1])) >= 200
    # 中间那一格是两色的混合，不是任何一套单色的同一档。
    assert mixed.colors[2][1] not in {blue_only.colors[2][1], orange_only.colors[2][1]}


def test_three_colours_each_get_a_band_of_their_own() -> None:
    palette = palette_from_colors(["#336699", "#b06a3b", "#7a9e7e"])

    first = _rgb(palette.colors[0][1])
    middle = _rgb(palette.colors[2][1])
    greenish = _rgb(palette.colors[3][1])
    last = _rgb(palette.colors[5][1])

    assert first[2] > first[0], "起点该是第一个颜色（蓝）"
    assert middle[0] > middle[1], "中间那段该是第二个颜色（橙）"
    assert greenish[1] > greenish[0] and greenish[1] > greenish[2], "后段该是第三个颜色（绿）"
    assert last[1] > last[0], "末档仍是绿"


def test_three_colours_are_all_in_the_title() -> None:
    palette = palette_from_colors(["#336699", "#b06a3b", "#7a9e7e"])

    assert palette.title == "自定义 · #336699 → #b06a3b → #7a9e7e"


def test_the_recipe_carries_the_whole_palette() -> None:
    """配方必须自足 —— 清掉缓存之后要靠它把同一张图重算出来。"""
    palette = palette_from_colors(["#336699", "#b06a3b"])
    recipe = palette.recipe(7)

    assert recipe.preset == CUSTOM_PRESET
    assert recipe.colors == palette.colors
    assert palette_from_colors(["#336699", "#b06a3b"]).recipe(7).digest() == recipe.digest()
    # 顺序也是配方的一部分：反过来就是另一幅画。
    assert palette_from_colors(["#b06a3b", "#336699"]).recipe(7).digest() != recipe.digest()


def test_a_bad_colour_is_refused() -> None:
    with pytest.raises(ValueError, match="#rrggbb"):
        palette_from_color("#12345")


def test_no_colour_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="至少要一个颜色"):
        palette_from_colors([])


def test_a_custom_palette_has_no_name_of_its_own_in_the_preset_table() -> None:
    """自定义那套不在 ``PALETTES`` 里，所以默认名字由调用方递进来。"""
    palette = palette_from_color("#336699")
    recipe = palette.recipe(1)

    assert name_suggestion(recipe, fallback=palette.title) == palette.title
    assert name_suggestion(recipe) == "背景"
