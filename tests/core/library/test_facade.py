"""``gallery.core.library.facade`` 的测试。

重点是那条硬性约束：**打开库不得写入任何东西**。写盘必须由用户在弹窗里明确同意后的
``prepare_library`` 触发。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from gallery.core import paths
from gallery.core.constants import CONFIG_DIR_NAME
from gallery.core.errors import ConfigError, ImageDecodeError, LibraryNotAccessibleError
from gallery.core.events.models import EventDraft
from gallery.core.library.config import (
    LibraryConfig,
    load_config,
    prepare_library,
    save_config,
)
from gallery.core.library.facade import Library
from gallery.core.library.index import FacetIndex
from gallery.core.library.photos import FolderRef, PhotoRef


def test_open_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        Library.open(tmp_path / "不存在")

    assert "打不开" in excinfo.value.message


def test_open_file_instead_of_directory(tmp_path: Path, make_photo: Callable[..., Path]) -> None:
    with pytest.raises(LibraryNotAccessibleError) as excinfo:
        Library.open(make_photo("a.jpg"))

    assert "不是一个文件夹" in excinfo.value.message


class _Writer:
    """只回答「能不能用」的写入工具（``MetadataWriter`` 是 ``Protocol``）。"""

    def __init__(self, can: bool) -> None:
        self._can = can

    def available(self) -> bool:
        return self._can


def test_a_missing_exiftool_hides_the_metadata_editor_instead_of_failing_on_save(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**没注入 writer ≠ 能写。**

    界面据此决定要不要摆「编辑元数据…」那一条（ADR 0014 决策 4：不摆一条点下去只会
    报错的命令）。而生产路径 ``Library.open`` 从不传 writer，于是 ``None`` 说的是
    「还没探过、默认用 exiftool」，不是「能用」。

    ``.writer is None`` 时恒真的话，这条判据在生产环境下等于没有 —— 没装 exiftool 的
    机器上菜单照样出现，填完点保存才在 ``write_metadata`` 里抛错。比一开始就不出现糟糕得多。
    """
    make_photo("一张.jpg")
    library = Library.open(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "空目录"))

    assert library.writer is None, "前提不成立：生产路径不该注入 writer"
    assert library.can_write_metadata() is False
    assert library.exiftool_available() is False


def test_the_metadata_capability_is_asked_only_once(tmp_path: Path) -> None:
    """探测的结果要记住 —— 这条判据每次弹菜单、每次更新菜单可用性都会被问。"""
    library = Library.open(tmp_path)
    calls: list[int] = []

    class _Counting(_Writer):
        def available(self) -> bool:
            calls.append(1)
            return True

    library._writer = _Counting(True)
    assert library.exiftool_available() is True
    assert library.exiftool_available() is True
    assert library.can_write_metadata() is True

    assert len(calls) == 1, f"探测被问了几次：{len(calls)} 次（该只问一次）"


