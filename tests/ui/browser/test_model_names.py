"""模型上「文件名显示成什么」：隐藏扩展名只影响照片，不动排序（视图菜单那一项）。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402


def _photo(tmp_path: Path, name: str) -> PhotoRef:
    return PhotoRef(path=tmp_path / name, size=100, mtime_ns=1)


@pytest.fixture
def model(qt_app) -> PhotoListModel:  # type: ignore[no-untyped-def]
    del qt_app
    return PhotoListModel(icon_edge=64)


def _display(model: PhotoListModel, row: int) -> object:
    return model.data(model.index(row, 0), int(Qt.ItemDataRole.DisplayRole))


def test_photos_show_their_extension_by_default(tmp_path: Path, model: PhotoListModel) -> None:
    model.append([_photo(tmp_path, "海边日落.jpg")])

    assert _display(model, 0) == "海边日落.jpg"


def test_hiding_extensions_keeps_the_stem_of_a_photo(tmp_path: Path, model: PhotoListModel) -> None:
    model.append([_photo(tmp_path, "海边日落.jpg")])

    model.set_show_extensions(False)

    assert _display(model, 0) == "海边日落"


def test_hiding_extensions_leaves_album_names_alone(tmp_path: Path, model: PhotoListModel) -> None:
    """相册名里的点不是扩展名，整段都要留着。"""
    model.append([_photo(tmp_path, "海边日落.jpg")])
    model.insert_entry(FolderRef(path=tmp_path / "2025.07 苏州", mtime_ns=1))

    model.set_show_extensions(False)

    assert _display(model, 0) == "2025.07 苏州"
    assert _display(model, 1) == "海边日落"


def test_the_sort_order_still_uses_the_full_name(tmp_path: Path, model: PhotoListModel) -> None:
    """隐藏扩展名只是显示：排序键不能跟着变，否则切一下顺序就跳。"""
    model.append([_photo(tmp_path, "b.jpg"), _photo(tmp_path, "a.png")])
    before = [entry.name for entry in model.entries()]

    model.set_show_extensions(False)

    assert [entry.name for entry in model.entries()] == before
