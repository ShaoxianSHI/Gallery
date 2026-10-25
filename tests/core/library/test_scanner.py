"""``gallery.core.library.scanner`` 的测试。

扫描是最容易「悄悄出错」的一环：漏掉照片、把隐藏文件当成照片、在坏目录上崩掉，
都会直接影响用户对整个软件的信任。这里把每种边界都锁定。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library import scanner
from gallery.core.library.photos import PhotoRef
from gallery.core.library.scanner import ScanReport, iter_photos


def names(refs: Iterator[PhotoRef]) -> list[str]:
    return [ref.path.name for ref in refs]


def test_recursive_by_default(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg")
    nested = tmp_path / "2024-京都"
    make_photo("b.jpg", directory=nested)
    make_photo("c.png", directory=nested / "更深的" / "再深一层")

    assert sorted(names(iter_photos(tmp_path))) == ["a.jpg", "b.jpg", "c.png"]


def test_non_recursive_only_top_level(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg")
    make_photo("b.jpg", directory=tmp_path / "sub")

    assert names(iter_photos(tmp_path, recursive=False)) == ["a.jpg"]


def test_skips_hidden_and_config_dir(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("visible.jpg")
    make_photo(".hidden.jpg")
    make_photo("secret.jpg", directory=tmp_path / ".hidden-dir")
    make_photo("cached.jpg", directory=tmp_path / CONFIG_DIR_NAME / "cache")
    make_photo("._resource-fork.jpg")

    assert names(iter_photos(tmp_path)) == ["visible.jpg"]


def test_ignores_unsupported_files(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg")
    (tmp_path / "notes.txt").write_text("不是照片", encoding="utf-8")
    (tmp_path / "raw.cr3").write_bytes(b"\x00" * 8)
    (tmp_path / "movie.mkv").write_bytes(b"\x00" * 8)

    assert names(iter_photos(tmp_path)) == ["a.jpg"]


def test_videos_are_scanned_like_photos(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """视频与照片共用同一个 ``PhotoRef``：网格、改名、事件都只认路径（ADR 0090）。"""
    make_photo("a.jpg")
    for name in ("b.mp4", "c.MOV", "d.m4v", "e.avi", "f.3gp"):
        (tmp_path / name).write_bytes(b"\x00" * 8)

    refs = list(iter_photos(tmp_path))

    assert [ref.path.name for ref in refs] == [
        "a.jpg",
        "b.mp4",
        "c.MOV",
        "d.m4v",
        "e.avi",
        "f.3gp",
    ]
    assert [ref.is_video for ref in refs] == [False, True, True, True, True, True]


def test_order_is_deterministic(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("b.jpg")
    make_photo("A.jpg")
    make_photo("c.jpg")
    make_photo("d.jpg", directory=tmp_path / "sub")

    assert names(iter_photos(tmp_path)) == ["A.jpg", "b.jpg", "c.jpg", "d.jpg"]


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        list(iter_photos(tmp_path / "不存在"))

    assert "无法打开" in excinfo.value.message


def test_file_as_root_raises(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    path = make_photo("a.jpg")

    with pytest.raises(LibraryNotAccessibleError):
        list(iter_photos(path))


def test_cancellation_stops_iteration(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    for index in range(10):
        make_photo(f"{index:02d}.jpg")

    seen: list[PhotoRef] = []

    def should_cancel() -> bool:
        return len(seen) >= 2

    for ref in iter_photos(tmp_path, should_cancel=should_cancel):
        seen.append(ref)

    assert len(seen) == 2


def test_symlinks_are_skipped(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    """既不收录指向文件的符号链接，也不进入指向目录的符号链接（否则可能无限递归）。"""
    root = tmp_path / "库"
    root.mkdir()
    real = make_photo("real.jpg", directory=root)
    outside = tmp_path / "外部"
    make_photo("outside.jpg", directory=outside)
    (root / "link.jpg").symlink_to(real)
    (root / "link-dir").symlink_to(outside, target_is_directory=True)

    assert names(iter_photos(root)) == ["real.jpg"]


def test_unreadable_directory_is_reported(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_photo("ok.jpg")
    blocked = tmp_path / "无权限"
    blocked.mkdir()
    real_scandir = os.scandir

    def fake_scandir(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        if Path(path) == blocked:
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", fake_scandir)
    report = ScanReport()

    found = names(iter_photos(tmp_path, report=report))

    assert found == ["ok.jpg"]
    assert report.has_issues is True
    assert len(report.skipped_dirs) == 1
    assert str(blocked) in report.skipped_dirs[0]
    assert "1 个文件夹无法读取" in report.summary


def test_report_summary_is_empty_without_issues() -> None:
    report = ScanReport()

    assert report.summary == ""
    assert report.has_issues is False
