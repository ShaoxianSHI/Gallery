"""红线 4：**五个写操作在记账失败时都必须如实说「撤销不了」**。

``append_record`` 写不进去时返回 ``False``（文件已经改好了），它必须一路传到结果对象上。"""

from __future__ import annotations

from pathlib import Path

from gallery.core.metadata.album import create_album
from gallery.core.metadata.paste import copy_into
from gallery.core.metadata.rename import rename_entry
from gallery.core.metadata.transfer import move_into
from gallery.core.metadata.trash import trash_entries


def test_rename(tmp_path: Path, unwritable_journal: Path, photo) -> None:
    result = rename_entry(unwritable_journal, photo, "海边日落")

    assert result.renamed is True, "文件已经改好了，不能因为记不上账就说没改成"
    assert result.journaled is False
    assert (unwritable_journal / "京都" / "海边日落.jpg").exists()


def test_trash(tmp_path: Path, unwritable_journal: Path, photo) -> None:
    result = trash_entries(unwritable_journal, [photo])

    assert result.journaled is False
    assert result.moved, "照片确实已经移进废纸篓了"
    assert not photo.path.exists()


def test_paste(tmp_path: Path, unwritable_journal: Path, photo) -> None:
    result = copy_into(unwritable_journal, unwritable_journal / "奈良", [photo.path])

    assert result.journaled is False
    assert (unwritable_journal / "奈良" / "一张.jpg").is_file(), "源文件原地不动，目标多一份"


def test_move_to_another_album(tmp_path: Path, unwritable_journal: Path, photo) -> None:
    result = move_into(unwritable_journal, unwritable_journal / "奈良", [photo.path])

    assert result.journaled is False
    assert (unwritable_journal / "奈良" / "一张.jpg").is_file()
    assert not photo.path.exists()


def test_create_album(tmp_path: Path, unwritable_journal: Path) -> None:
    result = create_album(unwritable_journal, unwritable_journal / "京都")

    assert result.journaled is False
    assert result.album.path.is_dir(), "空文件夹已经建出来了"


def test_the_unwritable_journal_fixture_actually_blocks_writing(
    unwritable_journal: Path,
) -> None:
    """fixture 本身要可信：不然上面五条在「其实能记账」的情况下也全绿。"""
    from gallery.core.metadata.journal import append_record

    assert (
        append_record(
            unwritable_journal,
            __import__("gallery.core.metadata.journal", fromlist=["WriteRecord"]).WriteRecord(
                action="rename", source=Path("/a"), target=Path("/b"), result="ok"
            ),
        )
        is False
    )
