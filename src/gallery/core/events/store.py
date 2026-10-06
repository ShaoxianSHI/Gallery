"""事件库（配置目录下的 ``events.db``）：事件定义与照片↔事件的绑定（ADR 0043）。

**它挂在索引那条 SQLite 连接上**（别名 ``ev``），于是 SQL 层仍然是一张库（``#目的地 京都``
能走子查询），而生命周期上仍是两个文件、两份 ``user_version``。这么定是因为事件是用户手写
的数据，而索引的表结构版本一对不上就**整表重建** —— 事件没有任何一条能从照片重扫出来。

三件必须分别处理、否则会静默出事的事（ADR 0043 决策 1）：

- ``PRAGMA user_version`` 与 ``journal_mode`` 都是**每个库各一份**，事件那份必须写成
  ``ev.`` 前缀。裸写会读到索引的版本号，于是事件表的版本检查每开一次都整表重建一次。
- events.db 是**数据**不是缓存：``synchronous`` 不跟着索引降级，只把 ``journal_mode``
  钉成 ``DELETE``（``-wal`` / ``-shm`` 跟着用户的库在云盘上互相覆盖，ADR 0038 决策 5）。
- 挂不上时只记一条日志，索引其余功能照常 —— 「记录为」那一行随之不出现。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from gallery.core import paths
from gallery.core.errors import EventsUnavailableError
from gallery.core.events.kinds import (
    COMMON_FIELDS,
    EVENT_KINDS,
    EventField,
    EventKind,
    join_multi,
    kind_by_id,
    normalize_day,
    parse_cost,
    split_multi,
)
from gallery.core.metadata.journal import (
    ACTION_EVENT,
    ACTION_EVENT_CREATE,
    ACTION_EVENT_DELETE,
    ACTION_UNDO_EVENT,
    RESULT_OK,
    FieldChange,
    WriteRecord,
    append_record,
)

logger = logging.getLogger(__name__)

#: 挂到索引连接上时的别名。搜索层的子查询按它写（``ev.photo_events``）。
SCHEMA_NAME: Final = "ev"

#: 事件库的表结构版本。**只管 events.db 自己那份** ``PRAGMA ev.user_version``，
#: 与索引的 ``SCHEMA_VERSION`` 无关（索引升版会重建它的表，事件库不会）。
SCHEMA_VERSION: Final = 1

#: 一条语句里最多塞多少个绑定变量。``IN (…)`` 与多列插入都按它分批：
#: SQLite 的上限是 999（老版本），一行的绑定数还不止一个，所以按**行**取这个数的一半。
CHUNK: Final = 400

#: 绑定表。它与类型无关，所以写死在这儿（扩展表按类型生成，见 :func:`_kind_schema`）。
_BINDINGS_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS ev.photo_events (
    path     TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    PRIMARY KEY (path, event_id)
);
CREATE INDEX IF NOT EXISTS ev.photo_events_event ON photo_events(event_id);
"""

