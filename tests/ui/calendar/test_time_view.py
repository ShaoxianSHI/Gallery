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
