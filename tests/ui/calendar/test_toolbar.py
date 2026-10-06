"""日历工具条：换外观之后那一行的字色跟没跟过去。

工具条带着样式表，于是 ``QStyleSheetStyle`` 给「工具条连同它的子控件」这一支塞了
一份**自己的**调色板 —— :func:`gallery.ui.theme.apply_theme` 下发的调色板到不了它们。
而页面是在 ``apply_theme`` **之后**才重新铺样式表的，于是每次换外观它都在替这一支
**照上一次那份调色板**重画一遍。

**原先那一行只有按钮的字色写在样式表里，标题那个 QLabel 没写**（规则还错写成
``QPushButton#title``，从加进来那天起就没匹配上过），于是症状是：用户在深色下用着
Gallery，把系统切到浅色 —— 别处都白了，只有标题那一行整行看不见。用户原话
「浅色模式下……这些文字还是浅色，看不到了」。

两个要紧的地方，都是这条 bug 教出来的：

- **起手的外观必须先定下来，页面才拿得到对的那一份。** 这一支的调色板是在**建控件
  那一刻**取的；测试要是拿当前（浅色）那份建页面，再切深、再切浅，那么「切到浅色」
  那一轮里这一支本来就是浅色的那份 —— 症状压根不出现，用例一路绿。
- **量画出来的像素，不量 ``label.palette()``**：出问题时两者是**不一致的**
  （``palette()`` 已经是新那一档，画的却还是旧那一档），量调色板会一路绿。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterator

import pytest

from gallery.ui.calendar import CalendarPage

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolButton  # noqa: E402

TODAY = datetime.date(2024, 5, 16)

#: macOS 深色那一档（``ui.theme`` 模块文档里量过的那两个系统色）。
DARK_WINDOW = QColor(0x32, 0x32, 0x32)
DARK_BASE = QColor(0x17, 0x17, 0x17)
#: 浅色：窗口那层 ``#ececec``，内容那层白。
LIGHT_WINDOW = QColor(0xEC, 0xEC, 0xEC)
LIGHT_BASE = QColor(0xFF, 0xFF, 0xFF)

#: 标题是 22px 的**大字**，WCAG 对大字（≥24px 常规 / ≥18.66px 粗体）要的是 3:1。
#: 取最低那一档而不是 AA 正文的 4.5:1：这一行量的是「跟没跟过去」，深浅两套配色
#: 真实都在 12:1 上下，真出问题时它掉到 1.0:1 那一头，离阈值远得很。
MIN_CONTRAST = 3.0


def _switch(qt_app: QApplication, *, window: QColor, base: QColor) -> None:
    """模拟系统换外观 —— 平台就是这么把新调色板写进应用的。

    **只 ``setPalette`` + 转事件循环，不自己调 ``page._on_theme_changed``**：换外观
    那一下平台发的是 ``paletteChanged``，``ThemeWatcher`` 接到之后排一个 0ms 定时器，
    等这一批事件走完才下发底色并发 ``changed`` —— 页面是被那个信号带上的。
    （``apply_theme`` 在前、页面重铺样式表在后，**这个先后就是那个 bug 的成因**。）
    手工调那两个方法等于把顺序抄了一遍，抄错了与没测一样。
    """
    palette = QPalette(qt_app.palette())
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, base)
    dark = base.lightnessF() < 0.5
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(role, QColor(0xEC, 0xEC, 0xEE) if dark else QColor(0x1A, 0x1A, 0x1A))
    qt_app.setPalette(palette)
    qt_app.processEvents()


@pytest.fixture
def page_in(qt_app: QApplication) -> Iterator[Callable[..., CalendarPage]]:
    """按「起手是什么外观」建日历那一页 —— 外观必须**先定下来**。跑完还原。"""
    original = QPalette(qt_app.palette())

    def _make(*, window: QColor, base: QColor) -> CalendarPage:
        _switch(qt_app, window=window, base=base)
        page = CalendarPage()
        page.set_today(TODAY)
        page.go_today()
        page.resize(1100, 700)
        page.show()
        qt_app.processEvents()
        return page

    yield _make
    qt_app.setPalette(original)
    qt_app.processEvents()


def _luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def _contrast(first: QColor, second: QColor) -> float:
    brighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (brighter + 5) / (darker + 5)


def _title_contrast(page: CalendarPage) -> tuple[float, str]:
    """工具条上**标题那一块**的字与底的对比度，附一句给人看的说明。

    取的是**整页**截图里标题 ``QLabel`` 覆盖的那一块：那一块里除了字就是底色，
    所以**最深的一端是字、铺得最多的一端是底**。

    **量整页，不单独 ``bar.grab()``**：工具条自己不铺底色（它是透的，底下是整页
    那一层），单独抓它会拿 ``QStyleSheetStyle`` 给这一支塞的那份调色板把底铺上 ——
    而那一份正是出问题的那一份，于是量出来的是「工具条自己以为的底色」，不是屏幕上
    那一块（浅色下它仍是深色那一档的底，量出 5.6:1 这种看着还行、其实量错了的数）。
    """
    label = page._bar._title
    image: QImage = page.grab().toImage()
    left, top = label.mapTo(page, label.rect().topLeft()).toTuple()
    pixels = [
        image.pixelColor(left + dx, top + dy)
        for dx in range(label.width())
        for dy in range(label.height())
    ]
    ink = min(pixels, key=lambda color: _luminance(color))
    background = max(pixels, key=lambda color: _luminance(color))
    return _contrast(ink, background), f"字 {ink.name()} 压在底 {background.name()} 上"


def test_dark_to_light_keeps_the_title_readable(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """深色起手 → 切浅色：标题那一行不许停在深色那一档（用户报的那一条）。"""
    page = page_in(window=DARK_WINDOW, base=DARK_BASE)
    _switch(qt_app, window=LIGHT_WINDOW, base=LIGHT_BASE)

    contrast, described = _title_contrast(page)
    assert contrast >= MIN_CONTRAST, (
        f"切到浅色之后标题只有 {contrast:.2f}:1（{described}）—— 浅底上的白字看不见。"
        "工具条带着样式表，下发的调色板到不了这一支，字色必须写在样式表里"
    )


def test_light_to_dark_keeps_the_title_readable(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """浅色起手 → 切深色：反过来也不许停在浅色那一档（同一个毛病的另一头）。"""
    page = page_in(window=LIGHT_WINDOW, base=LIGHT_BASE)
    _switch(qt_app, window=DARK_WINDOW, base=DARK_BASE)

    contrast, described = _title_contrast(page)
    assert contrast >= MIN_CONTRAST, (
        f"切到深色之后标题只有 {contrast:.2f}:1（{described}）—— 深底上的深字看不见"
    )


@pytest.fixture
def shown_page(qt_app: QApplication) -> Iterator[CalendarPage]:
    """造一个**真的显示着**的页面。

    ``isVisible()`` 要求祖先全都可见，而这些用例没把页面放上屏幕 —— 于是一条
    「有条件时那一块该可见」会红，而它其实已经 ``setVisible(True)`` 了。问「有没有被显式收起来」
    （``isHidden()``）才量的是我们真正写下的那一句。
    """
    page = CalendarPage()
    page.resize(1000, 700)
    page.show()
    qt_app.processEvents()
    try:
        yield page
    finally:
        page.deleteLater()


def test_the_chips_show_before_the_level_buttons(
    qt_app: QApplication, shown_page: CalendarPage
) -> None:
    """条件胶囊摆在**年 / 月 / 周左边**，不是右边（ADR 0040 决策 9）。

    右边那一头就是分段选择器：胶囊按录入顺序从右往左排开的话，最后几枚会**压在那三个按钮
    上面** —— 两组控件叠在同一处，而叠着的那一组是永远点不到的那个（用户原话：「和相册视图
    一致的话会和这几个按钮重叠」）。

    量的是**两组的左右次序**，不是某个坐标：胶囊在左、分段器在右，这个关系一旦反过来立刻红。
    """
    page = shown_page
    page.set_conditions(["相机：Canon"])
    qt_app.processEvents()

    chips = page._bar._chips
    levels = page._bar._group.buttons()[-1]

    assert chips.isVisible() is True, "有条件时那一块没收起来 —— 用户看不到自己加了哪几条"
    assert chips.mapTo(page._bar, QPoint(0, 0)).x() < levels.mapTo(page._bar, QPoint(0, 0)).x(), (
        "胶囊摆到分段选择器右边去了 —— 两者会重叠"
    )


def test_no_conditions_means_no_chips(qt_app: QApplication, shown_page: CalendarPage) -> None:
    """条件撤光后那一块**整个收起来** —— 留一圈空白在那儿像是还有条件。"""
    page = shown_page
    page.set_conditions(["相机：Canon"])
    qt_app.processEvents()
    assert page._bar._chips.isVisible() is True

    page.set_conditions([])
    qt_app.processEvents()

    assert page._bar._chips.isVisible() is False


def test_clearing_the_conditions_takes_the_chips_away(
    qt_app: QApplication, shown_page: CalendarPage
) -> None:
    """撤光条件后工具条上**一枚都不留** —— 用户原话：「UI 上第二个一直存在不消失」。

    **这条曾经红过**，而红的方式很能说明问题：条件确实撤掉了（模型里是空的），工具条上那枚
    胶囊却还挂着。它之所以发生，是因为摆胶囊的那一句写在 :meth:`MainWindow._update_breadcrumb`
    的**末尾**，而那个方法在「无条件」时 ``hide()`` 完就 ``return`` 了 —— 撤光的路径正好
    走的是早退那一支。切到相册视图再切回来就好了，因为切视图会重走一遍没有早退的那条路，
    症状于是变成「要绕一圈才干净」。

    所以这条量的是**条件空了之后工具条上真的什么都没有**：模型为空而控件还在，量模型永远绿。
    """
    page = shown_page
    page.set_conditions(["相机：Canon", "城市：西安"])
    qt_app.processEvents()
    assert page._bar._chips.findChildren(QToolButton), "前提不成立：胶囊没摆出来"

    page.set_conditions([])
    qt_app.processEvents()

    assert page._bar._chips.findChildren(QToolButton) == [], "条件已经撤光，工具条上还留着胶囊"
    assert page._bar._chips.isHidden() is True, "条件已经撤光，那一块没收起来"


def test_every_chip_stays_whole_when_they_do_not_all_fit(
    qt_app: QApplication, shown_page: CalendarPage
) -> None:
    """摆不下时**收掉尾部那几枚**，而不是把每一枚都压成省略号 —— 守护。

    **用户原话：「添加第二个之后就只显示第二个了」**，而那一枚其实是**第一条**被压没了：
    ``QToolButton`` 窄到尺寸提示以下会把文字省略掉，于是「拍摄时…1 ✕」这样一条**不存在的
    条件**摆在那里 —— 用户读到的是错的，而且点它撤掉的是另一条（症状与「删错条件」一模一样）。

    量的是**每枚摆出来的都拿到自己需要的宽度**：压缩是逐枚按比例摊的，摊完谁都不够；收掉尾部
    则每一枚都是完整的。
    """
    page = shown_page
    page.resize(720, 480)
    qt_app.processEvents()
    # 第一条刻意写长：只有长条件才会触发溢出（两条短的在最小窗口下也放得下）。
    page.set_conditions(["拍摄时间：2026-01-01 00:00 之后的那一段", "文件名：山", "城市：西安"])
    qt_app.processEvents()

    chips = page._bar._chips.findChildren(QToolButton)
    assert chips, "前提不成立：一枚都没摆"
    for chip in chips:
        assert chip.width() >= chip.sizeHint().width(), (
            f"{chip.text()!r} 只有 {chip.width()} 点而它要 {chip.sizeHint().width()} 点 ——"
            " 文字被省略掉了，用户读到的是一条不存在的条件"
        )

    # **至少完整摆下一枚**：只有一条条件时它必须可读，挤成省略号等于没显示。
    assert page._bar._overflow < len(page._bar._all_conditions), "整块都收起来了，一条都没摆"

    # 排不下的那些要说清楚还有几条，而不是假装条件只有那么几条。
    # **只钉「+N 与 ``_overflow`` 对得上」而不钉具体是几条** —— 那个数取决于字体度量，
    # 换个平台就变，写死会让这条用例偶尔红，而它红的原因与被守的东西无关。
    overflow_labels = [
        label.text()
        for label in page._bar._chips.findChildren(QLabel)
        if label.text().startswith("+")
    ]
    assert overflow_labels == [f"+{page._bar._overflow}"], (
        f"没显示的那几条没说清楚：标着 {overflow_labels}，实际还有 {page._bar._overflow} 条"
    )


def test_the_chips_grow_back_when_the_window_does(
    qt_app: QApplication, shown_page: CalendarPage
) -> None:
    """窗口拖宽后**多摆几枚** —— 收起来的那几条要跟着回来。

    钉的是「重排这件事发生在 ``resizeEvent`` 里」：只按 ``set_conditions`` 时算一次的话，
    拖宽窗口仍只有那几枚，与眼前的空间对不上（用户得重新加一条条件才看得到变化）。
    """
    page = shown_page
    page.resize(720, 480)
    qt_app.processEvents()
    page.set_conditions(["拍摄时间：2026-01-01 00:00 之后的那一段", "文件名：山", "城市：西安"])
    qt_app.processEvents()
    assert page._bar._overflow > 0, "前提不成立：窄窗口下并没有溢出"

    page.resize(1400, 480)
    qt_app.processEvents()

    assert page._bar._overflow == 0, "窗口已经够宽了，还有条件没摆出来"
    assert len(page._bar._chips.findChildren(QToolButton)) == 3


def test_the_chips_stay_readable_in_both_appearances(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """浅色与深色下胶囊都得读得出来 —— 守护。

    **这一条量的是画出来的像素，不是 ``chip.palette()``**，理由与上面那个标题的用例一样：
    工具条带着样式表，而样式表会让这一支拿到 ``QStyleSheetStyle`` **自己那份**调色板 ——
    胶囊自己的样式表里那几条 ``palette(text)`` / ``palette(mid)`` 取的正是它。出问题时
    ``palette()`` 已经是新那一档、画的却还是旧那一档，量调色板会一路绿。

    为什么这条不是杞人忧天：胶囊**套在工具条里**（工具条那一行占掉的就是面包屑那一行），
    于是它们第一次吃到了工具条那份样式表的级联 —— 而这条级联路径此前没有人验过。
    """
    for name, window, base in (
        ("浅色", LIGHT_WINDOW, LIGHT_BASE),
        ("深色", DARK_WINDOW, DARK_BASE),
    ):
        page = page_in(window=window, base=base)
        page.resize(1000, 700)
        page.show()
        qt_app.processEvents()
        page.set_conditions(["相机：Canon"])
        qt_app.processEvents()

        chip = page._bar._chips.findChildren(QToolButton)[0]
        image = page.grab().toImage()
        left, top = chip.mapTo(page, QPoint(0, 0)).toTuple()
        pixels = [
            image.pixelColor(left + dx, top + dy)
            for dx in range(chip.width())
            for dy in range(chip.height())
        ]
        ink = min(pixels, key=lambda color: _luminance(color))
        background = max(pixels, key=lambda color: _luminance(color))
        ratio = _contrast(ink, background)

        # **4.5:1 是正文可读的下限**：胶囊上写的是条件（「相机：Canon」）而不是图标。
        assert ratio >= 4.5, (
            f"{name}下胶囊读不出来：{ratio:.1f}:1（字 {ink.name()} 压在底 {background.name()} 上）"
        )
        page.deleteLater()


def test_a_click_on_a_chip_reaches_the_page(qt_app: QApplication, shown_page: CalendarPage) -> None:
    """工具条上的胶囊点下去**要真的报到外面去** —— 否则用户看得见却点不动。

    钉的是「序号原样传出去」：传错了下标，用户点第二枚改的却是第一条，而症状是他看到输入框里
    冒出另一条条件。
    """
    page = shown_page
    page.set_conditions(["文件名：海边", "相机：Canon"])
    qt_app.processEvents()
    seen: list[tuple[str, int]] = []
    page.condition_edited.connect(lambda index: seen.append(("edited", index)))
    page.condition_removed.connect(lambda index: seen.append(("removed", index)))

    chips = page._bar._chips.findChildren(QToolButton)
    assert len(chips) == 2
    # 点右端那个 ✕（热区按字体度量分，见 ``ChipButton._hits_mark``）。
    _click(chips[1], at_mark=True)
    # 点文字那一段。
    _click(chips[0], at_mark=False)

    assert seen == [("removed", 1), ("edited", 0)], f"传出去的序号不对：{seen}"


def _click(chip: QToolButton, *, at_mark: bool) -> None:
    """在胶囊上按一下并松开（``at_mark`` 决定落在文字还是右端那个 ✕）。"""
    x = chip.width() - 4 if at_mark else 10
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        QApplication.sendEvent(
            chip,
            QMouseEvent(
                kind,
                QPointF(x, chip.height() / 2),
                QPointF(x, chip.height() / 2),
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )


def test_the_title_keeps_the_default_font_size(
    qt_app: QApplication,
    page_in: Callable[..., CalendarPage],
) -> None:
    """标题是**系统默认那一档字号**，样式表不许碰它。

    那条 ``font-size: 22px`` 的规则写的是 ``QPushButton#title``，而标题是个
    ``QLabel`` —— 从加进来那天起就没匹配上过。也就是说「22px 的标题」**从来没
    存在过**，标题一直就是默认那一档。修字色的时候把它一并对上，看着是顺手，
    实际上是把字号放大了一号（用户原话「字体变大了，调回原本的字号」）。

    量的是「标签的字体与应用默认字体相等」，而不是某个具体数字 —— 默认字号跟平台
    与系统设置走，写死一个数只会在换机器时红。
    """
    page = page_in(window=LIGHT_WINDOW, base=LIGHT_BASE)
    label = page._bar._title

    assert label.font() == qt_app.font(), (
        f"标题是 {label.font().pointSize()}pt，应用默认是 {qt_app.font().pointSize()}pt ——"
        " 样式表不该给标题改字号（那条 22px 是按钮时代的遗留，从没生效过）"
    )
    rule = page._bar.styleSheet().split("QLabel#title")[1].split("}")[0]
    assert "font-size" not in rule, f"标题那条规则里不该出现 font-size，实际是：{rule.strip()}"
