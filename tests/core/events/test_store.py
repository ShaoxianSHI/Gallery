"""事件库：建表、读写、绑定（ADR 0043）。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.errors import EventNotFoundError
from gallery.core.events import schema
from gallery.core.events import store as store_module
from gallery.core.events.kinds import (
    COVER,
    EVENT_KINDS,
    MOVIE,
    SOURCE_JAV,
    SQL_TYPES,
    EventKind,
    fields_of,
)
from gallery.core.events.schema import _BINDINGS_SCHEMA, _EVENTS_SCHEMA, _kind_schema
from gallery.core.events.store import (
    Event,
    EventDraft,
    EventRef,
    EventStore,
    event_by_id,
    events_of_kind,
    follow_moves,
)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """一条**和索引一样**的连接：自己那本库 + 挂在旁边的 ``ev``。"""
    opened = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def events(connection: sqlite3.Connection, tmp_path: Path) -> EventStore:
    attached = store_module.attach(connection, threading.Lock(), root=tmp_path)

    assert attached is not None
    return attached


def _draft(**values: str) -> EventDraft:
    """一份旅行草稿。没写名称的就是那趟默认的京都五日。"""
    return EventDraft(values={"title": "京都五日", **values})


def _create(store: EventStore, **values: str) -> Event:
    """建一个旅行并返回它 —— 大多数用例只关心那一条事件，不关心那次写动的收尾结构。"""
    return store.create("trip", _draft(**values)).event


def _update(store: EventStore, event_id: int, **values: str) -> Event:
    return store.update(event_id, _draft(**values)).event


# ---- 建表 --------------------------------------------------------------------


#: 旅行独有的那几个字段的 id，按声明的顺序。
TRIP_KEYS = ("destination", "companions", "transport", "lodging", "cost", "rating")

#: 建出来的两张表**应该长什么样**。**另写一份、不从定义里推**：schema 是由字段定义生成的，
#: 拿定义去比它自己生成的东西永远相等（这一条最初就是这么写的，改坏一个列名它照样绿）。
#: 这里手写下来，于是「表形状变了」能被看见 —— 而那一句话的意思是
#: **要么改回去，要么把 :data:`store.SCHEMA_VERSION` 加一**。
#: ``cover_path`` 是封面那一列（ADR 0048），它排在 ``note`` 之后、公共字段声明的末尾。
_EXPECTED_EVENTS = [
    ("id", "INTEGER", 0),
    ("kind", "TEXT", 1),
    ("title", "TEXT", 1),
    ("started_on", "TEXT", 0),
    ("ended_on", "TEXT", 0),
    ("note", "TEXT", 0),
    ("cover_path", "TEXT", 0),
    ("created_at", "INTEGER", 1),
    ("modified_at", "INTEGER", 1),
]

_EXPECTED_TRIP = [
    ("event_id", "INTEGER", 0),
    ("destinations", "TEXT", 0),
    ("companions", "TEXT", 0),
    ("transport", "TEXT", 0),
    ("lodging", "TEXT", 0),
    ("cost", "REAL", 0),
    ("rating", "INTEGER", 0),
]

_EXPECTED_SHOW = [
    ("event_id", "INTEGER", 0),
    ("artist", "TEXT", 0),
    ("guests", "TEXT", 0),
    ("program", "TEXT", 0),
    ("show_type", "TEXT", 0),
    ("start_time", "TEXT", 0),
    ("city", "TEXT", 0),
    ("venue", "TEXT", 0),
    ("ticket_price", "REAL", 0),
    ("paid", "REAL", 0),
    ("seat", "TEXT", 0),
    ("channel", "TEXT", 0),
    ("rating", "INTEGER", 0),
    ("thoughts", "TEXT", 0),
]


def _table_info(connection: sqlite3.Connection, table: str) -> list[tuple[str, str, int]]:
    return [
        (str(row[1]), str(row[2]).upper(), int(row[3]))
        for row in connection.execute(f"PRAGMA ev.table_info({table})")
    ]


def test_the_event_table_has_exactly_the_columns_we_agreed_on(
    connection: sqlite3.Connection, events: EventStore
) -> None:
    """公共表那一行列名与 ``NOT NULL``。名称是必填的，于是它在库里也是 ``NOT NULL``。"""
    assert _table_info(connection, "events") == _EXPECTED_EVENTS


@pytest.mark.parametrize(
    ("table", "expected"),
    [("event_trip", _EXPECTED_TRIP), ("event_show", _EXPECTED_SHOW)],
)
def test_a_kind_table_has_exactly_the_columns_its_fields_declare(
    connection: sqlite3.Connection,
    events: EventStore,
    table: str,
    expected: list[tuple[str, str, int]],
) -> None:
    """类型扩展表逐列锁定 —— 列名与类型两样都要对。

    类型不是「好不好看」：花费存成 ``TEXT`` 的话 ``#花费 >3000`` 会被 SQLite 按文本比，
    于是 ``"900" > "3000"`` 为真。
    """
    assert _table_info(connection, table) == expected


@pytest.mark.parametrize("kind", EVENT_KINDS)
def test_every_declared_column_is_readable_through_the_store(
    events: EventStore, kind: EventKind
) -> None:
    """**每个声明的字段都真的写得进、读得出**（ADR 0043 决策 3 的行为面）。

    上面两条钉的是表长什么样，这一条钉的是读写语句能不能跑 —— ADR 0026 那个 ``f.captured_at``
    的教训是「列名错了要到 SQLite 执行才说 ``no such column``」，而那正是这条测试在做的事。
    """
    filled = {
        field.id: "1" if field.sql_type in ("INTEGER", "REAL") else f"{field.id}-值"
        for field in fields_of(kind)
    }
    filled["started_on"], filled["ended_on"] = "2026-05-16", "2026-05-20"
    created = events.create(kind.id, EventDraft(values=filled)).event

    assert events.event(created.id) == created
    for field in fields_of(kind):
        assert created.text(field.id), field

    # 改一遍再读回来：两条语句（UPDATE 公共列 + REPLACE 扩展表）都真的落到了库里。
    updated = events.update(created.id, EventDraft(values=filled)).event
    assert events.event(created.id) == updated


def test_the_declared_sql_types_are_the_ones_we_can_dispatch_on() -> None:
    """建表与读写靠列的类型分派，所以声明里只能出现那几种。"""
    for kind in EVENT_KINDS:
        for field in fields_of(kind):
            assert field.sql_type in SQL_TYPES, field


def test_the_event_library_keeps_its_own_schema_version(
    connection: sqlite3.Connection, events: EventStore
) -> None:
    """事件库的版本号**是它自己那一份**（``ev.user_version``）。

    写成裸的 ``PRAGMA user_version`` 就是读写索引的版本号，于是事件表的版本检查每开一次都
    「碰巧通过」或整表重建一次 —— 而重建一次就把用户填的东西全丢了（ADR 0043 决策 1）。
    """
    assert connection.execute(f"PRAGMA {store_module.SCHEMA_NAME}.user_version").fetchone()[0] == (
        store_module.SCHEMA_VERSION
    )
    assert connection.execute("PRAGMA main.user_version").fetchone()[0] == 0


def test_the_event_library_never_uses_wal(
    connection: sqlite3.Connection, events: EventStore
) -> None:
    """日志模式钉成 ``DELETE``：``-wal`` / ``-shm`` 会跟着用户的库被云同步来回传（ADR 0038）。"""
    mode = connection.execute(f"PRAGMA {store_module.SCHEMA_NAME}.journal_mode").fetchone()[0]

    assert str(mode).lower() == "delete"


def _open_a_v1_events_db(root: Path) -> Path:
    """造一份**版本 1 的事件库**：没有封面那一列，里面有一条事件与一条绑定。

    走真代码（建表语句 + 写入）而不是手写 DDL —— 于是这张表与「上一版程序建出来的那张」逐字同形，
    唯一的差别是最后那句 ``DROP COLUMN``：加封面之前的那一版**确实没有这一列**，而用当前的
    建表语句造 fixture 的话表里已经带着它，于是「迁移有没有真的补列」这条就验不到了
    （症状是 fixture 自己变成了一个已经是最新版的库，迁移那一步全部跳过，用例照样绿）。
    """
    path = paths.events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)  # attach 平时会建这个父目录，这里是直接开文件
    legacy = sqlite3.connect(path)
    try:
        # 建表语句是给「挂在索引那条连接上」写的（带 ``ev.`` 前缀），这里去掉前缀直接建文件。
        prefix = f"{schema.SCHEMA_NAME}."
        legacy.executescript(_EVENTS_SCHEMA.replace(prefix, ""))
        legacy.executescript(_BINDINGS_SCHEMA.replace(prefix, ""))
        legacy.execute(f"ALTER TABLE events DROP COLUMN {COVER.column}")
        legacy.execute(
            "INSERT INTO events (kind, title, created_at, modified_at)"
            " VALUES ('trip', '京都五日', 1, 1)"
        )
        legacy.execute("INSERT INTO photo_events (path, event_id) VALUES ('/海边-01.jpg', 1)")
        legacy.execute("PRAGMA user_version = 1")
        legacy.commit()
        columns = [row[1] for row in legacy.execute("PRAGMA table_info(events)")]
        assert COVER.column not in columns, "这份 fixture 必须真的是「还没有封面列」的那一版"
    finally:
        legacy.close()
    return path


def test_adding_the_cover_column_keeps_the_events(tmp_path: Path) -> None:
    """**升一级版本只加一列，用户填的事件与绑定一个字节都不动**（ADR 0048 决策 3）。

    这条是整个改动里唯一一处「做错了就是不可逆的数据损失」的地方：事件是唯一一份
    重建不出来的数据（ADR 0043 决策 1），而原先「版本对不上就 ``DROP`` 全部表」那条老路
    让「加一列」等于清空用户填的全部事件与绑定。

    所以这里从**一份真的版本 1 的库**出发（:func:`_open_a_v1_events_db`），重开一次，
    然后逐项断言：事件还在、绑定还在、封面列存在且为空。

    **重开一次库**才测得到（一条连接只挂一次 ``ev``，见 :func:`attach`）。
    """
    _open_a_v1_events_db(tmp_path)

    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        store = store_module.attach(connection, threading.Lock(), root=tmp_path)
        assert store is not None
        events = store.all_events()
        assert [event.title for event in events] == ["京都五日"]
        assert store.photos_of_event(events[0].id) == (Path("/海边-01.jpg"),)
        # 旧行补出来的那一列必须是空的 —— 也就是「用组内第一张」，与加列之前的行为一样。
        assert events[0].cover_path is None
        columns = [
            str(row[1])
            for row in connection.execute(
                f"PRAGMA {store_module.SCHEMA_NAME}.table_info({store_module.EVENTS_TABLE})"
            )
        ]
        assert "cover_path" in columns
        assert (
            connection.execute(f"PRAGMA {store_module.SCHEMA_NAME}.user_version").fetchone()[0]
            == store_module.SCHEMA_VERSION
        )
    finally:
        connection.close()


def test_migrating_twice_is_the_same_as_migrating_once(tmp_path: Path) -> None:
    """迁移步是**幂等**的：新库建完表已经有封面那一列，再补一次就是 ``duplicate column name``。

    而那正是新库**第一次打开**会走的路（版本 0 → 2），所以这一条是「新建库能不能打开」。
    """
    for _ in range(2):
        connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
        try:
            assert store_module.attach(connection, threading.Lock(), root=tmp_path) is not None
        finally:
            connection.close()


def _open_a_v2_events_db(root: Path) -> Path:
    """造一份**版本 2 的事件库**：影片那一张表里还叫 ``studio``、还没有 ``source`` 那一列。

    与 :func:`_open_a_v1_events_db` 同一个道理 —— 走真代码建出最新的结构，再把 2 → 3 那一步加上去的
    两样**反着做回去**。不这么做的话 fixture 自己就是最新版，迁移那一步全部跳过，用例照样绿。
    """
    path = paths.events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(path)
    try:
        prefix = f"{schema.SCHEMA_NAME}."
        legacy.executescript(_EVENTS_SCHEMA.replace(prefix, ""))
        legacy.executescript(_BINDINGS_SCHEMA.replace(prefix, ""))
        legacy.execute(_kind_schema(MOVIE).replace(prefix, ""))
        legacy.execute(f"ALTER TABLE {MOVIE.table} DROP COLUMN source")
        legacy.execute(f"ALTER TABLE {MOVIE.table} RENAME COLUMN creator TO studio")
        legacy.execute(
            "INSERT INTO events (kind, title, created_at, modified_at)"
            " VALUES ('movie', '一部影片', 1, 1)"
        )
        legacy.execute(
            f"INSERT INTO {MOVIE.table} (event_id, code, studio)"
            " VALUES (1, 'IPX-660', 'IDEA POCKET')"
        )
        legacy.execute("PRAGMA user_version = 2")
        legacy.commit()
        columns = [row[1] for row in legacy.execute(f"PRAGMA table_info({MOVIE.table})")]
        assert "studio" in columns and "source" not in columns, (
            "这份 fixture 必须真的是版本 2 那一版"
        )
    finally:
        legacy.close()
    return path


def test_the_movie_migration_carries_the_author_over_and_fills_the_source(tmp_path: Path) -> None:
    """2 → 3 那一步：``studio`` 的**值要跟着改名过去**，已有的行补上「来源 = JavDB」
    （ADR 0068 决策 7）。

    回填那一条不许省：不补的话，「库里已有的影片」与「这次要导入的同一部」在判重时对不上，
    用户会拿到两份一样的影片 —— 而改名丢值是另一种静默的数据损失。
    """
    _open_a_v2_events_db(tmp_path)

    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        store = store_module.attach(connection, threading.Lock(), root=tmp_path)
        assert store is not None
        events = store.all_events()
        assert [event.title for event in events] == ["一部影片"]
        assert events[0].text("code") == "IPX-660"
        assert events[0].text("creator") == "IDEA POCKET"
        assert events[0].text("source") == SOURCE_JAV
    finally:
        connection.close()


def _open_a_v3_events_db(root: Path) -> Path:
    """造一份**版本 3 的事件库**：影片的评分列还是 ``REAL``，里面是来源站给的小数。

    与上面两份 fixture 同一个道理：把 3 → 4 那一步**反着做回去** —— 评分列声明成 ``REAL``
    （迁移只改值、不改列的类型），并放进两条真实会遇到的旧值（4.59 与 0）。
    """
    path = paths.events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(path)
    try:
        prefix = f"{schema.SCHEMA_NAME}."
        legacy.executescript(_EVENTS_SCHEMA.replace(prefix, ""))
        legacy.executescript(_BINDINGS_SCHEMA.replace(prefix, ""))
        old_schema = _kind_schema(MOVIE).replace(prefix, "")
        legacy.execute(old_schema.replace("rating INTEGER", "rating REAL"))
        legacy.executemany(
            "INSERT INTO events (kind, title, created_at, modified_at) VALUES ('movie', ?, 1, 1)",
            [("一部影片",), ("尚未人评",)],
        )
        legacy.executemany(
            f"INSERT INTO {MOVIE.table} (event_id, code, rating) VALUES (?, ?, ?)",
            [(1, "IPX-660", 4.59), (2, "ABC-001", 0)],
        )
        legacy.execute("PRAGMA user_version = 3")
        legacy.commit()
        kinds = {
            str(row[1]): str(row[2]).upper()
            for row in legacy.execute(f"PRAGMA table_info({MOVIE.table})")
        }
        assert kinds["rating"] == "REAL", "这份 fixture 必须真的是评分列还是 REAL 的那一版"
    finally:
        legacy.close()
    return path


def test_the_movie_rating_migration_rounds_old_values_into_the_five_buttons(
    tmp_path: Path,
) -> None:
    """3 → 4 那一步：来源站的小数收进 1–5，``0`` 当没评分；事件与其余字段一个都不动。

    影片评分在面板上变成五个按钮之后，``4.59`` 与任何一个按钮都对不上 —— 不迁的话，用户打开
    那个事件按保存就把评分清空了，而界面上一个字都不说。
    """
    _open_a_v3_events_db(tmp_path)

    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        store = store_module.attach(connection, threading.Lock(), root=tmp_path)
        assert store is not None
        by_title = {event.title: event for event in store.all_events()}
        assert by_title["一部影片"].text("rating") == "5"
        assert by_title["尚未人评"].text("rating") == ""

        # 旧列声明是 REAL，写进去的整数会被读成 4.0 —— 读回来仍要能与按钮上的「4」对上。
        updated = store.update(
            by_title["一部影片"].id,
            EventDraft(values={"title": "一部影片", "code": "IPX-660", "rating": "4"}),
        ).event
        assert updated.text("rating") == "4"
    finally:
        connection.close()


def _open_a_v4_events_db(root: Path) -> Path:
    """造一份**版本 4 的事件库**：绑定与封面还是绝对路径（ADR 0086 之前）。"""
    path = paths.events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    inner = root / "京都" / "海边-01.jpg"
    outside = Path("/库外/a.jpg")
    legacy = sqlite3.connect(path)
    try:
        prefix = f"{schema.SCHEMA_NAME}."
        legacy.executescript(_EVENTS_SCHEMA.replace(prefix, ""))
        legacy.executescript(_BINDINGS_SCHEMA.replace(prefix, ""))
        legacy.execute(
            f"INSERT INTO events (kind, title, created_at, modified_at, {COVER.column})"
            " VALUES ('trip', '京都五日', 1, 1, ?)",
            (str(inner),),
        )
        legacy.executemany(
            "INSERT INTO photo_events (path, event_id) VALUES (?, 1)",
            [(str(inner),), (str(outside),)],
        )
        legacy.execute("PRAGMA user_version = 4")
        legacy.commit()
    finally:
        legacy.close()
    return path


def test_the_path_migration_makes_in_library_paths_relative(tmp_path: Path) -> None:
    """4 → 5 那一步：库内的绝对路径改成键，**库外的不动**（那是旧记录或明确绑在外面的）。

    这条迁移只改值、不重建表：事件与绑定一条都不能少，读出来也仍然是原来的绝对路径。
    """
    _open_a_v4_events_db(tmp_path)

    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        store = store_module.attach(connection, threading.Lock(), root=tmp_path)
        assert store is not None
        keys = {
            str(row[0])
            for row in connection.execute(
                f"SELECT path FROM {store_module.SCHEMA_NAME}.{store_module.BINDINGS_TABLE}"
            )
        }
        assert keys == {"京都/海边-01.jpg", "/库外/a.jpg"}
        cover = connection.execute(
            f"SELECT {COVER.column} FROM {store_module.SCHEMA_NAME}.{store_module.EVENTS_TABLE}"
        ).fetchone()[0]
        assert cover == "京都/海边-01.jpg"
        assert set(store.photos_of_event(1)) == {
            tmp_path / "京都" / "海边-01.jpg",
            Path("/库外/a.jpg"),
        }
        event = store.event(1)
        assert event is not None and event.cover_path == tmp_path / "京都" / "海边-01.jpg"
    finally:
        connection.close()


def test_a_newer_events_file_is_left_alone_instead_of_rebuilt(tmp_path: Path) -> None:
    """库里的版本比这份程序新（用户降级了应用）时**不挂事件库，也不删它**。

    「读不懂一份文件」不等于「可以删掉它」：事件是唯一一份重建不出来的数据。
    界面据此让「记录为」那一行不出现（与配置目录建不出来时同一条降级路径，
    ADR 0043 决策 1），而用户那份库**原样留在磁盘上**。
    """
    path = _open_a_v1_events_db(tmp_path)
    newer = sqlite3.connect(path)
    try:
        newer.execute(f"PRAGMA user_version = {store_module.SCHEMA_VERSION + 1}")
        newer.commit()
    finally:
        newer.close()

    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        assert store_module.attach(connection, threading.Lock(), root=tmp_path) is None
        surviving = sqlite3.connect(path)
        try:
            rows = surviving.execute("SELECT title FROM events").fetchall()
        finally:
            surviving.close()
        assert rows == [("京都五日",)]
    finally:
        connection.close()


def test_every_migration_step_says_what_it_does(tmp_path: Path) -> None:
    """链条里**每一级都要有语句**（索引 0 那一级除外 —— 它是建表，由建表语句本身承担）。

    空着的一级意味着「升到那一版时什么都不做」，而 ``SCHEMA_VERSION`` 已经写了那么大：
    症状是新程序读旧库时列对不上，``no such column`` 要到第一次查询才报 ——
    界面上表现为「事件那一族全空」，而用户看不出是迁移漏了一步。
    """
    steps = schema._MIGRATIONS

    assert len(steps) == store_module.SCHEMA_VERSION
    for index, step in enumerate(steps):
        if index == 0:
            continue
        assert step, f"第 {index} 级迁移是空的"


def test_the_event_tables_are_not_rebuilt_by_a_version_mismatch(tmp_path: Path) -> None:
    """索引升版时重建的是**索引自己那两张表**，事件那几张一个都不许碰（ADR 0043 决策 1）。

    **重开一次库**才测得到这件事（一条连接只挂一次 ``ev``，见 :func:`attach`）。
    """
    _open_a_v1_events_db(tmp_path)
    opened = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        assert store_module.attach(opened, threading.Lock(), root=tmp_path) is not None
    finally:
        opened.close()

    # 索引升版：它的 ``user_version`` 一变就整表重建，而 ``ev.`` 那一份一个字都不该动。
    index = sqlite3.connect(tmp_path / "index.db")
    try:
        index.execute("DROP TABLE IF EXISTS facets")
        index.execute("DROP TABLE IF EXISTS photos")
        index.execute("PRAGMA user_version = 0")
        index.commit()
    finally:
        index.close()

    reopened_connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        store = store_module.attach(reopened_connection, threading.Lock(), root=tmp_path)
        assert store is not None
        assert [event.title for event in store.all_events()] == ["京都五日"]
        tables = {
            row[0]
            for row in reopened_connection.execute(
                f"SELECT name FROM {store_module.SCHEMA_NAME}.sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        reopened_connection.close()

    for kind in EVENT_KINDS:
        assert kind.table in tables


def test_the_library_is_unavailable_instead_of_crashing(tmp_path: Path) -> None:
    """挂不上（只读卷 / 网络盘）时返回 ``None``，不是一个空库：界面据此让「记录为」不出现。

    这里用「配置目录的位置上蹲着一个普通文件」模拟建不出目录 —— 真机上那是只读卷与断掉的
    网络盘，而两种情形在代码里走的是同一支（``mkdir`` 抛 ``OSError``）。
    """
    connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        paths.config_dir(blocked).write_text("我是个文件")
        assert store_module.attach(connection, threading.Lock(), root=blocked) is None
    finally:
        connection.close()


def test_an_unavailable_store_refuses_to_write_and_says_so(tmp_path: Path) -> None:
    """不可用时不静默什么都不做：写的一方要么看不见那个入口，要么拿到一句能读的中文。"""
    from gallery.core.errors import EventsUnavailableError

    store = EventStore(tmp_path)

    assert not store.available
    assert store.all_events() == ()
    assert store.events_for_photos([tmp_path / "a.jpg"]) == {}
    with pytest.raises(EventsUnavailableError):
        store.create("trip", _draft())


# ---- 读写 --------------------------------------------------------------------


def test_a_draft_is_normalized_on_the_way_in(events: EventStore) -> None:
    """面板交出来的是文字，存进去的是归一过的值：日期补零、多值统一分隔符、数字变数字。"""
    created = _create(
        events,
        started_on="2026-5-16",
        destination="京都, 奈良",
        companions="小王、小李",
        cost="3,280.50",
        rating="5",
    )

    assert created.started_on == "2026-05-16"
    assert {field: created.values[field] for field in TRIP_KEYS} == {
        "destination": "京都、奈良",
        "companions": "小王、小李",
        "transport": None,
        "lodging": None,
        "cost": 3280.5,
        "rating": 5,
    }


def test_an_empty_field_is_null_and_not_an_empty_string(events: EventStore) -> None:
    """空的一律存 ``NULL``：筛选与分组要把「没填」与「填了个空」当同一件事。"""
    created = _create(events, destination="", cost="")

    assert created.note is None
    assert created.ended_on is None
    assert created.values["destination"] is None
    assert created.values["cost"] is None


def test_a_month_alone_becomes_the_first_of_that_month(events: EventStore) -> None:
    """只知道「五月去的」也是常态；存成那一个月 1 号，日期范围筛选才排得进去。"""
    created = _create(events, started_on="2026-05")

    assert created.started_on == "2026-05-01"


def test_events_are_read_newest_first(events: EventStore) -> None:
    """菜单与分组都按开始日期新→旧摆，没填日期的排在最后。"""
    _create(events, title="老", started_on="2024-01-01")
    _create(events, title="没日期")
    _create(events, title="新", started_on="2026-05-16")

    assert [event.title for event in events.all_events()] == ["新", "老", "没日期"]


def test_an_update_replaces_the_whole_extension_row(events: EventStore) -> None:
    """改完只值一次 ⌘Z，所以公共列与扩展表在**同一次写**里改完，而扩展表是整行覆盖：
    ``INSERT OR REPLACE`` 会把这次没填的字段清空 —— 那不是 bug，是「面板给的就是最终状态」。"""
    created = _create(events, destination="京都", rating="5")

    updated = _update(events, created.id, **{"title": "京都五日游", "cost": "4000"})

    assert updated.title == "京都五日游"
    assert updated.values["cost"] == 4000.0
    assert updated.values["rating"] is None
    assert updated.values["destination"] is None
    assert updated.modified_at >= created.modified_at


def test_an_unknown_kind_is_refused_before_it_is_stored(events: EventStore) -> None:
    """存下一条认不出的记录，下次打开就是一条打不开的事件 —— 当场拒掉。"""
    with pytest.raises(ValueError):
        events.create("concert", _draft())

    assert events.all_events() == ()


def test_events_of_kind_splits_the_cached_list() -> None:
    """菜单要的是「这一类的全部」，而缓存里是整个库。"""
    trips = tuple(
        store_module.Event(
            id=index,
            kind="trip",
            created_at=0,
            modified_at=0,
            values={"title": f"t{index}"},
        )
        for index in (1, 2)
    )

    assert events_of_kind(trips, "trip") == trips
    assert events_of_kind(trips, "concert") == ()


def test_a_photo_can_belong_to_several_events(events: EventStore) -> None:
    """一张照片可以同时属于一次演出与一次旅行（ADR 0043 决策 7 的整个理由）。"""
    trip = _create(events, title="京都五日", started_on="2026-05-16")
    other = _create(events, title="大阪一日", started_on="2026-05-17")
    photo = Path("/库/IMG_0001.jpg")

    events.bind([photo], trip.id)
    events.bind([photo], other.id)

    found = events.events_for_photos([photo])[photo]

    assert [ref.title for ref in found] == ["大阪一日", "京都五日"]


def test_binding_the_same_photo_twice_changes_nothing(events: EventStore) -> None:
    """同一个事件里记两次不算两张 —— 多选里混进同一张是常态。"""
    event = _create(events)
    photo = Path("/库/IMG_0001.jpg")

    assert events.bind([photo], event.id) == 1
    assert events.bind([photo], event.id) == 0
    assert events.photos_of_event(event.id) == (photo,)


def test_binding_more_photos_than_one_statement_can_hold(events: EventStore) -> None:
    """一次「按日期范围加入」可能几百上千张，而一条语句塞不下那么多绑定变量。"""
    event = _create(events)
    photos = [Path(f"/库/IMG_{index:05d}.jpg") for index in range(store_module.CHUNK * 2 + 7)]

    assert events.bind(photos, event.id) == len(photos)

    found = events.events_for_photos(photos)

    assert len(found) == len(photos)
    assert all(refs == (refs[0],) and refs[0].id == event.id for refs in found.values())


def test_unbinding_removes_only_those_photos(events: EventStore) -> None:
    event = _create(events)
    keep, drop = Path("/库/keep.jpg"), Path("/库/drop.jpg")
    events.bind([keep, drop], event.id)

    assert events.unbind([drop], event.id) == 1
    assert events.photos_of_event(event.id) == (keep,)
    assert events.unbind([drop], event.id) == 0


def test_a_photo_without_events_simply_has_no_entry(events: EventStore) -> None:
    """没绑定过的照片不该在结果里占一个空列表 —— 分组那边按「有没有这份数据」判断。"""
    event = _create(events)
    events.bind([Path("/库/有.jpg")], event.id)

    assert events.events_for_photos([Path("/库/没有.jpg")]) == {}


def test_the_group_key_leads_with_the_date_and_ends_with_the_id(events: EventStore) -> None:
    """组键 ``起始日期#id``：日期在前才能按时间排，id 在后才能保证两个同名同日的旅行不同组。"""
    event = _create(events, started_on="2026-05-16")
    photo = Path("/库/a.jpg")
    events.bind([photo], event.id)

    ref = events.events_for_photos([photo])[photo][0]

    assert ref == EventRef(id=event.id, kind="trip", title="京都五日", started_on="2026-05-16")
    assert ref.group_key == f"2026-05-16#{event.id}"


