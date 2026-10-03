"""搜索：条件长什么样、落到哪一列、整句 SQL 长什么样（纯函数）。

三块，各有各的理由：

- **字段表** :data:`SEARCH_FIELDS` —— ``#`` 后面能选的那些项。**字段 id 与
  :mod:`gallery.core.metadata.facets` 用的是同一套**（相册就是 ``FACET_FOLDER``、
  文件名就是 ``FACET_NAME``），不另立一套：两套字段表迟早各自漂，而漂了之后
  「界面上写的那个相机」与「查的那个相机」就对不上了。
- **条件模型** :class:`SearchCondition` —— 界面把它摆成面包屑右侧的胶囊，
  core 把它翻成 SQL。两者之间只有这一个数据结构。
- **SQL** :func:`build_query` —— **只在这里生成**。于是「哪个字段落到哪一列、
  怎么匹配」这件事在代码里是可 grep 的一段，而不是散在各个 ``if`` 分支里
  （ADR 0026 决策 6）。

不碰文件、不碰 Qt、不碰 Qt 的事件循环，因此整份都可以直接单测。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NamedTuple

from gallery.core.library.photos import PhotoRef, normalize_suffix
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

#: 取多少条结果。一万张全摆进模型会让整屏重排慢下来，而用户多半只是想看看前几百张。
#: 命中总数由界面另行查出来显示，因此「被截断了」这件事用户看得见。
RESULT_LIMIT: Final = 5000

#: ``LIKE`` 里的通配符。用户搜 ``IMG_001`` 时 ``_`` 不该去顶替任何字符，
#: 否则会多出一堆「IMGX001」那样的照片。转义了就必须同时写 ``ESCAPE``。
_LIKE_ESCAPE: Final = "\\"

type SearchKind = Literal["name", "album", "format", "text", "size", "number", "datetime"]

#: 条件的比较方式。空串是「等于」，于是相等那一档不必另写一份 SQL。
COMPARATORS: Final = ("", "=", ">", ">=", "<", "<=")

#: 一段区间两端之间的分隔符：``2023～2024``。
#:
#: **全角，而且只认这一个**。日期本身就带半角连字符（``2023-05``），多认几种分隔符只会
#: 让「为什么这条没生效」更难猜 —— 用户敲了半角 ``~`` 却没生效时，没人会去猜是全角的问题。
#: 两侧的空格由 :func:`_period` 各自 ``strip`` 掉，于是 ``2023 ～ 2024`` 一样认。
RANGE_SEPARATOR: Final = "～"


@dataclass(frozen=True, slots=True)
class SearchField:
    """``#`` 后面能选的一项：它是哪个字段、落到哪一列、怎么匹配。

    :param id: 与 :mod:`gallery.core.metadata.facets` 同一个 id 空间。
    :param label: 候选列表里显示的名字。**只有别名会与 ``Facet.label`` 不同**
        （「所在文件夹」在搜索里叫「相册」）。
    :param kind: 怎么匹配 —— 决定用子串、前缀还是范围。
    :param column: SQL 里的列（``p.`` = 文件表，``f.`` = facet 表）。
    :param hint: 候选右侧那句灰字，告诉用户这里能写什么。
    """

    id: str
    label: str
    kind: SearchKind
    column: str
    hint: str = ""

    @property
    def needs_metadata(self) -> bool:
        """这个字段是不是必须真去读 EXIF 才拿得到。

        没装 exiftool 时（Pillow 降级路径），地点与关键词那一族根本读不出来 ——
        仍然让它们出现在候选里可以，但界面要先把这句话说出来（见 ADR 0026）。
        """
        return self.column.startswith("f.")


def _field(
    facet_id: str,
    label: str,
    kind: SearchKind,
    column: str,
    *,
    hint: str = "",
) -> SearchField:
    return SearchField(id=facet_id, label=label, kind=kind, column=column, hint=hint)


#: 候选列表的顺序即这里面的顺序。**不打乱 ``GROUP_FACET_IDS`` / ``SORT_FACET_IDS``
#: 的既有顺序**（字段与分组排序看到的是同一套字段，不该在这里另排一遍），
#: 只把「不读 EXIF、立刻出结果」的四个提到最前面：文件名、相册、文件格式、文件大小。
#:
#: ``column`` 写的是 **SQL 里的列名**，不是 ``PhotoFacets`` 上的属性名 —— 两者只在
#: 拍摄时间这一项上不一样（属性叫 ``captured_at``，列叫 ``captured``）。写错的后果
#: **不会在解析与生成 SQL 时露出来**：SQLite 直到执行才说 ``no such column``，而那条
#: 错误被 :meth:`FacetIndex.search` 包成了「检索没能完成」—— 界面上表现为「一条都搜不到」。
#: ``tests/core/library/test_search.py`` 里那条列名守护就是为这件事准备的。
SEARCH_FIELDS: Final[tuple[SearchField, ...]] = (
    _field(FACET_NAME, "文件名", "name", "p.name"),
    _field(FACET_FOLDER, "相册", "album", "p.folder", hint="含子相册"),
    _field(FACET_FORMAT, "文件格式", "format", "p.suffix", hint="jpg 或 JPEG"),
    _field(FACET_SIZE, "文件大小", "size", "p.size", hint="5MB 或 >10MB"),
    _field(FACET_CAPTURED, "拍摄时间", "datetime", "f.captured", hint="2024 / 2024-05"),
    _field(FACET_CAMERA, "相机", "text", "f.camera"),
    _field(FACET_LENS, "镜头", "text", "f.lens"),
    _field(FACET_COUNTRY, "国家", "text", "f.country"),
    # **标签里不许有空格**：字段名是拿来敲的（「#省/州 浙江」），而搜索框把 ``#`` 与
    # 之间的那一段当字段名 —— 名字里带空格的话，放回输入框时会按空格切开，认不回来。
    # 分组与元数据面板那边仍写「省 / 州」，那是给人看的文案，不是拿来敲的。
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

_FIELDS_BY_ID: Final[dict[str, SearchField]] = {field.id: field for field in SEARCH_FIELDS}

#: 不带 ``#`` 时搜的那个字段（也是候选列表的第一项）。
DEFAULT_FIELD: Final = _FIELDS_BY_ID[FACET_NAME]

_SELECT: Final = (
    "SELECT p.path, p.size, p.mtime_ns FROM photos p LEFT JOIN facets f ON f.path = p.path"
)

_SELECT_COUNT: Final = "SELECT COUNT(*) FROM photos p LEFT JOIN facets f ON f.path = p.path"


class SearchOutcome(NamedTuple):
    """一次检索的结果。

    :param refs: 命中的照片，**按文件名**排好（界面随后按用户选的排序字段重排）。
    :param total: 命中总数。**它可以大于 ``len(refs)``**（结果被
        :data:`RESULT_LIMIT` 截断），也可能是 :data:`library.index` 库里那些
        「文件早就被删了」的残留行 —— 所以界面摆的是 ``len(refs)``，说出口的是 ``total``。
    """

    refs: list[PhotoRef]
    total: int


@dataclass(frozen=True, slots=True)
class ParsedInput:
    """搜索框里那一串东西解析之后的样子。

    :param selecting: 用户还在**挑字段**（敲了 ``#`` 还没确认）。此时回车是
        「补全这个字段」，不是「提交一条条件」。
    :param field: 已经定下来的字段；还在挑时是 ``None``（列表全开）。
    :param value: 字段后面那一段原文，还没敲就是空串。
    """

    selecting: bool
    field: SearchField | None
    value: str


@dataclass(frozen=True, slots=True)
class SearchCondition:
    """一条已确认的筛选条件。

    ``text`` 是用户敲的原文（面包屑上的胶囊照原样显示），``value`` 是解析之后
    真正拿去比的那个值 ——「2.8」与「< 2」在胶囊上长得不一样，在 SQL 里也不同。
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
        """能把它还原成搜索框里那一串的样子（``#相机 Canon`` / ``海边``）。

        **要带上字段前缀**：只把值放回去的话，用户在输入框里看到的是「旅行」——
        不知道这一条是按哪个字段筛的，而改条件多半正是要换字段（那是这个交互的主要用法）。
        默认字段（文件名）不带前缀，与它当初是怎么敲进来的一致。
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
    """搜索框里那一串 → 该补全还是该提交。

    ``None`` 表示「什么都不用做」（空串，或只有 ``#`` 后面一个字段名还没落到值上）。

    **认不出字段名时不报错**，退回按文件名搜：``#海边`` 多半是用户想找一张名字带
    「海边」的照片，而「这个输入不合法，请重选字段」对他说不出任何他没打算做的事。
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
    """候选列表里匹配一个字段名：id（``#camera``）与中文名（``#相机``）都认。

    比较时**去掉空格**，于是「省/州」与「省 / 州」（面板那边的写法）是同一个。
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
    """``#`` 后面正在敲的那一段字段名（没敲空格的那一段；没带 ``#`` 就是空串）。

    界面用它过滤候选列表。**不要拿解析结果的 ``value`` 当查询串**：那一位是
    「字段后面那个值」，用户还在挑字段时它必然是空的，于是列表会列出全部字段 ——
    敲了「相机」两个字却看不见「相机」被选中。
    """
    if not text.startswith("#"):
        return ""
    head, separator, _tail = text[1:].partition(" ")
    return "" if separator else head


def make_condition(parsed: ParsedInput) -> SearchCondition | None:
    """把一次解析变成一条可提交的条件；不该提交（还没输入 / 还在挑字段 / 数字写坏了）
    就是 ``None``。

    **收 ``None`` 进去**是因为界面那一侧最自然的写法就是「解析完直接交给它」
    （``make_condition(parse_input(raw))``），而空输入框按回车正是 ``None`` ——
    在这里挡住，调用方就不必为这一种最常见的输入单开一个分支。
    """
    if parsed is None or parsed.selecting or parsed.field is None:
        return None
    return _condition(parsed.field, parsed.value)


def unusable_reason(field: SearchField, value: str) -> str:
    """这句话为什么变不成一条条件 —— 界面横幅照它说一句中文（不套「要一个值」那种话）。

    :func:`make_condition` 返回 ``None`` 时界面唯一能说的话就是这一句，所以**说得具体
    是这里唯一的指望**：``2024～2023`` 与 ``2024-13`` 要改的地方完全不一样，而用户看得见
    的只有横幅。日期这一族因此说得比别的字段细，其余各栏用一句通用的话。

    **判据与 :func:`_is_reversed` 共用同一处**（「什么算写反了」不该有两份定义）。
    """
    if field.kind == "datetime":
        pieces = value.split(RANGE_SEPARATOR)
        if len(pieces) == 2 and _is_reversed(*pieces):
            left, right = (piece.strip() for piece in pieces)
            return f"的区间写反了：「{left}」在「{right}」后面，起始要写在前面。"
        return (
            "要一个日期：2024、2024-05、2024-05-16；"
            f"要一段区间就写成 2023{RANGE_SEPARATOR}2024（{RANGE_SEPARATOR} 前后可以有空格）。"
        )
    return f"这一栏要一个值，例如 {field.hint or '关键词'}。"


def _condition(field: SearchField, text: str) -> SearchCondition | None:
    value = text.strip()
    if not value:
        return None
    if field.kind == "name" or field.kind == "text":
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
        parsed_size = _parse_size(value)
        if parsed_size is None:
            return None
        return SearchCondition(field, text, parsed_size[0], parsed_size[1])
    if field.kind == "number":
        parsed_number = _parse_number(value)
        if parsed_number is None:
            return None
        return SearchCondition(field, text, parsed_number[0], parsed_number[1])
    parsed_dates = _parse_datetime(value)
    if parsed_dates is None:
        return None
    return SearchCondition(field, text, parsed_dates[0], parsed_dates[1])


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

    **不带比较符时取的是「那一段之内」**：``#拍摄时间 2024`` 是「2024 年拍的」，
    而不是「大于 2024」。带 ``>`` / ``<`` 时才取它的一侧。

    ``2023～2024`` 是**两端各含自己那一整段**：左边从 2023 年元旦起，右边到 2024 年
    12 月 31 日为止 —— 与裸值是同一套语义，于是 ``2023～2024`` 就是「2023 与 2024 两年」，
    而 ``2023-05 ～ 2023-08`` 是那三个月。``～`` 前后的空格无所谓。

    **区间里不认比较符**（``>2023～2024`` 提交不了）：要单边就用 ``>`` / ``<``，
    一句话里塞两个比较符说的是两件不同的事。
    """
    pieces = text.split(RANGE_SEPARATOR)
    if len(pieces) > 2:
        return None
    if len(pieces) == 2:
        # 「什么算写反了」只有 :func:`_is_reversed` 一处定义 —— 界面要靠它说那句话，
        # 这里要靠它收下条件；两边各判一次，迟早判成两样。
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


