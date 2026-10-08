"""把「哪一层 + 哪一天」翻成 Google 日历的深链（纯函数，不碰进程）。"""

from __future__ import annotations

import datetime

import pytest

from gallery.core.external.gcal import HOST, deep_link

BASE = f"https://{HOST}/calendar/u/0/r"


def test_week_carries_the_full_date() -> None:
    assert deep_link("week", datetime.date(2026, 6, 29)) == f"{BASE}/week/2026/6/29"


def test_month_carries_the_full_date() -> None:
    assert deep_link("month", datetime.date(2026, 6, 29)) == f"{BASE}/month/2026/6/29"


def test_year_carries_only_the_year() -> None:
    """年视图**不带月日**：实测带不带都行，而不带的那一份渲染出来是干净的年视图。"""
    assert deep_link("year", datetime.date(2026, 6, 29)) == f"{BASE}/year/2026"


def test_month_is_one_based() -> None:
    """**月份是 1 起算的** —— 网上大量教程说 0-based，那是错的。

    实测：`/r/month/2026/6/29` 渲染出来是「2026年6月」。写反了会静默地跳到隔壁那个月，
    而界面上看不出错，所以这里固定住这个值。
    """
    january = deep_link("month", datetime.date(2026, 1, 15))
    december = deep_link("month", datetime.date(2026, 12, 15))
    assert january.endswith("/month/2026/1/15")
    assert december.endswith("/month/2026/12/15")
    assert "/month/2026/0/" not in january


def test_leap_day_is_not_shifted() -> None:
    assert deep_link("month", datetime.date(2024, 2, 29)).endswith("/month/2024/2/29")


def test_year_boundary_keeps_its_own_year() -> None:
    """跨年那一周：12 月 31 日算在它自己这一年里，不推到下一年。"""
    assert deep_link("week", datetime.date(2025, 12, 31)).endswith("/week/2025/12/31")


def test_account_index_is_taken_from_the_argument() -> None:
    assert deep_link("month", datetime.date(2026, 6, 29), account=1) == (
        f"https://{HOST}/calendar/u/1/r/month/2026/6/29"
    )


def test_negative_account_falls_back_to_the_first_one() -> None:
    assert deep_link("month", datetime.date(2026, 6, 29), account=-3).startswith(
        f"https://{HOST}/calendar/u/0/"
    )


@pytest.mark.parametrize("level", ["", "day", "decade", "WEEK"])
def test_unknown_level_is_refused(level: str) -> None:
    """不认识的一层直接报错，不要猜成月视图 —— 猜错会静默地开到隔壁那一个月。"""
    with pytest.raises(ValueError):
        deep_link(level, datetime.date(2026, 6, 29))  # type: ignore[arg-type]
