"""「导入影片」的输入面板：粘贴一批编号或链接，写进哪个文件夹（ADR 0063 / 0068）。

**一个文件都不碰**：它只把用户敲的那一份读成一组条目交出去，编号怎么认交给
``core.events.sources.parse_ref``，抓取与写盘走 ``ui.write_ops``（与元数据编辑器、
事件编辑器同一条分工）。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gallery.ui import theme
from gallery.ui.dialogs.event_edit import ERROR_LINES, dialog_style
from gallery.ui.theme import rgba

#: 输入区留几行高。
INPUT_LINES = 8

#: 条目之间的分隔符：换行、空格、中英文逗号都算。
_SEPARATORS = re.compile(r"[\s,，、]+")


def split_entries(text: str) -> list[str]:
    """把用户敲的一整段拆成一行一条，去重并保持顺序（同一部只导一次）。

    **只按分隔符拆，不认编号**：哪一条是番号、哪一条是链接由
    ``core.events.sources.parse_ref`` 说了算（识别规则只该有一处）。
    """
    seen: set[str] = set()
    entries: list[str] = []
    for piece in _SEPARATORS.split(text):
        entry = piece.strip()
        if entry and entry not in seen:
            seen.add(entry)
            entries.append(entry)
    return entries


class MovieImportDialog(QDialog):
    """问「哪些编号或链接、写到哪个文件夹」。:meth:`entries` 给出用户敲的那一批。"""

    def __init__(self, parent: QWidget | None, *, target: Path) -> None:
        super().__init__(parent)
        self._target = Path(target)
        self.setWindowTitle("导入影片")
        self.setModal(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()
        self._build()

    def entries(self) -> list[str]:
        """用户敲的那些条目（拆好、去重）。空 = 什么都没填。"""
        return split_entries(self._input.toPlainText())

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        heading = QLabel("按编号或链接导入影片")
        heading.setObjectName("eventEditTitle")
        layout.addWidget(heading)

        hint = QLabel(
            "每行一个编号或链接（番号、YouTube 的地址、B 站的地址都行），也可以逗号或空格分开。"
            "封面会存进下面这个文件夹。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText(
            "IPX-660\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\nBV1GJ411x7h7"
        )
        self._input.setFixedHeight(self.fontMetrics().height() * INPUT_LINES)
        layout.addWidget(self._input)

        folder = QLabel(f"写入文件夹：{self._target}")
        folder.setObjectName("movieImportFolder")
        folder.setWordWrap(True)
        folder.setToolTip(str(self._target))
        layout.addWidget(folder)

        self._error = QLabel()
        self._error.setObjectName("eventEditError")
        self._error.setWordWrap(True)
        self._error.setFixedHeight(self.fontMetrics().height() * ERROR_LINES)
        layout.addWidget(self._error)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        start = QPushButton("导入")
        start.setObjectName("saveButton")
        start.setDefault(True)
        start.clicked.connect(self._on_accept)
        row.addWidget(cancel)
        row.addWidget(start)
        layout.addLayout(row)

        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_accept(self) -> None:
        if not self.entries():
            self._error.setText("先填至少一个编号或链接，例如 IPX-660 或 https://youtu.be/…。")
            return
        self.accept()

    def _apply_theme(self) -> None:
        base = dialog_style(theme.current_theme(), self.palette())
        fill = rgba(theme.current_theme().text_at(0.08))
        self.setStyleSheet(
            base
            + f"""
    QPlainTextEdit {{
        color: {rgba(theme.current_theme().text_at(0.92))};
        background: {fill};
        border: 1px solid {rgba(theme.current_theme().text_at(0.18))};
        border-radius: 5px;
        padding: 4px 6px;
        font-size: 12px;
    }}
    QLabel#movieImportFolder {{
        color: {rgba(theme.current_theme().text_at(0.62))};
        font-size: 11px;
    }}
    """
        )


__all__ = ["INPUT_LINES", "MovieImportDialog", "split_entries"]
