"""周视图那一个「延一轮再定位」的定时器。

它原先是 ``QTimer.singleShot(0, self._apply_scroll)`` —— **两参**形态，那个定时器
与本对象毫无关系。于是「按了 ``W`` → 同一轮里 ⌘Q 关窗」这种再平常不过的时序会让它
照常触发，去调一个已经析构的 ``_apply_scroll``，换来一条
``Internal C++ object already deleted``（同一个道理见 ``browser/grid.py`` 的
``eventFilter``）。

更糟的是那一版的副产物：那之前还有一个布尔闩锁 ``_scrolling``，定时器没跑成就永远
卡在「正在定位」，``_reveal_photos`` 从此次次早退 —— **周视图再也找不到它的第一张
照片**，而用户看到的只是一条空荡荡的时间轴。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import shiboken6  # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QImage, QRegion  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.ui.calendar.thumbs import ThumbStore  # noqa: E402
from gallery.ui.calendar.time_view import HOURS_PER_DAY, WeekView  # noqa: E402

ANCHOR = datetime.date(2021, 9, 12)
#: 一周里的第一个小时有照片 —— 定位应该把滚动条推到 0 行前面一点。
PHOTO_HOUR = 14


def _ref(name: str) -> PhotoRef:
    return PhotoRef(path=Path(name), size=1, mtime_ns=0)


def _buckets(refs: list[PhotoRef], hour: int = PHOTO_HOUR) -> tuple[dict, dict]:
    by_day = {ANCHOR: refs}
    by_hour = {(ANCHOR, hour): refs}
    return by_day, by_hour


def test_the_reveal_never_reaches_a_view_that_is_already_gone(
    qt_app: QApplication,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """排了定位之后立刻把控件删掉 —— 那一轮不许去调一个已经析构的方法。

    症状是一条 ``RuntimeError: Internal C++ object ... already deleted`` 从定时器里
    逃出来。PySide6 把它打到 ``stderr`` 上就放过（不转成测试失败），所以这里读
    ``stderr`` —— 这正是它当年能一直没人看见的原因。
    """
    view = WeekView(ThumbStore())
    view.set_anchor(ANCHOR)  # 排一个定位
    shiboken6.delete(view)  # 还没等事件循环就被销毁（≈ 按了 W 之后同一轮里 ⌘Q）
    qt_app.processEvents()

    assert "already deleted" not in capsys.readouterr().err


def test_repeated_anchor_changes_still_land_on_the_first_photo(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """连着按 ← / → 只定位一次，而且以**最后一次**的锚点为准。

    这一条钉的是那个闩锁不许把定位吃掉：闸门一旦卡住（或者连按几下只排了第一次
    却没跑成），滚动条就永远停在 0，周视图里那一周明明有照片却一片空白。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_buckets([_ref("a.jpg"), _ref("b.jpg")]))
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()

    for _ in range(5):
        view.set_anchor(ANCHOR)
    for _ in range(4):
        view.set_anchor(ANCHOR + datetime.timedelta(days=7))
    view.set_anchor(ANCHOR)  # 最后一次说了算

    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]
    assert view._area.verticalScrollBar().value() > 0, "没有滚到第一张照片所在的那个小时"


def test_the_reveal_happens_again_when_the_photos_arrive_after_it(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """拍摄时间**晚到**时（真的如此：后台读完才喂桶），定位要补一次。

    定位是 :meth:`WeekView.set_anchor` 排的，而刚切到周视图那一刻桶多半还是空的 ——
    定位对着空桶跑完，把滚动条按回 0 就不管了：这一周明明有照片、屏幕却停在 00:00。
    而「看得见哪些」正是按那个滚动位置算的，于是要的那几张压根没人要图（症状：切到
    某一周时缩略图只剩一片占位灰，切到下一周再切回来才正常）。
    """
    view = WeekView(ThumbStore())
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()

    view.set_anchor(ANCHOR)  # 此刻桶还是空的
    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]
    assert view._area.verticalScrollBar().value() == 0

    view.set_buckets(*_buckets([_ref("a.jpg"), _ref("b.jpg")]))  # 照片到了

    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]
    assert view._area.verticalScrollBar().value() > 0, "数据晚到，没有补一次定位"


