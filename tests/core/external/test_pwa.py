"""本机装没装那个 PWA —— 用临时目录现场造应用包，不读本机真身。"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from gallery.core.external import pwa
from gallery.core.external.gcal import HOST
from gallery.core.external.pwa import ICON_RELATIVE, app_icon, find_chrome_pwa


def make_app(
    apps_dir: Path,
    name: str,
    *,
    shortcut_url: str | None = None,
    icon: bool = False,
) -> Path:
    """在 ``apps_dir`` 下造一个「装好的 PWA」应用包。

    **plist 用 :mod:`plistlib` 写**：那是标准库，与 macOS 上 ``plutil`` 写出来的
    XML plist 是同一个格式，而读的那一侧也是它 —— 于是这个测试不依赖本机装了
    任何命令行工具。
    """
    app = apps_dir / f"{name}.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    info: dict[str, object] = {"CFBundleIdentifier": f"com.test.{name}"}
    if shortcut_url is not None:
        info["CrAppModeShortcutURL"] = shortcut_url
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    if icon:
        (app / ICON_RELATIVE).write_bytes(b"\x00icon")
    return app


def test_finds_the_calendar_pwa(tmp_path: Path) -> None:
    apps = tmp_path / "Chrome Apps.localized"
    make_app(apps, "其他", shortcut_url="https://example.com/")
    wanted = make_app(apps, "Google 日历", shortcut_url=f"https://{HOST}/calendar/r")
    assert find_chrome_pwa(HOST, search_dirs=[apps]) == wanted


def test_localized_name_does_not_matter(tmp_path: Path) -> None:
    """**包的名字是本地化的**（本机叫「Google 日历」，英文系统上是 ``Google Calendar``）。

    所以判据是它自己的启动地址，不是名字 —— 这条锁定「改名/换语言之后照样认得出」。
    """
    apps = tmp_path / "Chrome Apps.localized"
    make_app(apps, "Google Calendar", shortcut_url=f"https://{HOST}/calendar/r")
    assert find_chrome_pwa(HOST, search_dirs=[apps]) is not None


def test_nothing_installed(tmp_path: Path) -> None:
    apps = tmp_path / "Chrome Apps.localized"
    apps.mkdir()
    assert find_chrome_pwa(HOST, search_dirs=[apps]) is None


def test_other_site_is_not_a_match(tmp_path: Path) -> None:
    apps = tmp_path / "Chrome Apps.localized"
    make_app(apps, "YouTube", shortcut_url="https://www.youtube.com/")
    assert find_chrome_pwa(HOST, search_dirs=[apps]) is None


def test_missing_search_dir_is_silent(tmp_path: Path) -> None:
    """扫一个不存在的目录只是「没找到」，不是错误。"""
    assert find_chrome_pwa(HOST, search_dirs=[tmp_path / "没有这个目录"]) is None


def test_damaged_package_is_skipped(tmp_path: Path) -> None:
    """plist 损坏 / 不是 PWA 的包跳过去继续扫 —— 那个目录里还躺着别的东西。"""
    apps = tmp_path / "Chrome Apps.localized"
    apps.mkdir()
    (apps / "坏包.app").mkdir()
    make_app(apps, "没有地址的", shortcut_url=None)
    make_app(apps, "Google 日历", shortcut_url=f"https://{HOST}/calendar/r")
    assert find_chrome_pwa(HOST, search_dirs=[apps]) is not None


def test_plist_without_the_key_is_not_a_match(tmp_path: Path) -> None:
    apps = tmp_path / "Chrome Apps.localized"
    make_app(apps, "普通应用", shortcut_url=None)
    assert find_chrome_pwa(HOST, search_dirs=[apps]) is None


def test_icon_path_is_the_bundle_one(tmp_path: Path) -> None:
    """**图标用应用包自带的那一份**：那是它自己的图标，永远是最新的，
    而我们不往仓库里放第三方商标素材（ADR 0034 决策 6）。"""
    apps = tmp_path / "Chrome Apps.localized"
    with_icon = make_app(apps, "有图标", icon=True)
    without = make_app(apps, "没图标", icon=False)
    assert app_icon(with_icon) == with_icon / ICON_RELATIVE
    assert app_icon(without) is None


# ---- 访达那个图标：从系统里读，读不到就说读不到 -------------------------


def test_finder_icon_is_missing_when_the_system_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**非 macOS（或者那个文件被挪走）时返回 ``None``**，而不是一个不存在的路径 ——
    界面那一层靠这个 ``None`` 决定要不要退回自画的轮廓。"""
    monkeypatch.setattr(pwa, "FINDER_ICON", tmp_path / "没有这个文件.icns")
    assert pwa.finder_icon() is None


def test_finder_icon_points_at_the_system_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """系统里有就原样给出来 —— **读的是系统那一份，仓库里不放**（ADR 0034 决策 6）。"""
    fake = tmp_path / "Finder.icns"
    fake.write_bytes(b"")
    monkeypatch.setattr(pwa, "FINDER_ICON", fake)
    assert pwa.finder_icon() == fake
