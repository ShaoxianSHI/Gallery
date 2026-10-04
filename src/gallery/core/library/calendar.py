"""日历视图的日期计算与分桶（纯 Python，不碰 Qt）。

日历有三层（年 / 月 / 周），每层都是「一个锚点日期 → 一段连续的日子」。
于是这里只有三件事：**当前层是哪一段日子**、**翻一页去哪里**、**这层标题写什么**。
全部是纯函数，所以能直接单测（与 ``library.session`` 的几何推导同一个路数）。

另一半是**分桶**：把照片按「哪一天」「哪一个小时」归拢。这一步**不是**
``library.groups.build_groups`` 那个分组 —— 分组只给「有照片的那些组」，而日历要的是
一张**每一天都有格子**的表（2021 年只有 8 天有照片，另外 357 格也得画出来）。

**格子与照片是两回事：格子里铺的日子含上下月补位，照片只属于它自己的那个月。**
:func:`month_matrix` 那 42 天里有 10 月 1 日，而那一格在「9 月」那张表里 ——
于是「这一格摆不摆照片」「这一月有几张」都得问 :func:`in_month` 与
:func:`month_days`，而不是「矩阵里有」。

**没有拍摄时间的照片不进日历**（ADR 0033 决策 4）：它们不落到任何一天，画在哪儿都是
另一个话题。分组视图那边「没值的排最后」（ADR 0009）在这里不成立。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets

#: 没有元数据时的占位：任何照片都能用它算 key，只是分不进任何一桶。
EMPTY_FACETS = PhotoFacets()

#: 三层，从粗到细。顺序也是 ``Y`` / ``M`` / ``W`` 三枚快捷键的顺序。
#:
#: **没有日视图**：一周七列铺开之后，一天就是一个小时格子 —— 再单开一层「日」，
#: 得到的还是同一批照片，只是把它摊到整屏；用户真正想做的事（「那天下午拍了什么」）
#: 点一下那一小时就到了（见 :func:`group_by_hour`）。
LEVELS: Final[tuple[str, ...]] = ("year", "month", "week")

#: 各层的名称（标题栏的分段选择器与无障碍名称用它）。
LEVEL_NAMES: Final[Mapping[str, str]] = {"year": "年", "month": "月", "week": "周"}

#: 每个月的天数（平年）。闰年二月多一天，见 :func:`days_in_month`。
_DAYS_IN_MONTH: Final[tuple[int, ...]] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: 一周的第一天：**周日**。三层日历的列都是周日打头（``date.weekday()`` 里周日是 6，
#: 取模后落在最前面）。
_FIRST_WEEKDAY: Final = 6

#: 中文短星期名。**索引是「周日打头」那一套**（0 = 日 … 6 = 六），与表头那七列、
#: 与 :func:`week_index` 一致 —— **不是** ``date.weekday()`` 那一套（那一套周一在前，
#: 0 = 一、6 = 日）。两套差一天，所以**别拿 ``day.weekday()`` 直接索引它**：那样每一列
#: 的名字都差一天，表头第一列（周日）会写成「周六」。
#:
#: 要按 ``date.weekday()`` 问「这是不是周末」，问 :data:`WEEKEND_WEEKDAYS`。
WEEKDAY_NAMES: Final[tuple[str, ...]] = ("日", "一", "二", "三", "四", "五", "六")

#: 周末是哪两天 —— **``date.weekday()`` 的口径**：周六 5、周日 6。
#:
#: 早先这里是 ``{6, 0}``，那是从「周日打头」那套数出来的（可 0 在 ``weekday()`` 里是
#: **周一**）：周视图于是把周一涂成了周末、周六反而没有底色，与表头对不上。
WEEKEND_WEEKDAYS: Final[frozenset[int]] = frozenset({5, 6})

#: 月视图表头写「星期一」而不是「一」：一列里只有「一」是认不出来的。
WEEKDAY_HEADERS: Final[tuple[str, ...]] = tuple(f"星期{name}" for name in WEEKDAY_NAMES)

#: 一格按天分的桶。
DayBuckets = Mapping[datetime.date, Sequence[PhotoRef]]

#: 一格按（天, 小时）分的桶。周 / 日两层的每一个小时格子就是其中一项。
HourBuckets = Mapping[tuple[datetime.date, int], Sequence[PhotoRef]]


def is_level(value: str) -> bool:
    return value in LEVELS


def _is_leap(year: int) -> bool:
    """格里高利历的闰年（四年一闰，百年不闰，四百年再闰）。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(month: datetime.date) -> int:
    """``month`` 那个月有多少天（闰年二月 29 天）。``month`` 的「几号」不看。"""
    if month.month == 2 and _is_leap(month.year):
        return 29
    return _DAYS_IN_MONTH[month.month - 1]


