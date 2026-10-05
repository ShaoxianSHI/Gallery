"""日历的工具条：``‹ › 今天`` / 标题 / 年 月 周。

这一行**占掉原本放面包屑的那一行**（ADR 0033 决策 5）：两者不并存 ——
日历时这一行讲的是「现在看的是哪一段日子」，再叠一条目录路径既挤又答非所问。
反过来网格那一行仍然是面包屑（那时这一行归它）。

配色同样取自 :class:`~gallery.ui.calendar.colors.CalendarColors`：样式表要的是
字符串，于是这里把那一套 ``QColor`` 翻译成 ``#rrggbb`` 再拼进去 —— 颜色仍然只有
一个来源（``ui.theme``），不另写一份 Google 式的色值。

**这一行上的字色一律写进样式表，没有一处靠下发的调色板。** 不是偏好，是被迫：
本控件带着样式表，于是 ``QStyleSheetStyle`` 给「工具条连同它的子控件」这一支塞了一份
**自己的**调色板，此后 :func:`gallery.ui.theme.apply_theme` 下发的调色板就到不了它们
（同一个道理见 ``ui.theme`` 模块文档第 2 条）。而 :meth:`CalendarPage._on_theme_changed`
是在 ``apply_theme`` **之后**才调到这里来的 —— 于是每次换外观，它都在替这一支
**照上一次那份调色板**重画一次：切到浅色时别处都白了，只有这一行还留着深色那一档的
白字（症状：那一行标题整行看不见，而同一行上的按钮都好好的 —— 按钮的字色写在
样式表里，标题原来没写）。

**标题用系统默认那一档字号，样式表不许碰它。** 那条 ``font-size: 22px`` 是标题
还是按钮时代的遗留，而它写的选择器又一直是错的（``QPushButton#title``，而标题是
``QLabel``）—— 换句话说「22px 的标题」从来没存在过，标题一直就是默认那一档。修颜色
的时候把它一并对上，看着是顺手，实际上是把字号放大了一号（用户原话「字体变大了，
调回原本的字号」）。**字号是产品选择**：真要放大标题，改这一处，并同步
``tests/ui/calendar/test_toolbar.py`` 里那一条 —— 别让它以「修 bug」的名义溜进来。

钉住字色的是 ``tests/ui/calendar/test_toolbar.py`` 里的
``test_dark_to_light_keeps_the_title_readable`` 与它那一条反向的用例。
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
    #: 「今天」。
    today_requested = Signal()
    #: 换层。
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

        分段选择器选中态用**强调色压淡的那一层**：与日历里「选中的一天」同一份颜色，
        于是「这一层被选中了」与「那一天被选中了」看起来是一回事。

        **这一行上的每一处字色都在样式表里写死，没有一处靠下发的调色板** ——
        原因见模块文档：``setStyleSheet`` 会让 ``QStyleSheetStyle`` 给这一支（工具条
        连同里面的子控件）塞一份「自己的」调色板，此后 :func:`gallery.ui.theme.
        apply_theme` 下发的调色板到不了它们。而 ``CalendarPage._on_theme_changed``
        恰恰是在 ``apply_theme`` **之后**才调到这里来的，于是下一次 ``setStyleSheet``
        会照着这一支**上一次**那份调色板重算 —— 症状是「切到浅色，别处都白了，
        只有这一行还留着深色那一档的字」，浅色底上白字等于看不见。
        """
        accent = colors.accent
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: none; border-radius: 6px; padding: 0 14px; color: {_hex(colors.text)};
            }}
            QPushButton#nav {{ padding: 0; font-size: 22px; }}
            /* 标题是个 QLabel：原先这条写的是 ``QPushButton#title``，**从来没匹配上过**，
               所以它一直是系统默认那一档字号，也一直没拿到颜色 —— 而颜色才是那个
               bug。**这里只补颜色，不补字号**：那条 22px 是按钮时代的遗留（标题
               当初是个 QPushButton），它一直没生效过，标题就一直是默认那一档；
               一并「顺手对上」反而把字号放大了一号（用户原话「字体变大了，
               调回原本的字号」）。要改标题字号是另一件事，别混在这一条里。 */
            QLabel#title {{ color: {_hex(colors.text)}; }}
            QPushButton:hover {{ background: {_hex(colors.chip)}; }}
            QPushButton:checked {{
                background: rgba({accent.red()}, {accent.green()}, {accent.blue()}, 38);
                color: {_hex(accent)}; font-weight: 600;
            }}
            """
        )


__all__ = ["CalendarBar"]
