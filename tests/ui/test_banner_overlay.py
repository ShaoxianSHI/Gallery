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
import re
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSize  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.ui import theme  # noqa: E402
from gallery.ui.viewer.metadata import PANEL_WIDTH  # noqa: E402
from gallery.ui.window.widgets.banner_overlay import (  # noqa: E402
    AUTO_HIDE_SECONDS,
    FILL_RGB,
    FILL_SHARE,
    MARGIN_TOP,
    MAX_WIDTH,
    MIN_AVOID_WIDTH,
    BannerOverlay,
    banner_rect,
    banner_width,
    text_height,
    tinted,
)

#: 算术测试用的那一块「内容区」。
AREA = QRect(0, 100, 1200, 700)

#: 大图页上摆着元数据面板时的那一块（**与内容区同一套坐标** —— 两者都量在窗口上）。
PANEL = QRect(900, 0, PANEL_WIDTH, 800)

#: macOS 深浅两套系统色，与 ``tests/ui/test_theme.py`` 那份同源：底是 ``Base``，字是
#: ``WindowText``。横幅的底色与字色就是从这两个角色算出来的，所以两套都得量。
LIGHT_BACKGROUND = QColor(0xFF, 0xFF, 0xFF)
DARK_BACKGROUND = QColor(0x17, 0x17, 0x17)
LIGHT_TEXT = QColor(0x1A, 0x1A, 0x1A)
DARK_TEXT = QColor(0xEC, 0xEC, 0xEE)
APPEARANCES = [
    pytest.param(LIGHT_BACKGROUND, LIGHT_TEXT, id="light"),
    pytest.param(DARK_BACKGROUND, DARK_TEXT, id="dark"),
]

#: WCAG AA 对正文的对比度下限。
MIN_CONTRAST = 4.5

