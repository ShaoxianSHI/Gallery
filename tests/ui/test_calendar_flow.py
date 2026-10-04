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

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
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

#: 三月一张（一格一张 → 进大图页）、四月两张（各自一格）、五月两张挤在同一个小时
#: （一格多张 → 摊成一层网格）。
DATES = {
    "三月一张.jpg": "2024:03:16 10:00:00",
    "四月一张.jpg": "2024:04:16 09:00:00",
    "四月两张.jpg": "2024:04:16 18:00:00",
    "五月两张甲.jpg": "2024:05:16 09:00:00",
    "五月两张乙.jpg": "2024:05:16 09:30:00",
}
NAMES = tuple(DATES)

#: 测试里的「今天」：那一天照片最多（月视图打开就看得见东西）。
APRIL = datetime.date(2024, 4, 16)
MARCH = datetime.date(2024, 3, 16)
MAY = datetime.date(2024, 5, 16)


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


def _open(
    qt_app: QApplication, root: Path, session_base: Path, *, group_facet: str | None = None
) -> MainWindow:
    """开一个停在日历上的窗口。``group_facet`` 用来复现「用户开着分组进日历」。"""
    save_session(
        SessionState(
            recent_roots=(root,),
            browse_mode=BROWSE_MODE_CALENDAR,
            group_facet=group_facet,
        ),
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
    assert page.photo_count() == 2  # 四月那一张 + 四月那一张（三月那张不在这一屏上）


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


def test_a_scan_in_flight_does_not_land_on_the_drill_layer(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """扫描在途时点一格，那一遍扫描的结果**不许**把这一层换成整库。

    刚切到日历就点一格时正好会这样：日历那一页摆的还是上一次的桶（照片已经能点），
    而底下那遍整库平铺还在扫。它一回来，这一格的几张就变成整库照片了 ——
    症状是「点开一格，出来一大片」。

    这里把「在途」摆出来而不是等它真的在途：测试库太小，扫描一瞬间就完了，
    等不到那一刻；而要验的正是**迟到的那一批**落地时会怎样。
    """
    from gallery.core.library.scanner import ScanReport

    window = calendar_window
    photos, label = _cell_of(window, 4)
    assert len(photos) == 2

    stale_generation = window._generation
    window._scan_job = 4242  # 假装整库平铺还在扫
    window._staged = list(window._model.entries())

    window._calendar_page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._scan_job is None, "下钻时没有把在途的那一遍作废"
    assert window._model.rowCount() == 2

    # 那一遍「扫完了」，结果迟到：换代之后它就不认这一层了
    window._on_scan_done(stale_generation, ScanReport())
    qt_app.processEvents()
    assert window._model.rowCount() == 2, "迟到的扫描结果把这一格换成了整库"


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


def test_a_group_facet_on_does_not_empty_the_calendar(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> None:
    """开着「按拍摄日期分组」切到日历，日历照样有照片。

    这是真出过的：模型是网格与日历共用的，带着分组时它摆的是一张张**组卡片**，
    而日历只从模型里挑 ``PhotoRef`` 挑到零张 —— 整层空着，界面上看不出任何原因。
    分组是网格那一页的事；日历这一页不分组，模型里装整库照片。
    """
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    prepare_library(root)
    session_base = tmp_path / "会话"

    window = _open(qt_app, root, session_base, group_facet="captured_day")
    page = window._calendar_page
    page.set_today(APRIL)
    page.go_today()
    assert wait_until(
        qt_app,
        lambda: window_is_idle(window) and page.photo_count() > 0,
    ), "开着分组时日历仍然是空的"
    # 模型里装的是照片，不是组卡片
    assert window._model.rowCount() == len(NAMES)
    assert page.photo_count() == 2  # 四月那两张

    # 从日历退出去，网格上那个分组还是用户自己选的
    window.show_grid()
    assert wait_until(qt_app, lambda: window_is_idle(window))
    assert window._model.group_count() > 0


def test_the_week_view_sits_on_the_hour_of_its_first_photo(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """周视图起手就停在有照片的那几个小时，不是从零点开始让人自己滚。"""
    window = calendar_window
    page = window._calendar_page
    page.set_today(MARCH)
    page.set_level("week")
    page.go_today()
    assert wait_until(
        qt_app,
        lambda: page.current_view()._area.verticalScrollBar().value() > 0,
        timeout=2.0,
    ), "周视图没有定位到有照片的那几个小时"


def _click_hour_cell(
    qt_app: QApplication, window: MainWindow, day: datetime.date, hour: int
) -> None:
    """在周视图里点某一天某一小时的那一格（坐标是时间轴内容里的，与滚到哪儿无关）。"""
    view = window._calendar_page.current_view()
    column, row = view.column_width(), view.row_height()
    point = QPoint(
        int(view.content_left() + (day - view.days()[0]).days * column + column / 2),
        int(hour * row + row / 2),
    )
    QTest.mouseClick(view._grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qt_app.processEvents()


def _week_at(qt_app: QApplication, window: MainWindow, day: datetime.date) -> None:
    """切到周视图，并等它把滚动定位完 —— 用户点那一格时它早就落定了。"""
    page = window._calendar_page
    page.set_today(day)
    page.go_today()
    page.set_level("week")
    view = page.current_view()
    assert wait_until(qt_app, lambda: not view._reveal.isActive()), "周视图没有定位完"


def test_coming_back_from_a_photo_leaves_the_arrows_working(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """周视图里点进一张照片再退出来，**← / → 要还能翻周**。

    焦点必须点名交给周视图：退回来时 Qt 把焦点顺焦点链交给页面上第一个能接焦点的控件 ——
    那一页里那是那个 ``QScrollArea``，它把 ← / → 当成滚动时间轴吃掉，于是日历的
    「上一周 / 下一周」按多少次都没反应。月视图里落到的恰好是视图自己，所以只有周视图犯病。
    """
    window = calendar_window
    page = window._calendar_page
    _week_at(qt_app, window, MARCH)
    _click_hour_cell(qt_app, window, MARCH, 10)
    assert window._stack.currentWidget() is window._detail_page, "点那一格没有进大图页"

    QTest.keyClick(window._detail, Qt.Key.Key_Escape)
    _settle(qt_app, window)
    assert window._stack.currentWidget() is page

    view = page.current_view()
    assert qt_app.focusWidget() is view, f"焦点停在 {type(qt_app.focusWidget()).__name__}"
    before = page.anchor()
    QTest.keyClick(view, Qt.Key.Key_Right)
    assert page.anchor() == before + datetime.timedelta(days=7), "→ 没有翻到下一周"
    QTest.keyClick(view, Qt.Key.Key_Left)
    assert page.anchor() == before, "← 没有翻回上一周"


def test_coming_back_from_a_photo_leaves_no_day_highlighted(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """退回来时那一格不再高亮（锚点不动 —— 那才是用户刚找到的地方）。

    周视图点空白格子什么也不会发生（它提前 return 了），所以留着的高亮是块**点不掉**的底色。
    """
    window = calendar_window
    page = window._calendar_page
    _week_at(qt_app, window, MARCH)
    _click_hour_cell(qt_app, window, MARCH, 10)
    assert page.current_view()._selected == MARCH, "前提不成立：点过的那一天没被选中"

    QTest.keyClick(window._detail, Qt.Key.Key_Escape)
    _settle(qt_app, window)

    assert page.current_view()._selected is None
    assert page.anchor() == MARCH


def test_coming_back_from_a_drill_grid_leaves_the_arrows_working(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """一格多张（摊成一层网格）那条路是同一套收拾：焦点、选中。"""
    window = calendar_window
    page = window._calendar_page
    _week_at(qt_app, window, MAY)
    _click_hour_cell(qt_app, window, MAY, 9)
    assert window._stack.currentWidget() is window._grid_page, "点那一格没有摊成网格"
    assert window._model.rowCount() == 2

    window.go_up()
    _settle(qt_app, window)
    assert window._stack.currentWidget() is page

    view = page.current_view()
    assert qt_app.focusWidget() is view, f"焦点停在 {type(qt_app.focusWidget()).__name__}"
    assert view._selected is None
    before = page.anchor()
    QTest.keyClick(view, Qt.Key.Key_Right)
    assert page.anchor() == before + datetime.timedelta(days=7)


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
