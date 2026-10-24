"""编辑一个事件（旅行、演出……）—— 字段**由事件的定义生成**（ADR 0043）。

这个对话框一个用户的文件都不碰：它只把用户填的那一份读成 :class:`EventEdit`，写库走
``core.events``。控件由 :func:`~gallery.core.events.kinds.fields_of` 摆出来，加一类事件、给它加
一个字段，这里一个字都不用改（ADR 0043 决策 3）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette, QResizeEvent
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
    COVER,
    EventField,
    EventKind,
    cost_problem,
    ended_on_problem,
    fields_of,
    started_on_problem,
    suggested_title,
    title_problem,
)
from gallery.core.events.prefill import EventPrefill
from gallery.core.events.store import Event, EventDraft
from gallery.ui import theme
from gallery.ui.outline import opaque_over
from gallery.ui.theme import AppTheme, rgba

#: 错误那一行留几行的位置。固定高度是为了对话框不跳：报错前后它都在那儿。
ERROR_LINES: Final = 2

#: 输入框至少这么宽（「United States」那种地名放不下）。
FIELD_MIN_WIDTH: Final = 220

#: 输入框那一圈的圆角半径（点）—— 与搜索胶囊、改名输入框同一档手感。
FIELD_RADIUS: Final = 5

#: 一排最多摆几个选择按钮。五个评分正好一行，八个来源分成两行。
MAX_CHOICES_PER_ROW: Final = 5

#: 每一个字段是哪种控件 —— 一排按钮还是输入框。**由字段自己的声明说了算**，没有第三种。
type FieldWidget = QLineEdit | ChoiceButtons

#: 「删除事件…」按下去时 :meth:`QDialog.exec` 给回来的那个码。**不是 Accepted 也不是 Rejected**
#: —— 调用方要在 exec 回来之后分派，而那一刻这个对话框已经关掉了。
#:
#: **不用信号**：信号是从 ``clicked`` 里同步发出来的，于是「确认框」会嵌套在对话框自己的
#: ``exec()`` 里面 —— 两个模态叠在一起，关闭顺序会乱。一个返回值把两件事分成先后两步。
DELETE_REQUESTED: Final = 2


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


def _row_counts(total: int) -> tuple[int, ...]:
    """每行摆几个按钮：行数尽量少，各行的个数尽量平均。"""
    if total <= 0:
        return ()
    rows = (total + MAX_CHOICES_PER_ROW - 1) // MAX_CHOICES_PER_ROW
    columns = (total + rows - 1) // rows
    return (columns,) * (rows - 1) + (total - columns * (rows - 1),)


class ChoiceButtons(QWidget):
    """有值域的字段摆成按钮：点一下选中，再点一下取消（都不选 = 不记这一项）。

    不超过 :data:`MAX_CHOICES_PER_ROW` 项时只占一排；多了就按行铺开，**每一行都铺满整个字段列**
    （八个来源分两行，最右那个按钮与输入框的右缘对齐）。**单选由这一层自己管**（``QButtonGroup``
    设成非互斥）：互斥的组不给「再点一下把当前那个取消」，而评分是可不记的。
    """

    def __init__(self, choices: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        group = QButtonGroup(self)
        group.setExclusive(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 与其余字段同宽：这一排要铺满输入框那一列，不是各占自己那一小块。
        self.setMinimumWidth(FIELD_MIN_WIDTH)
        index = 0
        for count in _row_counts(len(choices)):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            for choice in choices[index : index + count]:
                button = QPushButton(choice)
                button.setObjectName("eventEditChoice")
                button.setCheckable(True)
                # 每个按钮都 Expanding：这一行的几个平分宽度，于是每行左右两端都对齐字段列。
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                # 过一层 lambda：``clicked`` 那个 bool 穿不过 ``partial`` 的参数自省。
                button.clicked.connect(
                    lambda checked=False, chosen=choice: self._on_pressed(chosen, checked)
                )
                row.addWidget(button)
                self._buttons[choice] = button
                group.addButton(button)
            index += count
            column.addLayout(row)

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


class ElidedLabel(QLabel):
    """一行文字放不下时**从中间省略**，全文挂在提示气泡上。

    **这一行的长短不参与布局**（水平方向 ``QSizePolicy.Ignored``）：它写的是用户起的名字，而
    窗口的宽该由那几个输入框定 —— 否则一个长名字就把面板顶成一条（中间省略，事件名两头最有
    信息量）。与标题条那一行同一档做法（:mod:`gallery.ui.window.widgets.title_bar`）。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        """按当前宽度摆一次。宽度是布局给的（``Ignored`` 不参与定宽），所以每次改大小都重来。"""
        self.setText(
            self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width())
        )


