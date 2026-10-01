"""重命名的测试。

全部在 ``tmp_path`` 里进行：这里测的操作**真的会改用户的文件名**，
所以测试用的每个「照片」都是当场造出来的临时文件，一个字节也不许落到真实相册里。

重点覆盖三类风险：

1. 该拦的必须拦住（非法名字、覆盖已有文件、改扩展名、以点开头）。
2. 改完必须能改回去（日志里有据可查，撤销真的把名字还回来了）。
3. 失败也要如实记账，不许悄悄吞掉。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.errors import RenameError
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.metadata.journal import (
    ACTION_RENAME,
    ACTION_UNDO,
    RESULT_FAILED,
    RESULT_OK,
    read_records,
)
from gallery.core.metadata.rename import rename_entry, validate_name
from gallery.core.metadata.undo import undo_last_write

#: 冒充「另一张同名照片」的内容。改名绝不能把它盖掉。
_PLACEHOLDER = "占位内容".encode()


@pytest.fixture
def photo(tmp_path: Path) -> PhotoRef:
    path = tmp_path / "IMG_0001.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdb not really a jpeg")
    return PhotoRef.from_path(path)


def make_album(root: Path, name: str = "2025 苏州") -> FolderRef:
    folder = root / name
    folder.mkdir()
    return FolderRef.from_path(folder)


# ---- 校验：不该放行的名字 ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "不能为空"),
        ("   ", "不能为空"),
        ("..", "保留名字"),
        (".隐藏", "以「.」开头"),
        ("海边/日落", "不能带"),
        ("海边\\日落", "不能带"),
        ("海\t边", "控制字符"),
        ("x" * 300, "太长"),
    ],
)
def test_illegal_names_are_rejected(photo: PhotoRef, name: str, fragment: str) -> None:
    with pytest.raises(RenameError) as caught:
        validate_name(name, photo)
    assert fragment in caught.value.message


def test_photo_cannot_change_its_extension(photo: PhotoRef) -> None:
    """改了扩展名，这个文件就不再是 Gallery 认得的照片了 —— 相当于删了它。"""
    with pytest.raises(RenameError) as caught:
        validate_name("海边.png", photo)
    assert "不能改扩展名" in caught.value.message


def test_missing_extension_is_kept_for_the_user(photo: PhotoRef) -> None:
    """只写主干时补回原扩展名：用户不该被迫去敲 .jpg。"""
    assert validate_name("海边日落", photo) == "海边日落.jpg"


def test_extension_case_does_not_matter(photo: PhotoRef) -> None:
    assert validate_name("海边.JPG", photo) == "海边.JPG"


def test_surrounding_spaces_are_trimmed(photo: PhotoRef) -> None:
    """手输的名字很容易带上首尾空格，悄悄多出一个空格更烦。"""
    assert validate_name("  海边  ", photo) == "海边.jpg"


def test_album_name_keeps_dots(photo: PhotoRef) -> None:
    """相册名里的点不是扩展名，不能被当成扩展名处理。"""
    album = FolderRef(path=Path("/tmp/2025.06"), mtime_ns=0)
    assert validate_name("2025.07 苏州", album) == "2025.07 苏州"


def test_config_dir_cannot_be_renamed(tmp_path: Path) -> None:
    config = paths.config_dir(tmp_path)
    config.mkdir()
    entry = FolderRef.from_path(config)
    with pytest.raises(RenameError) as caught:
        rename_entry(tmp_path, entry, "备份")
    assert "配置目录" in caught.value.message


def test_library_root_cannot_be_renamed(tmp_path: Path) -> None:
    with pytest.raises(RenameError) as caught:
        rename_entry(tmp_path, FolderRef.from_path(tmp_path), "新名字")
    assert "库本身" in caught.value.message


def test_outside_the_library_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-外人.jpg"
    outside.write_bytes(b"x")
    entry = PhotoRef.from_path(outside)
    try:
        with pytest.raises(RenameError) as caught:
            rename_entry(tmp_path / "库", entry, "改名")
        assert "只能重命名库里的东西" in caught.value.message
    finally:
        outside.unlink(missing_ok=True)


# ---- 执行 -------------------------------------------------------------------


def test_rename_moves_the_file(tmp_path: Path, photo: PhotoRef) -> None:
    original = photo.path.read_bytes()

    result = rename_entry(tmp_path, photo, "海边日落")

    assert result.renamed is True
    assert result.entry.name == "海边日落.jpg"
    assert result.previous.name == "IMG_0001.jpg"
    assert result.entry.path.exists()
    assert not photo.path.exists()
    # 内容一个字节都不能变 —— 这是改名，不是「重新保存一张照片」。
    assert result.entry.path.read_bytes() == original
    assert result.entry.size == photo.size


def test_rename_album_keeps_its_children(tmp_path: Path) -> None:
    album = make_album(tmp_path)
    (album.path / "第一张.jpg").write_bytes(b"x")

    rename_entry(tmp_path, album, "2025 苏州 夏天")

    assert (tmp_path / "2025 苏州 夏天" / "第一张.jpg").exists()
    assert not (tmp_path / "2025 苏州").exists()


def test_same_name_does_nothing(tmp_path: Path, photo: PhotoRef) -> None:
    """名字没变就什么都别做，连日志都不该多一条。"""
    result = rename_entry(tmp_path, photo, "IMG_0001.jpg")

    assert result.renamed is False
    assert result.entry is photo
    assert read_records(tmp_path) == []


def test_existing_name_is_never_overwritten(tmp_path: Path, photo: PhotoRef) -> None:
    """POSIX 的 rename 会静默覆盖，这里必须自己挡住：被覆盖的是用户的照片。"""
    victim = tmp_path / "海边.jpg"
    original = "用户的老照片".encode()
    victim.write_bytes(original)

    with pytest.raises(RenameError) as caught:
        rename_entry(tmp_path, photo, "海边.jpg")

    assert "已经有一个叫" in caught.value.message
    assert victim.read_bytes() == original
    assert photo.path.exists()


def test_failed_rename_is_recorded(
    tmp_path: Path, photo: PhotoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统拒绝了改名也要写日志：用户得能在日志里看到「试过但没成」。"""

    def boom(self: Path, target: Path) -> None:
        del self, target
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "rename", boom, raising=True)
    with pytest.raises(RenameError):
        rename_entry(tmp_path, photo, "海边")

    recorded = read_records(tmp_path)
    assert [record.result for record in recorded] == [RESULT_FAILED]
    assert recorded[0].action == ACTION_RENAME
    assert recorded[0].source.name == "IMG_0001.jpg"
    assert recorded[0].message
    assert photo.path.exists(), "改名失败时原文件必须还在原处"


