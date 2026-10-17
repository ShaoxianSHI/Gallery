"""事件名在界面上怎么截短 —— 菜单、横幅、元数据面板共用同一条规矩。"""

from __future__ import annotations

from gallery.core.events.models import DISPLAY_NAME_MAX, shorten_name


def test_a_short_name_is_left_alone() -> None:
    assert shorten_name("京都五日") == "京都五日"


def test_a_name_exactly_at_the_limit_is_left_alone() -> None:
    name = "字" * DISPLAY_NAME_MAX

    assert shorten_name(name) == name


def test_a_longer_name_is_elided_in_the_middle_to_the_limit() -> None:
    """两头留下 —— 日期在头、地点常在后半段，中间那一截最不值钱。"""
    name = "2026-05-16 · 京都、奈良、大阪、神戸、姫路"

    shortened = shorten_name(name)

    assert len(shortened) == DISPLAY_NAME_MAX
    assert shortened.startswith(name[:9])
    assert shortened.endswith(name[-10:])
    assert "…" in shortened
