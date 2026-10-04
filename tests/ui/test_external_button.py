"""标题条上那个「用外部应用打开」入口：摆位、下拉框里那几项、以及它跟着页面出现。

**为什么这些要单独测**：按钮只有日历页上出现，而日历页要一个真的库才进得去 ——
于是「它该不该出现」这条规矩很容易只靠手点验。摆位与下拉框那几条更不该为此造窗口。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until  # noqa: E402

from gallery.core.external import launch  # noqa: E402
from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.ui.external_button import (  # noqa: E402
    BUTTON_GAP,
    BUTTON_SIZE,
    ExternalAppsButton,
    ExternalEntry,
    button_rect,
    calendar_icon,
)
from gallery.ui.main_window import MainWindow  # noqa: E402

SEARCH_X = 800


def _set_anchor(window: MainWindow, day: datetime.date) -> None:
    """把日历那一页的锚点摆到某一天。

    **``CalendarPage`` 没有 ``set_anchor``**：它是用户翻页翻到的那个位置，只能
    ``step`` / ``go_today`` 相对地走（``set_anchor`` 在各层那一份上）。而这里要测的
    只是「打开时读到的是不是这一天」，直接写那个私有字段最省事 —— 测试摸私有成员
    是本仓库的惯例（见 ``main_window`` 顶部那段说明）。
    """
    window._calendar_page._anchor = day


# ---- 摆位（纯算术）---------------------------------------------------


def test_button_sits_left_of_the_search_field_with_a_gap() -> None:
    rect = button_rect(SEARCH_X, 40)
    # 用 ``left() + width()`` 而不是 ``right()``：后者算的是**最后一个像素的坐标**，
    # 与下一段的起点一比就差一。
    assert rect.left() + rect.width() + BUTTON_GAP == SEARCH_X
    assert rect.width() == BUTTON_SIZE


def test_button_is_square() -> None:
    assert button_rect(SEARCH_X, 40).height() == BUTTON_SIZE


def test_button_is_centred_in_the_strip() -> None:
    """两个不同高的东西并排站，中线对齐才像同一行里的。

    **不比 ``center()``**：Qt 那个点落在格子坐标上（22 高的矩形报出来是 19 而不是 20），
    「上下留白一样多」才是这里要说的那件事。
    """
    strip = 40
    rect = button_rect(SEARCH_X, strip)
    assert rect.y() * 2 + rect.height() == strip


# ---- 下拉框（不必造窗口）-----------------------------------------------


def _entry(entry_id: str, name: str, enabled: bool) -> ExternalEntry:
    return ExternalEntry(id=entry_id, name=name, icon=None, enabled=lambda: enabled)


def _menu_texts(button: ExternalAppsButton) -> list[str]:
    menu = button.menu()
    assert menu is not None
    return [action.text() for action in menu.actions()]


def test_menu_lists_the_full_name(qt_app: QApplication) -> None:
    button = ExternalAppsButton()
    button.set_entries([_entry("google_calendar", "Google 日历", True)])
    assert _menu_texts(button) == ["Google 日历"]


def test_menu_carries_an_icon(qt_app: QApplication) -> None:
    """下拉框里是**图标 + 全称**：只有图标的话用户得一个个点过去才知道会发生什么。"""
    button = ExternalAppsButton()
    button.set_entries([_entry("google_calendar", "Google 日历", True)])
    menu = button.menu()
    assert menu is not None
    action = menu.actions()[0]
    assert isinstance(action.icon(), QIcon)
    assert not action.icon().isNull()


def test_the_button_itself_shows_only_the_icon(qt_app: QApplication) -> None:
    button = ExternalAppsButton()
    assert button.toolButtonStyle().name == "ToolButtonIconOnly"


def test_unavailable_entries_are_left_out(qt_app: QApplication) -> None:
    """此刻没东西可开的那一项**整条不出现**，而不是摆一个点下去没反应的灰条目。"""
    button = ExternalAppsButton()
    button.set_entries(
        [
            _entry("google_calendar", "Google 日历", True),
            _entry("finder", "访达", False),
        ]
    )
    assert _menu_texts(button) == ["Google 日历"]


def test_refresh_reasks_availability(qt_app: QApplication) -> None:
    """翻页要重新问一遍 —— 判据是活的，不是建菜单那一刻定死的。"""
    state = {"on": False}
    entry = ExternalEntry(id="x", name="某应用", icon=None, enabled=lambda: state["on"])
    button = ExternalAppsButton()
    button.set_entries([entry])
    assert _menu_texts(button) == []
    state["on"] = True
    button.refresh()
    assert _menu_texts(button) == ["某应用"]


def test_choosing_an_entry_reports_which(qt_app: QApplication) -> None:
    """点下去只报「选了哪一项」，具体做什么在外面按 id 分派。"""
    entry = _entry("google_calendar", "Google 日历", True)
    button = ExternalAppsButton()
    button.set_entries([entry])
    seen: list[object] = []
    button.entry_activated.connect(seen.append)
    menu = button.menu()
    assert menu is not None
    menu.actions()[0].trigger()
    assert seen == [entry]


def test_calendar_icon_is_never_null(qt_app: QApplication) -> None:
    """装了 PWA 用它自己的图标，没装就画一个 —— 两种情况下都得有个图标。"""
    assert not calendar_icon().isNull()


# ---- 装进窗口 ---------------------------------------------------------


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "照片"
    root.mkdir()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(root / "一张.jpg")
    prepare_library(root)
    instance = MainWindow(session_base=tmp_path / "状态", initial_library=root)
    # **要真的 show 出来**：子控件的 ``isVisible()`` 说的是「自己和祖先都可见」，
    # 窗口没显示时它一律是 False —— 那不是「按钮没摆出来」。
    instance.show()
    assert wait_until(
        qt_app, lambda: instance._scan_job is None and instance._model.rowCount() > 0
    ), "扫描没有在时限内完成"
    yield instance
    instance.close()


def test_button_is_hidden_on_the_grid_page(window: MainWindow) -> None:
    """日历那一页有「哪一层、哪一天」可言，网格页没有 —— 于是它整条不出现。"""
    assert window._stack.currentWidget() is window._grid_page
    assert window._external_button.isVisible() is False


def test_button_appears_on_the_calendar_page(window: MainWindow) -> None:
    window._enter_calendar()
    window.show()
    assert window._external_button.isVisible() is window._owns_title_bar


def test_button_is_placed_left_of_the_search_field(window: MainWindow) -> None:
    window._enter_calendar()
    window.show()
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    button = window._external_button.geometry()
    field = window._search_field.geometry()
    assert button.left() + button.width() < field.left()
    assert field.left() - (button.left() + button.width()) == BUTTON_GAP


def test_title_text_gives_room_for_the_button(window: MainWindow) -> None:
    """按钮摆出来之后，标题文字要给它也让出来 —— 否则长相册名从它底下穿过去。"""
    window._enter_calendar()
    window.show()
    if not window._owns_title_bar:
        pytest.skip("这一条标题条在本平台上不存在（只有 macOS 有）")
    with_button = window._title_bar._right_reserved
    window._stack.setCurrentWidget(window._grid_page)
    window._update_action_states()
    without = window._title_bar._right_reserved
    assert with_button - without == BUTTON_SIZE + BUTTON_GAP


def test_calendar_entry_carries_the_current_level_and_day(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打开的必须是**当前那一页**：哪一层、哪一天，一样都不差。"""
    seen: list[tuple[str, datetime.date]] = []

    def fake(level: str, day: datetime.date, **_kwargs: object) -> launch.OpenOutcome:
        seen.append((level, day))
        return launch.OpenOutcome(target_id="google_calendar", where=launch.WHERE_BROWSER)

    monkeypatch.setattr(launch, "open_google_calendar", fake)
    window._enter_calendar()
    window._calendar_page.set_level("week")
    _set_anchor(window, datetime.date(2026, 6, 29))
    window._open_google_calendar()
    wait_until(QApplication.instance(), lambda: bool(seen))
    assert seen == [("week", datetime.date(2026, 6, 29))]


def test_selected_day_wins_over_the_anchor(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**有选中就用选中的那一天** ——「年视图点 5 月 16 号 → 打开日历」看到的是那天。"""
    seen: list[tuple[str, datetime.date]] = []

    def fake(level: str, day: datetime.date, **_kwargs: object) -> launch.OpenOutcome:
        seen.append((level, day))
        return launch.OpenOutcome(target_id="google_calendar", where=launch.WHERE_APP)

    monkeypatch.setattr(launch, "open_google_calendar", fake)
    window._enter_calendar()
    _set_anchor(window, datetime.date(2026, 6, 1))
    window._calendar_page.set_selected(datetime.date(2026, 3, 12))
    window._open_google_calendar()
    wait_until(QApplication.instance(), lambda: bool(seen))
    assert seen == [("month", datetime.date(2026, 3, 12))]
