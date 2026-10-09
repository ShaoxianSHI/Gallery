"""控件拆解：把一整个布局里的旧控件一次性清干净。留在顶层是因为 ``calendar/toolbar.py``
也要它（ADR 0060）—— 顺序错了就是日历工具条上残留几个带标题栏的小白窗。
顺序有坑，必须**先 ``hide()``，再 ``setParent(None)``，最后 ``deleteLater()``**：把一个
可见控件 ``setParent(None)`` 等于把它提升成顶层窗口，Qt 会顺手显示出来 —— 用户就看到旧内容
作为小白窗（macOS 上还带标题栏）弹在面板原来的位置上。只藏不摘也不行：``deleteLater``
只是排队，事件循环转一圈之前旧控件仍是布局的隐藏孩子。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLayout, QWidget


def clear_layout(layout: QLayout) -> None:
    """清空 ``layout`` 里的条目，把取出来的控件按正确顺序拆掉。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()


def clear_widgets(widgets: list[QWidget]) -> None:
    """拆掉一组控件（不经过布局时用；顺序与 :func:`clear_layout` 完全一致）。"""
    for widget in widgets:
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()


__all__ = ["clear_layout", "clear_widgets"]
