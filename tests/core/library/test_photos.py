"""``gallery.core.library.photos`` 的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gallery.core.library.photos import PhotoRef, format_label, is_supported_photo


@pytest.mark.parametrize(
    "name",
    ["a.jpg", "A.JPEG", "b.png", "c.HEIC", "d.heif", "e.tif", "f.tiff", "g.gif", "h.webp", "i.bmp"],
)
def test_supported_extensions(name: str) -> None:
    assert is_supported_photo(Path(name)) is True


@pytest.mark.parametrize("name", ["a.cr3", "b.nef", "c.arw", "d.txt", "e.mp4", "f", "g.jpg.txt"])
def test_unsupported_extensions(name: str) -> None:
    assert is_supported_photo(Path(name)) is False


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
    assert format_label(".dng") == "DNG"


def test_photo_ref_is_hashable_and_frozen(tmp_path: Path) -> None:
    ref = PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=2)

    assert hash(ref) == hash(PhotoRef(path=tmp_path / "a.jpg", size=1, mtime_ns=2))
    with pytest.raises(AttributeError):
        ref.size = 3  # type: ignore[misc]
