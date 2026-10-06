"""日历视图的日期计算与分桶（纯 Python，不碰 Qt）。

三层（年 / 月 / 周）都是「一个锚点日期 → 一段连续的日子」，因此这里全是纯函数：这一层
是哪几天、翻一页去哪里、标题写什么。另一半是**分桶** —— 与 ``groups.build_groups`` 不同，
日历要的是一张**每一天都有格子**的表。
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
#: **没有日视图**：一周七列铺开后一天就是一个小时格子，点一下那一小时就到了。
LEVELS: Final[tuple[str, ...]] = ("year", "month", "week")

#: 各层的名称（标题栏的分段选择器与无障碍名称用它）。
LEVEL_NAMES: Final[Mapping[str, str]] = {"year": "年", "month": "月", "week": "周"}

#: 每个月的天数（平年）。闰年二月多一天，见 :func:`days_in_month`。
_DAYS_IN_MONTH: Final[tuple[int, ...]] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: 一周的第一天：**周日**（``date.weekday()`` 里周日是 6，取模后落在最前面）。
_FIRST_WEEKDAY: Final = 6

#: 中文短星期名，**索引是「周日打头」那一套**（0 = 日 … 6 = 六），与表头那七列、与
#: :func:`week_index` 一致 —— **不是** ``date.weekday()``（那一套周一在前）。
#: 要按 ``date.weekday()`` 问「是不是周末」，问 :data:`WEEKEND_WEEKDAYS`。
WEEKDAY_NAMES: Final[tuple[str, ...]] = ("日", "一", "二", "三", "四", "五", "六")

#: 周末是哪两天 —— **``date.weekday()`` 的口径**：周六 5、周日 6。
#: 早先写成 ``{6, 0}``（那是「周日打头」那套数出来的），周视图于是把周一涂成周末、周六没有底色。
WEEKEND_WEEKDAYS: Final[frozenset[int]] = frozenset({5, 6})

#: 月视图表头写「星期一」而不是「一」：一列里只有「一」是认不出来的。
WEEKDAY_HEADERS: Final[tuple[str, ...]] = tuple(f"星期{name}" for name in WEEKDAY_NAMES)

#: 一格按天分的桶。
DayBuckets = Mapping[datetime.date, Sequence[PhotoRef]]

#: 一格按（天, 小时）分的桶。**周视图**的每一个小时格子就是其中一项。
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

    **判据只在这里写一处**（年月都算上，只比 ``month`` 会在跨年的补位上错）：界面不自己
    比较 ``day.month``，否则邻月的照片会被摆进邻月的格子里。
    """
    return (day.year, day.month) == (month.year, month.month)


def month_days(month: datetime.date) -> list[datetime.date]:
    """``month`` 自己的那几天（29–31 天）。

    数这一月的张数、列这一月要哪几张缩略图都问它 —— 别拿 42 天的矩阵去减。
    """
    first = datetime.date(month.year, month.month, 1)
    return [first + datetime.timedelta(days=offset) for offset in range(days_in_month(month))]


