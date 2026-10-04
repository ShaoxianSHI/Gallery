"""日历的**排版计算**：一行多高、一格多大、一行放几格、照片在格子里落在哪。

纯 Python，不碰 Qt —— 与 :mod:`gallery.core.library.calendar` 是同一个路数：
几何只有一处算法，绘制与测试都量它，不必再复刻一遍魔数。

**格子比例是定的，照片按自己的比例缩进去**（与
:meth:`gallery.ui.browser.grid.Cell.target_rect` 同一手法）。3:2 的照片放进方格里
左右各留一条，那正是网格视图一直在画的样子；反过来把照片裁满格子，那才是另一件事。
"""

from __future__ import annotations

from typing import Final

#: 一格的宽高比（宽 ÷ 高）。**这是格子的比例，与照片本身无关** —— 照片按它缩。
CELL_ASPECT: Final = 1.5

#: 格子四边留白（像素）。周视图的行高里也要扣掉这一份，否则一格铺不满一列。
CELL_GAP: Final = 5.0

#: 圆角（像素）。
CARD_RADIUS: Final = 6.0

#: 日视图一行（一个小时）封顶多高。
#:
#: 不封顶的话行高由列宽除以比例算出来：日视图那一列是一整行宽（约 1200 像素），
#: 一行就高到 800 上下 —— 比整屏还高，于是「一个小时的行长宽比」正好倒过来。
DAY_ROW_CAP: Final = 84.0

#: 一行至少多高（周视图在窄窗口下仍要放得下一格的缩略图）。
MIN_ROW_HEIGHT: Final = 56.0

#: 时间轴上一天有几个小时。
HOURS_PER_DAY: Final = 24


def row_height(level: str, column: float) -> float:
    """一行（一个小时）多高。周视图与日视图的差别只在这一处。

    - 周视图：净高定成「一格正好铺满这一列」，于是**每小时恰好一格** ——
      「一个小时一张」不是一条规则，而是放不下第二格的自然结果。
    - 日视图：行高封顶，剩下的宽度全用来并排放几张。
    """
    if level == "day":
        return DAY_ROW_CAP
    return max(MIN_ROW_HEIGHT, (column - 2 * CELL_GAP) / CELL_ASPECT + 2 * CELL_GAP)


def cell_size(row: float) -> tuple[float, float]:
    """一行里**一格**的（宽, 高）。"""
    height = max(1.0, row - 2 * CELL_GAP)
    return height * CELL_ASPECT, height


def cells_in_row(row_width: float, row: float) -> int:
    """一行放得下几格（至少一格 —— 没有照片的那几小时是空的，不是零格）。"""
    cell_w, _cell_h = cell_size(row)
    return max(1, int((row_width - 2 * CELL_GAP) // (cell_w + CELL_GAP)))


def cells_and_offset(row_width: float, row: float) -> tuple[int, float]:
    """``(放得下几格, 整排左边留多少)``。

    日视图那一格并排放好几张，而用户刚刚明确要它们**居中** —— 照片从左边起画的话，
    一小时放 3 张时右边空出一条，一小时放 9 张时几乎看不出来。居中之后两种都齐。
    """
    count = cells_in_row(row_width, row)
    cell_w, _cell_h = cell_size(row)
    used = count * cell_w + (count - 1) * CELL_GAP
    # **扣掉两边各留的缝再对半分**：画的时候左边是 ``CELL_GAP + offset``，
    # 不扣那一份的话右边会少一个 ``CELL_GAP``，整排偏左 10 像素。
    return count, max(0.0, (row_width - 2 * CELL_GAP - used) / 2)


def fitted_size(source_w: int, source_h: int, box_w: float, box_h: float) -> tuple[float, float]:
    """一张 ``source_w × source_h`` 的照片放进 ``box_w × box_h`` 的格子里，
    按原比例缩放之后是多大（居中由调用方做）。

    分母是 0（源图或格子有一边为零）时退回整格，免得算出 ``ZeroDivisionError``。
    """
    if source_w <= 0 or source_h <= 0 or box_w <= 0 or box_h <= 0:
        return max(0.0, box_w), max(0.0, box_h)
    factor = min(box_w / source_w, box_h / source_h)
    return source_w * factor, source_h * factor


def month_cells(level_days: int = 6, week_days: int = 7) -> int:
    """月视图要几格：6 行 × 7 列 = 42（与 ``calendar.days_of('month', …)`` 对齐）。"""
    return level_days * week_days


__all__ = [
    "CARD_RADIUS",
    "CELL_ASPECT",
    "CELL_GAP",
    "DAY_ROW_CAP",
    "HOURS_PER_DAY",
    "MIN_ROW_HEIGHT",
    "cell_size",
    "cells_and_offset",
    "cells_in_row",
    "fitted_size",
    "month_cells",
    "row_height",
]
