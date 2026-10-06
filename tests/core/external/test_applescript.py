"""认窗口、改地址，以及「别认错」的那几条（不跑真的 osascript）。"""

from __future__ import annotations

from collections.abc import Sequence

from gallery.core.external.applescript import (
    ChromeWindow,
    chrome_is_up,
    find_pwa_window,
    is_pwa_window,
    list_windows,
    set_window_url,
)
from gallery.core.external.gcal import HOST
from gallery.core.external.runner import CommandResult

CALENDAR = f"https://{HOST}/calendar/u/0/r/week/2026/6/29"

#: 枚举脚本的真实输出形状：每件东西一行，首字母做前缀。
LISTING = f"W2\nN\nT{CALENDAR}\nW1\nN普通窗口\nThttps://example.com/\n"


def runner_returning(result: CommandResult):
    def runner(argv: Sequence[str], *, timeout: float | None = None) -> CommandResult:
        del argv, timeout
        return result

    return runner


def recording_runner(result: CommandResult):
    calls: list[list[str]] = []

    def runner(argv: Sequence[str], *, timeout: float | None = None) -> CommandResult:
        del timeout
        calls.append(list(argv))
        return result

    return runner, calls


def test_listing_is_parsed_into_windows() -> None:
    windows = list_windows(runner_returning(CommandResult(0, LISTING)))
    assert [(w.index, w.name, w.urls) for w in windows] == [
        (2, "", (CALENDAR,)),
        (1, "普通窗口", ("https://example.com/",)),
    ]


def test_failed_listing_gives_no_windows() -> None:
    """脚本失败就是「现在认不出窗口」—— 返回空的，让外面走降级。"""
    assert list_windows(runner_returning(CommandResult(1, "boom"))) == []


def test_garbled_listing_gives_no_windows() -> None:
    assert list_windows(runner_returning(CommandResult(0, "看不懂"))) == []


def test_chrome_is_up_asks_pgrep_not_applescript() -> None:
    """**必须用 ``pgrep``**：AppleScript 会自动启动目标应用，于是「查一下在不在」
    变成了「把它启动一遍」，还会顺带开出一个「新标签页」窗口。"""
    runner, calls = recording_runner(CommandResult(0))
    assert chrome_is_up(runner) is True
    assert calls[0][0] == "pgrep"


def test_chrome_not_up_is_a_nonzero_exit() -> None:
    assert chrome_is_up(runner_returning(CommandResult(1))) is False


def test_app_window_is_recognized() -> None:
    window = ChromeWindow(index=3, name="", urls=(CALENDAR,))
    assert is_pwa_window(window, HOST) is True


def test_named_window_is_not_the_app() -> None:
    """普通窗口**哪怕只开着一个日历标签**也不是那个应用窗口。

    认错了就会去改地址，改掉的正是用户正在看的那一页 —— 比开不起来更难解释。
    """
    assert is_pwa_window(ChromeWindow(1, "Google 日历", (CALENDAR,)), HOST) is False


def test_window_with_several_tabs_is_not_the_app() -> None:
    many = ChromeWindow(1, "", (CALENDAR, "https://example.com/"))
    assert is_pwa_window(many, HOST) is False


def test_window_on_another_site_is_not_the_app() -> None:
    assert is_pwa_window(ChromeWindow(1, "", ("https://example.com/",)), HOST) is False


def test_find_picks_the_first_match() -> None:
    """输出是倒着枚举的，头一个就是最新的 —— 同时开着几个候选时取最新的那个。"""
    windows = list_windows(runner_returning(CommandResult(0, LISTING)))
    assert find_pwa_window(windows, HOST) == 2


def test_find_returns_none_rather_than_guessing() -> None:
    """**认不出就返回 None**，不挑一个差不多的窗口去改。"""
    windows = list_windows(runner_returning(CommandResult(0, "W1\nN普通\nThttps://x.com/\n")))
    assert find_pwa_window(windows, HOST) is None


def test_set_window_url_writes_that_window() -> None:
    runner, calls = recording_runner(CommandResult(0))
    assert set_window_url(3, CALENDAR, runner) is True
    assert "set URL of tab 1 of window 3" in calls[0][-1]
    assert CALENDAR in calls[0][-1]


def test_set_window_url_reports_failure() -> None:
    assert set_window_url(3, CALENDAR, runner_returning(CommandResult(1))) is False


def test_set_window_url_escapes_quotes() -> None:
    """链接里带引号时不能把脚本报错撑破。"""
    runner, calls = recording_runner(CommandResult(0))
    set_window_url(1, 'https://x.com/a"b\\c', runner)
    assert '\\"b\\\\c' in calls[0][-1]
