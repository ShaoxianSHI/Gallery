"""撤销栈的测试（ADR 0008）：改名与移到废纸篓共用同一摞，而它是照着 ``journal/`` 重放出来的。

四件事：顺序（栈顶最新）、粒度（删十张只值一次 ⌘Z）、韧性（失败的留在栈上可重试）、库隔离。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.journal import ACTION_RENAME, ACTION_TRASH, read_records
from gallery.core.metadata.rename import rename_entry
from gallery.core.metadata.trash import trash_entries
from gallery.core.metadata.undo import next_undo_label, pending_undo_runs, undo_last_write

_CONTENT = b"placeholder, not really a jpeg"


@pytest.fixture
def two_photos(tmp_path: Path) -> tuple[PhotoRef, PhotoRef]:
    first = tmp_path / "一.jpg"
    second = tmp_path / "二.jpg"
    first.write_bytes(_CONTENT)
    second.write_bytes(_CONTENT)
    return PhotoRef.from_path(first), PhotoRef.from_path(second)


def test_nothing_to_undo_on_a_fresh_library(tmp_path: Path) -> None:
    assert undo_last_write(tmp_path) is None
    assert next_undo_label(tmp_path) is None


def test_undo_takes_back_the_most_recent_write(tmp_path: Path, two_photos) -> None:
    first, second = two_photos
    rename_entry(tmp_path, first, "改名过")
    trash_entries(tmp_path, [second])

    result = undo_last_write(tmp_path)

    assert result is not None
    assert result.action == ACTION_TRASH
    assert result.entries[0].path == second.path
    assert second.path.exists(), "⌘Z 撤的是栈顶，也就是那次删除"
    # 改名还欠着一次撤销：它没被碰过，名字也仍然是新的那一个。
    assert (tmp_path / "改名过.jpg").exists()
    assert not first.path.exists()
    assert len(pending_undo_runs(tmp_path)) == 1


def test_undo_unwinds_mixed_operations_in_reverse_order(tmp_path: Path, two_photos) -> None:
    first, second = two_photos
    trash_entries(tmp_path, [first])
    rename_entry(tmp_path, second, "改名过")

    assert undo_last_write(tmp_path).action == ACTION_RENAME
    assert undo_last_write(tmp_path).action == ACTION_TRASH
    assert undo_last_write(tmp_path) is None
    assert first.path.exists()
    assert (tmp_path / "二.jpg").exists()


def test_label_follows_the_top_of_the_stack(tmp_path: Path, two_photos) -> None:
    """菜单要能提前告诉用户 ⌘Z 会撤掉什么。"""
    first, second = two_photos
    rename_entry(tmp_path, first, "改名过")
    assert next_undo_label(tmp_path) == "重命名"

    trash_entries(tmp_path, [second])
    assert next_undo_label(tmp_path) == "移到废纸篓"

    undo_last_write(tmp_path)
    assert next_undo_label(tmp_path) == "重命名"


def test_one_batch_is_undone_in_one_go(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    photos = [PhotoRef.from_path(make_photo(f"{index}.jpg")) for index in range(3)]
    trash_entries(tmp_path, photos)

    result = undo_last_write(tmp_path)

    assert result is not None
    assert len(result.entries) == 3
    assert all(photo.path.exists() for photo in photos)
    assert len(pending_undo_runs(tmp_path)) == 0


def test_two_batches_are_undone_one_after_another(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    first = PhotoRef.from_path(make_photo("甲.jpg"))
    second = PhotoRef.from_path(make_photo("乙.jpg"))
    trash_entries(tmp_path, [first])
    trash_entries(tmp_path, [second])

    undo_last_write(tmp_path)
    assert not first.path.exists()
    assert second.path.exists()

    undo_last_write(tmp_path)
    assert first.path.exists()


def test_album_undo_comes_back_as_one_entry(tmp_path: Path, make_photo) -> None:
    make_photo("第一张.jpg", directory=tmp_path / "2025 苏州")
    album = FolderRef.from_path(tmp_path / "2025 苏州")
    trash_entries(tmp_path, [album])

    result = undo_last_write(tmp_path)

    assert result is not None
    assert result.replaced is None, "取回来的条目是插回网格，不是替换某一项"
    assert isinstance(result.entries[0], FolderRef)
    assert (tmp_path / "2025 苏州" / "第一张.jpg").exists()


def test_a_failed_restore_stays_on_the_stack(
    tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """废纸篓里少了一张就少了一张，但其余的照取；再按一次 ⌘Z 还能重试那一张。"""
    kept = PhotoRef.from_path(make_photo("还在.jpg"))
    lost = PhotoRef.from_path(make_photo("丢了.jpg"))
    trash_entries(tmp_path, [kept, lost])
    (fake_trash / "丢了.jpg").unlink()

    first = undo_last_write(tmp_path)

    assert first is not None
    assert [entry.name for entry in first.entries] == ["还在.jpg"]
    assert [failure.name for failure in first.failures] == ["丢了.jpg"]
    assert kept.path.exists()

    second = undo_last_write(tmp_path)

    assert second is not None
    assert [failure.name for failure in second.failures] == ["丢了.jpg"], "失败的仍在栈上"
    assert (tmp_path / "还在.jpg").exists(), "成功的不会被取第二遍"
    assert len(pending_undo_runs(tmp_path)) == 1


def test_restore_is_recorded_so_the_stack_stays_consistent(
    tmp_path: Path, fake_trash: Path, two_photos
) -> None:
    first, _second = two_photos
    trash_entries(tmp_path, [first])

    undo_last_write(tmp_path)

    actions = [record.action for record in read_records(tmp_path)]
    assert actions == [ACTION_TRASH, "restore"]


def test_another_library_never_leaks_into_the_stack(tmp_path: Path, fake_trash: Path) -> None:
    """撤销以「库」为单位：在 B 库按 ⌘Z 不该把 A 库删掉的东西取回来。"""
    library = tmp_path / "相册甲"
    other = tmp_path / "相册乙"
    library.mkdir()
    other.mkdir()
    (library / "一.jpg").write_bytes(_CONTENT)
    (other / "二.jpg").write_bytes(_CONTENT)
    trash_entries(library, [PhotoRef.from_path(library / "一.jpg")])

    result = undo_last_write(other)

    assert result is None
    assert not (library / "一.jpg").exists()
    assert (fake_trash / "一.jpg").exists(), "另一个库的栈不该被动到"
