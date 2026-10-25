"""移到系统废纸篓的测试。

四类风险：内容一个字节不少、越权的一律不许动、恢复时**绝不覆盖**、批量部分失败每条都记日志。"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.errors import DeleteError
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.journal import (
    ACTION_RESTORE,
    ACTION_TRASH,
    RESULT_FAILED,
    RESULT_OK,
    read_records,
)
from gallery.core.metadata.trash import (
    move_to_trash,
    restore_entries,
    trash_dir_for,
    trash_entries,
)

_CONTENT = b"\xff\xd8\xff\xdb placeholder, not really a jpeg"

#: 废纸篓里原本就有的东西：用户的，谁也不许动它。
_OTHER = b"already in the trash"

#: 取回失败后，原位置上出现的另一个文件。
_LATER = b"something else moved in"


@pytest.fixture
def photo(tmp_path: Path) -> PhotoRef:
    path = tmp_path / "海边.jpg"
    path.write_bytes(_CONTENT)
    return PhotoRef.from_path(path)


def test_trash_dir_next_to_home_is_the_home_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """和 home 在同一个卷上时，废纸篓就在 home 里（访达的规则）。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    picture = tmp_path / "a.jpg"
    picture.write_bytes(_CONTENT)

    assert trash_dir_for(picture) == tmp_path / ".Trash"


def test_trash_dir_on_another_volume_lives_on_that_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不在同一个卷上时，废纸篓跟着卷走（外接盘 / U 盘就是这么放的）。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr("gallery.core.metadata.trash._same_volume", lambda _a, _b: False)

    assert trash_dir_for(tmp_path / "照片.jpg") == (
        Path(tmp_path.anchor) / ".Trashes" / str(os.getuid())
    )


