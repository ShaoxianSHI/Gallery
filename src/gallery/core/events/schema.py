"""事件库的建表与版本：表名、列清单、DDL 与连接上的那一条 pragma。

它**只描述库长什么样**，不含读写（那在 :mod:`gallery.core.events.store`）。单独成模块是
因为这里的几条约束各自都能静默出事：列清单必须从字段定义生成（手写一份就有两处会漂），
而 ``user_version`` 与 ``journal_mode`` 都是**每个库各一份**、必须带 ``ev.`` 前缀。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Final

from gallery.core.events.kinds import COMMON_FIELDS, EVENT_KINDS, EventField, EventKind

logger = logging.getLogger(__name__)

#: 挂到索引连接上时的别名。搜索层的子查询按它写（``ev.photo_events``）。
SCHEMA_NAME: Final = "ev"


#: 公共表与绑定表的表名。**搜索层按它们写子查询**，所以名字是常量而不是各处各写一遍 ——
#: 写错要到 SQLite 执行时才报 ``no such table``，而那一眼看上去是「这一栏没得筛」。
EVENTS_TABLE: Final = "events"


BINDINGS_TABLE: Final = "photo_events"


#: 事件库的表结构版本。**只管 events.db 自己那份** ``PRAGMA ev.user_version``，
#: 与索引的 ``SCHEMA_VERSION`` 无关（索引升版会重建它的表，事件库不会）。
SCHEMA_VERSION: Final = 1


#: 一条语句里最多塞多少个绑定变量。``IN (…)`` 与多列插入都按它分批：
#: SQLite 的上限是 999（老版本），一行的绑定数还不止一个，所以按**行**取这个数的一半。
CHUNK: Final = 400


#: 绑定表。它与类型无关，所以写死在这儿（扩展表按类型生成，见 :func:`_kind_schema`）。
_BINDINGS_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS ev.{BINDINGS_TABLE} (
    path     TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES {EVENTS_TABLE}(id) ON DELETE CASCADE,
    PRIMARY KEY (path, event_id)
);
CREATE INDEX IF NOT EXISTS ev.{BINDINGS_TABLE}_event ON {BINDINGS_TABLE}(event_id);
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
    f"CREATE TABLE IF NOT EXISTS ev.{EVENTS_TABLE} (\n    "
    + ",\n    ".join(f"{name} {declaration}" for name, declaration in _event_columns())
    + "\n);"
    f"CREATE INDEX IF NOT EXISTS ev.{EVENTS_TABLE}_kind_started"
    f" ON {EVENTS_TABLE}(kind, started_on DESC);"
)


def _kind_schema(kind: EventKind) -> str:
    """一类事件的扩展表。**列名与类型都从字段定义拼出来**，不许手写。"""
    columns = ",\n    ".join(f"{field.column} {declaration_of(field)}" for field in kind.fields)
    return (
        f"CREATE TABLE IF NOT EXISTS ev.{kind.table} (\n"
        f"    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,\n"
        f"    {columns}\n);"
    )


def _drop_script() -> str:
    """版本对不上时重建的是**事件库自己的表**。

    **只列事件库里的表名** —— 索引的重建脚本（``index.py``）同样只列它自己的两张。哪一边
    漏写一个表名，用户填的东西就没了，而两边的守护测试各盯各的那一句。
    """
    tables = [EVENTS_TABLE, BINDINGS_TABLE, *(kind.table for kind in EVENT_KINDS)]
    return "".join(f"DROP TABLE IF EXISTS {SCHEMA_NAME}.{table};" for table in tables)


def _relax_journal_mode_only(connection: sqlite3.Connection) -> None:
    """**只动日志模式，不动持久性** —— 它叫 ``only`` 就是要挡住「与索引那一个合并」。

    ``journal_mode=DELETE`` 的理由与索引相同（ADR 0038 决策 5：``-wal`` / ``-shm`` 跟着用户的库
    在云盘上互相覆盖），但 ``synchronous`` 必须保持默认。索引是丢了能重建的缓存
    （``index._relax_cache_durability`` 连 ``synchronous`` 一起降），这里是用户自己填的数据，
    两个函数名字长得像而策略**相反**，合成一个就会静默地降低事件的持久性。
    **失败只记日志**：落到 WAL 也只是多两个文件。
    """
    try:
        connection.execute(f"PRAGMA {SCHEMA_NAME}.journal_mode=DELETE")
    except sqlite3.Error as exc:
        logger.info("事件库沿用默认日志模式：%s", exc)


__all__ = [
    "BINDINGS_TABLE",
    "CHUNK",
    "EVENTS_TABLE",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "declaration_of",
]
