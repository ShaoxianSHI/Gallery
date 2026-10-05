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
from PySide6.QtGui import QColor, QFontMetrics, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui import theme  # noqa: E402
from gallery.ui.banner_overlay import (  # noqa: E402
    AUTO_HIDE_SECONDS,
    MARGIN_TOP,
    MAX_WIDTH,
    MIN_AVOID_WIDTH,
    BannerOverlay,
    banner_rect,
    banner_width,
    text_height,
)
from gallery.ui.panels.metadata import PANEL_WIDTH  # noqa: E402

#: 算术测试用的那一块「内容区」。
AREA = QRect(0, 100, 1200, 700)

#: 大图页上摆着元数据面板时的那一块（**与内容区同一套坐标** —— 两者都量在窗口上）。
PANEL = QRect(900, 0, PANEL_WIDTH, 800)


# ---- 1. 算术 ----------------------------------------------------------------


def test_the_banner_stops_growing_at_a_readable_width() -> None:
    """:data:`MAX_WIDTH`：再宽就成了段落，提示没那么多字。"""
    assert banner_width(AREA) == MAX_WIDTH
    assert banner_width(QRect(0, 0, MAX_WIDTH * 3, 700)) == MAX_WIDTH


def test_a_narrow_window_narrows_the_banner_rather_than_overflowing_it() -> None:
    """窄窗口里换行，而不是横着铺出去 —— 铺出去的话右半句就没了。"""
    width = banner_width(QRect(0, 0, 320, 400))
    assert 0 < width < 320


def test_the_banner_sits_centred_on_the_top_edge() -> None:
    """**横向居中、纵向浮在内容区顶边下方** —— 那一带是面包屑所在的一行（ADR 0029）。"""
    rect = banner_rect(AREA, QSize(400, 30))
    assert rect.width() == 400
    assert rect.left() == AREA.width() // 2 - 200, "应当水平居中"
    assert rect.top() == AREA.top() + MARGIN_TOP, "应当贴着顶边"
    assert rect.bottom() < AREA.bottom(), "压的是面包屑那一带，不是最后一排缩略图"


def test_the_banner_does_not_sit_on_the_window_edge() -> None:
    """不贴边：贴在最上沿会看着像标题栏的一部分，贴最下沿像状态栏（那个刻意不建）。"""
    rect = banner_rect(AREA, QSize(400, 30))
    assert rect.top() > AREA.top()
    assert rect.bottom() + 1 < AREA.bottom() + 1


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


# ---- 1b. 让开元数据面板（位置不动，只收窄）------------------------------------


def test_the_banner_is_centred_on_the_window_even_with_the_panel_open() -> None:
    """**居中的基准是整个窗口**：面板摆着时横幅不该偏到照片那一半的正中间（用户反馈）。

    原先的做法是把 ``area`` 减去面板，于是中心点跟着跑偏 —— 大图页上一句话摆在照片
    正中央，看着像只对照片说话。这一条量的是**中心点**，宽度对不对量的是它右边那条。
    """
    area = QRect(0, 0, 1200, 800)
    rect = banner_rect(area, QSize(MAX_WIDTH, 30), avoid=PANEL)
    assert abs(rect.center().x() - area.center().x()) <= 1, (
        f"横幅没有按整个窗口居中：中心 {rect.center().x()}，窗口 {area.center().x()}"
    )
    # 旧做法（减去面板）的中心点在照片区正中，两者差着整整半个面板。
    photo_area = QRect(area)
    photo_area.setRight(PANEL.left() - 1)
    assert rect.center().x() - photo_area.center().x() >= PANEL_WIDTH // 2


def test_a_centred_banner_stops_short_of_the_panel_instead_of_covering_it() -> None:
    """两件都要：**位置一分不动**，宽度让开 —— 面板不该被盖住（它正显示当前这张的 EXIF）。"""
    area = QRect(0, 0, 1200, 800)
    rect = banner_rect(area, QSize(MAX_WIDTH, 30), avoid=PANEL)
    assert rect.right() < PANEL.left(), f"横幅 {rect} 压到了面板 {PANEL} 上"
    assert rect.left() > area.left(), "仍然要留在窗口里"


def test_a_window_wide_enough_for_it_keeps_its_full_width() -> None:
    """够宽时面板压根碰不着横幅，那条 :data:`MAX_WIDTH` 仍然赢 —— 窗口 1280 是默认尺寸。"""
    area = QRect(0, 0, 1280, 800)
    panel = QRect(1280 - PANEL_WIDTH, 0, PANEL_WIDTH, 800)
    assert banner_width(area, avoid=panel) == MAX_WIDTH


