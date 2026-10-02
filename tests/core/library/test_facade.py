"""``gallery.core.library.facade`` 的测试。

重点是那条红线：**打开库不得写入任何东西**。写盘必须由用户在弹窗里明确同意后的
``prepare_library`` 触发。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

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


# ---- 全部相册（ADR 0019：复制到相册 / 移动到相册的目标清单）------------------


def test_albums_lists_every_album_recursively(tmp_path: Path) -> None:
    """列**全库**的相册，不是只列当前这一层 —— 用户要能挑到深层相册。"""
    for name in ("京都", "京都/奈良", "京都/奈良/东大寺", "2025/苏州"):
        (tmp_path / name).mkdir(parents=True)
    make_photo_in(tmp_path, "京都/a.jpg")
    library = Library.open(tmp_path)

    assert [library.album_label(f.path) for f in library.albums()] == [
        "2025",
        "2025/苏州",
        "京都",
        "京都/奈良",
        "京都/奈良/东大寺",
    ]


def test_albums_order_keeps_a_parent_next_to_its_children(tmp_path: Path) -> None:
    """按**完整路径**排，不按深度优先的先序。

    先序会让「京都」的所有子相册散落在整棵树的各处，菜单读起来像随机排列。
    """
    for name in ("京都", "京都/大阪", "京都/宇治", "奈良"):
        (tmp_path / name).mkdir(parents=True)
    library = Library.open(tmp_path)

    assert [library.album_label(f.path) for f in library.albums()] == [
        "京都",
        "京都/大阪",
        "京都/宇治",
        "奈良",
    ]


def test_albums_skips_hidden_and_config_directories(tmp_path: Path) -> None:
    """隐藏项与配置目录不算相册 —— 否则用户会看见一个「.gallery」可往里挪照片。"""
    for name in ("京都", ".隐藏相册", ".gallery/cache"):
        (tmp_path / name).mkdir(parents=True)
    library = Library.open(tmp_path)

    assert [library.album_label(f.path) for f in library.albums()] == ["京都"]


def test_albums_does_not_follow_symlinks(tmp_path: Path) -> None:
    """不跟符号链接：跟进去既可能走出库，也可能无限递归。"""
    (tmp_path / "京都").mkdir()
    (tmp_path / "链接").symlink_to(tmp_path / "京都")
    library = Library.open(tmp_path)

    assert [library.album_label(f.path) for f in library.albums()] == ["京都"]


def test_albums_is_empty_for_a_flat_library(tmp_path: Path) -> None:
    """一个相册都没有时返回空清单（界面据此干脆不建那两条菜单）。"""
    make_photo_in(tmp_path, "散着的一张.jpg")
    library = Library.open(tmp_path)

    assert library.albums() == []


def test_album_label_uses_the_relative_path(tmp_path: Path) -> None:
    """标签是相对库根的路径：两个年份下各有一个「苏州」，只写名字分不清。"""
    (tmp_path / "2024").mkdir()
    (tmp_path / "2024/苏州").mkdir()
    (tmp_path / "2025").mkdir()
    (tmp_path / "2025/苏州").mkdir()
    library = Library.open(tmp_path)

    assert library.album_label(tmp_path / "2024" / "苏州") == "2024/苏州"
    assert library.album_label(tmp_path / "2025" / "苏州") == "2025/苏州"
    assert library.album_label(tmp_path / "2025") == "2025"


def make_photo_in(root: Path, name: str) -> Path:
    """在 ``root`` 下造一张最小的合法 JPEG（不引 exiftool，不进版本控制）。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 3), (10, 20, 30)).save(path)
    return path


def test_album_label_never_renders_the_root_as_a_dot(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """库根本身在菜单里要显示成**它自己的名字**，不是一个孤零零的「.」。

    ``relative_to(库根, 库根)`` 给的是 ``Path('.')``，写出来就是 "."；而那句
    ``or target.name`` 永远轮不到（``relative_to`` 从不给出空路径）。
    """
    root = tmp_path / "照片"
    (root / "京都").mkdir(parents=True)
    make_photo("一张.jpg", directory=root / "京都")
    library = Library.open(root)

    assert library.album_label(root) == "照片"
    assert library.album_label(root / "京都") == "京都"
    assert library.album_label(root / "京都" / "内") == "京都/内"