def test_a_landed_reveal_is_not_repeated_when_the_buckets_change(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """已经定位过的那一次**不该**被数据打回来：用户滚到哪就是哪。

    喂桶在换库、重扫、模型重排时都会来一遍（那一遍扫描的每一步都算一次变化），
    无条件补一次定位于是把用户正在看的位置一次次拽回第一张照片。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_buckets([_ref("a.jpg")]))
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)
    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]

    bar = view._area.verticalScrollBar()
    bar.setValue(max(0, bar.value() - 300))  # 用户自己往上滚了一段
    where = bar.value()
    view.set_buckets(*_buckets([_ref("a.jpg"), _ref("b.jpg")]))

    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]
    assert bar.value() == where, "已经定位过的那一次被数据拽回去了"


def test_a_second_reveal_while_one_is_pending_does_not_lose_it(
    qt_app: QApplication,  # noqa: ARG001
) -> None:
    """已经排着一个的时候再排一次 —— 闸门**续期**而不是各排各的。

    排成两个互不相干的定位没有害处，但会让「正好排了一个」这件事不再等于
    ``isActive()``；而那句判断正是「这一轮还有没有没落地的定位」的唯一依据。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_buckets([_ref("a.jpg")]))

    view.set_anchor(ANCHOR)
    assert view._reveal.isActive()
    view.set_anchor(ANCHOR + datetime.timedelta(days=7))

    assert view._reveal.isActive(), "第二次定位把第一次顶掉了 —— 仍然只剩一个在排队"


def _two_photos() -> tuple[dict, dict]:
    """同一天 08:00 与 20:00 各一张 —— 定位该落在 08:00 上方。"""
    refs = [_ref("a.jpg"), _ref("b.jpg")]
    return {ANCHOR: refs}, {(ANCHOR, 8): refs[:1], (ANCHOR, 20): refs[1:]}


def test_the_reveal_does_not_count_as_landed_before_the_scroll_bar_has_a_range(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """**没有范围就不算落地** —— 算了就再也没有第二次。

    切到这一层时，报窗口与定位常常跑在**内部布局之前**（``setCurrentWidget`` 之后
    滚动区的布局是延后处理的）：那一刻滚动条的最大值还是 0，``setValue`` 设了也没用。
    而「已落地」这道闸一关上，定位就再也补不回来 —— 这一层永远停在 00:00，而
    「看得见哪些」又是按这个位置算的，照片所在的那些格子**压根没人要图**
    （症状：切到某一周是一片占位灰）。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_two_photos())
    view.set_anchor(ANCHOR)  # 排一个定位
    view._apply_scroll()  # 手跑一次 = 定时器抢在布局前面（真机上有这个窗口）

    assert not view._revealed, "滚动条还没有范围，这一次不算落地"

    view.resize(900, 600)
    view.show()
    assert wait_for(lambda: not view._reveal.isActive())  # type: ignore[operator]
    assert view._area.verticalScrollBar().value() > 0, "内部几何定了之后没有补一次定位"


def test_a_relayout_inside_the_week_reports_the_window_again(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """**内部几何变了就重报一次**（不是整层被 resize 的那种）。

    可见区是按「网格宽度 → 行高」「视口高度」「滚动位置」算的，而前两个要等**内部布局**
    跑过才定。切到这一层的那一次报窗口跑在布局前面时，算出来的是**另一批格子**；布局跑完
    之后要是没人再报一次，那一批就永远没人要图 —— 屏幕上是占位灰。

    这里只动**内部那个时间轴**的宽度：整层尺寸没变（所以 `WeekView.resizeEvent` 不响），
    滚动范围也没变（所以滚动去抖那一路不响）—— 能报出窗口的只有「盯着内部几何」这一路。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_two_photos())
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()
    view._geometry_notify.stop()

    reports: list[int] = []
    view.window_changed.connect(lambda: reports.append(1))
    grid = view._grid
    grid.resize(max(1, grid.width() - 60), grid.height())

    assert wait_for(lambda: bool(reports)), "内部几何变了，却没有再报一次窗口"


