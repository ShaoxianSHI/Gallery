"""事件类型与字段的定义 —— **面板与 SQL 列名的唯一来源**（ADR 0043）。

每一类事件的字段都不一样，这里给每一类一份 :class:`EventKind`。面板控件与 SQLite 的建表 /
读写语句都从这份定义生成，**不许手写**：ADR 0026 那一轮把列名写错，两层都察觉不到，SQLite
直到执行才报 ``no such column``，而界面把它显示成「条件太严」。纯定义，不碰 SQLite、不碰文件、
不依赖 Qt，可直接单测。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from gallery.core.i18n import t

#: 多值字段在库里拼回一格时用的分隔符。
MULTI_SEPARATOR: Final = "、"

#: 拆分多值输入时都认的几种分隔符：中英文逗号与顿号。
_MULTI_SPLIT_CHARS: Final = ",，、"

#: 把上面那几种分隔符统一成英文逗号，于是拆分只有一条路。
_SEPARATOR_TABLE = str.maketrans({character: "," for character in _MULTI_SPLIT_CHARS})

#: 评分的取值范围。旅行、演出、影片三类共用这一档。
RATING_MIN: Final = 1
RATING_MAX: Final = 5

#: 面板里那一排评分按钮的文字。**顺序即按钮顺序**，值域之外不给。
RATING_CHOICES: Final = tuple(str(score) for score in range(RATING_MIN, RATING_MAX + 1))

#: 字段的列允许是哪几种类型。建表与读写都按它分派，别处不再判断类型名。
#:
#: ``DAY`` 不是 SQLite 的类型，是本层的一个标记：SQLite 没有日期类型，日历那类字段存成
#: ``YYYY-MM-DD`` 的文本（可直接比较与排序）。建表时它落成 ``TEXT``，读写时过一遍归一。
SQL_TYPES: Final = frozenset({"TEXT", "INTEGER", "REAL", "DAY"})


@dataclass(frozen=True, slots=True)
class EventField:
    """一类事件里的一个字段。

    ``column`` 是扩展表里的列名，``id`` 是面板与筛选里的名字（两者分开：多值字段的列名成了复数）。
    ``sql_type`` 与 ``column`` 一样由这里声明、**不许在别处手写**：花费那一列若存成 ``TEXT``，
    ``#花费 >3000`` 会被 SQLite 按文本比（``"900" > "3000"`` 为真）。
    """

    id: str
    #: 面板里那个名字的**键**（不是译好的字）：语言在 draw 之前才定（ADR 0106）。
    #: **不许有空格**：字段名是要被敲的。
    label_key: str
    column: str
    #: 建表时这一列的类型。只认 ``TEXT`` / ``INTEGER`` / ``REAL`` 三种。
    sql_type: str = "TEXT"
    #: 筛选里 ``#字段名`` 缺值时给的那句「例如 …」的键。**面板不在输入框底部显示它。**
    hint_key: str = ""
    #: 能不能留空。
    required: bool = False
    #: 能不能填多项（填了就按 :data:`MULTI_SEPARATOR` 拼回一格存）。
    multiple: bool = False
    #: 非空时面板把它画成下拉而不是输入框，值域就是这几项。
    choices: tuple[str, ...] = ()
    #: 非空时面板**不画成输入框**，而是交给那一族专门的控件（现在是封面）。留着空串的是
    #: 「就是一格文字」，与 :attr:`choices` 同一个约定：控件由字段自己的声明说了算。
    control: str = ""

    @property
    def label(self) -> str:
        return t(self.label_key)

    @property
    def hint(self) -> str:
        return t(self.hint_key) if self.hint_key else ""


@dataclass(frozen=True, slots=True)
class EventKind:
    """一类事件。``id`` 存进库里，``label`` 上菜单。"""

    id: str
    label_key: str
    #: 存这一类独有字段的那张表的表名。
    table: str
    fields: tuple[EventField, ...]
    #: 自动标题在日期之后接哪几栏：**取第一个非空的**（旅行是目的地，演出先剧目再艺人）。
    title_parts: tuple[str, ...] = ()
    #: 从选中照片预填地点时填进哪一栏（旅行填目的地，演出填城市）。
    place_field: str = ""
    #: 没填名称时菜单里那一行写什么的键（分组的「空组」标题也用它）。
    empty_label_key: str = "event.kind.default_empty"

    @property
    def label(self) -> str:
        return t(self.label_key)

    @property
    def empty_label(self) -> str:
        return t(self.empty_label_key)


#: 事件的封面：一条指向照片的路径（ADR 0048）。**它是一个公共字段而不是结构列** ——
#: 于是建表、读写、journal 前像、撤销与恢复全由字段定义生成，手写一列要在七个地方各写一遍，
#: 而漏掉 ``_field_changes`` 那一处的症状是「改封面不进撤销栈」，不报错。
COVER: Final = EventField("cover", "event.field.cover", "cover_path", control="cover")


#: **公共列**：任何一类事件都问得出的那几个问题。它们住在 ``events`` 表里，于是「列出所有旅行」
#: 「这张照片属于谁」都是一条 SQL，不必按类型 UNION。
COMMON_FIELDS: Final[tuple[EventField, ...]] = (
    EventField(
        "title",
        "event.field.title",
        "title",
        hint_key="event.field.title.hint",
        required=True,
    ),
    EventField(
        "started_on",
        "event.field.started_on",
        "started_on",
        "DAY",
        hint_key="event.field.started_on.hint",
    ),
    EventField(
        "ended_on",
        "event.field.ended_on",
        "ended_on",
        "DAY",
        hint_key="event.field.ended_on.hint",
    ),
    EventField("note", "event.field.note", "note"),
    COVER,
)

TRIP: Final = EventKind(
    id="trip",
    label_key="event.kind.trip",
    table="event_trip",
    fields=(
        EventField(
            "destination",
            "event.field.destination",
            "destinations",
            hint_key="event.field.destination.hint",
            multiple=True,
        ),
        EventField(
            "companions",
            "event.field.companions",
            "companions",
            hint_key="event.field.companions.hint",
            multiple=True,
        ),
        EventField("transport", "event.field.transport", "transport"),
        EventField("lodging", "event.field.lodging", "lodging"),
        EventField(
            "cost",
            "event.field.cost",
            "cost",
            "REAL",
            hint_key="event.field.cost.hint",
        ),
        EventField("rating", "event.field.rating", "rating", "INTEGER", choices=RATING_CHOICES),
    ),
    title_parts=("destination",),
    place_field="destination",
    empty_label_key="event.kind.trip.empty",
)

SHOW: Final = EventKind(
    id="show",
    label_key="event.kind.show",
    table="event_show",
    fields=(
        EventField(
            "artist",
            "event.field.artist",
            "artist",
            hint_key="event.field.artist.hint",
            multiple=True,
        ),
        EventField(
            "guests",
            "event.field.guests",
            "guests",
            hint_key="event.field.guests.hint",
            multiple=True,
        ),
        EventField(
            "program",
            "event.field.program",
            "program",
            hint_key="event.field.program.hint",
        ),
        EventField(
            "show_type",
            "event.field.show_type",
            "show_type",
            hint_key="event.field.show_type.hint",
        ),
        EventField(
            "start_time",
            "event.field.start_time",
            "start_time",
            hint_key="event.field.start_time.hint",
        ),
        EventField("city", "event.field.city", "city"),
        EventField("venue", "event.field.venue", "venue", hint_key="event.field.venue.hint"),
        EventField(
            "ticket_price",
            "event.field.ticket_price",
            "ticket_price",
            "REAL",
            hint_key="event.field.ticket_price.hint",
        ),
        EventField(
            "paid",
            "event.field.paid",
            "paid",
            "REAL",
            hint_key="event.field.paid.hint",
        ),
        EventField("seat", "event.field.seat", "seat", hint_key="event.field.seat.hint"),
        EventField(
            "channel",
            "event.field.channel",
            "channel",
            hint_key="event.field.channel.hint",
        ),
        EventField("rating", "event.field.rating", "rating", "INTEGER", choices=RATING_CHOICES),
        EventField("thoughts", "event.field.thoughts", "thoughts"),
    ),
    title_parts=("program", "artist"),
    place_field="city",
    empty_label_key="event.kind.show.empty",
)

#: 「来源」那一栏的值域：这部影片是从哪个网站抓来的（ADR 0068 / 0082 / 0083）。
#: **它同时是打开影片网站与判重的键**，所以值都要稳定；用网站自己的名字是因为译成中文反而
#: 认不出（YouTube 就是 YouTube），而中文站本来就以中文示人。
SOURCE_JAV: Final = "JavDB"
SOURCE_YOUTUBE: Final = "YouTube"
SOURCE_BILIBILI: Final = "Bilibili"
SOURCE_TENCENT: Final = "腾讯视频"
SOURCE_IQIYI: Final = "爱奇艺"
SOURCE_YOUKU: Final = "优酷"
SOURCE_MANGO: Final = "芒果TV"
SOURCE_DOUYIN: Final = "抖音"
SOURCE_TIKTOK: Final = "TikTok"
SOURCE_PORNHUB: Final = "Pornhub"

#: 已知的来源。**顺序即面板里那一排按钮的顺序**（每行四个，最后一行两个）。
MOVIE_SOURCES: Final = (
    SOURCE_IQIYI,
    SOURCE_TENCENT,
    SOURCE_YOUKU,
    SOURCE_MANGO,
    SOURCE_BILIBILI,
    SOURCE_YOUTUBE,
    SOURCE_DOUYIN,
    SOURCE_TIKTOK,
    SOURCE_JAV,
    SOURCE_PORNHUB,
)

MOVIE: Final = EventKind(
    id="movie",
    label_key="event.kind.movie",
    table="event_movie",
    fields=(
        EventField("source", "event.field.source", "source", choices=MOVIE_SOURCES),
        EventField(
            "code",
            "event.field.code",
            "code",
            hint_key="event.field.code.hint",
        ),
        EventField(
            "duration",
            "event.field.duration",
            "duration",
            "INTEGER",
            hint_key="event.field.duration.hint",
        ),
        # 三栏都取中性词：同一栏要装下片商、频道与 UP 主（ADR 0068 决策 2）。
        EventField(
            "creator",
            "event.field.creator",
            "creator",
            hint_key="event.field.creator.hint",
        ),
        EventField("label", "event.field.label", "label"),
        EventField("series", "event.field.series", "series", hint_key="event.field.series.hint"),
        EventField("director", "event.field.director", "director"),
        EventField(
            "actors",
            "event.field.actors",
            "actors",
            hint_key="event.field.actors.hint",
            multiple=True,
        ),
        EventField(
            "tags",
            "event.field.tags",
            "tags",
            hint_key="event.field.tags.hint",
            multiple=True,
        ),
        EventField("rating", "event.field.rating", "rating", "INTEGER", choices=RATING_CHOICES),
    ),
    # 发行日期复用公共列 ``started_on``：菜单排序与「按事件分组」的组键都读它，
    # 单独开一列会让影片全落到「无日期」那一组里。
    title_parts=("code",),
    empty_label_key="event.kind.movie.empty",
)

#: 已知的事件类型。**顺序即菜单顺序**。
EVENT_KINDS: Final[tuple[EventKind, ...]] = (TRIP, SHOW, MOVIE)

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


def suggested_title(kind: EventKind, values: Mapping[str, str]) -> str:
    """新建事件时给名称栏的一个默认值：「开始日期 · 关键字段」。

    「关键字段」由 :attr:`EventKind.title_parts` 说了算，取第一个非空的。有内容就**不带类型名**
    （用户刚从菜单点过来）；什么都没填才退回一个能认出来的名字 —— 参照系统日历新建事件的行为。
    """
    head = values.get("started_on", "").strip()
    tail = _title_tail(kind, values)
    parts = tuple(part for part in (head, tail) if part)
    return " · ".join(parts) or t("event.suggested_title.new", kind=kind.label)


def _title_tail(kind: EventKind, values: Mapping[str, str]) -> str:
    """``title_parts`` 里第一个非空的那一栏；多值字段先归一成顿号分隔。"""
    fields = {field.id: field for field in kind.fields}
    for field_id in kind.title_parts:
        text = values.get(field_id, "").strip()
        if not text:
            continue
        field = fields.get(field_id)
        return join_multi(split_multi(text)) if field is not None and field.multiple else text
    return ""


def title_problem(kind: EventKind, title: str) -> str | None:
    """名称那一栏有什么不对就说清楚。``None`` 表示没问题。"""
    if title.strip():
        return None
    return t("event.problem.title", kind=kind.label)


def started_on_problem(text: str) -> str | None:
    """开始日期那一栏有什么不对就说清楚。年月（``2026-05``）也算 —— 用户常常只记得那个。"""
    if not text.strip():
        return None
    if parse_calendar_day(text) is None:
        return t("event.problem.started_on", date=text.strip())
    return None


def ended_on_problem(started_on: str, ended_on: str) -> str | None:
    """结束日期与开始日期对不对得上。"""
    if not ended_on.strip():
        return None
    end = parse_calendar_day(ended_on)
    if end is None:
        return t("event.problem.ended_on", date=ended_on.strip())
    start = parse_calendar_day(started_on)
    if start is not None and end < start:
        return t(
            "event.problem.ended_before_started",
            end=ended_on.strip(),
            start=started_on.strip(),
        )
    return None


def cost_problem(text: str, label: str = "") -> str | None:
    """某一栏金额有什么不对就说清楚（花费、票价、实付都走它）。不记这一项就留空，所以空不是错。"""
    stripped = text.strip()
    label = label or "event.field.cost"
    if not stripped:
        return None
    value = parse_cost(stripped)
    if value is None:
        return t("event.problem.cost_not_number", label=label, text=stripped)
    if value < 0:
        return t("event.problem.cost_negative", label=label)
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


def rating_text(value: float | None) -> str:
    """把评分（来源站常给小数）收进 1–5 这一档；收不进去就是空 —— 那就是没评分。

    **四舍五入到整数**：面板上只有五个按钮，没有半星；留着小数的话那一格读回来谁也选不中。
    ``0`` 与 6 以上都当没评分（来源站用 0 表示「还没有人评」）。
    """
    if value is None:
        return ""
    score = int(value + 0.5)
    return str(score) if RATING_MIN <= score <= RATING_MAX else ""


def parse_calendar_day(text: str) -> date | None:
    """把用户填的日期读成 ``date``；看不懂就是 ``None``。

    **月与日可以省**：``2026-05`` 读成那个月的 1 号 —— 旅行常常只知道「五月去的」，逼用户补一个
    具体日子只会让人随便挑一个。具体日子交给 :class:`datetime.date` 判（``2026-02-31`` 会被挡下）。
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