#: 样式表里 ``rgba(r, g, b, a)`` 那一种写法。
RGBA = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)")


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
    """**居中的基准是整个窗口**：面板摆着时横幅不该偏到照片那一半的正中间。

    ``area`` 要是减去面板，中心点就跟着跑偏 —— 大图页上一句话摆在照片正中央，看着像只对照片
    说话。这一条量的是**中心点**，宽度对不对量的是它右边那条。
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
    """没给 ``avoid``（面板收着）与给了一块不相交的那一块：宽度一模一样。"""
    area = QRect(0, 0, 1200, 800)
    assert banner_width(area, avoid=None) == banner_width(area)
    assert banner_width(area, avoid=QRect(1400, 0, 300, 800)) == banner_width(area)


# ---- 1c. 实色底 -------------------------------------------------------------


def _declared_colors(sheet: str) -> tuple[QColor, QColor, QColor]:
    """样式表里那三个颜色，按写进去的顺序：底、边框、字。"""
    colors: list[QColor] = []
    for match in RGBA.finditer(sheet):
        color = QColor(*(int(part) for part in match.groups()[:3]))
        color.setAlphaF(float(match.group(4)))
        colors.append(color)
    assert len(colors) == 3, f"样式表里的颜色不止这三个：{sheet}"
    return colors[0], colors[1], colors[2]


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        level = value / 255
        return level / 12.92 if level <= 0.04045 else ((level + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast(first: QColor, second: QColor) -> float:
    """两个颜色之间的对比度（WCAG 那一条算式）。"""
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_stylesheet_asks_for_no_transparency(qt_app: QApplication) -> None:
    """写进样式表的底、边框、字都是实色 —— 留着 alpha 就等于让底下那张照片掺进来。"""
    fill, edge, text = _declared_colors(BannerOverlay().styleSheet())
    assert fill.alpha() == 255
    assert edge.alpha() == 255
    assert text.alpha() == 255


def test_the_flat_colour_is_the_old_one_laid_on_the_background() -> None:
    """实底取的是「同一个比例铺在主题底色上」—— 从前那一片橙的观感没换。

    浅色下是那片淡奶白，深色下是那片深棕，两者的分量都锁定在这里：改了比例就是在改用户
    每天看到的那对橙色，那该是一次有意的改动。
    """
    light = tinted(LIGHT_BACKGROUND, FILL_RGB, FILL_SHARE)
    dark = tinted(DARK_BACKGROUND, FILL_RGB, FILL_SHARE)
    assert light.alpha() == 255 and dark.alpha() == 255
    assert (light.red(), light.green(), light.blue()) == (255, 242, 219)
    assert (dark.red(), dark.green(), dark.blue()) == (60, 47, 24)


@pytest.mark.parametrize(("background", "text"), APPEARANCES)
def test_the_words_are_far_enough_from_the_flat_colour(background: QColor, text: QColor) -> None:
    """深浅两套主题下，正文色对这块实底的对比度都够读（WCAG AA）。

    底色跟着照片走、字色只跟着主题走时，这两个变量会各自落到一头去；实底之后这一对由
    主题算出来，这条守住「算出来之后也够远」。
    """
    assert _contrast(tinted(background, FILL_RGB, FILL_SHARE), text) >= MIN_CONTRAST


def test_the_css_colour_is_readable_on_the_theme_it_runs_on(qt_app: QApplication) -> None:
    """上面那两条用的是平台色；这一条量的是控件**此刻真的写进去**的那一对。"""
    fill, _, text = _declared_colors(BannerOverlay().styleSheet())
    assert _contrast(fill, text) >= MIN_CONTRAST


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

    展开面板时那句话往往还挂在上面 —— 等下一条消息来才摆正，中间那几秒就白压着面板了
    （``reposition`` 与 ``show_message`` 都要走 ``_place``，两条路都指着它）。
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

    判据是「这一列里有没有一个像素明显**不同于横幅自己的底色**」。不按「离字色有多近」判：
    抗锯齿的边是字色与底色的混合，离纯字色可以很远，于是细笔画那一端会整列漏掉、首尾不对称。
    底与边框的差远小于字与底的差（外层两行边框因此不算），深浅两套主题都是同一份判法。
    """
    # 左内边距里那一块：越过边框，离第一个字还远。
    background = image.pixelColor(6, image.height() // 2)

    def distance(color: QColor) -> float:
        return (
            (color.red() - background.red()) ** 2
            + (color.green() - background.green()) ** 2
            + (color.blue() - background.blue()) ** 2
        ) ** 0.5

    columns = [
        x
        for x in range(image.width())
        if max(distance(image.pixelColor(x, y)) for y in range(1, image.height() - 1)) > 100
    ]
    return (columns[0], columns[-1]) if columns else (-1, -1)


def test_the_words_sit_in_the_middle_of_the_banner(qt_app: QApplication, host: QWidget) -> None:
    """**字要居中**：横幅的宽度是「装得下就多宽」，左对齐的话窄横幅永远偏在一侧。

    这里**画出来量像素**，而不是只看 ``alignment()`` —— 看见的是墨迹落在哪儿，
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


# ---- 2b. 压在照片上 ----------------------------------------------------------


class _Backdrop(QWidget):
    """一块自己刷成纯色的底：横幅压在照片上，这里用深 / 浅两块代替照片。"""

    def __init__(self, color: QColor) -> None:
        super().__init__()
        self._color = color
        self.resize(1200, 800)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)


def _fill_over(qt_app: QApplication, photo: QColor) -> QColor:
    """把横幅摆在 ``photo`` 那么一块底的上面，取一个只剩底色、还没有字的像素。"""
    host = _Backdrop(photo)
    host.show()
    banner = BannerOverlay(host)
    banner.reposition(QRect(0, 0, 1200, 800))
    banner.show_message("已重命名为「海边日落.jpg」。")
    qt_app.processEvents()

    rect = banner.geometry()
    # 左边那条内边距：越过边框，离第一个字还隔着 PADDING_X 那么宽。
    color = host.grab().toImage().pixelColor(rect.left() + 6, rect.center().y())
    assert color.isValid(), "没取到像素"
    host.close()
    return color


def test_the_flat_background_hides_whatever_photo_is_behind_it(qt_app: QApplication) -> None:
    """**画出来是实的**：同一句话压在深色与浅色照片上，看到的必须是同一个颜色。

    底色半透明时，字清不清楚由底下那张照片决定 —— 实底之后这个变量就不存在了。
    顺带锁定颜色：它得是从前 16% 的橙压在本应用底色上那一片，别换成另一套橙。
    """
    over_light = _fill_over(qt_app, QColor(0xFF, 0xFF, 0xFF))
    over_dark = _fill_over(qt_app, QColor(0x00, 0x00, 0x00))
    assert over_light == over_dark, (
        f"底色还透着底下那张照片：{over_light.name()} / {over_dark.name()}"
    )

    expected = tinted(theme.current_theme().background, FILL_RGB, FILL_SHARE)
    assert over_light == expected, (
        f"实底是 {over_light.name()}，从前那一片橙是 {expected.name()} —— 看着已经不像原来那对了"
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

    那时定时器必须一起停：不然它会在**下一条**消息刚显示时原样再收一次。
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
