"""面板共用的控件拆解：把一整个布局里的旧控件一次性清干净。

三处都要做同一件事（面包屑每换一次目录就重摆、元数据面板每读出一份报告就重画、
欢迎页每次刷新最近列表就重摆），而这件事有一个**顺序上的坑**，值得只写一次：

    先 ``hide()``，再 ``setParent(None)``，最后 ``deleteLater()``。

把一个**可见**控件 ``setParent(None)`` 等于把它提升成顶层窗口，而 Qt 会顺手把
顶层窗口**显示出来**。于是顺序写反时，用户会看到面板原本的位置上弹出几个白色小窗
（macOS 上还带标题栏），里面是上一份内容 —— 正是「在照片的位置闪一下的那个小白窗」。
藏过之后再摘成顶层控件，Qt 就没有东西可显示，那个小窗也就不会存在。

只藏不摘同样不够：``deleteLater`` 只是排队，事件循环真正转一圈之前，旧控件一直是
布局的隐藏孩子（``processEvents`` 不处理延迟删除），它会混在控件树里被下一次渲染与
查询算上。
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
