"""``gallery.ui.navigation`` 的测试：面包屑、上级推导、层与格的标识，都不需要 Qt。"""

from __future__ import annotations

from pathlib import Path

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef
from gallery.core.library.session import BROWSE_MODE_ALBUMS, BROWSE_MODE_FLAT
from gallery.ui.navigation import (
    breadcrumb_parts,
    entry_key,
    level_key,
    parent_of,
    row_of_entry_key,
)


def test_breadcrumb_at_root_has_one_part() -> None:
    root = Path("/照片/库")

    assert breadcrumb_parts(root, root) == [("库", root)]


def test_breadcrumb_lists_every_level() -> None:
    root = Path("/照片/库")
    current = root / "2022-复活节波尔多" / "比拉沙丘"

    parts = breadcrumb_parts(root, current)

    assert [label for label, _ in parts] == ["库", "2022-复活节波尔多", "比拉沙丘"]
    assert [path for _, path in parts] == [
        root,
        root / "2022-复活节波尔多",
        root / "2022-复活节波尔多" / "比拉沙丘",
    ]


def test_breadcrumb_falls_back_to_root_for_outside_paths() -> None:
    root = Path("/照片/库")

    assert breadcrumb_parts(root, Path("/别处/某目录")) == [("库", root)]


def test_breadcrumb_labels_root_without_name() -> None:
    parts = breadcrumb_parts(Path("/"), Path("/"))

    assert parts == [("/", Path("/"))]


def test_parent_of_walks_up_one_level() -> None:
    root = Path("/照片/库")
    current = root / "a" / "b"

    assert parent_of(root, current) == root / "a"
    assert parent_of(root, root / "a") == root


def test_parent_of_stops_at_root() -> None:
    root = Path("/照片/库")

    assert parent_of(root, root) is None


def test_parent_of_refuses_outside_paths() -> None:
    root = Path("/照片/库")

    assert parent_of(root, Path("/别处")) is None
    assert parent_of(root, Path("/照片/库2")) is None


# ---- 层与格的标识 -------------------------------------------------------------------------


def _photo(path: str) -> PhotoRef:
    return PhotoRef(path=Path(path), size=1, mtime_ns=0)


def _album(path: str) -> FolderRef:
    return FolderRef(path=Path(path), mtime_ns=0)


def _group(facet: str, key: str) -> GroupRef:
    return GroupRef(facet=facet, key=key, label=key, photos=())


def test_every_album_is_a_level_of_its_own() -> None:
    """相册模式下，一个目录就是一层。"""
    root = Path("/照片/库")

    assert level_key(BROWSE_MODE_ALBUMS, root) != level_key(BROWSE_MODE_ALBUMS, root / "海边")


def test_flat_and_albums_are_different_levels() -> None:
    """平铺模式与相册模式摆着的东西不一样，不能互相顶掉对方的记忆。"""
    root = Path("/照片/库")

    assert level_key(BROWSE_MODE_FLAT, root) != level_key(BROWSE_MODE_ALBUMS, root)


def test_a_group_level_differs_from_the_level_it_came_from() -> None:
    """进了分组就是更深的一层，退出后回到的正是原来那一层。"""
    root = Path("/照片/库")

    group = level_key(BROWSE_MODE_FLAT, root, group=("captured_year", "2024"))

    assert group != level_key(BROWSE_MODE_FLAT, root)
    assert level_key(BROWSE_MODE_FLAT, root) == level_key(BROWSE_MODE_FLAT, None)


def test_the_same_group_key_in_two_albums_is_two_levels() -> None:
    """``旅行/2024`` 与 ``家庭/2024`` 都可能分出 ``2024-05``：是两层，不是同一层。"""
    root = Path("/照片/库")

    one = level_key(BROWSE_MODE_ALBUMS, root / "旅行", group=("captured_month", "2024-05"))
    other = level_key(BROWSE_MODE_ALBUMS, root / "家庭", group=("captured_month", "2024-05"))

    assert one != other


def test_the_same_key_under_different_facets_is_two_levels() -> None:
    """组标识只在它自己的字段里唯一：「2024 年」与「2024 年 5 月」不能串。"""
    root = Path("/照片/库")

    year = level_key(BROWSE_MODE_FLAT, root, group=("captured_year", "2024"))
    month = level_key(BROWSE_MODE_FLAT, root, group=("captured_month", "2024"))

    assert year != month


def test_a_photo_and_an_album_are_both_recognized_by_path() -> None:
    """照片与相册都按路径认：同一个路径就是同一条。

    刻意不分「是照片还是相册」：一个路径要么是文件要么是目录，不可能是两者，
    而多加一段只会让改名之后对不上（改了名的还是同一条东西，只是路径变了）。
    """
    assert entry_key(_photo("/照片/库/海边/IMG_1.jpg")) == entry_key(
        _photo("/照片/库/海边/IMG_1.jpg")
    )
    assert entry_key(_album("/照片/库/海边")) == entry_key(_album("/照片/库/海边"))
    assert entry_key(_photo("/照片/库/海边/IMG_1.jpg")) != entry_key(
        _photo("/照片/库/海边/IMG_2.jpg")
    )
    assert entry_key(_album("/照片/库/海边")) != entry_key(_album("/照片/库/山巅"))


def test_a_group_is_told_apart_by_facet_as_well_as_key() -> None:
    assert entry_key(_group("captured_year", "2024")) != entry_key(_group("captured_month", "2024"))


def test_rows_are_found_by_identity_not_by_row_number() -> None:
    """重排之后行号变了，标识没变 —— 返回上一层时按标识找回来才对得上。"""
    first = _photo("/照片/库/海边/IMG_1.jpg")
    album = _album("/照片/库/海边")
    second = _photo("/照片/库/海边/IMG_2.jpg")
    entries = [album, first, second]

    assert row_of_entry_key(entries, entry_key(album)) == 0
    assert row_of_entry_key(entries, entry_key(first)) == 1
    assert row_of_entry_key(entries, entry_key(second)) == 2


def test_a_missing_entry_has_no_row() -> None:
    """记着的那一条已经没了（改名 / 移走 / 删掉）：如实说没有。"""
    entries = [_photo("/照片/库/海边/IMG_1.jpg")]

    assert row_of_entry_key(entries, entry_key(_photo("/照片/库/海边/IMG_9.jpg"))) == -1
    assert row_of_entry_key(entries, entry_key(_group("captured_year", "2024"))) == -1
    assert row_of_entry_key([], entry_key(_photo("/照片/库/海边/IMG_1.jpg"))) == -1
