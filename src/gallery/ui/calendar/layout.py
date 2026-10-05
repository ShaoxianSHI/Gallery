"""日历的**排版计算**：一行多高、一格多大、照片在格子里落在哪 —— 纯 Python，不碰 Qt。

**格子比例是定的，照片按自己的比例缩进去**（与网格视图同一手法）：3:2 的照片放进方格里
左右各留一条，而不是裁满格子。
"""

from __future__ import annotations

from typing import Final

#: 一格的宽高比（宽 ÷ 高）。**这是格子的比例，与照片本身无关** —— 照片按它缩。
CELL_ASPECT: Final = 1.5

#: 格子四边留白（像素）。周视图的行高里也要扣掉这一份，否则一格铺不满一列。
CELL_GAP: Final = 5.0

CARD_RADIUS: Final = 6.0

#: 一行至少多高（周视图在窄窗口下仍要放得下一格的缩略图）。
MIN_ROW_HEIGHT: Final = 56.0

HOURS_PER_DAY: Final = 24


def row_height(column: float) -> float:
    """一行（一个小时）多高。

    净高定成「一格正好铺满这一列」，于是**每小时恰好一格** ——
    「一个小时一张」不是一条规则，而是放不下第二格的自然结果。
    """
    return max(MIN_ROW_HEIGHT, (column - 2 * CELL_GAP) / CELL_ASPECT + 2 * CELL_GAP)


def cell_size(row: float) -> tuple[float, float]:
    """一行里**一格**的（宽, 高）。"""
    height = max(1.0, row - 2 * CELL_GAP)
    return height * CELL_ASPECT, height


def cell_rect(row_width: float, row: float) -> tuple[float, float, float, float]:
    """一格在行里的落位：``(x, y, 宽, 高)``，坐标相对这一行的左上角。

    **永远居中**（不靠左、不摊开）；**窄列按列宽收窄而不是溢出**到隔壁那一天上去。
    """
    cell_w, cell_h = cell_size(row)
    available = max(1.0, row_width - 2 * CELL_GAP)
    if cell_w > available:
        cell_w, cell_h = available, available / CELL_ASPECT
    return CELL_GAP + (available - cell_w) / 2, CELL_GAP, cell_w, cell_h


def fitted_size(source_w: int, source_h: int, box_w: float, box_h: float) -> tuple[float, float]:
    """一张 ``source_w × source_h`` 的照片按原比例缩进 ``box_w × box_h`` 之后是多大。

    有一边为零时退回整格，免得算出 ``ZeroDivisionError``；居中由调用方做。
    """
    if source_w <= 0 or source_h <= 0 or box_w <= 0 or box_h <= 0:
        return max(0.0, box_w), max(0.0, box_h)
    factor = min(box_w / source_w, box_h / source_h)
    return source_w * factor, source_h * factor


def month_cells(level_days: int = 6, week_days: int = 7) -> int:
    """月视图要几格：6 行 × 7 列 = 42（与 ``calendar.month_matrix(…)`` 对齐）。

    **这 42 格含上下月补位**；那一月自己的日子只有 29–31 天 —— 格子数与照片数是两回事。
    """
    return level_days * week_days


__all__ = [
    "CARD_RADIUS",
    "CELL_ASPECT",
    "CELL_GAP",
    "HOURS_PER_DAY",
    "MIN_ROW_HEIGHT",
    "cell_rect",
    "cell_size",
    "fitted_size",
    "month_cells",
    "row_height",
]
