"""日历在窗口里的整条链：切到日历 → 点一格 → 网格 → 大图 → 一路退回来。

用户点名的三件事都在这里钉住：点**就一张**的那一格 → 大图页，退回来是**日历**；点**好几张**
的那一格 → 网格里就是那几张，再退一级才回日历；退回来时**位置不动**。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from waiting import wait_for_idle, wait_until, window_is_idle  # noqa: E402

from gallery.core.library.calendar import month_matrix  # noqa: E402
from gallery.core.library.config import (  # noqa: E402
    BROWSE_MODE_CALENDAR,
    load_config,
    prepare_library,
)
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.core.metadata import batch  # noqa: E402
from gallery.core.places.resolver import LazyPlaceResolver, ResolvedPlace  # noqa: E402
from gallery.ui.calendar.month_view import MonthView  # noqa: E402
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
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_CALENDAR, group_facet=group_facet)
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


def _click_a_cell(view: MonthView, day: datetime.date) -> None:
    """在月视图里**真的点一下**那一格（走鼠标事件，与用户点下去同一条路）。

    **不直接调 ``select_cell``**：那样子跳过了「先选中、再摊开」的前半截，而下面那一条
    要验的恰恰是前半截留在页面上的那一天（事件坐标要过一遍 Qt，所以按控件坐标发）。
    """
    point: QPointF = view._day_cell(month_matrix(view.anchor()).index(day)).center()  # type: ignore[attr-defined]
    view.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


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


def test_the_cell_you_come_back_from_becomes_the_anchor(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """点开一格 → 退回来：**那一格成为锚点，而高亮不回来**。

    用户点的是**有照片的那一格**时它会摊开，而原先退回来时「点过的那一天」被一起丢掉了，
    于是按 ``W`` 翻到的是与那一格毫无关系的那一周（点空格子不会摊开，所以没暴露这个
    不对称）。**锚点挪的是那一天，摆着的那一段不动**。
    """
    window = calendar_window
    page = window._calendar_page
    page.set_today(datetime.date(2024, 5, 20))  # 停在五月二十：与点开的那格不是同一天
    page.go_today()
    page.set_level("month")
    view = page.current_view()
    assert isinstance(view, MonthView)
    assert len(list(view.photos_of(MAY))) == 2, "前提不成立：五月那一格不是好几张"
    assert page.anchor() != MAY, "前提不成立：锚点就是点开的那一天"

    _click_a_cell(view, MAY)
    qt_app.processEvents()
    assert window._stack.currentWidget() is window._grid_page, "那一格没有摊开"

    window.go_up()
    _settle(qt_app, window)
    assert window._stack.currentWidget() is page
    assert page.selected_day() is None, "退回来还留着高亮"
    assert page.anchor() == MAY, "点开的那一格没有成为锚点"

    page.set_level("week")
    assert MAY in page.current_view().days(), "按 W 没有落到点开的那一天那一周"


def _days_total(page: object) -> int:
    """日历那份桶里一共有几张（跨天加起来）。"""
    view = page.current_view()  # type: ignore[attr-defined]
    return sum(
        len(list(view.photos_of(day)))
        for day in (MARCH, APRIL, MAY)  # type: ignore[attr-defined]
    )


def test_entering_a_drill_does_not_repoint_the_calendar_at_those_photos(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """摊开的那一层**不是**「这一层的照片」，拿它分桶等于告诉日历整个库就那几张。

    进网格是「先摆内容再换页」，换页那一刻日历还在前面而模型已经被换成那几张，于是其余
    格子当场空掉，要等退回来之后那一遍重扫才恢复。**这里不扫完就断言**。
    """
    window = calendar_window
    page = window._calendar_page
    photos, label = _cell_of(window, 5)  # 五月那格挤着两张 → 会摊成一层
    assert len(photos) == 2

    page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._stack.currentWidget() is window._grid_page

    assert _days_total(page) == len(NAMES), "整个日历被换成了摊开的那几张"

    window.go_up()
    qt_app.processEvents()
    assert window._stack.currentWidget() is page
    assert _days_total(page) == len(NAMES), "刚退回来，整库那几张就已经少了"


def test_a_scan_in_flight_does_not_land_on_the_drill_layer(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """扫描在途时点一格，那一遍扫描的结果**不许**把这一层换成整库（症状是「点开一格，
    出来一大片」）。

    这里把「在途」摆出来而不是等它真的在途：测试库太小扫描一瞬间就完了，等不到那一刻；
    而要验的正是**迟到的那一批**落地时会怎样。
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


