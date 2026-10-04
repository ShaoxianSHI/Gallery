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
from gallery.core.errors import ConfigError, LibraryNotAccessibleError
from gallery.core.library.config import (
    LibraryConfig,
    load_config,
    prepare_library,
    save_config,
)
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


# ---- 相册封面（ADR 0028）---------------------------------------------------


@pytest.fixture
def library_with_album(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    """一个库：``京都/`` 里两张照片（``一张.jpg``、``另一张.jpg``），配置目录已建好。"""
    album = tmp_path / "京都"
    make_photo("一张.jpg", directory=album)
    make_photo("另一张.jpg", directory=album)
    prepare_library(tmp_path)
    return Library.open(tmp_path)


def test_set_album_cover_writes_the_config_and_takes_effect(
    library_with_album: Library,
) -> None:
    """设完之后：文件里多了一行、下一次打开库读得到、这一层立刻就是那张封面。"""
    album = library_with_album.root / "京都"
    chosen = album / "另一张.jpg"

    library_with_album.set_album_cover(album, chosen)

    assert load_config(library_with_album.root).album_covers == {"京都": "京都/另一张.jpg"}
    assert Library.open(library_with_album.root).folder_cover(album).path == chosen


def test_setting_a_cover_leaves_the_other_settings_alone(
    library_with_album: Library, tmp_path: Path
) -> None:
    """写封面走的是**整份配置**：顺手把用户手改的缩略图档位抹掉是不能接受的。"""
    save_config(
        library_with_album.root,
        LibraryConfig(thumbnail_max_edge=128, recursive=False),
    )
    library = Library.open(library_with_album.root)
    album = library.root / "京都"

    library.set_album_cover(album, album / "另一张.jpg")

    stored = load_config(library.root)
    assert (stored.recursive, stored.thumbnail_max_edge) == (False, 128)


def test_the_cover_is_found_in_a_sub_album(library_with_album: Library) -> None:
    """父相册的封面可以是子相册里的照片 —— 递归的库本来就常这么用。"""
    nested = library_with_album.root / "京都" / "奈良"
    nested.mkdir()
    photo = nested / "鹿.jpg"
    photo.write_bytes(b"x")
    library_with_album.set_album_cover(library_with_album.root / "京都", photo)

    cover = library_with_album.folder_cover(library_with_album.root / "京都")

    assert cover is not None
    assert cover.path == photo


def test_a_cover_that_is_gone_falls_back_to_the_first_photo(
    library_with_album: Library,
) -> None:
    """封面照片被移走之后退回「第一张照片」，**而那一行配置留着**。

    留着是有意的：用户把照片挪出去又挪回来时，它又该是封面了；而界面上什么都不用说 ——
    相册卡片本来就该有一张能看的封面，用户要的是封面，不是原因。
    """
    album = library_with_album.root / "京都"
    chosen = album / "另一张.jpg"
    library_with_album.set_album_cover(album, chosen)
    chosen.unlink()

    cover = library_with_album.folder_cover(album)

    assert cover is not None
    assert cover.path == album / "一张.jpg"
    assert library_with_album.has_album_cover(album) is True


def test_an_unreadable_cover_is_skipped_like_any_other(library_with_album: Library) -> None:
    """指定的那张被 ``skip`` 挡下（云盘上没下完）时，往后找而不是卡住。

    与「第一张照片」那条规矩完全一样：不卡住，否则整张卡片永远挂着一张黑图。
    """
    album = library_with_album.root / "京都"
    chosen = album / "另一张.jpg"
    library_with_album.set_album_cover(album, chosen)

    cover = library_with_album.folder_cover(album, skip={chosen})

    assert cover is not None
    assert cover.path == album / "一张.jpg"


def test_an_album_with_no_cover_reports_none(library_with_album: Library) -> None:
    album = library_with_album.root / "京都"

    assert library_with_album.album_cover(album) is None
    assert library_with_album.has_album_cover(album) is False


def test_clear_album_cover_puts_the_first_photo_back(library_with_album: Library) -> None:
    album = library_with_album.root / "京都"
    library_with_album.set_album_cover(album, album / "另一张.jpg")

    assert library_with_album.clear_album_cover(album) is True

    cover = library_with_album.folder_cover(album)
    assert cover is not None
    assert cover.path == album / "一张.jpg"
    assert load_config(library_with_album.root).album_covers == {}


def test_clearing_a_cover_that_never_existed_writes_nothing(tmp_path: Path) -> None:
    """没设置过就**一个字节都不写**：一次没有变化的改写只是白占一次 fsync，
    在只读的库上还会凭空造出一个报错。"""
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    before = paths.config_file(tmp_path).read_bytes()

    assert library.clear_album_cover(tmp_path / "京都") is False

    assert paths.config_file(tmp_path).read_bytes() == before


def test_setting_a_cover_needs_the_config_dir(tmp_path: Path) -> None:
    """用户当初选的是「缓存到系统目录」：那个承诺是照片文件夹一个字节都不写入。

    悄悄把配置目录建出来就是毁掉它，所以这里只说清楚，不写。
    """
    album = tmp_path / "京都"
    photo = album / "一张.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    library = Library.open(tmp_path, cache_in_library=False, cache_base=tmp_path / "系统")

    with pytest.raises(ConfigError) as excinfo:
        library.set_album_cover(album, photo)

    assert "在库中缓存" in excinfo.value.message
    assert paths.has_config_dir(tmp_path) is False
    assert sorted(path.name for path in tmp_path.iterdir()) == ["京都"]


def test_a_cover_from_another_album_is_refused(library_with_album: Library) -> None:
    """兄弟相册里的照片不是这个相册的封面 —— 与解析配置时那条校验同一个判据，
    否则写进去的东西下一次打开库就会变成一句报错。"""
    library = library_with_album
    other = library.root / "奈良"
    other.mkdir()
    stranger = other / "鹿.jpg"
    stranger.write_bytes(b"x")

    with pytest.raises(ConfigError) as excinfo:
        library.set_album_cover(library.root / "京都", stranger)

    assert "不在这个相册里" in excinfo.value.message


def test_the_library_root_is_not_an_album(library_with_album: Library) -> None:
    """库根在网格上不是一张相册卡片，没有封面可设。"""
    library = library_with_album
    top = library.root / "散着的.jpg"
    top.write_bytes(b"x")

    with pytest.raises(ConfigError) as excinfo:
        library.set_album_cover(library.root, top)

    assert "库根目录不是一个相册" in excinfo.value.message


def test_an_unsupported_file_is_not_a_cover(library_with_album: Library) -> None:
    album = library_with_album.root / "京都"

    with pytest.raises(ConfigError) as excinfo:
        library_with_album.set_album_cover(album, album / "读我.txt")

    assert "支持的照片格式" in excinfo.value.message


def test_a_hand_edited_cover_that_is_not_a_photo_is_ignored(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """用户把封面那一行指到了一个 ``.txt`` 上：不当成错，而是当没设过。

    错的是那一份配置，不是一次浏览；而 ``folder_cover`` 已经会退回第一张照片，
    硬报错只会让用户连照片都看不成。
    """
    album = tmp_path / "京都"
    make_photo("一张.jpg", directory=album)
    (album / "读我.txt").write_text("x", encoding="utf-8")
    prepare_library(tmp_path, config=LibraryConfig(album_covers={"京都": "京都/读我.txt"}))
    library = Library.open(tmp_path)

    cover = library.folder_cover(album)

    assert cover is not None
    assert cover.path == album / "一张.jpg"


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
    """隐藏项与配置目录不算相册 —— 否则用户会看见一个配置目录可往里挪照片。"""
    # 配置目录名走常量（``paths.config_dir`` 的那个来源），不在这里再写一遍字面量。
    config_cache = str(paths.config_dir(tmp_path) / "cache")
    for name in ("京都", ".隐藏相册", config_cache):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
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
