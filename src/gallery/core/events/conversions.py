"""事件字段与 SQL 值之间的转换，以及 journal 前像 / 后像的拼装。

**空值、日期与金额各有唯一一条归一**（:func:`_to_sql`）：新建、撤销、恢复三条路都经它，
各写一份的症状不是报错，而是「撤销回来的值与原值差一点点」。单独成模块让这条归一一眼可见。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from gallery.core.events.kinds import (
    COMMON_FIELDS,
    EventField,
    EventKind,
    join_multi,
    normalize_day,
    parse_cost,
    split_multi,
)
from gallery.core.events.models import Event, EventDraft, SqlValue
from gallery.core.metadata.journal import FieldChange


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


__all__ = [
    "_before_values",
    "_event_from_common",
    "_field_changes",
    "_field_values",
    "_from_sql",
    "_now",
    "_parse_payload",
    "_text_of",
    "_to_sql",
]
