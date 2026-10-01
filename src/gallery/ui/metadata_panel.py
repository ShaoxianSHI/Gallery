"""大图右侧的元数据面板。

推挤式而不是浮层：面板占掉宽度、图片区域缩小并重新适配，因此**任何照片都不会被挡住**。
背景与图片区同色，看起来像同一个取景器旁边的一块信息板。

值一律可选中复制；底部「显示全部字段」展开来源返回的原始键值 —— 精选字段表覆盖不到的
东西（制造商注释、ICC 配置等）在那里都能翻到。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette
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

from gallery.core.metadata import MetadataGroup, MetadataReport
from gallery.ui.detail_view import BACKGROUND

#: 面板宽度。窄了中文标签换行，宽了抢照片的地方。
PANEL_WIDTH = 300

#: 左列标签宽度。
LABEL_WIDTH = 84

#: 「已复制」提示停留多久。
COPIED_HINT_MS = 1400

#: 面板底色与图片区一致，整块看起来就像取景器旁边的一块信息板。
PANEL_BACKGROUND = BACKGROUND

#: 面板里的正文颜色。深底浅字，与图片区一致。
PANEL_TEXT = QColor(236, 236, 238)


def _dark_palette() -> QPalette:
    """面板用的深色调色板。

    样式表管文字颜色，调色板管底色 —— 两者缺一不可：样式表对普通 QWidget 的背景
    不生效，而滚动区视口又是按 ``Base`` 角色自己填色的。
    """
    palette = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(role, PANEL_BACKGROUND)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(role, PANEL_TEXT)
    return palette


_STYLE = f"""
#metadataPanel {{ background: {PANEL_BACKGROUND.name()}; }}
#metadataGroupTitle {{ color: rgba(255,255,255,0.52); font-size: 11px; font-weight: 600; }}
#metadataLabel {{ color: rgba(255,255,255,0.42); font-size: 12px; }}
#metadataValue {{ color: rgba(255,255,255,0.92); font-size: 12px; }}
#metadataNotice {{ color: #ffcf70; font-size: 11px; }}
#metadataEmpty {{ color: rgba(255,255,255,0.45); font-size: 12px; }}
#metadataError {{ color: #ff9b8a; font-size: 12px; }}
QPushButton#metadataButton {{
    color: rgba(255,255,255,0.85);
    background: rgba(255,255,255,0.10);
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 11px;
}}
QPushButton#metadataButton:hover {{ background: rgba(255,255,255,0.18); }}
QCheckBox#metadataToggle {{ color: rgba(255,255,255,0.62); font-size: 11px; }}
"""


class MetadataPanel(QWidget):
    """展示一张照片的元数据。只负责画，读取由主窗口在后台完成。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        self.setFixedWidth(PANEL_WIDTH)
        # 普通 QWidget 不会画样式表里的背景色，必须用调色板 + autoFillBackground，
        # 否则面板会是系统窗口的白底，配着浅色文字完全看不清。
        self._dark_palette = _dark_palette()
        self.setPalette(self._dark_palette)
        # 样式表里的背景对普通 QWidget 不生效（需要 WA_StyledBackground），而调色板
        # 又会被样式表接管 —— 实测两者都靠不住，所以底色自己画，见 paintEvent。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_STYLE)

        self._report: MetadataReport | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 裸声明会级联到所有子控件：让滚动区、内容区与每一行都透明，
        # 露出面板自己画的深色底，而不是各自按系统调色板填白。
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        self._body_layout.addStretch(1)
        # 滚动区视口默认按 Base 角色填色，这里让它也吃深色，别在面板里开一块白。
        self._scroll.setPalette(self._dark_palette)
        self._scroll.viewport().setPalette(self._dark_palette)
        self._scroll.viewport().setAutoFillBackground(True)
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

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt 命名
        """自己画底色。

        样式表背景对普通 QWidget 不生效（除非 ``WA_StyledBackground``），而 macOS 上
        连调色板也可能被原生样式覆盖 —— 面板是浅底深字还是深底浅字，是能不能看清的
        分界线，不值得赌样式系统的脾气，直接填。
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), PANEL_BACKGROUND)

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
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 先摘出控件树再排队删除：deleteLater 只是排队，旧控件在真正销毁前
                # 仍然会画在自己原来的位置上，切图时会看到上一份内容糊在新内容上面。
                widget.setParent(None)
                widget.deleteLater()
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
    "PANEL_BACKGROUND",
    "PANEL_TEXT",
    "PANEL_WIDTH",
    "MetadataPanel",
]
