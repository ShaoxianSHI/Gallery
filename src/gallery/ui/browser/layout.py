"""一套几何下「第几行摆在哪」—— 切档过渡、锚点与可视区共用这一份。

纯像素数学：**不读 ``visualRect``**，所以没被 Qt 排过版的那几百行也算得出位置。行号算术
（``row // 列数 × 格高``）在分区式里不成立 —— 标题行夹在照片行中间、行高有两档、列数一变
后面所有分区的位移量取决于它前面有几个标题。这里换成「分区 + 两档行高」，两种形态共用。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize

__all__ = ["BlockRow", "GridLayout", "SectionSpan"]


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """模型里一段连续的行：要么是「标题 + 成员」，要么是最前面那批相册。"""

    #: 这一段第一行的模型行号。
    start: int
    #: 这一段一共几行（标题算一行）。
    count: int
    #: 头一行是不是分区标题。
    headed: bool
    #: 这一段在内容坐标里的顶边。
    top: int


@dataclass(frozen=True, slots=True)
class BlockRow:
    """一行**格子**：标题自己占一行，或者一行照片。"""

    top: int
    height: int
    first: int
    last: int


@dataclass(frozen=True, slots=True)
class GridLayout:
    """一套几何下的位置表。``columns`` 必须是**量出来**的实际列数，不是打算摆几列。"""

    columns: int
    cell: QSize
    header: QSize
    spans: tuple[SectionSpan, ...]
    blocks: tuple[BlockRow, ...]

    @classmethod
    def of(
        cls,
        *,
        row_count: int,
        header_rows: Sequence[int],
        columns: int,
        cell: QSize,
        header: QSize,
    ) -> GridLayout:
        """按模型的行数与标题行号排一份位置表。

        ``header_rows`` 必须升序（模型给的就是）。没有标题时整层是一段无头的分区 —— 卡片式
        走的正是这一支，位置与旧的「行号 ÷ 列数 × 格高」逐字相同。
        """
        columns = max(1, columns)
        segments: list[tuple[int, int, bool]] = []
        headers = [row for row in header_rows if 0 <= row < row_count]
        if headers:
            if headers[0] > 0:
                segments.append((0, headers[0], False))
            for index, row in enumerate(headers):
                end = headers[index + 1] if index + 1 < len(headers) else row_count
                segments.append((row, end - row, True))
        else:
            segments.append((0, row_count, False))

        spans: list[SectionSpan] = []
        blocks: list[BlockRow] = []
        top = 0
        for start, count, headed in segments:
            if count <= 0:
                continue
            spans.append(SectionSpan(start=start, count=count, headed=headed, top=top))
            members_from = start + 1 if headed else start
            members = count - 1 if headed else count
            if headed:
                blocks.append(BlockRow(top=top, height=header.height(), first=start, last=start))
                top += header.height()
            for offset in range(0, members, columns):
                first = members_from + offset
                last = min(members_from + members - 1, first + columns - 1)
                blocks.append(BlockRow(top=top, height=cell.height(), first=first, last=last))
                top += cell.height()
        return cls(
            columns=columns,
            cell=cell,
            header=header,
            spans=tuple(spans),
            blocks=tuple(blocks),
        )

    def rect(self, row: int) -> QRect:
        """第 ``row`` 行这一格的位置与尺寸（内容坐标）。越界给空矩形。"""
        span = self._span(row)
        if span is None:
            return QRect()
        offset = row - span.start
        if span.headed:
            if offset == 0:
                return QRect(0, span.top, self.header.width(), self.header.height())
            offset -= 1
        row_index, column = divmod(offset, self.columns)
        top = span.top + (self.header.height() if span.headed else 0)
        top += row_index * self.cell.height()
        return QRect(column * self.cell.width(), top, self.cell.width(), self.cell.height())

    def rows_near(self, top: int, bottom: int) -> list[int]:
        """纵向落在 ``[top, bottom)`` 里的行号（升序，含标题行）。

        二分到第一块可能还露头的那一行，再顺着块往后走 —— 与库的大小无关。
        """
        if not self.blocks or bottom <= top:
            return []
        index = max(0, bisect_right(self.blocks, top, key=lambda block: block.top) - 1)
        rows: list[int] = []
        while index < len(self.blocks):
            block = self.blocks[index]
            if block.top >= bottom:
                break
            if block.top + block.height > top:
                rows.extend(range(block.first, block.last + 1))
            index += 1
        return rows

    def _span(self, row: int) -> SectionSpan | None:
        if row < 0 or not self.spans:
            return None
        index = bisect_right(self.spans, row, key=lambda span: span.start) - 1
        if index < 0:
            return None
        span = self.spans[index]
        if row >= span.start + span.count:
            return None
        return span
