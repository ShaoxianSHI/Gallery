"""背景图接进窗口：存下来、挂到某一档、窗口真的画出来、删掉之后退回背景色。

**两档是分开的**：给浅色那一档挂一张不该影响深色；而「哪一档生效」仍由系统决定（ADR 0097）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from gallery.core.backdrops import PALETTES  # noqa: E402
from gallery.core.library.session import load_session  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window import settings_flow  # noqa: E402


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    instance = MainWindow(session_base=tmp_path / "会话", cache_base=tmp_path / "缓存")
    instance.resize(900, 600)
    instance.show()
    yield instance
    instance.close()


def _fake_generate_dialog(
    monkeypatch: pytest.MonkeyPatch, *, name: str, outcome: str
) -> list[dict[str, object]]:
    """把生成对话框换成一个按剧本交出配方与名字的桩；返回值记下每次被问到的种子。"""
    seen: list[dict[str, object]] = []
    recipe = PALETTES[0].recipe(5)

    class Fake:
        def __init__(self, parent: object, *, seed: int) -> None:
            seen.append({"seed": seed})

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def recipe(self) -> object:
            return recipe

        def name(self) -> str:
            return name

        def outcome(self) -> str:
            return outcome

    monkeypatch.setattr(settings_flow, "BackdropGenerateDialog", Fake)
    return seen


def _solid_photo(tmp_path: Path, color: tuple[int, int, int]) -> Path:
    path = tmp_path / "照片.png"
    Image.new("RGB", (800, 500), color).save(path)
    return path


def _corner(window: MainWindow) -> QColor:
    return window.grab().toImage().pixelColor(window.width() - 24, window.height() // 2)


def test_generating_saves_it_and_switches_the_light_tier_over(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「设为浅色」= 存进缓存 + 存进清单 + 这一档用上它。"""
    seen = _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="light")

    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()

    assert seen, "对话框没被打开"
    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.backdrop_for(dark=False) != ""
    assert prefs.backdrop_for(dark=True) == ""
    item = window._backdrops.store.get(prefs.backdrop_for(dark=False))
    assert item is not None and item.name == "雾蓝"
    assert window._backdrops.store.path_of(item).is_file()


def test_saving_without_assigning_leaves_both_tiers_alone(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="save")

    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()

    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.backdrop_for(dark=False) == ""
    assert prefs.backdrop_for(dark=True) == ""
    assert [entry.name for entry in window._backdrops.store.items()] == ["雾蓝"]


def test_the_saved_name_shows_up_under_both_tiers(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="save")
    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()

    for dark in (False, True):
        labels = [action.text() for action in window._backdrops.menus[dark].actions()]
        assert labels == ["使用纯色", "雾蓝"]


def test_assigning_from_the_submenu_takes_effect(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="save")
    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()
    item = window._backdrops.store.items()[0]

    window._backdrops.actions[True][item.id].trigger()
    qt_app.processEvents()

    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.backdrop_for(dark=True) == item.id
    assert prefs.backdrop_for(dark=False) == ""
    assert window._backdrops.actions[True][item.id].isChecked()
    # 横幅要说是**哪一档**换成了什么 —— 「这一档」离开菜单就不知道指谁。
    assert window._banner.text() == "深色背景已设为「雾蓝」。"


def test_going_back_to_the_colour_clears_that_tier(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="light")
    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()
    assert theme.ensure_installed().backdrop_pixmap() is not None

    window._backdrops.actions[False][""].trigger()
    qt_app.processEvents()

    assert load_session(base=tmp_path / "会话").preferences.backdrop_for(dark=False) == ""
    assert theme.ensure_installed().backdrop_pixmap() is None
    assert window._banner.text() == "浅色背景已改为纯色。"


def test_a_light_photo_is_painted_as_is(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """图本来就够淡，遮罩是 0 —— 画面里那颗像素就应该是照片自己的颜色。"""
    source = _solid_photo(tmp_path, (245, 240, 230))
    item = window._backdrops.store.add_photo(source, "素色")

    window._assign_backdrop(False, item.id)
    qt_app.processEvents()

    painted = _corner(window)
    assert abs(painted.red() - 245) <= 6
    assert abs(painted.green() - 240) <= 6
    assert abs(painted.blue() - 230) <= 6


def test_a_dark_photo_is_washed_out_in_the_light_tier(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    """**只换底不换字**：深色照片放进浅色那一档必须被压淡，否则正文看不清（ADR 0097）。"""
    source = _solid_photo(tmp_path, (20, 20, 30))
    item = window._backdrops.store.add_photo(source, "暗色")

    window._assign_backdrop(False, item.id)
    qt_app.processEvents()

    assert _corner(window).lightness() > 150


def test_deleting_a_backdrop_drops_the_reference(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_generate_dialog(monkeypatch, name="雾蓝", outcome="light")
    window._settings_extras["backdrop_generate"].trigger()
    qt_app.processEvents()
    item = window._backdrops.store.items()[0]

    window._backdrops.store.remove(item.id)
    window._drop_missing_backdrops()
    window._rebuild_backdrop_menus()
    window._apply_backdrops()

    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.backdrop_for(dark=False) == ""
    assert [action.text() for action in window._backdrops.menus[False].actions()] == [
        "使用纯色",
        "暂无已保存的背景图",
    ]