def test_it_stops_narrowing_before_it_becomes_a_vertical_strip() -> None:
    """收到 :data:`MIN_AVOID_WIDTH` 以下就不再让了。

    两三字一行、连着五六行的竖条不像提示，比它短暂压住面板左边缘更难认 —— 那种窗口
    （已经窄到最小宽度 720）本来就是勉强摆得下元数据面板的。
    """
    area = QRect(0, 0, 720, 800)
    panel = QRect(720 - PANEL_WIDTH, 0, PANEL_WIDTH, 800)
    assert banner_width(area, avoid=panel) == MIN_AVOID_WIDTH


def test_a_panel_that_is_not_there_changes_nothing() -> None:
    """没给 ``avoid``（面板收着）与给了一块不相交的那一块：宽度与原先一模一样。"""
    area = QRect(0, 0, 1200, 800)
    assert banner_width(area, avoid=None) == banner_width(area)
    assert banner_width(area, avoid=QRect(1400, 0, 300, 800)) == banner_width(area)


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
    assert rect.top() == area.top() + MARGIN_TOP


def test_the_widget_honours_the_rectangle_it_is_told_to_avoid(
    qt_app: QApplication, host: QWidget
) -> None:
    """``reposition(area, avoid)`` 那一支不是只有算术那一半：控件自己也得照办。"""
    banner = BannerOverlay(host)
    host.resize(1200, 800)
    area = QRect(0, 0, 1200, 800)
    panel = QRect(900, 0, PANEL_WIDTH, 800)
    banner.reposition(area, panel)
    banner.show_message("已撤销，恢复为「海边.jpg」。")
    qt_app.processEvents()

    rect = banner.geometry()
    assert abs(rect.center().x() - area.center().x()) <= 1, "应当按整个窗口居中"
    assert not rect.intersects(panel), f"横幅 {rect} 压到了面板上"


def test_opening_the_thing_it_avoids_shortens_it_in_place(
    qt_app: QApplication, host: QWidget
) -> None:
    """面板一展开，已经显示着的那一条要**当场收窄**（不重新 ``show_message``）。

    用户展开面板时那句话往往还挂在上面 —— 等下一条消息来才摆正，中间那几秒就白压着
    面板了（``reposition`` 与 ``show_message`` 都要走 ``_place``，两条路都指着它）。
    """
    banner = BannerOverlay(host)
    host.resize(1200, 800)
    area = QRect(0, 0, 1200, 800)
    panel = QRect(900, 0, PANEL_WIDTH, 800)
    banner.reposition(area)
    banner.show_message("已撤销，恢复为「海边.jpg」。")
    qt_app.processEvents()
    before = banner.geometry()

    banner.reposition(area, panel)
    qt_app.processEvents()

    after = banner.geometry()
    assert after != before, "面板展开后横幅没有重摆"
    assert after.width() < before.width(), "让开面板要收窄，不是挪位置"
    assert not after.intersects(panel)


def _columns_with_ink(image: QImage) -> tuple[int, int]:
    """图里带字的列，最左与最右各是哪一列。

    判据是「这一列里有没有一个像素**足够接近字色**」—— 不去猜底色是什么（它跟着主题，
    还掺了 16% 的橙），于是浅色深色两套主题下都是同一份判法。
    """
    text = theme.current_theme().text

    def distance(color: QColor) -> int:
        return (
            (color.red() - text.red()) ** 2
            + (color.green() - text.green()) ** 2
            + (color.blue() - text.blue()) ** 2
        )

    limit = 60**2
    columns = [
        x
        for x in range(image.width())
        if min(distance(image.pixelColor(x, y)) for y in range(image.height())) < limit
    ]
    return (columns[0], columns[-1]) if columns else (-1, -1)


def test_the_words_sit_in_the_middle_of_the_banner(qt_app: QApplication, host: QWidget) -> None:
    """**字要居中**：横幅的宽度是「装得下就多宽」，左对齐的话窄横幅永远偏在一侧。

    这里**画出来量像素**，而不是只看 ``alignment()`` —— 用户看见的是墨迹落在哪儿，
    「标志位设了」与「看着是歪的」中间还隔着换行、内边距与几何。
    """
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 800, 600))
    banner.show_message("已重命名为「海边日落.jpg」。")
    qt_app.processEvents()

    width = banner.width()
    assert width > 0, "横幅不该是零宽"
    left, right = _columns_with_ink(banner.grab().toImage())
    assert left >= 0, "一个字都没画出来"
    # 容忍几像素：量的是**墨迹**，而首尾两个字的左右边距（bearing）本身就不等，
    # 换行排版还会再添一点零头。左对齐时这个差是上百像素，两者不会混。
    slack = max(4, width // 40)
    assert abs(left - (width - 1 - right)) <= slack, (
        f"字没有居中：左 {left} / 右 {width - 1 - right}（横幅宽 {width}）"
    )


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