def _period(text: str) -> tuple[datetime.datetime, datetime.datetime] | None:
    """一段日期 → 它那一段的起止（左闭右开）。写不成一个日期就是 ``None``。

    **只写了哪几级，段就有多长**：``2024`` 是一整年、``2024-05`` 是一整个月、
    ``2024-05-16`` 是那一天 —— 于是「那一段之内」这句话有确定的落点。

    **不存在的日子返回 ``None`` 而不是抛**：``2023-02-30``（或平年里的 2 月 29 日）
    光看「1 <= 日 <= 31」是过得去的，真去造 ``datetime`` 才会炸，而炸出去的地方是
    界面上的回车 —— 那时候用户只看到「按了回车什么也没发生」。
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
    """区间两端是不是写反了（写不成日期的那一种不算「反了」，那是另一种毛病）。

    界面要靠它说一句人话（见 :func:`unusable_reason`），而判据只有这一处 ——
    两边各判一次，迟早会把「什么算反了」判成两样。
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


def build_query(
    conditions: Sequence[SearchCondition], *, limit: int = RESULT_LIMIT
) -> tuple[str, list[object]]:
    """这些条件 → 一条 SQL 与它的绑定值。

    **条件一律 ``AND``**（ADR 0026 决策 1）：每提交一条就再收窄一层。
    排序留给界面（``library.groups.sort_photos`` 按用户选的字段排），
    这里只保证同一批结果的先后稳定，于是 ``PhotoRef`` 造出来时是确定的。
    """
    clauses: list[str] = []
    values: list[object] = []
    for condition in conditions:
        clause, bound = _clause(condition)
        clauses.append(f"({clause})")
        values.extend(bound)
    sql = _SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY p.name LIMIT {max(1, int(limit))}"
    return sql, values


