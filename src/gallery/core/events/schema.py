"""事件库的建表与版本：表名、列清单、DDL、迁移步与连接上的那一条 pragma。

它**只描述库长什么样**，不含读写（那在 :mod:`gallery.core.events.store`）。单独成模块是因为这里
几条约束各自都能静默出事：列清单必须从字段定义生成，而 ``user_version`` 与 ``journal_mode`` 都是
**每个库各一份**、必须带 ``ev.`` 前缀。**版本对不上时是迁移，不是重建**（ADR 0048 决策 3）：事件
是唯一一份重建不出来的数据，认不出的版本（比这份程序新）改成**不挂事件库**。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from gallery.core import paths
from gallery.core.events.kinds import (
    COMMON_FIELDS,
    COVER,
    MOVIE,
    SOURCE_JAV,
    EventField,
    EventKind,
)
from gallery.core.sql import ESCAPE, like_pattern

logger = logging.getLogger(__name__)

#: 挂到索引连接上时的别名。搜索层的子查询按它写（``ev.photo_events``）。
SCHEMA_NAME: Final = "ev"


#: 公共表与绑定表的表名。**搜索层按它们写子查询**，所以名字是常量而不是各处各写一遍 ——
#: 写错要到 SQLite 执行时才报 ``no such table``，而那一眼看上去是「这一栏没得筛」。
EVENTS_TABLE: Final = "events"


BINDINGS_TABLE: Final = "photo_events"


#: 事件库的表结构版本。**只管 events.db 自己那份** ``PRAGMA ev.user_version``，
#: 与索引的 ``SCHEMA_VERSION`` 无关（索引升版会重建它的表，事件库不会）。
#:
#: **升版只做「加列 / 改名 / 一条显式语句」**（ADR 0048 决策 3、ADR 0068 决策 7）：每一步都要在
#: :data:`_MIGRATIONS` 里补一条，而那一级不许空着（``test_store`` 里有回归测试）。
SCHEMA_VERSION: Final = 5


#: 一条语句里最多塞多少个绑定变量。``IN (…)`` 与多列插入都按它分批：
#: SQLite 的上限是 999（老版本），一行的绑定数还不止一个，所以按**行**取这个数的一半。
CHUNK: Final = 400


#: 绑定表。它与类型无关，所以硬编码在这儿（扩展表按类型生成，见 :func:`_kind_schema`）。
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
    校验在面板那一层，这里只是最后一层保险。
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


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    """这张表有没有这一列。**迁移步因此是幂等的**：新库走完建表语句已经有那一列，
    再 ``ALTER`` 就是 ``duplicate column name``（那是新库第一次打开就报错）。"""
    rows = connection.execute(f"PRAGMA {SCHEMA_NAME}.table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _add_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    """给一张表补一列。**没有才补** —— 见 :func:`_has_column` 那条理由。"""
    if not _has_column(connection, table, column):
        connection.execute(f"ALTER TABLE {SCHEMA_NAME}.{table} ADD COLUMN {column} {declaration}")


@dataclass(frozen=True, slots=True)
class _AddColumn:
    """给一张表补一列（ADR 0048 起就有这一种步子）。"""

    table: str
    column: str
    declaration: str


@dataclass(frozen=True, slots=True)
class _RenameColumn:
    """把一列改名。

    **旧列在新列不在时才改**：新库第一次打开时建表语句已经建的是新列名，这时改名会报
    ``no such column``（与加列同一个幂等理由）。
    """

    table: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class _RunSql:
    """一条显式语句。

    **只在加列与改名表达不了的时候用**（回填一列的值；将来真要重建某一张表也走它）。
    写在这里的语句同样要幂等 —— 每个库都会把它跑一遍。
    """

    statement: str


@dataclass(frozen=True, slots=True)
class _RelativizePaths:
    """把绝对路径改成相对库根的键（ADR 0086）。**要库根，所以语句在运行时才拼**。"""


#: 迁移步里能做的一件事。四种都是**幂等**的，理由见各自的类。
type _MigrationStep = _AddColumn | _RenameColumn | _RunSql | _RelativizePaths


#: 迁移步：``_MIGRATIONS[i]`` 是把 ``ev.user_version`` 从 ``i`` 升到 ``i + 1`` 要做的事。
#:
#: **升版只加列 / 改名 / 跑一条显式语句，绝不重建表。** 事件是唯一一份重建不出来的数据
#: （ADR 0043 决策 1），而原先「版本对不上就 ``DROP`` 全部表」那条老路让「加一列」等于清空
#: 用户的全部事件与绑定。将来真要重建某一张表，写成这一步里的一条显式语句（里面可以带 ``DROP``），
#: **不再由「版本对不上」这个隐式条件触发**。
#:
#: 索引 0 是「建表」，由 :data:`_EVENTS_SCHEMA` 那几段建表语句本身承担（新库直接建出最新结构），
#: 所以它是空的 —— **那是唯一允许为空的一级**（回归测试盯着其余每一级都要有语句）。
_MIGRATIONS: Final[tuple[tuple[_MigrationStep, ...], ...]] = (
    (),  # 0 → 1：建表语句自己就是这一步
    (_AddColumn(EVENTS_TABLE, COVER.column, "TEXT"),),  # 1 → 2：封面那一列（ADR 0048）
    (  # 2 → 3：影片的来源，以及那三栏改取中性词（ADR 0068 决策 2 与决策 7）
        _AddColumn(MOVIE.table, "source", "TEXT"),
        _RenameColumn(MOVIE.table, "studio", "creator"),
        # 这一条之前，影片只有 JavDB 一个来路，所以已有的行补上它。**不许省**：不补的话，
        # 「库里已有的影片」与「这次要导入的同一部」在判重时对不上，用户会拿到两份一样的。
        _RunSql(
            f"UPDATE {SCHEMA_NAME}.{MOVIE.table} SET source = '{SOURCE_JAV}'"
            " WHERE source IS NULL OR source = ''"
        ),
    ),
    (  # 3 → 4：影片评分从「随便填的数字」收进 1–5 这一档（ADR 0084）
        _RunSql(
            f"UPDATE {SCHEMA_NAME}.{MOVIE.table} SET rating ="
            " CASE WHEN ROUND(rating) BETWEEN 1 AND 5 THEN CAST(ROUND(rating) AS INTEGER)"
            " ELSE NULL END"
            " WHERE rating IS NOT NULL"
        ),
    ),
    (  # 4 → 5：绑定与封面改存相对库根的键，整个文件夹搬走也还指得对（ADR 0086）
        _RelativizePaths(),
    ),
)


def _run_step(connection: sqlite3.Connection, step: _MigrationStep, root: Path) -> None:
    """做迁移步里的一件事。**认不出的种类就是缺陷**（加了一种却忘了在这里分派）。"""
    if isinstance(step, _AddColumn):
        _add_column(connection, step.table, step.column, step.declaration)
    elif isinstance(step, _RenameColumn):
        if _has_column(connection, step.table, step.old) and not _has_column(
            connection, step.table, step.new
        ):
            connection.execute(
                f"ALTER TABLE {SCHEMA_NAME}.{step.table} RENAME COLUMN {step.old} TO {step.new}"
            )
    elif isinstance(step, _RelativizePaths):
        _relativize(connection, root)
    else:
        connection.execute(step.statement)


def _relativize(connection: sqlite3.Connection, root: Path) -> None:
    """把库内那些绝对路径改成相对库根的键。**库外的不动** —— 那是旧记录或明确绑在外面的。"""
    prefix = paths.library_key_prefix(root)
    pattern = like_pattern(prefix, "%")
    offset = len(prefix) + 1
    connection.execute(
        f"UPDATE {SCHEMA_NAME}.{BINDINGS_TABLE} SET path = substr(path, ?)"
        f" WHERE path LIKE ? ESCAPE '{ESCAPE}'",
        (offset, pattern),
    )
    connection.execute(
        f"UPDATE {SCHEMA_NAME}.events SET {COVER.column} = substr({COVER.column}, ?)"
        f" WHERE {COVER.column} LIKE ? ESCAPE '{ESCAPE}'",
        (offset, pattern),
    )


def migrate(connection: sqlite3.Connection, version: int, *, root: Path) -> None:
    """把事件库从 ``version`` 升到 :data:`SCHEMA_VERSION`。

    **调用方要已经建好表**，所以顺序是「先建表、后迁移」：旧库补上缺的那几列，新库已经带着最新列、
    迁移看见它在就跳过（幂等）。**整个链条在一个事务里**：``executescript`` 会隐式提交，用它跑迁移
    断在半路时库就停在中间状态。所以这里只 ``execute``，事务由调用方的 ``with connection:`` 给出。
    """
    for step in range(max(version, 0), SCHEMA_VERSION):
        for action in _MIGRATIONS[step]:
            _run_step(connection, action, root)


def _relax_journal_mode_only(connection: sqlite3.Connection) -> None:
    """**只动日志模式，不动持久性** —— 它叫 ``only`` 就是要挡住「与索引那一个合并」。

    ``journal_mode=DELETE`` 的理由与索引相同（ADR 0038 决策 5），但 ``synchronous`` 必须保持默认：
    索引是丢了能重建的缓存（``index._relax_cache_durability`` 连 ``synchronous`` 降），这里是用户
    自己填的数据，两个策略**相反**，合成一个就会静默地降低事件的持久性。**失败只记日志**。
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
    "migrate",
]
