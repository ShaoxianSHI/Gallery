"""面包屑导航条：库名 ▸ 子目录 ▸ 当前目录，右边挂着已生效的筛选条件（ADR 0026）。

平时只在相册模式下显示；**有筛选条件时一律显示** —— 平铺模式下那里没有文件夹可指，
而条件总得有个地方摆。

路径段除最后一段（当前所在层）外都可以点击跳回；右端那几枚条件胶囊点一下就撤掉那一条。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from gallery.ui.panels.layout import clear_layout

#: 分隔符。
SEPARATOR = "▸"

#: 条件胶囊右边那个「删掉它」的记号。
DISMISS_MARK = "✕"

#: 路径上每一段都是一个按钮，但按钮不画自己的底色：``background: transparent`` 让它
#: 融进这一行统一的底色里（见 ``ui.theme``），否则每个路径段都是一块系统按钮色的圆角块，
#: 在浅色主题下比周围的底色明显深一档。悬停靠下划线示意即可。
_BUTTON_STYLE = """
QToolButton {
    border: none;
    background: transparent;
    padding: 2px 6px;
    color: palette(link);
    font-size: 12px;
}
QToolButton:hover { text-decoration: underline; }
"""

#: 条件胶囊：一块**不是链接**的面 —— 它不是「可以去的地方」，是「正在生效的东西」，
#: 所以不用链接色、不用下划线，点它做的是「撤掉」。
_CHIP_STYLE = """
QToolButton#searchChip {
    border: 1px solid palette(mid);
    border-radius: 9px;
    background: transparent;
    padding: 1px 8px;
    color: palette(text);
    font-size: 11px;
}
QToolButton#searchChip:hover { background: palette(alternate-base); }
"""


class BreadcrumbBar(QWidget):
    """可点击的路径条，右端挂着已生效的筛选条件。"""

    #: 用户点了某一段，要求跳到那里。
    path_requested = Signal(object)
    #: 用户点掉了右端某一枚条件胶囊，带的是它的序号（撤掉的是「刚敲的那一条」，
    #: 而不是界面上数出来的位置 —— 两者在重排之后未必一样）。
    condition_removed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(0)

    def set_path(
        self,
        parts: Sequence[tuple[str, Path]],
        *,
        group: str | None = None,
        conditions: Sequence[str] = (),
        result_count: int | None = None,
    ) -> None:
        """重建整条路径。``parts`` 由 ``ui.navigation.breadcrumb_parts`` 给出。

        ``group`` 是当前进入的分组名（见 ADR 0009）。它**不是磁盘上的目录**，因此
        只能显示、点不动 —— 放在最后一段，视觉上也正好是「当前所在的位置」。

        ``conditions`` 是已生效的筛选条件（每个一段文字），``result_count`` 是这次
        检索命中了多少张 —— 两者都在**右端**、按录入顺序从左往右排（ADR 0026 决策 4）。
        有条件时路径末尾多一段「搜索结果」：那是这一层真正的所在，而它也不是目录。
        """
        self._clear()
        for index, (label, path) in enumerate(parts):
            if index:
                self._layout.addWidget(self._separator())
            if index == len(parts) - 1:
                self._layout.addWidget(self._current_label(label, path))
            else:
                self._layout.addWidget(self._segment_button(label, path))
        if group:
            self._layout.addWidget(self._separator())
            self._layout.addWidget(self._group_label(group))
        if conditions:
            self._layout.addWidget(self._separator())
            self._layout.addWidget(self._search_label(result_count))
        self._layout.addStretch(1)
        for position, text in enumerate(conditions):
            if position:
                self._layout.addSpacing(4)
            self._layout.addWidget(self._chip(position, text))

    def _search_label(self, result_count: int | None) -> QLabel:
        """「搜索结果」那一段。命中数写在里面，**被截断时用户看得见**。"""
        text = "搜索结果" if result_count is None else f"搜索结果（{result_count}）"
        widget = QLabel(text)
        widget.setToolTip("正在看检索出来的照片（Esc 逐条撤销条件）")
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _chip(self, position: int, text: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("searchChip")
        button.setText(f"{text} {DISMISS_MARK}")
        button.setToolTip(f"撤销这一条筛选条件：{text}")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_CHIP_STYLE)
        button.clicked.connect(
            lambda _checked=False, index=position: self.condition_removed.emit(index)
        )
        return button

    def _separator(self) -> QLabel:
        separator = QLabel(SEPARATOR)
        separator.setStyleSheet("color: palette(mid); font-size: 11px;")
        return separator

    def _segment_button(self, label: str, path: Path) -> QToolButton:
        button = QToolButton()
        button.setText(label)
        button.setToolTip(str(path))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_BUTTON_STYLE)
        button.clicked.connect(lambda _checked=False, target=path: self.path_requested.emit(target))
        return button

    def _current_label(self, label: str, path: Path) -> QLabel:
        widget = QLabel(label)
        widget.setToolTip(str(path))
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _group_label(self, label: str) -> QLabel:
        """分组名：和当前目录一样的重体，只是不可点（点它没有对应的目录可回）。"""
        widget = QLabel(label)
        widget.setToolTip("当前分组（Esc 返回上一层）")
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _clear(self) -> None:
        # 面包屑**每换一次目录就整个重摆**，所以顺序上的那个坑（先藏、再摘）在这里
        # 一样踩得到 —— 详见 panels.layout 的模块文档。
        clear_layout(self._layout)


__all__ = ["DISMISS_MARK", "SEPARATOR", "BreadcrumbBar"]
