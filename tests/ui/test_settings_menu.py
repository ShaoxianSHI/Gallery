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

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402
from waiting import wait_for_idle  # noqa: E402

from gallery import __version__  # noqa: E402
from gallery.core import paths  # noqa: E402
from gallery.core.external import apps  # noqa: E402
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
        "显示",
        "设置",
    ]


def test_the_submenus_are_grouped_in_a_stable_order(window: MainWindow) -> None:
    titles = [
        action.text() for action in _settings_menu(window).actions() if action.menu() is not None
    ]

    assert titles == ["主题", "背景", "动画速度", "放映间隔", "影片网站", "缓存上限"]


def test_the_menu_reads_top_to_bottom_in_three_groups(window: MainWindow) -> None:
    """**每一段都要能一句话念出来**：显示与播放 / 影片与缓存 / 维护。

    分隔线只有三根，意义全在「同段里的东西互不相干却归同一类」这件事上 —— 所以这里连
    分隔线的位置一起锁住，挪动一项就会红。
    """
    labels = [action.text() for action in _settings_menu(window).actions()]

    assert labels == [
        "主题",
        "背景",
        "动画速度",
        "放映间隔",
        "影片网站",
        "外部应用顺序…",
        "启动时打开上次的库",
        "",
        "缓存上限",
        "清空缩略图缓存",
        "",
        "打开日志文件夹",
        "关于 Gallery",
    ]


def test_clearing_the_cache_sits_next_to_its_limit(window: MainWindow) -> None:
    """预算与清扫管的是同一批文件，**分居两张顶层菜单就谁也找不到谁**。"""
    assert window._clear_cache_action in _settings_menu(window).actions()
    file_menu = window.menuBar().actions()[0].menu()
    assert window._clear_cache_action not in file_menu.actions()


def test_the_backdrop_commands_live_under_background(window: MainWindow) -> None:
    """主题与背景是两件事：背景那几项不该再挂在「主题」里面。"""
    theme_menu = next(
        action.menu() for action in _settings_menu(window).actions() if action.text() == "主题"
    )
    background_menu = next(
        action.menu() for action in _settings_menu(window).actions() if action.text() == "背景"
    )

    assert [action.text() for action in theme_menu.actions()] == ["跟随系统", "浅色", "深色"]
    assert "管理背景…" not in [action.text() for action in theme_menu.actions()]
    assert "管理背景…" in [action.text() for action in background_menu.actions()]


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


# ---- 背景：两档自定义背景色（ADR 0097）---------------------------------------


def _fake_background_dialog(
    monkeypatch: pytest.MonkeyPatch,
    values: tuple[str | None, str | None],
    *,
    accepted: bool = True,
) -> dict[str, object]:
    """把对话框换成一个按剧本返回答案的桩，并记下窗口递给它的初始两档。"""
    seen: dict[str, object] = {}

    class Fake:
        def __init__(
            self,
            parent: object,
            *,
            light: str | None = None,
            dark: str | None = None,
        ) -> None:
            seen["initial"] = (light, dark)

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def values(self) -> tuple[str | None, str | None]:
            return values

    monkeypatch.setattr(settings_flow, "BackgroundColorDialog", Fake)
    return seen


def _background_submenu(window: MainWindow):
    action = next(one for one in _settings_menu(window).actions() if one.text() == "背景")
    return action.menu()


def test_the_background_menu_lists_the_slots_before_how_to_fill_them(window: MainWindow) -> None:
    """先答「这一档用哪张」，再答「纯色用哪一种」，最后才是造一张新的（ADR 0097 / 0098 / 0101）。

    **下半那四项之间没有分隔线**：它们是同一件事的四种来源（纯色 / 生成 / 照片 / 管理已有的），
    再切一刀只会让「管理」看起来像另一类东西。
    """
    actions = _background_submenu(window).actions()
    labels = [action.text() for action in actions if not action.isSeparator()]

    assert labels == [
        "浅色背景",
        "深色背景",
        "设置纯色背景…",
        "生成水墨背景…",
        "使用照片背景…",
        "管理背景…",
    ]
    assert [action.isSeparator() for action in actions] == [
        False,
        False,
        True,
        False,
        False,
        False,
        False,
    ]


