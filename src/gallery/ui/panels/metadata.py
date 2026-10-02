"""大图右侧的元数据面板。

推挤式而不是浮层：面板占掉宽度、图片区域缩小并重新适配，因此**任何照片都不会被挡住**。
底色与图片区同色（同一份主题，见 ``ui.theme``），看起来像同一个取景器旁边的一块信息板。

值一律可选中复制；底部「显示全部字段」展开来源返回的原始键值 —— 精选字段表覆盖不到的
东西（制造商注释、ICC 配置等）在那里都能翻到。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gallery.core.metadata.exif import MetadataGroup, MetadataReport
from gallery.ui import theme
from gallery.ui.panels.layout import clear_layout
from gallery.ui.theme import AppTheme, rgba

#: 面板宽度。窄了中文标签换行，宽了抢照片的地方。
PANEL_WIDTH = 300

#: 左列标签宽度。
LABEL_WIDTH = 84

#: 「已复制」提示停留多久。
COPIED_HINT_MS = 1400


def panel_style(app_theme: AppTheme) -> str:
    """面板的文字与按钮样式。底色不在这里 —— 它由 ``paintEvent`` 统一画。

    文字色全部从主题派生：深色主题下是「浅字压深底」，浅色主题下自动变成「深字压浅底」，
    不必再维护两套写死的字色。
    """
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        self.setFixedWidth(PANEL_WIDTH)
        # 底色不在样式表里：普通 QWidget 的样式表背景要靠 WA_StyledBackground，
        # 而 macOS 上连调色板也可能被原生样式覆盖 —— 实测两者都靠不住，
        # 所以底色自己画，见 paintEvent。系统换浅色 / 深色时重新取一次。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self._theme_watcher = theme.ensure_installed()
        self._theme_watcher.changed.connect(self._apply_theme)
        self._apply_theme()

        self._report: MetadataReport | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 裸声明会级联到所有子控件：让滚动区、内容区与每一行都透明，
        # 露出面板自己画的底色，而不是各自按调色板再填一遍。
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)
        layout.addWidget(self._scroll, 1)

        self._footer = QWidget()
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        self._copy_button = QPushButton("复制全部")
        self._copy_button.setObjectName("metadataButton")
        self._copy_button.clicked.connect(self._copy_all)
        footer_layout.addWidget(self._copy_button)
        self._show_all = QCheckBox("显示全部字段")
        self._show_all.setObjectName("metadataToggle")
        self._show_all.toggled.connect(lambda _checked: self._render())
        footer_layout.addWidget(self._show_all)
        footer_layout.addStretch(1)
        layout.addWidget(self._footer)

        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(lambda: self._copy_button.setText("复制全部"))

        self.clear()

    # ---- 主题 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        """按当前主题重画文字与按钮（底色在 ``paintEvent`` 里取，不用重建）。"""
        self.setStyleSheet(panel_style(theme.current_theme()))
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt 命名
        """自己画底色。

        样式表背景对普通 QWidget 不生效（除非 ``WA_StyledBackground``），而 macOS 上
        连调色板也可能被原生样式覆盖 —— 面板与照片区是不是同一块底色，是能不能看清
        的分界线，不值得赌样式系统的脾气，直接填。
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.current_theme().background)

    # ---- 状态 --------------------------------------------------------------

    def clear(self) -> None:
        """还没有选中照片。"""
        self._report = None
        self._footer.hide()
        self._fill((self._message("尚未打开照片", "metadataEmpty"),))

    def show_loading(self, name: str = "") -> None:
        self._footer.hide()
        text = f"正在读取元数据…\n{name}" if name else "正在读取元数据…"
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

    @property
    def report(self) -> MetadataReport | None:
        return self._report

    # ---- 渲染 --------------------------------------------------------------

    def _render(self) -> None:
        report = self._report
        if report is None:
            return
        widgets: list[QWidget] = []
        if report.notice:
            widgets.append(self._message(report.notice, "metadataNotice"))
        groups = list(report.groups)
        if self._show_all.isChecked() and report.all_fields:
            groups.append(MetadataGroup(title="全部字段", fields=report.all_fields))
        for group in groups:
            widgets.append(self._group_widget(group))
        self._fill(widgets)

    def _group_widget(self, group: MetadataGroup) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel(group.title)
        title.setObjectName("metadataGroupTitle")
        layout.addWidget(title)

        for field in group.fields:
            layout.addWidget(self._row(field.label, field.value))
        return container

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
        # 「先藏、再摘、再排队删」的三步顺序，理由（以及那个「闪一下的白窗」是怎么来的）
        # 写在 ``panels.layout`` 里 —— 面包屑与欢迎页同样要拆控件，规矩只有一份。
        clear_layout(self._body_layout)
        for widget in widgets:
            self._body_layout.addWidget(widget)
        self._body_layout.addStretch(1)
        self._scroll.verticalScrollBar().setValue(0)

    # ---- 复制 --------------------------------------------------------------

    def _copy_all(self) -> None:
        report = self._report
        if report is None:
            return
        lines = [report.as_text()]
        if self._show_all.isChecked() and report.all_fields:
            lines.append("")
            lines.append("【全部字段】")
            lines.extend(f"{field.label}：{field.value}" for field in report.all_fields)
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._copy_button.setText("已复制")
        self._hint_timer.start(COPIED_HINT_MS)


__all__ = [
    "COPIED_HINT_MS",
    "LABEL_WIDTH",
    "PANEL_WIDTH",
    "MetadataPanel",
    "panel_style",
]
