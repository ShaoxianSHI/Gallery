"""日历那一页：数据进去、切层、翻页、快捷键、点格下钻。

这些用例只碰页面自己（不建 ``MainWindow``）：浏览方式、会话、缩略图流水线那些是
外层的事，在 ``tests/ui/test_main_window_*.py`` 那边。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path

import pytest

from gallery.core.library.calendar import month_matrix
from gallery.core.library.photos import PhotoRef
from gallery.core.metadata.facets import PhotoFacets
from gallery.ui.calendar import CalendarPage

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

TODAY = datetime.date(2021, 9, 12)
#: 那一周的周三。选中它，那一格的高亮就不会落在采样点上（周末那两列上）。
WEDNESDAY = datetime.date(2021, 9, 15)

#: 不写阶段的滚轮事件按「还在滑」算（``ScrollUpdate`` 是默认的那种）。
DEFAULT_PHASE = Qt.ScrollPhase.ScrollUpdate


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


def scroll(
    view: QWidget,
    dx: int,
    dy: int = 0,
    *,
    phase: Qt.ScrollPhase = DEFAULT_PHASE,
    pixels: bool = True,
) -> QWheelEvent:
    """在某一层上划一下：发一个 macOS 触控板那种形状的滚轮事件（两个字段都给）。

    **手写事件而不是伪造手势**：真机上两指划走的就是横向的 ``QWheelEvent``，那几个
    ``Momentum*`` 阶段的常量 PySide 压根没绑（只有旧的那四个），所以这一条只能照
    滚轮事件来验。
    """
    event = QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        QPoint(dx, dy) if pixels else QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        phase,
        False,
    )
    view.wheelEvent(event)
    return event


def flick(page: CalendarPage, fingers: int = -1, *, pixels: bool = True, step: int = 40) -> None:
    """一次用力的甩动：手指按下 + 两次增量（80 像素，刚够过阈值）。

    ``fingers`` 是**手指往哪边走**：``-1`` 往左（往后一段），``1`` 往右（往前一段）。

    ``pixels=False`` 走的是角度增量那份阈值（120），于是每一步要更大一点。
    """
    view = page.current_view()
    scroll(view, step * fingers, phase=Qt.ScrollPhase.ScrollBegin, pixels=pixels)
    scroll(view, step * fingers, pixels=pixels)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("year", datetime.date(2022, 9, 12)),
        ("month", datetime.date(2021, 10, 12)),
        ("week", datetime.date(2021, 9, 19)),
    ],
)
def test_swiping_left_pages_one_period(
    page: CalendarPage, level: str, expected: datetime.date
) -> None:
    """**两指往左甩一下 = 往后翻一段**（年 ± 一年、月 ± 一月、周 ± 七天），三层都成立。

    方向在这一条里是锁定的：那是把那一页纸往左拖的手感，屏幕上的内容跟着走，
    右边那一份露出来。反过来（「手势 = 同方向的那个按键」）读起来是反的。
    """
    page.set_level(level)
    flick(page)
    assert page.anchor() == expected


def test_swiping_right_pages_back(page: CalendarPage) -> None:
    page.set_level("month")
    flick(page, fingers=1)
    assert page.anchor() == datetime.date(2021, 8, 12)


def test_a_nudge_is_not_a_page(page: CalendarPage) -> None:
    """轻轻推一下不该翻（真机上那也就二三十像素）。"""
    page.set_level("month")
    view = page.current_view()
    scroll(view, -20)
    assert page.anchor() == TODAY


def test_one_flick_pages_once_and_the_momentum_after_it_pages_nothing(
    page: CalendarPage,
) -> None:
    """一次甩动**只翻一页**，手指抬起来之后系统补的那一截惯性**一页都不翻**。

    惯性那一截常有上百像素，没有这道前置条件就是「手已经离板子了，它自己又跳了三个月」。
    """
    page.set_level("month")
    view = page.current_view()
    scroll(view, -40, phase=Qt.ScrollPhase.ScrollBegin)
    scroll(view, -40)  # 80 像素：翻一页
    scroll(view, -40)  # 手还压在左边：不再翻
    for delta in (-60, -40, -20):
        scroll(view, delta, phase=Qt.ScrollPhase.ScrollMomentum)
    assert page.anchor() == datetime.date(2021, 10, 12)


def test_scrolling_vertically_leaves_the_page_alone(page: CalendarPage) -> None:
    """**竖着划不翻页，而且要让事件继续往上走** —— 周视图的时间轴就靠它滚。

    钉的是 ``isAccepted``：``QWidget`` 默认忽略不认识的滚轮事件，于是周视图里那个
    ``QScrollArea`` 才拿得到它；这里一 ``accept``，时间轴就再也滚不动了。
    """
    page.set_level("week")
    view = page.current_view()
    event = scroll(view, 0, 90)
    assert page.anchor() == TODAY
    assert not event.isAccepted()


def test_a_diagonal_scroll_counts_as_vertical(page: CalendarPage) -> None:
    """斜着划（横向小于纵向）算竖着：斜着滚时间轴是常事，斜一下就翻周不是。"""
    page.set_level("week")
    scroll(page.current_view(), 30, 90)
    assert page.anchor() == TODAY


def test_a_horizontal_scroll_is_taken_by_the_calendar(page: CalendarPage) -> None:
    """横向那一下要 **accept**：否则它会继续往上冒，在周视图里横向滚不了任何东西。"""
    page.set_level("week")
    event = scroll(page.current_view(), 90)
    assert event.isAccepted()


def test_a_device_without_pixel_deltas_still_pages(page: CalendarPage) -> None:
    """只给角度增量的设备（Magic Mouse 一类）同样翻页 —— 走的是另一条阈值。"""
    page.set_level("month")
    flick(page, pixels=False, step=70)  # 140：两格滚轮（角度增量那份阈值是 120）
    assert page.anchor() == datetime.date(2021, 10, 12)


def test_a_swipe_is_a_page_turn_in_every_respect(page: CalendarPage) -> None:
    """甩一下与按方向键是**同一件事**：收掉屏幕外的高亮、重报缩略图窗口。

    三个入口共用 :meth:`CalendarPage.step`，这一条钉的是「真接上了」，不是各写一份。
    锚点本身不动 —— ``set_selected`` 只改选中，翻到哪一段是 ``step`` 说了算（规则见
    :meth:`CalendarPage.set_level`）。
    """
    page.set_level("year")
    page.set_selected(datetime.date(2021, 5, 16))
    reports: list[int] = []
    page.window_changed.connect(lambda: reports.append(1))

    flick(page)

    assert page.anchor() == datetime.date(2022, 9, 12)
    assert page.selected_day() is None, "翻走了却把别处那一天的高亮留着"
    assert reports, "翻页没有重报缩略图窗口"


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
    # 同一秒的一批按文件名排（burst1 在 burst10 前），所以比集合而不比顺序。
    assert {ref.path for ref in photos} == {ref.path for ref in burst}


def test_the_week_view_starts_on_sunday(page: CalendarPage) -> None:
    """一周的第一列是周日（10 月 4 日那天，周日 10-03 在它左边一格）。"""
    page.set_level("week")
    days = page.current_view().days()
    assert len(days) == 7
    assert days[0].weekday() == 6  # 周日
    assert days[-1].weekday() == 5  # 周六


def test_no_column_is_tinted_for_the_weekend(
    page: CalendarPage,
    qt_app: QApplication,  # noqa: ARG001 - 靠它把布局跑过一遍
) -> None:
    """周六周日与其他五天**一模一样**（早年是给那两列掺了 3% 正文色的淡底）。

    钉的是**画出来的那一屏**，不是「某个成员还在不在」：采样点全是空格子正中那一小块
    （格子里只有左上角的日期与照片，中间没别的东西），所以谁再把那层淡底加回来，
    无论加在月视图的格子上还是周视图的表头与时间轴上，这一条都会红。

    采样点一律避开**选中的那一格** —— 它本来就有一层选中高亮，与周末无关。
    """
    page.resize(900, 600)
    page.show()
    qt_app.processEvents()

    page.set_level("month")
    view = page.current_view()
    view.select_day(WEDNESDAY)  # 选中落在第三行的周三，于是改看第四行
    # 第四行的七格：周日 → 周六（一个月视图的第一列就是周日）
    centers = [view._day_cell(index).center() for index in range(21, 28)]  # type: ignore[attr-defined]
    _assert_all_background(_render(view), centers, view.background, "月视图")

    page.set_level("week")
    view = page.current_view()
    view.select_day(WEDNESDAY)
    column = view.column_width()  # type: ignore[attr-defined]
    weekend = [
        QPointF(view.content_left() + (index + 0.5) * column, 0)  # type: ignore[attr-defined]
        for index in (0, 6)  # 周日与周六，且都不是选中的周三
    ]
    for widget, y, where in (
        # 网格与表头是两个控件各画各的：表头高 56，日期号那一行到 50 就没了。
        (view._grid, view.row_height() * 2.5, "周视图的时间轴"),  # type: ignore[attr-defined]
        (view._header, 53.0, "周视图的表头"),  # type: ignore[attr-defined]
    ):
        image = _render(widget)
        _assert_all_background(
            image, [QPointF(point.x(), y) for point in weekend], view.background, where
        )


def _render(widget: QWidget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_RGBA8888)
    widget.render(image)
    return image


def _assert_all_background(
    image: QImage, points: list[QPointF], background: QColor, where: str
) -> None:
    seen = {image.pixelColor(int(p.x()), int(p.y())).name() for p in points}
    assert seen == {background.name()}, f"{where}的那几列不是底色：{sorted(seen)}"


def test_the_week_header_names_the_column_it_sits_on(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
    qt_app: QApplication,
) -> None:
    """表头那一列写的就是那一列的星期 —— **第一列是周日，不是周六**。

    :data:`WEEKDAY_NAMES` 是「周日打头」那一套，而 ``date.weekday()`` 是「周一在前」，
    两者差一天。原先周视图拿后者去索引前者，于是七个名字整体挪了一格：**周六跑到了
    最前面**，而那一列其实是周日。
    """
    from gallery.ui.calendar import time_view as time_view_module

    page.set_level("week")
    page.resize(1000, 700)
    page.show()
    qt_app.processEvents()
    view = page.current_view()

    labels: list[str] = []
    original = time_view_module.draw_text

    def record(painter, rect, text, *args, **kwargs):
        if isinstance(text, str) and text.startswith("周"):
            labels.append(text)
        return original(painter, rect, text, *args, **kwargs)

    monkeypatch.setattr(time_view_module, "draw_text", record)
    view._header.render(QPixmap(view._header.size()))

    assert labels == ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


@pytest.mark.parametrize("level", ["week", "month"])
def test_a_resized_view_reports_its_window_again(
    page: CalendarPage,
    qt_app: QApplication,
    wait_for: Callable[..., bool],
    level: str,
) -> None:
    """**改窗口大小要重新报一次「看得见哪些」** —— 缩略图就是照这一句去要的。

    没有它就是「周视图的缩略图有一定概率出不来」：拖一下窗口边会多出几个小时的格子，
    而外面仍然拿着改之前那份清单。多出来的那些没人要图，于是一直空着，直到用户碰巧滚一下
    或换一层才补上 —— 复现与否全看有没有拖过窗口、有没有滚过。

    **只有周视图真会因此变**：它看得见哪几个小时取决于视口高与行高（行高又是列宽推出来
    的，所以要改的是**长宽比**）；月视图那 42 格本来就全在屏幕上，年视图要的那一批与几何
    无关。两层也跟着走同一条路，只是报出去的是同一批。
    """
    refs = [_photo(f"p{day}_{hour}.jpg") for day in range(7) for hour in range(24)]
    page.set_photos(
        refs,
        {
            ref.path: _values(ref, TODAY + datetime.timedelta(days=index // 24), index % 24)
            for index, ref in enumerate(refs)
        },
    )
    page.set_level(level)
    page.resize(1000, 700)
    page.show()
    qt_app.processEvents()

    reports: list[set[Path]] = []
    page.window_changed.connect(
        lambda: reports.append({ref.path for ref in page.current_view().visible_refs()})
    )

    page.resize(1000, 240)  # 压扁：看得见的少了
    assert wait_for(lambda: len(reports) >= 1), "改完窗口大小没有重报一次"
    assert wait_for(lambda: not page.current_view()._geometry_notify.isActive())
    short = len(page.current_view().visible_refs())

    page.resize(1000, 700)  # 放回去：新露出来的那几个小时

    # **等的是「最后那一次报全了」而不是「报了几次」**：改尺寸时会报好几次（内部几何
    # 一变就补一次，见 `WeekView.eventFilter`），按次数等会等到过渡中的那一条。
    def reported_whole_window() -> bool:
        visible = {ref.path for ref in page.current_view().visible_refs()}
        return bool(reports) and not (visible - reports[-1])

    assert wait_for(reported_whole_window), "放回去之后没有再报一次完整的那一屏"
    visible = {ref.path for ref in page.current_view().visible_refs()}
    assert not visible - reports[-1], f"{len(visible - reports[-1])} 张看得见却没报出去"
    if level == "week":
        assert len(visible) > short, "前提不成立：压扁窗口没有减少看得见的那几格"


def test_coming_back_to_the_page_leaves_no_day_highlighted(
    page: CalendarPage,
    qt_app: QApplication,
) -> None:
    """回到日历这一页时，上一次点开的那一格**不再高亮**，而那**一天成了锚点**。

    用户点的是某一天，进的是那张照片或那一格 —— 退回来时那一格不在眼前。留着的高亮
    是一块**点不掉**的底色（周视图里那一份高亮曾经是整列 24 行，压根点不掉）。可它仍然是
    用户刚指着的那一天，所以按 ``Y`` / ``M`` / ``W`` 看到的该是它所在的那一层（下面那一条
    钉的就是这件事）。

    **摆着的那一段不动**：那是用户刚找到的地方（滚动条与那一段都留着，理由见
    ``window/calendar_flow._back_to_calendar``）。
    """
    ref = _photo("a.jpg")
    page.set_photos([ref], {ref.path: _values(ref, TODAY, 9)})
    page.set_level("week")
    page.show()
    qt_app.processEvents()
    view = page.current_view()
    days_before = list(view.days())
    # 周视图里点的是**某一小时那一格**（那一格有照片 → 会展开），不是整天
    view.select_hour(WEDNESDAY, 14)  # 点的是周三，不是起手那个锚点（今天）
    assert view.is_selected_cell(WEDNESDAY, 14), "前提不成立：点过的那一格没被选中"
    assert page.anchor() == TODAY, "前提不成立：锚点一开始就是点过的那一天"

    page.hide()
    page.show()
    qt_app.processEvents()

    assert view.selected_cell() is None, "退回来还留着那一格的高亮"
    assert view._selected is None
    assert page.selected_day() is None
    assert page.anchor() == WEDNESDAY, "点过的那一天没有成为锚点"
    assert list(view.days()) == days_before, "摆着的那一周被这一次「回来」挪走了"


def test_the_cell_you_come_back_from_is_where_the_next_level_opens(
    page: CalendarPage,
    qt_app: QApplication,
) -> None:
    """年视图里点开一格 → 退回来 → 按 W / M / Y：看到的是**那一格所在的那一层**。

    退回来若把「点过的那一天」一起丢了，按 ``W`` 翻到的就是锚点原来那一天所在的那一周 ——
    与用户刚点的地方毫无关系。点**空格子**时这条路一直是对的（高亮替它记着那一天），
    而**点得进去的那一格退回来就丢了** —— 这个不对称正是这条要钉的。
    """
    picked = datetime.date(2021, 5, 16)
    page.set_level("year")
    page.show()
    qt_app.processEvents()
    page.current_view().select_day(picked)  # 外面接上去展开那一格

    page.hide()
    page.show()
    qt_app.processEvents()  # 退回来

    assert page.selected_day() is None, "退回来还留着高亮"
    for level in ("week", "month", "year"):
        page.set_level(level)
        assert page.anchor() == picked, f"{level} 那一层没有落在点过的那一天"
        assert picked in page.current_view().days(), f"{level} 那一层摆的不是那一天所在的一段"


# 焦点那一条（退回来时焦点要回到当前那层，← / → 才重新有用）走真实窗口：独立的控件在
# 离屏平台上拿不到焦点，见 ``tests/ui/test_calendar_flow.py``。


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

    # 9 月那张小月确实也画到了这一天（占位），但高亮只有一次
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


def test_every_level_shows_the_same_photo_for_the_same_day(
    page: CalendarPage,
    qt_app: QApplication,  # noqa: ARG001
) -> None:
    """**同一天在三层里是同一张照片** —— 一格只有一张脸。

    曾经年视图取当天**最晚**的那张而月 / 周两层取**最早**的那张，于是 9 月 12 日
    在年视图里是 09:02 拍的那张、切到月视图就变成 09:00 的那张。同一个日子在两屏上
    长成两张脸，用户会以为是自己记错了哪一天。

    **三层各报的那一张必须是同一个文件** —— 报的那张（`visible_refs`，决定去解码谁）
    与画的那张（`paint_*`）是成对的，只改一处的话格子会永远停在占位圆点上，
    所以这一条量的是报窗口，与下面那条量绘制的那条配成一对。
    """
    refs = [_photo(f"三种{index}.jpg") for index in range(3)]
    page.set_photos(
        refs,
        {
            # 同一个钟点里三张，拍摄时间依次变晚：08:00 / 08:01 / 08:02
            ref.path: _values(ref, TODAY, 8, index)
            for index, ref in enumerate(refs)
        },
    )
    page.resize(1200, 800)
    page.show()
    qt_app.processEvents()

    wanted: dict[str, Path] = {}
    for level in ("year", "month", "week"):
        page.set_level(level)
        qt_app.processEvents()
        reported = [ref.path for ref in page.current_view().visible_refs()]
        assert refs[0].path in reported, (
            f"{level} 视图没有要当天最早的那张（{refs[0].name}）；它要的是 {reported}"
        )
        for ref in refs[1:]:
            assert ref.path not in reported, f"{level} 视图把当天较晚的那张（{ref.name}）当成了代表"
        wanted[level] = refs[0].path

    assert len(set(wanted.values())) == 1, f"三层给同一天挑了不同的代表：{wanted}"


def test_the_year_view_draws_the_photo_it_reported(
    page: CalendarPage,
    qt_app: QApplication,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """年视图**画的那一张**就是它报的那一张（曾经一格报 `[-1]` 而画 `[0]`）。

    这两处是成对的，分开写就会出现「要了 A 的图、屏幕上画的是 B」—— 症状是那一格
    永远是占位圆点，而 ``photo()`` 一查是有图的（数据没错，于是格外难查）。
    """
    refs = [_photo(f"一对{index}.jpg") for index in range(2)]
    page.set_photos(
        refs,
        {ref.path: _values(ref, TODAY, 8, index) for index, ref in enumerate(refs)},
    )
    page.set_level("year")
    page.resize(1200, 800)
    page.show()
    qt_app.processEvents()
    view = page.current_view()

    # 每张缩略图各自填一种颜色，格子里画出来的到底是哪一张一眼就分得开。
    for index, ref in enumerate(refs):
        image = QImage(48, 48, QImage.Format.Format_RGB32)
        image.fill(QColor(20 * (index + 1), 0, 0))
        page.set_thumbnail(ref.path, image, page.thumbnail_edge())

    asked: list[Path] = []
    original = view.photo

    def record(ref):  # type: ignore[no-untyped-def]
        asked.append(ref.path)
        return original(ref)

    monkeypatch.setattr(view, "photo", record)
    view.render(QPixmap(view.size()))

    assert asked, "这一帧里一格都没查过缩略图 —— 测试在错误的前提下空转成绿的"
    reported = {ref.path for ref in view.visible_refs()}
    assert len(reported) == 1, f"前提不成立：这一天报了 {len(reported)} 张：{reported}"
    assert set(asked) <= reported, (
        f"画的时候查了 {sorted(set(asked))}，报出去的却是 {sorted(reported)} —— "
        "要了 A 的图、画的是 B"
    )


def _a_year_of_photos(page: CalendarPage, count: int = 40) -> list[PhotoRef]:
    refs = [_photo(f"burst{index}.jpg") for index in range(count)]
    page.set_photos(
        refs,
        {
            ref.path: _values(ref, TODAY + datetime.timedelta(days=index % 28), 9)
            for index, ref in enumerate(refs)
        },
    )
    page.set_level("year")
    return refs


def test_a_burst_of_thumbnails_is_painted_exactly_once(
    page: CalendarPage,
    qt_app: QApplication,
    wait_for: Callable[..., bool],
) -> None:
    """**流水线是一张一张送图回来的，页面只许重画一帧。**

    ``ThumbnailPipeline.ready`` 每解码完一张就发一次，于是 ``set_thumbnail`` 会被调
    好几百次。原先每一次都直接 ``update()``，而 ``update()`` 是立刻排一次整页重绘 ——
    300 张照片铺满 2024（405 个有图的格子）实测要 **1890 ms**，一次全送到只要
    **0.1 ms**。单次定时器的 ``start()`` 会原地续期，于是来一百张就是「下一轮画一次」。
    """
    _a_year_of_photos(page)
    view = page.current_view()

    painted: list[int] = []
    view.update = lambda *args, **kwargs: painted.append(1)  # type: ignore[method-assign]

    image = QImage(48, 48, QImage.Format.Format_RGBA8888)
    image.fill(0x336699)
    edge = page.thumbnail_edge()
    arriving = view.visible_refs()
    # 40 张落在 9 月 12 日 – 10 月 9 日这 28 天上，横跨两张小月；占位格不画照片，
    # 所以每张只报一次。
    assert len(arriving) == 28, "前提不成立：这一层要的那一批不是这 28 天"
    assert len(set(arriving)) == len(arriving), "同一张照片报了两遍"
    for ref in arriving:
        page.set_thumbnail(ref.path, image, edge)

    assert painted == [], "还在同一轮里，不该已经画过了"
    assert wait_for(lambda: painted), "闸门没有落地"
    qt_app.processEvents()
    assert len(painted) == 1, f"{len(arriving)} 张图重画了 {len(painted)} 帧"


def test_the_thumbnails_of_a_burst_all_land(
    page: CalendarPage,
    qt_app: QApplication,  # noqa: ARG001
) -> None:
    """合并的是**重画**，不是图本身 —— 每一张都得在手上（`forget` 与换层都靠这个）。"""
    refs = _a_year_of_photos(page, 5)
    view = page.current_view()
    image = QImage(48, 48, QImage.Format.Format_RGBA8888)
    image.fill(0x336699)
    edge = page.thumbnail_edge()
    for ref in refs:
        page.set_thumbnail(ref.path, image, edge)

    assert all(view.photo(ref) is not None for ref in refs)


def _pick_day(page: CalendarPage, day: datetime.date) -> None:
    """用户点到了那一天（走的是页面那一份选中）。"""
    page.set_selected(day)
    assert page.selected_day() == day


def test_switching_levels_lands_on_the_day_that_was_picked(page: CalendarPage) -> None:
    """**选中了哪一天，切层就翻到那一天所在的那一层。**

    「在年视图里点 5 月 16 号 → 按 W」要看到 5 月 16 号那一周，而不是上周剩下那几天。
    三条入口（``Y`` / ``M`` / ``W``、工具条、会话恢复）都走 :meth:`CalendarPage.set_level`，
    所以这里一条就锁定了全部。
    """
    picked = datetime.date(2021, 5, 16)
    page.set_level("year")
    _pick_day(page, picked)

    page.set_level("month")
    assert page.anchor() == picked
    assert picked in page.current_view().days(), "切到月视图没有落在那一个月"

    page.set_level("week")
    assert picked in page.current_view().days(), "切到周视图没有落在那一周"

    page.set_level("year")
    assert page.anchor() == picked


def test_the_picked_day_stays_picked_across_the_levels(page: CalendarPage) -> None:
    """**整页一份选中**：切到别的层仍然是它（不是一层一份、切过去就忘了）。"""
    picked = datetime.date(2021, 5, 16)
    page.set_level("year")
    _pick_day(page, picked)

    for level in ("month", "week", "year", "week"):
        page.set_level(level)
        view = page.current_view()
        assert page.selected_day() == picked
        assert view.selected_day() == picked, f"{level} 那一层没拿到选中的那一天"


def test_switching_levels_keeps_the_day_in_every_screen(page: CalendarPage) -> None:
    """切层走的是「选中 → 锚点」这条路，于是**每一层都摆着那一天**。"""
    page.set_level("year")
    _pick_day(page, datetime.date(2021, 5, 16))
    page.set_level("month")  # 切层：锚点跟着选中的那一天走，于是这一屏有那一天
    assert page.selected_day() == datetime.date(2021, 5, 16)
    assert datetime.date(2021, 5, 16) in page.current_view().days()


def test_paging_away_drops_a_day_that_is_no_longer_on_screen(page: CalendarPage) -> None:
    """翻到了别的一段日子：选中的那一天不在这儿了，那块高亮就收掉。

    留着就是**指着屏幕外的一天**：年视图里点 5 月 16 号再按 ←，屏幕上是 2020 年，
    而那一格还指着 2021 年的 5 月 16 号 —— 用户以为改的是他看见的那一张。
    """
    page.set_level("year")
    _pick_day(page, datetime.date(2021, 5, 16))
    page.step(-1)
    assert page.anchor().year == 2020
    assert page.selected_day() is None


def test_clicking_a_cell_in_the_week_view_highlights_that_cell(page: CalendarPage) -> None:
    """周视图里**点哪一格高亮哪一格**，并且那一天被选中（切层时要用）。"""
    page.set_level("week")
    view = page.current_view()
    day, hour = view.days()[2], 14
    view.select_hour(day, hour)

    assert view.selected_cell() == (day, hour)
    assert view.is_selected_cell(day, hour)
    assert not view.is_selected_cell(day, hour + 1)
    assert view.selected_day() == day
    assert page.selected_day() == day


def test_the_week_view_does_not_paint_the_whole_column(
    page: CalendarPage,
    qt_app: QApplication,
) -> None:
    """**只涂点中的那一格**，不是选中那一天的全部 24 行。

    从年 / 月视图切过来时（没有点中的那一格），连那一格都不涂，只在表头留一层淡底说
    「选的是哪一天」。
    """
    page.resize(1000, 700)
    page.show()
    page.set_level("week")
    qt_app.processEvents()
    view = page.current_view()
    view.select_hour(view.days()[3], 14)
    qt_app.processEvents()

    selection = view.colors.selection
    picked = _week_cell_point(view, view.days()[3], 14)
    same_column_other_hour = _week_cell_point(view, view.days()[3], 2)
    other_column = _week_cell_point(view, view.days()[0], 14)

    image = _render(view._grid)
    picked_color = image.pixelColor(int(picked.x()), int(picked.y()))
    assert picked_color != view.background, "点中的那一格没有涂上选中色"
    for name, point in (
        ("同一列的别的钟点", same_column_other_hour),
        ("别的列", other_column),
    ):
        color = image.pixelColor(int(point.x()), int(point.y()))
        assert color != selection, f"{name}也被涂了 —— 整列都亮着就是旧行为"
        assert color == view.background, f"{name}不是底色：{color.name()}"


def _week_cell_point(view, day: datetime.date, hour: int):  # type: ignore[no-untyped-def]
    """周视图里某一格正中的点（时间轴内容坐标）。"""
    from PySide6.QtCore import QPointF

    column = view.column_width()
    row = view.row_height()
    return QPointF(
        view.content_left() + (day - view.days()[0]).days * column + column / 2,
        hour * row + row / 2,
    )
