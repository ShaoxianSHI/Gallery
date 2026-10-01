"""面包屑导航条：库名 ▸ 子目录 ▸ 当前目录。

只在相册模式下显示。除最后一段（当前所在层）外都可以点击跳回。

它是**浮在网格之上**的一层，不是压在照片上面的一行标题：底下是照片，控件只占自己
那么大一块，宽度永远贴着内容（末尾那个 stretch 是为了让长路径从左侧排开，不是为了
把整行撑满）。背景走系统毛玻璃；拿不到毛玻璃时退成半透明底色（见 ``ui.vibrancy``）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from gallery.ui.vibrancy import FrostedPanel

#: 分隔符。
SEPARATOR = "▸"

_BUTTON_STYLE = """
QToolButton {
    border: none;
    padding: 2px 6px;
    color: palette(link);
    font-size: 12px;
}
QToolButton:hover { text-decoration: underline; }
"""


class BreadcrumbBar(FrostedPanel):
    """可点击的路径条。"""

    #: 用户点了某一段，要求跳到那里。
    path_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, name="breadcrumb")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 4, 10, 4)
        self._layout.setSpacing(0)

    def set_path(self, parts: Sequence[tuple[str, Path]]) -> None:
        """重建整条路径。``parts`` 由 ``ui.navigation.breadcrumb_parts`` 给出。"""
        self._clear()
        for index, (label, path) in enumerate(parts):
            if index:
                separator = QLabel(SEPARATOR)
                separator.setStyleSheet("color: palette(mid); font-size: 11px;")
                self._layout.addWidget(separator)
            if index == len(parts) - 1:
                self._layout.addWidget(self._current_label(label, path))
            else:
                self._layout.addWidget(self._segment_button(label, path))
        self._layout.addStretch(1)

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

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 同 metadata_panel：先摘出控件树，否则旧的一段会残留在原位置。
                widget.setParent(None)
                widget.deleteLater()


__all__ = ["SEPARATOR", "BreadcrumbBar"]
