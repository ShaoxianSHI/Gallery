"""大图右侧的元数据面板。

推挤式而不是浮层：面板占掉宽度、图片区域缩小并重新适配，任何照片都不会被挡住。
值一律可选中复制；底部「显示全部字段」展开来源返回的原始键值（ICC 配置等在那里也能翻到）。
照片自己的元数据下面接着摆它所属的那些事件，一个事件一组。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gallery.core.events.kinds import COVER, EventField, fields_of, kind_by_id
from gallery.core.events.models import Event, shorten_name
from gallery.core.i18n import t
from gallery.core.metadata.report import MetadataField, MetadataGroup, MetadataReport
from gallery.ui import theme
from gallery.ui.layout import clear_layout
from gallery.ui.scrollbar_style import apply_scrollbar_style
from gallery.ui.theme import AppTheme, rgba


#: 「位置」那一组的标题（core 的报告按当前语言生成，所以比较时也得现查）。
def _place_group_title() -> str:
    return t("metadata.group.location")


PANEL_WIDTH = 300
LABEL_WIDTH = 84
COPIED_HINT_MS = 1400

#: 面板内容离右缘留几像素（点）。**滚动条那一侧不算它**：那一侧要的是一条窄缝（见下一档）。
CONTENT_PAD = 12

#: 面板那条滚动条离**窗口**右缘留几像素（点），与网格页取同一个值
#: （``main_window._GRID_PAGE_MARGIN_RIGHT``）：用户在网格与大图之间来回走，两条滚动条到
#: 窗口右缘的距离要读得出是同一条。滑块自己还带
#: :data:`~gallery.ui.scrollbar_style.HANDLE_GAP` 那 2 点，两段加起来才是滑块右缘的距离。
SCROLLBAR_EDGE_GAP = 2

#: 滚动区与页脚**自己**的右边距（点）= :data:`CONTENT_PAD` 减去那一档窄缝。滚动条挪到窗口
#: 边上之后，正文与页脚靠外的留白得在这一层补回来 —— 文本紧贴窗口右缘比一条宽缝难看得多，
#: 而这一档补的是位置，那一份宽度一分不少。
_CONTENT_PAD_RIGHT = CONTENT_PAD - SCROLLBAR_EDGE_GAP


def event_title(event: Event) -> str:
    """事件那一块的组标题：``旅行 · 京都五日``。带上类型名 —— 一张照片可以同时属于一次旅行与
    一场演出，只写名字分不出那是哪一类。名称太长时中间省略，与菜单、横幅同一档。
    """
    kind = kind_by_id(event.kind)
    name = shorten_name(event.display_name)
    if kind is None:
        return name
    return t("viewer.metadata.event_title", kind=kind.label, name=name)


def event_fields(event: Event) -> tuple[MetadataField, ...]:
    """一个事件在面板上摆的那几行：**只摆填过的，跳过名称与封面**。

    名称已经在组标题里了；封面是一条路径，摆出来只是一串文件名。空着的那几格是用户没记的，
    摆一排占位文字只会把有用的信息挤下去。
    """
    kind = kind_by_id(event.kind)
    if kind is None:
        return ()
    fields: list[MetadataField] = []
    for field in fields_of(kind):
        if field is COVER or field.id == "title":
            continue
        value = _event_value(event, field)
        if value:
            fields.append(MetadataField(label=field.label, value=value))
    return tuple(fields)


def _event_value(event: Event, field: EventField) -> str:
    """一格值按文字给出。``REAL`` 去掉浮点尾巴（``3280.0`` → ``3280``）。"""
    value = event.values.get(field.id)
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)


def panel_style(app_theme: AppTheme) -> str:
    """面板的文字与按钮样式。**底色不在这一份里**：面板自己不铺，露出窗口那一块（ADR 0096）。"""
    return f"""
