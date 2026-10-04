"""日历在窗口里的整条链：切到日历 → 点一格 → 网格 → 大图 → 一路退回来。

用户点名的三件事都在这里钉住：

- 日历上点**就一张**的那一格 → 大图页，退回来是**日历**（不是「全部照片」）；
- 点**好几张**的那一格 → 网格里就是那几张，再退一级才回日历；
- 退回来时**位置不动**（停在哪一周、滚到哪个小时都要还在）。

拍摄时间用假的批量来源（见 ``fake_facet_source``）：测试不依赖本机装了 exiftool。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_until, window_is_idle  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_CALENDAR,
    SessionState,
    load_session,
    save_session,
)
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 三月一张、四月两张 —— 「一格一张」与「一格多张」两种都要有。
DATES = {
    "三月一张.jpg": "2024:03:16 10:00:00",
    "四月一张.jpg": "2024:04:16 09:00:00",
    "四月两张.jpg": "2024:04:16 18:00:00",
}
NAMES = tuple(DATES)

#: 测试里的「今天」：那一天照片最多（月视图打开就看得见东西）。
APRIL = datetime.date(2024, 4, 16)


@pytest.fixture
def fake_places(monkeypatch: pytest.MonkeyPatch) -> None:
    def place_for(
        self: object, latitude: float, longitude: float, *, status: object = None
    ) -> ResolvedPlace:
        return ResolvedPlace(city="西安", region="陕西省", country="中国")

    monkeypatch.setattr(LazyPlaceResolver, "place_for", place_for)
    monkeypatch.setattr(LazyPlaceResolver, "ready", property(lambda _self: True))


@pytest.fixture
def fake_facet_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def read_many(
        self: object, paths: Sequence[Path], *, should_cancel: object = None
    ) -> Iterator[tuple[Path, Mapping[str, object] | None]]:
        for path in paths:
            raw: dict[str, object] = {}
            when = DATES.get(Path(path).name)
            if when:
                raw["EXIF:DateTimeOriginal"] = when
            yield Path(path), raw

    monkeypatch.setattr(batch.ExifToolBatchSource, "read_many", read_many, raising=True)


def _open(qt_app: QApplication, root: Path, session_base: Path) -> MainWindow:
    """开一个停在日历上的窗口。"""
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_CALENDAR),
        base=session_base,
    )
    window = MainWindow(session_base=session_base, initial_library=root)
    window.resize(900, 600)
    window.show()
    qt_app.processEvents()
    return window


@pytest.fixture
def calendar_window(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> MainWindow:
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    prepare_library(root)
    window = _open(qt_app, root, tmp_path / "会话")
    page = window._calendar_page
    # **把「今天」指到有照片的那一天**：日历一上来停在今天，而测试里的照片都在
    # 2024 年 —— 于是「这一段有几张」永远是 0，测出来的是没停对地方，不是日历坏了。
    page.set_today(APRIL)
    page.go_today()
    assert wait_until(
        qt_app,
        lambda: window_is_idle(window) and page.photo_count() > 0,
    ), "扫描或日历读拍摄时间没有收工"
    qt_app.processEvents()
    return window


def _settle(qt_app: QApplication, window: MainWindow) -> None:
    """转到「后台任务都收工」为止。"""
    assert wait_until(qt_app, lambda: window_is_idle(window)), "后台任务没有收工"
    qt_app.processEvents()


def _cell_of(window: MainWindow, month: int) -> tuple[list[object], str]:
    """月视图里那个月的某一格（有几张就交几张）。"""
    page = window._calendar_page
    page.set_today(datetime.date(2024, month, 16))
    page.go_today()
    page.set_level("month")
    day = datetime.date(2024, month, 16)
    view = page.current_view()
    photos = list(view.photos_of(day))
    assert photos, f"{day} 这一格没有照片"
    return photos, f"{month}月16日"


def test_the_calendar_starts_on_the_month(calendar_window: MainWindow) -> None:
    page = calendar_window._calendar_page
    assert calendar_window._browse_mode == BROWSE_MODE_CALENDAR
    assert calendar_window._stack.currentWidget() is page
    assert page.level() == "month"
    assert page.photo_count() == 2  # 四月那一张 + 四月那一张（��月那张不在这一屏上）


def test_one_photo_in_a_cell_opens_the_big_view_and_comes_back(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    window = calendar_window
    page = window._calendar_page
    photos, label = _cell_of(window, 3)
    anchor_before = page.anchor()  # **先落到那一格**，再记住停在哪
    assert len(photos) == 1

    page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._stack.currentWidget() is window._detail_page

    window.show_grid()
    qt_app.processEvents()
    # **回日历，而且停在原来那一段**
    assert window._stack.currentWidget() is page
    assert page.anchor() == anchor_before
    assert window._browse_mode == BROWSE_MODE_CALENDAR


def test_several_photos_in_a_cell_are_laid_out_in_a_grid(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    window = calendar_window
    page = window._calendar_page
    photos, label = _cell_of(window, 4)
    anchor_before = page.anchor()
    assert len(photos) == 2

    page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._stack.currentWidget() is window._grid_page
    assert window._model.rowCount() == 2

    # 从网格退一级才回日历
    assert window._can_go_up()
    window.go_up()
    _settle(qt_app, window)
    assert window._stack.currentWidget() is page
    assert page.anchor() == anchor_before
    # 模型回到整库：从日历再点照片还得靠它的行
    assert window._model.rowCount() == len(NAMES)


def test_a_drill_grid_shows_which_cell_it_came_from(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    window = calendar_window
    photos, label = _cell_of(window, 4)
    window._calendar_page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._drill_label == label
    assert window._drill_photos is not None


def test_escaping_out_of_the_calendar_leaves_the_browse_mode(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """日历页上按 Esc 是**退出日历这种浏览方式**（菜单里那个勾不能是假话）。"""
    window = calendar_window
    window.show_grid()
    qt_app.processEvents()
    assert window._browse_mode != BROWSE_MODE_CALENDAR
    assert window._stack.currentWidget() is window._grid_page


def test_switching_browse_mode_forgets_the_way_back(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    window = calendar_window
    photos, label = _cell_of(window, 4)
    window._calendar_page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    window.set_browse_mode("albums")
    qt_app.processEvents()
    assert window._drill_photos is None
    assert window._from_calendar is False


def test_the_week_view_sits_on_the_hour_of_its_first_photo(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """周视图起手就停在有照片的那几个小时，不是从零点开始让人自己滚。"""
    window = calendar_window
    page = window._calendar_page
    page.set_today(datetime.date(2024, 3, 16))
    page.set_level("week")
    page.go_today()
    assert wait_until(
        qt_app,
        lambda: page.current_view()._area.verticalScrollBar().value() > 0,
        timeout=2.0,
    ), "周视图没有定位到有照片的那几个小时"


def test_the_level_survives_a_reopen(
    qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]
) -> None:
    """关掉重开仍然停在原来那一层。"""
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    prepare_library(root)
    session_base = tmp_path / "会话"

    window = _open(qt_app, root, session_base)
    window._calendar_page.set_level("week")
    window._refresh_title()

    reopened = MainWindow(session_base=session_base, initial_library=root)
    try:
        assert wait_until(qt_app, lambda: window_is_idle(reopened))
        assert load_session(base=session_base).resolved_calendar_level() == "week"
        assert reopened._calendar_page.level() == "week"
    finally:
        reopened.close()
