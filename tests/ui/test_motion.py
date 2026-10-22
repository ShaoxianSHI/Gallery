"""动画速度的换算（``gallery.ui.motion``，ADR 0073）。

这一层不碰 Qt：它只答「这一档下 160 毫秒该变成多少」。真正把值接到控件上的那一段在
``test_settings_menu.py``，本文件只锁定三件事 —— 中档就是原来那个数、关档是 0、
以及捏合限速**永远不短于切档动画**。
"""

from __future__ import annotations

import pytest

from gallery.ui.browser.transition import EDGE_ANIMATION_MS
from gallery.ui.motion import SPEED_SCALES, pinch_interval_ms, scaled_ms
from gallery.ui.thumb_sizes import MIN_PINCH_INTERVAL_MS

EDGE = EDGE_ANIMATION_MS


def test_medium_is_exactly_the_number_we_started_with() -> None:
    assert scaled_ms(EDGE, "normal") == EDGE


@pytest.mark.parametrize(
    ("speed", "expected"),
    [("slow", 256), ("fast", 96), ("off", 0)],
)
def test_each_speed_scales_the_base_duration(speed: str, expected: int) -> None:
    assert scaled_ms(EDGE, speed) == expected


def test_an_unknown_speed_falls_back_to_medium() -> None:
    assert scaled_ms(EDGE, "turbo") == EDGE


def test_the_off_speed_is_zero_for_every_scale() -> None:
    assert SPEED_SCALES["off"] == 0.0
    assert scaled_ms(450, "off") == 0


@pytest.mark.parametrize("speed", ["slow", "normal", "fast", "off"])
def test_the_pinch_interval_is_never_shorter_than_the_animation(speed: str) -> None:
    """``MIN_PINCH_INTERVAL_MS`` 原本就是为这条设的；慢档把动画拉长之后它必须跟着拉长。"""
    assert pinch_interval_ms(EDGE, speed, minimum=MIN_PINCH_INTERVAL_MS) >= scaled_ms(EDGE, speed)
    assert pinch_interval_ms(EDGE, speed, minimum=MIN_PINCH_INTERVAL_MS) >= MIN_PINCH_INTERVAL_MS


def test_the_slow_speed_really_raises_the_old_pinch_floor() -> None:
    """慢档 256ms 已经高过原来那 220ms —— 不跟着抬就是「限速比动画还短」。"""
    assert pinch_interval_ms(EDGE, "slow", minimum=MIN_PINCH_INTERVAL_MS) > MIN_PINCH_INTERVAL_MS
