"""搜索：条件长什么样、落到哪一列、整句 SQL 长什么样（纯函数，不碰文件也不碰 Qt）。

字段 id 与 :mod:`gallery.core.metadata.facets` 用的是同一套，不另立一套：两套字段表迟早各自漂，
漂了之后「界面上写的那个相机」与「查的那个相机」就对不上。**SQL 只在 :func:`build_query` 生成**，
于是「哪个字段落到哪一列、怎么匹配」在代码里是可 grep 的一段（ADR 0026 决策 6）。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NamedTuple

from gallery.core import paths
from gallery.core.events.kinds import EVENT_KINDS
from gallery.core.events.store import BINDINGS_TABLE, EVENTS_TABLE, SCHEMA_NAME
from gallery.core.library.photos import FolderRef, PhotoRef, normalize_suffix
from gallery.core.metadata.facets import (
    FACET_APERTURE,
    FACET_CAMERA,
    FACET_CAPTURED,
    FACET_CITY,
    FACET_COUNTRY,
    FACET_FOCAL,
    FACET_FOLDER,
    FACET_FORMAT,
    FACET_ISO,
    FACET_KEYWORD,
    FACET_LENS,
    FACET_MODIFIED,
    FACET_NAME,
    FACET_PIXELS,
    FACET_REGION,
    FACET_SHUTTER,
    FACET_SIZE,
    relative_folder,
)
from gallery.core.sql import ESCAPE, like_pattern

#: 取多少条结果。一万张全摆进模型会让整屏重排慢下来，而用户多半只是想看看前几百张。
#: 命中总数由界面另行查出来显示，因此「被截断了」这件事用户看得见。
RESULT_LIMIT: Final = 5000

type SearchKind = Literal["name", "album", "format", "text", "size", "number", "datetime", "event"]

#: 一段区间两端之间的分隔符：**认全角与半角两种**，文档与提示里一律摆全角那一个。判据是日期
#: 语法里根本不会出现波浪号，多认一种写法不产生歧义，少认一种却只是把用户挡在门外；两侧的
#: 空格由 :func:`_period` 各自 ``strip`` 掉。
RANGE_SEPARATOR: Final = "～"

#: 半角那一个。**不是另一种语义，是同一种语义的另一种键盘**（见 :data:`RANGE_SEPARATOR`）。
HALF_WIDTH_RANGE_SEPARATOR: Final = "~"


@dataclass(frozen=True, slots=True)
class SearchField:
    """``#`` 后面能选的一项：它是哪个字段、落到哪一列、怎么匹配。
    :param label: 候选列表里的名字；只有别名与 ``Facet.label`` 不同（「所在文件夹」叫「相册」）。
    :param column: SQL 列（``p.`` = 文件表，``f.`` = facet 表），事件那族要配 :attr:`table` 看。
    :param table: 事件库里的那张表（ADR 0043）；非空表示走 ``EXISTS`` 子查询而不是直接比某一列。
    :param sql_type: 事件列的类型（``TEXT`` / ``DAY`` / ``INTEGER`` / ``REAL``），匹配方式由它定。
    """

    id: str
    label: str
    kind: SearchKind
    column: str
    hint: str = ""
    table: str = ""
    sql_type: str = ""

    @property
    def is_event(self) -> bool:
        return self.table != ""

    @property
    def needs_metadata(self) -> bool:
        """这个字段是不是必须真去读 EXIF 才拿得到：没装 exiftool 时地点与关键词那一族根本读不出来，
        仍然可以出现在候选里，但界面要把这句话说出来（ADR 0026）。

        **事件那一族为假**：它不来自照片，也就不看 exiftool 装没装。
        """
        return self.column.startswith("f.")

    @property
    def is_numeric(self) -> bool:
        """这一列是不是数值 —— **匹配方式由它分派**。

        三个分派处都要问它：:func:`_condition`、:func:`_clause` 与 :func:`_event_clause`。
        三处各写各的判据时，改一处漏一处**不会报错**。事件那一族按**列声明的类型**判（同一个
        ``kind="event"`` 里既有文字也有数值，``kind`` 说不了这件事）。
        """
        if self.is_event:
            return self.sql_type in ("INTEGER", "REAL")
        return self.kind in ("number", "size")

    @property
    def is_date(self) -> bool:
        """时间那一族：解析成区间、渲染成区间比较。

        ``f.captured`` 存的是秒、``p.mtime_ns`` 是纳秒，所以渲染那一侧还要按字段再分一次
        （见 :func:`_date_clause`）。
        """
        return self.kind == "datetime"

    @property
    def stores_event_kind_id(self) -> bool:
        """「事件类型」那一列：库里存的是 id（``trip``）、用户敲的是中文（``旅行``），
        两者要比对得上。
        """
        return self.is_event and self.column == "kind"


def _field(
    facet_id: str,
    label: str,
    kind: SearchKind,
    column: str,
    *,
    hint: str = "",
) -> SearchField:
    return SearchField(id=facet_id, label=label, kind=kind, column=column, hint=hint)


#: 「事件类型」在库里存的是 id（``trip``），而用户敲的是中文名（``旅行``）。两种都认。
EVENT_KIND_VALUES: Final[dict[str, str]] = {
    alias: kind.id for kind in EVENT_KINDS for alias in (kind.id, kind.label)
}


def _event_fields() -> tuple[SearchField, ...]:
    """事件那一族的可搜字段，**由事件类型定义生成**（ADR 0043 决策 6、0046）。

    **每类独有的字段一律带类型前缀**（``旅行·目的地``、``演出·评分``）：候选列表是全局的，要让人
    一眼看出属于哪一类，两类里同名字段也因此分得开；公共那两条（``事件类型`` / ``事件``）不带前缀。
    ``id`` 同样带类型（``event_trip_destination``），否则两类同名拼出的条件会撞成一条。
    """
    common = (
        SearchField(
            id="event_kind",
            label="事件类型",
            kind="event",
            column="kind",
            table=EVENTS_TABLE,
            hint=" / ".join(kind.label for kind in EVENT_KINDS),
        ),
        SearchField(
            id="event",
            label="事件",
            kind="event",
            column="title",
            table=EVENTS_TABLE,
            hint="京都五日",
        ),
    )
    per_kind: list[SearchField] = []
    for kind in EVENT_KINDS:
        for field in kind.fields:
            per_kind.append(
                SearchField(
                    id=f"event_{kind.id}_{field.id}",
                    label=f"{kind.label}·{field.label}",
                    kind="event",
                    column=field.column,
                    table=kind.table,
                    sql_type=field.sql_type,
                    hint=field.hint,
                )
            )
    return (*common, *per_kind)


#: 照片本身的那些字段。事件那一族接在它后面，每类独有的字段各带类型前缀（见
#: :func:`_event_fields`）。
_PHOTO_FIELDS: Final[tuple[SearchField, ...]] = (
    _field(FACET_NAME, "文件名", "name", "p.name"),
    _field(FACET_FOLDER, "相册", "album", "p.folder", hint="含子相册"),
    _field(FACET_FORMAT, "文件格式", "format", "p.suffix", hint="jpg 或 JPEG"),
    _field(FACET_SIZE, "文件大小", "size", "p.size", hint="5MB 或 >10MB"),
    _field(FACET_CAPTURED, "拍摄时间", "datetime", "f.captured", hint="2024 / 2024-05"),
    _field(FACET_CAMERA, "相机", "text", "f.camera"),
    _field(FACET_LENS, "镜头", "text", "f.lens"),
    _field(FACET_COUNTRY, "国家", "text", "f.country"),
    # **标签里不许有空格**：字段名是拿来敲的（「#省/州 浙江」），放回输入框时会按空格切开认不回来。
    _field(FACET_REGION, "省/州", "text", "f.region"),
    _field(FACET_CITY, "城市", "text", "f.city"),
    _field(FACET_KEYWORD, "关键词", "text", "f.keywords"),
    _field(FACET_APERTURE, "光圈", "number", "f.aperture", hint="1.8 或 < 2"),
    _field(FACET_SHUTTER, "快门", "number", "f.shutter", hint="0.002 或 > 0.001"),
    _field(FACET_ISO, "ISO", "number", "f.iso", hint="800 或 >1600"),
    _field(FACET_FOCAL, "焦距", "number", "f.focal", hint="35 或 <= 70"),
    _field(FACET_PIXELS, "像素尺寸", "number", "f.pixels", hint="12000000 或 >12MP"),
    _field(FACET_MODIFIED, "修改时间", "datetime", "p.mtime_ns", hint="2024 / 2024-05"),
)

#: 候选列表的顺序即这里里面的顺序。**不打乱 GROUP_FACET_IDS / SORT_FACET_IDS 的既有顺序**
#: （字段与分组排序看到的是同一套字段），只把「不读 EXIF、立刻出结果」的四个提到最前面。
#: ``column`` 写的是 **SQL 里的列名**而不是 ``PhotoFacets`` 的属性名 —— 只有拍摄时间两者不同
#: （属性 ``captured_at``，列 ``captured``），写错要到 SQLite 执行时才报出来。
SEARCH_FIELDS: Final[tuple[SearchField, ...]] = (
    *_PHOTO_FIELDS,
    *_event_fields(),
)

_FIELDS_BY_ID: Final[dict[str, SearchField]] = {field.id: field for field in SEARCH_FIELDS}

#: 不带 ``#`` 时搜的那个字段（也是候选列表的第一项）。
DEFAULT_FIELD: Final = _FIELDS_BY_ID[FACET_NAME]

_SELECT: Final = (
    "SELECT p.path, p.size, p.mtime_ns FROM photos p LEFT JOIN facets f ON f.path = p.path"
)

_SELECT_COUNT: Final = "SELECT COUNT(*) FROM photos p LEFT JOIN facets f ON f.path = p.path"

#: 「哪些路径命中了」的查询（ADR 0040 决策 2）。**与 :data:`_SELECT` 的差别只有取哪几列** ——
#: 相交用的就是它，所以这一列也要走同一个 ``LEFT JOIN``，于是「命中」这件事只有一处定义。
_SELECT_PATHS: Final = "SELECT p.path FROM photos p LEFT JOIN facets f ON f.path = p.path"


class SearchOutcome(NamedTuple):
    """一次检索的结果。

    :param refs: 命中的照片，**按文件名**排好（界面随后按用户选的排序字段重排）。
    :param total: 命中总数。**它可以大于 ``len(refs)``**（被 :data:`RESULT_LIMIT` 截断，或是索引
        里那些「文件早就被删了」的残留行），所以界面摆的是 ``len(refs)``、说出口的是 ``total``。
    """

    refs: list[PhotoRef]
    total: int


@dataclass(frozen=True, slots=True)
class ParsedInput:
    """搜索框里那一串东西解析之后的样子。

    :param selecting: 用户还在**挑字段**（敲了 ``#`` 还没确认）。此时回车是「补全这个字段」，
        不是「提交一条条件」；``field`` 为 ``None``（列表全开），``value`` 是字段后面那段的原文。
    """

    selecting: bool
    field: SearchField | None
    value: str


@dataclass(frozen=True, slots=True)
class SearchCondition:
    """一条已确认的筛选条件。``text`` 是用户敲的原文（胶囊照原样显示），``value`` 是解析之后真正
    拿去比的那个值 ——「2.8」与「< 2」在胶囊上长得不一样，在 SQL 里也不同。
    """

    field: SearchField
    text: str
    comparator: str
    value: str

    @property
    def label(self) -> str:
        return self.field.label

    @property
    def chip_text(self) -> str:
        """面包屑上那个胶囊写什么。"""
        return f"{self.field.label}：{self.text}"

    @property
    def as_input(self) -> str:
        """能把它还原成搜索框里那一串的样子（``#相机 Canon``）。

        **要带上字段前缀**：只把值放回去的话，用户看到的是「旅行」，不知道这一条按哪个字段筛 ——
        而改条件多半正是要换字段，那是这个交互的主要用法。默认字段（文件名）不带前缀。
        """
        if self.field.id == DEFAULT_FIELD.id:
            return self.text
        return f"#{self.field.label} {self.text}"

    @property
    def identity(self) -> tuple[str, str]:
        """这一层标识里用的那份身份（字段 + 原文，见 ``ui.navigation.level_key``）。"""
        return (self.field.id, self.text)


# ---- 解析输入 ---------------------------------------------------------------------


def parse_input(raw: str) -> ParsedInput | None:
    """搜索框里那一串 → 该补全还是该提交（``None`` 表示什么都不用做）。

    **认不出字段名时不报错**，退回按文件名搜：``#海边`` 多半是想找一张名字带「海边」的照片，
    而「这个输入不合法」对用户说不出任何他没打算做的事。
    """
    text = raw.strip()
    if not text:
        return None
    if not text.startswith("#"):
        return ParsedInput(selecting=False, field=DEFAULT_FIELD, value=text)
    body = text[1:].strip()
    if not body:
        return ParsedInput(selecting=True, field=None, value="")
    head, separator, tail = body.partition(" ")
    field = match_field(head)
    if field is None:
        return ParsedInput(selecting=False, field=DEFAULT_FIELD, value=body)
    value = tail.strip()
    # 没有空格（还在挑字段），或者挑完了但值还空着：都是「回车 = 补全」。
    return ParsedInput(selecting=not separator or not value, field=field, value=value)


def match_field(text: str) -> SearchField | None:
    """候选列表里匹配一个字段名：id（``#camera``）与中文名（``#相机``）都认。比较时**去掉空格**，
    于是「省/州」与「省 / 州」是同一个。
    """
    needle = _compact(text)
    if not needle:
        return None
    for field in SEARCH_FIELDS:
        if needle in (_compact(field.id), _compact(field.label)):
            return field
    return None


def suggestions(query: str) -> list[SearchField]:
    """候选列表：该给哪些字段（``query`` 是 ``#`` 后面已经敲的那几个字，空 = 全给）。

    顺序即 :data:`SEARCH_FIELDS` 的顺序 —— 命中就在原位，不做重排：
    「相册」在第二位，它就该在第二位。
    """
    needle = _compact(query)
    if not needle:
        return list(SEARCH_FIELDS)
    return [
        field
        for field in SEARCH_FIELDS
        if needle in _compact(field.label) or needle in _compact(field.id)
    ]


def partial_field(text: str) -> str:
    """``#`` 后面正在敲的那一段字段名（没带 ``#`` 就是空串）。

    界面用它过滤候选列表。**不要拿解析结果的 ``value`` 当查询串**：那一位是「字段后面那个值」，
    用户还在挑字段时它必然是空的，于是列表会列出全部字段 —— 敲了「相机」却看不见它被选中。
    """
    if not text.startswith("#"):
        return ""
    head, separator, _tail = text[1:].partition(" ")
    return "" if separator else head


def make_condition(parsed: ParsedInput) -> SearchCondition | None:
    """把一次解析变成一条可提交的条件；不该提交（没输入 / 还在挑字段 / 数字写坏了）就是 ``None``。

    **收 ``None`` 进去**：界面那一侧最自然的写法就是 ``make_condition(parse_input(raw))``，
    而空输入框按回车正是 ``None`` —— 在这里挡住，调用方就不必为最常见的输入单开一个分支。
    """
    if parsed is None or parsed.selecting or parsed.field is None:
        return None
    return _condition(parsed.field, parsed.value)


def unusable_reason(field: SearchField, value: str) -> str:
    """这句话为什么变不成一条条件 —— 界面横幅照它说一句中文（不套「要一个值」那种话）。

    :func:`make_condition` 返回 ``None`` 时界面唯一能说的话就是这一句，所以**说得具体是这里
    唯一的指望**：``2024～2023`` 与 ``2024-13`` 要改的地方完全不一样，而用户看得见的只有横幅。
    """
    if field.kind == "datetime":
        pieces = _split_range(value)
        if len(pieces) == 2 and _is_reversed(*pieces):
            left, right = (piece.strip() for piece in pieces)
            return f"的区间写反了：「{left}」在「{right}」后面，起始要写在前面。"
        return (
            "要一个日期：2024、2024-05、2024-05-16；"
            f"要一段区间就写成 2023{RANGE_SEPARATOR}2024"
            f"（{RANGE_SEPARATOR} 前后可以有空格，{HALF_WIDTH_RANGE_SEPARATOR} 也认）。"
        )
    return f"这一栏要一个值，例如 {field.hint or '关键词'}。"


def _condition(field: SearchField, text: str) -> SearchCondition | None:
    """``#字段 值`` → 一条条件；解析不出来返回 ``None``（界面照 :func:`unusable_reason` 说话）。

    **每一种 ``kind`` 都在这里有名字**，没有回退那一条：原先时间那一族是 ``if`` 链末尾的
    fallthrough，于是新加一种 kind 却忘了在这里写规则时，它会**被当成日期解析** ——
    值照样解析得出来（``2024`` 那种），只是含义完全不对。
    """
    value = text.strip()
    if not value:
        return None
    if field.is_event:
        # 匹配方式由那一列声明的类型说了算（与照片那一族同一件事，判据见 :attr:`SearchField.
        # is_numeric`）：文字、多值都是子串匹配，数值才是比较式。
        if field.is_numeric:
            parsed_number = _parse_number(value)
            return None if parsed_number is None else SearchCondition(field, text, *parsed_number)
        return SearchCondition(field=field, text=text, comparator="", value=value)
    if field.kind in ("name", "text"):
        return SearchCondition(field=field, text=text, comparator="", value=value)
    if field.kind == "album":
        # 相册写成 "旅行/2024" 也认，但两边多余的斜杠不参与匹配。
        cleaned = value.replace("\\", "/").strip("/")
        if not cleaned:
            return None
        return SearchCondition(field=field, text=cleaned, comparator="", value=cleaned)
    if field.kind == "format":
        suffix = normalize_suffix(value)
        if suffix is None:
            return None
        return SearchCondition(field=field, text=value, comparator="", value=suffix)
    if field.kind == "size":
        parsed = _parse_size(value)
    elif field.kind == "number":
        parsed = _parse_number(value)
    elif field.is_date:
        parsed = _parse_datetime(value)
    else:
        raise AssertionError(f"没给这个搜索字段定义解析规则：{field.id}（kind={field.kind}）")
    return None if parsed is None else SearchCondition(field, text, parsed[0], parsed[1])


# ---- 数值 / 日期 -------------------------------------------------------------------


_COMPARATOR = re.compile(r"^(>=|<=|>|<|=)?\s*(.+)$")

_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")

#: 大小可以带单位；不带就是字节。``MB`` 按 10 的幂算（厂商口径），与访达显示的一致。
_SIZE_UNITS: Final[dict[str, float]] = {
    "": 1.0,
    "b": 1.0,
    "k": 1e3,
    "kb": 1e3,
    "m": 1e6,
    "mb": 1e6,
    "g": 1e9,
    "gb": 1e9,
    "mp": 1e6,  # 「1200 万像素」那种写法，落到像素数上
}

_DATE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")


def _parse_number(text: str) -> tuple[str, str] | None:
    """``>800`` → ``(">", "800")``；不是数字就是 ``None``（界面据此说「这一栏要数字」）。"""
    comparator, body = _split_comparator(text)
    body = body.strip()
    if not _NUMBER.match(body):
        return None
    return comparator, body


def _parse_size(text: str) -> tuple[str, str] | None:
    """``>10MB`` → ``(">", "10000000")``。单位不认识就是 ``None``。"""
    comparator, body = _split_comparator(text)
    body = body.strip().casefold().replace(" ", "")
    unit = ""
    for suffix in sorted(_SIZE_UNITS, key=len, reverse=True):
        if suffix and body.endswith(suffix):
            unit = suffix
            body = body[: -len(suffix)]
            break
    if not _NUMBER.match(body) or unit not in _SIZE_UNITS:
        return None
    return comparator, _number_text(float(body) * _SIZE_UNITS[unit])


def _parse_datetime(text: str) -> tuple[str, str] | None:
    """``2024`` / ``2024-05`` / ``2024-05-16`` / ``2023～2024`` → 那一段的时间区间。

    **不带比较符时取「那一段之内」**：``#拍摄时间 2024`` 是「2024 年拍的」，不是「大于 2024」；
    ``2023～2024`` 两端各含自己那一整段；全角半角都认（见 :data:`RANGE_SEPARATOR`）。
    **区间里不认比较符**（``>2023～2024`` 提交不了）：一句话里两个比较符说的是两件事。
    """
    pieces = _split_range(text)
    if len(pieces) > 2:
        return None
    if len(pieces) == 2:
        # 「什么算写反了」只有 :func:`_is_reversed` 一处定义 —— 界面要靠它说那句话，
        # 这里要靠它判定是否写反；两边各判一次，迟早判成两样。
        if _is_reversed(*pieces):
            return None
        low, high = _period(pieces[0]), _period(pieces[1])
        if low is None or high is None:  # pragma: no cover - _is_reversed 已经排除过
            return None
        return "between", f"{low[0].timestamp()}|{high[1].timestamp()}"
    comparator, body = _split_comparator(text)
    period = _period(body)
    if period is None:
        return None
    start, stop = period
    if comparator in ("", "="):
        return "between", f"{start.timestamp()}|{stop.timestamp()}"
    if comparator == ">":
        return ">", _number_text(stop.timestamp())
    if comparator == ">=":
        return ">=", _number_text(start.timestamp())
    if comparator == "<":
        return "<", _number_text(start.timestamp())
    return "<=", _number_text(stop.timestamp())


def _split_range(text: str) -> list[str]:
    """按区间分隔符切成两端；没有分隔符就是一个元素（那就是「裸值」那条路）。

    **全角与半角先归一**：``2023~2024`` 与 ``2023～2024`` 是同一个意思，而切分这件事有两个
    调用方（解析条件、给横幅找理由）—— 各切各的，迟早一边认一种写法。
    """
    return text.replace(HALF_WIDTH_RANGE_SEPARATOR, RANGE_SEPARATOR).split(RANGE_SEPARATOR)


def _period(text: str) -> tuple[datetime.datetime, datetime.datetime] | None:
    """一段日期 → 它那一段的起止（左闭右开）。写不成一个日期就是 ``None``。

    **只写了哪几级，段就有多长**：``2024`` 一整年、``2024-05`` 一整月、``2024-05-16`` 就是那一天。
    **不存在的日子返回 ``None`` 而不是抛**：``2023-02-30`` 真去造 ``datetime`` 才炸，而炸出去的地方
    是界面上的回车，用户只看到「按了回车什么也没发生」。
    """
    match = _DATE.match(text.strip())
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    try:
        start = datetime.datetime(year, month or 1, day or 1)  # noqa: DTZ001 - 按本机时间
    except ValueError:
        return None
    if day is not None:
        stop = start + datetime.timedelta(days=1)
    elif month is not None:
        stop = _add_months(start, 1)
    else:
        stop = _add_months(start, 12)
    return start, stop


def _is_reversed(left: str, right: str) -> bool:
    """区间两端是不是写反了（写不成日期的那一种不算「反了」，那是另一种毛病）。判据只有这一处 ——
    界面与解析各判一次，迟早会把「什么算反了」判成两样（见 :func:`unusable_reason`）。
    """
    low, high = _period(left), _period(right)
    return low is not None and high is not None and low[0] >= high[0]


def _split_comparator(text: str) -> tuple[str, str]:
    match = _COMPARATOR.match(text.strip())
    if match is None:  # pragma: no cover - 正则保证一定匹配
        return "", text.strip()
    return (match.group(1) or ""), match.group(2)


def _add_months(moment: datetime.datetime, months: int) -> datetime.datetime:
    """加若干个月（同一天存在时就是那一天，月末溢出会落到下个月 —— 那一档没人在意）。"""
    month = moment.month - 1 + months
    return datetime.datetime(  # noqa: DTZ001
        moment.year + month // 12, month % 12 + 1, moment.day
    )


def _number_text(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else repr(number)


def _compact(text: str) -> str:
    """比较字段名时去掉所有空白，于是「省/州」与「省 / 州」等价。"""
    return "".join(text.split()).casefold()


# ---- 生成 SQL --------------------------------------------------------------------


def _where(conditions: Sequence[SearchCondition]) -> tuple[str, list[object]]:
    """这些条件 → ``WHERE`` 那一段与它的绑定值。

    **三个查询共用它**（``build_query`` / ``build_count_query`` / ``build_match_query``），
    于是「命中」这件事只有一处 —— 整库检索与当前层筛选（ADR 0040 决策 2）不可能给出不一样的答案。
    """
    clauses: list[str] = []
    values: list[object] = []
    for condition in conditions:
        clause, bound = _clause(condition)
        clauses.append(f"({clause})")
        values.extend(bound)
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), values


def build_query(
    conditions: Sequence[SearchCondition], *, limit: int = RESULT_LIMIT
) -> tuple[str, list[object]]:
    """这些条件 → 一条 SQL 与它的绑定值。

    **条件一律 ``AND``**（ADR 0026 决策 1），每提交一条就再收窄一层。排序留给界面，这里只保证
    同一批结果的先后稳定，于是 ``PhotoRef`` 造出来时是确定的。
    """
    where, values = _where(conditions)
    sql = _SELECT + where
    sql += f" ORDER BY p.name LIMIT {max(1, int(limit))}"
    return sql, values


def build_count_query(conditions: Sequence[SearchCondition]) -> tuple[str, list[object]]:
    """同样这些条件的**命中总数**，与 :func:`build_query` 共用同一个 ``WHERE``，两处不可能算出不一样
    的条件。界面拿它说清「共 12345 张，只显示前 5000 张」—— 被截断了这件事用户必须看得见。
    """
    where, values = _where(conditions)
    return _SELECT_COUNT + where, values


def build_match_query(conditions: Sequence[SearchCondition]) -> tuple[str, list[object]]:
    """这些条件命中了**哪些路径** —— 当前层筛选（ADR 0040 决策 2）要的那一份。

    与 :func:`build_query` 差两点，**都别去掉**：**只取路径**（求交只需一个字符串）；**既不排序也
    不截断**（:data:`RESULT_LIMIT` 在这里等于静默丢命中，会表现成「一条都没有」而不是答案）。
    """
    where, values = _where(conditions)
    return _SELECT_PATHS + where, values


def albums_kept(
    albums: Sequence[FolderRef], matching: Collection[Path], *, root: Path
) -> list[FolderRef]:
    """相册模式下留下哪些卡片：**子树里有命中的那些**（ADR 0040 决策 3）。

    一张都不命中照片的子相册不该摆出来（摆着空相册等于骗用户点进去）；命中的照片在不在卡片自己
    这一层不算，卡片的含义是它的整棵子树。一次遍历按「相对 ``root`` 的第一段」分桶，代价 O(命中数)
    而不是 O(卡片 × 命中数)。**只在这一层直接列出来的卡片里判**，``root`` 之外的一律留。
    """
    if not albums:
        return []
    # **卡片的位置用它自己，命中照片的位置用它的父目录** —— ``relative_folder`` 收的是「照片所在
    # 文件夹」，拿它去算一张卡片会把卡片自己的名字也去掉（``旅行/2024`` 算成 ``旅行``）。
    prefixes = tuple(_directory_prefix(album.path, root) for album in albums)
    hit: set[str] = set()
    for path in matching:
        relative = relative_folder(path, root)
        for prefix in prefixes:
            if relative == prefix or relative.startswith(prefix + "/"):
                hit.add(prefix)
                break
    return [album for album, prefix in zip(albums, prefixes, strict=True) if prefix in hit]


def _directory_prefix(path: Path, root: Path) -> str:
    """一个**目录**相对库根的位置（posix 写法）；就在库根下时是空串，不在库内时是绝对路径。

    **空串在这里只有一个含义**：这个目录就是库根本身。那种卡片不该由这一层列出来（列的是子相册），
    所以它匹配不到任何命中 —— 宁可让它消失，也不要让它变成一张「什么都命中」的通配卡片。
    """
    # ``resolve=False``：这一处的 ``root`` 与 ``album.path`` 都已经是库内解析过的路径
    # （库根由 ``Library.open`` 解析，卡片由扫描器给出），再解析一趟只是白花。
    relative = paths.relative_posix(root, path, resolve=False)
    return str(path) if relative is None else relative


def _clause(condition: SearchCondition) -> tuple[str, list[object]]:
    """一条条件 → ``(SQL 片段, 绑定值)``。**匹配方式逐字段定死**（ADR 0026 决策 6），唯一一份。

    文件名与文本一族走 ``LIKE '%q%'``；相册是 ``= q`` 或 ``LIKE 'q/%'``（含子相册）；文件格式是
    ``= '.jpg'``；大小与数值是比较式（等于是空比较符）；时间是区间（``f.captured`` 是秒，
    ``p.mtime_ns`` 是纳秒）。
    """
    field = condition.field
    value = condition.value
    if field.is_event:
        return _event_clause(field, condition)
    if field.kind in ("name", "text"):
        # 文件名与文本一族用同一条规则 —— 原先写成两支逐字相同的分支。
        return f"{field.column} LIKE ? ESCAPE '{ESCAPE}'", [f"%{like_pattern(value)}%"]
    if field.kind == "album":
        return (
            f"({field.column} = ? OR {field.column} LIKE ? ESCAPE '{ESCAPE}')",
            [value, like_pattern(value, "/%")],
        )
    if field.kind == "format":
        return f"{field.column} = ?", [value]
    if field.is_numeric:
        return f"{field.column} {condition.comparator or '='} ?", [float(value)]
    if field.is_date:
        return _date_clause(field, condition)
    raise AssertionError(f"没给这个搜索字段定义匹配方式：{field.id}（kind={field.kind}）")


def _date_clause(field: SearchField, condition: SearchCondition) -> tuple[str, list[object]]:
    """时间那一族的 SQL。区间比较时 ``value`` 是「起|止」两个 epoch 秒。

    ``f.captured`` 存的是秒，而 ``p.mtime_ns`` 是**纳秒**（按文件的修改时间排时用后者），
    所以用到它时要把两头都乘上去 —— 区间与单值两处都要，漏一处就会拿秒去比纳秒。
    """
    value = condition.value
    if condition.comparator == "between":
        low, _, high = value.partition("|")
        if field.id == FACET_MODIFIED:
            low, high = str(float(low) * 1e9), str(float(high) * 1e9)
        return f"{field.column} >= ? AND {field.column} < ?", [float(low), float(high)]
    if field.id == FACET_MODIFIED:
        return f"{field.column} {condition.comparator} ?", [float(value) * 1e9]
    return f"{field.column} {condition.comparator} ?", [float(value)]


def _join_column(table: str) -> str:
    """事件那张表的主键叫什么：公共表是 ``id``，每类事件的扩展表是 ``event_id``。

    两张表都拿同一个小写别名 ``t``，于是子查询只在这一处区分 —— 别处各写一遍就会有一个地方
    拼错，而错的那个要等 SQLite 执行时才报 ``no such column: t.event_id``。
    """
    return "id" if table == EVENTS_TABLE else "event_id"


def _event_clause(field: SearchField, condition: SearchCondition) -> tuple[str, list[object]]:
    """事件条件 → ``EXISTS`` 子查询（ADR 0043 决策 6）。

    **走 EXISTS 而不是 ``JOIN``**：一张照片可属于几个事件，``JOIN`` 会让它在结果里重复出现，
    而我们要的只是「它是不是属于某个满足条件的事件」。事件库挂在索引那条连接上。
    """
    value = condition.value
    head = (
        f"SELECT 1 FROM {SCHEMA_NAME}.{BINDINGS_TABLE} pe"
        f" JOIN {SCHEMA_NAME}.{field.table} t ON t.{_join_column(field.table)} = pe.event_id"
        f" WHERE pe.path = p.path AND "
    )
    if field.stores_event_kind_id:
        return f"EXISTS ({head} t.kind = ?)", [EVENT_KIND_VALUES.get(value, value)]
    if field.is_numeric:
        return f"EXISTS ({head} t.{field.column} {condition.comparator or '='} ?)", [float(value)]
    return (
        f"EXISTS ({head} t.{field.column} LIKE ? ESCAPE '{ESCAPE}')",
        [f"%{like_pattern(value)}%"],
    )


def rows_to_refs(rows: Sequence[tuple[object, ...]]) -> list[PhotoRef]:
    """``(路径, 大小, 修改时间)`` 三元组 → ``PhotoRef``。**大小与修改时间直接从索引里带出来**，不为
    每条结果再 ``stat()`` 一次 —— 一万条结果就是一万次系统调用。
    """
    refs: list[PhotoRef] = []
    for row in rows:
        path, size, mtime_ns = row[0], row[1], row[2]
        if not isinstance(path, str) or not isinstance(size, int) or not isinstance(mtime_ns, int):
            continue
        refs.append(PhotoRef(path=Path(path), size=size, mtime_ns=mtime_ns))
    return refs


def relative_folder_path(root: Path, path: Path) -> str:
    """照片所在相册相对库根的位置。直接转给 :func:`gallery.core.metadata.facets.relative_folder`
    —— 索引里存的就是那一个函数算出来的字符串，两处各写一份迟早对不上。
    """
    return relative_folder(path, root)


__all__ = [
    "DEFAULT_FIELD",
    "HALF_WIDTH_RANGE_SEPARATOR",
    "RANGE_SEPARATOR",
    "RESULT_LIMIT",
    "SEARCH_FIELDS",
    "ParsedInput",
    "SearchCondition",
    "SearchField",
    "SearchKind",
    "SearchOutcome",
    "albums_kept",
    "build_count_query",
    "build_match_query",
    "build_query",
    "make_condition",
    "match_field",
    "parse_input",
    "partial_field",
    "relative_folder_path",
    "rows_to_refs",
    "suggestions",
    "unusable_reason",
]