def in_month(day: datetime.date, month: datetime.date) -> bool:
    """这一天是不是 ``month`` 那个月**自己**的日子 —— 补位格不算。

    月视图与年视图那张 6 × 7 的表都含上下月补位，于是 10 月 1 日在「9 月」那张表里
    也会出现一次。**那一格不摆照片，也不算进九月的张数**：用户问的是「九月拍了什么」，
    而九月的照片不该出现在九月的格子里 —— 那是邻月的照片，摆错了地方。

    **判据只在这里写一处**：年月都算上（只比 ``month`` 会在跨年的补位上错），
    界面不自己比较 ``day.month``。
    """
    return (day.year, day.month) == (month.year, month.month)


def month_days(month: datetime.date) -> list[datetime.date]:
    """``month`` 自己的那几天（29–31 天），顺序与 :func:`month_matrix` 里的一致。

    数这一月的张数、列这一月要哪几张缩略图，都问它 —— 而不是拿 42 天的矩阵去减。
    """
    first = datetime.date(month.year, month.month, 1)
    return [first + datetime.timedelta(days=offset) for offset in range(days_in_month(month))]


def days_of(level: str, anchor: datetime.date) -> list[datetime.date]:
    """这一层**自己**的那段日子（**不含**上下月补位）。

    - 年：365 天，闰年 366 —— 停在 12 月 31 日，不多出下一年那一天的；
    - 月：29–31 天；
    - 周：周日到周六七天（周视图那七列本来就没有补位）。

    **铺格子用的 6 × 7（含补位）是另一件事**，见 :func:`month_matrix`：那张表从
    **当月 1 号所在那一周的周日**起算，所以第一行总从周日开始，与表头那七个字对得上；
    而「这一层有几张照片」问的是自己那几天。
    """
    if level == "year":
        first = datetime.date(anchor.year, 1, 1)
        length = 366 if _is_leap(anchor.year) else 365
        return [first + datetime.timedelta(days=offset) for offset in range(length)]
    if level == "month":
        return month_days(anchor)
    start = week_start(anchor)
    return [start + datetime.timedelta(days=offset) for offset in range(7)]


def month_matrix(anchor: datetime.date) -> list[datetime.date]:
    """铺格子用的那 6 × 7 = 42 天，**含上下月补位**。

    从**当月 1 号所在那一周的周日**起算，所以第一行总从周日开始，
    与表头那七个字对得上。

    **这张表是给格子用的，不是给照片用的**：前后补位那几天是邻月的日子，
    问「这一格摆不摆照片」用 :func:`in_month`，问「这一月有几天照片」用
    :func:`month_days`。
    """
    start = week_start(datetime.date(anchor.year, anchor.month, 1))
    return [start + datetime.timedelta(days=offset) for offset in range(42)]


def week_start(day: datetime.date) -> datetime.date:
    """这一周的周日（这一周是从周日开始数的）。"""
    return day - datetime.timedelta(days=(day.weekday() - _FIRST_WEEKDAY) % 7)


def week_index(day: datetime.date) -> int:
    """这一天在「周日打头的那一周」里是第几列（周日 0 … 周六 6）。

    表头那一行写哪个星期名、点到了哪一列 —— 都问它。**两套星期口径差一天**：
    ``date.weekday()`` 是周一 0 … 周日 6，而日历的列是周日 0 … 周六 6。
    拿前者去索引 :data:`WEEKDAY_NAMES`（周日打头那一套）会**每一列都差一天** ——
    症状是表头第一列写着「周六」，而那一列其实是周日。
    """
    return (day.weekday() - _FIRST_WEEKDAY) % 7


def shift(level: str, anchor: datetime.date, steps: int) -> datetime.date:
    """往前 / 往后翻一页。

    年翻一年、月翻一月、周翻七天。**保持同一个「日子」**：
    1 月 31 日往后翻一个月是 2 月 28 日（``date`` 自己会截断），下一次往前翻
    回 1 月 28 日 —— 用户来回翻两下就停在 28 号上，不会越翻越偏。
    """
    if steps == 0:
        return anchor
    if level == "year":
        return anchor.replace(year=anchor.year + steps)
    if level == "month":
        total = anchor.year * 12 + (anchor.month - 1) + steps
        year, month = divmod(total, 12)
        return _clamped(year, month + 1, anchor.day)
    return anchor + datetime.timedelta(days=steps * 7)


def _clamped(year: int, month: int, day: int) -> datetime.date:
    """``date`` 的年月组合在「这一天不存在」时截到当月最后一天（1 月 31 日 + 1 月）。"""
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1


def title(level: str, anchor: datetime.date) -> str:
    """这一层的标题（工具条上那一行大字）。

    - 年：``2021年``
    - 月：``2021年9月``
    - 周：``2021年9月6日 – 9月12日 · 第 36 周``
    """
    if level == "year":
        return f"{anchor.year}年"
    if level == "month":
        return f"{anchor.year}年{anchor.month}月"
    start = week_start(anchor)
    end = start + datetime.timedelta(days=6)
    span = f"{start.year}年{start.month}月{start.day}日 – {end.month}月{end.day}日"
    return f"{span} · 第 {week_number(start)} 周"