def test_the_header_is_repainted_when_the_viewport_changes_width(
    qt_app: QApplication,
    wait_for: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """竖滚动条占位把视口挤窄之后，**表头必须重画** —— 否则它一直停在旧列宽上。

    表头那七列是按 ``column_width()`` 摆的，而那个数取自**时间轴的宽度**：滚动条一出现，
    视口就窄掉一条滚动条，列宽跟着变，**而表头自己一个像素都没动**。Qt 只在控件自身尺寸
    变化时替它重画，于是表头停在旧列宽上 —— 症状正是「表头与时间轴对不齐，往右越差越多」，
    差值累到最后一列就是一条滚动条的宽度（实测 1200 宽的窗口：149.14 vs 147.0）。

    **只能靠「有没有人标脏」来钉**：``grab()`` / ``render()`` 会强制重画，屏幕上那份陈旧
    像素在像素探针里永远看不见 —— 真机上它是这样被抓到的：表头连续 5 次重画全发生在
    滚动条占位之前，之后一次都没有。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_two_photos())
    # 起手让滚动条不占位置：视口宽 = 整层宽（= 滚动条还没出现的那一刻）
    view._area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()

    wide = view.column_width()
    asked: list[float] = []
    monkeypatch.setattr(
        type(view._header), "update", lambda self, *a: asked.append(view.column_width())
    )

    # 滚动条出现 —— 时间轴跟着视口一起窄掉，列宽变了
    view._area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    assert wait_for(lambda: view.column_width() != wide), "滚动条没有改变视口宽度，这一条就白测了"
    assert asked, "视口窄了，表头却没被要求重画 —— 它会一直停在旧列宽上"
    assert asked[-1] == view.column_width(), "表头是按**旧**列宽重画的，还是对不齐"


def test_a_delivered_thumbnail_asks_the_timeline_to_repaint(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一张缩略图到了，**要让画着格子的那个控件重画** —— 周视图里是时间轴。

    这一条钉的是一个真出现过的症状：图明明已经在仓库里（``photo()`` 不是 ``None``），
    屏幕上却一直是占位灰，而且**等多久都不变**，一滚动或一换层就出来。
    原因是那次重画问错了对象 —— ``CalendarPage`` 收到图之后重画的是**这一层**
    （``WeekView.update()``），而格子是里面那个 ``_TimeGrid`` 画的；月 / 年视图画在
    自己身上，所以只有周视图犯病（滚动与换层会强制重画那一块，于是「一滑动就出来」）。

    （测试环境里 ``update()`` 本来就不会产生 paint 事件 —— 离屏平台不会 expose 窗口。
    所以这里钉的是**契约**：画格子的那个控件必须被标脏。）
    """
    view = WeekView(ThumbStore())
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)
    qt_app.processEvents()

    asked: list[str] = []
    monkeypatch.setattr(type(view._grid), "update", lambda self, *a: asked.append("grid"))
    monkeypatch.setattr(type(view), "update", lambda self, *a: asked.append("view"))

    view.repaint_content()

    assert "grid" in asked, "没有让时间轴重画 —— 图在手上，屏幕上还是占位灰"


