"""缩略图尺寸档位，以及把连续手势攒成离散档位的累加器。

**为什么是三个离散档位而不是连续缩放**：缩略图磁盘缓存按固定长边生成，
离散档位能让「同一张照片」的缓存组合保持少量且稳定；连续缩放则会逼出
「同一个尺寸系数对应一份缓存」的爆炸，或者干脆永远糊一层。

**最大档不得超过缩略图缓存的长边**（``DEFAULT_THUMBNAIL_MAX_EDGE``），
否则界面等于把小图拉大。这条不变量由测试守着。

捏合与滚轮都是连续事件（一次手势会来十几个小增量），直接每来一个增量就切一档
会瞬间从最小跳到最大，所以用 :class:`StepAccumulator` 攒够阈值才切一档。
这里刻意不导入 Qt，好让这套逻辑能用普通 pytest 覆盖。
"""

from __future__ import annotations

from typing import Final

#: 三档缩略图长边（像素），从最小到最大。均不超过 512 的缓存长边。
THUMBNAIL_SIZES: Final[tuple[int, ...]] = (112, 176, 288)

#: 与 :data:`THUMBNAIL_SIZES` 一一对应的中文档位名。
THUMBNAIL_LABELS: Final[tuple[str, ...]] = ("小", "中", "大")

#: 默认档位：中间的「中」，也就是查看器一直以来的尺寸。
DEFAULT_INDEX: Final = 1

#: 默认缩略图长边。
DEFAULT_EDGE: Final = THUMBNAIL_SIZES[DEFAULT_INDEX]

#: 触控板捏合的切换阈值（原生手势事件的 value 累计到这个量就切一档）。
PINCH_THRESHOLD: Final = 0.1

#: ⌘+滚轮的切换阈值：一格传统滚轮是 120，触控板则是一连串小增量。
WHEEL_THRESHOLD: Final = 120.0


def clamp_index(index: int) -> int:
    """把档位序号夹到合法范围。"""
    return max(0, min(len(THUMBNAIL_SIZES) - 1, index))


def edge_for_index(index: int) -> int:
    """档位序号 → 缩略图长边。"""
    return THUMBNAIL_SIZES[clamp_index(index)]


def label_for_index(index: int) -> str:
    """档位序号 → 中文档位名。"""
    return THUMBNAIL_LABELS[clamp_index(index)]


def shifted_index(index: int, step: int) -> int:
    """在档位之间移动 ``step`` 档，越界即停在两端。"""
    return clamp_index(clamp_index(index) + step)


def nearest_index(edge: int | None) -> int:
    """把任意长边（例如上次会话记下来的值）归到最近的档位。"""
    if edge is None:
        return DEFAULT_INDEX
    return min(range(len(THUMBNAIL_SIZES)), key=lambda index: abs(THUMBNAIL_SIZES[index] - edge))


def describe(index: int) -> str:
    """状态栏提示文案。"""
    return f"缩略图：{label_for_index(index)}（{edge_for_index(index)} 像素）"


class StepAccumulator:
    """把连续的手势/滚轮增量攒成离散的档位切换。

    正数表示「放大」（更大的缩略图），负数表示「缩小」。
    """

    __slots__ = ("_threshold", "_accumulated")

    def __init__(self, threshold: float) -> None:
        if threshold <= 0:
            raise ValueError("阈值必须为正数")
        self._threshold = threshold
        self._accumulated = 0.0

    @property
    def accumulated(self) -> float:
        return self._accumulated

    def feed(self, delta: float) -> int:
        """喂入一个增量，返回本次应切换的档数（通常为 0，也可能是 ±1 或更多）。"""
        if delta != delta:  # NaN：忽略，否则累加器会被永久污染
            return 0
        self._accumulated += delta
        steps = 0
        while self._accumulated >= self._threshold:
            self._accumulated -= self._threshold
            steps += 1
        while self._accumulated <= -self._threshold:
            self._accumulated += self._threshold
            steps -= 1
        return steps

    def reset(self) -> None:
        self._accumulated = 0.0


__all__ = [
    "DEFAULT_EDGE",
    "DEFAULT_INDEX",
    "PINCH_THRESHOLD",
    "THUMBNAIL_LABELS",
    "THUMBNAIL_SIZES",
    "WHEEL_THRESHOLD",
    "StepAccumulator",
    "clamp_index",
    "describe",
    "edge_for_index",
    "label_for_index",
    "nearest_index",
    "shifted_index",
]
