"""面包屑导航条：库名 ▸ 子目录 ▸ 当前目录，右端挂着生效中的筛选条件（ADR 0026）。

「当前所在」是路径**后面**跟着的那一段，它之前的每一段都可点击跳回（见 :meth:`set_path`）。
**结果那一段有两种说法**（ADR 0040 决策 4）：整库检索报「搜索结果（128）」而只报库名，
筛选报「筛选后（12）」而**照旧报完整路径** —— 范围没变时藏路径是在说一件不存在的事。
**胶囊不住在这里**：网格页与日历页摆的是同一批，:mod:`gallery.ui.chips` 才是那一份（ADR 0060）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from gallery.core.library.config import SEARCH_SCOPE_LEVEL, SEARCH_SCOPE_LIBRARY
from gallery.ui import theme
from gallery.ui.chips import build_chip
from gallery.ui.layout import clear_layout
from gallery.ui.theme import rgba

SEPARATOR = "▸"

#: 分隔符的字色取正文色的这一档不透明度。不能写 ``palette(mid)``：深色下那是 ``#585858``，
#: 一枚 11 点的 ``▸`` 抗锯齿一展开就看不见了；0.55 按非文字内容的 3:1 那条线挑。
SEPARATOR_ALPHA = 0.55

#: 这一行四边的留白；右边那一格是默认值，有条件胶囊时由 ``set_right_inset`` 换算。
_PAD = 2

_SEPARATOR_OBJECT_NAME = "separator"

#: 路径段按钮不画自己的底色，否则每一段都是一块比周围深一档的系统按钮色圆角块。
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


class BreadcrumbBar(QWidget):
    """可点击的路径条，右端挂着已生效的筛选条件。"""

    path_requested = Signal(object)
    #: 用户撤掉了右端某一枚条件胶囊，带的是它的序号（不是界面上数出来的位置）。
    condition_removed = Signal(int)
    condition_edited = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        self._layout.setSpacing(0)
        self._theme = theme.current_theme()
        # 记下 ``ensure_installed()`` 返回的监听器：``findChild`` 翻不到挂在 ``QApplication``
        # 上的它。
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)

    def set_right_inset(self, points: int) -> None:
        """把右端那几枚条件胶囊的右缘推到「离这一行的右缘 ``points`` 点」的地方。

        对齐的是标题栏右上角那个搜索胶囊的右缘，不是窗口右缘（换算由
        :meth:`MainWindow._layout_title_bar` 做）：不对齐时两列东西的右缘是折的。
        """
        self._layout.setContentsMargins(_PAD, _PAD, max(0, points), _PAD)

    def _apply_theme(self) -> None:
        """系统换浅色 / 深色之后，把现存的分隔符按新主题重上一次色。

        就地改样式而不整条重摆：重摆要等布局再激活一轮才落位，中间那一刻新控件还停在
        640×480 的默认尺寸且不可见。
        """
        self._theme = theme.current_theme()
        style = self._separator_style()
        for widget in self.findChildren(QLabel, _SEPARATOR_OBJECT_NAME):
            widget.setStyleSheet(style)

    def set_path(
        self,
        parts: Sequence[tuple[str, Path]],
        *,
        group: str | None = None,
        conditions: Sequence[str] = (),
        result_count: int | None = None,
        editing: int | None = None,
        scope: str = SEARCH_SCOPE_LIBRARY,
    ) -> None:
        """重建整条路径。``parts`` 由 ``ui.navigation.breadcrumb_parts`` 给出。

        「当前所在」落在路径上，仅当后面既没有分组也没有结果段可指。
        **作用域只改那一段的说法，不改路径本身**（ADR 0040 决策 4）：整库检索时调用方只传库名，
        报出「旅行 ▸ 2024」就是在说一件范围真变了的事。"""
        self._clear()
        # 「当前所在」落在路径上，仅当后面既没有分组也没有结果段可指。
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
            self._layout.addWidget(self._result_label(result_count, scope))
        self._layout.addStretch(1)
        for position, text in enumerate(conditions):
            if position:
                self._layout.addSpacing(4)
            self._layout.addWidget(self._chip(position, text, editing == position))

    def _result_label(self, result_count: int | None, scope: str) -> QLabel:
        """结果那一段。命中数写在里面，**被截断时用户看得见**。

        **两句话说两件不同的事**（ADR 0040 决策 4）：整库检索的结果横跨全库，而筛选的结果是
        「当前这一层里剩下的那些」。用同一句话会把前者说成后者 —— 界面表现是「这个文件夹里搜到
        4 张」，而实际那 4 张大半在别处。
        """
        filtering = scope == SEARCH_SCOPE_LEVEL
        title = "筛选后" if filtering else "搜索结果"
        text = title if result_count is None else f"{title}（{result_count}）"
        widget = QLabel(text)
        widget.setToolTip(
            "当前这一层里符合这些条件的照片；Esc 逐条撤销条件"
            if filtering
            else "整个库里检索出来的照片（不是只在某一个文件夹里）；Esc 逐条撤销条件"
        )
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _chip(self, position: int, text: str, editing: bool) -> QToolButton:
        return build_chip(text, position, editing=editing, parent=self, owner=self)

    def _separator(self) -> QLabel:
        separator = QLabel(SEPARATOR)
        # 名字是给 :meth:`_apply_theme` 认的（换外观时要挨个找到现存的那些）。
        separator.setObjectName(_SEPARATOR_OBJECT_NAME)
        separator.setStyleSheet(self._separator_style())
        return separator

    def _separator_style(self) -> str:
        """分隔符那一枚记号的字色 —— **由主题派生**，理由见 :data:`SEPARATOR_ALPHA`。"""
        return f"color: {rgba(self._theme.text_at(SEPARATOR_ALPHA))}; font-size: 11px;"

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

    def _group_label(self, group: str) -> QLabel:
        """分组名：和当前目录一样的重体，只是不可点（点它没有对应的目录可回）。"""
        widget = QLabel(group)
        widget.setToolTip("当前分组（Esc 返回上一层）")
        widget.setStyleSheet("font-size: 12px; font-weight: 600; padding: 2px 6px;")
        return widget

    def _clear(self) -> None:
        # 每换一次目录就整个重摆，顺序上的那个坑在这里一样踩得到（见 :mod:`gallery.ui.layout`）。
        clear_layout(self._layout)

    def clear_content(self) -> None:
        """**清空这一行的内容**（给「这一行此刻不该出现」的那些调用点用）。

        **与 :meth:`_clear` 的区别是公开**：``_clear`` 是「我要重摆一遍」的第一步，而这一句是
        「这一行现在什么都不该有」。``hide()`` 只让控件不画、**它们还留在布局里**，于是「藏起来」
        与「摆成空的」会被当成同一件事，之后有人读这一行就量到了上一层的残留。
        """
        self._clear()


__all__ = ["SEPARATOR", "BreadcrumbBar"]
