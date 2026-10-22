"""动画时长按「慢 / 中 / 快 / 关」缩放。

**只缩动画**：横幅停留、防抖与轮询各有自己的时长约束（有的要求「不得短于另一项」），
跟着一起缩会让那些不变量悄悄失效。速度档位本身存在应用级会话里（ADR 0073）。
"""

from __future__ import annotations

from typing import Final

#: 每一档相对「中」的倍率。**「关」是 0**，调用方据此走「不播动画、直接落位」的分支。
SPEED_SCALES: Final[dict[str, float]] = {
    "slow": 1.6,
    "normal": 1.0,
    "fast": 0.6,
    "off": 0.0,
}


def scaled_ms(base_ms: int, speed: str) -> int:
    """这一档下 ``base_ms`` 该变成多少毫秒；不认识的速度按「中」算。"""
    return round(base_ms * SPEED_SCALES.get(speed, 1.0))


def pinch_interval_ms(edge_ms: int, speed: str, *, minimum: int) -> int:
    """捏合限速的最小间隔。**不得短于切档动画**，否则限速形同虚设。"""
    return max(minimum, scaled_ms(edge_ms, speed))


__all__ = ["SPEED_SCALES", "pinch_interval_ms", "scaled_ms"]
