"""``gallery.core.scanner`` 在相册模式下的行为：逐层列举。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.photos import FolderRef, PhotoRef
from gallery.core.scanner import ScanReport, iter_entries


def names(entries: list[FolderRef | PhotoRef]) -> list[str]:
    return [entry.name for entry in entries]


def folders(entries: list[FolderRef | PhotoRef]) -> list[str]:
    return [entry.name for entry in entries if isinstance(entry, FolderRef)]


def photos(entries: list[FolderRef | PhotoRef]) -> list[str]:
    return [entry.name for entry in entries if isinstance(entry, PhotoRef)]


def test_folders_come_before_photos(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """同一层里既有相册又有照片时，相册排在最前面。"""
    make_photo("b.jpg")
    make_photo("a.jpg")
    (tmp_path / "旅行").mkdir()
    (tmp_path / "家庭").mkdir()

    entries = list(iter_entries(tmp_path))

    assert folders(entries) == ["家庭", "旅行"]
    assert photos(entries) == ["a.jpg", "b.jpg"]
    assert names(entries) == ["家庭", "旅行", "a.jpg", "b.jpg"]


def test_only_one_level(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """逐层列举不会递归：子目录里的照片由进入该目录后再列。"""
    make_photo("top.jpg")
    nested = tmp_path / "旅行"
    make_photo("inside.jpg", directory=nested)

    entries = list(iter_entries(tmp_path))

    assert names(entries) == ["旅行", "top.jpg"]
    assert list(iter_entries(nested))[0].name == "inside.jpg"


def test_skips_hidden_config_and_unsupported(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("visible.jpg")
    make_photo(".hidden.jpg")
    (tmp_path / ".hidden-dir").mkdir()
    (tmp_path / CONFIG_DIR_NAME).mkdir()
    (tmp_path / "notes.txt").write_text("不是照片", encoding="utf-8")

    entries = list(iter_entries(tmp_path))

    assert names(entries) == ["visible.jpg"]


def test_empty_folder_is_listed(tmp_path: Path) -> None:
    (tmp_path / "空的").mkdir()

    entries = list(iter_entries(tmp_path))

    assert folders(entries) == ["空的"]
    assert isinstance(entries[0], FolderRef)
    assert entries[0].mtime_ns > 0


def test_symlinks_are_skipped(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    root = tmp_path / "库"
    root.mkdir()
    outside = tmp_path / "外部"
    outside.mkdir()
    (root / "链接目录").symlink_to(outside, target_is_directory=True)

    assert list(iter_entries(root)) == []


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(LibraryNotAccessibleError):
        list(iter_entries(tmp_path / "不存在"))


def test_cancellation_stops_early(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    for index in range(5):
        make_photo(f"{index:02d}.jpg")

    seen: list[FolderRef | PhotoRef] = []

    def should_cancel() -> bool:
        return len(seen) >= 2

    for entry in iter_entries(tmp_path, should_cancel=should_cancel):
        seen.append(entry)

    assert len(seen) == 2


def test_unreadable_directory_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """目标目录本身读不了时，记进报告而不是抛异常。

    注意单层列举只会读目标目录一次，并不会进入子目录，所以「读不了」的场景
    是目标目录本身，而不是它的某个子目录。
    """
    import os

    from gallery.core import scanner

    blocked = tmp_path / "无权限"
    blocked.mkdir()
    real_scandir = os.scandir

    def fake_scandir(path: str | os.PathLike[str]) -> object:
        if Path(path) == blocked:
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", fake_scandir)
    report = ScanReport()

    assert list(iter_entries(blocked, report=report)) == []
    assert report.has_issues is True
    assert str(blocked) in report.skipped_dirs[0]
