"""空状态页：还没打开任何库时看到的东西。

它是应用的第一屏，所以除了一个「打开文件夹」按钮之外，还要把两件事说清楚：
照片不会被移动或修改，以及首次打开会询问缓存目录放在哪里。

**次要文字的颜色必须由主题派生**，不能写 ``palette(mid)``：那是 Qt 的「填充色」
角色（给未选中的行、表格隔行铺底用的），拿它当字色，深色下在 #171717 的底上只有
3.6:1 的对比度，达不到 WCAG AA 对正文的 4.5:1 —— 也就是欢迎页那两行字看着发灰的原因。
透明度档位见 :func:`welcome_style`。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 最多列出几个最近打开的库。
MAX_RECENT_SHOWN = 5

#: 次要文字的透明度。0.82 是**浅色下**仍能守住 4.5:1 的下限 ——
#: 黑字压白底的对比度掉得比白字压黑底快得多，同一个值在深色下反而有 7.8:1。
#: 别为了「层次感」再往下调，元数据面板那档 0.5 在浅色下只有 2:1。
SUBTITLE_ALPHA = 0.88
SECONDARY_ALPHA = 0.82


def welcome_style(theme: AppTheme) -> str:
    """欢迎页的文字样式。底色不在这里 —— 它由窗口统一铺好。

    文字色全部从主题派生：深色下是「浅字压深底」，浅色下自动变成「深字压浅底」，
    不必再维护两套写死的字色（与 ``panels.metadata`` 同一套办法）。
    """
    return f"""
#welcomeTitle {{ font-size: 30px; font-weight: 600; }}
#welcomeSubtitle {{ font-size: 14px; color: {rgba(theme.text_at(SUBTITLE_ALPHA))}; }}
#welcomeHint {{ font-size: 12px; color: {rgba(theme.text_at(SECONDARY_ALPHA))}; }}
#recentTitle {{ font-size: 12px; color: {rgba(theme.text_at(SECONDARY_ALPHA))}; }}
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

    #: 用户点了「打开文件夹…」。
    open_requested = Signal()
    #: 用户点了某个最近打开的库。
    recent_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 字色在样式表里，就得跟着主题重来：样式表只在构造时设一次，
        # 系统换浅色 / 深色时不会自己重算（见 panels.metadata 的同一处）。
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
            "不会移动或修改你的照片（改文件名、移入废纸篓，都只有你自己动手时才会发生）。"
            "首次打开时会先询问缓存目录放在哪里。"
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

    # ---- 主题 --------------------------------------------------------------

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