def test_open_makes_the_config_dir_and_keeps_thumbs_out_of_it(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """``.gallery/`` 自动建（ADR 0038 决策 2），**缩略图不进它**（决策 1）。

    一条断言把两件事都锁定：照片目录里只多出一个 ``.gallery``，而缩略图落在
    注入的系统目录下 —— 「同一个库两份缓存」那个局面正是这两条凑出来的。
    """
    make_photo("a.jpg")
    system = tmp_path.parent / "系统目录"

    library = Library.open(tmp_path, cache_base=system)

    assert paths.has_config_dir(tmp_path) is True
    assert library.config_dir_ready is True
    # 用常量而不是字面量：硬编码的话，常量改名时这个断言会在一个与改名无关的
    # 目录里凭空造出一个配置目录，然后照样全绿（tests/test_guards.py 守着这条）。
    assert sorted(path.name for path in tmp_path.iterdir()) == [CONFIG_DIR_NAME, "a.jpg"]
    assert library.thumbnails.directory == paths.thumbnail_cache_dir(tmp_path, base=system)
    assert tmp_path not in library.thumbnails.directory.parents


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


def test_a_video_has_a_thumbnail_but_no_preview_or_full(
    tmp_path: Path, make_video: Callable[..., Path]
) -> None:
    """视频只有第一帧；「大图」那两条路必须拒绝，别把 4K 当预览解出来（ADR 0090）。"""
    make_video(frames=1, directory=tmp_path)
    library = Library.open(tmp_path)
    ref = next(iter(library.iter_photos()))

    assert library.thumbnail(ref).width == 64
    with pytest.raises(ImageDecodeError):
        library.preview(ref, max_edge=32)
    with pytest.raises(ImageDecodeError):
        library.full(ref)


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


def test_a_video_can_be_an_album_cover(library_with_album: Library) -> None:
    """视频有第一帧缩略图，当封面与照片没有区别（ADR 0090）。"""
    album = library_with_album.root / "京都"
    clip = album / "片子.mp4"
    clip.write_bytes(b"\x00" * 8)

    library_with_album.set_album_cover(album, clip)

    assert load_config(library_with_album.root).album_covers == {"京都": "京都/片子.mp4"}
    cover = library_with_album.album_cover(album)
    assert cover is not None and cover.is_video is True


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
    # 「第一张」是扫描顺序的第一张：汉字按拼音排，所以「另」在「一」前面。
    assert cover.path == album / "另一张.jpg"
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
    """``.gallery/`` 建不出来时（只读卷）封面设置只说清楚，不硬写。

    **门本身还在**：``.gallery/` 存不住东西的库上，封面与界面偏好都没有地方落，
    硬写只会报错。
    """
    album = tmp_path / "京都"
    photo = album / "一张.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    library = Library.open(tmp_path)
    # 模拟只读卷：``.gallery/` 建出来了，随后又没了（沙箱里造不出真的只读卷）。
    library._config_dir_ready = False

    with pytest.raises(ConfigError) as excinfo:
        library.set_album_cover(album, photo)

    assert "没有地方保存" in excinfo.value.message


# ---- 设置菜单的库级两项（ADR 0073）-----------------------------------------


def test_set_movie_site_writes_the_config(tmp_path: Path) -> None:
    library = Library.open(tmp_path)

    library.set_movie_site("https://missav.com/en")

    assert library.config.movie_site == "https://missav.com/en"
    assert load_config(tmp_path).movie_site == "https://missav.com/en"


def test_a_bad_movie_site_leaves_the_file_alone(tmp_path: Path) -> None:
    library = Library.open(tmp_path)
    before = paths.config_file(tmp_path).read_bytes()

    with pytest.raises(ConfigError):
        library.set_movie_site("jable.tv")

    assert paths.config_file(tmp_path).read_bytes() == before


def test_setting_a_movie_site_needs_the_config_dir(tmp_path: Path) -> None:
    library = Library.open(tmp_path)
    library._config_dir_ready = False

    with pytest.raises(ConfigError) as excinfo:
        library.set_movie_site("https://missav.com/en")

    assert "写不进去" in excinfo.value.message


def test_set_cache_limit_reaches_both_the_config_and_the_cache(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    make_photo("a.jpg")
    library = Library.open(tmp_path)

    library.set_cache_limit(1024)

    assert library.config.max_cache_bytes == 1024
    assert library.thumbnails.max_bytes == 1024
    assert load_config(tmp_path).max_cache_bytes == 1024


def test_a_negative_cache_limit_is_refused(tmp_path: Path) -> None:
    library = Library.open(tmp_path)

    with pytest.raises(ConfigError):
        library.set_cache_limit(-1)


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

    assert "支持的格式" in excinfo.value.message


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


def _library_of(tmp_path: Path, make_photo: Callable[..., Path], count: int) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        make_photo(f"照片-{number:03d}.jpg", directory=root)
    return Library.open(root)


def _walk(
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
    refs: list[PhotoRef],
    size: int,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[int, int]]:
    """让被测那一段只看到 ``refs``，返回 ``[(批大小, 已扫过)]``。

    **把整库遍历换掉是必要的**：真的 ``iter_photos`` 自己也会问同一个 ``should_cancel``，
    取消为真时它一张都不交 —— 于是「收尾那半批被丢掉了」与「压根没扫到照片」在结果上
    分不出来，用例会在**测不到那一行**的情况下变绿。
    """
    monkeypatch.setattr(library, "iter_photos", lambda **_: iter(refs))
    return [
        (len(batch), scanned) for batch, scanned in library._iter_photo_batches(size, should_cancel)
    ]


def _refs_of(library: Library) -> list[PhotoRef]:
    return list(library.iter_photos())


def test_the_batch_walker_yields_full_batches_and_then_the_tail(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """成批产出：前面的都是整批，最后那半批也交出来，计数跟着上一批。"""
    library = _library_of(tmp_path, make_photo, 5)

    assert _walk(library, monkeypatch, _refs_of(library), 2) == [(2, 2), (2, 4), (1, 5)]


def test_the_tail_is_dropped_when_cancelled_but_kept_otherwise(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**不够一整批时走的是收尾那一条**，它同样要看取消 —— 这条判据唯一的家。

    它原先在 ``index_photos`` 与 ``resolve_places`` 里各写一遍（「非空 **且** 未取消」），
    抄漏一处不会报错：只会在用户按了停之后再多写一批。只有一张而批大小是 2，整批那条路
    一次都不会走，能交出来的只有收尾那半批，于是两半都钉得住。
    """
    library = _library_of(tmp_path, make_photo, 1)
    refs = _refs_of(library)

    assert _walk(library, monkeypatch, refs, 2, lambda: True) == [], "取消了还交收尾那批"
    assert _walk(library, monkeypatch, refs, 2) == [(1, 1)]


def test_the_full_batch_is_not_yielded_once_cancelled(
    tmp_path: Path, make_photo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """整批那一条也要看取消：取消了就不该再交出去。"""
    library = _library_of(tmp_path, make_photo, 2)

    assert _walk(library, monkeypatch, _refs_of(library), 2, lambda: True) == []


def test_the_batch_walker_yields_nothing_for_an_empty_library(
    tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """空库一次都不交 —— 收尾那一次不该交出一个空批（否则索引会白写一遍）。"""
    root = tmp_path / "空库"
    root.mkdir(parents=True, exist_ok=True)

    assert list(Library.open(root)._iter_photo_batches(2, None)) == []


def test_the_relative_path_helpers_share_one_calculation_but_not_their_edges(
    tmp_path: Path,
) -> None:
    """四份「相对库根」现在共用 :func:`gallery.core.paths.relative_posix`，**但两端各按目的处置**。

    | 调用点 | 就是库根 | 库外 |
    |---|---|---|
    | ``facade.album_label`` | 文件夹的名字 | 文件夹的名字 |
    | ``facade._relative_posix`` | ``None`` | ``None`` |
    | ``facets.relative_folder`` | 空串 | 绝对路径 |
    | ``search._directory_prefix`` | 空串 | 绝对路径 |

    这条用例钉的是**那两个边角故意不一样**。「看着像重复、顺手合成一个」正是这里最危险的
    改动：合成之后某一处的菜单名或分组键会悄悄换写法，而没有任何东西报错 —— 因为它们
    各自都还是「算得出一个字符串」。
    """
    from gallery.core.library import facade as facade_module
    from gallery.core.library.search import _directory_prefix
    from gallery.core.metadata.facets import relative_folder

    root = tmp_path / "库"
    (root / "旅行").mkdir(parents=True)
    outside = tmp_path / "别处"
    outside.mkdir()
    library = Library.open(root)

    # 就是库根的那一档
    assert library.album_label(root) == "库", "菜单里要写名字，不能是一个孤零零的点"
    assert facade_module._relative_posix(root, root) is None, "库根不是一张卡片，没有「相对位置」"
    assert relative_folder(root / "a.jpg", root) == "", "照片就在根下 —— 相册是空的"
    assert _directory_prefix(root, root) == "", "空串只有一个含义：它就是库根"

    # 库外的那一档
    assert library.album_label(outside) == "别处"
    assert facade_module._relative_posix(root, outside) is None
    assert relative_folder(outside / "a.jpg", root) == str(outside), "库外宁可啰嗦也别显示成空"
    assert _directory_prefix(outside, root) == str(outside)

    # 正常的相对路径四边一致
    assert library.album_label(root / "旅行") == "旅行"
    assert facade_module._relative_posix(root, root / "旅行") == "旅行"
    assert relative_folder(root / "旅行" / "a.jpg", root) == "旅行"
    assert _directory_prefix(root / "旅行", root) == "旅行"


# ---- 库文件被别的程序（云盘）换掉（ADR 0064）---------------------------------


def _replace_with_an_empty_database(target: Path, tmp_path: Path) -> None:
    """把 ``target`` 换成**另一份同名的空库**：云盘同步与「另存为」都是这个形状。"""
    other = tmp_path / "换上去的那一份"
    sqlite3.connect(other).close()
    os.replace(other, target)


def test_a_replaced_events_database_is_reopened(tmp_path: Path) -> None:
    """云盘换掉 ``events.db`` 之后：读要看见换上去的那一份，写要落进它。

    这就是那次事故的形状 —— 封面下载成功（纯文件操作），事件却没建起来；而读还一直给旧内容，
    于是「删掉的东西还在」「重复导入说库里已经有了」。
    """
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    library.create_event("trip", EventDraft(values={"title": "京都五日"}))

    _replace_with_an_empty_database(paths.events_file(tmp_path), tmp_path)

    assert library.all_events() == ()
    written = library.create_event("trip", EventDraft(values={"title": "奈良一日"}))
    assert written.event.id == 1  # 落进了换上去的那一份
    assert [event.text("title") for event in library.all_events()] == ["奈良一日"]


def test_replacing_the_index_keeps_the_events_readable(tmp_path: Path) -> None:
    """索引那份被换掉、连接重开之后，事件库要跟着重新挂上 —— 不然事件整段用不了。"""
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    library.create_event("trip", EventDraft(values={"title": "京都五日"}))

    _replace_with_an_empty_database(paths.index_file(tmp_path), tmp_path)

    assert library.facet_index.count_photos() == 0  # 读到的是换上去的那一份
    assert [event.text("title") for event in library.events.all_events()] == ["京都五日"]


def test_a_write_refused_with_a_readonly_database_is_retried_once(tmp_path: Path) -> None:
    """一次真的 ``SQLITE_READONLY``：重开一次连接再试一遍，那一次就接着干完了。

    拿 ``PRAGMA query_only`` 造这个错 —— 重开出来的连接不带这个开关，重试因此会成功，
    与真实事故里「文件被换掉，重开一次接着干」是同一条路。
    """
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    connection = library.facet_index.connection
    assert connection is not None
    connection.execute("PRAGMA query_only=1")

    written = library.create_event("trip", EventDraft(values={"title": "京都五日"}))

    assert written.event.text("title") == "京都五日"
    assert [event.text("title") for event in library.all_events()] == ["京都五日"]


def test_the_index_connection_is_opened_once_under_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """几个线程同时第一次访问 ``facet_index``，只许建出**一条**连接。

    原先那里是「没有就建」而没有锁：几个线程会各建一个 ``FacetIndex``，于是同一份 ``index.db`` /
    ``events.db`` 上出现两条连接，其中一条写的时候报 ``database is locked`` —— 又一次冒充
    「未预期的问题」。把构造拉长一点，这条竞态才是**必然**发生而不是碰运气。
    """
    prepare_library(tmp_path)
    library = Library.open(tmp_path)
    built: list[FacetIndex] = []
    original = FacetIndex.__init__

    def counting(self: FacetIndex, *args: object, **kwargs: object) -> None:
        time.sleep(0.01)
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        built.append(self)

    monkeypatch.setattr(FacetIndex, "__init__", counting)
    started = threading.Barrier(8)
    opened: list[FacetIndex] = []

    def open_it() -> None:
        started.wait()
        opened.append(library.facet_index)

    threads = [threading.Thread(target=open_it) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1
    assert len(set(map(id, opened))) == 1
