"""``gallery.core.library.index`` 的测试。

索引是缓存，不是数据：它可以随时删掉，唯一的硬要求是**别给过期的东西**。
所以这里最要紧的两条是「按 (大小, 修改时间) 判新鲜度」与「结构变了整表重建」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gallery.core.library.index import SCHEMA_VERSION, FacetIndex
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets

FULL = PhotoFacets(
    captured_at=1715856722.0,
    year=2024,
    month=5,
    day=16,
    camera="Canon EOS R6",
    lens="RF35mm F1.8",
    city="Bordeaux",
    region="Nouvelle-Aquitaine",
    country="France",
    keywords=("旅行", "海边"),
    latitude=34.215,
    longitude=108.965,
    place_inferred=True,
    aperture=2.8,
    shutter=0.005,
    iso=400,
    focal=35.0,
    pixels=12_000_000.0,
)


def _ref(tmp_path: Path, name: str = "a.jpg", size: int = 10, mtime: int = 100) -> PhotoRef:
    return PhotoRef(path=tmp_path / name, size=size, mtime_ns=mtime)


def test_round_trip_keeps_every_field(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, FULL)])

    assert index.lookup([ref]) == {ref.path: FULL}
    index.close()


def test_coordinates_and_inferred_flag_survive_a_round_trip(tmp_path: Path) -> None:
    """地点是从坐标推出来的，索引里必须连同「这是推断的」一起记住。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, FULL)])

    got = index.lookup([ref])[ref.path]
    assert got.latitude == 34.215
    assert got.longitude == 108.965
    assert got.place_inferred is True
    index.close()


def test_keywords_survive_the_separator(tmp_path: Path) -> None:
    """关键词本身可能含逗号与分号，存的时候不能靠它们拼回去。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    tricky = PhotoFacets(keywords=("旅行, 海边", "a;b"))

    index.store([(ref, tricky)])

    assert index.lookup([ref])[ref.path].keywords == ("旅行, 海边", "a;b")
    index.close()


def test_unknown_photo_is_not_returned(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")

    assert index.lookup([_ref(tmp_path)]) == {}
    index.close()


def test_changed_mtime_invalidates_the_row(tmp_path: Path) -> None:
    """元数据写回会改文件的修改时间；那之后这一行必须当没有。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    touched = PhotoRef(path=ref.path, size=ref.size, mtime_ns=ref.mtime_ns + 1)

    assert index.lookup([touched]) == {}
    index.close()


def test_changed_size_invalidates_the_row(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    resized = PhotoRef(path=ref.path, size=ref.size + 1, mtime_ns=ref.mtime_ns)

    assert index.lookup([resized]) == {}
    index.close()


def test_store_replaces_instead_of_duplicating(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])
    index.store([(ref, PhotoFacets(year=1999))])

    assert index.lookup([ref])[ref.path].year == 1999
    index.close()


def test_write_chunking_matches_row_count(tmp_path: Path) -> None:
    """分批写入（每批 500 行）不能丢行 —— 这条曾经是静默写歪的那种 bug。"""
    index = FacetIndex(tmp_path / "index.db")
    refs = [_ref(tmp_path, f"{number:05}.jpg") for number in range(1200)]
    items = [
        (ref, PhotoFacets(year=2024, iso=100 + number % 400)) for number, ref in enumerate(refs)
    ]

    assert index.store(items) == len(items)

    got = index.lookup(refs)
    assert len(got) == len(refs)
    assert got[refs[1199].path].iso == 100 + 1199 % 400
    index.close()


def test_lookup_of_many_paths(tmp_path: Path) -> None:
    """一次查上千条要分批（SQLite 的变量数有上限）。"""
    index = FacetIndex(tmp_path / "index.db")
    refs = [_ref(tmp_path, f"{number:05}.jpg") for number in range(1500)]
    index.store([(ref, PhotoFacets(year=2020)) for ref in refs])

    assert len(index.lookup(refs)) == 1500
    index.close()


def test_prune_drops_photos_that_are_gone(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    keep, drop = _ref(tmp_path, "keep.jpg"), _ref(tmp_path, "drop.jpg")
    index.store([(keep, FULL), (drop, FULL)])

    assert index.prune([keep.path]) == 1
    assert set(index.lookup([keep, drop])) == {keep.path}
    index.close()


def test_clear_empties_the_index(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    index.clear()

    assert index.lookup([ref]) == {}
    index.close()


def test_memory_mode_never_touches_disk(tmp_path: Path) -> None:
    """路径为 ``None`` 时只在内存里缓存：连文件都不该被创建。"""
    index = FacetIndex(None)
    ref = _ref(tmp_path)

    index.store([(ref, FULL)])

    assert index.lookup([ref]) == {ref.path: FULL}
    assert index.persistent is False
    assert list(tmp_path.iterdir()) == []


def test_unwritable_path_degrades_to_memory(tmp_path: Path) -> None:
    """缓存写不进去不该让分组功能整个不可用：退回内存，本次会话照常用。"""
    blocker = tmp_path / "挡住"
    blocker.write_text("我是个文件，不是目录", encoding="utf-8")

    index = FacetIndex(blocker / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, FULL)])

    assert index.lookup([ref]) == {ref.path: FULL}
    assert index.persistent is False


def test_old_schema_is_rebuilt(tmp_path: Path) -> None:
    """结构版本对不上就整表重建：缓存没有迁移的价值。"""
    path = tmp_path / "index.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE facets (path TEXT PRIMARY KEY, size INTEGER)")
    legacy.execute("INSERT INTO facets VALUES ('旧的一行', 1)")
    legacy.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    legacy.commit()
    legacy.close()

    index = FacetIndex(path)
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    assert index.lookup([ref]) == {ref.path: FULL}
    index.close()


def test_open_for_library_and_external_cache(tmp_path: Path) -> None:
    """用户在开场选「缓存到系统目录」时，索引必须跟着走（照片目录零写入）。"""
    inside = FacetIndex.open(tmp_path, in_library=True)
    outside = FacetIndex.open(tmp_path, in_library=False, base=tmp_path / "系统缓存")

    assert inside.path == tmp_path / ".gallery" / "index.db"
    assert outside.path is not None and "系统缓存" in str(outside.path)
    inside.close()
    outside.close()
