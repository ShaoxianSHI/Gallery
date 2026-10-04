"""日历视图的日期计算与分桶（纯 Python，不碰 Qt）。

日历有四层（年 / 月 / 周 / 日），每层都是「一个锚点日期 → 一段连续的日子」。
于是这里只有三件事：**当前层是哪一段日子**、**翻一页去哪里**、**这层标题写什么**。
全部是纯函数，所以能直接单测（与 ``library.session`` 的几何推导同一个路数）。

另一半是**分桶**：把照片按「哪一天」「哪一个小时」归拢。这一步**不是**
``library.groups.build_groups`` 那个分组 —— 分组只给「有照片的那些组」，而日历要的是
一张**每一天都有格子**的表（2021 年只有 8 天有照片，另外 357 格也得画出来）。

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

#: 四层，从粗到细。顺序也是 ``Y`` / ``M`` / ``W`` / ``D`` 四枚快捷键的顺序。
LEVELS: Final[tuple[str, ...]] = ("year", "month", "week", "day")

#: 各层的名称（标题栏的分段选择器与无障碍名称用它）。
LEVEL_NAMES: Final[Mapping[str, str]] = {
    "year": "年",
    "month": "月",
    "week": "周",
    "day": "日",
}

#: 年视图铺的那几天。取 366 是为了闰年的 2 月 29 日也有格子（按「最多」铺，
#: 平年最后那一格落在下一年 1 月 1 日上 —— 年视图里没人会数它）。
#: （2 月 29 日在闰年会被多列一天 —— 年视图里多出来的那一格不会有人去数。）
_DAYS_IN_YEAR: Final = 366

#: 一周的第一天。中文日历与 Google Calendar（中文界面）都是周一打头。
_FIRST_WEEKDAY: Final = 0  # ``date.weekday()`` 里周一是 0

#: 中文短星期名，索引与 ``date.weekday()`` 一致（周一在前）。
WEEKDAY_NAMES: Final[tuple[str, ...]] = ("一", "二", "三", "四", "五", "六", "日")

#: 月视图表头写「星期一」而不是「一」：一列里只有「一」是认不出来的。
WEEKDAY_HEADERS: Final[tuple[str, ...]] = tuple(f"星期{name}" for name in WEEKDAY_NAMES)

#: 一格按天分的桶。
DayBuckets = Mapping[datetime.date, Sequence[PhotoRef]]

#: 一格按（天, 小时）分的桶。周 / 日两层的每一个小时格子就是其中一项。
HourBuckets = Mapping[tuple[datetime.date, int], Sequence[PhotoRef]]


def is_level(value: str) -> bool:
    return value in LEVELS


def days_of(level: str, anchor: datetime.date) -> list[datetime.date]:
    """这一层铺开的那段日子（含补位的邻月日子）。

    - 年：366 天，够把 12 张小月都铺满；
    - 月：6 × 7 = 42 天，从**当月 1 号所在那一周的周一**起算 ——
      所以月视图第一行总从周一开始，与表头那七个字对得上；
    - 周：周一到周日七天；
    - 日：就那一天。
    """
    if level == "year":
        start = datetime.date(anchor.year, 1, 1)
        return [start + datetime.timedelta(days=offset) for offset in range(_DAYS_IN_YEAR)]
    if level == "month":
        return month_matrix(anchor)
    if level == "week":
        start = week_start(anchor)
        return [start + datetime.timedelta(days=offset) for offset in range(7)]
    return [anchor]


def month_matrix(anchor: datetime.date) -> list[datetime.date]:
    """覆盖整月的那 6 × 7 = 42 天，含上下月补位。

    从**当月 1 号所在那一周的周一**起算，所以第一行总从周一开始，
    与表头那七个字对得上（``date.weekday()`` 里周一是 0）。
    """
    start = week_start(datetime.date(anchor.year, anchor.month, 1))
    return [start + datetime.timedelta(days=offset) for offset in range(42)]


def week_start(day: datetime.date) -> datetime.date:
    """这一周的周一。"""
    return day - datetime.timedelta(days=(day.weekday() - _FIRST_WEEKDAY) % 7)


def shift(level: str, anchor: datetime.date, steps: int) -> datetime.date:
    """往前 / 往后翻一页。

    年翻一年、月翻一月、周翻七天、日翻一天。**保持同一个「日子」**：
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
    days = {"week": 7, "day": 1}.get(level, 1)
    return anchor + datetime.timedelta(days=steps * days)


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
    - 日：``2021年9月12日 星期日``
    """
    if level == "year":
        return f"{anchor.year}年"
    if level == "month":
        return f"{anchor.year}年{anchor.month}月"
    if level == "day":
        return f"{anchor.year}年{anchor.month}月{anchor.day}日 星期{weekday_name(anchor)}"
    start = week_start(anchor)
    end = start + datetime.timedelta(days=6)
    span = f"{start.year}年{start.month}月{start.day}日 – {end.month}月{end.day}日"
    return f"{span} · 第 {iso_week(start)} 周"


def weekday_name(day: datetime.date) -> str:
    return WEEKDAY_NAMES[day.weekday()]


def iso_week(day: datetime.date) -> int:
    """ISO 8601 周号（周一为一周之首，含 1 月 4 日的那周是第 1 周）。"""
    return day.isocalendar()[1]


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
    "DayBuckets",
    "HourBuckets",
    "capture_day",
    "capture_hour",
    "count_days",
    "count_hours",
    "days_of",
    "group_by_day",
    "group_by_hour",
    "is_level",
    "iso_week",
    "month_matrix",
    "shift",
    "title",
    "week_start",
    "weekday_name",
]
