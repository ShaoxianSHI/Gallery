"""整个库搬家 / 复制之后，事件与照片的关联还在（ADR 0086）。

绑定与封面存的是**相对库根的键**，所以把文件夹挪个位置、换台机器、或者复制一份，
键都还指得对；而读出来仍然是绝对路径，界面那一层一个字都不用改。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.events.kinds import COVER
from gallery.core.events.schema import BINDINGS_TABLE, EVENTS_TABLE
from gallery.core.events.store import EventDraft
from gallery.core.library import Library
from gallery.core.library.search import make_condition, parse_input
from gallery.core.metadata.journal import read_records


@pytest.fixture
def library(tmp_path: Path, make_photo: Callable[..., Path]) -> Library:
    root = tmp_path / "库"
    root.mkdir(parents=True)
    make_photo("海边-01.jpg", directory=root)
    make_photo("海边-02.jpg", directory=root / "京都")
    return Library.open(root)


def _trip(library: Library, photos: list[Path]) -> int:
    event_id = library.create_event("trip", EventDraft.of(title="京都五日")).event.id
    library.bind_photos(photos, event_id)
    return event_id


def _stored_paths(root: Path) -> list[str]:
    connection = sqlite3.connect(paths.events_file(root))
    try:
        rows = connection.execute(f"SELECT path FROM {BINDINGS_TABLE} ORDER BY path").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()


def _stored_cover(root: Path, event_id: int) -> str | None:
    connection = sqlite3.connect(paths.events_file(root))
    try:
        row = connection.execute(
            f"SELECT {COVER.column} FROM {EVENTS_TABLE} WHERE id = ?", (event_id,)
        ).fetchone()
        return None if row is None or row[0] is None else str(row[0])
    finally:
        connection.close()


def _move(root: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root), str(destination))
    return destination


def test_bindings_and_the_cover_are_stored_relative_to_the_root(library: Library) -> None:
    """库里存的是键，不是绝对路径 —— 搬走之后它仍然指得对，靠的就是这一点。"""
    first = library.root / "海边-01.jpg"
    second = library.root / "京都" / "海边-02.jpg"
    event_id = _trip(library, [first, second])
    library.set_event_cover(event_id, second)
    library.close_index()

    assert _stored_paths(library.root) == ["京都/海边-02.jpg", "海边-01.jpg"]
    assert _stored_cover(library.root, event_id) == "京都/海边-02.jpg"


def test_moving_the_whole_library_keeps_the_bindings(library: Library, tmp_path: Path) -> None:
    first = library.root / "海边-01.jpg"
    second = library.root / "京都" / "海边-02.jpg"
    event_id = _trip(library, [first, second])
    library.set_event_cover(event_id, second)
    library.close_index()

    moved = _move(library.root, tmp_path / "搬家之后" / "库")

    reopened = Library.open(moved)
    try:
        assert set(reopened.events.photos_of_event(event_id)) == {
            moved / "海边-01.jpg",
            moved / "京都" / "海边-02.jpg",
        }
        found = reopened.events.events_for_photos([moved / "京都" / "海边-02.jpg"])
        assert [ref.title for ref in found[moved / "京都" / "海边-02.jpg"]] == ["京都五日"]
        event = reopened.event(event_id)
        assert event is not None and event.cover_path == moved / "京都" / "海边-02.jpg"
        assert not reopened.find_orphan_bindings().found
    finally:
        reopened.close_index()


def test_copying_the_whole_library_gives_the_copy_its_own_bindings(
    library: Library, tmp_path: Path
) -> None:
    """复制出来的库是一个独立的库 —— 两边各看各的照片，不再争同一批绝对路径。"""
    photo = library.root / "海边-01.jpg"
    event_id = _trip(library, [photo])
    library.close_index()

    copied = tmp_path / "副本"
    shutil.copytree(library.root, copied)

    copy = Library.open(copied)
    try:
        assert copy.events.photos_of_event(event_id) == (copied / "海边-01.jpg",)
        original = Library.open(library.root)
        try:
            assert original.events.photos_of_event(event_id) == (photo,)
        finally:
            original.close_index()
    finally:
        copy.close_index()


def test_event_search_finds_the_photos_after_the_move(library: Library, tmp_path: Path) -> None:
    """检索里的 ``EXISTS`` 子查询也按库根把键接回绝对路径（``p.path = ? || pe.path``）。"""
    _trip(library, [library.root / "海边-01.jpg", library.root / "京都" / "海边-02.jpg"])
    library.index_photos()
    library.close_index()

    moved = _move(library.root, tmp_path / "搬走" / "库")

    reopened = Library.open(moved)
    try:
        reopened.index_photos()
        condition = make_condition(parse_input("#事件 京都五日"))
        assert condition is not None
        found = reopened.search_photos([condition])
        assert sorted(ref.name for ref in found.refs) == ["海边-01.jpg", "海边-02.jpg"]
        assert reopened.matching_paths([condition]) == {
            moved / "海边-01.jpg",
            moved / "京都" / "海边-02.jpg",
        }
    finally:
        reopened.close_index()


def test_undoing_a_deleted_event_after_the_move_restores_its_bindings(
    library: Library, tmp_path: Path
) -> None:
    """journal 里也存键 —— 撤销可以把事件放回新位置的这些照片上。"""
    second = library.root / "京都" / "海边-02.jpg"
    event_id = _trip(library, [library.root / "海边-01.jpg", second])
    library.set_event_cover(event_id, second)
    library.delete_event(event_id)
    library.close_index()

    moved = _move(library.root, tmp_path / "搬走" / "库")

    reopened = Library.open(moved)
    try:
        result = reopened.undo_last_write()
        assert result is not None and result.event is not None
        assert set(reopened.events.photos_of_event(event_id)) == {
            moved / "海边-01.jpg",
            moved / "京都" / "海边-02.jpg",
        }
        event = reopened.event(event_id)
        assert event is not None and event.cover_path == moved / "京都" / "海边-02.jpg"
    finally:
        reopened.close_index()


def test_the_journal_stores_bindings_as_keys(library: Library) -> None:
    event_id = _trip(library, [library.root / "京都" / "海边-02.jpg"])
    library.delete_event(event_id)

    record = read_records(library.root)[-1]

    assert record.payload is not None
    assert json.loads(record.payload)["bindings"] == ["京都/海边-02.jpg"]


def test_a_binding_outside_the_library_keeps_its_absolute_path(
    library: Library, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """库外路径没有「相对库根」可言，仍然是绝对路径 —— 界面够不到它，但 API 与旧记录不该被拦。"""
    outside = make_photo("外面.jpg", directory=tmp_path / "外面")
    event_id = _trip(library, [outside])
    library.close_index()

    assert _stored_paths(library.root) == [str(outside)]

    reopened = Library.open(library.root)
    try:
        assert reopened.events.photos_of_event(event_id) == (outside,)
    finally:
        reopened.close_index()
