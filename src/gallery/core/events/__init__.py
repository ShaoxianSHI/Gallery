"""「事件」：生活中的事（旅行、演出、聚会……）与其字段定义、存放与照片关联（ADR 0043）。

事件数据住在配置目录下的 ``events.db`` —— 那里**唯一不是缓存**的东西：它是用户自己填的，
重扫照片重建不出来。界面层通常只需要 :class:`~gallery.core.events.kinds.EventKind` 那几份
类型定义与 :class:`~gallery.core.events.store.EventStore`；其余从具体子模块导入
（字段定义 :mod:`~gallery.core.events.kinds`、绑定 :mod:`~gallery.core.events.membership`），
**不要经本文件绕圈互相导入**。
"""

from gallery.core.events.kinds import (
    COMMON_FIELDS,
    EVENT_KINDS,
    KINDS_BY_ID,
    SHOW,
    TRIP,
    EventField,
    EventKind,
    fields_of,
    kind_by_id,
    suggested_title,
)

# ``prefill`` **不在这里导出**：它要 ``core.library.calendar``，而 ``core.metadata.facets``
# 反过来要本包（``EventRef``）—— 从这里拉它，那条链就成了环。领域内部按 AGENTS.md 的规矩
# 从具体子模块导入即可（``from gallery.core.events.prefill import prefill_from_photos``）。

__all__ = [
    "COMMON_FIELDS",
    "EVENT_KINDS",
    "KINDS_BY_ID",
    "SHOW",
    "TRIP",
    "EventField",
    "EventKind",
    "fields_of",
    "kind_by_id",
    "suggested_title",
]
