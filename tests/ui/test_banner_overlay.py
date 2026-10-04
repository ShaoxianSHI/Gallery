"""覆盖在内容上的那一条横幅（ADR 0029）。

分两块：

1. **算术**（:func:`banner_width` / :func:`banner_rect` / :func:`text_height`）—— 纯函数，
   单独测，不必造窗口。与 ``title_bar.editor_rect`` 同一做法。
2. **控件本身**：摆在哪、会不会自己收起来、外部收掉之后定时器还算不算。

**等的是条件，不是秒数**：自动收起那条用 ``wait_for`` 转到「不再可见」为止 ——
定时器给的是 0.05 秒的量级，条件成立就立刻返回，快机器慢机器都一样。
"""

from __future__ import annotations

import os
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSize  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui.banner_overlay import (  # noqa: E402
    AUTO_HIDE_SECONDS,
    MARGIN_BOTTOM,
    MAX_WIDTH,
    BannerOverlay,
    banner_rect,
    banner_width,
    text_height,
)

#: 算术测试用的那一块「内容区」。
AREA = QRect(0, 100, 1200, 700)


# ---- 1. 算术 ----------------------------------------------------------------


def test_the_banner_stops_growing_at_a_readable_width() -> None:
    """:data:`MAX_WIDTH`：再宽就成了段落，提示没那么多字。"""
    assert banner_width(AREA) == MAX_WIDTH
    assert banner_width(QRect(0, 0, MAX_WIDTH * 3, 700)) == MAX_WIDTH


def test_a_narrow_window_narrows_the_banner_rather_than_overflowing_it() -> None:
    """窄窗口里换行，而不是横着铺出去 —— 铺出去的话右半句就没了。"""
    width = banner_width(QRect(0, 0, 320, 400))
    assert 0 < width < 320


def test_the_banner_sits_centred_and_floats_above_the_bottom_edge() -> None:
    rect = banner_rect(AREA, QSize(400, 30))
    assert rect.width() == 400
    assert rect.left() == AREA.width() // 2 - 200, "应当水平居中"
    assert rect.bottom() + 1 + MARGIN_BOTTOM == AREA.bottom() + 1, "应当浮在底边上方一点"


def test_the_banner_never_sticks_out_of_the_area_it_was_given() -> None:
    """窗口窄到摆不下给定尺寸时，宁可缩窄也不越界。"""
    rect = banner_rect(QRect(0, 0, 200, 100), QSize(MAX_WIDTH, 30))
    assert rect.left() >= 0 and rect.right() <= 199


def test_the_banner_is_as_wide_as_the_text_needs() -> None:
    """窄的那一句不该被拉成一整条 —— 宽度上限是「装得下」，不是「占满」。"""
    rect = banner_rect(AREA, QSize(banner_width(AREA), 30))
    assert rect.width() <= MAX_WIDTH


def test_a_long_message_wraps_instead_of_being_cut(qt_app: QApplication) -> None:
    """长消息换行时变高，而不是被裁掉半行（高度是按宽度算出来的）。"""
    metrics = QFontMetrics(qt_app.font())
    text = (
        "已把 3 项移到废纸篓，但其中一项没移走：「一张 2.jpg」早就不在了；"
        "⌘Z 可以撤销上面那两项，完整列表在日志里。"
    )
    one_line = text_height(text, MAX_WIDTH * 4, metrics)
    wrapped = text_height(text, MAX_WIDTH, metrics)
    assert wrapped > one_line, "放得下就该是一行，放不下就要换行"
    assert text_height(text, MAX_WIDTH // 2, metrics) > wrapped, "更窄就该换更多行"


def test_an_empty_message_still_takes_one_line(qt_app: QApplication) -> None:
    """空消息不该画成一个两像素的条 —— 摆在那儿像出了什么界面问题。"""
    metrics = QFontMetrics(qt_app.font())
    assert text_height("", MAX_WIDTH, metrics) >= metrics.height()


# ---- 2. 控件 ----------------------------------------------------------------


@pytest.fixture
def host(qt_app: QApplication) -> QWidget:
    """一块当作窗口用的父控件：横幅是它的孩子，与它在 ``MainWindow`` 里的身份一样。"""
    parent = QWidget()
    parent.resize(1200, 800)
    parent.show()
    yield parent
    parent.close()


def test_showing_a_message_does_not_touch_the_area_underneath(
    qt_app: QApplication, host: QWidget
) -> None:
    """**横幅出现时不挪底下任何一样东西**（ADR 0029 决策 1）—— 它压根不在任何布局里。"""
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 1200, 800))
    qt_app.processEvents()
    before = host.geometry()

    banner.show_message("已把 3 项移到废纸篓。⌘Z 可以撤销。", auto_hide_seconds=0.05)
    qt_app.processEvents()

    assert banner.isVisible()
    assert host.geometry() == before
    assert banner.parent() is host, "横幅必须挂在窗口上，不是某一页里"


