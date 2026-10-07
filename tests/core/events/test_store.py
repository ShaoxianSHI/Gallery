"""事件库：建表、读写、绑定（ADR 0043）。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from gallery.core import paths
from gallery.core.events import store as store_module
from gallery.core.events.kinds import EVENT_KINDS, SQL_TYPES, TRIP, fields_of
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
_EXPECTED_EVENTS = [
    ("id", "INTEGER", 0),
    ("kind", "TEXT", 1),
    ("title", "TEXT", 1),
    ("started_on", "TEXT", 0),
    ("ended_on", "TEXT", 0),
    ("note", "TEXT", 0),
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


def test_a_kind_table_has_exactly_the_columns_its_fields_declare(
    connection: sqlite3.Connection, events: EventStore
) -> None:
    """类型扩展表逐列钉住 —— 列名与类型两样都要对。

    类型不是「好不好看」：花费存成 ``TEXT`` 的话 ``#花费 >3000`` 会被 SQLite 按文本比，
    于是 ``"900" > "3000"`` 为真。
    """
    assert _table_info(connection, "event_trip") == _EXPECTED_TRIP


def test_every_declared_column_is_readable_through_the_store(events: EventStore) -> None:
    """**每个声明的字段都真的写得进、读得出**（ADR 0043 决策 3 的行为面）。

    上面两条钉的是表长什么样，这一条钉的是读写语句能不能跑 —— ADR 0026 那个 ``f.captured_at``
    的教训是「列名错了要到 SQLite 执行才说 ``no such column``」，而那正是这条测试在做的事。
    """
    filled = {
        field.id: "1" if field.sql_type in ("INTEGER", "REAL") else f"{field.id}-值"
        for field in fields_of(TRIP)
    }
    filled["started_on"], filled["ended_on"] = "2026-05-16", "2026-05-20"
    created = events.create("trip", EventDraft(values=filled)).event

    assert events.event(created.id) == created
    for field in fields_of(TRIP):
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


def test_a_version_mismatch_rebuilds_only_the_event_tables(tmp_path: Path) -> None:
    """版本对不上时重建的是**事件库自己的表**，而那句重建脚本要连类型扩展表一起列出来。

    漏写一个表名，用户那一类的事件就没了 —— 而索引那边的重建脚本同样只列它自己的两张表。

    **重开一次库**才测得到这件事（一条连接只挂一次 ``ev``，见 :func:`attach`）。
    """
    path = paths.events_file(tmp_path)
    opened = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        first = store_module.attach(opened, threading.Lock(), root=tmp_path)
        assert first is not None
        first.create("trip", _draft())
    finally:
        opened.close()

    # 下一次打开时版本是旧的：模拟将来事件库升了结构。
    stale = sqlite3.connect(path)
    stale.execute("PRAGMA user_version = 0")
    stale.commit()
    stale.close()

    reopened_connection = sqlite3.connect(tmp_path / "index.db", check_same_thread=False)
    try:
        reopened = store_module.attach(reopened_connection, threading.Lock(), root=tmp_path)
        assert reopened is not None
        assert reopened.all_events() == ()  # 重建过了，东西不在了
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


def test_an_event_without_a_date_gets_a_key_that_sinks(events: EventStore) -> None:
    """没填日期的那个组要沉到最后：组键以 ``#`` 开头（分组的排序函数据此给 0）。"""
    event = _create(events)
    photo = Path("/库/a.jpg")
    events.bind([photo], event.id)

    ref = events.events_for_photos([photo])[photo][0]

    assert ref.group_key == f"#{event.id}"


def test_renaming_a_photo_carries_its_events_over(events: EventStore) -> None:
    """改名是红线 1 唯一豁免的操作，而它会让路径变 —— 绑定跟着搬，否则用户那份心意就丢了
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
    现在只剩 :func:`event_by_id` 一处，所以两个方向都在这里钉住 —— 「找不到」那一支
    原先在整个仓库里没有一条用例直接量过（界面那条路靠 `assert ... is None` 间接覆盖）。
    """
    assert event_by_id((), 1) is None, "空清单上不该凭空冒出一条"

    created = events.create("trip", EventDraft(values={"title": "京都五日"})).event
    assert event_by_id(events.all_events(), created.id) == created
    assert event_by_id(events.all_events(), created.id + 1) is None, "不存在的 id 必须给 None"