def build_count_query(conditions: Sequence[SearchCondition]) -> tuple[str, list[object]]:
    """同样这些条件的**命中总数**。

    与 :func:`build_query` 共用同一个 ``WHERE``，于是两处不可能算出不一样的条件。
    界面拿它说清「共 12345 张，只显示前 5000 张」—— 被截断了这件事用户必须看得见。
    """
    clauses: list[str] = []
    values: list[object] = []
    for condition in conditions:
        clause, bound = _clause(condition)
        clauses.append(f"({clause})")
        values.extend(bound)
    sql = _SELECT_COUNT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql, values


def _clause(condition: SearchCondition) -> tuple[str, list[object]]:
    """一条条件 → ``(SQL 片段, 绑定值)``。

    **匹配方式逐字段定死**（ADR 0026 决策 6），且这里就是唯一一份：

    ==============  ==========================================================
    字段            匹配
    ==============  ==========================================================
    文件名          ``LIKE '%q%'``
    相册            ``= q`` 或 ``LIKE 'q/%'``（含子相册）
    文件格式        ``= '.jpg'``
    文本一族        ``LIKE '%q%'``
    大小 / 数值     比较式（等于是空比较符）
    时间            区间；``f.captured`` 是秒，``p.mtime_ns`` 是纳秒
    ==============  ==========================================================
    """
    field = condition.field
    value = condition.value
    if field.kind == "name":
        return f"{field.column} LIKE ? ESCAPE '{_LIKE_ESCAPE}'", [f"%{_like(value)}%"]
    if field.kind == "text":
        return f"{field.column} LIKE ? ESCAPE '{_LIKE_ESCAPE}'", [f"%{_like(value)}%"]
    if field.kind == "album":
        return (
            f"({field.column} = ? OR {field.column} LIKE ? ESCAPE '{_LIKE_ESCAPE}')",
            [value, f"{_like(value)}/%"],
        )
    if field.kind == "format":
        return f"{field.column} = ?", [value]
    if field.kind in ("number", "size"):
        comparator = condition.comparator or "="
        return f"{field.column} {comparator} ?", [float(value)]
    # 时间：区间比较时 ``value`` 是「起|止」两个 epoch 秒。
    if condition.comparator == "between":
        low, _, high = value.partition("|")
        if field.id == FACET_MODIFIED:
            low, high = str(float(low) * 1e9), str(float(high) * 1e9)
        return f"{field.column} >= ? AND {field.column} < ?", [float(low), float(high)]
    if field.id == FACET_MODIFIED:
        return f"{field.column} {condition.comparator} ?", [float(value) * 1e9]
    return f"{field.column} {condition.comparator} ?", [float(value)]


