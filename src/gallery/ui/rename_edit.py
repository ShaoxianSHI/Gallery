"""行内重命名编辑器。

浮在格子下面那行文件名上的输入框，行为照搬访达：回车提交、Esc 取消、点别处也提交。
照片默认**只选中主干**、不选中扩展名，于是直接打字就是在改名字，不会手滑把 ``.jpg`` 敲掉。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QWidget


class RenameEdit(QLineEdit):
    """重命名用的输入框。"""

    #: 用户确认了新名字。
    committed = Signal(str)
    #: 用户放弃（Esc）。
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._settled = False

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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:
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


__all__ = ["RenameEdit"]