def test_the_message_is_placed_inside_the_area_it_was_given(
    qt_app: QApplication, host: QWidget
) -> None:
    banner = BannerOverlay(host)
    area = QRect(0, 0, 800, 600)
    banner.reposition(area)
    banner.show_message("已重命名为「海边日落.jpg」。")
    qt_app.processEvents()

    rect = banner.geometry()
    assert area.contains(rect), f"横幅 {rect} 摆到了 {area} 外面"
    assert rect.bottom() + 1 + MARGIN_BOTTOM == area.bottom() + 1


def test_a_message_that_is_only_going_to_be_read_once_leaves_by_itself(
    qt_app: QApplication, host: QWidget, wait_for: Callable[..., bool]
) -> None:
    """说过就走：确认类的那几句过 :data:`AUTO_HIDE_SECONDS` 自己收起来（ADR 0029 决策 3）。"""
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 800, 600))
    banner.show_message("已重命名为「海边日落.jpg」。", auto_hide_seconds=0.05)
    qt_app.processEvents()

    assert banner.auto_hide_pending
    assert wait_for(lambda: not banner.isVisible()), "到点没收起来"


def test_a_message_that_needs_reading_stays(qt_app: QApplication, host: QWidget) -> None:
    """出错、这一层是空的、后台进度 —— 都不给 ``auto_hide_seconds``，就一直在。"""
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 800, 600))
    banner.show_message("读取元数据失败，按默认顺序显示：…")
    qt_app.processEvents()

    assert not banner.auto_hide_pending


def test_a_new_message_replaces_the_one_before_it(qt_app: QApplication, host: QWidget) -> None:
    """横幅是全局共享的一个槽：新的一句盖掉旧的，旧的定时器不许再收新的。"""
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 800, 600))
    banner.show_message("已重命名为「一.jpg」。", auto_hide_seconds=0.05)
    qt_app.processEvents()

    banner.show_message("没能粘贴：磁盘满了")
    qt_app.processEvents()

    assert banner.text() == "没能粘贴：磁盘满了"
    assert not banner.auto_hide_pending, "先前的定时器不该把留着的那一句也收掉"


def test_hiding_it_from_outside_stops_the_countdown(
    qt_app: QApplication, host: QWidget, wait_for: Callable[..., bool]
) -> None:
    """换层、换库、进度收尾都是从外面直接 ``hide()`` 的（见 ``MainWindow``）。

    那时定时器必须一起停：不然它会在**下一条**消息刚上屏时原样再收一次。
    """
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 800, 600))
    banner.show_message("正在读取 128 张照片的元数据…", auto_hide_seconds=0.05)
    qt_app.processEvents()

    banner.hide()
    qt_app.processEvents()
    assert not banner.auto_hide_pending
    assert not wait_for(lambda: banner.auto_hide_pending, timeout=0.2)


def test_the_countdown_is_long_enough_to_read_one_sentence() -> None:
    """收起来是产品决定，不是随手填的一个数：给这个值定一条下限，别让人调到 0.5 秒。"""
    assert AUTO_HIDE_SECONDS >= 3.0
