"""走哪条路、什么时候降级（全部塞进假 Runner，不启动任何真程序）。"""

from __future__ import annotations

import datetime
import plistlib
from collections.abc import Sequence
from pathlib import Path

from gallery.core.external.gcal import HOST
from gallery.core.external.launch import (
    WHERE_APP,
    WHERE_BROWSER,
    open_google_calendar,
    open_in_app,
    open_in_google_maps,
    open_in_maps,
)
from gallery.core.external.pwa import SHORTCUT_URL_KEY
from gallery.core.external.runner import CommandResult

CALENDAR_WINDOW = f"W2\nN\nThttps://{HOST}/calendar/u/0/r\n"


class FakeRunner:
    """按命令的形状给答案，并把每次调用记下来。

    **认的是 argv 的形状而不是顺序** —— 真实实现里的先后本来就是被测的东西，
    把顺序也编码进桩里，这条测试就看不出「是不是先 pgrep 再枚举」了。
    """

    def __init__(
        self,
        *,
        chrome_up: bool = True,
        windows: str = CALENDAR_WINDOW,
        open_app_rc: int = 0,
        open_url_rc: int = 0,
        set_rc: int = 0,
    ) -> None:
        self.chrome_up = chrome_up
        self.windows = windows
        #: ``open -a <应用>`` 与 ``open <链接>`` 是两件事，失败也要分开算 ——
        #: 一起设的话「应用起不来、浏览器也开不了」会被误判成「降级成功了」。
        self.open_app_rc = open_app_rc
        self.open_url_rc = open_url_rc
        self.set_rc = set_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], *, timeout: float | None = None) -> CommandResult:
        del timeout
        call = list(argv)
        self.calls.append(call)
        joined = " ".join(call)
        if call[0] == "pgrep":
            return CommandResult(0 if self.chrome_up else 1)
        if call[0] == "osascript":
            if "set URL of tab" in joined:
                return CommandResult(self.set_rc)
            return CommandResult(0, self.windows)
        if call[0] == "open":
            return CommandResult(self.open_app_rc if "-a" in call else self.open_url_rc)
        return CommandResult(127, stderr=f"没预期的命令：{joined}")


class FakeClock:
    """拨得很快的时钟：``now`` 每次前进一秒，于是超时那条路一秒就走完，不必真睡。"""

    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        current = self.value
        self.value += 1.0
        return current

    def sleep(self, seconds: float) -> None:
        del seconds


def make_installed_pwa(tmp_path: Path) -> tuple[Path, Path]:
    """造一个「装了 PWA」的搜索目录，并返回（目录, 应用包）。"""
    apps = tmp_path / "Chrome Apps.localized"
    app = apps / "Google 日历.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CrAppModeShortcutURL": f"https://{HOST}/calendar/r"})
    )
    return apps, app


DAY = datetime.date(2026, 6, 29)


def test_installed_pwa_opens_in_the_app(tmp_path: Path) -> None:
    apps, app = make_installed_pwa(tmp_path)
    runner = FakeRunner()
    outcome = open_google_calendar(
        "week", DAY, runner=runner, search_dirs=[apps], sleeper=lambda _: None
    )
    assert outcome.where == WHERE_APP
    assert outcome.detail is None
    assert ["open", "-a", str(app)] in runner.calls
    assert any("set URL of tab 1 of window 2" in " ".join(c) for c in runner.calls)


def test_without_pwa_it_opens_the_browser(tmp_path: Path) -> None:
    """没装就走浏览器，**只调一次 ``open <url>``**，不碰 PWA 那三步。"""
    runner = FakeRunner()
    outcome = open_google_calendar(
        "week", DAY, runner=runner, search_dirs=[tmp_path / "空的"], sleeper=lambda _: None
    )
    assert outcome.where == WHERE_BROWSER
    assert runner.calls == [["open", f"https://{HOST}/calendar/u/0/r/week/2026/6/29"]]


def test_browser_is_used_when_the_app_will_not_start(tmp_path: Path) -> None:
    apps, _ = make_installed_pwa(tmp_path)
    runner = FakeRunner(open_app_rc=1)
    outcome = open_google_calendar(
        "week", DAY, runner=runner, search_dirs=[apps], sleeper=lambda _: None
    )
    assert outcome.where == WHERE_BROWSER
    assert outcome.detail == "没能启动那个应用，已改用浏览器打开"
    # **降级必须真的去开浏览器**：只改一个 where 的话，横幅会说「已改用浏览器打开」
    # 而浏览器压根没被叫起来。
    assert ["open", f"https://{HOST}/calendar/u/0/r/week/2026/6/29"] in runner.calls


def test_browser_is_used_when_the_window_is_never_recognized(tmp_path: Path) -> None:
    """PWA 起来了、窗口也开了，但认不出那一个 —— 宁可开浏览器，也不要改错窗口。"""
    apps, _ = make_installed_pwa(tmp_path)
    runner = FakeRunner(windows="W1\nN普通窗口\nThttps://example.com/\n")
    clock = FakeClock()
    outcome = open_google_calendar(
        "week", DAY, runner=runner, search_dirs=[apps], sleeper=clock.sleep, now=clock.now
    )
    assert outcome.where == WHERE_BROWSER
    assert outcome.detail == "没认出那个应用的窗口，已改用浏览器打开"


