"""模型上的「增量改动」：实时监听靠它把一格照片就地换掉（ADR 0013）。

网格要重新排版才有感，但整层重排会让整屏闪回占位框再一张张弹回来 —— 各处注释里
反复回避的正是这件事。所以这里锁定的是「同一个路径在网格上只可能有一行」，
以及插进去的那一行**位置是对的**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402


def _photo(tmp_path: Path, name: str) -> PhotoRef:
    return PhotoRef(path=tmp_path / name, size=100, mtime_ns=1)


@pytest.fixture
def model(qt_app) -> PhotoListModel:  # type: ignore[no-untyped-def]
    del qt_app
    return PhotoListModel(icon_edge=64)


def test_a_new_entry_lands_before_photos(tmp_path: Path, model: PhotoListModel) -> None:
    """相册永远排在照片前面：新相册不能被追加到一张照片后面去。"""
    model.append([_photo(tmp_path, "一张.jpg")])

    row = model.insert_entry(FolderRef(path=tmp_path / "相册", mtime_ns=1))

    assert row == 0
    assert [entry.name for entry in model.entries()] == ["相册", "一张.jpg"]


def test_a_new_entry_lands_among_its_namesakes(tmp_path: Path, model: PhotoListModel) -> None:
    model.append([_photo(tmp_path, "a.jpg"), _photo(tmp_path, "c.jpg")])

    model.insert_entry(_photo(tmp_path, "b.jpg"))

    assert [entry.name for entry in model.entries()] == ["a.jpg", "b.jpg", "c.jpg"]


def test_the_first_of_all_still_lands_first(tmp_path: Path, model: PhotoListModel) -> None:
    """比整层都小的那个要插到最前面，而不是被追加到末尾。"""
    model.append([_photo(tmp_path, "b.jpg"), _photo(tmp_path, "c.jpg")])

    assert model.insert_entry(_photo(tmp_path, "a.jpg")) == 0


def test_a_new_entry_lands_by_pinyin_among_chinese_names(
    tmp_path: Path, model: PhotoListModel
) -> None:
    """插行用的是同一套拼音键：王（wang）要落在李（li）与张（zhang）中间。"""
    model.append([_photo(tmp_path, "李.jpg"), _photo(tmp_path, "张.jpg")])

    assert model.insert_entry(_photo(tmp_path, "王.jpg")) == 1
    assert [entry.name for entry in model.entries()] == ["李.jpg", "王.jpg", "张.jpg"]


def test_an_entry_is_never_on_two_rows(tmp_path: Path, model: PhotoListModel) -> None:
    """监听报上来的新增与撤销取回可能撞上同一张：只该有一行。"""
    picture = _photo(tmp_path, "一张.jpg")
    model.append([picture])

    row = model.insert_entry(_photo(tmp_path, "一张.jpg"))

    assert row == 0
    assert model.rowCount() == 1


def test_append_skips_what_is_already_there(tmp_path: Path, model: PhotoListModel) -> None:
    """扫描进行中时用户又丢进来一张：两处都记录就会出现两格同一张照片。"""
    model.append([_photo(tmp_path, "一张.jpg")])

    model.append([_photo(tmp_path, "一张.jpg"), _photo(tmp_path, "两张.jpg")])

    assert [entry.name for entry in model.entries()] == ["一张.jpg", "两张.jpg"]


def test_append_keeps_the_duplicates_inside_one_batch_away(tmp_path: Path, model: PhotoListModel):
    """同一批里重复的那一条也只留一份。"""
    model.append([_photo(tmp_path, "一张.jpg"), _photo(tmp_path, "一张.jpg")])

    assert model.rowCount() == 1


def test_whole_level_places_a_photo_from_any_folder(tmp_path: Path, model: PhotoListModel) -> None:
    """平铺模式下整棵子树摊在一层：新照片要按名字插进整层，而不是追加到末尾。"""
    model.append(
        [
            _photo(tmp_path / "旅行", "b.jpg"),
            _photo(tmp_path, "d.jpg"),
        ]
    )

    model.insert_entry(_photo(tmp_path / "旅行", "c.jpg"), whole_level=True)

    assert [entry.name for entry in model.entries()] == ["b.jpg", "c.jpg", "d.jpg"]


def test_remove_paths_takes_the_rows_out(tmp_path: Path, model: PhotoListModel) -> None:
    """磁盘上少了一个文件：界面上就该少一格，且不必重扫整层。"""
    keep, drop = _photo(tmp_path, "留下.jpg"), _photo(tmp_path, "没了.jpg")
    model.append([keep, drop])

    removed = model.remove_paths([drop.path])

    assert [entry.name for entry in removed] == ["没了.jpg"]
    assert [entry.name for entry in model.entries()] == ["留下.jpg"]
    assert model.row_of_path(drop.path) == -1
    assert model.row_of_path(keep.path) == 0, "删一行之后行号要重排"


def test_remove_paths_of_unknown_paths_does_nothing(tmp_path: Path, model: PhotoListModel) -> None:
    model.append([_photo(tmp_path, "一张.jpg")])

    assert model.remove_paths([tmp_path / "从来没来过.jpg"]) == []
    assert model.rowCount() == 1


def test_group_cards_are_not_removed_by_path(tmp_path: Path, model: PhotoListModel) -> None:
    """分组卡片不是磁盘上的东西：按路径删照片不该把卡片一起摘掉。"""
    photo = _photo(tmp_path, "a.jpg")
    group = GroupRef(facet="country", key="France", label="法国", photos=(photo,))
    model.reorder([group, photo])

    model.remove_paths([photo.path])

    assert [entry.name for entry in model.entries()] == ["法国"]


def test_forget_thumbnails_makes_the_row_ask_again(tmp_path: Path, model: PhotoListModel) -> None:
    """文件被改过之后，手里那张缩略图不再作数：那一行要能重新去要一张。"""
    picture = _photo(tmp_path, "被改过.jpg")
    model.append([picture])

    # 没缓存过就不要动任何东西
    model.forget_thumbnails([picture.path])
    assert model.has_thumbnail(picture) is False
    assert model.needs_thumbnail(picture) is True


def test_group_cards_are_counted_apart_from_albums_still(tmp_path: Path, model: PhotoListModel):
    """模型原有的分组计数不受影响（这里只补一条锁定，别让后面的改动碰坏它）。"""
    photo = _photo(tmp_path, "a.jpg")
    model.reorder(
        [
            FolderRef(path=tmp_path / "相册", mtime_ns=1),
            GroupRef(facet="country", key="France", label="法国", photos=(photo,)),
            photo,
        ]
    )

    assert model.counts() == (1, 1)
    assert model.group_count() == 1
