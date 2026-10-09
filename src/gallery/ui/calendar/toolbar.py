"""日历的工具条：``‹ › 今天`` / 标题 / 年 月 周，占掉原本放面包屑的那一行（ADR 0033 决策 5）。

**这一行上的字色一律写进样式表**（被迫，不是偏好）：挂着样式表的控件拿到的是
``QStyleSheetStyle`` 自己那份调色板，``apply_theme`` 下发的到不了 —— 漏写的那一处切到浅色之后
就是看不见的白字。**这一行上还挂着生效中的筛选条件**（ADR 0040 决策 9），摆在年 / 月 / 周
**左边**：筛选时用户看不到网格页那条面包屑，条件不摆在这里就等于「加了但不知道加了什么」。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from gallery.core.library.calendar import LEVEL_NAMES, LEVELS, title
from gallery.ui.calendar.colors import CalendarColors
from gallery.ui.chips import DISMISS_MARK, build_chip
from gallery.ui.layout import clear_layout

_BAR_HEIGHT = 60


def _hex(color: object) -> str:
    return color.name()  # type: ignore[attr-type]


class CalendarBar(QWidget):
    """一行：往前 / 今天 / 往后、当前这段日子的标题、三层的分段选择器。

    **条件胶囊摆在年 / 月 / 周左边**（ADR 0040 决策 9）：这一行占掉的就是网格页那一条面包屑
    （ADR 0033 决策 5），而筛选时用户看不到网格页 —— 条件不摆在这里就等于「加了但不知道加了
    什么」。摆左边而不是右边：右边那一头就是分段选择器，胶囊按录入顺序从右往左排开会压在它上面。
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

        # 胶囊区在分段选择器**左边**，但**不忽略掉标题那一份弹性** —— 标题是「我在看哪一段」，
        # 条件是附注，所以空间不够时先让胶囊那几枚收缩（见 :meth:`set_conditions`）。
        self._chips = QWidget()
        self._chips.setObjectName("chips")
        self._chips_layout = QHBoxLayout(self._chips)
        self._chips_layout.setContentsMargins(0, 0, 8, 0)
        self._chips_layout.setSpacing(4)
        self._chips.setVisible(False)
        # 完整那一批条件与「正在改哪一条」记在这里，``_refit`` 每次按当前宽度重新决定摆几枚。
        self._all_conditions: list[str] = []
        self._editing: int | None = None
        self._overflow = 0
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

        **与面包屑共用同一份胶囊**（:func:`~gallery.ui.chips.build_chip`）—— 两处各
        派生一遍样式，过一阵就是「日历上那几枚比网格上那几枚浅一档」。宽度不够时**收掉排不下的
        那几枚**，而不是把每一枚都压成省略号 —— 见 :meth:`_refit`。
        """
        self._all_conditions = list(conditions)
        self._editing = editing
        self._refit()

    def _refit(self) -> None:
        """按当前宽度决定**摆几枚**，摆出来的与留在模型里的必须是同一批。

        **挤掉的必须是尾部那几枚，不能按比例压缩每一枚** —— 压缩到宽度以下时 ``QToolButton`` 会
        省略文字，于是长条件变成「拍摄时…1 ✕」，用户读到的是一条**不存在的条件**。完整那条留着、
        后面跟一枚「+N」才对。**条件被点掉时按索引走**，所以收起来的那几枚撤掉后索引不会错位。
        """
        clear_layout(self._chips_layout)
        shown, overflow = self._how_many_fit()
        self._overflow = len(self._all_conditions) - shown

        for position, text in enumerate(self._all_conditions[:shown]):
            self._chips_layout.addWidget(
                build_chip(
                    text,
                    position,
                    editing=self._editing == position,
                    parent=self._chips,
                    owner=self,
                )
            )
        if overflow:
            self._chips_layout.addWidget(self._overflow_chip(overflow))
        self._chips.setVisible(bool(self._all_conditions))
        self._chips.setMaximumWidth(self._available_width())
        # 水平方向不设下限：**胶囊是附注，标题才是「我在看哪一段」**，空间紧的时候先让附注让路。
        self._title.setMinimumWidth(0)

    def _available_width(self) -> int:
        """胶囊最多能占多宽 —— 剩下的留给标题与分段选择器。

        **标题优先**：它是「我在看哪一段」，条件是附注，所以空间紧的时候先让附注让路。
        留出的 320 点是「今天」按钮加前后翻页加年 / 月 / 周那一组（实测约 300），给宽一点，
        别让分段选择器被挤扁。
        """
        keep_for_the_rest = self._title.sizeHint().width() + 320
        return max(0, self.width() - keep_for_the_rest)

    def _how_many_fit(self) -> tuple[int, int]:
        """返回「完整摆得下几枚」与「后面还剩几条」。

        **至少摆一枚**：一行只有一条条件时它必须完整可读 —— 挤成省略号等于没显示。
        摆不下第一条就把整块收起来（``+N`` 那枚也放不下，见 :meth:`_overflow_chip`）。
        """
        available = self._available_width()
        used = 0
        shown = 0
        for text in self._all_conditions:
            needed = QFontMetrics(self.font()).horizontalAdvance(f"{text} {DISMISS_MARK}") + 26
            if shown and used + needed > available:
                break
            used += needed
            shown += 1
        return shown, len(self._all_conditions) - shown

    def _overflow_chip(self, count: int) -> QLabel:
        """「+N」那一枚 —— 说明还有几条没显示，而不是假装条件只有那么几条。

        **按整块能不能摆下它决定收不收**：标题那条本来就可能被压到 0 宽，这时再塞一枚
        「+N」只会在工具条上多出一块空白。它自己**不是按钮** —— 摆的是数量，不是能点的东西，
        给它指针光标会让用户以为点得动。
        """
        label = QLabel(f"+{count}")
        label.setObjectName("overflow")
        label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        # 窗口一变宽就该多摆几枚 —— 不重排的话拖宽窗口仍只有那几枚，与空间对不上。
        self._refit()

    def set_title(self, level: str, anchor: object) -> None:  # type: ignore[no-untyped-def]
        self._title.setText(title(level, anchor))  # type: ignore[arg-type]

    def set_colors(self, colors: CalendarColors) -> None:
        """按主题重铺一次样式表。

        选中态用**强调色压淡的那一层**，与日历里「选中的一天」同一份颜色。**标题字号是系统默认
        那一档**：那条 22px 是按钮时代的遗留（标题当初是 ``QPushButton``），看着像顺手对上，
        实际是放大一号；**每一处字色都在样式表里硬编码**，原因见模块文档。
        """
        accent = colors.accent
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: none; border-radius: 6px; padding: 0 14px; color: {_hex(colors.text)};
            }}
            QPushButton#nav {{ padding: 0; font-size: 22px; }}
            /* 标题是个 QLabel：**只补颜色、不补字号** —— 字号是另一件事，别混在这一条里。 */
            QLabel#title {{ color: {_hex(colors.text)}; }}
            /* 「+N」那一枚：字色与胶囊一样，所以每个字符都要显式写 ——
               ``QStyleSheetStyle`` 给这一支塞了它自己那份调色板（模块文档），而
               ``faint`` 就是胶囊那档「次要信息」的色。 */
            QLabel#overflow {{ color: {_hex(colors.faint)}; font-size: 11px; }}
            QPushButton:hover {{ background: {_hex(colors.chip)}; }}
            QPushButton:checked {{
                background: rgba({accent.red()}, {accent.green()}, {accent.blue()}, 38);
                color: {_hex(accent)}; font-weight: 600;
            }}
            """
        )


__all__ = ["CalendarBar"]
