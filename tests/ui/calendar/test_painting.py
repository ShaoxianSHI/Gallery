"""``painting`` 那三份缓存：年视图一帧要 612 次取字体 / 度量 / 画笔。

**这不是「优化一下」而是「不许再犯」**：现造一次 ``QFont``、``QFontMetrics``、
``QPen(QColor(...))`` 各是几百纳秒，乘上 612 就是每帧好几毫秒，而这三种东西整轮里
其实只有个位数的取值。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.photos import PhotoRef  # noqa: E402
from gallery.core.metadata.facets import PhotoFacets  # noqa: E402
from gallery.ui.calendar import painting  # noqa: E402
from gallery.ui.calendar.page import CalendarPage  # noqa: E402
from gallery.ui.calendar.painting import draw_text, font_px, reset_caches  # noqa: E402


@pytest.fixture(autouse=True)
def clean_caches() -> None:  # type: ignore[misc]
    """每条用例前后都从空缓存开始 —— 缓存是模块级的，别的用例不该把结果带过来。"""
    reset_caches()
    yield
    reset_caches()


def test_the_same_size_and_weight_share_one_font() -> None:
    """同一个 ``(字号, 字重)`` 永远返回**同一个对象** —— 这就是缓存本身。"""
    assert font_px(11) is font_px(11)
    assert font_px(11, QFont.Weight.DemiBold) is font_px(11, QFont.Weight.DemiBold)


def test_a_different_size_or_weight_is_a_different_font() -> None:
    assert font_px(11) is not font_px(12)
    assert font_px(11) is not font_px(11, QFont.Weight.DemiBold)
    assert font_px(11).pixelSize() == 11
    assert font_px(11, QFont.Weight.DemiBold).weight() == QFont.Weight.DemiBold


def test_draw_text_only_reads_the_font_it_is_given(qt_app: QApplication) -> None:
    """整套缓存的前提：``draw_text`` **读**那个字体，不就地改它。

    ``font_px`` 返回的是共享的那一份，而 PySide6 的 ``QFont`` 按值传递 ——
    真被改一次，整轮的字号都跟着变，症状是「某些格子的字忽然变大了」，
    极难往这一行上想。所以这条钉住「唯一的消费方只读它」。
    """
    del qt_app
    font = font_px(13)
    surface = QImage(64, 24, QImage.Format.Format_ARGB32)  # **要留着**：QPainter 只借它一个指针
    painter = QPainter(surface)
    draw_text(painter, QRectF(0, 0, 64, 24), "2024年", font, QColor("#334455"), elide=False)
    painter.end()

    assert font.pixelSize() == 13
    assert font.weight() == QFont.Weight.Normal


def test_the_metrics_of_equal_fonts_are_shared() -> None:
    assert painting._font_metrics(font_px(12)) is painting._font_metrics(font_px(12))


def test_each_colour_object_gets_its_own_pen() -> None:
    """颜色按**对象**记（理由见 :func:`painting._solid_pen`）。

    等值的两个 ``QColor`` 各记各的 —— 只是多一支笔；**同一个对象**必须拿到同一支，
    因为画笔是可变的，拿到一份去改就会污染整轮。
    """
    red = QColor("#ff0000")
    other_red = QColor("#ff0000")

    assert painting._solid_pen(red) is painting._solid_pen(red)
    assert painting._solid_pen(red) is not painting._solid_pen(other_red)
    assert painting._solid_pen(red) is not painting._solid_pen(QColor("#0000ff"))
    assert painting._solid_pen(red).color() == red


def test_the_caches_are_bounded() -> None:
    """拿算出来的字号当键（年视图的日期徽标就是）不能把缓存撑爆。"""
    for size in range(1, painting._CACHE_LIMIT * 3):
        font_px(size)
        painting._solid_pen(QColor(size % 256, 0, 0))

    assert len(painting._fonts) <= painting._CACHE_LIMIT
    assert len(painting._pens) <= painting._CACHE_LIMIT


def test_one_year_frame_leaves_only_a_handful_of_entries(qt_app: QApplication) -> None:
    """实跑一帧年视图：缓存里该留下的是**个位数**的条目，而不是 612 个。"""
    page = _year_page()
    view = page.current_view()
    view.resize(1200, 800)
    view.show()
    view.render(QPixmap(view.size()))

    assert len(painting._fonts) <= 12, painting._fonts.keys()
    assert len(painting._pens) <= 24, len(painting._pens)


def _year_page() -> CalendarPage:
    """一整年铺满照片的年视图（页面要留着 —— 视图是它的 C++ 子对象）。"""
    page = CalendarPage()
    page.set_today(datetime.date(2021, 9, 12))
    page.go_today()
    page.set_level("year")
    refs: list[PhotoRef] = []
    values = {}
    for index in range(60):
        ref = PhotoRef(path=Path(f"/tmp/p{index}.jpg"), size=1, mtime_ns=0)
        day = datetime.date(2021, 1, 1) + datetime.timedelta(days=index)
        refs.append(ref)
        values[ref.path] = PhotoFacets(
            captured_at=float(day.toordinal() * 86400),
            year=day.year,
            month=day.month,
            day=day.day,
        )
    page.set_photos(refs, values)
    return page
