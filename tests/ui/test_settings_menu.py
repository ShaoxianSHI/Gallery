"""「设置」菜单：两组存储、各自立刻生效（ADR 0073）。

应用级那几项写会话文件（换库不该重来），库级那两项经 ``Library`` 写进 ``config.toml``。
这里逐条锁定「选了之后存到哪、界面上哪一处跟着变、换库之后勾选态跟不跟」。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery import __version__  # noqa: E402
from gallery.core import paths  # noqa: E402
from gallery.core.library.config import load_config  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    Preferences,
    SessionState,
    load_session,
    save_session,
)
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.window import settings_flow  # noqa: E402

GIB = 1024**3


def _new_window(tmp_path: Path) -> MainWindow:
    instance = MainWindow(
        session_base=tmp_path / "会话",
        cache_base=tmp_path / "缓存",
    )
    instance.resize(900, 600)
    instance.show()
    return instance


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    instance = _new_window(tmp_path)
    yield instance
    instance.close()


def _library(window: MainWindow, qt_app: QApplication, root: Path) -> None:
    root.mkdir(exist_ok=True)
    assert window.open_library(root) is True
    wait_for_idle(qt_app, window)


def _trigger(window: MainWindow, group: str, value: object) -> None:
    window._settings_actions[group][value].trigger()


def _custom_action(window: MainWindow):
    return next(
        action
        for action in window._settings_actions["movie_site"].values()
        if action.text() == "自定义…"
    )


def _settings_menu(window: MainWindow):
    return window.menuBar().actions()[-1].menu()


def test_settings_is_the_last_top_level_menu(window: MainWindow) -> None:
    assert [action.text() for action in window.menuBar().actions()] == [
        "文件",
        "编辑",
        "视图",
        "设置",
    ]


def test_the_submenus_are_grouped_in_a_stable_order(window: MainWindow) -> None:
    titles = [
        action.text() for action in _settings_menu(window).actions() if action.menu() is not None
    ]

    assert titles == ["动画速度", "外观", "影片网站", "缓存上限", "放映间隔"]


def test_medium_is_checked_by_default(window: MainWindow) -> None:
    assert window._settings_actions["animation"]["normal"].isChecked()
    assert window._settings_extras["open_last_library"].isChecked()


def test_slow_animation_reaches_both_pages_and_is_saved(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    _trigger(window, "animation", "slow")

    assert window._grid._transition_duration_ms() == 256
    assert window._grid._pinch_steps.min_interval_ms == 256
    assert window._detail._animation.duration() == 352
    assert window._detail._fade_animation.duration() == 720
    assert window._settings_actions["animation"]["slow"].isChecked()
    assert load_session(base=tmp_path / "会话").preferences.animation_speed == "slow"


def test_off_animation_makes_a_size_step_land_without_a_transition(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    root = tmp_path / "照片"
    root.mkdir()
    make_photo("一张.jpg", directory=root)
    _library(window, qt_app, root)
    _trigger(window, "animation", "off")

    window.set_thumbnail_size(2)

    assert window._grid._transition_duration_ms() == 0
    assert window._grid.is_transitioning is False


def test_a_slow_preference_applies_at_startup(qt_app: QApplication, tmp_path: Path) -> None:
    save_session(
        SessionState(preferences=Preferences(animation_speed="slow")),
        base=tmp_path / "会话",
    )

    window = _new_window(tmp_path)
    try:
        assert window._grid._transition_duration_ms() == 256
        assert window._detail._animation.duration() == 352
        assert window._settings_actions["animation"]["slow"].isChecked()
    finally:
        window.close()


def test_appearance_choice_is_saved_and_applied(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(theme, "apply_appearance", calls.append)

    _trigger(window, "appearance", "dark")

    assert calls == ["dark"]
    assert load_session(base=tmp_path / "会话").preferences.appearance == "dark"
    assert window._settings_actions["appearance"]["dark"].isChecked()


def test_a_movie_site_preset_writes_the_library_config(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "照片"
    _library(window, qt_app, root)

    _trigger(window, "movie_site", "https://missav.com/en")

    assert load_config(root).movie_site == "https://missav.com/en"
    assert window._settings_actions["movie_site"]["https://missav.com/en"].isChecked()


def test_a_custom_movie_site_is_checked_as_custom(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "照片"
    _library(window, qt_app, root)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("https://x.test/v", True))
    )

    _custom_action(window).trigger()

    assert load_config(root).movie_site == "https://x.test/v"
    assert _custom_action(window).isChecked()
    assert window._settings_actions["movie_site"]["https://missav.com/en"].isChecked() is False


def test_cancelling_the_custom_dialog_leaves_the_site_alone(
    window: MainWindow, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "照片"
    _library(window, qt_app, root)
    before = load_config(root).movie_site
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("https://x.test/v", False))
    )

    _custom_action(window).trigger()

    assert load_config(root).movie_site == before


def test_the_cache_limit_reaches_the_library_and_its_cache(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "照片"
    _library(window, qt_app, root)

    _trigger(window, "cache", 5 * GIB)

    assert load_config(root).max_cache_bytes == 5 * GIB
    assert window._library.thumbnails.max_bytes == 5 * GIB
    assert window._settings_actions["cache"][5 * GIB].isChecked()


def test_library_level_settings_need_an_open_library(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    assert all(not action.isEnabled() for action in window._library_setting_actions)

    _library(window, qt_app, tmp_path / "照片")

    assert all(action.isEnabled() for action in window._library_setting_actions)


def test_the_settings_follow_the_library_when_you_switch(
    window: MainWindow, qt_app: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "库一"
    second = tmp_path / "库二"
    _library(window, qt_app, first)
    _trigger(window, "movie_site", "https://jable.tv/videos")
    _library(window, qt_app, second)
    _trigger(window, "movie_site", "https://missav.com/en")

    _library(window, qt_app, first)

    assert window._settings_actions["movie_site"]["https://jable.tv/videos"].isChecked()
    assert window._settings_actions["movie_site"]["https://missav.com/en"].isChecked() is False


def test_the_slideshow_interval_is_saved_and_reaches_the_timer(
    window: MainWindow, tmp_path: Path
) -> None:
    _trigger(window, "slideshow", 8)

    assert window._slideshow_timer.interval() == 8000
    assert load_session(base=tmp_path / "会话").preferences.slideshow_seconds == 8


def test_the_open_last_library_switch_is_saved(window: MainWindow, tmp_path: Path) -> None:
    window._settings_extras["open_last_library"].trigger()

    assert load_session(base=tmp_path / "会话").preferences.open_last_library is False
    assert window._settings_extras["open_last_library"].isChecked() is False


def test_disabling_the_switch_keeps_the_welcome_page_on_the_next_start(
    qt_app: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "照片"
    root.mkdir()
    save_session(
        SessionState(
            recent_roots=(root,),
            preferences=Preferences(open_last_library=False),
        ),
        base=tmp_path / "会话",
    )

    window = _new_window(tmp_path)
    try:
        assert window._library is None
    finally:
        window.close()


def test_the_log_folder_is_opened_through_the_launcher(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(
        settings_flow.launch, "open_folder", lambda path: opened.append(path) or True
    )

    window._settings_extras["log_folder"].trigger()

    assert opened == [paths.app_log_file().parent]


def test_about_reports_the_version(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda _parent, _title, text: shown.append(text)
    )

    window._settings_extras["about"].trigger()

    assert shown and __version__ in shown[0]
