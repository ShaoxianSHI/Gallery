"""面包屑导航条：库名 ▸ 子目录 ▸ 当前目录，右边挂着已生效的筛选条件（ADR 0026）。

平时只在相册模式下显示；**有筛选条件时一律显示** —— 平铺模式下那里没有文件夹可指，
而条件总得有个地方摆。

路径段都可以点击跳回，**只有「当前所在」那一段点不动** —— 而当前所在是路径**后面**
跟着的那一段：开着分组时是组名，检索时是「搜索结果」，都不是路径段本身
（见 :meth:`BreadcrumbBar.set_path`）。右端那几枚条件胶囊**点文字是改**
（把它放回搜索框）、**点右端那个 ✕ 才是撤掉**。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from gallery.ui.panels.layout import clear_layout

#: 分隔符。
SEPARATOR = "▸"

#: 条件胶囊右边那个「撤掉它」的记号。
DISMISS_MARK = "✕"

#: 胶囊左右各留多少（点）。**热区算它**（见 :meth:`ChipButton._hits_mark`），
#: 改样式表里的内边距时这个也要跟着改 —— 两处写同一个数不如让计算从样式表那一档来。
_CHIP_PADDING_H = 8

#: 路径上每一段都是一个按钮，但按钮不画自己的底色：``background: transparent`` 让它
#: 融进这一行统一的底色里（见 ``ui.theme``），否则每个路径段都是一块系统按钮色的圆角块，
#: 在浅色主题下比周围的底色明显深一档。悬停靠下划线示意即可。
_BUTTON_STYLE = """
QToolButton {
    border: none;
    background: transparent;
    padding: 2px 6px;
    color: palette(link);
    font-size: 12px;
}
QToolButton:hover { text-decoration: underline; }
"""


def _chip_style(*, editing: bool) -> str:
    """条件胶囊的样式。

    它是一块**不是链接**的面 —— 它不是「可以去的地方」，是「正在生效的东西」，
    所以不用链接色、不用下划线。

    两种状态：

    - 平时：点它是**改**；
    - 正在改（已放进搜索框、按回车会就地替换）：边框换成高亮色、底色压深一档 ——
      用户得能看出「回车之后是改这一条，而不是再多一条」。
    """
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
    """一枚筛选条件胶囊：**点文字是改，点 ✕ 是撤掉**。

    两件事挤在一个按钮上，所以按位置分热区：``✕`` 那一段是「撤掉」，其余是「改」。
    分界用字体度量算 —— ``✕`` 紧跟在文字后面，样式表里的左右内边距也是已知的
    （:data:`_CHIP_PADDING_H`），于是「点在 ✕ 上」这件事不必靠猜。

    **键盘走「改」那一条**（``clicked`` 即改）：键盘没有「点 ✕ 上」这回事，
    而「改」是更常用、更不容易丢东西的那一个（`Esc` 与清空后回车都还留着撤掉的入口）。
    """

    #: 点在文字上（默认动作，与键盘激活同一条）。
    edited = Signal()
    #: 点在右端那个 ✕ 上。
    removed = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchChip")
        self.setText(f"{text} {DISMISS_MARK}")
        # **不挂提示**：这一行本来就写得清清楚楚（条件 + 一个 ✕），鼠标停在上面再弹
        # 一句「点一下改这一条」，读的是屏幕上已经有的东西，还要用户移开鼠标才看得完。
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.edited)

    def _hits_mark(self, x: float) -> bool:
        """这一点是不是落在右端那个 ``✕`` 上。"""
        text = self.text()
        body = text[: -len(DISMISS_MARK)].rstrip()  # 去掉标记与它前面那个空格
        metrics = QFontMetrics(self.font())
        # 文字起点 + 文字宽度 + 一个空格 ≈ 标记起点；再往左留两像素容差，
        # 省得「点准了却没反应」。
        mark_start = _CHIP_PADDING_H + metrics.horizontalAdvance(body) + 4
        return x >= mark_start - 2

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._hits_mark(event.position().x()):
            self.removed.emit()
        else:
            self.edited.emit()
        event.accept()


class BreadcrumbBar(QWidget):
    """可点击的路径条，右端挂着已生效的筛选条件。"""

    #: 用户点了某一段，要求跳到那里。
    path_requested = Signal(object)
    #: 用户撤掉了右端某一枚条件胶囊，带的是它的序号（撤掉的是「刚敲的那一条」，
    #: 而不是界面上数出来的位置 —— 两者在重排之后未必一样）。
    condition_removed = Signal(int)
    #: 用户要改某一枚条件胶囊（把它放回搜索框），带的是它的序号。
    condition_edited = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(0)

    def set_path(
        self,
        parts: Sequence[tuple[str, Path]],
        *,
        group: str | None = None,
        conditions: Sequence[str] = (),
        result_count: int | None = None,
        editing: int | None = None,
    ) -> None:
        """重建整条路径。``parts`` 由 ``ui.navigation.breadcrumb_parts`` 给出。

        ``group`` 是当前进入的分组名（见 ADR 0009）。它**不是磁盘上的目录**，因此
        只能显示、点不动 —— 放在最后一段，视觉上也正好是「当前所在的位置」。

        ``conditions`` 是已生效的筛选条件（每个一段文字），``result_count`` 是这次
        检索命中了多少张 —— 两者都在**右端**、按录入顺序从左往右排（ADR 0026 决策 4）。
        有条件时路径末尾多一段「搜索结果」：那是这一层真正的所在，而它也不是目录。

        ``editing`` 是「正在输入框里改」的那一条的序号（没有就 ``None``）—— 它要看起来
        与别的胶囊不一样，否则用户按回车会突然发现多了一条而不是「原来那条被改了」。

        **「当前所在」是路径后面跟着的那一段，不是路径自己最后一段。** 开着分组时
        当前所在是那一组（「照片 ▸ 旅行 ▸ 2024 年」里，``旅行`` 只是走到组里的路），
        检索时是「搜索结果」。于是这两种情况下路径上的每一段都是**可点的** ——
        把相册画成不可点的当前层，用户就只能按 `Esc` 退，而 `Esc` 一次只退一层。
        """
        self._clear()
        # 「当前所在」落在路径上，仅当后面既没有分组也没有搜索结果可指。
        current = None if (group or conditions) else len(parts) - 1
        for index, (label, path) in enumerate(parts):
            if index:
                self._layout.addWidget(self._separator())
            if index == current:
                self._layout.addWidget(self._current_label(label, path))
            else:
                self._layout.addWidget(self._segment_button(label, path))
        if group:
            self._layout.addWidget(self._separator())
            self._layout.addWidget(self._group_label(group))
        if conditions:
            self._layout.addWidget(self._separator())
            self._layout.addWidget(self._search_label(result_count))
        self._layout.addStretch(1)
        for position, text in enumerate(conditions):
            if position:
                self._layout.addSpacing(4)
            self._layout.addWidget(self._chip(position, text, editing == position))

    def _search_label(self, result_count: int | None) -> QLabel:
        """「搜索结果」那一段。命中数写在里面，**被截断时用户看得见**。"""
        text = "搜索结果" if result_count is None else f"搜索结果（{result_count}）"
        widget = QLabel(text)
        widget.setToolTip("整个库里检索出来的照片（不是只在某一个文件夹里）；Esc 逐条撤销条件")
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _chip(self, position: int, text: str, editing: bool) -> ChipButton:
        button = ChipButton(text, self)
        button.setStyleSheet(_chip_style(editing=editing))
        button.edited.connect(lambda index=position: self.condition_edited.emit(index))
        button.removed.connect(lambda index=position: self.condition_removed.emit(index))
        return button

    def _separator(self) -> QLabel:
        separator = QLabel(SEPARATOR)
        separator.setStyleSheet("color: palette(mid); font-size: 11px;")
        return separator

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

    def _group_label(self, label: str) -> QLabel:
        """分组名：和当前目录一样的重体，只是不可点（点它没有对应的目录可回）。"""
        widget = QLabel(label)
        widget.setToolTip("当前分组（Esc 返回上一层）")
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _clear(self) -> None:
        # 面包屑**每换一次目录就整个重摆**，所以顺序上的那个坑（先藏、再摘）在这里
        # 一样踩得到 —— 详见 panels.layout 的模块文档。
        clear_layout(self._layout)


__all__ = ["DISMISS_MARK", "SEPARATOR", "BreadcrumbBar", "ChipButton"]
