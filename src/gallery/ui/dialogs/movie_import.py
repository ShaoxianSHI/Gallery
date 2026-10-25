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
_SOURCE_COPY: Final[dict[str, SourceCopy]] = {
    SOURCE_BILIBILI: SourceCopy(
        title="导入 Bilibili 影片",
        heading="按 BV 号或链接导入 Bilibili 影片",
        hint="每行一个 BV 号或 B 站视频地址（b23.tv 短链请先在浏览器中打开，再粘贴完整地址）。"
        "封面保存到下方文件夹。",
        placeholder="BV1GJ411x7h7\nhttps://www.bilibili.com/video/BV1GJ411x7h7",
    ),
    SOURCE_YOUTUBE: SourceCopy(
        title="导入 YouTube 影片",
        heading="按链接或 videoId 导入 YouTube 影片",
        hint="每行一个 YouTube 地址或 11 位 videoId（youtu.be 与 watch 链接都行）。"
        "封面保存到下方文件夹。",
        placeholder="https://www.youtube.com/watch?v=dQw4w9WgXcQ\ndQw4w9WgXcQ",
    ),
    SOURCE_DOUYIN: SourceCopy(
        title="导入抖音影片",
        heading="按链接或编号导入抖音影片",
        hint="每行一个抖音视频地址或分享短链（v.douyin.com 开头），也可以直接填视频编号。"
        "短链需联网展开一次，才能确定对应的视频。封面保存到下方文件夹。",
        placeholder="https://www.douyin.com/video/7345678901234567890\nhttps://v.douyin.com/iRNBho6u/",
    ),
    SOURCE_TIKTOK: SourceCopy(
        title="导入 TikTok 影片",
        heading="按链接或编号导入 TikTok 影片",
        hint="每行一个 TikTok 视频地址或分享链接（vm.tiktok.com、tiktok.com/t 开头都行），"
        "也可以直接填视频 id。封面保存到下方文件夹。",
        placeholder="https://www.tiktok.com/@scout2015/video/6718335390845095173\n"
        "https://vm.tiktok.com/ZMabc123/",
    ),
    SOURCE_JAV: SourceCopy(
        title="导入 JavDB 影片",
        heading="按番号导入 JavDB 影片",
        hint="每行一个番号，例如 IPX-660；大小写与连字符都可以按习惯写。封面保存到下方文件夹。",
        placeholder="IPX-660\nDSOD-040",
    ),
    SOURCE_IQIYI: SourceCopy(
        title="导入爱奇艺影片",
        heading="按链接导入爱奇艺影片",
        hint="每行一个爱奇艺视频地址，地址里 v_ 后面那一串就是编号；整部剧的专辑地址（a_ 开头）"
        "不受支持，请打开某一集后再复制。封面保存到下方文件夹。",
        placeholder="https://www.iqiyi.com/v_19rqxr146g.html\nv_19rqxr146g",
    ),
    SOURCE_TENCENT: SourceCopy(
        title="导入腾讯视频影片",
        heading="按链接或 vid 导入腾讯视频影片",
        hint="每行一个腾讯视频地址或 11 位 vid；整部剧的合集地址不受支持，请打开某一集后再复制。"
        "封面保存到下方文件夹。",
        placeholder="https://v.qq.com/x/page/d4102jjf5cm.html\nd4102jjf5cm",
    ),
    SOURCE_YOUKU: SourceCopy(
        title="导入优酷影片",
        heading="按链接或编号导入优酷影片",
        hint="每行一个优酷视频地址或编号（X 开头那一串）。封面保存到下方文件夹。",
        placeholder="https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA==.html",
    ),
    SOURCE_MANGO: SourceCopy(
        title="导入芒果 TV 影片",
        heading="按链接或编号导入芒果 TV 影片",
        hint="每行一个芒果 TV 视频地址（/b/… 那一页）或单集编号。整部剧的页面（/h/ 开头）"
        "不行，请点开某一集再复制。有的影片芒果未提供上映日期，该字段将留空。"
        "封面保存到下方文件夹。",
        placeholder="https://www.mgtv.com/b/294541/3779644.html",
    ),
    SOURCE_PORNHUB: SourceCopy(
        title="导入 Pornhub 影片",
        heading="按链接或编号导入 Pornhub 影片",
        hint="每行一个 Pornhub 视频地址，或地址里 viewkey= 后面那一串编号"
        "（view_video.php 与 /embed/ 两种地址都行）。作者页与合集页请先打开某一部再复制。"
        "封面保存到下方文件夹。",
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
        self._copy = _SOURCE_COPY[source]
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
            example = self._copy.placeholder.splitlines()[0]
            self._error.setText(f"请至少填写一个编号或链接，例如 {example}。")
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
