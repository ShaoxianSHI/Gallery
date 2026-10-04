"""年视图一帧里那些「算一次就够」的东西。

**12 张小月的日期矩阵在一帧之内是不变的**：42 个 ``datetime.date`` × 12。
原先每张小月算两遍（数张数一遍 ``days_of``、画格子一遍 ``month_matrix``），
于是每帧重建 24 × 42 = 1008 个 ``date`` —— 而这只是一次 ``paintEvent``。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.ui.calendar import year_view as year_view_module  # noqa: E402
from gallery.ui.calendar.page import CalendarPage  # noqa: E402


@pytest.fixture
def page(qt_app: QApplication) -> CalendarPage:  # noqa: ARG001 - 造控件的就是它
    widget = CalendarPage()
    widget.set_today(datetime.date(2021, 9, 12))
    widget.go_today()
    widget.set_level("year")
    refs: list[PhotoRef] = []
    values = {}
    for index in range(60):
        ref = PhotoRef(path=Path(f"/tmp/year{index}.jpg"), size=1, mtime_ns=0)
        day = datetime.date(2021, 1, 1) + datetime.timedelta(days=index)
        refs.append(ref)
        values[ref.path] = PhotoFacets(
            captured_at=float(day.toordinal() * 86400),
            year=day.year,
            month=day.month,
            day=day.day,
        )
    widget.set_photos(refs, values)
    widget.resize(1200, 800)
    return widget


def test_one_frame_builds_each_month_matrix_once(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12 张小月 → **12 次**，不是 24 次（每张小月要算两遍的那个版本）。"""
    view = page.current_view()
    calls: list[datetime.date] = []
    original = year_view_module.month_matrix

    def counted(anchor: datetime.date) -> list[datetime.date]:
        calls.append(anchor)
        return original(anchor)

    monkeypatch.setattr(year_view_module, "month_matrix", counted)
    view.render(QPixmap(view.size()))

    assert len(calls) == 12, f"一帧里算了 {len(calls)} 次日期矩阵"


def test_a_frame_still_draws_every_day_of_the_year(
    page: CalendarPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """矩阵只算一遍之后，**该画的一格都不能少**（12 × 42 = 504 个日期数字）。

    这是「算一次就够」最容易踩的坑：把两次遍历合并成一次时，很容易顺手把
    「数张数」那一遍里的东西一起丢了。
    """
    view = page.current_view()
    drawn: list[str] = []
    original = year_view_module.draw_text

    def recorded(painter, rect, text, font, color, *args, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append(text)
        return original(painter, rect, text, font, color, *args, **kwargs)

    monkeypatch.setattr(year_view_module, "draw_text", recorded)
    view.render(QPixmap(view.size()))

    assert sum(1 for text in drawn if text.isdigit()) == 504, len(drawn)
    # 12 张小月各一个「N 月」标题；表头是「星期一」之类取最后一个字（84 个）。
    assert sum(1 for text in drawn if text.endswith("月")) == 12, len(drawn)
