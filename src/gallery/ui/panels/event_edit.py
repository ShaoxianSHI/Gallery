"""编辑一个事件（旅行、演出……）—— 字段**由事件的定义生成**（ADR 0043）。

这个对话框一个用户的文件都不碰：它只把用户填的那一份读成 :class:`EventEdit`，写库走
``core.events``。**公共列与类型独有的字段一视同仁**：控件由
:func:`~gallery.core.events.kinds.fields_of` 摆出来，所以加一类事件、给它加一个字段，
这里一个字都不用改 —— 面板与建表语句共用同一份定义（ADR 0043 决策 3）。

两处判断写在代码里：

- **名称给一个默认值**：填了目的地与日期之后名称栏自己长成「目的地 · 日期」，直到用户动手
  改过它为止（见 :meth:`_suggest_title`）。
- **「把这段日期之间的照片都记进来」** 摆在面板上而**不在右键菜单里** —— 多一级菜单会让
  「记录为 → 旅行 → 京都五日」变成四级，而这一步本来就只在做这件事的时候才想到（ADR 0043 决策 4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gallery.core.events.kinds import (
    EventField,
    EventKind,
    cost_problem,
    ended_on_problem,
    fields_of,
    started_on_problem,
    suggested_title,
    title_problem,
)
from gallery.core.events.store import Event, EventDraft
from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 错误那一行留几行的位置。固定高度是为了对话框不跳：报错前后它都在那儿。
ERROR_LINES: Final = 2

#: 输入框至少这么宽（「United States」那种地名放不下）。
FIELD_MIN_WIDTH: Final = 220

#: 每一个字段是哪种控件 —— 一排按钮还是输入框。**由字段自己的声明说了算**，没有第三种。
type FieldWidget = QLineEdit | ChoiceButtons


@dataclass(frozen=True, slots=True)
class EventEdit:
    """面板交出来的那一份。"""

    draft: EventDraft
    #: 「把开始到结束之间的照片都记进来」勾着、且填了开始日期时为真 —— **主窗口**据此按日期
    #: 范围批量绑定，面板自己不碰任何一张照片。
    include_range: bool = False

    @property
    def first_day(self) -> str:
        """要查的那一段的头一天。没填就是空串，而空串**永远不触发**那次查询。"""
        return self.draft.text("started_on")

    @property
    def last_day(self) -> str:
        """尾一天。没填就与头一天同一天 —— 「当天来回」是最常见的那种。"""
        return self.draft.text("ended_on") or self.first_day


class ChoiceButtons(QWidget):
    """有值域的字段摆成一排按钮：点一下选中，再点一下取消（都不选 = 不记这一项）。

    比下拉少一次展开的动作，五个评分也一眼全在。**单选由这一层自己管**（``QButtonGroup``
    设成非互斥）：互斥的组不给「再点一下把当前那个取消」，而评分是可不记的。
    """

    def __init__(self, choices: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        group = QButtonGroup(self)
        group.setExclusive(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 与其余字段同宽：这一排要铺满输入框那一列，不是各占自己那一小块。
        self.setMinimumWidth(FIELD_MIN_WIDTH)
        for choice in choices:
            button = QPushButton(choice)
            button.setObjectName("eventEditChoice")
            button.setCheckable(True)
            # 每个按钮都 Expanding：五个平分这一排（用户要的就是「铺满这一排」）。
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            # 过一层 lambda：``clicked`` 那个 bool 穿不过 ``partial`` 的参数自省。
            button.clicked.connect(
                lambda checked=False, chosen=choice: self._on_pressed(chosen, checked)
            )
            row.addWidget(button)
            self._buttons[choice] = button
            group.addButton(button)

    def _on_pressed(self, choice: str, checked: bool) -> None:
        if checked:
            for other, button in self._buttons.items():
                if other != choice:
                    button.setChecked(False)

    def value(self) -> str:
        """选中的那一项；都没选就是空串。"""
        return next((choice for choice, button in self._buttons.items() if button.isChecked()), "")

    def set_value(self, text: str) -> None:
        for choice, button in self._buttons.items():
            button.setChecked(choice == text)


def dialog_style(app_theme: AppTheme, palette: QPalette) -> str:
    """对话框的样式。文字色全部从主题派生，与元数据编辑器同一份底色。

    选中的评分按钮借用**与弹出菜单、候选列表同一档**的高亮（``Highlight`` / ``HighlightedText``，
    由 :mod:`gallery.ui.theme` 钉死），所以要 ``palette`` 把那一份递进来 —— 不在这里另派生一份。
    """
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#eventEditTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#eventEditHint {{ color: {rgba(app_theme.text_at(0.62))}; font-size: 11px; }}
    QLabel#eventEditError {{ color: {rgba(app_theme.error)}; font-size: 11px; }}
    QLineEdit {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {rgba(app_theme.text_at(0.08))};
        border: 1px solid {rgba(app_theme.text_at(0.18))};
        border-radius: 5px;
        padding: 3px 6px;
        font-size: 12px;
    }}
    QLineEdit:focus {{ border: 1px solid {rgba(app_theme.link)}; }}
    QPushButton {{
        color: {rgba(app_theme.text_at(0.9))};
        background: {rgba(app_theme.text_at(0.12))};
        border: none; border-radius: 6px; padding: 5px 8px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {rgba(app_theme.text_at(0.2))}; }}
    QPushButton#eventEditChoice:checked {{
        color: {rgba(highlighted_text)};
        background: {rgba(highlight)};
    }}
    QCheckBox {{ color: {rgba(app_theme.text_at(0.78))}; font-size: 12px; }}
    """


