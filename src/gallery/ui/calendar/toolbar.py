"""日历的工具条：``‹ › 今天`` / 标题 / 年 月 周，占掉原本放面包屑的那一行（ADR 0033 决策 5）。

**这一行上的字色一律写进样式表**（被迫，不是偏好）：挂着样式表的控件拿到的是
``QStyleSheetStyle`` 自己那份调色板，``apply_theme`` 下发的到不了 —— 漏写的那一处切到浅色
之后就是看不见的白字。

**这一行上还挂着生效中的筛选条件**（ADR 0040 决策 9），摆在年 / 月 / 周**左边**：这一行占掉的
就是网格页那条面包屑，而筛选时用户看不到网格页 —— 条件不摆在这里就等于「加了但不知道加了
什么」。摆左边而不是右边：胶囊按录入顺序从右往左排开的话会压在那三个按钮上。
**胶囊与面包屑共用同一份**（:func:`~gallery.ui.panels.breadcrumb.build_chip`），两边接到同一个
处理函数上。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from gallery.core.library.calendar import LEVEL_NAMES, LEVELS, title
from gallery.ui.calendar.colors import CalendarColors
from gallery.ui.panels.breadcrumb import build_chip
from gallery.ui.panels.layout import clear_layout

_BAR_HEIGHT = 60


def _hex(color: object) -> str:
    return color.name()  # type: ignore[attr-type]


class CalendarBar(QWidget):
    """一行：往前 / 今天 / 往后、当前这段日子的标题、三层的分段选择器。

    **条件胶囊摆在年 / 月 / 周左边**（ADR 0040 决策 9）：这一行占掉的就是网格页那一条面包屑
    （ADR 0033 决策 5），而筛选时用户看不到网格页 —— 条件不摆在这里就等于「加了但不知道加了
    什么」。摆左边而不是右边：右边那一头就是分段选择器，胶囊按录入顺序从右往左排开的话会压
    在它上面（用户原话：「和相册视图一致的话会和这几个按钮重叠」）。
    """

    #: 用户点了某一段（``-1`` 往前一页，``1`` 往后一页）。
    step_requested = Signal(int)
    today_requested = Signal()
    level_changed = Signal(str)
    #: 用户点胶囊上的文字（改）/ 右端那个 ✕（撤掉），带的是条件在列表里的序号。
    condition_edited = Signal(int)
    condition_removed = Signal(int)

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

        # 胶囊区在分段选择器**左边**，但**不吃掉标题那一份弹性** —— 标题是「我在看哪一段」，
        # 条件是附注，所以空间不够时先让胶囊那几枚收缩（见 :meth:`set_conditions`）。
        self._chips = QWidget()
        self._chips.setObjectName("chips")
        self._chips_layout = QHBoxLayout(self._chips)
        self._chips_layout.setContentsMargins(0, 0, 8, 0)
        self._chips_layout.setSpacing(4)
        self._chips.setVisible(False)
        layout.addWidget(self._chips)

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

    def set_conditions(self, conditions: Sequence[str], *, editing: int | None = None) -> None:
        """摆出生效中的条件（``conditions`` 空 = 整块收起来）。

        **与面包屑共用同一份胶囊**（:func:`~gallery.ui.panels.breadcrumb.build_chip`）——
        两处各派生一遍样式，过一阵就是「日历上那几枚比网格上那几枚浅一档」。

        宽度不够时**先缩胶囊、后压标题**：标题是「我在看哪一段」，条件是附注，所以空间紧的时候
        先让附注让路。
        """
        clear_layout(self._chips_layout)
        self._chips.setVisible(bool(conditions))
        for position, text in enumerate(conditions):
            self._chips_layout.addWidget(
                build_chip(
                    text, position, editing=editing == position, parent=self._chips, owner=self
                )
            )
        # 水平方向给一个**上限**，而不是让这一块去争弹性：``Stretch`` 会让标题与胶囊抢宽度
        # （标题长的时候把胶囊压成一条缝），而 ``Ignored`` 更糟 —— 它**丢掉尺寸提示**，布局于是
        # 给 0 宽，整块连同里面的胶囊一起消失。附注要的是「最多占这么多、别再抢」。
        #
        # **上限在 :meth:`resizeEvent` 里算，不在这里** —— ``set_conditions`` 常发生在布局跑过
        # 之前，那时 ``self.width()`` 还是 0，算出来是个 0 宽的上限，胶囊当场消失（实测）。
        self._cap_chips_width()
        self._title.setMinimumWidth(0)

    def _cap_chips_width(self) -> None:
        """胶囊最多能占多宽 —— 剩下的留给标题与分段选择器。

        **标题优先**：它是「我在看哪一段」，条件是附注，所以空间紧的时候先让附注让路。
        留出的 320 点是「今天」按钮加前后翻页加年 / 月 / 周那一组（实测约 300），给宽一点，
        别让分段选择器被挤扁。
        """
        keep_for_the_rest = self._title.sizeHint().width() + 320
        available = max(0, self.width() - keep_for_the_rest)
        self._chips.setMaximumWidth(available)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._cap_chips_width()

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
