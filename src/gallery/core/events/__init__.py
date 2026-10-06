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
    TRIP,
    EventField,
    EventKind,
    fields_of,
    kind_by_id,
    suggested_title,
)

__all__ = [
    "COMMON_FIELDS",
    "EVENT_KINDS",
    "KINDS_BY_ID",
    "TRIP",
    "EventField",
    "EventKind",
    "fields_of",
    "kind_by_id",
    "suggested_title",
]
