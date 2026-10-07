"""事件类型与字段的定义 —— **面板与 SQL 列名的唯一来源**（ADR 0043）。

每一类事件（旅行、演出……）的字段都不一样，所以这里给每一类一份 :class:`EventKind`，
里面列着它独有的 :class:`EventField`。两处都从这份定义生成，都不许手写：

- 输入面板由 :data:`COMMON_FIELDS` 加 ``kind.fields`` **生成**控件；
- SQLite 的建表语句与读写语句由 ``field.column`` **拼出**列名。

**为什么强调「不许手写」**：ADR 0026 那一轮把拍摄时间的列名写成了 ``f.captured_at``（那是
``PhotoFacets`` 上的属性名，列名其实是 ``captured``），解析层与生成 SQL 两层都察觉不到，
SQLite 直到执行才说 ``no such column``，而那句话被包成「检索没能完成」——界面上与
「条件太严」长得一模一样。加一个字段的守护测试因此是**拿真 schema 把每个声明的列跑一遍**。

纯定义：不碰 SQLite、不碰文件、不依赖 Qt，因此可以直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

#: 多值字段在库里拼回一格时用的分隔符。
MULTI_SEPARATOR: Final = "、"

#: 拆分多值输入时都认的几种分隔符：中英文逗号与顿号。
_MULTI_SPLIT_CHARS: Final = ",，、"

#: 把上面那几种分隔符统一成英文逗号，于是拆分只有一条路。
_SEPARATOR_TABLE = str.maketrans({character: "," for character in _MULTI_SPLIT_CHARS})

#: 评分的取值范围（旅行这一类用它；将来别的类型也用同一档）。
RATING_MIN: Final = 1
RATING_MAX: Final = 5

#: 字段的列允许是哪几种类型。建表与读写都按它分派，别处不再判断类型名。
#:
#: ``DAY`` 不是 SQLite 的类型，是本层的一个标记：SQLite 没有日期类型，日历那类字段存成
#: ``YYYY-MM-DD`` 的文本（可直接比较与排序）。建表时它落成 ``TEXT``，读写时过一遍归一。
SQL_TYPES: Final = frozenset({"TEXT", "INTEGER", "REAL", "DAY"})


@dataclass(frozen=True, slots=True)
class EventField:
    """一类事件里的一个字段。

    ``column`` 是它在扩展表里的列名，``id`` 是它在面板与筛选里的名字 —— 两者分开是因为
    「目的地」那一栏可以填「京都、奈良」多项，列名于是成了复数。

    ``sql_type`` 与 ``column`` 一样由这里声明、**不许在别处手写**：花费那一列存成 ``TEXT`` 的
    话，``#花费 >3000`` 会被 SQLite 按文本比（``"900" > "3000"`` 为真）。
    """

    id: str
    #: 面板与筛选里用的中文名（``#目的地 京都`` 敲的就是它）。**不许有空格**：字段名是要被敲的。
    label: str
    column: str
    #: 建表时这一列的类型。只认 ``TEXT`` / ``INTEGER`` / ``REAL`` 三种。
    sql_type: str = "TEXT"
    #: 筛选里 ``#字段名`` 缺值时给的那句「例如 …」。**面板不在输入框底下显示它。**
    hint: str = ""
    #: 能不能留空。
    required: bool = False
    #: 能不能填多项（填了就按 :data:`MULTI_SEPARATOR` 拼回一格存）。
    multiple: bool = False
    #: 非空时面板把它画成下拉而不是输入框，值域就是这几项。
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EventKind:
    """一类事件。``id`` 存进库里，``label`` 上菜单。"""

    id: str
    label: str
    #: 存这一类独有字段的那张表的表名。
    table: str
    fields: tuple[EventField, ...]
    #: 没填名称时菜单里那一行写什么（分组的「空组」标题也用它）。
    empty_label: str = "未命名"


#: **公共列**：任何一类事件都问得出的那几个问题。它们住在 ``events`` 表里，于是「列出所有旅行」
#: 「这张照片属于谁」都是一条 SQL，不必按类型 UNION。
COMMON_FIELDS: Final[tuple[EventField, ...]] = (
    EventField("title", "名称", "title", hint="不填的话按目的地与日期自动生成", required=True),
    EventField("started_on", "开始日期", "started_on", "DAY", hint="2026-05-16，只知道年月也可以"),
    EventField("ended_on", "结束日期", "ended_on", "DAY", hint="当天来回就与开始日期同一天"),
    EventField("note", "备注", "note"),
)

TRIP: Final = EventKind(
    id="trip",
    label="旅行",
    table="event_trip",
    fields=(
        EventField(
            "destination",
            "目的地",
            "destinations",
            hint="一次去了几个地方就都写上，用逗号分开",
            multiple=True,
        ),
        EventField("companions", "同行者", "companions", hint="用逗号分开", multiple=True),
        EventField("transport", "交通方式", "transport"),
        EventField("lodging", "住宿", "lodging"),
        EventField("cost", "花费", "cost", "REAL", hint="只填数字；不记这一项就留空"),
        EventField(
            "rating",
            "评分",
            "rating",
            "INTEGER",
            choices=tuple(str(score) for score in range(RATING_MIN, RATING_MAX + 1)),
        ),
    ),
    empty_label="未命名旅行",
)

#: 已知的事件类型。**顺序即菜单顺序**。
EVENT_KINDS: Final[tuple[EventKind, ...]] = (TRIP,)

KINDS_BY_ID: Final[dict[str, EventKind]] = {kind.id: kind for kind in EVENT_KINDS}


def kind_by_id(kind_id: str | None) -> EventKind | None:
    """按 id 取一类事件；认不出来的（含 ``None``）返回 ``None``。"""
    if kind_id is None:
        return None
    return KINDS_BY_ID.get(kind_id)


def fields_of(kind: EventKind) -> tuple[EventField, ...]:
    """面板要摆的字段：公共列在前，这一类独有的在后。"""
    return (*COMMON_FIELDS, *kind.fields)


def split_multi(text: str) -> tuple[str, ...]:
    """把「京都, 奈良」这样的输入切成几项，空项丢掉。"""
    normalized = text.translate(_SEPARATOR_TABLE)
    return tuple(part for part in (piece.strip() for piece in normalized.split(",")) if part)


def join_multi(items: tuple[str, ...]) -> str:
    """把几项拼回一格（存进库里的样子）。"""
    return MULTI_SEPARATOR.join(items)


def suggested_title(kind: EventKind, destination: str, started_on: str) -> str:
    """新建事件时给名称栏的一个默认值：「开始日期 · 目的地」。

    比留空必填少一次决定 —— 参照的是系统日历新建事件时那个自动标题的行为。有内容就**不带
    类型名**（用户刚从菜单点过来，已经知道是旅行）；什么都没填才退回一个能认出来的名字。
    """
    tail = " · ".join(
        part for part in (started_on.strip(), join_multi(split_multi(destination))) if part
    )
    return tail or f"新的{kind.label}"


def title_problem(kind: EventKind, title: str) -> str | None:
    """名称那一栏有什么不对就说清楚。``None`` 表示没问题。"""
    if title.strip():
        return None
    return f"这一次{kind.label}得有个名字：以后只能靠它认出是哪一趟。"


def started_on_problem(text: str) -> str | None:
    """开始日期那一栏有什么不对就说清楚。年月（``2026-05``）也算 —— 用户常常只记得那个。"""
    if not text.strip():
        return None
    if parse_calendar_day(text) is None:
        return f"开始日期看不懂：「{text.strip()}」。可以填成 2026-05-16，也可以只填 2026-05。"
    return None


def ended_on_problem(started_on: str, ended_on: str) -> str | None:
    """结束日期与开始日期对不对得上。"""
    if not ended_on.strip():
        return None
    end = parse_calendar_day(ended_on)
    if end is None:
        return f"结束日期看不懂：「{ended_on.strip()}」。可以填成 2026-05-20，也可以只填 2026-05。"
    start = parse_calendar_day(started_on)
    if start is not None and end < start:
        return f"结束日期（{ended_on.strip()}）比开始日期（{started_on.strip()}）还早。"
    return None


def cost_problem(text: str) -> str | None:
    """花费那一栏有什么不对就说清楚。不记这一项就留空，所以空不是错。"""
    stripped = text.strip()
    if not stripped:
        return None
    value = parse_cost(stripped)
    if value is None:
        return f"花费不是一个数字：「{stripped}」。只填数字就行，比如 3280 或 3280.50。"
    if value < 0:
        return "花费不会是负的。"
    return None


def parse_cost(text: str) -> float | None:
    """把花费那一栏读成数字；看不懂就是 ``None``。**允许千分位逗号**（``3,280``）。"""
    cleaned = text.strip().replace(",", "").replace("，", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_calendar_day(text: str) -> date | None:
    """把用户填的日期读成 ``date``；看不懂就是 ``None``。

    **月与日可以省**：``2026-05`` 读成那个月的 1 号 —— 旅行常常只知道「五月去的」，
    逼用户补一个具体日子只会让人随便挑一个，而那个日子会跟着进日期筛选。

    具体日子交给 :class:`datetime.date` 判（``2026-02-31`` 会被挡下来），手写「一个月最多
    31 天」会把二月糊弄过去。
    """
    parts = text.strip().split("-")
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        return None
    year, month = numbers[0], numbers[1]
    day = numbers[2] if len(numbers) == 3 else 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_day(text: str) -> str:
    """把日期读成 ``YYYY-MM-DD`` 存进库；空的就是空。看不懂时原样留着（校验那一关会挡下来）。"""
    parsed = parse_calendar_day(text)
    return text.strip() if parsed is None else parsed.isoformat()


__all__ = [
    "COMMON_FIELDS",
    "EVENT_KINDS",
    "KINDS_BY_ID",
    "MULTI_SEPARATOR",
    "RATING_MAX",
    "RATING_MIN",
    "SQL_TYPES",
    "TRIP",
    "EventField",
    "EventKind",
    "cost_problem",
    "ended_on_problem",
    "fields_of",
    "join_multi",
    "kind_by_id",
    "normalize_day",
    "parse_calendar_day",
    "parse_cost",
    "split_multi",
    "started_on_problem",
    "suggested_title",
    "title_problem",
]
