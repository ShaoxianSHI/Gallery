"""「设置 › 背景 › 自定义背景色…」那个对话框：浅色 / 深色两档背景色各一个色块。

**一个文件都不碰**：这里只把用户挑的那两档颜色读出来，存哪儿、怎么下发归设置菜单
（ADR 0097）。两档各自被限制在自己那一侧：正文色取的是系统那一套，只换底不换字，
挑反了就是浅字压浅底，而那种界面不会有任何地方报错。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gallery.core.library.session import normalize_background
from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 对话框的最小宽度（点）。两行色块 + 底部那一排按钮要摆得开。
MIN_WIDTH = 380

#: 每一档没挑过时，取色器从这个颜色起手（浅色 / 深色各一个）。
STARTER_COLORS = {False: "#f5f5f5", True: "#1c1c1e"}

#: 色块上的字色：底浅压深字、底深压浅字。**这一处故意不跟主题**：它要读的是那块颜色本身。
INK_ON_LIGHT = "#111111"
INK_ON_DARK = "#f2f2f2"

WRONG_SIDE = "浅色背景请选浅色，深色背景请选深色：正文颜色不随背景改变，选反会导致看不清。"

SLOT_LABELS = {False: "浅色模式", True: "深色模式"}


class BackgroundColorDialog(QDialog):
    """挑两档背景色；没挑过的那一档写「默认」，表示跟随系统。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        light: str | None = None,
        dark: str | None = None,
    ) -> None:
        super().__init__(parent)
        #: 键是「是不是深色那一档」，值是 ``#rrggbb`` 或空串（跟随系统）。
        self._values: dict[bool, str] = {False: light or "", True: dark or ""}
        self._swatches: dict[bool, QPushButton] = {}
        self.setWindowTitle("背景色")
        self.setModal(True)
        self.setMinimumWidth(MIN_WIDTH)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build()

    # ---- 对外 --------------------------------------------------------------

    def values(self) -> tuple[str | None, str | None]:
        """``(浅色那一档, 深色那一档)``；没挑过的是 ``None``。"""
        return (self._values[False] or None, self._values[True] or None)

    # ---- 搭建 --------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("两档各选一个颜色；「默认」表示使用系统浅色 / 深色模式。")
        hint.setObjectName("backgroundHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for dark in (False, True):
            layout.addLayout(self._build_row(dark))

        self._error = QLabel("")
        self._error.setObjectName("backgroundError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        layout.addLayout(self._build_buttons())
        self._sync_swatches()

    def _build_row(self, dark: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        name = QLabel(SLOT_LABELS[dark])
        name.setFixedWidth(64)
        row.addWidget(name)

        swatch = QPushButton()
        swatch.setObjectName("backgroundSwatch")
        swatch.setMinimumWidth(180)
        swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        swatch.clicked.connect(lambda _checked=False, target=dark: self._pick(target))
        self._swatches[dark] = swatch
        row.addWidget(swatch, 1)
        return row

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        reset = QPushButton("恢复默认")
        reset.setObjectName("backgroundReset")
        reset.clicked.connect(self._reset)
        self._reset_button = reset
        row.addWidget(reset)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("saveButton")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    # ---- 取色 --------------------------------------------------------------

    def _pick(self, dark: bool) -> None:
        """开系统取色器挑这一档。挑反了留在对话框里说清楚，**不改任何值**。"""
        current = self._values[dark] or STARTER_COLORS[dark]
        chosen = QColorDialog.getColor(QColor(current), self, f"{SLOT_LABELS[dark]}的背景色")
        if not chosen.isValid():
            return
        clean = normalize_background(chosen.name(), dark=dark)
        if clean is None:
            self._error.setText(WRONG_SIDE)
            self._error.show()
            return
        self._values[dark] = clean
        self._error.hide()
        self._sync_swatches()

    def _reset(self) -> None:
        """两档一起恢复默认（跟随系统）。"""
        self._values = {False: "", True: ""}
        self._error.hide()
        self._sync_swatches()

    def _sync_swatches(self) -> None:
        app_theme = theme.current_theme()
        for dark, swatch in self._swatches.items():
            value = self._values[dark]
            swatch.setText(value or "默认")
            swatch.setStyleSheet(_swatch_style(value, app_theme))
            swatch.setToolTip("跟随系统的浅色 / 深色" if not value else f"{value}（点击更换）")

    # ---- 外观 --------------------------------------------------------------

    def _apply_theme(self) -> None:
        self.setStyleSheet(dialog_style(theme.current_theme(), self.palette()))
        if self._swatches:
            self._sync_swatches()


def _swatch_style(value: str, app_theme: AppTheme) -> str:
    """色块自己那一块底色与字色：没挑过就是「默认」那一档淡淡的底。

    **悬停也写在里面**：这一份是设在控件自己身上的，祖上那条 ``QPushButton:hover`` 在悬停时
    会按选择器把它盖成灰色 —— 色块一闪，用户就看不见自己挑的是什么了。
    """
    if not value:
        return (
            f"QPushButton {{ background: {rgba(app_theme.text_at(0.06))};"
            f" color: {rgba(app_theme.text_at(0.62))}; }}"
            f"QPushButton:hover {{ background: {rgba(app_theme.text_at(0.1))}; }}"
        )
    ink = INK_ON_LIGHT if QColor(value).lightnessF() >= 0.5 else INK_ON_DARK
    return (
        f"QPushButton {{ background: {value}; color: {ink}; }}"
        f"QPushButton:hover {{ background: {value}; }}"
    )


def dialog_style(app_theme: AppTheme, palette: QPalette) -> str:
    """对话框的外观：与另外三个设置类对话框同一份底色，按钮同一档。"""
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
    return f"""
    QDialog {{ background: {rgba(app_theme.background)}; }}
    QLabel {{ color: {rgba(app_theme.text_at(0.92))}; font-size: 12px; }}
    QLabel#backgroundHint {{ color: {rgba(app_theme.text_at(0.62))}; }}
    QLabel#backgroundError {{ color: {rgba(app_theme.error)}; }}
    QPushButton {{
        color: {rgba(app_theme.text_at(0.9))};
        background: {rgba(app_theme.text_at(0.12))};
        border: 1px solid transparent;
        border-radius: 6px; padding: 5px 10px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {rgba(app_theme.text_at(0.2))}; }}
    QPushButton:focus {{ border-color: {rgba(highlight)}; }}
    QPushButton#saveButton {{ color: {rgba(highlighted_text)}; background: {rgba(highlight)}; }}
    QPushButton#backgroundSwatch {{ border-radius: 5px; padding: 6px 10px; }}
    QPushButton#backgroundReset {{ font-size: 11px; }}
    """


__all__ = [
    "MIN_WIDTH",
    "BackgroundColorDialog",
    "dialog_style",
]