def minutes_of_seconds(seconds: int | None) -> int | None:
    """秒 → 「时长」那一栏的分钟（四舍五入）。**不足半分钟的就是空**。

    YouTube 与 B 站给的都是秒，而那一栏是给人看的整数：170 分钟比 10200 秒好读。
    两个来源共用这一条规则，各写一份的话总有一天一个四舍五入、另一个向下取整。
    """
    if not seconds or seconds < 0:
        return None
    return round(seconds / 60) or None


__all__ = [
    "COMMON_FIELDS",
    "COVER",
    "EVENT_KINDS",
    "KINDS_BY_ID",
    "MOVIE",
    "MOVIE_SOURCES",
    "MULTI_SEPARATOR",
    "RATING_CHOICES",
    "RATING_MAX",
    "RATING_MIN",
    "SHOW",
    "SOURCE_BILIBILI",
    "SOURCE_DOUYIN",
    "SOURCE_IQIYI",
    "SOURCE_JAV",
    "SOURCE_MANGO",
    "SOURCE_PORNHUB",
    "SOURCE_TENCENT",
    "SOURCE_TIKTOK",
    "SOURCE_YOUKU",
    "SOURCE_YOUTUBE",
    "SQL_TYPES",
    "TRIP",
    "EventField",
    "EventKind",
    "cost_problem",
    "ended_on_problem",
    "fields_of",
    "join_multi",
    "kind_by_id",
    "minutes_of_seconds",
    "normalize_day",
    "parse_calendar_day",
    "parse_cost",
    "rating_text",
    "split_multi",
    "started_on_problem",
    "suggested_title",
    "title_problem",
]