def test_the_page_repaints_whatever_draws_the_cells(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """页面收到缩略图之后那次重画，走的必须是 :meth:`LevelView.repaint_content`。

    （同上：测试环境看不见 paint 事件，所以钉的是「谁被要求重画」。）
    """
    from gallery.ui.calendar.page import CalendarPage

    page = CalendarPage()
    page.set_today(ANCHOR)
    page.go_today()
    page.set_level("week")
    page.resize(900, 600)
    page.show()
    qt_app.processEvents()

    asked: list[str] = []
    view = page.current_view()
    monkeypatch.setattr(type(view), "repaint_content", lambda self: asked.append("content"))
    page._repaint_current_view()

    assert asked == ["content"], "收到图之后没有让「画格子的那个控件」重画"


def test_the_reveal_lands_before_the_first_paint(
    qt_app: QApplication,
    wait_for: object,
) -> None:
    """定位**当场落位**，不留一帧「还在旧位置」的画面。

    落在 0 ms 定时器上时，第一帧已经按旧位置画出来了，下一轮才跳过去 —— 用户看见的
    是内容从上面窜下来（周视图里就是「一张图飞快跑下来」）。切过来 / 翻页的这一刻，
    滚动条的范围其实已经有了，所以先同步试一次。
    """
    view = WeekView(ThumbStore())
    view.set_buckets(*_two_photos())
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()
    view._area.verticalScrollBar().setValue(0)
    view._revealed = False

    view.set_anchor(ANCHOR)  # ← 不转事件循环

    assert view._area.verticalScrollBar().value() > 0, "定位没有当场落位，第一帧会画在旧位置上"
    assert not view._reveal.isActive(), "已经落位了还排着一次多余的定位"


def test_switching_levels_reveals_before_returning(
    qt_app: QApplication,
) -> None:
    """切层时**先摆上这一层再定位**：函数返回时滚动条已经在合适的位置。

    （`set_anchor` 早于 `setCurrentWidget` 的话，定位那一刻这一层还没被布局过 ——
    范围是 0，只能排到下一轮。）
    """
    from gallery.ui.calendar.page import CalendarPage

    page = CalendarPage()
    page.set_today(ANCHOR)
    page.go_today()
    refs = [_ref("a.jpg"), _ref("b.jpg")]
    values = {
        ref.path: PhotoFacets(
            captured_at=float(ANCHOR.toordinal() * 86400 + hour * 3600),
            year=ANCHOR.year,
            month=ANCHOR.month,
            day=ANCHOR.day,
            hour=hour,
        )
        for ref, hour in zip(refs, (8, 20), strict=True)
    }
    page.set_photos(refs, values)
    page.resize(900, 600)
    page.show()
    qt_app.processEvents()

    page.set_level("week")  # ← 不转事件循环

    view = page.current_view()
    assert view._area.verticalScrollBar().value() > 0, "切层返回时还停在旧位置上"


def test_a_cell_waiting_for_its_thumbnail_is_not_a_grey_block(
    qt_app: QApplication,
) -> None:
    """有照片、图还没到的那一格：**画一个安静的圆点，不是满格灰块**。

    「满格灰块 → 小一圈的照片」这一对帧看起来就是闪一下、尺寸还在变（照片按比例缩在
    格子里摆，比整格小）。月视图同一个状态画的是圆点，所以它不闪 —— 周视图对齐过来。
    """
    from PySide6.QtGui import QImage

    from gallery.ui.calendar.layout import cell_rect

    view = WeekView(ThumbStore())
    view.set_buckets({ANCHOR: [_ref("a.jpg")]}, {(ANCHOR, 8): [_ref("a.jpg")]})
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)
    qt_app.processEvents()

    bar = view._area.verticalScrollBar()
    bar.setValue(7 * int(view.row_height()))  # 让 08:00 那一格落在屏幕上
    qt_app.processEvents()

    # **前提摆出来**：那一格在屏幕上、有照片、图还没到（不然下面两条断言会空转绿）
    row_px = view.row_height()
    assert bar.value() <= 8 * row_px <= bar.value() + view._area.viewport().height(), (
        "08:00 那一格不在屏幕上，这条用例什么也没验到"
    )
    waiting = view.photos_in_hour(ANCHOR, 8)[0]
    assert view.photo(waiting) is None, "前提不成立：那一格的图已经到了"

    shot = QImage(view._grid.size(), QImage.Format.Format_RGBA8888)
    view._grid.render(shot)
    x, y, width, height = cell_rect(view.column_width(), row_px)
    left = int(view.content_left() + x)
    # **渲染的是网格自己**：滚动只影响视口那一层，网格坐标始终是 `hour * row`
    top = int(8 * row_px + y)
    background = view.background.name()

    corner = shot.pixelColor(left + int(width * 0.15), top + int(height * 0.15)).name()
    assert corner == background, f"那一格被铺满了（{corner}）—— 图一到就会缩一圈，看着闪"

    painted = sum(
        1
        for dx in range(int(width))
        for dy in range(int(height))
        if shot.pixelColor(left + dx, top + dy).name() != background
    )
    assert painted < width * height * 0.05, f"那一格画了 {painted} 个点，不止一个小圆点"


