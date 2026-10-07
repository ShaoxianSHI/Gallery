"""「复制」⌘D 的测试（ADR 0044）：每一项复制到**它自己所在的那一层**。

与 :mod:`tests.core.metadata.test_paste` 互补：那一组钉「粘进一个指定的目录」，这里钉
「目标目录不用挑」—— 一次调用里可能有好几个父目录，于是「每个目录只扫一次」那条规矩在这条
路上也要成立。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from gallery.core.metadata.journal import ACTION_PASTE, RESULT_OK, read_records
from gallery.core.metadata.paste import duplicate
from gallery.core.metadata.undo import next_undo_label


def _jpeg(path: Path) -> None:
    Image.new("RGB", (8, 6), (1, 2, 3)).save(path)


def test_each_item_lands_next_to_itself(library: Path) -> None:
    """目标就是每一项自己的父目录 —— 所以一次调用可以横跨几个相册。"""
    first = library / "京都" / "一张.jpg"
    second = library / "奈良" / "另有一张.jpg"
    _jpeg(second)

    result = duplicate(library, [first, second])

    assert [item.created for item in result.pasted] == [
        library / "京都" / "一张的副本.jpg",
        library / "奈良" / "另有一张的副本.jpg",
    ]


def test_the_sources_stay_exactly_where_they_were(library: Path) -> None:
    """只增不改：原件一个字节都不动（红线 1）。"""
    source = library / "京都" / "一张.jpg"
    before = source.read_bytes()

    duplicate(library, [source])

    assert source.read_bytes() == before
    assert source.exists()


def test_two_items_in_the_same_folder_do_not_collide(library: Path) -> None:
    """同一目录里的多项共用一份「已被占掉的名字」表：第二项不能挑到第一项刚用的名字。

    逐项 ``exists()`` 在这里也是对的（第一项已经落盘），但**一次 scandir** 才是这批的正解 ——
    一次调用跨几个父目录时，那一份表要每目录各建一次。
    """
    source = library / "京都" / "一张.jpg"

    result = duplicate(library, [source, source])

    assert [item.created.name for item in result.pasted] == [
        "一张的副本.jpg",
        "一张的副本 2.jpg",
    ]


def test_a_missing_source_is_reported_not_swallowed(library: Path) -> None:
    """有一项源不在了就要说出来，不能静默少一个：横幅靠它说明少了什么。"""
    source = library / "京都" / "一张.jpg"

    result = duplicate(library, [source, library / "京都" / "没来过.jpg"])

    assert [item.created.name for item in result.pasted] == ["一张的副本.jpg"]
    assert [failure.name for failure in result.failures] == ["没来过.jpg"]


def test_cancelling_stops_between_items(library: Path) -> None:
    """取消只在**项与项之间**生效：粘到一半停下来会留下半截文件（ADR 0018）。"""
    first = library / "京都" / "一张.jpg"
    second = library / "京都" / "另一张.jpg"
    calls = 0

    def should_cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = duplicate(library, [first, second], should_cancel=should_cancel)

    assert len(result.pasted) == 1
    assert (library / "京都" / "一张的副本.jpg").exists()


def test_duplicating_never_enters_the_undo_stack(library: Path) -> None:
    """**撤销一次复制就是删文件**（红线 1），所以这一条记了账却不进撤销栈 —— 与粘贴同一套
    （``ACTION_PASTE`` 在 ``undo`` 的 ``_NEVER_UNDOABLE`` 里）。钉的是「真的没进栈」。
    """
    source = library / "京都" / "一张.jpg"

    duplicate(library, [source])

    assert next_undo_label(library) is None


def test_the_copies_are_journalled(library: Path) -> None:
    """记账是必须的：它是用户唯一能查「那天到底动了什么」的痕迹。"""
    source = library / "京都" / "一张.jpg"

    duplicate(library, [source])

    records = [r for r in read_records(library) if r.action == ACTION_PASTE]
    assert [(r.source, r.target, r.result) for r in records] == [
        (source, library / "京都" / "一张的副本.jpg", RESULT_OK)
    ]


def test_an_album_is_duplicated_with_everything_inside(library: Path) -> None:
    """造相册的副本是整棵复制，不是空壳：用户点它是为了留一份整个旅行。"""
    album = library / "京都" / "2025"
    (album / "子相册").mkdir(parents=True)
    _jpeg(album / "子相册" / "一张.jpg")

    result = duplicate(library, [album])

    created = library / "京都" / "2025的副本"
    assert [item.created for item in result.pasted] == [created]
    assert (created / "子相册" / "一张.jpg").exists()