def test_a_ref_carries_the_kind_fields_for_grouping(events: EventStore) -> None:
    """分组要按「旅行·目的地」这类字段归拢，所以每条引用顺带带出这类事件的字段值。

    **公共列不在其中**：名称与日期已有同名字段，重复带一份只会让「值从哪儿来」多一条路。
    """
    event = _create(events, destination="京都、奈良", cost="3,280.50", rating="5")
    photo = Path("/库/a.jpg")
    events.bind([photo], event.id)

    ref = events.events_for_photos([photo])[photo][0]

    assert ref.text("destination") == "京都、奈良"
    assert ref.text("cost") == "3280.5"
    assert ref.text("rating") == "5"
    assert ref.text("transport") == ""  # 没填的字段不进来
    assert "title" not in dict(ref.values)


def test_an_event_without_a_date_gets_a_key_that_sinks(events: EventStore) -> None:
    """没填日期的那个组要沉到最后：组键以 ``#`` 开头（分组的排序函数据此给 0）。"""
    event = _create(events)
    photo = Path("/库/a.jpg")
    events.bind([photo], event.id)

    ref = events.events_for_photos([photo])[photo][0]

    assert ref.group_key == f"#{event.id}"


def test_renaming_a_photo_carries_its_events_over(events: EventStore) -> None:
    """改名是「不删除、不移动用户照片」那条硬性约束唯一豁免的操作，而它会让路径变 ——
    绑定跟着搬，否则用户那份心意就丢了
    （ADR 0043 决策 8）。"""
    event = _create(events)
    old, new = Path("/库/旧名.jpg"), Path("/库/新名.jpg")
    events.bind([old], event.id)

    assert follow_moves(events, [(old, new, False)]) == 1
    assert events.photos_of_event(event.id) == (new,)
    assert events.events_for_photos([old]) == {}


