"""滚动位置：换几何前后「用户看着的这一段」还留在原处。

同一个像素值在换过格子高矮或列数之后指的是另一批照片，所以换档按**相对位置**换算
（见 :meth:`PhotoGrid._set_cell`），换行时把选中的那一格摆回正中。判据只在这一处，
混进排版与过渡里就会各算一份、两边对不上。
"""

from __future__ import annotations

from gallery.ui.browser.layout import GridLayout


class AnchoringMixin:
    """把「用户看着的这一段」在几何变化前后保持在原处。"""

    def _focus_row(self) -> int:
        """换了行之后该把哪一格摆到正中；没有真选区时返回 ``-1``（不居中）。

        没选中时用户看的是「这一片内容」，缩放前后它该待在原处；居中会把靠边的位置
        推向两端（末尾那一屏尤其明显）。**不能退回 ``currentIndex()``**：Qt 在重排之后
        会把当前项顺手设成第 0 行，照它居中就是整屏弹回相册顶部。
        """
        if self.genuinely_selected_rows():
            return self.current_row()
        return -1

    def _keep_scroll(self, value: int) -> None:
        """把滚动条摆到 ``value`` 并把它当作这一趟的终点：不居中，也就不滑。

        没选中时换一档要的是「同一段内容还在这里」，起点与终点因此是同一个值。
        """
        self._centered_row = -1
        self._scroll_to = value
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(value)
        finally:
            self._driving_scroll = False

    def _centered_scroll_value(self, row: int, layout: GridLayout) -> int:
        """把第 ``row`` 行摆到正中时滚动条该在的值（夹进范围）。

        格子的位置来自 :class:`~gallery.ui.browser.layout.GridLayout`（过渡用的是同一份），
        **与那一行有没有被排版过无关**。夹范围只为内容比视口短的情况。
        """
        cell = layout.rect(row)
        if cell.isEmpty():
            return self.verticalScrollBar().value()
        bar = self.verticalScrollBar()
        target = cell.top() + cell.height() // 2 - self.viewport().height() // 2
        return max(bar.minimum(), min(bar.maximum(), target))

    def _center_on_row(self, row: int, layout: GridLayout) -> None:
        """把第 ``row`` 行摆到视口正中间（``layout`` 是当前这套几何的位置表）。

        **不用 ``scrollTo(PositionAtCenter)``**：它要问 ``visualRect``，而 Batched 布局下
        超出已排版那一批的行返回的是空矩形，于是它静默什么都不做。记下 ``row``
        （:attr:`_centered_row`）供几何变动后重算终点（见 :meth:`_examine_columns`）。
        """
        self._centered_row = row
        target = self._centered_scroll_value(row, layout)
        # :meth:`_start_transition` 紧接着就拿滚动条的当前位置当终点，所以这里**必须**摆过去。
        self._scroll_to = target
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(target)
        finally:
            self._driving_scroll = False


__all__ = ["AnchoringMixin"]
