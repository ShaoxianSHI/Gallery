"""重命名用的输入框 —— **网格与大图页共用这一份**。

网格里它浮在格子下面那行文件名上，大图页里它在窗口标题栏那一行（ADR 0004 / 0025）。行为照搬访达：
回车提交、Esc 取消、点别处也提交，照片默认只选中主干。住在顶层而不是 ``ui/browser/`` 里
（ADR 0006）：两处摆的是同一个控件，语义与外观各只有一份。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QKeyEvent, QShortcutEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 圆角半径（点）。系统那个 macOS 样式画出来的是个带立体边框的方框，浮在名字上很扎眼；
#: 改成一块圆角的输入面，底色见 :func:`field_background`（与外面同一个色，靠边框显出边界）。
RADIUS = 5

#: 左右各留一点，文字不贴着边框。
PADDING = 8


def field_background(app_theme: AppTheme) -> QColor:
    """输入面的底色：**就是窗口那块底色**，不另派生一档。

    原来是「比底色浮一点点」，而浅色下那一档灰恰好就是 macOS 系统窗口的颜色：三处输入面
    （搜索胶囊、外部打开那个药丸、改名输入框）于是从整块白里凸出来。现在读得出来靠的是那一圈
    边框，不靠底色（ADR 0035 决策 1）—— **三处共用这一个函数**，各派生一次早晚会差一档。
    """
    return QColor(app_theme.background)


def rename_edit_style(app_theme: AppTheme) -> str:
    """改名的输入框长什么样。

    **选区颜色交给调色板的 ``Highlight``，不写死任何一个蓝**：标题栏那一份曾经把链接色当高亮
    底，深色下那一片比正文还沉，「主干已经选中了」反而看不出来。其余颜色从主题派生。
    """
    return f"""
    QLineEdit#renameEdit {{
        background: {rgba(field_background(app_theme))};
        color: {rgba(app_theme.text)};
        border: 1px solid {rgba(app_theme.text_at(0.28))};
        border-radius: {RADIUS}px;
        padding: 0 {PADDING}px;
        selection-background-color: palette(highlight);
        selection-color: palette(highlighted-text);
    }}
    """


class RenameEdit(QLineEdit):
    """重命名用的输入框。"""

    #: 用户确认了新名字。
    committed = Signal(str)
    #: 用户放弃（Esc）。
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("renameEdit")
        # 样式挂在这个类上而不是调用方身上：两处摆的是同一个控件，外观就该只有一份。
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self.hide()
        self._settled = False

    def _apply_theme(self) -> None:
        """换过浅色 / 深色之后重新下发一次颜色。"""
        self.setStyleSheet(rename_edit_style(theme.current_theme()))

    # ---- 对外 --------------------------------------------------------------

    def start(self, rect: QRect, text: str, *, keep_suffix: bool) -> None:
        """在 ``rect`` 处开始编辑 ``text``。

        :param keep_suffix: ``True`` 时保留扩展名不选中（照片），``False`` 时全选（相册没有
            「扩展名」这回事）。
        """
        self._settled = False
        self.setGeometry(rect)
        self.setText(text)
        self.show()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        stem = len(text) - len(Path(text).suffix)
        stop = stem if keep_suffix else len(text)
        # 顺序不能反：setCursorPosition 会顺手清掉选区。
        self.setCursorPosition(stop)
        self.setSelection(0, stop)

    def stop(self) -> None:
        """外部强制收起（切层、滚动、关窗口），不发任何信号。"""
        self._settled = True
        self.hide()

    def is_editing(self) -> bool:
        return self.isVisible()

    # ---- 事件 --------------------------------------------------------------

    def event(self, event: QEvent) -> bool:
        # 窗口把 Esc 绑成了「上一级」，所以这里先把 Esc 抢过来（Qt 先问焦点控件要不要接管
        # 这次按键）：接管了就当成普通按键，否则改到一半按 Esc 会跳出上一层，字也白打了。
        if isinstance(event, QShortcutEvent) and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802 - 名字是 Qt 定的
        # 点到别处就提交，和访达一样；比默默丢掉用户敲的字友好。
        super().focusOutEvent(event)
        self._commit()

    # ---- 内部 --------------------------------------------------------------

    def _commit(self) -> None:
        if self._settled:
            return
        self._settled = True
        self.hide()
        self.committed.emit(self.text())

    def _cancel(self) -> None:
        if self._settled:
            return
        self._settled = True
        self.hide()
        self.cancelled.emit()


__all__ = ["PADDING", "RADIUS", "RenameEdit", "field_background", "rename_edit_style"]
