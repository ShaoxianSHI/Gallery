"""苹果日历的脚本：哪一层翻成哪个视图、日期怎么拼（纯函数，一条命令都不跑）。"""

from __future__ import annotations

import datetime

import pytest

from gallery.core.external.apple_calendar import APP_IDENTIFIER, failure_detail, script
from gallery.core.external.runner import CommandResult

DAY = datetime.date(2026, 6, 29)


@pytest.mark.parametrize(
    ("level", "view"),
    [("year", "month view"), ("month", "month view"), ("week", "week view")],
)
def test_each_level_switches_to_its_view(level: str, view: str) -> None:
    text = script(level, DAY)
    assert f"switch view to {view}" in text
    assert "view calendar at target" in text


def test_year_has_no_view_of_its_own() -> None:
    """脚本字典里的 ``view type`` 只有 day / week / month，写 ``year view`` 是语法错误。"""
    assert "year view" not in script("year", DAY)


def test_the_date_is_built_from_numbers_not_a_locale_literal() -> None:
    """``date "…"`` 跟着系统语言走，换个语言就解析不出来。"""
    text = script("week", DAY)
    assert 'date "' not in text
    assert "set year of target to 2026" in text
    assert "set month of target to 6" in text
    assert "set day of target to 29" in text


def test_the_day_is_reset_before_the_month_changes() -> None:
    """31 日改到 2 月会溢出到下个月：先归 1，这一次序不能换。"""
    text = script("month", datetime.date(2024, 2, 29))
    assert text.index("set day of target to 1") < text.index("set month of target to 2")
    assert text.index("set month of target to 2") < text.index("set day of target to 29")


def test_the_app_is_addressed_by_bundle_id() -> None:
    """名字跟着界面语言走，bundle id 不会。"""
    assert APP_IDENTIFIER in script("week", DAY)


def test_the_calendar_comes_to_the_front() -> None:
    """不带 ``activate`` 时命令在后台改，用户会以为按钮没反应。"""
    assert "activate" in script("week", DAY)


@pytest.mark.parametrize("level", ["", "day", "decade", "WEEK"])
def test_unknown_level_is_refused(level: str) -> None:
    """不认识的一层直接报错 —— 猜错会静默地开到隔壁那个月。"""
    with pytest.raises(ValueError):
        script(level, DAY)


def test_a_denied_automation_grant_says_where_to_fix_it() -> None:
    """拒绝授权是**用户能自己修的那一类**，提示要给到具体的那一栏。"""
    detail = failure_detail(CommandResult(1, stderr="execution error: … (-1743)"))
    assert "自动化" in detail
    assert "系统设置" in detail


def test_a_timeout_and_a_missing_app_are_told_apart() -> None:
    assert failure_detail(CommandResult(1, stderr="… (-1712)")) == "「日历」未响应"
    assert failure_detail(CommandResult(1, stderr="… (-10814)")) == "未找到系统「日历」"


def test_an_unknown_error_is_not_dressed_up_as_a_permission_problem() -> None:
    assert failure_detail(CommandResult(1, stderr="随便一段")) == "未能打开系统「日历」"
    assert failure_detail(CommandResult(-1, stderr="超时")) == "「日历」未响应"
