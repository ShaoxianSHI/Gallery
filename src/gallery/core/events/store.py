"""事件库（配置目录下的 ``events.db``）：事件定义与照片↔事件的绑定（ADR 0043）。

**它挂在索引那条 SQLite 连接上**（别名 ``ev``），于是 SQL 层仍是一张库（``#旅行·目的地 京都``
能走子查询），而生命周期上仍是两个文件、两份 ``user_version``：事件是用户手写的数据，而索引的
表结构版本一对不上就**整表重建**。三件必须分别处理的事：``user_version`` 与 ``journal_mode``
都要写成 ``ev.`` 前缀；``synchronous`` 不跟着索引降级；挂不上时只记一条日志，其余功能照常。
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
from gallery.core.errors import CoverNotBoundError, EventNotFoundError, EventsUnavailableError
from gallery.core.events.conversions import (
    _before_values,
    _event_from_common,
    _field_changes,
    _field_values,
    _now,
    _parse_payload,
)
from gallery.core.events.kinds import COMMON_FIELDS, COVER, EVENT_KINDS, EventKind, kind_by_id
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
from gallery.core.sql import ESCAPE, file_identity, like_pattern

logger = logging.getLogger(__name__)


def attach(
    connection: sqlite3.Connection, lock: threading.Lock, *, root: Path
) -> EventStore | None:
    """把事件库挂到索引那条连接上并建好表。挂不上返回 ``None``，**索引其余功能不受影响**。

    这是 :class:`~gallery.core.library.index.FacetIndex` 的 ``attach`` 挂钩（ADR 0043 决策 1）。
    **一条连接只挂一次**（``ev`` 已被占着时 ATTACH 报错，于是返回 ``None``）。**版本对不上时是迁移，
    不是重建**（ADR 0048 决策 3）；版本**比这份程序新**时不挂 —— 读不懂一份文件不等于可以删掉它。
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
        #: 打开那一刻事件库那份文件的身份。**云盘换掉的是它**（索引那边一点察觉都没有），
        #: 所以这里自己看一眼（见 :meth:`_live_connection`）。
        self._identity: tuple[int, int] | None = file_identity(paths.events_file(self._root))
        # guard: allow-long-comment（漏掉一处不报错：库写进去了，界面那份清单还是旧的）
        #: 事件清单变了吗。**由写入方（:class:`~gallery.core.library.facade.Library`）
        #: 挂上**，用来作废它那份会话缓存（ADR 0043 决策 7）。
        #:
        #: 放在这里而不是让调用方记得作废，是因为漏一处**不报错** —— 库写进去了，
        #: 而界面上那份清单还是旧的。这类失效关系写在散文里迟早会漏，挂在唯一的
        #: 写入方上就没有「漏掉的那一处」。
        self.on_change: Callable[[], None] | None = None
        #: **无条件**重开索引连接并交回新连接，**同一个写入方接线**。事件库挂在索引那条连接上，
        #: 所以重开的是索引；漏掉的症状是每次写都失败而界面看不出来。
        self.reopen: Callable[[], sqlite3.Connection | None] | None = None

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
        """全部事件，按开始日期新→旧、同日期的用 id 定先后。**一次读全**：一个库的事件只有几十条，
        界面把它整个缓存住（菜单的第二三层、面板的初始值、分组标签都从那份缓存来）。"""
        connection = self._live_connection()
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

        **按路径分批查**（``IN`` 一批放不下整个库），每张一个列表 —— 一张照片可以同时属于演出与
        一次旅行。**顺带把封面那一列取出来**（ADR 0048 决策 7）：分组要靠它知道卡片画哪一张，
        而这是同一次 JOIN。
        """
        connection = self._live_connection()
        if connection is None or not paths:
            return {}
        statement = (
            f"SELECT pe.path, e.id, e.kind, e.title, e.started_on, e.{COVER.column}"
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
                            cover_path=row[5],
                        )
                    )
        return {path: tuple(refs) for path, refs in found.items()}

    def photos_of_event(self, event_id: int) -> tuple[Path, ...]:
        """这个事件下面有哪些照片。"""
        connection = self._live_connection()
        if connection is None:
            return ()
        with self._lock:
            rows = connection.execute(
                f"SELECT path FROM {SCHEMA_NAME}.photo_events WHERE event_id = ?", (event_id,)
            ).fetchall()
        return tuple(Path(row[0]) for row in rows)

    def all_bindings(self) -> tuple[Orphan, ...]:
        """全部绑定，**带上事件名**（孤儿检查与它的提示框要的就是这两样）。

        **一条 JOIN 读全**，而不是「按事件逐个问」：绑定可能上千条，逐个事件一次查询就是上千次往返。
        返回 :class:`Orphan` 而不是新造一个类型 —— 它要的就是「路径 + 事件 id + 事件名」这三列。
        """
        connection = self._live_connection()
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
        if previous is None:
            raise EventNotFoundError("这个事件已经不在了。")
        kind_id = self._kind_of(event_id)
        kind = kind_by_id(kind_id)
        if kind is None:
            raise ValueError(f"认不出的事件类型：{kind_id}")
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

        绑定**显式删掉**，不靠 ``ON DELETE CASCADE``：SQLite 的 ``foreign_keys`` 默认关，不会级联，
        留下的孤儿绑定看不出来。``AUTOINCREMENT`` 让 id 不复用，孤儿也永远挂不上别的事件。
        记进 journal 的那一份**含绑定清单**（ADR 0043 决策 5）。
        """
        connection = self._require()
        previous = self.event(event_id)
        if previous is None:
            raise EventNotFoundError("这个事件已经不在了。")
        kind_id = self._kind_of(event_id)
        kind = kind_by_id(kind_id)
        if kind is None:
            raise ValueError(f"认不出的事件类型：{kind_id}")
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

    def set_cover(self, event_id: int, photo: Path) -> EventWriteOutcome:
        """把这个事件的封面设成某一张照片。**它必须已经绑在这个事件上**（ADR 0048 决策 1）。

        :raises CoverNotBoundError: 那张照片不属于这个事件 —— 卡片只能画组内那一张
            （缩略图流水线拿的是 ``PhotoRef``），所以「设一个组外的封面」在界面上根本画不出来。
            这个异常是界面那一层之外的回退。
        """
        if not self._is_bound(event_id, photo):
            raise CoverNotBoundError("只能从属于这次事件的照片里挑封面。", path=photo)
        kind_id = self._kind_of(event_id)
        kind = kind_by_id(kind_id)
        if kind is None:
            raise ValueError(f"认不出的事件类型：{kind_id}")
        return self._write_cover(kind, event_id, photo)

    def clear_cover(self, event_id: int) -> bool:
        """取消封面：回到「组内第一张」。**没设过就是 ``False``**（界面据此不摆那一行）。"""
        kind_id = self._kind_of(event_id)
        kind = kind_by_id(kind_id)
        if kind is None:
            raise ValueError(f"认不出的事件类型：{kind_id}")
        outcome = self._write_cover(kind, event_id, None)
        return outcome.changed

    def _is_bound(self, event_id: int, photo: Path) -> bool:
        """这张照片绑在这个事件上吗（不变式的那一半，ADR 0048 决策 1）。"""
        connection = self._require()
        with self._lock:
            row = connection.execute(
                f"SELECT 1 FROM {SCHEMA_NAME}.{BINDINGS_TABLE} WHERE event_id = ? AND path = ?",
                (event_id, str(photo)),
            ).fetchone()
        return row is not None

    def _write_cover(self, kind: EventKind, event_id: int, photo: Path | None) -> EventWriteOutcome:
        """只写封面那一列 —— **不拿整份草稿去 UPDATE**。

        那样做会把草稿里缺的字段写成 NULL：面板交上来的草稿只填了看见的那几格，封面若不在其中就是
        「打开面板按保存，封面没了」（ADR 0048 决策 2）。``modified_at`` **不动**：给事件记一次
        「最后修改」会让菜单按日期排序时莫名其妙跳位置（ADR 0043 决策 2）。
        """
        connection = self._require()
        previous = self.event(event_id)
        if previous is None:
            raise KeyError(f"没有 id 为 {event_id} 的事件")
        with self._lock, connection:
            connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {COVER.column} = ? WHERE id = ?",
                (str(photo) if photo is not None else None, event_id),
            )
        updated = self.event(event_id)
        assert updated is not None
        # 与 update 同一套：逐字段一样就不记，没有改动就没什么可撤的。
        changed = updated.cover_path != previous.cover_path
        if changed:
            self._changed()
        return EventWriteOutcome(
            event=updated,
            journaled=self._journal(ACTION_EVENT, kind, previous, updated, ()) if changed else True,
            changed=changed,
        )

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

        ``event_id`` 全程相同，所以按它加路径的 ``IN`` 删即可。被移出来若有封面，同一批里清空
        —— 封面恒为绑定里的一张（ADR 0048 决策 1），少了这一步卡片会指向一张不属于这次事件的照片。
        """
        connection = self._require()
        keys = [str(path) for path in paths]
        removed = 0
        cleared = 0
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
                cleared += self._clear_covers_in(connection, chunk, event_id=event_id)
        if cleared:
            self._changed()
        return removed

    def _clear_covers_in(
        self,
        connection: sqlite3.Connection,
        keys: Sequence[str],
        *,
        event_id: int | None = None,
    ) -> int:
        """这些路径不再被绑着，于是指向它们的封面要清空（ADR 0048 决策 1）。返回清掉几条。

        **与删绑定在同一个事务里**：分两次写的话，中间那一刻库里就有一个「封面指向一张不属于这次事件
        的照片」的瞬间。:param event_id: 只清这一个事件的（``unbind``）；``None`` 表示清所有事件的。
        """
        placeholders = ", ".join(["?"] * len(keys))
        if event_id is None:
            cursor = connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {COVER.column} = NULL"
                f" WHERE {COVER.column} IN ({placeholders})",
                list(keys),
            )
        else:
            cursor = connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {COVER.column} = NULL"
                f" WHERE id = ? AND {COVER.column} IN ({placeholders})",
                [event_id, *keys],
            )
        return max(cursor.rowcount, 0)

    def forget_bindings(self, paths: Sequence[Path]) -> int:
        """删掉这些路径上的**全部**绑定，返回删了几条（孤儿清理，ADR 0043 决策 9）。

        **按 path 删就够，不必按 ``(path, event_id)``**：照片没了，它每一条绑定都是孤儿，多带一个
        条件只会漏掉「同一张照片属于好几种事件」的情形。``path`` 是主键的前导列，``IN`` 走主键索引。
        **指向这些路径的封面也一并清空**（ADR 0048 决策 1）；返回值仍只是「清掉了几条绑定」。
        """
        connection = self._live_connection()
        if connection is None:
            return 0
        keys = [str(path) for path in paths]
        removed = 0
        cleared = 0
        with self._lock, connection:
            for start in range(0, len(keys), CHUNK):
                chunk = keys[start : start + CHUNK]
                placeholders = ", ".join(["?"] * len(chunk))
                cursor = connection.execute(
                    f"DELETE FROM {SCHEMA_NAME}.{BINDINGS_TABLE} WHERE path IN ({placeholders})",
                    chunk,
                )
                removed += max(cursor.rowcount, 0)
                cleared += self._clear_covers_in(connection, chunk)
        if cleared:
            self._changed()
        return removed

    def _apply_moves(self, moves: Iterable[tuple[Path, Path, bool]]) -> int:
        """真搬。**连接不可用就是 0 条** —— ``None`` 那个库由 :func:`follow_moves` 挡掉。

        **封面跟着搬，同一个事务**（ADR 0048 决策 4）。它与绑定在同一个库里、同一套路径空间，
        所以这一句和撤销那一遍（方向相反）就是全部 —— 没有第二个真相源要同步。
        封面那一列**没有索引**，而它是至多几十行里的一列，全表扫与走索引同一个量级。
        """
        connection = self._live_connection()
        if connection is None:
            return 0
        pairs = [(Path(old), Path(new), is_dir) for old, new, is_dir in moves]
        if not pairs:
            return 0
        moved = 0
        covers = 0
        with self._lock, connection:
            for old, new, is_dir in pairs:
                if is_dir:
                    old_prefix, new_prefix = str(old), str(new)
                    parameters: tuple[object, ...] = (
                        new_prefix,
                        len(old_prefix) + 1,
                        like_pattern(old_prefix, "/%"),
                        old_prefix,
                    )
                    cursor = connection.execute(
                        f"UPDATE OR REPLACE {SCHEMA_NAME}.{BINDINGS_TABLE}"
                        f" SET path = ? || substr(path, ?)"
                        f" WHERE path LIKE ? ESCAPE '{ESCAPE}' OR path = ?",
                        parameters,
                    )
                    covers += self._move_covers(connection, is_dir=True, parameters=parameters)
                else:
                    cursor = connection.execute(
                        f"UPDATE OR REPLACE {SCHEMA_NAME}.{BINDINGS_TABLE}"
                        f" SET path = ? WHERE path = ?",
                        (str(new), str(old)),
                    )
                    covers += self._move_covers(
                        connection, is_dir=False, parameters=(str(new), str(old))
                    )
                moved += max(cursor.rowcount, 0)
        # 搬了封面就要作废那份清单缓存（理由见 :meth:`_changed`）。
        if covers:
            self._changed()
        return moved

    def _move_covers(
        self, connection: sqlite3.Connection, *, is_dir: bool, parameters: tuple[object, ...]
    ) -> int:
        """把封面从旧位置改写到新位置，返回改掉几条。

        **参数与绑定那句逐字相同**，两种形状各写一遍 SQL（相册那一支按前缀改写后缀）。单张照片可能
        是**几个**事件的封面，不按事件逐个改 —— 一句 SQL 用 ``WHERE cover_path = ?`` 改全部。
        """
        if is_dir:
            cursor = connection.execute(
                f"UPDATE {SCHEMA_NAME}.events"
                f" SET {COVER.column} = ? || substr({COVER.column}, ?)"
                f" WHERE {COVER.column} LIKE ? ESCAPE '{ESCAPE}' OR {COVER.column} = ?",
                parameters,
            )
        else:
            cursor = connection.execute(
                f"UPDATE {SCHEMA_NAME}.events SET {COVER.column} = ? WHERE {COVER.column} = ?",
                parameters,
            )
        return max(cursor.rowcount, 0)

    def _kind_of(self, event_id: int) -> str | None:
        """这一条事件是哪一类。改它之前要按类型拼扩展表的列，所以得先问一次。

        **没有这一条就是 ``None``** —— 抛 ``KeyError`` 出去没人接得住（跑在主线程的槽里），
        而这一条最可能来自「界面拿着一份过期的清单」那种正常情形（见 ``EventNotFoundError``）。
        """
        connection = self._require()
        with self._lock:
            row = connection.execute(
                f"SELECT kind FROM {SCHEMA_NAME}.events WHERE id = ?", (event_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def _require(self) -> sqlite3.Connection:
        connection = self._live_connection()
        if connection is None:
            raise EventsUnavailableError("事件库这次不可用：这个库的文件夹建不出配置目录。")
        return connection

    def _live_connection(self) -> sqlite3.Connection | None:
        """这一趟该用的连接。**读写都先走这里** —— 事件库那份文件被别的程序（云盘）换掉时，
        写会失败、而读会一直给旧内容（症状是「删掉的东西还在」），所以先看一眼文件还是不是我们
        打开的那一份，不是就先重开一次（ADR 0064）。
        """
        path = paths.events_file(self._root)
        current = file_identity(path)
        if (
            current is not None
            and self._identity is not None
            and current != self._identity
            and self.reopen is not None
        ):
            self._connection = self.reopen()
            self._identity = file_identity(path)
        return self._connection


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
    **改了路径就要调它**：绑定按 ``path`` 记，而索引靠 ``(大小, 修改时间)`` 过期，丢索引对用户无感
    —— **事件绑定是用户一份心意**。纯写我们自己的库，不碰用户文件。**三个调用点共用这一处**：应用内
    改名、应用内移动到相册，以及监听器报上来的外部改名与移动。:param moves: ``(旧位置, 新位置,
    是不是文件夹)``；**只收真的动了的那些**。:param events: ``None`` 或不可用时**当没有这回事**。
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