#metadataPanel {{ background: transparent; }}
#metadataGroupTitle {{
    color: {rgba(app_theme.text_at(0.55))};
    font-size: 11px;
    font-weight: 600;
}}
#metadataLabel {{ color: {rgba(app_theme.text_at(0.52))}; font-size: 12px; }}
#metadataValue {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
#metadataNotice {{ color: {rgba(app_theme.notice)}; font-size: 11px; }}
#metadataEmpty {{ color: {rgba(app_theme.text_at(0.5))}; font-size: 12px; }}
#metadataError {{ color: {rgba(app_theme.error)}; font-size: 12px; }}
QPushButton#metadataButton {{
    color: {rgba(app_theme.text_at(0.85))};
    background: {rgba(app_theme.text_at(0.10))};
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 11px;
}}
QPushButton#metadataButton:hover {{ background: {rgba(app_theme.text_at(0.18))}; }}
QCheckBox#metadataToggle {{ color: {rgba(app_theme.text_at(0.62))}; font-size: 11px; }}
"""


class MetadataPanel(QWidget):
    """展示一张照片的元数据。只负责画，读取由主窗口在后台完成。"""

    #: 用户要编辑元数据（点了页脚那个按钮）。这里只发信号 —— 真正写文件的是 ``write_ops``。
    edit_metadata_requested = Signal()
    #: 用户在面板里按了 ← / →：上一张 / 下一张（-1 / +1）。焦点落在面板里也仍然该能
    #: 换照片 —— 点一下「复制全部」焦点就跑到面板这边，而网格的方向键只在网格有焦点时通。
    photo_step_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        self.setFixedWidth(PANEL_WIDTH)
        # 面板**不铺底色**（ADR 0096）：整块窗口只有一处铺底，它露出的就是那一块。
        theme.mark_see_through(self)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        # **首次下发挪到滚动区建好之后**：:meth:`_apply_theme` 要点名视口，那会儿它还不存在。

        self._report: MetadataReport | None = None
        self._events: tuple[Event, ...] = ()
        self._metadata_editable = False
        self._metadata_reason = ""
        #: 不能编辑时要不要在「位置」标题旁摆那句理由（视频页不摆，只把按钮置灰）。
        self._metadata_notice = True

        layout = QVBoxLayout(self)
        # 右边那一档比左边窄得多：滚动区要贴到窗口边上，让它的滚动条与网格页那条对齐。
        layout.setContentsMargins(CONTENT_PAD, CONTENT_PAD, SCROLLBAR_EDGE_GAP, 10)
        layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # **要透明就点名视口，别裸声明。** 不带选择器的那条会级联到所有子控件 —— 竖滚动条也在内，
        # 而它挂了样式表之后就不再走原生样式，轨道成了一条常驻的实心竖条（深色下尤其显眼）。
        self._body = QWidget()
        self._body.setAutoFillBackground(False)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, _CONTENT_PAD_RIGHT, 0)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)
        # 滚动区、它的视口与内容区三处都得点名：``setWidget`` 会把内容区设成 AutoFillBackground，
        # 而视口是 Qt 自己建的、样式表那条管不到它。三处都是同一句声明，换外观时不用再补。
        for widget in (self._scroll, self._scroll.viewport(), self._body):
            theme.mark_see_through(widget)
        apply_scrollbar_style(self._scroll.verticalScrollBar())
        layout.addWidget(self._scroll, 1)
        self._apply_theme()

        self._footer = QWidget()
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, _CONTENT_PAD_RIGHT, 0)
        footer_layout.setSpacing(8)
        self._copy_button = QPushButton(t("viewer.metadata.copy_all"))
        self._copy_button.setObjectName("metadataButton")
        self._copy_button.clicked.connect(self._copy_all)
        self._edit_button = QPushButton(t("viewer.metadata.edit"))
        self._edit_button.setObjectName("metadataButton")
        self._edit_button.clicked.connect(self.edit_metadata_requested)
        self._show_all = QCheckBox(t("viewer.metadata.show_all"))
        self._show_all.setObjectName("metadataToggle")
        self._show_all.toggled.connect(lambda _checked: self._render())
        footer_layout.addWidget(self._copy_button)
        footer_layout.addWidget(self._edit_button)
        footer_layout.addWidget(self._show_all)
        # 三个组件平分整行：两个按钮比勾选框窄，剩下的宽度按比例分给它们（``Expanding``）。
        for button in (self._copy_button, self._edit_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._footer)

        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(
            lambda: self._copy_button.setText(t("viewer.metadata.copy_all"))
        )

        self._install_step_shortcuts()
        self.clear()

    def _step_photo(self, delta: int) -> None:
        """← / → 转成换上一张 / 下一张；焦点在文本框里就不插手。"""
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QAbstractSpinBox, QComboBox)):
            return
        self.photo_step_requested.emit(delta)

    def _install_step_shortcuts(self) -> None:
        """把 ← / → 收进面板自己，范围是「面板以及面板里的东西」。

        不写在 ``keyPressEvent`` 里：Qt6 里控件 ignore 掉不认识的按键后不会往父控件冒，
        焦点停在按钮上时面板根本收不到。范围只给「面板这一支」—— 焦点在网格里时方向键仍归网格。
        """
        for key, delta in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.setShortcutVisibleInContextMenu(False)
            action.triggered.connect(partial(self._step_photo, delta))
            self.addAction(action)

    def _apply_theme(self) -> None:
        """按当前主题重画文字与按钮（底色归窗口，面板不铺）。"""
        self.setStyleSheet(panel_style(theme.current_theme()))
        self.update()

    def clear(self) -> None:
        """还没有选中照片。"""
        self._report = None
        self._events = ()
        self._footer.hide()
        self._fill((self._message(t("viewer.metadata.empty"), "metadataEmpty"),))

    def show_loading(self, name: str = "") -> None:
        self._footer.hide()
        text = (
            t("viewer.metadata.loading.named", name=name) if name else t("viewer.metadata.loading")
        )
        self._fill((self._message(text, "metadataEmpty"),))

    def show_error(self, message: str) -> None:
        self._report = None
        self._footer.hide()
        self._fill((self._message(message, "metadataError"),))

    def set_report(self, report: MetadataReport) -> None:
        """展示一份读好的元数据。"""
        self._report = report
        self._footer.show()
        self._render()

    def set_events(self, events: Sequence[Event]) -> None:
        """这张照片属于哪些事件，摆在它自己的元数据下面。

        **与 :meth:`set_report` 分开**：元数据是异步读回来的，还会被地点反查替换几次，而事件
        来自索引里那份缓存、一次定下来 —— 换报告时不该把事件一起丢掉。
        """
        self._events = tuple(events)
        self._render()

    def set_metadata_editable(
        self, editable: bool, *, reason: str = "", notice: bool = True
    ) -> None:
        """「编辑元数据」此刻能不能用；不能用时（没装 exiftool）把理由说出来 ——
        摆着一个点下去只会报错的按钮比没有更糟（ADR 0014）。
        """
        self._metadata_editable = editable
        self._metadata_reason = reason
        self._metadata_notice = notice
        self._edit_button.setEnabled(editable)
        self._edit_button.setToolTip(reason if not editable else t("viewer.metadata.edit_tooltip"))
        self._render()

    @property
    def metadata_editable(self) -> bool:
        return self._metadata_editable

    @property
    def report(self) -> MetadataReport | None:
        return self._report

    def _render(self) -> None:
        report = self._report
        if report is None:
            return
        widgets: list[QWidget] = []
        if report.notice:
            widgets.append(self._message(report.notice, "metadataNotice"))
        groups = list(report.groups)
        if self._show_all.isChecked() and report.all_fields:
            groups.append(
                MetadataGroup(title=t("viewer.metadata.all_fields"), fields=report.all_fields)
            )
        for group in groups:
            widgets.append(self._group_widget(group))
        for event in self._events:
            widgets.append(self._event_widget(event))
        self._fill(widgets)

    def _event_widget(self, event: Event) -> QWidget:
        """一个事件那一块，摆在照片自己的元数据下面。"""
        return self._group_widget(
            MetadataGroup(title=event_title(event), fields=event_fields(event))
        )

    def _group_widget(self, group: MetadataGroup) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(self._group_title(group.title))

        for field in group.fields:
            layout.addWidget(self._row(field.label, field.value))
        return container

    def _group_title(self, title: str) -> QWidget:
        """组标题。只有「位置」那一组例外：不能编辑时把理由说在标题右边。

        「编辑元数据」按钮在页脚而不在标题上：照片本来就没有位置时，面板上根本没有「位置」
        那一组，入口也就跟着没了 —— 而那恰恰是最需要补一个位置的时候。
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("metadataGroupTitle")
        layout.addWidget(label)
        layout.addStretch(1)
        if title == _place_group_title() and not self._metadata_editable and self._metadata_notice:
            hint = QLabel(self._metadata_reason or t("viewer.metadata.not_editable"))
            hint.setObjectName("metadataNotice")
            hint.setWordWrap(True)
            hint.setToolTip(self._metadata_reason)
            layout.addWidget(hint, 1)
        return row

    def _row(self, label: str, value: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        name = QLabel(label)
        name.setObjectName("metadataLabel")
        name.setFixedWidth(LABEL_WIDTH)
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(name)

        text = QLabel(value)
        text.setObjectName("metadataValue")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setCursor(Qt.CursorShape.IBeamCursor)
        layout.addWidget(text, 1)
        return container

    def _message(self, text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        return label

    def _fill(self, widgets: Sequence[QWidget]) -> None:
        # 「先藏、再摘、再排队删」的三步顺序与理由写在 :mod:`gallery.ui.layout` 里，规矩只有一份。
        clear_layout(self._body_layout)
        for widget in widgets:
            self._body_layout.addWidget(widget)
        self._body_layout.addStretch(1)
        self._scroll.verticalScrollBar().setValue(0)

    def _copy_all(self) -> None:
        report = self._report
        if report is None:
            return
        lines = [report.as_text()]
        if self._show_all.isChecked() and report.all_fields:
            lines.append("")
            lines.append(t("viewer.metadata.clipboard.all_fields"))
            lines.extend(
                t("viewer.metadata.clipboard.field", label=field.label, value=field.value)
                for field in report.all_fields
            )
        for event in self._events:
            lines.append("")
            lines.append(t("viewer.metadata.clipboard.event", name=event_title(event)))
            lines.extend(
                t("viewer.metadata.clipboard.field", label=field.label, value=field.value)
                for field in event_fields(event)
            )
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._copy_button.setText(t("viewer.metadata.copied"))
        self._hint_timer.start(COPIED_HINT_MS)


__all__ = [
    "CONTENT_PAD",
    "COPIED_HINT_MS",
    "LABEL_WIDTH",
    "PANEL_WIDTH",
    "SCROLLBAR_EDGE_GAP",
    "MetadataPanel",
    "event_fields",
    "event_title",
    "panel_style",
]
