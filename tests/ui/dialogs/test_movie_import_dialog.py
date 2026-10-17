"""「导入影片」对话框：把用户敲的一整段拆成番号（ADR 0063）。

只测那个纯函数 —— 它决定「用户粘进来的一坨文字里到底有几部影片」，多切一刀或漏切一刀都会
让导入多一部或少一部。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gallery.ui.dialogs.movie_import import split_codes  # noqa: E402


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
    ],
)
def test_codes_are_split_and_trimmed(text: str, expected: list[str]) -> None:
    assert split_codes(text) == expected


def test_duplicate_codes_collapse_keeping_order() -> None:
    assert split_codes("IPX-660\nDSOD-040\nipx-660") == ["IPX-660", "DSOD-040", "ipx-660"]