def test_custom_backgrounds_are_saved_and_take_effect(
    window: MainWindow,
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_background_dialog(monkeypatch, ("#f5f2ec", "#101018"))

    window._settings_extras["background"].trigger()
    qt_app.processEvents()

    assert seen["initial"] == (None, None), "第一次打开时两档都尚未挑过"
    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.background_for(dark=False) == "#f5f2ec"
    assert prefs.background_for(dark=True) == "#101018"
    # 离屏平台是浅色那一档，窗口应当当场换成挑的那一块
    assert theme.current_theme().background == QColor("#f5f2ec")
    image = window.grab().toImage()
    assert image.pixelColor(window.width() - 24, window.height() // 2) == QColor("#f5f2ec")


def test_the_dialog_is_seeded_with_what_is_stored(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_background_dialog(monkeypatch, ("#f5f2ec", "#101018"))
    window._settings_extras["background"].trigger()

    seen = _fake_background_dialog(monkeypatch, ("#f5f2ec", "#101018"), accepted=False)
    window._settings_extras["background"].trigger()

    assert seen["initial"] == ("#f5f2ec", "#101018")


def test_reset_brings_the_system_colour_back(
    window: MainWindow,
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = theme.current_theme().background
    _fake_background_dialog(monkeypatch, ("#f5f2ec", "#101018"))
    window._settings_extras["background"].trigger()
    qt_app.processEvents()
    assert theme.current_theme().background == QColor("#f5f2ec")

    _fake_background_dialog(monkeypatch, (None, None))
    window._settings_extras["background"].trigger()
    qt_app.processEvents()

    assert theme.current_theme().background == system
    prefs = load_session(base=tmp_path / "会话").preferences
    assert prefs.background_for(dark=False) is None
    assert prefs.background_for(dark=True) is None


def test_cancelling_the_background_dialog_changes_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = theme.current_theme().background
    _fake_background_dialog(monkeypatch, ("#f5f2ec", None), accepted=False)

    window._settings_extras["background"].trigger()

    assert theme.current_theme().background == before
    assert load_session(base=tmp_path / "会话").preferences.background_for(dark=False) is None


# ---- 外部应用顺序（ADR 0081）-----------------------------------------------


def _only_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """只装系统「日历」—— Google 那项不靠安装，永远在。"""
    monkeypatch.setattr(
        apps,
        "find_app",
        lambda app: Path("/System/Applications/Calendar.app") if app.id == "calendar" else None,
    )


def _fake_dialog(
    monkeypatch: pytest.MonkeyPatch,
    orders: dict[str, tuple[str, ...]],
    *,
    accepted: bool = True,
) -> dict[str, object]:
    """把对话框换成一个按剧本返回答案的桩，并记下窗口递给它的那四页。"""
    seen: dict[str, object] = {}

    class Fake:
        def __init__(self, parent: object, pages: object) -> None:
            seen["pages"] = tuple(pages)  # type: ignore[arg-type]

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def orders(self) -> dict[str, tuple[str, ...]]:
            return dict(orders)

    monkeypatch.setattr(settings_flow, "ExternalOrderDialog", Fake)
    return seen


def test_the_external_order_entry_sits_at_the_end_of_the_play_group(window: MainWindow) -> None:
    texts = [action.text() for action in _settings_menu(window).actions()]

    assert texts.index("外部应用顺序…") == texts.index("影片网站") + 1


def test_the_dialog_gets_four_pages_in_their_current_order(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_calendar(monkeypatch)
    seen = _fake_dialog(monkeypatch, {})

    window._settings_extras["external_order"].trigger()

    pages = seen["pages"]
    assert [page.label for page in pages] == ["图片", "视频", "网格", "日历"]  # type: ignore[attr-defined]
    calendar = next(page for page in pages if page.page == "calendar")  # type: ignore[attr-defined]
    assert [item.id for item in calendar.items] == ["apple_calendar", "google_calendar"]
    assert calendar.builtin == ("apple_calendar", "google_calendar")


def test_the_image_page_lists_the_builtin_order_it_will_fall_back_to(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图片页的内置次序：预览、Picview、ImageGlass、地图、Google 地图、访达、QSpace（ADR 0094）。"""
    monkeypatch.setattr(
        apps,
        "find_app",
        lambda app: (
            Path(f"/Applications/{app.name}.app")
            if app.id in {"preview", "picview", "imageglass", "maps", "qspace"}
            else None
        ),
    )
    seen = _fake_dialog(monkeypatch, {})

    window._settings_extras["external_order"].trigger()

    detail = next(
        page
        for page in seen["pages"]
        if page.page == "detail"  # type: ignore[attr-defined]
    )
    assert [item.id for item in detail.items] == [
        "preview",
        "picview",
        "imageglass",
        "maps",
        "google_maps",
        "finder",
        "qspace",
    ]


def test_the_dialog_only_gets_the_apps_installed_for_that_page(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装没装是 ``available`` 的事，与「现在看着哪张照片」无关。"""
    _only_calendar(monkeypatch)
    seen = _fake_dialog(monkeypatch, {})

    window._settings_extras["external_order"].trigger()

    pages = seen["pages"]
    detail = next(page for page in pages if page.page == "detail")  # type: ignore[attr-defined]
    ids = [item.id for item in detail.items]
    assert "preview" not in ids
    assert "imageglass" not in ids
    assert "finder" in ids
    assert "google_maps" in ids


def test_saving_the_dragged_order_writes_it_to_the_session(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_calendar(monkeypatch)
    _fake_dialog(monkeypatch, {"calendar": ("google_calendar", "apple_calendar")})

    window._settings_extras["external_order"].trigger()

    assert window._session.preferences.order_for("calendar") == (
        "google_calendar",
        "apple_calendar",
    )
    assert load_session(base=tmp_path / "会话").preferences.order_for("calendar") == (
        "google_calendar",
        "apple_calendar",
    )


def test_cancelling_the_dialog_writes_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_calendar(monkeypatch)
    _fake_dialog(monkeypatch, {"calendar": ("google_calendar", "apple_calendar")}, accepted=False)

    window._settings_extras["external_order"].trigger()

    assert window._session.preferences.order_for("calendar") == ()
    assert load_session(base=tmp_path / "会话").preferences.order_for("calendar") == ()


def test_an_order_equal_to_the_builtin_one_stores_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拖回原样不该留下一条「自定义」——「恢复默认」也就不会亮着。"""
    _only_calendar(monkeypatch)
    _fake_dialog(monkeypatch, {"calendar": ("apple_calendar", "google_calendar")})

    window._settings_extras["external_order"].trigger()

    assert window._session.preferences.order_for("calendar") == ()


def test_a_partial_or_duplicated_answer_is_completed_before_saving(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对话框万一少给一项、或者给重复了，存下去仍要是一个一个不缺的排列。"""
    _only_calendar(monkeypatch)
    _fake_dialog(monkeypatch, {"calendar": ("google_calendar", "google_calendar")})

    window._settings_extras["external_order"].trigger()

    assert window._session.preferences.order_for("calendar") == (
        "google_calendar",
        "apple_calendar",
    )


def test_an_unknown_id_from_the_dialog_is_dropped(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_calendar(monkeypatch)
    _fake_dialog(monkeypatch, {"calendar": ("不存在", "google_calendar")})

    window._settings_extras["external_order"].trigger()

    assert window._session.preferences.order_for("calendar") == (
        "google_calendar",
        "apple_calendar",
    )
