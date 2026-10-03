"""重命名用的输入框 —— **网格与大图页共用这一份**。

两处改名的入口各自摆一个输入框：网格里它浮在格子下面那行文件名上（ADR 0004 决策 6），
大图页里它就摆在窗口标题栏那一行（ADR 0025）。行为照搬访达：回车提交、Esc 取消、
点别处也提交。照片默认**只选中主干**、不选中扩展名，于是直接打字就是在改名字，
不会手滑把 ``.jpg`` 敲掉。

住在本层顶层而不是 ``ui/browser/`` 里，判据同 ADR 0006：改了它会不会牵连另一个包 —— 会。
另写一份的那条路已经被否决了：同一套语义抄两遍，抄的那份迟早和第一份不一样 ——
**外观也一样**：两处摆的是同一个控件，所以圆角、底色、文字、高亮都不必各调一遍，
改 :func:`rename_edit_style` 一处，两边一起变。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QKeyEvent, QShortcutEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from gallery.ui import theme
from gallery.ui.theme import AppTheme, rgba

#: 圆角半径（点）。系统那个 macOS 样式画出来的是个带立体边框的方框，浮在名字上很扎眼；
#: 改成一块微微凸起的圆角面 —— 与 macOS 菜单、访达的行内编辑一个气质。
RADIUS = 5

#: 左右各留一点，文字不贴着边框。
PADDING = 8


def _field_background(app_theme: AppTheme) -> QColor:
    """输入框的底色：比窗口那块底色「浮」一点点。

    底色只有一个来源（ADR 0005），所以这里是从它派生的，不写死任何颜色：
    深色下提亮一点、浅色下压暗一点，两种主题下都是「一块微微凸起的面」。
    """
    color = QColor(app_theme.background)
    return color.lighter(118) if app_theme.is_dark else color.darker(106)


def rename_edit_style(app_theme: AppTheme) -> str:
    """改名的输入框长什么样。

    **选区颜色交给调色板的 ``Highlight``，不写死任何一个蓝。** 这一块恰恰是最容易被
    写坏的地方：原先标题栏那一份把链接色当高亮底，深色下那一片比正文还沉，
    「主干已经选中了」这件事反而看不出来。走调色板则永远与系统、与网格里那一处一致 ——
    而网格里那一处走的本来就是系统样式。

    其余颜色全部从主题派生（与 ``panels.welcome`` 同一套办法）。
    """
    return f"""
    QLineEdit#renameEdit {{
        background: {rgba(_field_background(app_theme))};
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

        :param keep_suffix: ``True`` 时保留扩展名不选中（照片），
            ``False`` 时全选（相册没有「扩展名」这回事）。
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
        # 窗口把 Esc 绑成了「上一级」，所以这里先把 Esc 抢过来：Qt 会先问焦点控件要不要
        # 接管这次按键（ShortcutOverride），接管了就当成普通按键交给 keyPressEvent，
        # 不会走 QAction —— 否则改到一半按 Esc 会跳出上一层，输入框里的字也白打了。
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


__all__ = ["PADDING", "RADIUS", "RenameEdit", "rename_edit_style"]