def days_of(level: str, anchor: datetime.date) -> list[datetime.date]:
    """这一层**自己**的那段日子（**不含**上下月补位）：年 365/366 天、月 29–31 天、周七天。

    铺格子用的 6 × 7（含补位）是另一件事，见 :func:`month_matrix`。
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
    """铺格子用的那 6 × 7 = 42 天（含上下月补位），从**当月 1 号所在那一周的周日**起算。

    **这张表是给格子用的，不是给照片用的**：补位格只为对齐星期几而存在，**只画一个
    日期号**，点它什么都不做；摆不摆照片、这一月有几张问 :func:`in_month` 与
    :func:`month_days`。
    """
    start = week_start(datetime.date(anchor.year, anchor.month, 1))
    return [start + datetime.timedelta(days=offset) for offset in range(42)]


def week_start(day: datetime.date) -> datetime.date:
    """这一周的周日（这一周是从周日开始数的）。"""
    return day - datetime.timedelta(days=(day.weekday() - _FIRST_WEEKDAY) % 7)


def week_index(day: datetime.date) -> int:
    """这一天在「周日打头的那一周」里是第几列（周日 0 … 周六 6）。表头与点到的列都问它。

    **两套星期口径差一天**（``date.weekday()`` 是周一 0 … 周日 6），拿前者去索引
    :data:`WEEKDAY_NAMES` 会**每一列都差一天**：表头第一列（周日）会写成「周六」。
    """
    return (day.weekday() - _FIRST_WEEKDAY) % 7


def shift(level: str, anchor: datetime.date, steps: int) -> datetime.date:
    """往前 / 往后翻一页（年翻一年、月翻一月、周翻七天），**保持同一个「日子」**。

    1 月 31 日往后翻一个月是 2 月 28 日，再往前翻回 1 月 28 日 —— 来回翻两下不会越翻越偏。

    **闰日也一样**：2 月 29 日翻到没有闰日的年份截到 2 月 28 日（直接 ``replace(year=…)`` 会在
    2024-02-29 上抛 ``ValueError``，而三条入口 —— 工具条、方向键、两指横甩 —— 都走这里）。
    与月视图同一套判据，不另立一份。
    """
    if steps == 0:
        return anchor
    if level == "year":
        return _clamped(anchor.year + steps, anchor.month, anchor.day)
    if level == "month":
        total = anchor.year * 12 + (anchor.month - 1) + steps
        year, month = divmod(total, 12)
        return _clamped(year, month + 1, anchor.day)
    return anchor + datetime.timedelta(days=steps * 7)


def _clamped(year: int, month: int, day: int) -> datetime.date:
    """``date`` 的年月组合在「这一天不存在」时截到当月最后一天。

    1 月 31 日 + 1 月 → 2 月 28 日；**2 月 29 日 + 1 年 → 2 月 28 日**。
    逐日往前挪而不是查 ``monthrange``：闰年的 2 月有 29 天，两者恰好同一条路，
    而这一条不需要为「哪些月份有几天」另立一份判据。
    """
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1


def title(level: str, anchor: datetime.date) -> str:
    """这一层的标题：``2021年`` / ``2021年9月`` / ``2021年9月6日 – 9月12日 · 第 36 周``。"""
    if level == "year":
        return f"{anchor.year}年"
    if level == "month":
        return f"{anchor.year}年{anchor.month}月"
    start = week_start(anchor)
    end = start + datetime.timedelta(days=6)
    span = f"{start.year}年{start.month}月{start.day}日 – {end.month}月{end.day}日"
    return f"{span} · 第 {week_number(start)} 周"


def week_number(day: datetime.date) -> int:
    """这一年里的第几周（**按日历上那一周数**：周日到周六），刻意不用 ISO 8601。

    **第 1 周是「含 1 月 1 日的那一周」**，年底同理 —— 12 月 31 日所在的那一周仍是今年
    最后一周，不溢到「第 0 周」。
    """
    first_week = week_start(datetime.date(day.year, 1, 1))
    return (day - first_week).days // 7 + 1


def capture_day(facets: PhotoFacets) -> datetime.date | None:
    """这张照片的**墙上日期**；没有拍摄时间就是 ``None``。

    刻意不从 ``captured_at`` 换算：换了得到的是「在这台机器上看到的日期」，与 ``year`` /
    ``month`` / ``day`` 不是同一个口径（ADR 0033 决策 3）。
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
    """按天分桶，**只含有照片的那些天**（没有拍摄时间的照片不进日历，ADR 0033 决策 4）。

    桶内按 ``(拍摄时间, 文件名)`` 升序，于是**第一张就是「那天最早的那张」**；代表图问
    :func:`cell_cover`，**别在界面里自己写 ``photos[0]``**。
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
    """按（天, 小时）分桶 —— **周视图**每一个小时格子就是其中一项。

    **分钟与秒不参与**：一次连拍 48 张常常全在同一分钟，按小时归一后那一格只剩「放几张」
    一个问题，而不是一小时切 48 条什么都画不出来的细条。
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


def cell_cover(photos: Sequence[PhotoRef]) -> PhotoRef | None:
    """一格（一天 / 一个小时）拿哪张照片当代表 —— 空桶就是 ``None``。

    **一个格子只有一张脸**：三层各自写 ``photos[0]`` 或 ``photos[-1]`` 时，同一天在年视图
    与月视图里就长成两张脸。取桶里第一张（按拍摄时间最早的那张），它既是三层一致的那张，
    也是点开那一格之后网格里的第一张。
    """
    return photos[0] if photos else None


def count_days(buckets: DayBuckets, days: Iterable[datetime.date]) -> int:
    """这段日子上一共多少张照片（标题栏与「空日历」提示都用它）。"""
    return sum(len(buckets.get(day, ())) for day in days)


def count_hours(buckets: HourBuckets, days: Iterable[datetime.date]) -> int:
    """同上，按小时的桶来数。**周视图**一天要过 24 个格子，所以那一次多 24 次查表。"""
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
    "cell_cover",
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
