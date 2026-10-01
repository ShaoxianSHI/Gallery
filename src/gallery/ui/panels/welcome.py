"""空状态页：还没打开任何库时看到的东西。

它是应用的第一屏，所以除了一个「打开文件夹」按钮之外，还要把两件事说清楚：
照片不会被移动或修改，以及首次打开会询问缓存目录放在哪里。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

_STYLE = """
#welcomeTitle { font-size: 30px; font-weight: 600; }
#welcomeSubtitle { font-size: 14px; color: palette(mid); }
#welcomeHint { font-size: 12px; color: palette(mid); }
#recentTitle { font-size: 12px; color: palette(mid); }
QPushButton#openButton { font-size: 15px; padding: 8px 22px; }
QPushButton#recentButton {
    font-size: 13px;
    text-align: left;
    padding: 6px 12px;
    border: none;
    color: palette(link);
}
QPushButton#recentButton:hover { text-decoration: underline; }
"""

#: 最多列出几个最近打开的库。
MAX_RECENT_SHOWN = 5


class WelcomeView(QWidget):
    """引导用户选择第一个（或下一个）库。"""

    #: 用户点了「打开文件夹…」。
    open_requested = Signal()
    #: 用户点了某个最近打开的库。
    recent_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(10)
        layout.addStretch(1)

        title = QLabel("Gallery")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("照片留在你自己的文件夹里，Gallery 只是它的一个视图。")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(18)
        self._open_button = QPushButton("打开文件夹…")
        self._open_button.setObjectName("openButton")
        self._open_button.setDefault(True)
        self._open_button.clicked.connect(self.open_requested.emit)
        layout.addWidget(self._open_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(6)
        hint = QLabel(
            "不会移动或修改你的照片（改文件名只有你自己动手时才会发生）。首次打开时会先询问缓存目录放在哪里。"
        )
        hint.setObjectName("welcomeHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        layout.addSpacing(26)
        self._recent_title = QLabel("最近打开")
        self._recent_title.setObjectName("recentTitle")
        self._recent_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._recent_title)

        self._recent_layout = QVBoxLayout()
        self._recent_layout.setSpacing(2)
        layout.addLayout(self._recent_layout)
        layout.addStretch(2)

        self.set_recent(())

    def set_recent(self, roots: Sequence[Path]) -> None:
        """刷新最近打开的库列表。"""
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        shown = list(roots)[:MAX_RECENT_SHOWN]
        self._recent_title.setVisible(bool(shown))
        for root in shown:
            button = QPushButton(f"{root.name} — {root}")
            button.setObjectName("recentButton")
            button.setToolTip(str(root))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, path=root: self.recent_requested.emit(path)
            )
            self._recent_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)


__all__ = ["MAX_RECENT_SHOWN", "WelcomeView"]
