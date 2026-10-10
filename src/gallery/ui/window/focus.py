"""大图页正看着哪一行 —— 窗口上的只读面（ADR 0060 与 :mod:`~gallery.ui.window.state.focus` 分开）。

``FocusMixin._current_row`` 有 22 处测试在读，所以属性留着；**只读**：写入口是
:class:`FocusState` 的那几个具名方法，绕过它们直接赋值就是绕过这一族。
"""

from __future__ import annotations

from gallery.ui.window.navigation import EntryKey
from gallery.ui.window.state.focus import FocusState

__all__ = ["FocusMixin"]


class FocusMixin:
    def _init_focus(self) -> None:
        """建这一族的状态；``MainWindow.__init__`` 里只留这一句。"""
        self._focus_state = FocusState()

    @property
    def _current_row(self) -> int:
        """大图页正看着第几行（``-1`` = 没在看）。"""
        return self._focus_state.current

    @property
    def _pending_focus(self) -> EntryKey | None:
        """重排之后要回到哪一格（分组重进，ADR 0013）。"""
        return self._focus_state.pending
