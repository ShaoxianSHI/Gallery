"""``gallery.core.library.facade`` 的测试。

重点是那条红线：**打开库不得写入任何东西**。写盘必须由用户在弹窗里明确同意后的
``prepare_library`` 触发。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.errors import LibraryNotAccessibleError
from gallery.core.library.config import LibraryConfig, save_config
from gallery.core.library.facade import Library
from gallery.core.library.photos import FolderRef


def test_open_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        Library.open(tmp_path / "不存在")

    assert "打不开" in excinfo.value.message


def test_open_file_instead_of_directory(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        Library.open(make_photo("a.jpg"))

    assert "不是一个文件夹" in excinfo.value.message


def test_open_never_writes(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg")

    library = Library.open(tmp_path)

    assert paths.has_config_dir(tmp_path) is False
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.jpg"]
    assert library.thumbnails.directory == paths.thumbnail_cache_dir(tmp_path)
    assert library.cache_in_library is True


def test_open_in_system_cache(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    library = Library.open(tmp_path, cache_in_library=False, cache_base=tmp_path / "系统目录")

    assert library.cache_in_library is False
    assert library.thumbnails.directory == paths.external_thumbnail_cache_dir(
        tmp_path, base=tmp_path / "系统目录"
    )


def test_iter_photos_follows_config(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("top.jpg")
    make_photo("nested.jpg", directory=tmp_path / "sub")

    recursive = Library.open(tmp_path)
    assert sorted(ref.name for ref in recursive.iter_photos()) == ["nested.jpg", "top.jpg"]

    paths.config_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    save_config(tmp_path, LibraryConfig(recursive=False))
    flat = Library.open(tmp_path)

    assert [ref.name for ref in flat.iter_photos()] == ["top.jpg"]


def test_thumbnail_preview_and_full(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg", size=(800, 400))
    library = Library.open(tmp_path)
    ref = next(iter(library.iter_photos()))

    assert library.thumbnail(ref).size == (512, 256)
    assert library.preview(ref, max_edge=100).size == (100, 50)
    assert library.full(ref).size == (800, 400)


def test_clear_cache(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("a.jpg")
    library = Library.open(tmp_path)
    ref = next(iter(library.iter_photos()))
    library.thumbnail(ref)

    assert library.clear_cache() == 1
    assert library.thumbnails.total_bytes() == 0


def test_display_name(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    root = tmp_path / "2024-京都"
    make_photo("a.jpg", directory=root)

    assert Library.open(root).display_name == "2024-京都"


def test_relative_root_is_resolved_to_absolute(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """命令行可以传相对路径，但记进会话的必须是绝对路径。"""
    make_photo("a.jpg", directory=tmp_path / "库")
    monkeypatch.chdir(tmp_path)

    library = Library.open(Path("库"))

    assert library.root.is_absolute()
    assert library.root == (tmp_path / "库").resolve()


def test_iter_entries_lists_one_level(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    make_photo("top.jpg")
    album = tmp_path / "2022-复活节波尔多"
    make_photo("inside.jpg", directory=album)
    library = Library.open(tmp_path)

    entries = list(library.iter_entries())

    assert [entry.name for entry in entries] == ["2022-复活节波尔多", "top.jpg"]
    assert isinstance(entries[0], FolderRef)
    assert [entry.name for entry in library.iter_entries(album)] == ["inside.jpg"]


def test_iter_entries_refuses_to_leave_the_library(tmp_path: Path) -> None:
    """逐层浏览绝不能走出用户指定的库。"""
    library_root = tmp_path / "库"
    library_root.mkdir()
    outside = tmp_path / "别处"
    outside.mkdir()

    library = Library.open(library_root)

    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        list(library.iter_entries(outside))
    assert "库以外" in excinfo.value.message


def test_folder_cover_finds_first_photo(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    album = tmp_path / "相册"
    make_photo("b.jpg", directory=album)
    album_nested = album / "更深"
    make_photo("c.jpg", directory=album_nested)
    library = Library.open(tmp_path)

    cover = library.folder_cover(album)

    assert cover is not None
    assert cover.path == album / "b.jpg"


def test_folder_cover_returns_none_for_empty(tmp_path: Path) -> None:
    album = tmp_path / "空相册"
    album.mkdir()
    library = Library.open(tmp_path)

    assert library.folder_cover(album) is None
    assert library.folder_cover(tmp_path / "不存在") is None