def _like(text: str) -> str:
    """把 ``LIKE`` 的通配符转义掉（用户搜 ``IMG_001`` 时 ``_`` 不该顶替任何字符）。"""
    escaped = text.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    for character in ("%", "_"):
        escaped = escaped.replace(character, _LIKE_ESCAPE + character)
    return escaped


def rows_to_refs(rows: Sequence[tuple[object, ...]]) -> list[PhotoRef]:
    """``(路径, 大小, 修改时间)`` 三元组 → ``PhotoRef``。

    **大小与修改时间直接从索引里带出来**，不为每条结果再 ``stat()`` 一次 ——
    一万条结果就是一万次系统调用，而这两个值索引里本来就有。
    """
    refs: list[PhotoRef] = []
    for row in rows:
        path, size, mtime_ns = row[0], row[1], row[2]
        if not isinstance(path, str) or not isinstance(size, int) or not isinstance(mtime_ns, int):
            continue
        refs.append(PhotoRef(path=Path(path), size=size, mtime_ns=mtime_ns))
    return refs


def relative_folder_path(root: Path, path: Path) -> str:
    """照片所在相册相对库根的位置。

    直接转给 :func:`gallery.core.metadata.facets.relative_folder` —— 索引里存的
    就是那一个函数算出来的字符串，两处各写一份迟早对不上（那正是「#相册 旅行」
    一条都查不到的成因）。
    """
    return relative_folder(path, root)


__all__ = [
    "COMPARATORS",
    "DEFAULT_FIELD",
    "RANGE_SEPARATOR",
    "RESULT_LIMIT",
    "SEARCH_FIELDS",
    "ParsedInput",
    "SearchCondition",
    "SearchField",
    "SearchKind",
    "SearchOutcome",
    "build_count_query",
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