class EventEditDialog(QDialog):
    """新建或编辑一个事件。:meth:`edit` 给出用户填的那一份，写库不归这里管。"""

    def __init__(
        self,
        parent: QWidget | None,
        kind: EventKind,
        *,
        event: Event | None = None,
        selected: int = 0,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._event = event
        #: 上一次**自动生成**的名称。用户动过它之后就不再自动改了 —— 手写的名字是他的意思。
        self._auto_title = ""
        self._fields: dict[str, FieldWidget] = {}

        self.setWindowTitle("编辑事件" if event is not None else f"新建{kind.label}")
        self.setModal(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build(selected)
        if event is not None:
            self._fill(event)
        else:
            self._suggest_title()

    # ---- 对外 --------------------------------------------------------------

    def edit(self) -> EventEdit:
        """用户填完的那一份。空 = 那一项不记。"""
        return EventEdit(
            draft=EventDraft(values=self._values()),
            include_range=self._include_range.isChecked() and bool(self._text("started_on")),
        )

    # ---- 搭建 --------------------------------------------------------------

    def _build(self, selected: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        # 不能改变大小：用户拖一下窗口边就该是同样大小。
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        heading = QLabel(
            f"编辑「{self._event.display_name}」"
            if self._event is not None
            else f"新建{self._kind.label}"
        )
        heading.setObjectName("eventEditTitle")
        layout.addWidget(heading)
        layout.addLayout(self._build_form())
        layout.addLayout(self._build_range(selected))

        # 报错那一行在按钮上面：它说的是上面哪个框不对，放在那儿才找得回去。
        self._error = QLabel()
        self._error.setObjectName("eventEditError")
        self._error.setWordWrap(True)
        self._error.setFixedHeight(self.fontMetrics().height() * ERROR_LINES)
        layout.addWidget(self._error)

        layout.addLayout(self._build_buttons())

        # ⌘W 关闭：不挂这一条的话它会一路走到主窗口，在模态对话框里关掉整个窗口。
        # 显式写出来而不写 ``StandardKey.Close`` —— Qt 在 macOS 上把后者解析成 ⌘F4。
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        for field in fields_of(self._kind):
            widget = self._build_field(field)
            self._fields[field.id] = widget
            form.addRow(field.label, _with_hint(widget, field.hint))
        return form

    def _build_field(self, field: EventField) -> FieldWidget:
        """一个字段摆成什么控件：有值域的一排按钮，其余输入框。"""
        if field.choices:
            return ChoiceButtons(field.choices)
        line = QLineEdit()
        line.setMinimumWidth(FIELD_MIN_WIDTH)
        if field.multiple:
            line.setPlaceholderText("多个用逗号分开")
        if field.id in ("title", "destination", "started_on"):
            # 名称自动长成「目的地 · 日期」靠的就是这三栏互相看着（见 :meth:`_suggest_title`）。
            line.textChanged.connect(self._suggest_title)
        return line

    def _build_range(self, selected: int) -> QVBoxLayout:
        """「把这段日期之间的照片都记进来」 —— 容器型事件最常用的那一步。

        **默认勾上**：填了起止日期的用户多半正是这个意思，而勾着的唯一后果就是「多记几张」，
        而那正是这一步要做的。编辑已有事件时勾子收起来（那里已经在讨论改内容了）。
        """
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self._include_range = QCheckBox("把开始到结束之间的照片都记进来")
        self._include_range.setChecked(self._event is None)
        column.addWidget(self._include_range)
        if selected:
            column.addWidget(_hint(f"当前选中的 {selected} 张照片也会被记进来。"))
        return column

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.setDefault(True)
        save.clicked.connect(self._on_accept)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    # ---- 填与建议 ----------------------------------------------------------

    def _fill(self, event: Event) -> None:
        """把已有事件的那一份摆进各个框。"""
        for field in fields_of(self._kind):
            widget = self._fields[field.id]
            text = event.text(field.id)
            if isinstance(widget, ChoiceButtons):
                widget.set_value(text)
            else:
                widget.setText(text)
        self._auto_title = event.title

    def _suggest_title(self) -> None:
        """名称栏自动长成「目的地 · 日期」，**直到用户动手改过它为止**。

        判据是「名称栏里那一句还是不是上次自动生成的那句」。不是了就归用户写 —— 手写的名字是
        他的意思，而**把这一栏删空同样是**（删空与「还没填」在字符上一样，靠 ``_auto_title``
        区分：用户清空后它就不等于那句自动生成的了）。
        """
        current = self._text("title")
        if current != self._auto_title:
            return
        suggestion = suggested_title(
            self._kind, self._text("destination"), self._text("started_on")
        )
        if suggestion == current:
            return
        title = self._fields.get("title")
        if isinstance(title, QLineEdit):
            title.setText(suggestion)
        self._auto_title = suggestion

    # ---- 收尾 --------------------------------------------------------------

    def _on_accept(self) -> None:
        """先说清哪一处不对，不关对话框 —— 关掉了用户填的那一份就白填了。"""
        for problem in self._problems():
            if problem is not None:
                self._error.setText(problem)
                return
        self.accept()

    def _problems(self) -> tuple[str | None, ...]:
        """按「从上到下」的顺序逐处说一遍。**空 = 都没毛病。**"""
        values = self._values()
        return (
            title_problem(self._kind, values.get("title", "")),
            started_on_problem(values.get("started_on", "")),
            ended_on_problem(values.get("started_on", ""), values.get("ended_on", "")),
            cost_problem(values.get("cost", "")),
        )

    def _values(self) -> dict[str, str]:
        return {field.id: self._text(field.id) for field in fields_of(self._kind)}

    def _text(self, field_id: str) -> str:
        widget = self._fields.get(field_id)
        if isinstance(widget, ChoiceButtons):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return ""

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("eventEditHint")
    label.setWordWrap(True)
    return label


def _with_hint(widget: QWidget, text: str) -> QWidget:
    """控件加上它下面那行说明，竖着摆 —— 对话框窄一点也不会把说明挤成一行。

    没说明就**原样返回控件**：不要为了「结构统一」给每个字段都套一层空壳。
    """
    if not text:
        return widget
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    column.addWidget(widget)
    column.addWidget(_hint(text))
    holder = QWidget()
    holder.setLayout(column)
    return holder


__all__ = [
    "ERROR_LINES",
    "FIELD_MIN_WIDTH",
    "ChoiceButtons",
    "EventEdit",
    "EventEditDialog",
    "dialog_style",
]