#: ``events`` 表里除 :data:`COMMON_FIELDS` 之外那几列：身份与记账时间。
_EVENT_HEAD: Final = (("id", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("kind", "TEXT NOT NULL"))
_EVENT_TAIL: Final = (("created_at", "INTEGER NOT NULL"), ("modified_at", "INTEGER NOT NULL"))


def declaration_of(field: EventField) -> str:
    """建表时这一列写什么类型。``DAY`` 落成 ``TEXT``（SQLite 没有日期类型）。"""
    base = "TEXT" if field.sql_type == "DAY" else field.sql_type
    return base + (" NOT NULL" if field.required else "")


def _event_columns() -> tuple[tuple[str, str], ...]:
    """``events`` 表的列（名字 + 完整类型声明）。

    **从 :data:`COMMON_FIELDS` 生成**，与扩展表同一个道理：手写一份列清单就有两处会漂，
    而漂的那一处要到 SQLite 执行才报错。``required`` 的字段带上 ``NOT NULL`` ——
    校验在面板那一层，这里是兜底。
    """
    return (
        *_EVENT_HEAD,
        *((field.column, declaration_of(field)) for field in COMMON_FIELDS),
        *_EVENT_TAIL,
    )


#: ``events`` 的列名，顺序与 :func:`_event_columns` 一致。行按名字取，不按位置 ——
#: 位置错一位不会报错，只会让两个字段静默串个儿。
_EVENT_NAMES: Final = tuple(name for name, _declaration in _event_columns())

#: 取一行事件时那串列名。带 ``e.`` 前缀是因为查询会 ``JOIN`` 扩展表，而两边的列名不许撞。
_EVENT_SELECT: Final = ", ".join(f"e.{name}" for name in _EVENT_NAMES)

#: 建 ``events`` 表与它的索引。
_EVENTS_SCHEMA: Final = (
    "CREATE TABLE IF NOT EXISTS ev.events (\n    "
    + ",\n    ".join(f"{name} {declaration}" for name, declaration in _event_columns())
    + "\n);"
    "CREATE INDEX IF NOT EXISTS ev.events_kind_started ON events(kind, started_on DESC);"
)

type SqlValue = str | int | float | None


@dataclass(frozen=True, slots=True)
class Event:
    """一个事件：**所有字段的值都在 :attr:`values` 里**，键是 ``EventField.id``。

    公共列（名称、日期、备注）与类型独有的字段一视同仁 —— 面板预填因此就是一次循环，而
    「公共列有哪几个」只有 :data:`COMMON_FIELDS` 一处说了算。常用的那几样另有同名属性可读。

    **是字典而不可哈希**：字段随类型增删，具名属性做不到（``PhotoFacets`` 用元组是因为它的
    字段是写死的）。别拿它当键用。
    """

    id: int
    kind: str
    created_at: int
    modified_at: int
    values: dict[str, SqlValue] = field(default_factory=dict)

    def text(self, field_id: str) -> str:
        """某个字段的值按文字给出（面板与菜单要的就是这个）。空值就是空串。"""
        value = self.values.get(field_id)
        return "" if value is None else str(value)

    @property
    def title(self) -> str:
        return self.text("title")

    @property
    def started_on(self) -> str | None:
        return self.text("started_on") or None

    @property
    def ended_on(self) -> str | None:
        return self.text("ended_on") or None

    @property
    def note(self) -> str | None:
        return self.text("note") or None

    @property
    def display_name(self) -> str:
        """界面上怎么称呼它。没填名称（只可能是从库里读到的旧数据）时退回类型名。"""
        if self.title:
            return self.title
        kind = kind_by_id(self.kind)
        return kind.empty_label if kind is not None else ""


@dataclass(frozen=True, slots=True)
class EventWriteOutcome:
    """一次事件写动的结果。

    ``event`` 是**写完之后**的样子；删除时是**删之前**的样子 —— 两种情形界面都要拿它的名字
    去拼那句话。``unbound`` 只有删除时非空，撤销靠它把绑定放回去。

    ``changed`` 为假表示这次保存与库里那一份逐字段一样 —— 界面据此说「没有改动」，
    而不是「已更新 …」（与元数据编辑器同一套说法）。
    """

    event: Event
    journaled: bool
    changed: bool = True
    unbound: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class EventUndoResult:
    """一次事件撤销的结果。"""

    action: str
    #: 撤销之后这个事件的样子；撤销删除且没撤成时为 ``None``。
    event: Event | None
    journaled: bool
    #: 撤不成时给用户看的一句中文，``None`` 表示成了。
    message: str | None = None


@dataclass(frozen=True, slots=True)
class EventRef:
    """照片属于哪些事件时只需要带这么几样：分组要组键与显示名，菜单要名字。"""

    id: int
    kind: str
    title: str
    started_on: str | None

    @property
    def group_key(self) -> str:
        """分组的组键：``起始日期#事件 id``。

        日期在前，于是 :func:`~gallery.core.metadata.facets._date_group_order` 那种「把键里的数字
        全取出来拼成一个数」的比较法直接能用（日期占大头、id 兜底同日期的先后）；id 在后是
        因为组键必须**唯一** —— 两个同名同日的旅行不能挤成一组。
        """
        return f"{self.started_on or ''}#{self.id}"

    @property
    def label(self) -> str:
        return self.title


@dataclass(frozen=True, slots=True)
class EventDraft:
    """面板交出来的那一份：**每个字段一格文字**，键是 ``EventField.id``。

    公共列（名称、日期、备注）与类型独有的字段一视同仁 —— 字段表是数据、可以增删，具名属性
    做不到；而拆成两半会让「公共列有哪几个、什么顺序」又出现一处手写的地方。
    """

    values: dict[str, str] = field(default_factory=dict)

    def text(self, field_id: str) -> str:
        return self.values.get(field_id, "")

    @classmethod
    def of(cls, **fields: str) -> EventDraft:
        """``EventDraft.of(title="京都五日", destination="京都")`` —— 手写一份草稿时省一层字典。"""
        return cls(values=dict(fields))


def _kind_schema(kind: EventKind) -> str:
    """一类事件的扩展表。**列名与类型都从字段定义拼出来**，不许手写。"""
    columns = ",\n    ".join(f"{field.column} {declaration_of(field)}" for field in kind.fields)
    return (
        f"CREATE TABLE IF NOT EXISTS ev.{kind.table} (\n"
        f"    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,\n"
        f"    {columns}\n);"
    )


def _now() -> int:
    return int(time.time())


def _to_sql(field: EventField, value: str) -> SqlValue:
    """面板给的一格文字 → 存进库的值。空的一律存 ``NULL``，不存空串。"""
    if field.multiple:
        return join_multi(split_multi(value)) or None
    text = value.strip()
    if not text:
        return None
    if field.sql_type == "DAY":
        return normalize_day(text) or None
    if field.sql_type == "REAL":
        return parse_cost(text)
    if field.sql_type == "INTEGER":
        return int(text) if text.lstrip("+-").isdigit() else None
    return text


def _from_sql(field: EventField, value: object) -> SqlValue:
    """库里的一格 → 事件上那个字段的值。类型由 :attr:`EventField.sql_type` 说了算。"""
    if value is None:
        return None
    return str(value) if field.sql_type in ("TEXT", "DAY") else value  # type: ignore[return-value]


def _event_from_common(
    kind: EventKind, common: Mapping[str, SqlValue], extra: Sequence[SqlValue]
) -> Event:
    """一行 ``events`` + 一行扩展表拼成一个事件。公共列也进 ``values``，于是面板只走一条路。"""
    values: dict[str, SqlValue] = {
        field.id: _from_sql(field, common[field.column]) for field in COMMON_FIELDS
    }
    values.update(
        {field.id: _from_sql(field, extra[index]) for index, field in enumerate(kind.fields)}
    )
    # 这三列在库里是 ``INTEGER NOT NULL``，SQLite 只会给回整数；断言是说给类型检查器听的。
    identifier, created_at, modified_at = common["id"], common["created_at"], common["modified_at"]
    assert isinstance(identifier, int) and isinstance(created_at, int)
    assert isinstance(modified_at, int)
    return Event(
        id=identifier,
        kind=str(common["kind"]),
        created_at=created_at,
        modified_at=modified_at,
        values=values,
    )


def _field_values(fields: Sequence[EventField], draft: EventDraft) -> tuple[SqlValue, ...]:
    """一列字段的值，顺序与 ``fields`` 一致 —— 建表、插入、更新三处都按它拼。"""
    return tuple(_to_sql(field, draft.text(field.id)) for field in fields)


def _before_values(
    fields: Sequence[EventField], before: Mapping[str, str | None]
) -> tuple[SqlValue, ...]:
    """``field id → 原值的文字`` 转成能写进库的值。撤销与恢复都走这条 —— 原值在 journal 里
    是文字（``FieldChange`` 只存字符串），回来时过一遍与新建同一条 :func:`_to_sql`。"""
    return tuple(_to_sql(field, before.get(field.id) or "") for field in fields)


def _text_of(value: SqlValue) -> str | None:
    """一个值在 journal 里的样子。``FieldChange`` 只收字符串，数字先转成文字，
    回来时 :func:`_to_sql` 再读一遍 —— 于是「3,280.50 存成 3280.5」这条归一只有一处。"""
    return None if value is None else str(value)


def _field_changes(
    kind: EventKind, before: Event | None, after: Event | None
) -> tuple[FieldChange, ...]:
    """这次写动的**完整前像与后像**，公共列与类型独有字段都在里面。

    **不挑「变了的那几个」**：撤销是拿这张前像整行写回去的，而没被记进来的字段会被当成
    ``NULL`` 写下去 —— ``title`` 那一栏是 ``NOT NULL``，一次「只改了目的地」的撤销会当场撞上
    约束（这一类 bug 只在撤销时发作，正常路径看不出来）。
    """
    return tuple(
        FieldChange(
            tag=declared.id,
            before=_text_of(before.values.get(declared.id) if before is not None else None),
            after=_text_of(after.values.get(declared.id) if after is not None else None),
        )
        for declared in (*COMMON_FIELDS, *kind.fields)
    )


def _parse_payload(payload: str | None) -> dict[str, Any] | None:
    """事件记录里那段自己解释的 JSON。坏行返回 ``None``（日志不该把程序带崩）。"""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    event_id, kind_id = data.get("event_id"), data.get("kind")
    raw_bindings = data.get("bindings")
    if not isinstance(event_id, int) or not isinstance(kind_id, str):
        return None
    bindings = (
        [str(item) for item in raw_bindings if isinstance(item, str)]
        if isinstance(raw_bindings, list)
        else []
    )
    return {"event_id": event_id, "kind": kind_id, "bindings": bindings}


def attach(
    connection: sqlite3.Connection, lock: threading.Lock, *, root: Path
) -> EventStore | None:
    """把事件库挂到索引那条连接上并建好表。挂不上返回 ``None``，**索引其余功能不受影响**。

    这是 :class:`~gallery.core.library.index.FacetIndex` 的 ``attach`` 挂钩：它不认识事件，
    只是建完连接之后把返回值原样交出去（ADR 0043 决策 1）。位置按约定推（``paths.events_file``），
    与 :meth:`FacetIndex.open` 同一种写法。

    **一条连接只挂一次**（``ev`` 这个名字已经被占着时 ATTACH 会报错，于是返回 ``None``）。
    ``FacetIndex`` 只在建连接时调它一次，所以正常路径走不到那种情形。
    """
    path = paths.events_file(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(f"ATTACH DATABASE ? AS {SCHEMA_NAME}", (str(path),))
        _relax_durability(connection)
        version = connection.execute(f"PRAGMA {SCHEMA_NAME}.user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            connection.executescript(_drop_script())
            connection.execute(f"PRAGMA {SCHEMA_NAME}.user_version = {SCHEMA_VERSION}")
        connection.executescript(_EVENTS_SCHEMA)
        connection.executescript(_BINDINGS_SCHEMA)
        for kind in EVENT_KINDS:
            connection.executescript(_kind_schema(kind))
        connection.commit()
    except (OSError, sqlite3.Error) as exc:
        logger.info("事件库不可用（%s），事件功能这次不参与", exc)
        return None
    return EventStore(root, connection, lock)


def _drop_script() -> str:
    """版本对不上时重建的是**事件库自己的表**。

    **只列事件库里的表名** —— 索引的重建脚本（``index.py``）同样只列它自己的两张。哪一边
    漏写一个表名，用户填的东西就没了，而两边的守护测试各盯各的那一句。
    """
    tables = ["events", "photo_events", *(kind.table for kind in EVENT_KINDS)]
    return "".join(f"DROP TABLE IF EXISTS {SCHEMA_NAME}.{table};" for table in tables)


def _relax_durability(connection: sqlite3.Connection) -> None:
    """事件库的提交强度：**日志模式跟索引一样，持久性不跟**。

    ``journal_mode=DELETE`` 的理由与索引相同（ADR 0038 决策 5：``-wal`` / ``-shm`` 跟着用户的库
    在云盘上互相覆盖）。而 ``synchronous`` 保持默认 —— 索引是丢了能重建的缓存，这里是用户自己
    填的数据，为它付满额断电持久性不叫浪费。**失败只记日志**：落到 WAL 也只是多两个文件。
    """
    try:
        connection.execute(f"PRAGMA {SCHEMA_NAME}.journal_mode=DELETE")
    except sqlite3.Error as exc:
        logger.info("事件库沿用默认日志模式：%s", exc)


class EventStore:
    """事件库上的读写。没有连接就是**不可用**，不是一个空库。

    不可用的来路只有一个：配置目录建不出来（只读卷 / SMB），于是 events.db 同样建不出来
    （ADR 0043 决策 1）。界面据此让「记录为」那一行不出现，而不是弹一个错。
    """

    def __init__(
        self,
        root: Path,
        connection: sqlite3.Connection | None = None,
        lock: threading.Lock | None = None,
    ) -> None:
        self._root = Path(root)
        self._connection = connection
        self._lock = lock if lock is not None else threading.Lock()

    @property
    def available(self) -> bool:
        """事件库这次用不用得上。"""
        return self._connection is not None

    # ---- 读 ------------------------------------------------------------------

    def all_events(self) -> tuple[Event, ...]:
        """全部事件，按开始日期新→旧、id 兜底。**一次读全**：一个库的事件只有几十条，
        界面把它整个缓存住（菜单的第二三层、面板的初始值、分组标签都从那份缓存来）。"""
        connection = self._connection
        if connection is None:
            return ()
        with self._lock:
            rows = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM {SCHEMA_NAME}.events e"
                " ORDER BY e.started_on DESC, e.id DESC"
            ).fetchall()
            events: list[Event] = []
            for row in rows:
                common = dict(zip(_EVENT_NAMES, row, strict=True))
                kind = kind_by_id(str(common["kind"]))
                if kind is None:
                    # 库里存着这一类的定义没了（以后删掉一类事件就这么回事）。跳过而不是崩：
                    # 一条看不懂的记录不该让整份清单打不开。
                    logger.info("跳过一条认不出的事件（kind=%s）", common["kind"])
                    continue
                events.append(
                    _event_from_common(kind, common, self._extra_row(kind, int(common["id"])))
                )
            return tuple(events)

    def _extra_row(self, kind: EventKind, event_id: int) -> list[SqlValue]:
        """扩展表里那一行；没有就返回一排 ``None``（``LEFT JOIN`` 的等价物，少一次拼 SQL）。

        **调用方要已经拿着锁**（它只从 :meth:`all_events` 里被调一次）。
        """
        connection = self._connection
        assert connection is not None
        columns = ", ".join(field.column for field in kind.fields)
        row = connection.execute(
            f"SELECT {columns} FROM {SCHEMA_NAME}.{kind.table} WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return (
            [cast("SqlValue", value) for value in row]
            if row is not None
            else ([None] * len(kind.fields))
        )

    def event(self, event_id: int) -> Event | None:
        """按 id 取一个事件。"""
        return next((event for event in self.all_events() if event.id == event_id), None)

    def events_for_photos(self, paths: Sequence[Path]) -> dict[Path, tuple[EventRef, ...]]:
        """这些照片分别属于哪些事件（分组与筛选要的那一份）。

        **按路径分批查**（``IN`` 一批放不下整个库），每张照片一个列表 —— 一张照片可以同时属于
        一次演出与一次旅行。
        """
        connection = self._connection
        if connection is None or not paths:
            return {}
        statement = (
            f"SELECT pe.path, e.id, e.kind, e.title, e.started_on"
            f"  FROM {SCHEMA_NAME}.photo_events pe"
            f"  JOIN {SCHEMA_NAME}.events e ON e.id = pe.event_id"
            f" WHERE pe.path IN ({{placeholders}})"
            f" ORDER BY e.started_on DESC, e.id DESC"
        )
        found: dict[Path, list[EventRef]] = {}
        with self._lock:
            keys = [str(path) for path in paths]
            for start in range(0, len(keys), CHUNK):
                chunk = keys[start : start + CHUNK]
                rows = connection.execute(
                    statement.format(placeholders=", ".join(["?"] * len(chunk))), chunk
                ).fetchall()
                for row in rows:
                    found.setdefault(Path(row[0]), []).append(
                        EventRef(
                            id=int(row[1]),
                            kind=str(row[2]),
                            title=str(row[3]),
                            started_on=row[4],
                        )
                    )
        return {path: tuple(refs) for path, refs in found.items()}

    def photos_of_event(self, event_id: int) -> tuple[Path, ...]:
        """这个事件下面有哪些照片。"""
        connection = self._connection
        if connection is None:
            return ()
        with self._lock:
            rows = connection.execute(
                f"SELECT path FROM {SCHEMA_NAME}.photo_events WHERE event_id = ?", (event_id,)
            ).fetchall()
        return tuple(Path(row[0]) for row in rows)

    # ---- 写 ------------------------------------------------------------------

    def create(self, kind_id: str, draft: EventDraft) -> EventWriteOutcome:
        """新建一个事件。**类型认不出来直接抛** —— 那是调用方的错，不该存下一条看不懂的记录。

        **不上撤销栈**（与新建相册同款）：撤销它就是删，而刚建的那一刻用户还没往里放东西。
        journal 照留一条，可追溯。
        """
        kind = kind_by_id(kind_id)
        if kind is None:
            raise ValueError(f"认不出的事件类型：{kind_id}")
        connection = self._require()
        stamp = _now()
        with self._lock, connection:
            written = (
                "kind",
                *(field.column for field in COMMON_FIELDS),
                "created_at",
                "modified_at",
            )
            cursor = connection.execute(
                f"INSERT INTO {SCHEMA_NAME}.events ({', '.join(written)})"
                f" VALUES ({', '.join(['?'] * len(written))})",
                (kind.id, *_field_values(COMMON_FIELDS, draft), stamp, stamp),
            )
            event_id = int(cursor.lastrowid or 0)
            self._write_extra(connection, kind, event_id, draft)
        created = self.event(event_id)
        assert created is not None
        return EventWriteOutcome(
            event=created,
            journaled=self._journal(ACTION_EVENT_CREATE, kind, created, None, ()),
        )

    def update(self, event_id: int, draft: EventDraft) -> EventWriteOutcome:
        """把一个事件改成面板交出来的这一份。公共列与扩展表在**同一次写**里改完，
        所以它只值一次 ⌘Z。"""
        # 先读一遍原值：撤销要的正是它。**在同一次写之前读**，所以拿到的一定是改之前的样子。
        previous = self.event(event_id)
        kind = kind_by_id(self._kind_of(event_id))
        if previous is None or kind is None:
            raise ValueError(f"认不出的事件类型：{self._kind_of(event_id)}")
        connection = self._require()
        assignments = ", ".join(f"{field.column} = ?" for field in COMMON_FIELDS)
        with self._lock, connection:
            connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {assignments}, modified_at = ? WHERE id = ?",
                (*_field_values(COMMON_FIELDS, draft), _now(), event_id),
            )
            self._write_extra(connection, kind, event_id, draft)
        updated = self.event(event_id)
        assert updated is not None
        # 逐字段一样就不记：没有改动就没什么可撤的，而「记一条改回同样的值」只会在栈上白占一格。
        changed = updated.values != previous.values
        return EventWriteOutcome(
            event=updated,
            journaled=self._journal(ACTION_EVENT, kind, previous, updated, ()) if changed else True,
            changed=changed,
        )

    def _write_extra(
        self, connection: sqlite3.Connection, kind: EventKind, event_id: int, draft: EventDraft
    ) -> None:
        """写扩展表那一行。``INSERT OR REPLACE``：改了类型独有的字段就是覆盖整行。"""
        columns = ", ".join(field.column for field in kind.fields)
        placeholders = ", ".join("?" * (len(kind.fields) + 1))
        connection.execute(
            f"INSERT OR REPLACE INTO {SCHEMA_NAME}.{kind.table} (event_id, {columns})"
            f" VALUES ({placeholders})",
            (event_id, *_field_values(kind.fields, draft)),
        )

    def delete(self, event_id: int) -> EventWriteOutcome:
        """删掉一个事件，连同它那些绑定。可 ⌘Z 完整恢复。

        绑定**显式删掉**，不靠 ``ON DELETE CASCADE``：SQLite 的 ``foreign_keys`` 默认是关的，
        级联一次都不会发生，而留下的孤儿绑定看不出来（查询都是从 ``events`` 那边 JOIN 过来的）。
        ``AUTOINCREMENT`` 让 id 不会复用，所以孤儿也永远挂不上别的事件 —— 它只是垃圾，不是隐患。
        外键声明留着是说清意图，删除的**正确性**不能指望它。

        记进 journal 的那一份**含绑定清单** —— 事件回来了、照片关联没了的话，用户要等到某天
        按事件筛才发现少了一堆（ADR 0043 决策 5）。
        """
        connection = self._require()
        previous = self.event(event_id)
        kind = kind_by_id(self._kind_of(event_id))
        if previous is None or kind is None:
            raise ValueError(f"认不出的事件类型：{self._kind_of(event_id)}")
        unbound = self.photos_of_event(event_id)
        with self._lock, connection:
            # 三张表各删各的。**扩展表那一行不能忘**：它是孤儿，而它会挡住撤销 —— 恢复时按
            # 同一个 id 插回去，撞上 ``event_id`` 的主键约束。
            connection.execute(
                f"DELETE FROM {SCHEMA_NAME}.{kind.table} WHERE event_id = ?", (event_id,)
            )
            connection.execute(
                f"DELETE FROM {SCHEMA_NAME}.photo_events WHERE event_id = ?", (event_id,)
            )
            connection.execute(f"DELETE FROM {SCHEMA_NAME}.events WHERE id = ?", (event_id,))
        return EventWriteOutcome(
            event=previous,
            journaled=self._journal(ACTION_EVENT_DELETE, kind, previous, None, unbound),
            unbound=unbound,
        )

    # ---- journal 与撤销 ------------------------------------------------------

    def _journal(
        self,
        action: str,
        kind: EventKind,
        before: Event | None,
        after: Event | None,
        bindings: Sequence[Path],
    ) -> bool:
        """留一条可回滚的记录。**记账失败不让操作失败** —— 用户填好的东西不能因为记不上账就
        被说成没改成（与其余写操作同一条规矩，见 ``metadata.journal``）。"""
        anchor = after or before
        assert anchor is not None
        payload = json.dumps(
            {
                "event_id": anchor.id,
                "kind": kind.id,
                "bindings": [str(path) for path in bindings],
            },
            ensure_ascii=False,
        )
        record = WriteRecord(
            action=action,
            source=paths.events_file(self._root),
            target=paths.events_file(self._root),
            result=RESULT_OK,
            changes=_field_changes(kind, before, after),
            payload=payload,
        )
        return append_record(self._root, record)

    def undo_record(self, record: WriteRecord) -> EventUndoResult:
        """把一条事件记录改回去。**改与删都能撤，新建那一条走不到这里**（它不入栈）。"""
        payload = _parse_payload(record.payload)
        if payload is None:
            return EventUndoResult(record.action, None, False, "这条事件记录读不出来，没法撤销。")
        event_id, kind_id = payload["event_id"], payload["kind"]
        kind = kind_by_id(kind_id)
        if kind is None:
            return EventUndoResult(record.action, None, False, f"认不出的事件类型：{kind_id}。")
        try:
            if record.action == ACTION_EVENT_DELETE:
                restored = self._restore_event(kind, event_id, record.changes, payload["bindings"])
            else:
                restored = self._rewrite_event(kind, event_id, record.changes)
        except sqlite3.IntegrityError:
            # id 被占了 —— 只可能是手工改过数据库。说清楚，别把别人的事件覆盖掉。
            logger.info("撤销事件时 id 已经被占了：%s", event_id)
            return EventUndoResult(
                record.action, None, False, f"库里已经有一条 id 为 {event_id} 的事件，不敢覆盖。"
            )
        journaled = self._journal(ACTION_UNDO_EVENT, kind, None, restored, ())
        return EventUndoResult(record.action, restored, journaled)

    def _rewrite_event(
        self, kind: EventKind, event_id: int, changes: tuple[FieldChange, ...]
    ) -> Event:
        """把字段写回 ``changes`` 里的原值（撤销一次改动）。"""
        connection = self._require()
        before = {change.tag: change.before for change in changes}
        assignments = ", ".join(f"{field.column} = ?" for field in COMMON_FIELDS)
        extra_columns = ", ".join(field.column for field in kind.fields)
        with self._lock, connection:
            connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {assignments}, modified_at = ? WHERE id = ?",
                (
                    *_before_values(COMMON_FIELDS, before),
                    _now(),
                    event_id,
                ),
            )
            connection.execute(
                f"INSERT OR REPLACE INTO {SCHEMA_NAME}.{kind.table} (event_id, {extra_columns})"
                f" VALUES ({', '.join(['?'] * (len(kind.fields) + 1))})",
                (event_id, *_before_values(kind.fields, before)),
            )
        restored = self.event(event_id)
        assert restored is not None
        return restored

    def _restore_event(
        self,
        kind: EventKind,
        event_id: int,
        changes: tuple[FieldChange, ...],
        bindings: Sequence[str],
    ) -> Event:
        """把一个删掉的事件放回去：**连 id 一起**（绑定按 id 记着），再把绑定补上。

        id 显式写回是安全的：``AUTOINCREMENT`` 保证它不会被后来新建的事件占走。
        """
        connection = self._require()
        before = {change.tag: change.before for change in changes}
        columns = (
            "id",
            "kind",
            *(field.column for field in COMMON_FIELDS),
            "created_at",
            "modified_at",
        )
        extra_columns = ", ".join(field.column for field in kind.fields)
        with self._lock, connection:
            connection.execute(
                f"INSERT INTO {SCHEMA_NAME}.events ({', '.join(columns)})"
                f" VALUES ({', '.join(['?'] * len(columns))})",
                (
                    event_id,
                    kind.id,
                    *_before_values(COMMON_FIELDS, before),
                    _now(),
                    _now(),
                ),
            )
            connection.execute(
                f"INSERT INTO {SCHEMA_NAME}.{kind.table} (event_id, {extra_columns})"
                f" VALUES ({', '.join(['?'] * (len(kind.fields) + 1))})",
                (event_id, *_before_values(kind.fields, before)),
            )
        if bindings:
            self.bind([Path(path) for path in bindings], event_id)
        restored = self.event(event_id)
        assert restored is not None
        return restored

    def bind(self, paths: Iterable[Path], event_id: int) -> int:
        """把照片记进这个事件，返回新记进去几张（已经记着的算没动）。"""
        connection = self._require()
        keys = [str(path) for path in paths]
        written = 0
        with self._lock, connection:
            for start in range(0, len(keys), CHUNK):
                chunk = keys[start : start + CHUNK]
                placeholders = ", ".join(["(?, ?)"] * len(chunk))
                parameters: list[SqlValue] = []
                for key in chunk:
                    parameters.extend((key, event_id))
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO {SCHEMA_NAME}.photo_events (path, event_id)"
                    f" VALUES {placeholders}",
                    parameters,
                )
                written += max(cursor.rowcount, 0)
        return written

    def unbind(self, paths: Iterable[Path], event_id: int) -> int:
        """把这些照片从这个事件里拿出来，返回拿掉几张。

        ``event_id`` 在整次调用里是同一个，所以按它 + 路径的 ``IN`` 删就够 —— **不为同一件事
        再拼一次 ``(path, event_id)`` 的行值**，那个写法在老版本 SQLite 上不认。
        """
        connection = self._require()
        keys = [str(path) for path in paths]
        removed = 0
        with self._lock, connection:
            for start in range(0, len(keys), CHUNK):
                chunk = keys[start : start + CHUNK]
                placeholders = ", ".join(["?"] * len(chunk))
                cursor = connection.execute(
                    f"DELETE FROM {SCHEMA_NAME}.photo_events"
                    f" WHERE event_id = ? AND path IN ({placeholders})",
                    [event_id, *chunk],
                )
                removed += max(cursor.rowcount, 0)
        return removed

    def move_binding(self, old: Path, new: Path) -> bool:
        """照片改名之后把绑定一起搬过去（ADR 0043 决策 8）。

        **纯写我们自己的库，不碰用户文件**，红线 1 管不到这里；而不搬的话绑定就指向一个不存在的
        路径了 —— 索引里的 facet 行靠 ``(大小, 修改时间)`` 自动过期、用户无感，事件绑定是用户
        一份心意，不能这么兜。
        """
        connection = self._connection
        if connection is None:
            return False
        with self._lock, connection:
            cursor = connection.execute(
                f"UPDATE OR REPLACE {SCHEMA_NAME}.photo_events SET path = ? WHERE path = ?",
                (str(new), str(old)),
            )
        return cursor.rowcount > 0

    def _kind_of(self, event_id: int) -> str:
        """这一条事件是哪一类。改它之前要按类型拼扩展表的列，所以得先问一次。"""
        connection = self._require()
        with self._lock:
            row = connection.execute(
                f"SELECT kind FROM {SCHEMA_NAME}.events WHERE id = ?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"没有 id 为 {event_id} 的事件")
        return str(row[0])

    def _require(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise EventsUnavailableError("事件库这次不可用：这个库的文件夹建不出配置目录。")
        return connection


def events_of_kind(events: Sequence[Event], kind_id: str) -> tuple[Event, ...]:
    """从整份缓存里挑出某一类。顺序照 ``all_events``（新→旧），菜单直接按它摆。"""
    return tuple(event for event in events if event.kind == kind_id)


__all__ = [
    "CHUNK",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "Event",
    "EventDraft",
    "EventRef",
    "EventStore",
    "attach",
    "events_of_kind",
]
