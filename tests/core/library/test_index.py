"""``gallery.core.library.index`` 的测试。

索引是缓存，不是数据：它可以随时删掉，唯一的硬要求是**别给过期的东西**。
所以这里最要紧的两条是「按 (大小, 修改时间) 判新鲜度」与「结构变了整表重建」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gallery.core import paths
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
    place_checked=True,
    place_distance_km=2.4,
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
    assert got.place_checked is True
    assert got.place_distance_km == 2.4
    index.close()


def test_an_unresolved_row_says_so(tmp_path: Path) -> None:
    """「没查过」和「查了但查不到」必须分得开，否则每开一次库就重算一遍。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, PhotoFacets(latitude=34.215, longitude=108.965))])

    got = index.lookup([ref])[ref.path]
    assert got.place_checked is False
    assert got.place_distance_km is None
    index.close()


def test_a_checked_row_with_no_city_keeps_the_flag(tmp_path: Path) -> None:
    """附近没有城市也是结论：三列留空，但别再问一次。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, PhotoFacets(latitude=0.0, longitude=0.0, place_checked=True))])

    assert index.lookup([ref])[ref.path].place_checked is True
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


def test_forget_drops_only_the_given_paths(tmp_path: Path) -> None:
    """实时监听得知哪个路径不见了就删哪个 —— 别的相册的行不能跟着没（ADR 0013）。

    这正是 :meth:`prune` 不适合在相册模式下用的原因：它按「还活着的路径」去重，
    而「当前这一层」不等于「整个库」。
    """
    index = FacetIndex(tmp_path / "index.db")
    keep, drop = _ref(tmp_path, "keep.jpg"), _ref(tmp_path, "drop.jpg")
    index.store([(keep, FULL), (drop, FULL)])

    assert index.forget([drop.path]) == 1
    assert set(index.lookup([keep, drop])) == {keep.path}
    index.close()


def test_forget_of_nothing_or_of_unknown_paths_is_harmless(tmp_path: Path) -> None:
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    assert index.forget([]) == 0
    assert index.forget([tmp_path / "从没来过.jpg"]) == 0
    assert set(index.lookup([ref])) == {ref.path}
    index.close()


def test_forget_works_in_memory_mode(tmp_path: Path) -> None:
    """索引退化成内存时（库所在卷只读）同样要能删。"""
    index = FacetIndex(None)
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])

    assert index.forget([ref.path]) == 1
    assert index.lookup([ref]) == {}


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

    assert inside.path == paths.config_dir(tmp_path) / "index.db"
    assert outside.path is not None and "系统缓存" in str(outside.path)
    inside.close()
    outside.close()


def test_after_close_the_index_no_longer_holds_a_path(tmp_path: Path) -> None:
    """**关掉之后不该还能被偷偷重新打开。**

    过去 ``close()`` 只把 ``_connection`` 置空、``_path`` 留着，于是下一次
    ``store`` 会在「已经关掉」的状态上重新 ``connect`` 同一个文件 —— 在多数平台上
    这会成功（SQLite 自己管着自己的生命周期），所以它一直没人发现，但它意味着
    「关掉」其实没说清：数据去哪了、还写不写，都不确定。
    """
    index = FacetIndex(tmp_path / "index.db")
    index.store([(_ref(tmp_path, "一张.jpg"), FULL)])
    assert index.persistent

    index.close()

    assert not index.persistent
    assert index.path is None, "路径还在，就会有人重新连上它"


# ---- 提交强度按缓存配（不是按数据配）----------------------------------------


def test_the_index_runs_in_wal_with_normal_sync(tmp_path: Path) -> None:
    """索引是缓存，提交强度就该按缓存配：``WAL`` + ``synchronous=NORMAL``。

    默认设置下每次 ``commit`` 都要 fsync，而存照片是一张张来的 —— 等的是用户的耐心，
    存的是一份随时可以删掉重扫就回来的东西（红线 5）。因此这里钉的是**真正落到连接上
    的那两个 pragma 值**，不是「我们调用过 ``PRAGMA ...``」（那可能悄没声地失败）。
    """
    index = FacetIndex(tmp_path / "index.db")
    connection = index._connection
    assert connection is not None

    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

    assert journal_mode == "wal"
    assert synchronous == 1  # NORMAL；FULL 才是 2，那正是要躲开的那份等待
    index.close()


def test_a_relaxed_index_still_round_trips(tmp_path: Path) -> None:
    """降了持久性不等于降了正确性：读出来必须还是一模一样。"""
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    index.store([(ref, FULL)])
    index.close()

    # 重开一次：WAL 是持久的设置，重连仍然落在同一套设置上，数据也还在。
    reopened = FacetIndex(tmp_path / "index.db")
    assert reopened.lookup([ref]) == {ref.path: FULL}
    reopened.close()


def test_wal_leaves_no_side_files_behind_a_clean_close(tmp_path: Path) -> None:
    """``-wal`` / ``-shm`` 是 ``WAL`` 带来的两个新文件，正常关掉就该被 SQLite 收走。

    缓存目录里多两个文件本身不算事故，但它们**不该在关掉之后还赖着** —— 那说明连接
    没有正常关闭（而本模块把「关掉之后不再偷偷重开」当成一条承诺，见
    ``test_after_close_the_index_no_longer_holds_a_path``）。
    """
    index = FacetIndex(tmp_path / "index.db")
    index.store([(_ref(tmp_path), FULL)])
    assert (tmp_path / "index.db-wal").exists(), "开着 WAL 时就该有这个文件"

    index.close()

    assert sorted(path.name for path in tmp_path.iterdir()) == ["index.db"]


def test_a_corrupt_index_degrades_to_memory_instead_of_crashing(tmp_path: Path) -> None:
    """**加 pragma 不能换来一次崩溃。**

    ``sqlite3.connect`` 是懒的：对着一个不是数据库的文件它照样连上，报错要等到第一条
    语句。所以新加的 ``PRAGMA journal_mode=WAL`` 有可能成为第一个碰到坏库的地方 ——
    它必须照样降级成内存模式（本次会话照常用，下次重扫就重建），而不是把异常抛出去。
    """
    path = tmp_path / "index.db"
    path.write_bytes("这不是 SQLite 数据库".encode() * 64)
    ref = _ref(tmp_path)

    index = FacetIndex(path)

    assert index.persistent is False
    index.store([(ref, FULL)])
    assert index.lookup([ref]) == {ref.path: FULL}, "降级之后本次会话照常用"


class _VolumeWithoutSharedMemory(sqlite3.Connection):
    """模拟一个只读 / 不支持共享内存的卷：WAL 那句 pragma 一执行就报错。"""

    def execute(self, sql: str, *args: object) -> object:
        if sql.startswith("PRAGMA journal_mode"):
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return super().execute(sql, *args)


def test_a_failure_to_relax_durability_does_not_take_the_index_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pragma 失败**只记一条日志**：那个索引仍然能用。

    最常见的失败是「那个卷只读 / 不支持共享内存」—— WAL 要建 ``-wal`` 与 ``-shm`` 两个
    文件。这种情况下索引本来就能只读地开着（表已经在了，``CREATE TABLE IF NOT EXISTS``
    不写盘），不能因为一句 pragma 就把它整个打成内存模式。
    """
    real_connect = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = _VolumeWithoutSharedMemory
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)

    assert index.persistent, "pragma 失败不等于索引不可用"
    index.store([(ref, FULL)])
    assert index.lookup([ref]) == {ref.path: FULL}
    index.close()