def test_moving_a_binding_that_is_not_there_reports_it(events: EventStore) -> None:
    """没搬动过就如实报 0 条，让调用点不必猜。**重复搬也是 0** —— 应用内移动因此既自己搬
    一次、又被监听器报回来的那次搬一次（ADR 0043 决策 8）。"""
    assert follow_moves(events, [(Path("/库/没有.jpg"), Path("/库/另一个.jpg"), False)]) == 0

    event = _create(events)
    old, new = Path("/库/旧名.jpg"), Path("/库/新名.jpg")
    events.bind([old], event.id)
    assert follow_moves(events, [(old, new, False)]) == 1
    assert follow_moves(events, [(old, new, False)]) == 0


def test_renaming_an_album_carries_the_whole_subtree(events: EventStore) -> None:
    """相册换位置改的是里面每一张照片的绝对路径，一条按前缀的 UPDATE 就够。"""
    event = _create(events)
    photos = [Path("/库/京都/a.jpg"), Path("/库/京都/子相册/b.jpg")]
    events.bind(photos, event.id)

    assert follow_moves(events, [(Path("/库/京都"), Path("/库/奈良"), True)]) == 2
    assert events.photos_of_event(event.id) == (
        Path("/库/奈良/a.jpg"),
        Path("/库/奈良/子相册/b.jpg"),
    )