def test_undecidable_volume_falls_back_to_the_home_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """判断不了卷的时候宁可按同卷处理，也不要把文件搬到猜出来的路径上。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOENT, "断线的外接盘")

    monkeypatch.setattr(Path, "stat", explode)
    assert trash_dir_for(tmp_path / "照片.jpg") == tmp_path / ".Trash"


def test_file_is_moved_into_the_trash_with_its_bytes_intact(
    tmp_path: Path, fake_trash: Path, photo: PhotoRef
) -> None:
    trashed = move_to_trash(photo.path)

    assert not photo.path.exists(), "原位置必须空出来，否则它还在相册里"
    assert trashed == fake_trash / "海边.jpg"
    assert trashed.read_bytes() == _CONTENT


def test_existing_name_in_the_trash_is_never_overwritten(tmp_path: Path, fake_trash: Path) -> None:
    """废纸篓里可能有用户自己扔进去的东西还没还原，绝不能盖掉它。"""
    fake_trash.mkdir(parents=True)
    keeper = fake_trash / "海边.jpg"
    keeper.write_bytes(_OTHER)
    second = tmp_path / "海边.jpg"
    second.write_bytes(_CONTENT)

    trashed = move_to_trash(second)

    assert trashed != keeper
    assert trashed.name == "海边 2.jpg"
    assert keeper.read_bytes() == _OTHER


def test_album_is_moved_with_its_whole_tree(
    tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("第一张.jpg", directory=tmp_path / "2025 苏州")
    album = FolderRef.from_path(tmp_path / "2025 苏州")

    trashed = move_to_trash(album.path)

    assert not (tmp_path / "2025 苏州").exists()
    assert (trashed / "第一张.jpg").exists()


def test_library_root_cannot_be_trashed(tmp_path: Path, fake_trash: Path) -> None:
    with pytest.raises(DeleteError) as caught:
        trash_entries(tmp_path, [FolderRef.from_path(tmp_path)])

    assert "库本身" in caught.value.message
    assert tmp_path.is_dir()


def test_config_dir_cannot_be_trashed(tmp_path: Path, fake_trash: Path) -> None:
    config = paths.config_dir(tmp_path)
    config.mkdir()

    with pytest.raises(DeleteError) as caught:
        trash_entries(tmp_path, [FolderRef.from_path(config)])

    assert "配置目录" in caught.value.message
    assert config.is_dir()


def test_anything_outside_the_library_is_refused(tmp_path: Path, fake_trash: Path) -> None:
    outside = tmp_path.parent / "别人的照片.jpg"
    outside.write_bytes(_CONTENT)

    with pytest.raises(DeleteError) as caught:
        trash_entries(tmp_path, [PhotoRef.from_path(outside)])

    assert "只能移除库中的项目" in caught.value.message
    assert outside.exists()


def test_guard_runs_before_anything_is_moved(
    tmp_path: Path, fake_trash: Path, photo: PhotoRef
) -> None:
    """整批先验一遍再动手：越权的那一项不能让同批的其它项跟着消失。"""
    config = paths.config_dir(tmp_path)
    config.mkdir()

    with pytest.raises(DeleteError):
        trash_entries(tmp_path, [photo, FolderRef.from_path(config)])

    assert photo.path.exists(), "校验没过就一个都不许动"


def test_every_item_gets_its_own_record_sharing_one_batch(
    tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """批量删除记多条（可能有的成有的败），但共用一个 batch —— 一次 ⌘Z 撤一整批。"""
    first = PhotoRef.from_path(make_photo("一.jpg"))
    second = PhotoRef.from_path(make_photo("二.jpg"))

    result = trash_entries(tmp_path, [first, second])

    records = read_records(tmp_path)
    assert result.ok
    assert len(records) == 2
    assert {record.action for record in records} == {ACTION_TRASH}
    assert {record.result for record in records} == {RESULT_OK}
    assert len({record.batch for record in records}) == 1


def test_single_item_gets_no_batch_id(tmp_path: Path, fake_trash: Path, photo: PhotoRef) -> None:
    trash_entries(tmp_path, [photo])

    assert read_records(tmp_path)[0].batch is None


def test_a_failed_item_is_recorded_and_the_others_still_move(
    tmp_path: Path, fake_trash: Path, make_photo: Callable[..., Path]
) -> None:
    """批量里有一项动不了时，其余照删，但失败原因必须留下。"""
    good = PhotoRef.from_path(make_photo("还在.jpg"))
    gone = PhotoRef(path=tmp_path / "早就不在了.jpg", size=1, mtime_ns=1)

    result = trash_entries(tmp_path, [good, gone])

    assert result.ok is False
    assert [entry.name for entry in result.moved] == ["还在.jpg"]
    assert [failure.name for failure in result.failures] == ["早就不在了.jpg"]
    records = read_records(tmp_path)
    assert [record.result for record in records] == [RESULT_OK, RESULT_FAILED]
    assert records[1].message


def test_restore_puts_the_file_back_where_it_was(
    tmp_path: Path, fake_trash: Path, photo: PhotoRef
) -> None:
    trash_entries(tmp_path, [photo])
    record = read_records(tmp_path)[0]

    result = restore_entries(tmp_path, [record])

    assert result.ok
    assert result.restored[0].path == photo.path
    assert photo.path.read_bytes() == _CONTENT
    assert list(fake_trash.iterdir()) == []


def test_restore_keeps_size_and_mtime(tmp_path: Path, fake_trash: Path, photo: PhotoRef) -> None:
    """移动不改内容也不改时间，缩略图缓存因此还能命中。"""
    trash_entries(tmp_path, [photo])
    record = read_records(tmp_path)[0]

    restored = restore_entries(tmp_path, [record]).restored[0]

    assert isinstance(restored, PhotoRef)
    assert restored.size == photo.size
    assert restored.mtime_ns == photo.mtime_ns


def test_restore_never_overwrites_a_file_that_moved_in(tmp_path: Path, fake_trash: Path) -> None:
    """原位置被别的文件占了就报错：被盖掉的是用户的另一张照片。"""
    trash_entries(tmp_path, [PhotoRef.from_path(_write(tmp_path / "海边.jpg", _CONTENT))])
    record = read_records(tmp_path)[0]
    (tmp_path / "海边.jpg").write_bytes(_LATER)

    result = restore_entries(tmp_path, [record])

    assert result.ok is False
    assert (tmp_path / "海边.jpg").read_bytes() == _LATER
    assert (fake_trash / "海边.jpg").exists(), "取不回来也不能把它弄丢"
    assert read_records(tmp_path)[-1].action == ACTION_RESTORE
    assert read_records(tmp_path)[-1].result == RESULT_FAILED


def test_restore_says_so_when_the_trash_copy_is_gone(tmp_path: Path, fake_trash: Path) -> None:
    """用户清空过废纸篓、或在访达里还原过：如实说，不装作成功。"""
    photo = PhotoRef.from_path(_write(tmp_path / "海边.jpg", _CONTENT))
    trash_entries(tmp_path, [photo])
    record = read_records(tmp_path)[0]
    (fake_trash / "海边.jpg").unlink()

    result = restore_entries(tmp_path, [record])

    assert result.ok is False
    assert "废纸篓中已找不到" in result.failures[0].message


def test_restore_brings_back_the_whole_album(tmp_path: Path, fake_trash: Path) -> None:
    album = tmp_path / "2025 苏州"
    (album / "里面").mkdir(parents=True)
    (album / "第一张.jpg").write_bytes(_CONTENT)
    trash_entries(tmp_path, [FolderRef.from_path(album)])
    record = read_records(tmp_path)[0]

    restore_entries(tmp_path, [record])

    assert (album / "第一张.jpg").read_bytes() == _CONTENT


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_a_section_header_is_reported_as_not_on_disk(tmp_path: Path, photo: PhotoRef) -> None:
    """分区标题移不走：如实报一句失败，而不是去读一个不存在的路径。"""
    from gallery.core.library.photos import GroupRef, SectionRef

    group = GroupRef(facet="captured_month", key="2024-05", label="2024 年 5 月", photos=(photo,))
    header = SectionRef(group=group, count=1)

    result = trash_entries(tmp_path, [header])

    assert result.failures
    assert "分区标题" in result.failures[0].message
    assert photo.path.exists(), "照片一个字节都不许动"
