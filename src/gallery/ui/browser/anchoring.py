"""滚动锚定：换几何前后「视口正中那一格」还停在原处。

列数一变，同一个像素值指的已经是另一批照片（见 :func:`_anchored_scroll_value`），所以
换几何前先记下正中那一格的行号与屏幕位置，换完再把它摆回去。判据只在这一处，混进排版与
过渡里就会各算一份、两边对不上。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint


@dataclass(frozen=True)
class _ScrollAnchor:
    """换几何**之前**，视口正中那一格：行号，以及它当时在屏幕上的纵向位置。

    **两个都要**：行号用来算它换完之后落在第几行（列数一变行号就变了），屏幕位置
    用来把它摆回原处 —— 只摆到视口正中会多挪最多半格，那也是一跳。
    """

    row: int
    screen_y: int


class AnchoringMixin:
    """把「用户正看着的那一格」在几何变化前后钉在原处。"""

    def _anchor(self) -> _ScrollAnchor:
        """视口正中那一格：行号 + 它这一刻在屏幕上的纵向位置。

        **必须在改几何之前问**：改完之后「正中」已经指到另一格、格子高矮也变了。
        """
        middle = QPoint(self.viewport().width() // 2, self.viewport().height() // 2)
        index = self.indexAt(middle)
        if not index.isValid():
            return _ScrollAnchor(-1, middle.y())
        return _ScrollAnchor(index.row(), self.visualRect(index).center().y())

    def _anchored_scroll_value(self, anchor: _ScrollAnchor, columns: int) -> int | None:
        """把 ``anchor`` 那一格摆回它换几何之前所在的位置时，滚动条该在的值。

        这是过渡**第 0 帧**要落的地方。滚动位置是内容坐标，格子高矮与每行张数一变，
        同一个像素值指的就是**另一批照片** —— 照搬旧值，末尾放大一档时滚动条会一上来
        从 100% 跳到 46%（屏上正中的那一格从第 795 张变成第 366 张），然后才一路滑
        回来。缩小方向同样会闪。画面本身要与它配合（见
        :attr:`GridTransition.scroll_shift`），否则第 0 帧画出来的是「旧画面 + 新滚动
        位置」。量不出那一格（``row < 0``）时返回 ``None``，调用方退回像素值。
        """
        if anchor.row < 0 or columns <= 0:
            return None
        top = (anchor.row // columns) * self.gridSize().height()
        target = top + self.gridSize().height() // 2 - anchor.screen_y
        bar = self.verticalScrollBar()
        return max(bar.minimum(), min(bar.maximum(), target))

    def _focus_row(self, anchor_row: int) -> int:
        """换了行之后该把哪一格摆到正中。

        有真选区时是当前项，否则是换行之前视口正中那一格。**不能退回 ``currentIndex()``**：
        Qt 在模型重置与每一次重排之后都会把当前项顺手设成第 0 行，照它居中就等于
        「在相册中间放大一档 → 整屏弹回相册顶部」。
        """
        if self.genuinely_selected_rows():
            row = self.current_row()
            if row >= 0:
                return row
        if anchor_row >= 0:
            return anchor_row
        return self.current_row()

    def _centered_scroll_value(self, row: int, columns: int) -> int:
        """把第 ``row`` 行摆到正中时滚动条该在的值（夹进范围）。

        格子的位置由「``columns`` 列、每格 ``cell``」直接算出（:class:`GridTransition`
        用的是同一条算术），**与那一行有没有被排版过无关**。夹范围只为内容比视口短的
        情况；``columns`` 量不出来时退回 :attr:`_laid_out_columns`。
        """
        if columns <= 0:
            columns = max(1, self._laid_out_columns)
        cell_height = self.gridSize().height()
        top = (row // columns) * cell_height
        bar = self.verticalScrollBar()
        target = top + cell_height // 2 - self.viewport().height() // 2
        return max(bar.minimum(), min(bar.maximum(), target))

    def _center_on_row(self, row: int, columns: int) -> None:
        """把第 ``row`` 行摆到视口正中间（``columns`` 是当前这套布局实际的列数）。

        **不用 ``scrollTo(PositionAtCenter)``**：它要问 ``visualRect``，而 Batched 布局下
        超出已排版那一批的行返回的是空矩形，于是它静默什么都不做。记下 ``row``
        （:attr:`_centered_row`）供几何变动后重算终点（见 :meth:`_examine_columns`）。
        """
        self._centered_row = row
        target = self._centered_scroll_value(row, columns)
        # :meth:`_start_transition` 紧接着就拿滚动条的当前位置当终点，所以这里**必须**摆过去。
        self._scroll_to = target
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(target)
        finally:
            self._driving_scroll = False


__all__ = ["AnchoringMixin"]
