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
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from gallery.ui.vibrancy import CORNER_RADIUS, Vibrancy

#: 分隔符。
SEPARATOR = "▸"

#: 透明底：毛玻璃生效时用它 —— 自己的底色会把毛玻璃盖住。
_TRANSPARENT_STYLE = "QWidget#breadcrumb { background: transparent; }"

#: 降级用的半透明底色：alpha 190 约 75%，照片透得出轮廓，字也还看得清。
_FROSTED_ALPHA = 190

_BUTTON_STYLE = """
QToolButton {
    border: none;
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
        self.setObjectName("breadcrumb")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 4, 10, 4)
        self._layout.setSpacing(0)

        self._vibrancy = Vibrancy(self)
        self._vibrancy.attached_changed.connect(self._on_vibrancy_changed)
        self._apply_background(not self._vibrancy.attached)

    def _on_vibrancy_changed(self, attached: bool) -> None:
        """毛玻璃是显示时才挂上的，挂上/挂不上都要换一次底色。"""
        self._apply_background(not attached)

    def _apply_background(self, fallback: bool) -> None:
        """``fallback`` 为真（毛玻璃不可用）时铺半透明底色，否则保持透明。

        透明是**默认**、半透明是**降级**：顺序反了就会出现「拿不到毛玻璃，于是字直接
        糊在照片上」—— 那比不好看严重得多，是看不清。

        底色取自当前主题的窗口色：浅色主题下配深色文字、深色主题下配浅色文字，
        不然降级之后会出现深底黑字。
        """
        if not fallback:
            self.setStyleSheet(_TRANSPARENT_STYLE)
            return
        base = self.palette().color(QPalette.ColorRole.Window)
        self.setStyleSheet(
            f"QWidget#breadcrumb {{ background: rgba({base.red()}, {base.green()},"
            f" {base.blue()}, {_FROSTED_ALPHA}); border-radius: {CORNER_RADIUS}px; }}"
        )

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
