"""``gallery.core.library.scanner`` 在相册模式下的行为：逐层列举。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library.photos import FolderRef, PhotoRef
from gallery.core.library.scanner import ScanReport, describe_entry, is_visible_path, iter_entries


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

    from gallery.core.library import scanner

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


# ---- 单个路径：实时监听用的那一条 ---------------------------------------------


def test_describe_entry_builds_a_photo_ref(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """实时监听只给得出一个路径，大小与修改时间要现 stat 一次 —— 与扫描同构。"""
    picture = make_photo("海边.jpg")

    entry = describe_entry(tmp_path, picture)

    assert isinstance(entry, PhotoRef)
    assert entry.path == picture
    assert entry.size == picture.stat().st_size
    assert entry.mtime_ns == picture.stat().st_mtime_ns


def test_describe_entry_builds_a_folder_ref(tmp_path: Path) -> None:
    (tmp_path / "相册").mkdir()

    entry = describe_entry(tmp_path, tmp_path / "相册")

    assert isinstance(entry, FolderRef)
    assert entry.path == tmp_path / "相册"


def test_describe_entry_says_no_for_what_the_scanner_would_skip(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """不支持的格式、读不出来的东西：网格上本来就不该有它们。"""
    make_photo("能看的.jpg")
    (tmp_path / "读我.txt").write_text("不是照片", encoding="utf-8")
    (tmp_path / "链接.jpg").symlink_to(tmp_path / "能看的.jpg")

    assert describe_entry(tmp_path, tmp_path / "读我.txt") is None
    assert describe_entry(tmp_path, tmp_path / "早就不在了.jpg") is None
    # 符号链接：扫描同样不收（跟随可能走出库、也可能无限递归）
    assert describe_entry(tmp_path, tmp_path / "链接.jpg") is None


def test_describe_entry_refuses_to_leave_the_library(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    outside = make_photo("别的库.jpg", directory=tmp_path.parent / "别的库")

    assert describe_entry(tmp_path, outside) is None


def test_is_visible_path_matches_what_the_scan_walks(tmp_path: Path) -> None:
    """一条规则两处用：扫描看不见的路径，监听也不该报上来。"""
    seen = {entry.path for entry in iter_entries(tmp_path)}
    (tmp_path / "相册").mkdir()
    (tmp_path / ".隐藏").mkdir()
    (tmp_path / "读我.txt").write_text("x", encoding="utf-8")

    assert is_visible_path(tmp_path, tmp_path / "照片.jpg")
    assert is_visible_path(tmp_path, tmp_path / "相册")
    assert is_visible_path(tmp_path, tmp_path / "相册" / "里面.jpg")
    assert not is_visible_path(tmp_path, tmp_path / ".隐藏" / "a.jpg")
    assert not is_visible_path(tmp_path, tmp_path / f"{CONFIG_DIR_NAME}" / "index.db")
    assert not is_visible_path(tmp_path, tmp_path.parent / "别的库" / "a.jpg")
    # 已经在库内、也不隐藏：它「合规则」，只是此刻不在磁盘上。监听最常问到这种路径。
    assert is_visible_path(tmp_path, tmp_path / "早就不在了.jpg")
    assert seen == set()  # 这一层确实只有上面手工造出来的那些非照片


def test_is_visible_path_follows_symlinks_to_its_real_place(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """链到库外的目录不跟：跟进去既可能走出库，也可能无限递归。"""
    elsewhere = make_photo("目标.jpg", directory=tmp_path.parent / "目标库")
    (tmp_path / "链接").symlink_to(elsewhere.parent)

    assert is_visible_path(tmp_path, tmp_path / "链接" / "目标.jpg") is False