def test_deleting_an_event_reports_the_bindings_it_dropped(events: EventStore) -> None:
    """删除返回被连带删掉的绑定 —— 撤销要靠这份清单把它们放回去（ADR 0043 决策 5）。"""
    event = _create(events)
    photos = [Path("/库/a.jpg"), Path("/库/b.jpg")]
    events.bind(photos, event.id)

    dropped = events.delete(event.id).unbound

    assert set(dropped) == set(photos)
    assert events.all_events() == ()
    assert events.photos_of_event(event.id) == ()  # 绑定真的删了，不是留着当孤儿


def test_the_ids_are_never_reused_after_a_delete(events: EventStore) -> None:
    """``AUTOINCREMENT`` 不是可选项：rowid 复用会让「删除 → 新建 → ⌘Z」把刚建的那个覆盖掉。"""
    first = _create(events, title="先建的")
    events.delete(first.id)

    second = _create(events, title="后建的")

    assert second.id > first.id


def test_an_unreadable_kind_does_not_take_the_whole_list_down(events: EventStore) -> None:
    """库里存着一条认不出类型的事件时跳过它，而不是让整份清单打不开。"""
    kept = _create(events, title="认得的")
    events._require().execute(
        f"INSERT INTO {store_module.SCHEMA_NAME}.events"
        " (kind, title, created_at, modified_at) VALUES ('concert', '认不得的', 0, 0)"
    )

    assert [event.title for event in events.all_events()] == ["认得的"]
    assert kept.id > 0


