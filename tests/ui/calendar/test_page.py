"""日历那一页：数据进去、切层、翻页、快捷键、点格下钻。

这些用例只碰页面自己（不建 ``MainWindow``）：浏览方式、会话、缩略图流水线那些是
外层的事，在 ``tests/ui/test_main_window_*.py`` 那边。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from gallery.core.library.calendar import month_matrix
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets
from gallery.ui.calendar import CalendarPage

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

TODAY = datetime.date(2021, 9, 12)


def _photo(name: str) -> PhotoRef:
    return PhotoRef(path=Path(name), size=1, mtime_ns=0)


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
def page(qt_app: QApplication) -> CalendarPage:  # noqa: ARG001 - fixture 返回值就是那个 App
    widget = CalendarPage()
    widget.set_today(TODAY)
    widget.go_today()  # 页面起手停在「今天」；测试里今天被指到 2021-09-12
    return widget


def press(widget: CalendarPage, key: Qt.Key) -> None:
    widget.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_the_three_shortcuts_switch_levels(page: CalendarPage) -> None:
    for key, level in (
        (Qt.Key.Key_Y, "year"),
        (Qt.Key.Key_M, "month"),
        (Qt.Key.Key_W, "week"),
    ):
        press(page, key)
        assert page.level() == level
        assert page.current_view().level == level


def test_there_is_no_day_shortcut(page: CalendarPage) -> None:
    """没有日视图，因此 ``D`` 不切层（否则用户会莫名其妙地进到一个不存在的层）。"""
    page.set_level("week")
    press(page, Qt.Key.Key_D)
    assert page.level() == "week"


def test_the_arrow_keys_walk_one_period(page: CalendarPage) -> None:
    page.set_level("month")
    press(page, Qt.Key.Key_Right)
    assert page.anchor() == datetime.date(2021, 10, 12)
    press(page, Qt.Key.Key_Left)
    press(page, Qt.Key.Key_Left)
    assert page.anchor() == datetime.date(2021, 8, 12)


def test_the_week_steps_by_seven_days(page: CalendarPage) -> None:
    page.set_level("week")
    press(page, Qt.Key.Key_Right)
    assert page.anchor() == datetime.date(2021, 9, 19)


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
    dated = _photo("dated.jpg")
    undated = _photo("undated.jpg")
    page.set_photos(
        [dated, undated],
        {dated.path: _values(dated, TODAY, 9), undated.path: PhotoFacets()},
    )
    assert page.photo_count() == 1
    assert page.current_view().photos_of(TODAY) == [dated]


def test_a_burst_of_photos_lands_in_one_hour_cell(page: CalendarPage) -> None:
    refs = [_photo(f"burst{index}.jpg") for index in range(48)]
    page.set_photos(refs, {ref.path: _values(ref, TODAY, 19, 44) for ref in refs})
    page.set_level("week")
    view = page.current_view()
    assert len(view.photos_in_hour(TODAY, 19)) == 48
    assert view.photos_in_hour(TODAY, 20) == ()


def test_the_title_follows_the_level_and_the_anchor(page: CalendarPage) -> None:
    page.set_level("week")
    assert page.current_view().title() == "2021年9月12日 – 9月18日 · 第 38 周"
    page.set_level("month")
    assert page.current_view().title() == "2021年9月"


def test_clicking_a_day_with_photos_asks_to_lay_that_cell_out(page: CalendarPage) -> None:
    """月视图点有照片的一天 → 把**那一天**的那几张交出去（不是切层）。"""
    refs = [_photo(f"a{index}.jpg") for index in range(3)]
    page.set_photos(refs, {ref.path: _values(ref, TODAY, 9) for ref in refs})
    page.set_level("month")
    opened: list[tuple[list[PhotoRef], str]] = []
    page.cell_requested.connect(lambda photos, label: opened.append((photos, label)))
    page.current_view().select_cell(page.current_view().photos_of(TODAY), "9月12日")
    assert opened == [(refs, "9月12日")]


def test_clicking_a_day_without_photos_does_nothing(page: CalendarPage) -> None:
    page.set_level("month")
    opened: list[tuple[list[PhotoRef], str]] = []
    page.cell_requested.connect(lambda photos, label: opened.append((photos, label)))
    page.current_view().select_cell([], "9月7日")
    assert opened == []


def test_an_hour_cell_hands_over_its_own_photos(page: CalendarPage) -> None:
    """周视图点一个小时：交出去的是**那一小时**的照片与标题（含钟点）。"""
    burst = [_photo(f"burst{index}.jpg") for index in range(48)]
    lone = _photo("lone.jpg")
    page.set_photos(
        [*burst, lone],
        {
            **{ref.path: _values(ref, TODAY, 19, 44) for ref in burst},
            lone.path: _values(lone, TODAY, 9),
        },
    )
    page.set_level("week")
    opened: list[tuple[list[PhotoRef], str]] = []
    page.cell_requested.connect(lambda photos, label: opened.append((photos, label)))
    view = page.current_view()
    view.select_cell(view.photos_in_hour(TODAY, 19), "9月12日 19:00")
    photos, label = opened[0]
    assert label == "9月12日 19:00"
    # 同一秒的一批按文件名排（burst1 排在 burst10 前面），所以比集合不比顺序
    assert {ref.path for ref in photos} == {ref.path for ref in burst}


def test_the_week_view_starts_on_sunday(page: CalendarPage) -> None:
    """一周的第一列是周日（10 月 4 日那天，周日 10-03 在它左边一格）。"""
    page.set_level("week")
    days = page.current_view().days()
    assert len(days) == 7
    assert days[0].weekday() == 6  # 周日
    assert days[-1].weekday() == 5  # 周六


def test_today_is_highlighted_once_in_the_year_view(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,  # noqa: ARG001
) -> None:
    """年视图里 10 月 4 日出现在**两张**小月里（9 月那张的末尾 + 10 月自己那张），
    但「今天」只该高亮一处 —— 否则那一屏上有两个今天。
    """
    import datetime

    from gallery.ui.calendar import year_view as year_view_module

    today = datetime.date(2026, 10, 4)
    page.set_today(today)
    page.go_today()  # 锚点跟着「今天」走，否则年视图还停在 fixture 那一年的十月
    page.set_level("year")
    view = page.current_view()
    assert view.anchor().year == 2026

    outlined: list[tuple[datetime.date, int]] = []
    original = year_view_module.outline_rounded

    def record(painter, rect, color, width, *, radius=0.0):
        outlined.append((view._anchor, radius))
        return original(painter, rect, color, width, radius=radius)

    monkeypatch.setattr(year_view_module, "outline_rounded", record)
    view.resize(1200, 800)
    view.render(QPixmap(view.size()))

    # 9 月那张小月确实也画到了这一天（补位），但高亮只有一次
    september = [day for day in month_matrix(datetime.date(2026, 9, 1)) if day == today]
    assert september, "前提不成立：补位格里没有今天"
    assert len(outlined) == 1, f"今天被高亮了 {len(outlined)} 次"


def test_the_thumbnail_store_accounts_for_the_edge(page: CalendarPage) -> None:
    """按档位各记一份：换档不等于作废，只是这一层此刻不用它。"""
    image = QImage(40, 30, QImage.Format.Format_RGBA8888)
    image.fill(0x336699)
    ref = _photo("a.jpg")
    page.set_photos([ref], {ref.path: _values(ref, TODAY, 19)})
    page.set_level("week")
    edge = page.thumbnail_edge()
    page.set_thumbnail(ref.path, image, edge)
    assert page.current_view().photo(ref) is not None

    page.set_level("month")
    other = page.thumbnail_edge()
    assert other != edge
    assert page.current_view().photo(ref) is None  # 月视图那档还没有这张

    # 翻回周视图：旧的那一份还在，不必重新解码
    page.set_level("week")
    assert page.current_view().photo(ref) is not None


def test_the_level_change_is_reported_so_the_window_can_remember_it(page: CalendarPage) -> None:
    seen: list[str] = []
    page.level_changed.connect(seen.append)
    page.set_level("week")
    page.set_level("week")  # 再设一次同一层也要报：外面靠它刷新窗口
    assert seen == ["week", "week"]


def test_window_changed_fires_when_the_level_moves(page: CalendarPage) -> None:
    """翻页 / 换层 / 滚动都要重新报一次「现在看得见哪些」。"""
    seen: list[int] = []
    page.window_changed.connect(lambda: seen.append(1))
    page.set_level("week")
    page.step(1)
    page.go_today()
    assert len(seen) >= 3
