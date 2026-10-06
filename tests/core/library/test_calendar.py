"""日历的日期计算与分桶（纯函数，不起窗口）。"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from gallery.core.library.calendar import (
    LEVELS,
    WEEKDAY_HEADERS,
    WEEKDAY_NAMES,
    WEEKEND_WEEKDAYS,
    capture_day,
    capture_hour,
    cell_cover,
    count_days,
    count_hours,
    days_in_month,
    days_of,
    group_by_day,
    group_by_hour,
    in_month,
    is_level,
    month_days,
    month_matrix,
    shift,
    title,
    week_index,
    week_number,
    week_start,
)
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets


def make_ref(name: str) -> PhotoRef:
    return PhotoRef(path=Path(name), size=1, mtime_ns=0)


@pytest.mark.parametrize("level", LEVELS)
def test_levels_are_the_four_we_offer(level: str) -> None:
    assert is_level(level)
    assert not is_level("hour")
    assert not is_level("")


@pytest.mark.parametrize(("year", "length"), [(2021, 365), (2024, 366)])
def test_a_year_is_exactly_its_own_days(year: int, length: int) -> None:
    """**不多一天**：平年原先是 366 天，于是最后一天落在下一年 1 月 1 日上 ——
    那一天的照片于是算进了这一年的张数里。"""
    days = days_of("year", datetime.date(year, 9, 12))
    assert len(days) == length
    assert days[0] == datetime.date(year, 1, 1)
    assert days[-1] == datetime.date(year, 12, 31)


@pytest.mark.parametrize(("year", "month", "length"), [(2021, 2, 28), (2024, 2, 29)])
def test_february_knows_how_long_it_is(year: int, month: int, length: int) -> None:
    assert days_in_month(datetime.date(year, month, 15)) == length
    assert len(month_days(datetime.date(year, month, 15))) == length


@pytest.mark.parametrize(
    ("month", "length"),
    [(datetime.date(2021, 4, 9), 30), (datetime.date(2021, 12, 31), 31)],
)
def test_a_month_counts_only_the_days_it_has(month: datetime.date, length: int) -> None:
    """月视图停在四月：这一层**自己**的那几天是 30 天，不是那张 6 × 7 的 42 天。"""
    days = days_of("month", month)
    assert len(days) == length
    assert days[0] == datetime.date(month.year, month.month, 1)
    assert all(in_month(day, month) for day in days)


def test_a_filler_day_is_not_part_of_the_month() -> None:
    """9 月那张 6 × 7 的表里摆着 10 月 1 日与 8 月 31 日 —— **那不是九月的日子**。

    只比 ``month`` 在跨年时会错：12 月那张表的末尾摆着 1 月 1 日，
    「1 月」与 12 月当然不同，可它属于**下一年**。
    """
    september = datetime.date(2021, 9, 12)
    assert in_month(datetime.date(2021, 9, 1), september)
    assert not in_month(datetime.date(2021, 10, 1), september)
    assert not in_month(datetime.date(2021, 8, 31), september)
    # 同一个月份号，不同的年：12 月那张小月的末尾
    assert not in_month(datetime.date(2022, 1, 1), datetime.date(2021, 12, 1))


def test_the_filler_days_of_the_matrix_are_exactly_the_ones_left_out() -> None:
    anchor = datetime.date(2021, 9, 12)
    matrix = month_matrix(anchor)
    own = month_days(anchor)
    assert [day for day in matrix if in_month(day, anchor)] == own
    # 2021 年 9 月 1 号是周三：前面补 3 天（8 月 29–31），后面补 9 天（10 月 1–9）
    assert len(matrix) - len(own) == 12


def test_a_month_is_six_weeks_starting_on_a_sunday() -> None:
    days = month_matrix(datetime.date(2021, 9, 12))
    assert len(days) == 42
    assert days[0].weekday() == 6  # 周日
    assert datetime.date(2021, 9, 1) in days
    assert datetime.date(2021, 9, 30) in days
    # 2021 年 9 月 1 号是周三，往前补到那个周日的 8 月 29 日
    assert days[0] == datetime.date(2021, 8, 29)


def test_a_week_starts_on_sunday_and_ends_on_saturday() -> None:
    sunday = datetime.date(2021, 9, 12)
    assert week_start(sunday) == sunday
    assert days_of("week", sunday)[0] == sunday
    assert days_of("week", sunday)[-1] == datetime.date(2021, 9, 18)
    # 同一周的任意一天都指向同一个周日
    assert week_start(datetime.date(2021, 9, 18)) == sunday
    assert week_start(datetime.date(2021, 9, 15)) == sunday


def test_the_weekday_constants_run_from_sunday() -> None:
    assert WEEKDAY_NAMES[0] == "日"
    assert WEEKDAY_NAMES[-1] == "六"
    assert WEEKDAY_HEADERS[0] == "星期日"
    # 周末是**周六与周日**。``date.weekday()`` 是周一 0 … 周日 6，所以是 5 与 6 ——
    # 早先这里是 ``{6, 0}``，那个 0 是「周日打头」那一套的周一，于是周视图把周一涂成了
    # 周末、周六反而没有底色，与表头对不上。
    assert frozenset({5, 6}) == WEEKEND_WEEKDAYS
    assert datetime.date(2021, 9, 18).weekday() in WEEKEND_WEEKDAYS  # 周六
    assert datetime.date(2021, 9, 12).weekday() in WEEKEND_WEEKDAYS  # 周日
    assert datetime.date(2021, 9, 13).weekday() not in WEEKEND_WEEKDAYS  # 周一


@pytest.mark.parametrize("anchor", [datetime.date(2021, 9, 12), datetime.date(2024, 3, 16)])
def test_week_index_counts_the_columns_from_sunday(anchor: datetime.date) -> None:
    """一周里的第几列 = 表头那一列该写哪个星期。

    **两套星期口径差一天**：``date.weekday()`` 周一 0 … 周日 6，而日历的列周日 0 … 周六 6。
    拿前者去索引 :data:`WEEKDAY_NAMES` 会让七个名字整体差一天 —— 表头第一列（周日）
    写着「周六」。
    """
    days = days_of("week", anchor)
    assert [week_index(day) for day in days] == list(range(7))
    assert [WEEKDAY_NAMES[week_index(day)] for day in days] == [
        "日",
        "一",
        "二",
        "三",
        "四",
        "五",
        "六",
    ]


def test_there_is_no_day_level() -> None:
    """日历只有三层：再单开「日」摊出来的还是同一批照片。"""
    assert LEVELS == ("year", "month", "week")
    assert not is_level("day")


@pytest.mark.parametrize(
    ("level", "step", "expected"),
    [
        ("year", 1, datetime.date(2022, 9, 12)),
        ("year", -1, datetime.date(2020, 9, 12)),
        ("month", 1, datetime.date(2021, 10, 12)),
        ("month", -1, datetime.date(2021, 8, 12)),
        ("week", 1, datetime.date(2021, 9, 19)),
        ("week", -1, datetime.date(2021, 9, 5)),  # 9 月 5 日是周日
    ],
)
def test_stepping_walks_exactly_one_period(level: str, step: int, expected: datetime.date) -> None:
    assert shift(level, datetime.date(2021, 9, 12), step) == expected


def test_stepping_zero_stays_put() -> None:
    assert shift("month", datetime.date(2021, 9, 12), 0) == datetime.date(2021, 9, 12)


def test_month_step_lands_on_a_day_that_exists() -> None:
    """1 月 31 日往后翻一个月是 2 月 28 日 —— 不是 3 月 3 日。"""
    january_31 = datetime.date(2021, 1, 31)
    assert shift("month", january_31, 1) == datetime.date(2021, 2, 28)
    assert shift("month", shift("month", january_31, 1), -1) == datetime.date(2021, 1, 28)


def test_stepping_across_a_leap_day() -> None:
    assert shift("week", datetime.date(2024, 2, 26), 1) == datetime.date(2024, 3, 4)
    assert shift("month", datetime.date(2024, 2, 29), 1) == datetime.date(2024, 3, 29)
    # **年视图也得走同一套截断**：缺陷现场是它直接 ``replace(year=…)``，于是
    # 2024-02-29 往后翻一年抛 ``ValueError: day is out of range for month``。
    # 三条入口（工具条 / 方向键 / 两指横甩）都走 :func:`shift`，症状是「按了没反应」，
    # 而异常只在那一刻打到 stderr —— 界面连锚点都没更新，看起来像没听见。
    assert shift("year", datetime.date(2024, 2, 29), 1) == datetime.date(2025, 2, 28)
    assert shift("year", datetime.date(2024, 2, 29), -1) == datetime.date(2023, 2, 28)
    # 闰年本身要保住 2 月 29 日，别把好日子也截掉。
    assert shift("year", datetime.date(2024, 2, 29), 4) == datetime.date(2028, 2, 29)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("year", "2021年"),
        ("month", "2021年9月"),
        ("week", "2021年9月12日 – 9月18日 · 第 38 周"),
    ],
)
def test_title_says_which_period_is_on_screen(level: str, expected: str) -> None:
    assert title(level, datetime.date(2021, 9, 12)) == expected


def test_week_title_does_not_repeat_the_year_it_already_said() -> None:
    """2021-12-26 那周跨到下一年：后半段只写「1月1日」，不把 2022 再写一遍。"""
    assert title("week", datetime.date(2021, 12, 29)) == "2021年12月26日 – 1月1日 · 第 53 周"


def test_week_numbers_count_the_weeks_that_are_on_screen() -> None:
    """**第 1 周是含 1 月 1 日的那一周**（日历软件那套），不是 ISO 周号。

    ISO 是「周一为一周之首」，而这里的一周从周日开始；两套口径常常差一格，
    标题上写着一个数、屏幕上摆着另一周比不写更糟。
    """
    assert week_number(datetime.date(2021, 1, 1)) == 1  # 2021-01-01 是周五
    assert week_number(datetime.date(2021, 1, 3)) == 2  # 同一周的周日仍是第 1 周
    assert week_number(datetime.date(2021, 1, 4)) == 2  # 下周一进入第 2 周
    assert week_number(datetime.date(2021, 9, 12)) == 38
    # 年底不会溢到「第 0 周」
    assert week_number(datetime.date(2021, 12, 31)) == 53


def test_capture_day_reads_the_wall_clock_not_the_epoch() -> None:
    """写了时区偏移的照片，日期取**照片里记的那天**，不换算到本机。"""
    facets = PhotoFacets(captured_at=0.0, year=2021, month=9, day=12, hour=19, minute=44)
    assert capture_day(facets) == datetime.date(2021, 9, 12)
    assert capture_hour(facets) == (datetime.date(2021, 9, 12), 19)


@pytest.mark.parametrize(
    "facets",
    [
        PhotoFacets(),
        PhotoFacets(year=2021),
        PhotoFacets(year=2021, month=9),
        PhotoFacets(year=2021, month=2, day=31),  # 不存在的日期
    ],
)
def test_capture_day_is_none_without_a_usable_date(facets: PhotoFacets) -> None:
    assert capture_day(facets) is None
    assert capture_hour(facets) is None


def test_capture_hour_needs_the_clock_too() -> None:
    """有日期没有钟点（老索引里的行）进不了小时格子，但日期仍认得。"""
    facets = PhotoFacets(year=2021, month=9, day=12)
    assert capture_day(facets) == datetime.date(2021, 9, 12)
    assert capture_hour(facets) is None


def test_group_by_day_drops_photos_without_a_date() -> None:
    refs = [make_ref("a.jpg"), make_ref("b.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=1.0, year=2021, month=9, day=12, hour=9),
        refs[1].path: PhotoFacets(),
    }
    buckets = group_by_day(refs, values)
    assert list(buckets) == [datetime.date(2021, 9, 12)]
    assert buckets[datetime.date(2021, 9, 12)] == [refs[0]]


def test_group_by_day_sorts_inside_a_day_by_capture_time() -> None:
    refs = [make_ref("late.jpg"), make_ref("early.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=200.0, year=2021, month=9, day=12),
        refs[1].path: PhotoFacets(captured_at=100.0, year=2021, month=9, day=12),
    }
    assert group_by_day(refs, values)[datetime.date(2021, 9, 12)] == [refs[1], refs[0]]


def test_the_cell_cover_is_the_earliest_photo_of_that_day() -> None:
    """一格拿**最早**的那张当代表 —— 三层共用这一条（ADR 0033）。

    曾经年视图取 ``[-1]``（最晚）、月 / 周两层取 ``[0]``（最早），于是同一天在年视图里
    与月视图里是两张照片。理由留在核心层，三层不再各写一遍。
    """
    refs = [make_ref("third.jpg"), make_ref("first.jpg"), make_ref("second.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=300.0, year=2021, month=9, day=12),
        refs[1].path: PhotoFacets(captured_at=100.0, year=2021, month=9, day=12),
        refs[2].path: PhotoFacets(captured_at=200.0, year=2021, month=9, day=12),
    }
    bucket = group_by_day(refs, values)[datetime.date(2021, 9, 12)]
    assert cell_cover(bucket) is refs[1]


def test_an_empty_bucket_has_no_cover() -> None:
    """空桶没有脸可画 —— 返回 ``None``，由界面画占位（**不是 ``IndexError``**）。"""
    assert cell_cover([]) is None


def test_a_burst_lands_in_one_hour_bucket() -> None:
    """48 张全在 19:44 —— 同一个小时格子，而不是 48 个各自不同的格子。"""
    refs = [make_ref(f"burst{i}.jpg") for i in range(48)]
    values = {
        ref.path: PhotoFacets(
            captured_at=1000.0 + index, year=2021, month=9, day=12, hour=19, minute=44
        )
        for index, ref in enumerate(refs)
    }
    hours = group_by_hour(refs, values)
    assert list(hours) == [(datetime.date(2021, 9, 12), 19)]
    assert len(hours[(datetime.date(2021, 9, 12), 19)]) == 48


def test_minutes_do_not_take_part_in_grouping() -> None:
    refs = [make_ref("a.jpg"), make_ref("b.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=1.0, year=2021, month=9, day=12, hour=19, minute=1),
        refs[1].path: PhotoFacets(captured_at=2.0, year=2021, month=9, day=12, hour=19, minute=59),
    }
    assert list(group_by_hour(refs, values)) == [(datetime.date(2021, 9, 12), 19)]


def test_counting_a_period() -> None:
    refs = [make_ref("a.jpg"), make_ref("b.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=1.0, year=2021, month=9, day=12, hour=19),
        refs[1].path: PhotoFacets(captured_at=2.0, year=2021, month=9, day=13, hour=8),
    }
    days = group_by_day(refs, values)
    hours = group_by_hour(refs, values)
    assert count_days(days, month_matrix(datetime.date(2021, 9, 12))) == 2
    assert count_days(days, [datetime.date(2021, 9, 12)]) == 1
    assert count_days(days, [datetime.date(2020, 1, 1)]) == 0
    assert count_hours(hours, [datetime.date(2021, 9, 12)]) == 1
    assert count_hours(hours, []) == 0


def test_counting_a_month_leaves_out_the_filler_days() -> None:
    """「九月有几张」数九月自己的日子 —— 10 月 1 号那一张不算。

    10 月 1 号**确实在**九月那张 6 × 7 的表里（末尾那几格），所以按矩阵数是 2；
    而界面上写着的是「9 月」，那几格里摆的也是十月的照片。
    """
    refs = [make_ref("september.jpg"), make_ref("october.jpg")]
    values = {
        refs[0].path: PhotoFacets(captured_at=1.0, year=2021, month=9, day=12, hour=9),
        refs[1].path: PhotoFacets(captured_at=2.0, year=2021, month=10, day=1, hour=9),
    }
    buckets = group_by_day(refs, values)
    assert count_days(buckets, month_matrix(datetime.date(2021, 9, 1))) == 2
    assert count_days(buckets, month_days(datetime.date(2021, 9, 1))) == 1
