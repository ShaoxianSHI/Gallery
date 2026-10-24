"""分区标题在模型里的行为（ADR 0089）。

分区式与卡片式的差别只有一处：分组在网格里不是一格卡片，而是**整行的一条标题**，照片跟在
它后面连续铺开。于是模型这一层要锁定的是：

- 标题行**不可选中、不可启用** —— 点按、双击、⌘/⇧ 点、⌘A、方向键与 ``setCurrentIndex``
  都落不到它身上（「不能改名、不能删、不能打开」因此不需要在每个命令里各写一遍）；
- 标题不是照片、不是相册、也不是卡片：计数、右键落点、取条目的那些问题都各答各的；
- 张数、路径与标题行号这些「按行算」的东西不能被标题搅乱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from gallery.core.library.groups import build_sections  # noqa: E402
from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef, SectionRef  # noqa: E402
from gallery.ui.browser.model import (  # noqa: E402
    ALBUM_ROLE,
    ENTRY_ROLE,
    GROUP_COUNT_ROLE,
    GROUP_ROLE,
    PATH_ROLE,
    SECTION_ROLE,
    PhotoListModel,
)


def _photo(tmp_path: Path, name: str) -> PhotoRef:
    return PhotoRef(path=tmp_path / name, size=100, mtime_ns=1)


def _group(facet: str, key: str, label: str, photos: tuple[PhotoRef, ...]) -> GroupRef:
    return GroupRef(facet=facet, key=key, label=label, photos=photos)


def _rows(tmp_path: Path) -> list[object]:
    """两条分区：第一条两张照片，第二条一张，外加一个相册卡片。"""
    photos = [_photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg"), _photo(tmp_path, "c.jpg")]
    groups = [
        _group("captured_month", "2024-05", "2024 年 5 月", (photos[0], photos[1])),
        _group("captured_month", "2024-06", "2024 年 6 月", (photos[2],)),
    ]
    return [FolderRef(path=tmp_path / "相册", mtime_ns=1), *build_sections(groups)]


def _sectioned(model: PhotoListModel, tmp_path: Path) -> PhotoListModel:
    model.reorder(_rows(tmp_path), sectioned=True)  # type: ignore[arg-type]
    return model


@pytest.fixture
def model(qt_app) -> PhotoListModel:  # type: ignore[no-untyped-def]
    del qt_app
    return PhotoListModel(icon_edge=64)


def test_the_rows_are_albums_headers_and_photos(model: PhotoListModel, tmp_path: Path) -> None:
    model = _sectioned(model, tmp_path)

    assert model.sectioned is True
    assert [type(model.entry_at(row)).__name__ for row in range(model.rowCount())] == [
        "FolderRef",
        "SectionRef",
        "PhotoRef",
        "PhotoRef",
        "SectionRef",
        "PhotoRef",
    ]
    assert model.header_rows() == (1, 4)
    assert model.is_header(1) and not model.is_header(0)


def test_a_header_is_neither_selectable_nor_enabled(model: PhotoListModel, tmp_path: Path) -> None:
    """标题行连「当前项」都当不了 —— Qt 自己会跳过它（实测行为，见 ADR 0089）。"""
    model = _sectioned(model, tmp_path)

    header = model.flags(model.index(1, 0))
    photo = model.flags(model.index(2, 0))

    assert not header & Qt.ItemFlag.ItemIsEnabled
    assert not header & Qt.ItemFlag.ItemIsSelectable
    assert photo & Qt.ItemFlag.ItemIsEnabled
    assert photo & Qt.ItemFlag.ItemIsSelectable
    # 相册卡片照旧可选。
    assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsSelectable


def test_the_counts_do_not_mistake_a_header_for_a_photo(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """``(相册数, 照片数)``：标题既不是相册也不是照片，不能靠「总行数减一减」算。"""
    model = _sectioned(model, tmp_path)

    assert model.counts() == (1, 3)


def test_the_roles_tell_a_header_apart(model: PhotoListModel, tmp_path: Path) -> None:
    model = _sectioned(model, tmp_path)
    header = model.index(1, 0)
    photo = model.index(2, 0)

    assert header.data(SECTION_ROLE) is True
    assert photo.data(SECTION_ROLE) is False
    assert isinstance(header.data(ENTRY_ROLE), SectionRef)
    assert header.data(GROUP_ROLE) is False
    assert header.data(ALBUM_ROLE) is False
    assert header.data(GROUP_COUNT_ROLE) == "2 张"
    # 路径角色对标题回组键（与卡片同一个约定），不会去问一个不存在的路径。
    assert header.data(PATH_ROLE) == "2024-05"
    assert header.data(Qt.ItemDataRole.DecorationRole) is None, "标题不画图"
    assert model.display_name(model.entry_at(1)) == "2024 年 5 月"  # type: ignore[arg-type]


def test_group_at_ignores_headers_but_section_at_finds_them(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """右键落点的判据：标题不是卡片（否则组卡片的菜单会长在标题上），另有一个取标题的入口。"""
    model = _sectioned(model, tmp_path)

    assert model.group_at(1) is None
    assert model.group_at(0) is None
    section = model.section_at(1)
    assert isinstance(section, SectionRef)
    assert section.label == "2024 年 5 月"
    assert section.count == 2
    assert model.section_at(2) is None


def test_paths_still_find_their_rows(model: PhotoListModel, tmp_path: Path) -> None:
    """标题不占「路径 → 行号」那本索引，照片与相册照旧找得到。"""
    model = _sectioned(model, tmp_path)

    assert model.row_of_path(tmp_path / "b.jpg") == 3
    assert model.shows_path(tmp_path / "b.jpg") is True
    assert model.photo_at(1) is None
    assert model.photo_at(3) is not None


def test_appending_more_photos_keeps_the_header_rows_correct(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """插行之后标题行号会变，缓存要跟着失效（否则排版会按旧行号画分隔线）。"""
    model = _sectioned(model, tmp_path)
    assert model.header_rows() == (1, 4)

    model.append([_photo(tmp_path, "d.jpg")])

    assert model.header_rows() == (1, 4), "追加在末尾，标题位置没动"
    model.insert_entry(_photo(tmp_path, "0.jpg"), whole_level=True)
    assert model.is_header(model.header_rows()[0])


def test_removing_a_photo_row_keeps_the_model_consistent(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """删掉一行照片（标题自己不会被删）：行号索引与标题表都要跟得上。"""
    model = _sectioned(model, tmp_path)

    removed = model.remove_paths([tmp_path / "b.jpg"])

    assert [entry.name for entry in removed] == ["b.jpg"]
    assert model.row_of_path(tmp_path / "b.jpg") == -1
    assert model.header_rows() == (1, 3)


def test_reordering_into_a_plain_level_drops_the_section_mode(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """模式与内容一起给：换回卡片式 / 不分组之后，``sectioned`` 与标题表一起清掉。"""
    model = _sectioned(model, tmp_path)

    model.reorder([_photo(tmp_path, "a.jpg")])

    assert model.sectioned is False
    assert model.header_rows() == ()
    assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsSelectable


def test_a_thumbnail_arriving_for_a_photo_row_still_works(
    model: PhotoListModel, tmp_path: Path
) -> None:
    """标题没有缩略图，但它的成员有：回填那一格照旧点亮，不会去碰标题。"""
    model = _sectioned(model, tmp_path)
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)

    model.set_thumbnail(tmp_path / "b.jpg", image)

    assert model.has_thumbnail(tmp_path / "b.jpg")
