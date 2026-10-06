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
    """分批写入（每批 500 行）不能丢行 —— 丢行在索引里是静默的。"""
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


def test_prune_handles_a_list_longer_than_sqlite_takes_variables(tmp_path: Path) -> None:
    """**保留名单比 SQLite 能带的变量还长时，也要清得掉。**

    ``DELETE … WHERE path NOT IN (?, ?, …)`` 看着比「扫描 + 逐个 executemany」干净，
    但 SQLite 对一次语句里能带的变量个数有上限（十万个就报 ``too many SQL
    variables``），而 ``except sqlite3.Error`` 会把那个错吞成「返回 0」——
    **静默什么都不删**，正是索引这种可再生缓存最不该有的失败方式。

    注意**不能靠分批绕开**：``NOT IN`` 分批等于「不在这一批里就删」，会把别批的活照片
    全删掉 —— 那比删不掉糟得多。所以这一段是「先查出已知的、Python 侧算出该删哪些、
    再逐个删」的两步法。
    """
    index = FacetIndex(tmp_path / "index.db")
    stored = [_ref(tmp_path, f"p{index:06d}.jpg") for index in range(_PRUNE_STRESS)]
    index.store([(ref, FULL) for ref in stored])

    # 保留绝大多数、只删掉一张：名单长度远超任何合理的变量上限。
    drop = stored[-1]
    assert index.prune([ref.path for ref in stored[:-1]]) == 1

    assert set(index.lookup([drop])) == set(), "该删的那张还在"
    assert len(index.lookup(stored)) == len(stored) - 1, "不该动的活照片被误删了"
    index.close()


#: 保留名单要长到「一条 SQL 带不下」才说明问题。SQLite 的
#: ``SQLITE_MAX_VARIABLE_NUMBER`` 默认 32766，取两倍余量。
_PRUNE_STRESS = 70000


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


def test_the_index_always_lives_in_the_library(tmp_path: Path) -> None:
    """索引永远在 ``.gallery/index.db``（ADR 0038 决策 1）。

    库外那条路（``in_library=False``）删掉了：库级的界面偏好与它同在 ``config.toml``
    旁边，没有把索引拆到另一处的理由。
    """
    index = FacetIndex.open(tmp_path)

    assert index.path == paths.config_dir(tmp_path) / "index.db"
    index.close()


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


def test_the_index_runs_in_delete_with_normal_sync(tmp_path: Path) -> None:
    """索引是缓存，提交强度就该按缓存配：``DELETE`` + ``synchronous=NORMAL``。

    默认设置下每次 ``commit`` 都要 fsync，而存照片是一张张来的 —— 等的是用户的耐心，
    存的是一份随时可以删掉重扫就回来的东西（红线 5）。

    **日志模式钉的是 ``delete`` 而不是 ``wal``**（ADR 0038 决策 5）：索引住在用户的
    库里，而库常常在云盘上 —— WAL 的 ``-wal`` / ``-shm`` 两个文件会跟着一起同步，
    两台机器各跑 Gallery 时互相覆盖有损坏索引的风险。而 WAL 的卖点（读写不互斥）
    在这里用不上：这个类全程一把锁，SQLite 层的并发根本没机会发生。

    这里量的是**真正落到连接上的 pragma 值**，不是「我们调用过 ``PRAGMA ...``」
    （那可能悄没声地失败）。
    """
    index = FacetIndex(tmp_path / "index.db")
    connection = index._connection
    assert connection is not None

    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

    assert journal_mode == "delete"
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


def test_the_index_never_writes_a_side_file_next_to_the_library(tmp_path: Path) -> None:
    """``-journal`` / ``-wal`` / ``-shm`` 一个都不该留在 ``.gallery/`` 里。

    **这条比「关掉之后收干净」更要紧**：``.gallery/`` 住在用户的照片文件夹里，而那里
    常常挂着 Dropbox / OneDrive。多出来的每一个文件都是一次同步事件，而 ``-wal`` 与
    ``-shm`` 还会跟着库跑到另一台机器上去（ADR 0038 决策 5）。

    ``-journal`` 是 ``DELETE`` 模式的事务日志，``commit`` 之后就该被删掉；``-wal`` 与
    ``-shm`` 是 WAL 的遗留，切到 DELETE 之后**连出现的机会都不该有** ——
    它们在这儿出现，就说明 pragma 没落上（静默失败），所以这条同时也是 pragma 的探针。
    """
    index = FacetIndex(tmp_path / "index.db")
    ref = _ref(tmp_path)
    index.store([(ref, FULL)])
    index.store_photos([ref], root=tmp_path)
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