def test_rejected_rename_touches_nothing_and_logs_nothing(tmp_path: Path, photo: PhotoRef) -> None:
    """改名前就被拦下的（撞名、名字非法）没有动过磁盘，就不该冒充一次写操作。"""
    (tmp_path / "海边.jpg").write_bytes(_PLACEHOLDER)

    with pytest.raises(RenameError):
        rename_entry(tmp_path, photo, "海边.jpg")

    assert read_records(tmp_path) == []
    assert photo.path.exists()


# ---- 日志与撤销 -------------------------------------------------------------
# 撤销只有一个入口（metadata.undo 那一摞共用的栈，⌘Z 走它），所以这里也用它。


def test_successful_rename_is_recorded(tmp_path: Path, photo: PhotoRef) -> None:
    rename_entry(tmp_path, photo, "海边日落")

    records = read_records(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record.action == ACTION_RENAME
    assert record.result == RESULT_OK
    assert record.source.name == "IMG_0001.jpg"
    assert record.target.name == "海边日落.jpg"
    assert record.is_dir is False
    assert record.timestamp


def test_undo_restores_the_original_name(tmp_path: Path, photo: PhotoRef) -> None:
    rename_entry(tmp_path, photo, "海边日落")

    result = undo_last_write(tmp_path)

    assert result is not None
    assert result.action == ACTION_RENAME
    assert result.entries[0].name == "IMG_0001.jpg"
    assert result.entries[0].path.exists()
    assert not (tmp_path / "海边日落.jpg").exists()
    # 撤销本身也要记账：否则重放日志会以为这次改动还欠一次撤销。
    assert read_records(tmp_path)[-1].action == ACTION_UNDO


def test_undo_restores_the_original_album(tmp_path: Path) -> None:
    album = make_album(tmp_path)
    (album.path / "照片.jpg").write_bytes(b"x")
    rename_entry(tmp_path, album, "新相册名")

    undo_last_write(tmp_path)

    assert (tmp_path / "2025 苏州" / "照片.jpg").exists()
    assert not (tmp_path / "新相册名").exists()


def test_undo_unwinds_in_reverse_order(tmp_path: Path) -> None:
    """连改两次要能一层层退回去，而不是只认最后一次。"""
    first = make_album(tmp_path, "甲")
    second = make_album(tmp_path, "乙")
    rename_entry(tmp_path, first, "甲改")
    rename_entry(tmp_path, second, "乙改")

    undo_last_write(tmp_path)
    undo_last_write(tmp_path)

    assert (tmp_path / "甲").is_dir()
    assert (tmp_path / "乙").is_dir()
    assert not (tmp_path / "甲改").exists()
    assert not (tmp_path / "乙改").exists()


def test_undo_without_any_write_returns_none(tmp_path: Path) -> None:
    assert undo_last_write(tmp_path) is None


def test_undo_twice_returns_none_the_second_time(tmp_path: Path, photo: PhotoRef) -> None:
    """同一次改名不能被撤销两次。"""
    rename_entry(tmp_path, photo, "海边日落")
    assert undo_last_write(tmp_path) is not None
    assert undo_last_write(tmp_path) is None


def test_undo_refuses_when_the_original_name_is_taken(tmp_path: Path, photo: PhotoRef) -> None:
    rename_entry(tmp_path, photo, "海边日落")
    (tmp_path / "IMG_0001.jpg").write_bytes(_PLACEHOLDER)

    with pytest.raises(RenameError) as caught:
        undo_last_write(tmp_path)

    assert "撤销不了" in caught.value.message
    assert (tmp_path / "海边日落.jpg").exists()


def test_undo_refuses_when_the_file_is_gone(tmp_path: Path, photo: PhotoRef) -> None:
    rename_entry(tmp_path, photo, "海边日落")
    (tmp_path / "海边日落.jpg").unlink()

    with pytest.raises(RenameError) as caught:
        undo_last_write(tmp_path)

    assert "撤销不了" in caught.value.message


def test_failed_rename_is_not_undoable(tmp_path: Path, photo: PhotoRef) -> None:
    (tmp_path / "海边.jpg").write_bytes(_PLACEHOLDER)
    with pytest.raises(RenameError):
        rename_entry(tmp_path, photo, "海边.jpg")

    assert undo_last_write(tmp_path) is None


# ---- 日志文件本身 -----------------------------------------------------------


def test_journal_is_written_as_jsonl_under_the_config_dir(tmp_path: Path, photo: PhotoRef) -> None:
    rename_entry(tmp_path, photo, "海边日落")

    files = list(paths.journal_dir(tmp_path).glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("{")
    assert "海边日落.jpg" in lines[0]


def test_broken_journal_lines_are_skipped(tmp_path: Path, photo: PhotoRef) -> None:
    """日志被写坏一半不能让程序起不来，也不能让可撤销的记录一起丢掉。"""
    rename_entry(tmp_path, photo, "海边日落")
    files = list(paths.journal_dir(tmp_path).glob("*.jsonl"))
    with files[0].open("a", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON\n\n")

    assert len(read_records(tmp_path)) == 1


def test_rename_still_succeeds_when_the_journal_cannot_be_written(
    tmp_path: Path, photo: PhotoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    """记不上账不能把已经改好的文件说成没改成 —— 只能说「撤销不了」。"""
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths.journal_dir(tmp_path):
            raise OSError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse, raising=True)

    result = rename_entry(tmp_path, photo, "海边日落")

    assert result.renamed is True
    assert result.journaled is False
    assert (tmp_path / "海边日落.jpg").exists()


# ---- 门面 -------------------------------------------------------------------


def test_library_rename_and_undo(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """Library 是界面唯一能碰的入口，顺带确认缩略图缓存跟着搬家。"""
    from gallery.core.library.facade import Library

    picture = make_photo("海边.jpg", directory=tmp_path)
    library = Library.open(tmp_path)
    entry = PhotoRef.from_path(picture)
    library.thumbnail(entry)  # 先生成缓存条目，改名后应该还在

    result = library.rename_entry(entry, "海边日落")
    assert result.renamed is True
    assert library.thumbnails.total_bytes() > 0
    # 改完还能立刻再取到缩略图（缓存被挪到了新键上，没有重新解码）
    assert library.thumbnail(result.entry).width > 0

    undone = library.undo_last_write()
    assert undone is not None
    assert undone.entries[0].name == "海边.jpg"
    assert picture.exists()
