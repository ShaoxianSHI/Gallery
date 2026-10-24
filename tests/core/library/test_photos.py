"""``gallery.core.library.photos`` 的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.library.photos import (
    PhotoRef,
    format_label,
    format_suffixes,
    is_supported_media,
    is_supported_photo,
    is_video,
)


@pytest.mark.parametrize(
    "name",
    ["a.jpg", "A.JPEG", "b.png", "c.HEIC", "d.heif", "e.tif", "f.tiff", "g.gif", "h.webp", "i.bmp"],
)
def test_supported_extensions(name: str) -> None:
    assert is_supported_photo(Path(name)) is True


@pytest.mark.parametrize("name", ["a.cr3", "b.nef", "c.arw", "d.txt", "e.mp4", "f", "g.jpg.txt"])
def test_unsupported_extensions(name: str) -> None:
    assert is_supported_photo(Path(name)) is False


@pytest.mark.parametrize("name", ["a.mp4", "b.M4V", "c.mov", "d.AVI", "e.3gp"])
def test_video_extensions(name: str) -> None:
    assert is_supported_media(Path(name)) is True
    assert is_video(Path(name)) is True
    assert is_supported_photo(Path(name)) is False


@pytest.mark.parametrize(
    ("name", "supported"),
    [
        ("a.jpg", True),
        ("b.png", True),
        ("c.mkv", False),
        ("d.webm", False),
        ("e.wmv", False),
        ("f.flv", False),
    ],
)
def test_media_extensions_that_are_not_videos(name: str, supported: bool) -> None:
    assert is_video(Path(name)) is False
    assert is_supported_media(Path(name)) is supported


def test_photo_ref_from_path(tmp_path: Path) -> None:
    path = tmp_path / "IMG_0001.JPG"
    path.write_bytes(b"x" * 42)

    ref = PhotoRef.from_path(path)

    assert ref.path == path
    assert ref.size == 42
    assert ref.mtime_ns > 0
    assert ref.name == "IMG_0001.JPG"
    assert ref.suffix == ".jpg"
    assert ref.format_label == "JPEG"
    assert ref.is_supported() is True


def test_format_label_known_and_unknown() -> None:
    assert format_label(".heic") == "HEIC"
    assert format_label(".mp4") == "MP4"
    assert format_label(".mov") == "MOV"
    assert format_label(".dng") == "DNG"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("jpg", (".jpeg", ".jpg")),
        ("JPEG", (".jpeg", ".jpg")),
        (".jpeg", (".jpeg", ".jpg")),
        ("tif", (".tif", ".tiff")),
        ("TIFF", (".tif", ".tiff")),
        (".png", (".png",)),
        ("WebP", (".webp",)),
        ("mp4", (".m4v", ".mp4")),
        ("MP4", (".m4v", ".mp4")),
        ("MOV", (".mov",)),
        ("mkv", None),
        ("dng", None),
        ("回想", None),
    ],
)
def test_format_suffixes_covers_every_extension_of_a_format(
    typed: str, expected: tuple[str, ...] | None
) -> None:
    """认格式不认扩展名：jpg 与 jpeg 归到一个格式下，搜索与分组才有同一个判据。"""
    assert format_suffixes(typed) == expected


def test_photo_ref_is_hashable_and_frozen(tmp_path: Path) -> None:
    ref = PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=2)

    assert hash(ref) == hash(PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=2))
    with pytest.raises(AttributeError):
        ref.size = 3  # type: ignore[misc]
