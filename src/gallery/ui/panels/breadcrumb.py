"""面包屑导航条：库名 ▸ 子目录 ▸ 当前目录。

只在相册模式下显示。除最后一段（当前所在层）外都可以点击跳回。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

#: 分隔符。
SEPARATOR = "▸"

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


class BreadcrumbBar(QWidget):
    """可点击的路径条。"""

    #: 用户点了某一段，要求跳到那里。
    path_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(0)

    def set_path(self, parts: Sequence[tuple[str, Path]], *, group: str | None = None) -> None:
        """重建整条路径。``parts`` 由 ``ui.navigation.breadcrumb_parts`` 给出。

        ``group`` 是当前进入的分组名（见 ADR 0009）。它**不是磁盘上的目录**，因此
        只能显示、点不动 —— 放在最后一段，视觉上也正好是「当前所在的位置」。
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
        self._layout.addStretch(1)

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
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 同 metadata_panel：先摘出控件树，否则旧的一段会残留在原位置。
                widget.setParent(None)
                widget.deleteLater()


__all__ = ["SEPARATOR", "BreadcrumbBar"]