def test_the_now_line_stays_inside_todays_own_column(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「现在」那条线**只画在今天那一格里**，不一路划到最右边。

    原先它的终点是整条时间轴的右沿，于是「今天现在几点」看着像一条从今天往后延伸的
    进度线 —— 而右边那几天与它无关，那几天各有各的钟点。
    """
    from PySide6.QtGui import QImage

    view = WeekView(ThumbStore())
    view.resize(900, 600)
    view.show()
    qt_app.processEvents()

    # **钉在一个不在整点上的钟点**：整点上这条线正好压着那一行的横网格线，
    # 像素探针就分不清量到的到底是线还是线。
    monkeypatch.setattr(WeekView, "now_row", lambda self: 10.5)

    days = view.days()
    index = days.index(view.today())
    column = view.column_width()
    left = view.content_left()
    background = view.background.name()

    shot = QImage(view._grid.size(), QImage.Format.Format_RGBA8888)
    view._grid.render(shot)
    y = int(view.now_row() * view.row_height())

    # **一像素宽的线是带抗锯齿的**：落点常常被底色掺过一手，于是钉的是
    # 「这里有画东西 / 这里什么都没有」，不是「这里的颜色恰好等于笔的颜色」。
    inside = int(left + (index + 0.5) * column)
    painted = {shot.pixelColor(inside, y + dy).name() for dy in (-1, 0, 1)}
    assert painted != {background}, "今天那一格里没有画「现在」那条线"
    for neighbour in (index - 1, index + 1):
        if not 0 <= neighbour < len(days):
            continue
        x = int(left + (neighbour + 0.5) * column)
        around = {shot.pixelColor(x, y + dy).name() for dy in (-1, 0, 1)}
        assert around == {background}, f"那条线划出了今天那一格，落到了 {days[neighbour]} 上面"


def _full_week(view: WeekView, per_hour: int = 1) -> None:
    """一周七列、每小时都塞上照片（这样每一格都要画，跑不掉）。"""
    by_day: dict = {}
    by_hour: dict = {}
    for day in view.days():
        refs = [
            _ref(f"{day}_{hour}_{index}.jpg")
            for hour in range(HOURS_PER_DAY)
            for index in range(per_hour)
        ]
        by_day[day] = refs
        for hour in range(HOURS_PER_DAY):
            by_hour[(day, hour)] = [ref for ref in refs if f"_{hour}_" in ref.path.name]
    view.set_buckets(by_day, by_hour)


def _paint_counting(view: WeekView) -> list[int]:
    """数「这一帧真的画了几格」——装一个假的 ``_paint_hour_row``。"""
    calls: list[int] = []
    grid = view._grid
    real = grid._paint_hour_row

    def counting(*args: object, **kwargs: object) -> None:
        calls.append(1)
        real(*args, **kwargs)  # type: ignore[arg-type]

    grid._paint_hour_row = counting  # type: ignore[method-assign]
    return calls


def test_a_partial_repaint_only_draws_the_hours_it_can_show(
    qt_app: QApplication,
) -> None:
    """**局部重画不许走满 24 行。**

    缺陷现场：那一段循环原来无条件走满 25 条小时线 + 7×24 = 168 格，于是时间轴有多高
    就画多高。可它高 2218 px 而视口只有 724 px —— **七成四的活画在屏幕外头**。

    之所以要紧，是因为 :meth:`WeekView.update` 在**每张缩略图回来时**都调一次
    ``self._grid.update()``，而一次 ``update()`` 标脏整块。实测一周 336 张照片逐张到达
    = 336 次整块重画，paintEvent 累计 290 ms。

    断的是**形状**：一次局部重画只许碰看得见的那几行。量时间会随机器飘，量格数不会。
    """
    view = WeekView(ThumbStore())
    view.resize(1000, 780)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)
    _full_week(view)
    qt_app.processEvents()

    grid = view._grid
    calls = _paint_counting(view)
    band = QRect(0, 0, grid.width(), view._area.viewport().height())
    shot = QImage(grid.size(), QImage.Format.Format_RGBA8888)
    grid.render(shot, QPoint(0, 0), QRegion(band))  # 局部重画
    calls.clear()

    grid.render(shot, QPoint(0, 0), QRegion(band))

    row = view.row_height()
    hours_on_screen = min(HOURS_PER_DAY, int(band.height() // row) + 2)
    ceiling = len(view.days()) * hours_on_screen + len(view.days())  # 每格 + 一天的余量
    assert 0 < len(calls) <= ceiling, (
        f"只重画了可见的 {band.height()} px，却碰了 {len(calls)} 格（上限 {ceiling}）"
        " —— 七成四的活画在屏幕外头了"
    )


def test_a_full_repaint_still_draws_every_hour(qt_app: QApplication) -> None:
    """整块重画（窗口刚露出来、切页回来）**不许**因为收口而漏画。"""
    view = WeekView(ThumbStore())
    view.resize(1000, 780)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)
    _full_week(view)
    qt_app.processEvents()

    grid = view._grid
    calls = _paint_counting(view)
    shot = QImage(grid.size(), QImage.Format.Format_RGBA8888)
    grid.render(shot, QPoint(0, 0), QRegion(grid.rect()))

    assert len(calls) == len(view.days()) * HOURS_PER_DAY, (
        f"整块重画只碰了 {len(calls)} 格，应是 {len(view.days()) * HOURS_PER_DAY} 格"
        " —— 少画的地方在屏幕上就是空白"
    )


def test_no_clip_region_means_draw_everything(qt_app: QApplication) -> None:
    """**拿不到裁剪区就当整块都可见。**

    宁可多画，不可在该画的地方留空白 —— 而这里曾经真会一格都不画：
    裁剪区只在 ``event.region()`` 上，Qt **不会**把它设到 paintEvent 里我们自己新建的
    painter 上（实测 ``painter.clipBoundingRect()`` 是空矩形），于是照着它算会得出
    「第一行 0、最后一行 -1」。
    """
    view = WeekView(ThumbStore())
    view.resize(1000, 780)
    view.show()
    qt_app.processEvents()
    view.set_anchor(ANCHOR)

    assert view._grid._visible_hours(None, view.row_height()) == (0, HOURS_PER_DAY - 1)
    assert view._grid._visible_hours(object(), view.row_height()) == (
        0,
        HOURS_PER_DAY - 1,
    )
