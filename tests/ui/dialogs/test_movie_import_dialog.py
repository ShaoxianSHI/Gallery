"""「导入影片」对话框：把用户敲的一整段拆成一行一条（ADR 0063 / 0068）。

只测那个纯函数 —— 它决定「用户粘进来的一坨文字里到底有几部影片」，多切一刀或漏切一刀都会
让导入多一部或少一部。**它不认编号**：哪一条是番号、哪一条是链接由 ``core.events.sources``
说了算（那一条另有一份用例）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QPlainTextEdit  # noqa: E402

from gallery.core.events.kinds import (  # noqa: E402
    SOURCE_BILIBILI,
    SOURCE_IQIYI,
    SOURCE_JAV,
    SOURCE_MANGO,
    SOURCE_TENCENT,
    SOURCE_YOUKU,
    SOURCE_YOUTUBE,
)
from gallery.ui.dialogs.movie_import import MovieImportDialog, split_entries  # noqa: E402


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IPX-660", ["IPX-660"]),
        ("IPX-660\nDSOD-040", ["IPX-660", "DSOD-040"]),
        ("IPX-660, DSOD-040", ["IPX-660", "DSOD-040"]),
        ("IPX-660，DSOD-040", ["IPX-660", "DSOD-040"]),
        ("IPX-660 DSOD-040", ["IPX-660", "DSOD-040"]),
        ("  IPX-660  \n\n  ", ["IPX-660"]),
        ("", []),
        ("   \n  ", []),
        # 链接里的 ``?`` ``=`` ``&`` ``/`` 都不是分隔符 —— 一行一个地址才是常态。
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL0\nBV1GJ411x7h7",
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL0", "BV1GJ411x7h7"],
        ),
    ],
)
def test_entries_are_split_and_trimmed(text: str, expected: list[str]) -> None:
    assert split_entries(text) == expected


def test_duplicate_entries_collapse_keeping_order() -> None:
    assert split_entries("IPX-660\nDSOD-040\nipx-660") == ["IPX-660", "DSOD-040", "ipx-660"]


@pytest.mark.parametrize(
    ("source", "title", "heading", "placeholder"),
    [
        (
            SOURCE_BILIBILI,
            "导入 Bilibili 影片",
            "按 BV 号或链接导入 Bilibili 影片",
            "BV1GJ411x7h7\nhttps://www.bilibili.com/video/BV1GJ411x7h7",
        ),
        (
            SOURCE_YOUTUBE,
            "导入 YouTube 影片",
            "按链接或 videoId 导入 YouTube 影片",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ\ndQw4w9WgXcQ",
        ),
        (
            SOURCE_JAV,
            "导入 JavDB 影片",
            "按番号导入 JavDB 影片",
            "IPX-660\nDSOD-040",
        ),
        (
            SOURCE_IQIYI,
            "导入爱奇艺影片",
            "按链接导入爱奇艺影片",
            "https://www.iqiyi.com/v_19rqxr146g.html\nv_19rqxr146g",
        ),
        (
            SOURCE_TENCENT,
            "导入腾讯视频影片",
            "按链接或 vid 导入腾讯视频影片",
            "https://v.qq.com/x/page/d4102jjf5cm.html\nd4102jjf5cm",
        ),
        (
            SOURCE_YOUKU,
            "导入优酷影片",
            "按链接或编号导入优酷影片",
            "https://v.youku.com/v_show/id_XNjQ3MjkwNTUwNA==.html",
        ),
        (
            SOURCE_MANGO,
            "导入芒果 TV 影片",
            "按链接或编号导入芒果 TV 影片",
            "https://www.mgtv.com/h/14544857.html\n14544857",
        ),
    ],
)
def test_each_source_gets_its_own_copy(
    qt_app, tmp_path: Path, source: str, title: str, heading: str, placeholder: str
) -> None:
    """三个入口打开同一张面板，但标题、说明与例子都跟着来源走（ADR 0072）。"""
    dialog = MovieImportDialog(None, target=tmp_path, source=source)

    assert dialog.windowTitle() == title
    assert heading in [label.text() for label in dialog.findChildren(QLabel)]
    assert dialog.findChild(QPlainTextEdit).placeholderText() == placeholder
