"""事件库（配置目录下的 ``events.db``）：事件定义与照片↔事件的绑定（ADR 0043）。

**它挂在索引那条 SQLite 连接上**（别名 ``ev``），于是 SQL 层仍然是一张库（``#旅行·目的地 京都``
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
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import cast

from gallery.core import paths
from gallery.core.errors import EventsUnavailableError
from gallery.core.events.conversions import (
    _before_values,
    _event_from_common,
    _field_changes,
    _field_values,
    _now,
    _parse_payload,
)
from gallery.core.events.kinds import COMMON_FIELDS, EVENT_KINDS, EventKind, kind_by_id
from gallery.core.events.models import (
    Event,
    EventDraft,
    EventRef,
    EventUndoResult,
    EventWriteOutcome,
    Orphan,
    SqlValue,
)
from gallery.core.events.schema import (
    _BINDINGS_SCHEMA,
    _EVENT_NAMES,
    _EVENT_SELECT,
    _EVENTS_SCHEMA,
    BINDINGS_TABLE,
    CHUNK,
    EVENTS_TABLE,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    _kind_schema,
    _relax_journal_mode_only,
    migrate,
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
from gallery.core.sql import ESCAPE, like_pattern

logger = logging.getLogger(__name__)


def attach(
    connection: sqlite3.Connection, lock: threading.Lock, *, root: Path
) -> EventStore | None:
    """把事件库挂到索引那条连接上并建好表。挂不上返回 ``None``，**索引其余功能不受影响**。

    这是 :class:`~gallery.core.library.index.FacetIndex` 的 ``attach`` 挂钩：它不认识事件，
    只是建完连接之后把返回值原样交出去（ADR 0043 决策 1）。位置按约定推（``paths.events_file``），
    与 :meth:`FacetIndex.open` 同一种写法。

    **一条连接只挂一次**（``ev`` 这个名字已经被占着时 ATTACH 会报错，于是返回 ``None``）。
    ``FacetIndex`` 只在建连接时调它一次，所以正常路径走不到那种情形。

    **版本对不上时是迁移，不是重建**（ADR 0048 决策 3）：建表语句在前、迁移在后，于是新库走完
    建表就已经是最新结构（迁移那一步看见列在就跳过），旧库则由迁移把缺的那几列补上。
    版本**比这份程序新**时整个不挂 —— 读不懂一份文件不等于可以删掉它。
    """
    path = paths.events_file(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(f"ATTACH DATABASE ? AS {SCHEMA_NAME}", (str(path),))
        _relax_journal_mode_only(connection)
        version = int(connection.execute(f"PRAGMA {SCHEMA_NAME}.user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            # 用户把应用降级了：那份库比我们认得的版本新。**宁可不挂，也不删** ——
            # 读不懂一份文件不等于可以删掉它，而事件是唯一一份重建不出来的数据（ADR 0048 决策 3）。
            logger.info("事件库的版本 %d 比这份程序新（%d），这次不挂它", version, SCHEMA_VERSION)
            return None
        # 建表在前、迁移在后：新库建完就已经是最新结构（迁移那一步看见列在就跳过），
        # 旧库的表已存在、`IF NOT EXISTS` 什么都不做，迁移把缺的那几列补上（幂等）。
        connection.executescript(_EVENTS_SCHEMA)
        connection.executescript(_BINDINGS_SCHEMA)
        for kind in EVENT_KINDS:
            connection.executescript(_kind_schema(kind))
        with connection:
            migrate(connection, version)
            connection.execute(f"PRAGMA {SCHEMA_NAME}.user_version = {SCHEMA_VERSION}")
    except (OSError, sqlite3.Error) as exc:
        logger.info("事件库不可用（%s），事件功能这次不参与", exc)
        return None
    return EventStore(root, connection, lock)


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
        # guard: allow-long-comment（漏接一处不报错：库写进去了，界面那份清单还是旧的）
        #: 事件清单变了吗。**由写入方（:class:`~gallery.core.library.facade.Library`）
        #: 挂上**，用来作废它那份会话缓存（ADR 0043 决策 7）。
        #:
        #: 放在这里而不是让调用方记得作废，是因为漏一处**不报错** —— 库写进去了，
        #: 而界面上那份清单还是旧的。这类失效关系写在散文里迟早会漏，挂在唯一的
        #: 写入方上就没有「漏掉的那一处」。
        self.on_change: Callable[[], None] | None = None

    def _changed(self) -> None:
        """通知写入方：**事件清单**变了。

        只在这四处调 —— 改事件的 :meth:`create` / :meth:`update` / :meth:`delete`
        与撤销的 :meth:`undo_record`。绑定那三张表（:meth:`bind` 与两个姐妹）不在
        这份缓存里，所以它们**不**调：多调一次不会错，只是白扔一次缓存。
        """
        if self.on_change is not None:
            self.on_change()

    @property
    def available(self) -> bool:
        """事件库这次用不用得上。"""
        return self._connection is not None

    @property
    def root(self) -> Path:
        """这个事件库属于哪个库。**孤儿检查要靠它**：库根读不了就整个不查（ADR 0043 决策 9）。"""
        return self._root

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
        return event_by_id(self.all_events(), event_id)

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

    def all_bindings(self) -> tuple[Orphan, ...]:
        """全部绑定，**带上事件名**（孤儿检查与它的提示框要的就是这两样）。

        **一条 JOIN 读全**，而不是「按事件逐个问」：绑定可能上千条，逐个事件一次查询就是上千次
        往返，而开库时每个库都要跑这一遍。

        返回 :class:`Orphan` 而不是新造一个类型 —— 它要的就是「路径 + 事件 id + 事件名」，
        而那正是这个类型的三列。
        """
        connection = self._connection
        if connection is None:
            return ()
        with self._lock:
            rows = connection.execute(
                f"SELECT pe.path, e.id, e.title"
                f"  FROM {SCHEMA_NAME}.photo_events pe"
                f"  JOIN {SCHEMA_NAME}.events e ON e.id = pe.event_id"
                f" ORDER BY pe.path"
            ).fetchall()
        return tuple(
            Orphan(path=Path(row[0]), event_id=int(row[1]), event_title=str(row[2])) for row in rows
        )

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
        self._changed()
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
        if changed:
            self._changed()
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
        self._changed()
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
        self._changed()
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

    def forget_bindings(self, paths: Sequence[Path]) -> int:
        """删掉这些路径上的**全部**绑定，返回删了几条（孤儿清理，ADR 0043 决策 9）。

        **按 path 删就够，不必按 ``(path, event_id)``**：照片没了，它身上每一条绑定都是孤儿，
        而多带一个条件只会漏掉「同一张照片属于好几种事件」的情形。

        ``path`` 是主键的前导列，所以 ``IN`` 走主键索引，不必扫全表。
        """
        connection = self._connection
        if connection is None:
            return 0
        keys = [str(path) for path in paths]
        removed = 0
        with self._lock, connection:
            for start in range(0, len(keys), CHUNK):
                chunk = keys[start : start + CHUNK]
                placeholders = ", ".join(["?"] * len(chunk))
                cursor = connection.execute(
                    f"DELETE FROM {SCHEMA_NAME}.{BINDINGS_TABLE} WHERE path IN ({placeholders})",
                    chunk,
                )
                removed += max(cursor.rowcount, 0)
        return removed

    def _apply_moves(self, moves: Iterable[tuple[Path, Path, bool]]) -> int:
        """真搬。**连接不可用就是 0 条** —— ``None`` 那个库由 :func:`follow_moves` 挡掉。"""
        connection = self._connection
        if connection is None:
            return 0
        pairs = [(Path(old), Path(new), is_dir) for old, new, is_dir in moves]
        if not pairs:
            return 0
        moved = 0
        with self._lock, connection:
            for old, new, is_dir in pairs:
                if is_dir:
                    old_prefix, new_prefix = str(old), str(new)
                    cursor = connection.execute(
                        f"UPDATE OR REPLACE {SCHEMA_NAME}.{BINDINGS_TABLE}"
                        f" SET path = ? || substr(path, ?)"
                        f" WHERE path LIKE ? ESCAPE '{ESCAPE}' OR path = ?",
                        (
                            new_prefix,
                            len(old_prefix) + 1,
                            like_pattern(old_prefix, "/%"),
                            old_prefix,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        f"UPDATE OR REPLACE {SCHEMA_NAME}.{BINDINGS_TABLE}"
                        f" SET path = ? WHERE path = ?",
                        (str(new), str(old)),
                    )
                moved += max(cursor.rowcount, 0)
        return moved

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


def event_by_id(events: Sequence[Event], event_id: int) -> Event | None:
    """从一份事件清单里按 id 挑一个。

    **清单由调用方给**，因为两处各有一个正当的来源：:class:`EventStore` 是现查的，
    ``Library`` 用的是它那份会话缓存（ADR 0043 决策 7）。所以这里只收清单、不收
    store —— 收 store 就等于替调用方选了一边。
    """
    return next((event for event in events if event.id == event_id), None)


def follow_moves(events: EventStore | None, moves: Iterable[tuple[Path, Path, bool]]) -> int:
    """一批「旧位置 → 新位置」，把事件绑定搬过去，返回搬了几条（ADR 0043 决策 8）。

    **改了路径就要调它**：绑定按 ``path`` 记，而索引里的 facet 行靠 ``(大小, 修改时间)`` 自动
    过期、`forget_paths`` 清孤儿行，所以丢索引对用户无感 —— **事件绑定是用户一份心意，不能这么
    兜**。纯写我们自己的库，不碰用户文件，红线 1 管不到这里。

    **三个调用点共用这一处**（判 ``None`` 与「不可用」也在这里，于是写错一处不会静默丢绑定）：
    应用内改名、应用内移动到相册（撤销那两样同样走它），以及监听器报上来的外部改名与移动。

    :param moves: ``(旧位置, 新位置, 是不是文件夹)``。**只收真的动了的那些** —— 跳过与失败
        的那些路径压根没变。相册那一支按前缀改写后缀（``substr`` 按字符数切），一次相册改名
        牵动几千张，一条就够，不逐张照片去问。
    :param events: 事件库。``None`` 或不可用（配置目录建不出来的库）时**当没有这回事** ——
        文件已经动了，绑定是附带信息，报错只会让用户以为照片没挪成功。

    **一次事务**：一次「移动到相册」或一批外部变化可能是几百项，逐项一个事务就是几百次提交。
    而**重复调用是安全的** —— 搬过的那一次源路径上已经没有绑定了，于是这一轮一条都不改。
    应用内移动因此既自己搬一次、又被监听器报回来的那次搬一次（``MOVE`` 不做回声屏蔽，新文件的
    上屏就是靠监听器），第二次是空操作。
    """

    if events is None or not events.available:
        return 0
    return events._apply_moves(moves)


__all__ = [
    "BINDINGS_TABLE",
    "CHUNK",
    "EVENTS_TABLE",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "Event",
    "EventDraft",
    "EventRef",
    "EventStore",
    "EventUndoResult",
    "EventWriteOutcome",
    "Orphan",
    "attach",
    "event_by_id",
    "events_of_kind",
    "follow_moves",
]
