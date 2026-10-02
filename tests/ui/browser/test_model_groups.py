"""分组卡片在模型里的行为。

网格把「分组」当成一种和相册一样的卡片（ADR 0009），所以模型这一层要钉住的是：

- 组卡片能被计数、被取到、画得出封面；
- **一张照片当好几个组的封面时，到货的缩略图要同时点亮那几张卡**（按关键词分组就会这样）；
- 组卡片不是磁盘上的东西：不能被插回网格、不会把照片路径索引搅乱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.model import (  # noqa: E402
    ALBUM_ROLE,
    GROUP_COUNT_ROLE,
    GROUP_ROLE,
    PhotoListModel,
)


def _photo(tmp_path: Path, name: str) -> PhotoRef:
    return PhotoRef(path=tmp_path / name, size=100, mtime_ns=1)


def _group(facet: str, key: str, label: str, photos: tuple[PhotoRef, ...]) -> GroupRef:
    return GroupRef(facet=facet, key=key, label=label, photos=photos)


@pytest.fixture
def model(qt_app) -> PhotoListModel:  # type: ignore[no-untyped-def]
    del qt_app
    return PhotoListModel(icon_edge=64)


def test_group_cards_are_counted_apart_from_albums(tmp_path: Path, model: PhotoListModel) -> None:
    photos = (_photo(tmp_path, "a.jpg"),)
    model.reorder(
        [
            FolderRef(path=tmp_path / "相册", mtime_ns=1),
            _group("captured_month", "2024-05", "2024 年 5 月", photos),
            photos[0],
        ]
    )

    assert model.counts() == (1, 1)
    assert model.group_count() == 1
    assert model.group_at(1) is not None
    assert model.group_at(0) is None
    assert model.photo_at(1) is None


def test_group_roles_describe_the_card(tmp_path: Path, model: PhotoListModel) -> None:
    group = _group("captured_month", "2024-05", "2024 年 5 月", (_photo(tmp_path, "a.jpg"),))
    model.reorder([group])
    index = model.index(0, 0)

    assert model.data(index, GROUP_ROLE) is True
    assert model.data(index, GROUP_COUNT_ROLE) == "1 张"
    assert model.data(index, int(Qt.ItemDataRole.DisplayRole)) == "2024 年 5 月"
    assert model.data(index, ALBUM_ROLE) is False


def test_cover_thumbnail_lights_up_every_group_that_uses_it(
    tmp_path: Path, model: PhotoListModel
) -> None:
    """按关键词分组时一张照片可以当好几个组的封面，缩略图到货要一次点亮它们。"""
    cover = _photo(tmp_path, "封面.jpg")
    model.reorder(
        [
            _group("keyword", "海边", "海边", (cover,)),
            _group("keyword", "夜景", "夜景", (cover,)),
        ]
    )

    model.set_thumbnail(cover.path, QImage(80, 80, QImage.Format.Format_RGB32))

    assert model.has_thumbnail(cover.path)
    for row in (0, 1):
        painted = model.data(model.index(row, 0), int(Qt.ItemDataRole.DecorationRole))
        assert isinstance(painted, QPixmap) and not painted.isNull()


def test_group_card_does_not_hide_the_photo_behind_it(
    tmp_path: Path, model: PhotoListModel
) -> None:
    """组卡片没有自己的路径：``row_of_path`` 仍然只认磁盘上的东西。"""
    photo = _photo(tmp_path, "a.jpg")
    model.reorder([_group("keyword", "海边", "海边", (photo,)), photo])

    assert model.row_of_path(photo.path) == 1
    assert model.row_of_path(tmp_path / "不存在.jpg") == -1


def test_a_restored_entry_is_never_a_group(tmp_path: Path, model: PhotoListModel) -> None:
    model.reorder([_group("keyword", "海边", "海边", (_photo(tmp_path, "a.jpg"),))])

    assert model.insert_entry(_group("keyword", "海边", "海边", ())) == -1
    assert model.rowCount() == 1


def test_reorder_keeps_cached_thumbnails(tmp_path: Path, model: PhotoListModel) -> None:
    """重排只是换了位置，已经解码好的缩略图不该被丢掉重来一遍。"""
    photo = _photo(tmp_path, "a.jpg")
    model.append([photo])
    model.set_thumbnail(photo.path, QImage(80, 80, QImage.Format.Format_RGB32))

    model.reorder([photo])

    assert model.has_thumbnail(photo.path)
    assert model.row_of_path(photo.path) == 0


def test_removing_a_photo_keeps_the_group_cover_index_consistent(
    tmp_path: Path, model: PhotoListModel
) -> None:
    cover = _photo(tmp_path, "封面.jpg")
    other = _photo(tmp_path, "别的.jpg")
    model.reorder([_group("keyword", "海边", "海边", (cover,)), other])

    model.remove_rows([1])

    assert model.rowCount() == 1
    model.set_thumbnail(cover.path, QImage(80, 80, QImage.Format.Format_RGB32))
    assert model.has_thumbnail(cover.path)


def test_counts_ignore_groups_entirely(tmp_path: Path, model: PhotoListModel) -> None:
    photo = _photo(tmp_path, "a.jpg")
    model.reorder([_group("keyword", "海边", "海边", (photo,))])

    assert model.counts() == (0, 0)


def test_invalid_index_returns_nothing(model: PhotoListModel) -> None:
    assert model.group_at(0) is None
    assert model.data(QModelIndex()) is None
