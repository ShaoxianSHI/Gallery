"""空状态页：还没打开任何库时看到的东西 —— 四个页面之一（``PageRole.WELCOME``）。

除「打开文件夹」之外还要把「照片不会被移动或修改」说清楚。次要文字的颜色必须由主题派生，
不能写 ``palette(mid)``：那是 Qt 的填充色角色，深色下对比度只有 3.6:1，达不到 WCAG AA。
ADR 0060 把它从 `panels/` 搬到这里：它是一个**页面**，不是「围绕内容的辅助面板」。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from gallery.core import paths
from gallery.ui import theme
from gallery.ui.layout import clear_layout
from gallery.ui.theme import AppTheme, rgba

MAX_RECENT_SHOWN = 5

#: 次要文字的透明度。0.82 是浅色下仍能守住 4.5:1 的下限，别为了层次感再往下调。
SUBTITLE_ALPHA = 0.88
SECONDARY_ALPHA = 0.82


def welcome_style(app_theme: AppTheme) -> str:
    """欢迎页的文字样式。底色不在这里 —— 它由窗口统一铺好。

    文字色全部从主题派生，深浅两套底色自动跟上。
    """
    return f"""
#welcomeTitle {{ font-size: 30px; font-weight: 600; }}
#welcomeSubtitle {{ font-size: 14px; color: {rgba(app_theme.text_at(SUBTITLE_ALPHA))}; }}
#welcomeHint {{ font-size: 12px; color: {rgba(app_theme.text_at(SECONDARY_ALPHA))}; }}
#recentTitle {{ font-size: 12px; color: {rgba(app_theme.text_at(SECONDARY_ALPHA))}; }}
QPushButton#openButton {{ font-size: 15px; padding: 8px 22px; }}
QPushButton#recentButton {{
    font-size: 13px;
    text-align: left;
    padding: 6px 12px;
    border: none;
    color: palette(link);
}}
QPushButton#recentButton:hover {{ text-decoration: underline; }}
"""


class WelcomeView(QWidget):
    """引导用户选择第一个（或下一个）库。"""

    open_requested = Signal()
    recent_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 字色在样式表里，就得跟着主题重来：样式表只在构造时设一次。
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(10)
        layout.addStretch(1)

        title = QLabel("Gallery")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("照片保存在你自己的文件夹中，Gallery 只提供浏览。")
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
            "Gallery 不会自行移动、修改或删除照片；重命名与移入废纸篓只在你亲自操作时执行。"
            f"首次打开会在库内创建 {paths.CONFIG_DIR_NAME} 目录存放运行时数据。"
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
        clear_layout(self._recent_layout)

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

    def _apply_theme(self) -> None:
        """按当前主题重画文字。底色由窗口统一铺，这里只管字色。"""
        self.setStyleSheet(welcome_style(theme.current_theme()))


__all__ = [
    "MAX_RECENT_SHOWN",
    "SECONDARY_ALPHA",
    "SUBTITLE_ALPHA",
    "WelcomeView",
    "welcome_style",
]
