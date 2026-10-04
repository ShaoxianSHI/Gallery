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
from PySide6.QtGui import QImage  # noqa: E402
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


def _days_total(page: object) -> int:
    """日历那份桶里一共有几张（跨天加起来）。"""
    view = page.current_view()  # type: ignore[attr-defined]
    return sum(
        len(list(view.photos_of(day)))
        for day in (MARCH, APRIL, MAY)  # type: ignore[attr-defined]
    )


def test_switching_levels_leaves_no_cell_as_a_placeholder(
    qt_app: QApplication,
    calendar_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切到周视图之后，那一格**立刻**就有图 —— 不是灰的占位。

    三层要的档位互不相同（年 48 / 月 288 / 周 192 / 网格 176），而流水线的线程池里
    **同时只解那么几张**。切过去的那一刻，上一档位的那一次通常还在飞；曾经「同一张
    照片不能重复投递」是**按路径**一刀切的，于是新档位那一张被挡掉，在途的那一次回来
    时带着**旧边长**（谁都用不上），而新档位从此**没人再解** —— 要等碰巧又滚动一次才
    重新报上去。症状：刚切过来是灰的，切一下再切回来就好了（有时它自己又好了）。

    「上一档位还在飞」在这里是**摆出来**的而不是等出来的：把月视图那一档的解码卡在闸上
    （真机上正是从日历点开一格、或者从一个满屏的网格切过来时那样），于是周视图是自己
    走新那一次解出来的，还是干等着前一次收工，只看这一刻。
    """
    import threading

    import gallery.ui.thumbs.pipeline as pipeline_module

    real = pipeline_module.to_scaled_qimage
    gate = threading.Event()
    # 月视图这一档（288）—— 拿它当「旧档位」来卡闸
    month_edge = calendar_window._calendar_page.thumbnail_edge()

    def gated(image: object, edge: int) -> object:
        if edge == month_edge:
            gate.wait(5.0)  # 卡住，直到用例放行
        return real(image, edge)

    monkeypatch.setattr(pipeline_module, "to_scaled_qimage", gated)

    window = calendar_window
    page = window._calendar_page
    assert page.level() == "month"
    # 等夹具那几次解码收干净，下面要的是「此刻刚好有一次在飞」
    assert wait_until(qt_app, lambda: not window._pipeline._inflight), "夹具的解码没收工"
    in_flight = list(page.current_view().visible_refs())
    assert in_flight, "月视图这一屏没有要图的格子"

    window._pipeline.set_window(in_flight, target_edge=month_edge)  # 这一档在飞
    qt_app.processEvents()
    assert window._pipeline._inflight, "没排上任务，前提不成立"

    page.set_level("week")  # 周视图要另一个档位
    qt_app.processEvents()
    view = page.current_view()
    assert wait_until(qt_app, lambda: not view._reveal.isActive()), "定位没有落地"
    refs = list(view.visible_refs())
    assert refs, "周视图这一屏没有要图的格子"

    try:
        assert wait_until(
            qt_app, lambda: all(view.photo(ref) is not None for ref in refs), timeout=1.0
        ), f"新档位被在途的那一次挡住了：{[r.name for r in refs if view.photo(r) is None]}"
    finally:
        gate.set()  # 放行，别把工作线程留在闸上


def test_entering_a_drill_does_not_repoint_the_calendar_at_those_photos(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """摊开的那一层**不是**「这一层的照片」，拿它分桶等于告诉日历整个库就那几张。

    进网格是「先摆内容再换页」（理由见 ``_enter_calendar_drill``），于是换页那一刻
    日历还在前面，模型却已经被换成那几张 —— 重排立刻把日历重新分桶，整个换掉。
    于是其余格子当场空掉：症状是「点开一格再退回来，另外几格变灰了 / 变黑了」，
    而且要等退回来之后那一遍重扫把整库重新喂进去才恢复（真实库扫得久，于是看着像
    「切到下一周再切回来就好了」）。**这里不扫完就断言**：坏的时候就是那一刻坏。
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


def test_leaving_the_calendar_for_albums_lists_the_albums(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    fake_places: None,
    fake_facet_source: None,
) -> None:
    """从日历切到「相册」要看到相册，不是整库平铺。

    日历那一页底下装的是**整库照片**（点开一张要能按行找它，见 ``_enter_calendar``），
    所以退回「全部照片」可以直接复用那一份 —— 但「相册」这一层是**当前目录**，
    与那一份根本不是同一层。忘了重列，用户在相册视图里看到的就是整库平铺；
    开着分组时更离谱：那一份会被重新归拢成组卡片，**一个相册都没有**。
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
    """从日历下钻出来的那一层切到相册，**谁也不该选中**（ADR 0025 决策 6）。

    日历与日历下钻那一层摆的都不是「当前目录」，所以换浏览方式是一次新的浏览。
    日历曾经与相册模式共用同一个层标识，于是「还是这一层吗」会误判成立 ——
    网格上恢复出来的是下钻那一层停着的那一格，而那一张在新的一层里恰好也有。
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

    圆点那一套是「这一格有张照片、还没读出来」的占位（年 / 月视图里都这么画），而这一格
    **根本没有照片** —— 点它高亮，用户看见的却是「有一张还没出来」。
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

    退回来要重扫一遍整库，而扫描的每一步都会把新的一份桶喂给日历。原先每喂一次就把
    手上那份缩略图**全扔了**，于是这一屏先变成一片占位灰，等重新解码才回来 ——
    症状是「退回来之后有一格变灰了，切到下一周再切回来就好了」。缩略图是按
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

    Qt 判定双击是应用级的事（日历上那一下按下已经算第一下），于是紧接着落到网格页的
    那一下被判成「网格里的第二下」；而那一格只摊出了几张照片、摆在页面顶上，日历那一格
    所在的位置在网格页里是**空白** —— 于是「双击空白处退一层」当场把人带回日历。
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


def test_clicking_around_the_week_view_never_steals_the_arrows(
    qt_app: QApplication, calendar_window: MainWindow
) -> None:
    """**在周视图里点任何地方都不能把键盘抢走** —— 点格子、点表头、点那条竖滚动条。

    Qt 在鼠标按下时把焦点给「按到的那一层里第一个 focusPolicy != NoFocus 的控件」，
    而 ``QScrollArea`` 与 ``QScrollBar`` 默认都是 StrongFocus：焦点一落上去，← / → 变成
    滚动时间轴，日历的「上一周 / 下一周」按多少次都没反应（点一下滚动条就中招）。
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
        assert load_session(base=session_base).resolved_calendar_level() == "week"
        assert reopened._calendar_page.level() == "week"
    finally:
        reopened.close()
