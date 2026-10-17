"""事件的数据类型：一个事件、一份草稿、一条引用与一条绑定（ADR 0043）。

它们是**值对象**（字段随类型增删，所以用 ``values`` 字典），不含任何 SQL —— 建表在
:mod:`gallery.core.events.schema`、读写与 journal 在 :mod:`gallery.core.events.store`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from gallery.core.events.kinds import kind_by_id

type SqlValue = str | int | float | None

#: 界面上摆一个事件名时最长这么多字符，超了从中间省略。菜单、横幅、元数据面板共用这一个数 ——
#: 各写一份的话，同一个事件在三处会被截得不一样长。
DISPLAY_NAME_MAX: Final = 20


@dataclass(frozen=True, slots=True)
class Event:
    """一个事件：**所有字段的值都在 :attr:`values` 里**，键是 ``EventField.id``。

    公共列（名称、日期、备注）与类型独有的字段一视同仁 —— 面板预填因此就是一次循环，而
    「公共列有哪几个」只有 :data:`COMMON_FIELDS` 一处说了算。常用的那几样另有同名属性可读。
    **是字典而不可哈希**：字段随类型增删，具名属性做不到。别拿它当键用。
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
    def cover_path(self) -> Path | None:
        """封面照片的路径；没设就是 ``None``（界面上退回「组内第一张」，ADR 0048 决策 1）。

        **返回 ``None`` 而不是空串**：这一列可空，而「没设封面」在渲染那一侧是一个分支，
        空串会让那个分支写成 ``if event.cover_path != ""`` 这种没人会想到去看的形状。
        """
        stored = self.text("cover")
        return Path(stored) if stored else None

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

    ``event`` 是**写完之后**的样子；删除时是**删之前**的样子 —— 两种情形界面都要用它的名字。
    ``unbound`` 只有删除时非空，撤销靠它把绑定放回去。``changed`` 为假表示这次保存与库里那一份
    逐字段一样 —— 界面据此说「没有改动」。
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
    """照片属于哪些事件时只需要带这么几样：分组要组键与显示名，菜单要名字。

    :attr:`cover_path` 是**这个事件的封面**（ADR 0048），**不是这张照片的** —— 一张照片属于几个
    事件，就带着几份封面。分组靠它认出「这一组该画哪一张」，适用的条件是组键对得上（见
    :mod:`gallery.core.library.groups`）。:attr:`values` 是这类事件独有字段的文字值，
    「按旅行·目的地分组」要靠它；没值的字段不进来，于是空元组也很常见。
    """

    id: int
    kind: str
    title: str
    started_on: str | None
    #: 封面照片的路径（库里存的是文本）；没设就是 ``None``。
    cover_path: str | None = None
    #: 这类事件独有字段的文字值：``(字段 id, 文字)``。**公共列不在其中** —— 名称与日期
    #: 已有同名字段，重复带一份只会让「值从哪儿来」多一条路。
    values: tuple[tuple[str, str], ...] = ()

    def text(self, field_id: str) -> str:
        """这个事件某一栏的文字值；没填就是空串。

        「开始日期」这类公共列不在 :attr:`values` 里，**从各自的属性上答**；于是分组那一层
        对公共列与独有字段只有一条取值路径。
        """
        if field_id == "started_on":
            return self.started_on or ""
        return next((value for key, value in self.values if key == field_id), "")

    @property
    def group_key(self) -> str:
        """分组的组键：``起始日期#事件 id``。

        日期在前，于是 :func:`~gallery.core.metadata.facets._date_group_order` 那种「把键里的数字
        全取出来拼成一个数」的比较法直接能用（日期占大头、id 用来定同日期的先后）；id 在后是
        因为组键必须**唯一** —— 两个同名同日的旅行不能挤成一组。
        """
        return f"{self.started_on or ''}#{self.id}"

    @property
    def label(self) -> str:
        return self.title


@dataclass(frozen=True, slots=True)
class Orphan:
    """一条绑定，外加它属于哪个事件。

    **放在这儿而不是 ``orphans``**：它是 :meth:`EventStore.all_bindings` 造出来的，而那一头
    要读绑定表 —— 放到 ``orphans`` 就是包内两个模块互相导入。孤儿检查
    （:mod:`gallery.core.events.orphans`）从这里导入它。
    """

    path: Path
    event_id: int
    event_title: str


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


def shorten_name(name: str) -> str:
    """事件名太长时从中间省略（两头是日期与地点，比中间那一截更有信息量）。"""
    if len(name) <= DISPLAY_NAME_MAX:
        return name
    head = (DISPLAY_NAME_MAX - 1) // 2
    tail = DISPLAY_NAME_MAX - 1 - head
    return f"{name[:head]}…{name[-tail:]}"


__all__ = [
    "DISPLAY_NAME_MAX",
    "Event",
    "EventDraft",
    "EventRef",
    "EventUndoResult",
    "EventWriteOutcome",
    "Orphan",
    "SqlValue",
    "shorten_name",
]
