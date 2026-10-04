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
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.ui.calendar.thumbs import ThumbStore  # noqa: E402
from gallery.ui.calendar.time_view import WeekView  # noqa: E402

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