def week_number(day: datetime.date) -> int:
    """这一年里的第几周（**按日历上那一周数**：周日到周六）。

    刻意不用 ISO 8601 周号：ISO 是「周一为一周之首」，而这里的一周从周日开始 ——
    同一个日期在两套口径下常常差一格，标题上写着一个数、屏幕上摆着另一周，
    那比不写更糟。

    **第 1 周是「含 1 月 1 日的那一周」**：不管 1 月 1 日是星期几，它所在的那一周
    就是第 1 周（年底同理 —— 12 月 31 日所在的那一周仍是今年最后一周，不会溢到
    「第 0 周」或「第 54 周」）。这是日历软件惯用的那套。
    """
    first_week = week_start(datetime.date(day.year, 1, 1))
    return (day - first_week).days // 7 + 1


def capture_day(facets: PhotoFacets) -> datetime.date | None:
    """这张照片的**墙上日期**；没有拍摄时间就是 ``None``。

    刻意不从 ``captured_at`` 换算：照片写了时区偏移时，换算出来的是「在这台机器上
    看到的日期」，与 ``year`` / ``month`` / ``day`` 那一套不是同一个口径（ADR 0033 决策 3）。
    """
    year, month, day = facets.year, facets.month, facets.day
    if year is None or month is None or day is None:
        return None
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def capture_hour(facets: PhotoFacets) -> tuple[datetime.date, int] | None:
    """这张照片的（墙上日期, 小时）；缺任何一栏就是 ``None``。"""
    day = capture_day(facets)
    if day is None or facets.hour is None:
        return None
    return day, int(facets.hour)


def group_by_day(
    photos: Iterable[PhotoRef], values: Mapping[Path, PhotoFacets]
) -> dict[datetime.date, list[PhotoRef]]:
    """按天分桶，**只含有照片的那些天**。

    没有拍摄时间的照片直接不进来（ADR 0033 决策 4）。每桶内按拍摄时间排 ——
    桶里第一张就是「那天最早的那张」，周 / 日视图拿它当那一格的封面。
    """
    buckets: dict[datetime.date, list[tuple[float, str, PhotoRef]]] = {}
    for ref in photos:
        facets = values.get(ref.path, EMPTY_FACETS)
        day = capture_day(facets)
        if day is None:
            continue
        moment = facets.captured_at if facets.captured_at is not None else 0.0
        buckets.setdefault(day, []).append((moment, ref.name.casefold(), ref))
    return {
        day: [ref for _, _, ref in sorted(items, key=lambda item: (item[0], item[1]))]
        for day, items in buckets.items()
    }


def group_by_hour(
    photos: Iterable[PhotoRef], values: Mapping[Path, PhotoFacets]
) -> dict[tuple[datetime.date, int], list[PhotoRef]]:
    """按（天, 小时）分桶 —— 周 / 日两层每一个小时格子就是其中一项。

    **分钟与秒不参与**：一次连拍 48 张常常全在 19:44:02，按小时归一之后它们就是
    同一格，于是那一格的问题只剩「一格放几张」，不再是一小时切 48 条 0.8 像素高
    的细条（那条算下来是负数，什么都画不出来）。
    """
    buckets: dict[tuple[datetime.date, int], list[tuple[float, str, PhotoRef]]] = {}
    for ref in photos:
        facets = values.get(ref.path, EMPTY_FACETS)
        key = capture_hour(facets)
        if key is None:
            continue
        moment = facets.captured_at if facets.captured_at is not None else 0.0
        buckets.setdefault(key, []).append((moment, ref.name.casefold(), ref))
    return {
        key: [ref for _, _, ref in sorted(items, key=lambda item: (item[0], item[1]))]
        for key, items in buckets.items()
    }


def count_days(buckets: DayBuckets, days: Iterable[datetime.date]) -> int:
    """这段日子上一共多少张照片（标题栏与「空日历」提示都用它）。"""
    return sum(len(buckets.get(day, ())) for day in days)


def count_hours(buckets: HourBuckets, days: Iterable[datetime.date]) -> int:
    """同上，按小时的桶来数。周 / 日两层一天要过 24 个格子，所以那一次多 24 次查表。"""
    return sum(len(buckets.get((day, hour), ())) for day in days for hour in range(24))


__all__ = [
    "LEVELS",
    "LEVEL_NAMES",
    "WEEKDAY_HEADERS",
    "WEEKDAY_NAMES",
    "WEEKEND_WEEKDAYS",
    "DayBuckets",
    "HourBuckets",
    "capture_day",
    "capture_hour",
    "count_days",
    "count_hours",
    "days_in_month",
    "days_of",
    "group_by_day",
    "group_by_hour",
    "in_month",
    "is_level",
    "month_days",
    "month_matrix",
    "shift",
    "title",
    "week_index",
    "week_number",
    "week_start",
]
