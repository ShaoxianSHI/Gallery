"""月视图：格子与照片是两回事 —— **占位格里不摆邻月的照片**。

9 月 1 号是周三，于是那张 6 × 7 的表前面要有 8 月 29–31 号、后面要有 10 月 1–9 号。
那一屏回答的是「九月拍了什么」，而占位格里摆的照片是**邻月的**：10 月 1 号那张照片
出现在九月这一屏上，看起来就还像九月的。

于是有四处要改，而只改「画」那一处最容易漏 —— 数张数、向流水线要缩略图、
点下去展开，都还在拿那张 42 天的表当分母。
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.calendar import month_matrix  # noqa: E402
from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.ui.calendar.month_view import MonthView  # noqa: E402
from gallery.ui.calendar.thumb_store import ThumbStore  # noqa: E402

TODAY = datetime.date(2021, 9, 12)
#: 九月自己的一天 —— 它那一格就该有照片。
INSIDE = datetime.date(2021, 9, 12)
#: **占位格**：10 月 1 号落在九月那张 6 × 7 的末尾（2021 年 9 月有 30 天）。
FILLER = datetime.date(2021, 10, 1)

SEPTEMBER = PhotoRef(path=Path("/tmp/september.jpg"), size=1, mtime_ns=0)
OCTOBER = PhotoRef(path=Path("/tmp/october.jpg"), size=1, mtime_ns=0)


def _values(ref: PhotoRef, day: datetime.date) -> PhotoFacets:
    return PhotoFacets(
        captured_at=float(day.toordinal() * 86400),
        year=day.year,
        month=day.month,
        day=day.day,
    )


@pytest.fixture
def view(qt_app: QApplication) -> MonthView:  # noqa: ARG001 - 控件要有 QApplication 才存在
    widget = MonthView(ThumbStore())
    widget.set_today(TODAY)
    widget.set_anchor(TODAY)
    widget.set_buckets({INSIDE: [SEPTEMBER], FILLER: [OCTOBER]}, {})
    widget.resize(900, 700)
    return widget


def _cell_of(day: datetime.date) -> int:
    return month_matrix(TODAY).index(day)


def _asked_days(view: MonthView, monkeypatch: pytest.MonkeyPatch) -> list[datetime.date]:
    """这一帧里「取过照片」的那些天 —— 画一格照片必然先问它那天有几张。"""
    asked: list[datetime.date] = []
    original = view.photos_of

    def record(day: datetime.date) -> Sequence[PhotoRef]:
        asked.append(day)
        return original(day)

    monkeypatch.setattr(view, "photos_of", record)
    view.render(QPixmap(view.size()))
    return asked


def _click(view: MonthView, day: datetime.date) -> list[tuple[list[PhotoRef], str]]:
    """点那一格，返回摊出来的东西（点占位格时它该是空的）。"""
    opened: list[tuple[list[PhotoRef], str]] = []
    view.cell_requested.connect(lambda photos, label: opened.append((photos, label)))
    point: QPointF = view._day_cell(_cell_of(day)).center()  # type: ignore[attr-defined]
    view.mousePressEvent(  # type: ignore[arg-type]
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
    return opened


def test_the_filler_day_really_is_on_the_month_grid() -> None:
    """前提：**10 月 1 号确实摆在九月这一屏上**。

    钉这一条是因为下面几条都在说「它不该摆照片」—— 哪天占位格真的去掉了，
    那几条会空转成绿的，而「邻月的照片摆在别人的月份里」这个毛病却还在原地。
    """
    days = month_matrix(TODAY)
    assert days[0] == datetime.date(2021, 8, 29)  # 前面补了三天
    assert FILLER in days
    assert days[-1] == datetime.date(2021, 10, 9)  # 后面补了九天
    assert FILLER in days[_cell_of(INSIDE) + 1 :], "前提不成立：补位格不在九月自己那几天的后面"


def test_a_filler_cell_is_not_painted(view: MonthView, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _asked_days(view, monkeypatch)
    assert INSIDE in asked, "前提不成立：九月自己那一天没画照片"
    assert FILLER not in asked, "10 月 1 号那张照片被摆进了九月这一屏"


def test_a_filler_cell_is_not_counted(view: MonthView) -> None:
    """停在九月，九月的张数是 1 —— 10 月 1 号那张不算。"""
    assert view.photo_count() == 1


def test_the_thumbnail_window_leaves_the_filler_cells_out(view: MonthView) -> None:
    """为不画的格子解码出来的缩略图，永远不会被画出来。"""
    assert view.visible_refs() == [SEPTEMBER]


def test_clicking_a_filler_cell_lays_nothing_out(view: MonthView) -> None:
    """那一格看着是空的，摊出来的却是下个月那天的照片 —— 不如什么都不做。"""
    assert _click(view, INSIDE) == [([SEPTEMBER], "9月12日")], "前提不成立：自己那格点不动"
    assert _click(view, FILLER) == []