def test_event_by_id_finds_it_or_says_there_is_none(events: EventStore) -> None:
    """按 id 取事件：找到就给那一条，找不到给 ``None``。

    ``Library.event`` 与 :meth:`EventStore.event` 曾经各写一遍这个 ``next(...)``；
    现在只剩 :func:`event_by_id` 一处，所以两个方向都在这里锁定 —— 「找不到」那一支
    原先在整个仓库里没有一条用例直接量过（界面那条路靠 `assert ... is None` 间接覆盖）。
    """
    assert event_by_id((), 1) is None, "空清单上不该凭空冒出一条"

    created = events.create("trip", EventDraft(values={"title": "京都五日"})).event
    assert event_by_id(events.all_events(), created.id) == created
    assert event_by_id(events.all_events(), created.id + 1) is None, "不存在的 id 必须给 None"


def test_deleting_an_event_that_is_already_gone_is_a_chinese_error(events: EventStore) -> None:
    """删一个**库里已经没有**的事件：要抛 ``GalleryError``，不是裸 ``KeyError``。

    界面先拿手上那份清单判一次，而那份是会话缓存的、可能已经过期 —— 于是这条很平常地上门。
    抛裸异常上去，跑在主线程的槽里没有第二个人接得住，用户看到的是「点了删除什么也没发生」。
    """
    trip = _create(events)
    events.delete(trip.id)

    with pytest.raises(EventNotFoundError) as excinfo:
        events.delete(trip.id)

    assert "已不存在" in excinfo.value.message


def test_updating_an_event_that_is_already_gone_is_a_chinese_error(events: EventStore) -> None:
    """改也一样：同一处 ``_kind_of`` 原先也抛 ``KeyError``。"""
    with pytest.raises(EventNotFoundError) as excinfo:
        events.update(999, _draft())

    assert "已不存在" in excinfo.value.message