def test_leaving_the_calendar_for_albums_lists_the_albums(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> None:
    """从日历切到「相册」要看到相册，不是整库平铺。

    日历那一页底下装的是**整库照片**，而「相册」这一层是**当前目录**，与那一份根本不是
    同一层；忘了重列，用户在相册视图里看到的就是整库平铺，开着分组时更离谱 ——
    那一份会被重新归拢成组卡片，**一个相册都没有**。
    """
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    make_photo("相册里一张.jpg", directory=root / "四月")
    prepare_library(root)
    session_base = tmp_path / "会话"

    window = _open(qt_app, root, session_base)
    page = window._calendar_page
    page.set_today(APRIL)
    page.go_today()
    assert wait_until(qt_app, lambda: window_is_idle(window) and page.photo_count() > 0)

    window.set_browse_mode("albums")
    assert wait_until(qt_app, lambda: window_is_idle(window)), "切到相册视图后没有收工"
    qt_app.processEvents()

    albums, photos = window._model.counts()
    assert albums == 1, f"相册视图里没有相册，摆的是 {window._model.rowCount()} 行整库照片"
    assert window._model.row_of_path(root / "四月") >= 0
    # 相册模式下只列当前层：相册里那一张不该出现在这一层（它在「四月」那一层里）
    assert photos == len(NAMES)
    # 当前目录说得出口，面包屑就该摆出来（这一条与换页是同一段：日历那一页把它收掉了）
    assert window._breadcrumb.isVisible()


def test_leaving_the_calendar_for_albums_shows_albums_next_to_the_groups(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> None:
    """开着「按拍摄日期分组」切到相册，**相册照样在**（组卡片排在它们后面）。

    与上一条同一个病根，分组让它更显眼：整库被归拢成组卡片之后网格上全是组，
    「相册」视图看上去像是没有相册这个概念。
    """
    root = tmp_path / "照片"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        make_photo(name, directory=root)
    make_photo("相册里一张.jpg", directory=root / "四月")
    prepare_library(root)
    session_base = tmp_path / "会话"

    window = _open(qt_app, root, session_base, group_facet="captured_day")
    page = window._calendar_page
    page.set_today(APRIL)
    page.go_today()
    assert wait_until(qt_app, lambda: window_is_idle(window) and page.photo_count() > 0)

    window.set_browse_mode("albums")
    assert wait_until(qt_app, lambda: window_is_idle(window)), "切到相册视图后没有收工"
    qt_app.processEvents()

    # 分组是用户自己选的，退出日历不该顺手取消它（见 ``set_browse_mode`` 里那一段）
    assert window._model.group_count() > 0
    assert window._model.row_of_path(root / "四月") >= 0, "分组把相册卡片顶掉了"


def test_leaving_the_calendar_selects_nothing(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """从日历下钻出来的那一层切到相册，**谁也不该选中**。

    日历与日历下钻那一层摆的都不是「当前目录」，所以换浏览方式是一次新的浏览；共用同一个
    层标识会让「还是这一层吗」误判成立。
    """
    window = calendar_window
    photos, label = _cell_of(window, 5)  # 五月两张挤在同一个小时 → 摊成一层网格
    window._calendar_page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._model.rowCount() == 2
    row = window._model.row_of_path(photos[0].path)
    window._grid.select_row(row)
    assert window._grid.genuinely_selected_rows() == [row], "前置条件：那一格是选中的"

    window.set_browse_mode("albums")
    assert wait_until(qt_app, lambda: window_is_idle(window)), "切到相册视图后没有收工"
    qt_app.processEvents()

    assert window._grid.genuinely_selected_rows() == [], "换浏览方式却把上一页选中的那格带过来了"


def test_a_group_facet_on_does_not_empty_the_calendar(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> None:
    """开着「按拍摄日期分组」切到日历，日历照样有照片。

    模型是网格与日历共用的，带着分组时它摆的是一张张**组卡片**，而日历只从模型里挑
    ``PhotoRef`` —— 挑到零张时整层空着，界面上看不出任何原因。
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


def _point(view: object, day: datetime.date, hour: float) -> QPoint:
    """周视图里某一天某一小时那一格的中心（时间轴内容坐标，与滚到哪儿无关）。"""
    column, row = view.column_width(), view.row_height()  # type: ignore[attr-defined]
    return QPoint(
        int(view.content_left() + (day - view.days()[0]).days * column + column / 2),  # type: ignore[attr-defined]
        int(hour * row + row / 2),
    )


def _click_hour_cell(
    qt_app: QApplication, window: MainWindow, day: datetime.date, hour: int
) -> None:
    """在周视图里点某一天某一小时的那一格。"""
    view = window._calendar_page.current_view()
    QTest.mouseClick(
        view._grid,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _point(view, day, hour),
    )
    qt_app.processEvents()


def _week_at(qt_app: QApplication, window: MainWindow, day: datetime.date) -> None:
    """切到周视图，并等它把滚动定位完 —— 用户点那一格时它早就落定了。"""
    page = window._calendar_page
    page.set_today(day)
    page.go_today()
    page.set_level("week")
    view = page.current_view()
    assert wait_until(qt_app, lambda: not view._reveal.isActive()), "周视图没有定位完"


def test_a_picked_empty_cell_shows_only_the_selection_tint(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """点中的那一格**只有那层底色**：不再多一个占位圆点。

    圆点那一套是「这一格有张照片、还没读出来」的占位，而这一格**根本没有照片** ——
    点它高亮，用户看见的却是「有一张还没出来」。
    """
    window = calendar_window
    page = window._calendar_page
    _week_at(qt_app, window, MARCH)
    view = page.current_view()
    assert not view.photos_in_hour(MARCH, 3), "前提不成立：三点那一格有照片"
    view.select_hour(MARCH, 3)
    qt_app.processEvents()

    from gallery.ui.calendar.layout import cell_rect

    image = QImage(view._grid.size(), QImage.Format.Format_RGBA8888)
    view._grid.render(image)
    column, row = view.column_width(), view.row_height()
    offset = (MARCH - view.days()[0]).days
    # 那一格**真正**落在哪儿（与画的时候同一个算法，别拍脑袋取点）
    x, y, width, height = cell_rect(column, row)
    left = view.content_left() + offset * column + x
    top = 3 * row + y
    painted = {
        image.pixelColor(int(left + width * ratio), int(top + height * 0.5)).name()
        for ratio in (0.2, 0.5, 0.8)
    }
    # 底色是半透明的，落到屏幕上要与窗口底色掺过 —— 所以只断言「**整格是同一个颜色**，
    # 且那个颜色不是底色」：多一个占位圆点就会有两种颜色。
    assert len(painted) == 1, f"那一格里不止一种颜色（多半是那个占位圆点）：{sorted(painted)}"
    assert painted != {view.background.name()}, "点中的那一格没有铺选中底色"


def test_the_pictures_survive_a_rescan_of_the_whole_library(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """从一格退回来（那一遍整库要重扫、重喂桶）之后，**已出图的格子仍然是照片**。

    退回来要重扫一遍整库，而扫描的每一步都会把新的一份桶喂给日历。原先每喂一次就把手上
    那份缩略图**全扔了**，于是这一屏先变成一片占位灰，等重新解码才回来。缩略图是按
    ``(路径, 档位)`` 记着的，换一遍库并不会让同一张照片变成另一张。
    """
    window = calendar_window
    page = window._calendar_page
    _cell_of(window, 4)  # 让日历这一页有数据
    view = page.current_view()
    day = datetime.date(2024, 4, 16)
    refs = list(view.photos_of(day))
    assert refs, "前提不成立：四月那天没有照片"
    image = QImage(48, 48, QImage.Format.Format_RGBA8888)
    image.fill(0x336699)
    for ref in refs:
        page.set_thumbnail(ref.path, image, page.thumbnail_edge())

    page.set_photos(refs, {ref.path: _facets(ref, day, 9) for ref in refs})
    qt_app.processEvents()

    assert all(view.photo(ref) is not None for ref in refs), "重喂一次桶就把图全扔了"


def _facets(ref, day: datetime.date, hour: int):  # type: ignore[no-untyped-def]
    from gallery.core.metadata.facets import PhotoFacets

    return PhotoFacets(
        captured_at=float(day.toordinal() * 86400 + hour * 3600),
        year=day.year,
        month=day.month,
        day=day.day,
        hour=hour,
    )


def test_a_double_click_does_not_bounce_back_out_of_the_drill(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """**双击一格：进去之后不会被立刻弹回来。**

    Qt 判定双击是应用级的事（日历上那一下按下已经算第一下），于是紧接着落到网格页的那一下
    被判成「网格里的第二下」；而日历那一格所在的位置在网格页里是**空白** ——
    「双击空白处退一层」当场把人带回日历。
    """
    window = calendar_window
    _week_at(qt_app, window, MAY)
    _click_hour_cell(qt_app, window, MAY, 9)
    assert window._stack.currentWidget() is window._grid_page

    # 第二下落在网格页里（模拟用户在同一处再点一下）
    QTest.mouseDClick(
        window._grid.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        # 那一格只摊出几张照片、摆在页面顶上：这个位置在这一页是空白
        QPoint(4, max(8, window._grid.viewport().height() - 8)),
    )
    _settle(qt_app, window)

    assert window._stack.currentWidget() is window._grid_page, "双击把刚摊开的那一格弹回去了"


def test_coming_back_from_a_photo_leaves_the_arrows_working(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """周视图里点进一张照片再退出来，**← / → 要还能翻周**。

    焦点必须点名交给周视图：退回来时 Qt 把焦点交给页面上第一个能接焦点的控件 —— 那一页
    里那是那个 ``QScrollArea``，它把 ← / → 当成滚动时间轴吃掉。月视图里落到的恰好是视图
    自己，所以只有周视图犯病。
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


def test_clicking_around_the_week_view_never_steals_the_arrows(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """**在周视图里点任何地方都不能把键盘抢走** —— 点格子、点表头、点那条竖滚动条。

    Qt 在鼠标按下时把焦点给「按到的那一层里第一个 focusPolicy != NoFocus 的控件」，而
    ``QScrollArea`` 与 ``QScrollBar`` 默认都是 StrongFocus：焦点一落上去 ← / → 就变成滚动
    时间轴，日历的「上一周 / 下一周」按多少次都没反应。
    """
    window = calendar_window
    page = window._calendar_page
    _week_at(qt_app, window, MARCH)
    view = page.current_view()

    QTest.mouseClick(
        view._grid,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _point(view, MARCH, 10.0),
    )
    _settle(qt_app, window)
    assert window._stack.currentWidget() is window._detail_page, "点那一格没有进大图页"
    QTest.keyClick(window._detail, Qt.Key.Key_Escape)
    _settle(qt_app, window)

    bar = view._area.verticalScrollBar()
    for widget, point, where in (
        (view._header, QPoint(40, 20), "表头"),
        (view._grid, _point(view, MARCH, 3.0), "空白的那一格"),
        (bar, QPoint(bar.width() // 2, int(bar.height() * 0.3)), "竖滚动条"),
    ):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        _settle(qt_app, window)
        assert qt_app.focusWidget() is view, f"点了{where}之后焦点被抢走了"
        before = page.anchor()
        QTest.keyClick(view, Qt.Key.Key_Right)
        _settle(qt_app, window)
        assert page.anchor() == before + datetime.timedelta(days=7), f"点了{where}之后 → 失灵"


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
        # 日历停在哪一层是**库级**偏好（ADR 0038 决策 4）：换个库打开时各自回到自己那一层。
        assert load_config(root).ui.resolved_calendar_level() == "week"
        assert reopened._calendar_page.level() == "week"
    finally:
        reopened.close()


def _settle_everything(qt_app: QApplication, window: MainWindow) -> None:
    """连检索那一遍与索引维护一起等到收工（``_settle`` 只管日历那几条链）。"""
    wait_for_idle(qt_app, window, also=("_search_job", "_index_job", "_staged"))


def test_a_search_from_the_calendar_shows_its_results_on_the_grid(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """检索结果横跨整库，所以它**只在网格页上摆**，日历那一页不参与。

    钉住这一半才钉得住另一半：撤掉条件之后回到日历，靠的是「条件没了」，
    不是「从日历进来的就回去」。
    """
    window = calendar_window
    window._on_search_submitted("三月")
    _settle_everything(qt_app, window)
    assert window._stack.currentWidget() is window._grid_page, "检索结果没有摆在网格页上"
    assert window._model.rowCount() == 1


def test_clearing_a_search_comes_back_to_the_calendar(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """撤光条件：回日历，**停在原来那一段**。

    撤掉的是那一条条件，不是用户停在哪一周 —— 而浏览方式本来就是日历，屏幕上却摆着
    「全部照片」：菜单里那个勾与眼前那一页说的不是同一件事。
    """
    window = calendar_window
    page = window._calendar_page
    page.set_today(MARCH)
    page.go_today()
    page.set_level("week")
    anchor_before = page.anchor()
    level_before = page.level()

    window._on_search_submitted("三月")
    _settle_everything(qt_app, window)
    assert window._stack.currentWidget() is window._grid_page

    window.clear_search_conditions()
    _settle_everything(qt_app, window)

    assert window._stack.currentWidget() is page, "条件撤光了却停在「全部照片」那一页"
    assert window._browse_mode == BROWSE_MODE_CALENDAR
    assert window._calendar_action.isChecked(), "浏览方式还是日历，菜单里的勾却没了"
    assert page.anchor() == anchor_before, "退回日历时位置被清成了今天"
    assert page.level() == level_before, "退回日历时连停在哪一层都变了"
    # 桶里是整库，不是刚才那一格，更不是那一条条件
    assert _days_total(page) == len(NAMES)
    assert page.photo_count() == 1  # 三月那张仍停在这段日子里


def test_clearing_the_last_condition_comes_back_to_the_calendar(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """**撤光最后一条条件就回到日历**，且停在原来那一段。

    走的是 ``pop_last_condition``（``⇧Esc``）而不再是 ``go_up``（``Esc``）—— 后者只管位置了
    （ADR 0040 决策 11）。**回来的那一步没变**，变的只是从哪个键走：撤光条件后那一层不存在了，
    于是摆在最前面的就是日历。

    「停在原来那一段」一起钉：日历的锚点不该被这一趟检索/撤销挪走。
    """
    window = calendar_window
    page = window._calendar_page
    page.set_today(MARCH)
    page.go_today()
    anchor_before = page.anchor()

    window._on_search_submitted("三月")
    _settle_everything(qt_app, window)
    assert window._stack.currentWidget() is window._grid_page

    window.pop_last_condition()
    _settle_everything(qt_app, window)

    assert not window._conditions
    assert window._stack.currentWidget() is page, "撤光之后没有退回日历"
    assert page.anchor() == anchor_before


def test_changing_the_order_on_the_calendar_stays_on_the_calendar(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """在日历上换排序，**留在日历**上（与撤条件是同一条规矩）。

    这两处原来是同一个病：换层一律把页面摆成网格页，而浏览方式还是日历。
    """
    window = calendar_window
    page = window._calendar_page
    page.set_today(MARCH)
    page.go_today()
    anchor_before = page.anchor()

    window.set_sort_descending(not window._sort_descending)
    _settle_everything(qt_app, window)

    assert window._stack.currentWidget() is page, "改一下排序就被甩到「全部照片」那一页"
    assert page.anchor() == anchor_before
    assert _days_total(page) == len(NAMES)


def test_a_search_from_a_calendar_cell_forgets_that_cell(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """在日历摊开的那一格上检索：那一格**不再是**「上一层」。

    网格页上摆的是横跨整库的结果；留着那份记账，条件撤光之后按 ``Esc`` 会退到一个他
    这一趟没去过的层。
    """
    window = calendar_window
    page = window._calendar_page
    photos, label = _cell_of(window, 5)  # 五月两张挤在同一个小时 → 摊成一层网格
    page.current_view().select_cell(photos, label)
    qt_app.processEvents()
    assert window._drill_photos is not None, "前置条件：日历那一格没有摊开"

    window._on_search_submitted("三月")
    _settle_everything(qt_app, window)
    assert window._drill_photos is None, "网格上摆的是整库结果，却还记着那是日历上的一格"

    window.clear_search_conditions()
    _settle_everything(qt_app, window)
    assert window._stack.currentWidget() is page
    assert not window._can_go_up(), "底下没有一层了，Esc 不该还退得回去"
    window.go_up()
    qt_app.processEvents()
    assert window._stack.currentWidget() is page, "Esc 把人送到一个他没去过的层"
