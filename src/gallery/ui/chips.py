"""筛选条件胶囊 —— **网格页的面包屑与日历的工具条摆的是同一批**（ADR 0040 决策 9）。

**留在顶层是因为两个包都要它**：网格页那一行右端挂它，日历的工具条左端也挂同一批条件
（筛选时用户看不到网格页，条件不摆在那里就等于「加了但不知道加了什么」）。两处各派生一遍
样式的话，过一阵就是「日历上那几枚比网格上那几枚浅一档」—— 所以
:func:`build_chip` 是**唯一**的出口，样式与热区也只有一份。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontMetrics, QMouseEvent
from PySide6.QtWidgets import QToolButton, QWidget

DISMISS_MARK = "✕"

#: 胶囊左右各留多少（点）。热区算它（见 :meth:`ChipButton._hits_mark`），改样式表时同步。
_CHIP_PADDING_H = 8


def chip_style(*, editing: bool) -> str:
    """条件胶囊的样式：它不是链接、是正在生效的东西，所以不用链接色。"""
    border = "palette(highlight)" if editing else "palette(mid)"
    hover = "palette(alternate-base)" if not editing else ""
    return f"""
    QToolButton#searchChip {{
        border: 1px solid {border};
        border-radius: 9px;
        background: transparent;
        padding: 1px {_CHIP_PADDING_H}px;
        color: palette(text);
        font-size: 11px;
    }}
    QToolButton#searchChip:hover {{ background: {hover}; }}
    """


class ChipButton(QToolButton):
    """一枚筛选条件胶囊：点文字是改，点 ✕ 是撤掉。

    两件事挤在一个按钮上，所以按位置分热区：``✕`` 那一段是撤掉，其余是改（键盘走「改」，
    因为键盘没有「点 ✕」这回事）。
    """

    edited = Signal()
    removed = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchChip")
        self.setText(f"{text} {DISMISS_MARK}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.edited)

    def _hits_mark(self, x: float) -> bool:
        """这一点是不是落在右端那个 ``✕`` 上。"""
        text = self.text()
        body = text[: -len(DISMISS_MARK)].rstrip()  # 去掉标记与它前面那个空格
        metrics = QFontMetrics(self.font())
        # 文字起点 + 文字宽 + 一个空格 ≈ 标记起点；再往左留两像素容差。
        mark_start = _CHIP_PADDING_H + metrics.horizontalAdvance(body) + 4
        return x >= mark_start - 2

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._hits_mark(event.position().x()):
            self.removed.emit()
        else:
            self.edited.emit()
        event.accept()


def build_chip(
    text: str, position: int, *, editing: bool, parent: QWidget, owner: QObject
) -> ChipButton:
    """造一枚条件胶囊，并把它的两个信号接到 ``owner`` 的 ``condition_edited`` /
    ``condition_removed`` 上（带的是它在条件列表里的序号）。
    """
    button = ChipButton(text, parent)
    button.setStyleSheet(chip_style(editing=editing))
    button.edited.connect(lambda index=position: owner.condition_edited.emit(index))
    button.removed.connect(lambda index=position: owner.condition_removed.emit(index))
    return button


__all__ = ["DISMISS_MARK", "ChipButton", "build_chip", "chip_style"]
