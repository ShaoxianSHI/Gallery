"""「导入影片」对话框：把用户敲的一整段拆成一行一条（ADR 0063 / 0068）。

只测那个纯函数 —— 它决定「用户粘进来的一坨文字里到底有几部影片」，多切一刀或漏切一刀都会
让导入多一部或少一部。**它不认编号**：哪一条是番号、哪一条是链接由 ``core.events.sources``
说了算（那一条另有一份用例）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gallery.ui.dialogs.movie_import import split_entries  # noqa: E402


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
