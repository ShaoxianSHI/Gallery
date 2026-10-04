"""日历那一页：数据进去、切层、翻页、快捷键。

这些用例只碰页面自己（不建 ``MainWindow``）：浏览方式、会话、缩略图流水线那些是
外层的事，在 ``tests/ui/test_main_window_*.py`` 那边。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets
from gallery.ui.calendar import CalendarPage

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

TODAY = datetime.date(2021, 9, 12)


def _photo(name: str, day: datetime.date, hour: int, minute: int = 0) -> PhotoRef:
    ref = PhotoRef(path=Path(name), size=1, mtime_ns=0)
    return ref


def _values(ref: PhotoRef, day: datetime.date, hour: int, minute: int = 0) -> PhotoFacets:
    return PhotoFacets(
        captured_at=float(day.toordinal() * 86400 + hour * 3600 + minute * 60),
        year=day.year,
        month=day.month,
        day=day.day,
        hour=hour,
        minute=minute,
    )


@pytest.fixture
def page(qt_app: QApplication) -> CalendarPage:  # noqa: ARG001 - fixture 本身返回值就是那个 App
    widget = CalendarPage()
    widget.set_today(TODAY)
    widget.go_today()  # 页面起手停在「今天」；测试里今天被指到 2021-09-12
    return widget


def press(widget: CalendarPage, key: Qt.Key) -> None:
    widget.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_the_four_shortcuts_switch_levels(page: CalendarPage) -> None:
    for key, level in (
        (Qt.Key.Key_Y, "year"),
        (Qt.Key.Key_M, "month"),
        (Qt.Key.Key_W, "week"),
        (Qt.Key.Key_D, "day"),
    ):
        press(page, key)
        assert page.level() == level
        assert page.current_view().level == level


def test_the_arrow_keys_walk_one_period(page: CalendarPage) -> None:
    page.set_level("month")
    press(page, Qt.Key.Key_Right)
    assert page.anchor() == datetime.date(2021, 10, 12)
    press(page, Qt.Key.Key_Left)
    press(page, Qt.Key.Key_Left)
    assert page.anchor() == datetime.date(2021, 8, 12)


def test_the_week_steps_by_seven_days_and_the_day_by_one(page: CalendarPage) -> None:
    page.set_level("week")
    press(page, Qt.Key.Key_Right)
    assert page.anchor() == datetime.date(2021, 9, 19)
    page.set_level("day")  # 换层不改变停在哪一天，周视图那一步仍在
    assert page.anchor() == datetime.date(2021, 9, 19)
    press(page, Qt.Key.Key_Right)
    assert page.anchor() == datetime.date(2021, 9, 20)


def test_t_goes_back_to_today(page: CalendarPage) -> None:
    page.set_level("year")
    page.step(3)
    assert page.anchor() != TODAY
    press(page, Qt.Key.Key_T)
    assert page.anchor() == TODAY


def test_an_unknown_level_is_ignored(page: CalendarPage) -> None:
    page.set_level("week")
    page.set_level("decade")
    assert page.level() == "week"


def test_photos_without_a_capture_time_do_not_show_up(page: CalendarPage) -> None:
    dated = _photo("dated.jpg", TODAY, 19)
    undated = _photo("undated.jpg", TODAY, 19)
    page.set_photos(
        [dated, undated],
        {dated.path: _values(dated, TODAY, 19, 44), undated.path: PhotoFacets()},
    )
    assert page.photo_count() == 1
    assert page.current_view().photos_of(TODAY) == [dated]


def test_a_burst_of_photos_lands_in_one_hour_cell(page: CalendarPage) -> None:
    refs = [_photo(f"burst{index}.jpg", TODAY, 19) for index in range(48)]
    values = {ref.path: _values(ref, TODAY, 19, 44) for ref in refs}
    page.set_photos(refs, values)
    page.set_level("week")
    view = page.current_view()
    assert len(view.photos_in_hour(TODAY, 19)) == 48
    assert view.photos_in_hour(TODAY, 20) == ()


def test_the_title_follows_the_level_and_the_anchor(page: CalendarPage) -> None:
    page.set_level("week")
    assert page.current_view().title() == "2021年9月6日 – 9月12日 · 第 36 周"
    page.set_level("day")
    assert page.current_view().title() == "2021年9月12日 星期日"


def test_clicking_a_day_in_the_month_view_opens_that_day(page: CalendarPage) -> None:
    ref = _photo("a.jpg", datetime.date(2021, 9, 5), 10)
    page.set_photos([ref], {ref.path: _values(ref, datetime.date(2021, 9, 5), 10)})
    page.set_level("month")
    opened: list[datetime.date] = []
    page.day_requested.connect(opened.append)  # type: ignore[arg-type]
    view = page.current_view()
    view.select_day(datetime.date(2021, 9, 5), descend=True)
    assert opened == [datetime.date(2021, 9, 5)]


def test_a_day_without_photos_does_not_open(page: CalendarPage) -> None:
    page.set_level("month")
    opened: list[datetime.date] = []
    page.day_requested.connect(opened.append)  # type: ignore[arg-type]
    page.current_view().select_day(datetime.date(2021, 9, 7), descend=True)
    assert opened == []


def test_the_thumbnail_store_accounts_for_the_edge(page: CalendarPage) -> None:
    """换层时档位变了，手里那些按旧档位缩的图不能拿来填新格子。"""
    image = QImage(40, 30, QImage.Format.Format_RGBA8888)
    image.fill(0x336699)
    ref = _photo("a.jpg", TODAY, 19)
    page.set_photos([ref], {ref.path: _values(ref, TODAY, 19)})
    page.set_level("day")  # 那一层要 192 长边
    edge = page.thumbnail_edge()
    page.set_thumbnail(ref.path, image, edge)
    assert page.current_view().photo(ref) is not None
    # 手工塞一张按别的档位缩的：视为「还没出图」
    page._thumbs.set(ref.path, image, edge + 1)  # noqa: SLF001 - 记账本身就是要测的东西
    assert page.current_view().photo(ref) is None


def test_the_level_change_is_reported_so_the_window_can_remember_it(page: CalendarPage) -> None:
    seen: list[str] = []
    page.level_changed.connect(seen.append)  # type: ignore[arg-type]
    page.set_level("day")
    page.set_level("day")  # 再设一次同一层也要报：外面靠它刷新窗口
    assert seen == ["day", "day"]


def test_window_changed_fires_when_the_level_moves(page: CalendarPage) -> None:
    """翻页 / 换层 / 滚动都要重新报一次「现在看得见哪些」。"""
    seen: list[int] = []
    page.window_changed.connect(lambda: seen.append(1))  # type: ignore[arg-type]
    page.set_level("week")
    page.step(1)
    page.go_today()
    assert len(seen) >= 3