def test_browser_is_used_when_the_url_cannot_be_written(tmp_path: Path) -> None:
    apps, _ = make_installed_pwa(tmp_path)
    runner = FakeRunner(set_rc=1)
    outcome = open_google_calendar(
        "week", DAY, runner=runner, search_dirs=[apps], sleeper=lambda _: None
    )
    assert outcome.where == WHERE_BROWSER
    assert outcome.detail == "没能改写那个窗口的地址，已改用浏览器打开"


def test_nothing_sleeps_when_everything_is_ready(tmp_path: Path) -> None:
    """**等的是条件不是秒数**：一路都通的话一次都不该睡。"""
    apps, _ = make_installed_pwa(tmp_path)
    slept: list[float] = []
    open_google_calendar("week", DAY, runner=FakeRunner(), search_dirs=[apps], sleeper=slept.append)
    assert slept == []


def test_apple_script_is_never_used_to_probe_the_browser(tmp_path: Path) -> None:
    """探测浏览器在不在**只能用 pgrep** —— AppleScript 会把浏览器启动一遍。"""
    apps, _ = make_installed_pwa(tmp_path)
    runner = FakeRunner()
    open_google_calendar("week", DAY, runner=runner, search_dirs=[apps], sleeper=lambda _: None)
    first_osa = next(c for c in runner.calls if c[0] == "osascript")
    assert any(c[0] == "pgrep" for c in runner.calls)
    assert runner.calls.index(next(c for c in runner.calls if c[0] == "pgrep")) < (
        runner.calls.index(first_osa)
    )


def test_year_view_reaches_the_app(tmp_path: Path) -> None:
    apps, _ = make_installed_pwa(tmp_path)
    runner = FakeRunner()
    open_google_calendar("year", DAY, runner=runner, search_dirs=[apps], sleeper=lambda _: None)
    written = next(" ".join(c) for c in runner.calls if "set URL of tab" in " ".join(c))
    assert '/r/year/2026"' in written


def test_open_in_app_waits_for_a_browser_that_is_not_there_yet(tmp_path: Path) -> None:
    """浏览器冷启动要几秒：第一轮没起来不该立刻放弃。"""
    clock = FakeClock()
    answers = [False, False, True]

    class SlowRunner(FakeRunner):
        def __call__(self, argv: Sequence[str], *, timeout: float | None = None):
            if list(argv)[0] == "pgrep":
                self.chrome_up = answers.pop(0) if answers else True
            return super().__call__(argv, timeout=timeout)

    outcome = open_in_app(
        tmp_path / "随便.app",
        f"https://{HOST}/calendar/r",
        runner=SlowRunner(),
        sleeper=clock.sleep,
        now=clock.now,
    )
    assert outcome.where == WHERE_APP


def test_a_browser_that_never_starts_falls_back() -> None:
    clock = FakeClock()
    outcome = open_in_app(
        Path("/tmp/不存在的.app"),
        f"https://{HOST}/calendar/r",
        runner=FakeRunner(chrome_up=False),
        sleeper=clock.sleep,
        now=clock.now,
    )
    assert outcome.where == WHERE_BROWSER
    assert outcome.detail == "等不到浏览器启动，已改用浏览器打开"


def test_maps_is_opened_with_one_open_call() -> None:
    """**只起一个 ``open`` 进程**：系统「地图」认 ``maps://``，不需要轮询窗口。"""
    runner = FakeRunner()
    outcome = open_in_maps(39.908722, 116.397499, runner=runner)
    assert runner.calls == [["open", "maps://?ll=39.908722,116.397499&q=39.908722,116.397499"]]
    assert outcome.where == WHERE_APP
    assert outcome.detail is None


def test_a_missing_maps_app_says_what_to_do() -> None:
    runner = FakeRunner(open_url_rc=1)
    outcome = open_in_maps(39.9, 116.4, runner=runner)
    assert outcome.detail == "没能打开地图，请确认系统「地图」还在"


def test_google_maps_goes_through_the_browser() -> None:
    """Mac 上没有 Google 地图的应用，于是永远只有浏览器这一条路。"""
    runner = FakeRunner()
    outcome = open_in_google_maps(48.8584, 2.2945, runner=runner)
    assert runner.calls == [
        ["open", "https://www.google.com/maps/search/?api=1&query=48.858400,2.294500"]
    ]
    assert outcome.where == WHERE_BROWSER
    assert outcome.detail is None


def test_shortcut_key_is_the_one_detection_reads() -> None:
    """检测认的是 plist 里这一项 —— 改它名字就等于让 PWA 认不出来（ADR 0034 决策 2）。"""
    assert SHORTCUT_URL_KEY == "CrAppModeShortcutURL"
