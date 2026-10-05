"""日历的工具条：``‹ › 今天`` / 标题 / 年 月 周，占掉原本放面包屑的那一行（ADR 0033 决策 5）。

**这一行上的字色一律写进样式表**（被迫，不是偏好）：挂着样式表的控件拿到的是
``QStyleSheetStyle`` 自己那份调色板，``apply_theme`` 下发的到不了 —— 漏写的那一处切到浅色
之后就是看不见的白字。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from gallery.core.library.calendar import LEVEL_NAMES, LEVELS, title
from gallery.ui.calendar.colors import CalendarColors

_BAR_HEIGHT = 60


def _hex(color: object) -> str:
    return color.name()  # type: ignore[attr-type]


class CalendarBar(QWidget):
    """一行：往前 / 今天 / 往后、当前这段日子的标题、三层的分段选择器。"""

    #: 用户点了某一段（``-1`` 往前一页，``1`` 往后一页）。
    step_requested = Signal(int)
    today_requested = Signal()
    level_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_BAR_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        for label, step in (("‹", -1), ("›", 1)):
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setFixedSize(38, 38)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, s=step: self.step_requested.emit(s))
            layout.addWidget(button)
        today = QPushButton("今天")
        today.setObjectName("today")
        today.setFixedHeight(38)
        today.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        today.clicked.connect(self.today_requested)
        layout.addWidget(today)
        layout.addSpacing(10)

        self._title = QLabel()
        self._title.setObjectName("title")
        self._title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self._title)
        layout.addStretch(1)

        self._group = QButtonGroup(self)
        for level in LEVELS:
            button = QPushButton(LEVEL_NAMES[level])
            button.setCheckable(True)
            button.setFixedSize(50, 36)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, lv=level: self.level_changed.emit(lv))
            self._group.addButton(button)
            layout.addWidget(button)
        self._group.buttons()[0].setChecked(True)

    def set_level(self, level: str) -> None:
        for button in self._group.buttons():
            button.setChecked(button.text() == LEVEL_NAMES[level])

    def set_title(self, level: str, anchor: object) -> None:  # type: ignore[no-untyped-def]
        self._title.setText(title(level, anchor))  # type: ignore[arg-type]

    def set_colors(self, colors: CalendarColors) -> None:
        """按主题重铺一次样式表。

        选中态用**强调色压淡的那一层**，与日历里「选中的一天」同一份颜色。**标题字号是系统默认
        那一档**：那条 22px 是按钮时代的遗留（标题当初是 ``QPushButton``），看着像顺手对上，
        实际是放大一号；**每一处字色都在样式表里写死**，原因见模块文档。
        """
        accent = colors.accent
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: none; border-radius: 6px; padding: 0 14px; color: {_hex(colors.text)};
            }}
            QPushButton#nav {{ padding: 0; font-size: 22px; }}
            /* 标题是个 QLabel：原先写 ``QPushButton#title`` 从来没匹配上过，所以一直是默认
                那一档字号。**这里只补颜色、不补字号**：22px 是按钮时代的遗留且一直没生效，
                「顺手对上」反而把字号放大了 —— 标题字号是另一件事，别混在这一条里。 */
            QLabel#title {{ color: {_hex(colors.text)}; }}
            QPushButton:hover {{ background: {_hex(colors.chip)}; }}
            QPushButton:checked {{
                background: rgba({accent.red()}, {accent.green()}, {accent.blue()}, 38);
                color: {_hex(accent)}; font-weight: 600;
            }}
            """
        )


__all__ = ["CalendarBar"]