def dialog_style(app_theme: AppTheme, palette: QPalette) -> str:
    """对话框的样式。文字色全部从主题派生，与元数据编辑器同一份底色。

    选中的评分按钮借用**与弹出菜单同一档**的高亮（由 :mod:`gallery.ui.theme` 固定），所以要
    ``palette`` 把那一份递进来；输入框那圈边框与底色**先合成成不透明的**（理由见
    :mod:`gallery.ui.outline`）：Qt 把样式表那一圈描成八段，半透明的颜色在接缝处被调两遍就深一点。
    """
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    fill = opaque_over(app_theme.text_at(0.08), app_theme.background)
    edge = opaque_over(app_theme.text_at(0.18), fill)
    focus = opaque_over(app_theme.link, fill)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel:disabled {{ color: {rgba(app_theme.text_at(0.38))}; }}
    QLabel#eventEditTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#eventEditError {{ color: {rgba(app_theme.error)}; font-size: 11px; }}
    QLineEdit {{
        color: {rgba(app_theme.text_at(0.92))};
        background: {rgba(fill)};
        border: 1px solid {rgba(edge)};
        border-radius: {FIELD_RADIUS}px;
        padding: 3px 6px;
        font-size: 12px;
    }}
    QLineEdit:focus {{ border: 1px solid {rgba(focus)}; }}
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
    /* 删除与「取消 / 保存」不是一件事，**颜色要分开**：它不可撤销地删掉用户自己填的一批记录，
       摆在同一种中性灰里等于把它说成「跟保存差不多」。用主题的 error 那一档，不另派生。 */
    QPushButton#eventEditDelete {{ color: {rgba(app_theme.error)}; }}
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
        prefill: EventPrefill | None = None,
    ) -> None:
        """``prefill`` 是从选中的照片推出来的默认值（见 :mod:`gallery.core.events.prefill`）。

        **只在摆出来这一次填进去**，之后这一栏就是用户的 —— 与名称那一栏的自动生成同一件事，
        只是那边靠「当前那句还是不是我生成的」判断，这边靠「还没填过」。
        """
        super().__init__(parent)
        self._kind = kind
        self._event = event
        #: 上一次**自动生成**的名称。用户动过它之后就不再自动改了 —— 手写的名字是他的意思。
        self._auto_title = ""
        self._fields: dict[str, FieldWidget] = {}
        #: 用户在面板里按了「清除封面」。**与「库里本来就没设」分开** ——
        #: 前者是一次待写入的改动，后者什么都不用做。
        self._cover_cleared = False

        self.setWindowTitle("编辑事件" if event is not None else f"新建{kind.label}")
        self.setModal(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build()
        if event is not None:
            self._fill(event)
        else:
            self._prefill(prefill or EventPrefill())
            self._suggest_title()

    # ---- 对外 --------------------------------------------------------------

    def edit(self) -> EventEdit:
        """用户填完的那一份。空 = 那一项不记。"""
        return EventEdit(
            draft=EventDraft(values=self._values()),
            include_range=self._include_range.isChecked() and bool(self._text("started_on")),
        )

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        # 不能改变大小：用户拖一下窗口边就该是同样大小。
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        heading = ElidedLabel(
            f"编辑「{self._event.display_name}」"
            if self._event is not None
            else f"新建{self._kind.label}"
        )
        heading.setObjectName("eventEditTitle")
        layout.addWidget(heading)
        layout.addLayout(self._build_form())
        layout.addLayout(self._build_range())

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
            if field is COVER:
                # 封面不是一格文字：它显示当前是哪一张，配一个「清除」，
                # 而**新建时整行禁用**（见 :meth:`_build_cover`）。
                form.addRow(field.label, self._build_cover())
                continue
            widget = self._build_field(field)
            self._fields[field.id] = widget
            form.addRow(field.label, widget)
        return form

    def _build_cover(self) -> QWidget:
        """封面那一行：**当前封面的文件名 + 一个「清除封面」**，点后者直接收掉。

        **v1 不做缩略图挑选器**（ADR 0048 决策 2）：新建时哪些照片会被记进去要到面板关掉之后才
        定下来，网格里已经看得见那些照片，「设为事件封面」就挂在那张照片的右键菜单上（决策 6）。

        **新建时整行禁用**，只留一个灰色的「（待定）」：摆着却按不动，用户只会以为是坏的。
        """
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._cover_label = QLabel()
        # 长文件名不许把这一列顶宽：它会占满按钮左边的空位，放不下就裁掉（全文在提示气泡里）。
        self._cover_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._cover_label, 1)
        self._clear_cover_button = QPushButton("清除封面")
        self._clear_cover_button.setObjectName("eventEditClearCover")
        self._clear_cover_button.clicked.connect(self._clear_cover)
        layout.addWidget(self._clear_cover_button)
        # **顺序要紧**：`_cover_row_enabled` 在禁用时会改那行字（改成「（待定）」），
        # 所以它要走在 `_show_cover` 后面 —— 反过来就把「（待定）」那句盖掉了。
        self._show_cover(self._event.cover_path if self._event is not None else None)
        self._cover_row_enabled(self._event is not None)
        return row

    def _show_cover(self, path: Path | None) -> None:
        """把当前封面显示成**文件名**（不显示全路径：一行输入框那么宽，塞不下也没人要看）。

        **只取 ``name``，不 ``stat``**：这一行要在面板打开的那一刻就画出来，
        而那是主线程 —— 一次 ``stat`` 在云盘上可能几十毫秒，而文件名就够了。
        """
        text = path.name if path is not None else "未设置（用第一张照片）"
        self._cover_label.setText(text)
        self._cover_label.setToolTip(str(path) if path is not None else "")

    def _cover_row_enabled(self, enabled: bool) -> None:
        """新建时那一行禁用，只显示灰色的「（待定）」（见 :meth:`_build_cover`）。"""
        self._cover_label.setEnabled(enabled)
        self._clear_cover_button.setEnabled(enabled and self._cover() is not None)
        if not enabled:
            self._cover_label.setText("（待定）")

    def _clear_cover(self) -> None:
        """在面板里把封面收掉。**不立刻写库** —— 面板的规矩是一次编辑只写一次（ADR 0031），
        所以这里只改面板上那一份，按「保存」才落。"""
        self._show_cover(None)
        self._cover_cleared = True
        self._clear_cover_button.setEnabled(False)

    def _cover(self) -> Path | None:
        """面板上这一份封面。``None`` 就是「用第一张」。"""
        if self._cover_cleared:
            return None
        stored = self._event.cover_path if self._event is not None else None
        return stored

    def _build_field(self, field: EventField) -> FieldWidget:
        """一个字段摆成什么控件：有值域的一排按钮，其余输入框。"""
        if field.choices:
            return ChoiceButtons(field.choices)
        line = QLineEdit()
        line.setMinimumWidth(FIELD_MIN_WIDTH)
        if field.multiple:
            line.setPlaceholderText("多个用逗号分开")
        if field.id in {"title", "started_on", *self._kind.title_parts}:
            # 名称自动长成「日期 · 关键字段」靠的就是这几栏互相看着（见 :meth:`_suggest_title`）。
            line.textChanged.connect(self._suggest_title)
        return line

    def _build_range(self) -> QVBoxLayout:
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
        return column

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        if self._event is not None:
            # **只有编辑已有事件时才摆**：新建的那个还不存在，删了它没有对象。
            delete = QPushButton("删除事件…")
            delete.setObjectName("eventEditDelete")
            delete.clicked.connect(lambda: self.done(DELETE_REQUESTED))
            row.addWidget(delete)
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
            widget = self._fields.get(field.id)
            if widget is None:
                continue  # 封面那一行在 :meth:`_build_cover` 里摆好了
            text = event.text(field.id)
            if isinstance(widget, ChoiceButtons):
                widget.set_value(text)
            else:
                widget.setText(text)
        self._auto_title = event.title
        self._show_cover(event.cover_path)

    def _prefill(self, prefill: EventPrefill) -> None:
        """把从选中的照片推出来的那几个默认值填进去。**空串跳过** —— 那是「推不出来」，
        不是「要清空用户填的」。

        **只在摆出来这一刻填一次**，之后那几栏归用户：不挂 ``textChanged``，免得用户敲下的
        目的地被下一次触发顶回照片里那个。
        """
        for field_id, text in (
            (self._kind.place_field, prefill.place),
            ("started_on", prefill.started_on),
            ("ended_on", prefill.ended_on),
        ):
            widget = self._fields.get(field_id)
            if text and isinstance(widget, QLineEdit):
                widget.setText(text)

    def _suggest_title(self) -> None:
        """名称栏自动长成「日期 · 目的地」，**直到用户动手改过它为止**。

        判据是「名称栏里那一句还是不是上次自动生成的那句」。不是了就归用户写 —— 手写的名字是
        他的意思，而**把这一栏删空同样是**（删空与「还没填」在字符上一样，靠 ``_auto_title``
        区分：用户清空后它就不等于那句自动生成的了）。
        """
        current = self._text("title")
        if current != self._auto_title:
            return
        suggestion = suggested_title(self._kind, self._values())
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
            *(
                cost_problem(values.get(field.id, ""), field.label)
                for field in self._kind.fields
                if field.sql_type == "REAL"
            ),
        )

    def _values(self) -> dict[str, str]:
        """面板上这一份草稿。**封面必须在这里面**（ADR 0048 决策 2）——
        ``EventStore.update`` 是拿整份草稿写所有列的，而这里少给一格就是把它写成 NULL：
        用户「打开面板改个目的地、按保存」，回来发现封面没了，而界面上没有任何一句话说过这事。
        """
        values = {field.id: self._text(field.id) for field in fields_of(self._kind)}
        cover = self._cover()
        values[COVER.id] = str(cover) if cover is not None else ""
        return values

    def _text(self, field_id: str) -> str:
        widget = self._fields.get(field_id)
        if isinstance(widget, ChoiceButtons):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return ""

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))


__all__ = [
    "DELETE_REQUESTED",
    "ERROR_LINES",
    "FIELD_MIN_WIDTH",
    "FIELD_RADIUS",
    "MAX_CHOICES_PER_ROW",
    "ChoiceButtons",
    "ElidedLabel",
    "EventEdit",
    "EventEditDialog",
    "dialog_style",
]
