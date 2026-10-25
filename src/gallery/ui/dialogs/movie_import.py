"""「导入影片」的输入面板：粘贴一批编号或链接，写进哪个文件夹（ADR 0063 / 0068 / 0072）。

**一个文件都不碰**：它只把用户敲的那一份读成一组条目交出去，编号怎么认交给
``core.events.sources.parse_ref``，抓取与写盘走 ``ui.write_ops``（与元数据编辑器、
事件编辑器同一条分工）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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

from gallery.core.events.kinds import (
    SOURCE_BILIBILI,
    SOURCE_DOUYIN,
    SOURCE_IQIYI,
    SOURCE_JAV,
    SOURCE_MANGO,
    SOURCE_PORNHUB,
    SOURCE_TENCENT,
    SOURCE_TIKTOK,
    SOURCE_YOUKU,
    SOURCE_YOUTUBE,
)
from gallery.core.i18n import t
from gallery.ui import theme
from gallery.ui.dialogs.event_edit import ERROR_LINES, dialog_style
from gallery.ui.theme import rgba

#: 输入区留几行高。
INPUT_LINES = 8

#: 条目之间的分隔符：换行、空格、中英文逗号都算。
_SEPARATORS = re.compile(r"[\s,，、]+")


@dataclass(frozen=True, slots=True)
class SourceCopy:
    """一个导入来源在面板上的那几句话。**只有提示不同，拆条与识别三条路共用一份。**"""

    title: str
    heading: str
    hint: str
    placeholder: str


#: 八个入口各自的说法。进例子的东西必须和这个来源对得上：B 站给 BV 号，YouTube 给视频地址，
#: JavDB 给番号 —— 否则用户会拿另一个站的编号去试，然后在抓取失败时才看到原因。
def _copy_for(source: str) -> SourceCopy:
    """把目录里的三句话取成这一来源的文案。**取的时候才问语言** —— 模块级不能提前定下来。"""
    copy = _SOURCE_COPY[source]
    return SourceCopy(
        title=t(copy.title),
        heading=t(copy.heading),
        hint=t(copy.hint),
        placeholder=copy.placeholder,
    )


_SOURCE_COPY: Final[dict[str, SourceCopy]] = {
    SOURCE_BILIBILI: SourceCopy(
        title="dialog.movie.bilibili.title",
        heading="dialog.movie.bilibili.heading",
        hint="dialog.movie.bilibili.hint",
        placeholder="BV1GJ411x7h7\nhttps://www.bilibili.com/video/BV1GJ411x7h7",
    ),
    SOURCE_YOUTUBE: SourceCopy(
        title="dialog.movie.youtube.title",
        heading="dialog.movie.youtube.heading",
        hint="dialog.movie.youtube.hint",
        placeholder="https://www.youtube.com/watch?v=dQw4w9WgXcQ\ndQw4w9WgXcQ",
    ),
    SOURCE_DOUYIN: SourceCopy(
        title="dialog.movie.douyin.title",
        heading="dialog.movie.douyin.heading",
        hint="dialog.movie.douyin.hint",
        placeholder="https://www.douyin.com/video/7345678901234567890\nhttps://v.douyin.com/iRNBho6u/",
    ),
    SOURCE_TIKTOK: SourceCopy(
        title="dialog.movie.tiktok.title",
        heading="dialog.movie.tiktok.heading",
        hint="dialog.movie.tiktok.hint",
        placeholder="https://www.tiktok.com/@scout2015/video/6718335390845095173\n"
        "https://vm.tiktok.com/ZMabc123/",
    ),
    SOURCE_JAV: SourceCopy(
        title="dialog.movie.javdb.title",
        heading="dialog.movie.javdb.heading",
        hint="dialog.movie.javdb.hint",
        placeholder="IPX-660\nDSOD-040",
    ),
    SOURCE_IQIYI: SourceCopy(
        title="dialog.movie.iqiyi.title",
        heading="dialog.movie.iqiyi.heading",
        hint="dialog.movie.iqiyi.hint",
        placeholder="https://www.iqiyi.com/v_19rqxr146g.html\nv_19rqxr146g",
    ),
    SOURCE_TENCENT: SourceCopy(
        title="dialog.movie.tencent.title",
        heading="dialog.movie.tencent.heading",
        hint="dialog.movie.tencent.hint",
        placeholder="https://v.qq.com/x/page/d4102jjf5cm.html\nd4102jjf5cm",
    ),
    SOURCE_YOUKU: SourceCopy(
        title="dialog.movie.youku.title",
        heading="dialog.movie.youku.heading",
        hint="dialog.movie.youku.hint",
        placeholder="https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA==.html",
    ),
    SOURCE_MANGO: SourceCopy(
        title="dialog.movie.mango.title",
        heading="dialog.movie.mango.heading",
        hint="dialog.movie.mango.hint",
        placeholder="https://www.mgtv.com/b/294541/3779644.html",
    ),
    SOURCE_PORNHUB: SourceCopy(
        title="dialog.movie.pornhub.title",
        heading="dialog.movie.pornhub.heading",
        hint="dialog.movie.pornhub.hint",
        placeholder="https://www.pornhub.com/view_video.php?viewkey=ph5af5fef7c2aa7\nph5af5fef7c2aa7",
    ),
}


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

    def __init__(self, parent: QWidget | None, *, target: Path, source: str) -> None:
        super().__init__(parent)
        self._target = Path(target)
        self._copy = _copy_for(source)
        self.setWindowTitle(self._copy.title)
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

        heading = QLabel(self._copy.heading)
        heading.setObjectName("eventEditTitle")
        layout.addWidget(heading)

        hint = QLabel(self._copy.hint)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText(self._copy.placeholder)
        self._input.setFixedHeight(self.fontMetrics().height() * INPUT_LINES)
        layout.addWidget(self._input)

        folder = QLabel(t("dialog.movie.folder", path=self._target))
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
        cancel = QPushButton(t("dialog.cancel"))
        cancel.setObjectName("cancelButton")
        cancel.clicked.connect(self.reject)
        start = QPushButton(t("dialog.movie.start"))
        start.setObjectName("saveButton")
        start.setDefault(True)
        start.clicked.connect(self._on_accept)
        row.addWidget(cancel)
        row.addWidget(start)
        layout.addLayout(row)

        close_action = QAction(t("dialog.close"), self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.reject)
        self.addAction(close_action)
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_accept(self) -> None:
        if not self.entries():
            example = self._copy.placeholder.splitlines()[0]
            self._error.setText(t("dialog.movie.example_required", example=example))
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
