"""窗口底色只有一层的回归测试：标题框、相册路径那一层、最里头的照片区必须同色。

这里不检查控件树，而是**量渲染出来的像素**：在同一张窗口截图上逐层取样，颜色必须相同，
且等于主题给出的那一个底色。另有几条守「跟随系统主题」与「下发的调色板固定那几个角色」。
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from library_helpers import set_library_ui  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QWidget  # noqa: E402
from waiting import DEFAULT_TIMEOUT_SECONDS, wait_for_idle

from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    prepare_library,
)  # noqa: E402
from gallery.core.library.search import DEFAULT_FIELD, SearchCondition  # noqa: E402
from gallery.core.library.session import (
    SessionState,
    save_session,
)  # noqa: E402
from gallery.ui import rename_edit, theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402
from gallery.ui.panels.breadcrumb import ChipButton  # noqa: E402

#: 取样点离右边多远。相册只有两个，右边整片是空的，取到的就是底色本身。
SAMPLE_FROM_RIGHT = 24

#: macOS 深色下的两个系统色：窗口那层是 ``#323232``，内容那层是 ``#171717``。
DARK_WINDOW = QColor(0x32, 0x32, 0x32)
DARK_BASE = QColor(0x17, 0x17, 0x17)

#: 浅色下同理：窗口 ``#ececec``，内容白。
LIGHT_WINDOW = QColor(0xEC, 0xEC, 0xEC)
LIGHT_BASE = QColor(0xFF, 0xFF, 0xFF)


def _platform_palette(app: QApplication, *, window: QColor, base: QColor) -> QPalette:
    """照 macOS 的样子造一份系统调色板：``Window`` 与 ``Base`` 本来就不一样。"""
    palette = QPalette(app.palette())
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, base)
    dark = base.lightnessF() < 0.5
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Link,
    ):
        palette.setColor(role, QColor(0xEC, 0xEC, 0xEE) if dark else QColor(0x1A, 0x1A, 0x1A))
    # ``Mid`` 一族（Qt 用它画 3D 边框的中间那一档）深色下**一路压暗到 #585858**。补上这一档
    # 不是较真：样式表里 ``palette(mid)`` 读的是**应用**调色板，不设它的话离屏平台那份默认值
    # 会一直留着，「深色下那一枚记号看不见」在测试里压根不会发生。
    for role in (
        QPalette.ColorRole.Light,
        QPalette.ColorRole.Midlight,
        QPalette.ColorRole.Mid,
    ):
        palette.setColor(role, QColor(0x58, 0x58, 0x58) if dark else QColor(0xB8, 0xB8, 0xB8))
    return palette


@pytest.fixture
def appearance(qt_app: QApplication) -> Iterator[Callable[..., None]]:
    """模拟系统换外观 —— 平台就是这么把新调色板写进应用的。跑完还原。"""
    original = QPalette(qt_app.palette())

    def _switch(*, window: QColor, base: QColor) -> None:
        qt_app.setPalette(_platform_palette(qt_app, window=window, base=base))
        qt_app.processEvents()

    yield _switch
    qt_app.setPalette(original)
    qt_app.processEvents()


@pytest.fixture
def window(
    qt_app: QApplication,
    wait_for: Callable[..., bool],
    tmp_path: Path,
    make_photo: Callable[..., Path],
) -> MainWindow:
    """开一个相册模式的窗口：根目录下放两个相册，路径条与网格都可见。"""
    root = tmp_path / "库"
    for album in ("2022-复活节波尔多", "2023-京都"):
        for index in range(2):
            make_photo(f"{index}.jpg", directory=root / album)
    prepare_library(root)
    session_base = tmp_path / "session"
    save_session(SessionState(recent_roots=(root,)), base=session_base)
    set_library_ui(root, browse_mode=BROWSE_MODE_ALBUMS)

    window = MainWindow(session_base=session_base)
    window.resize(1200, 800)
    window.show()

    wait_for(
        lambda: window._model.rowCount() >= 2 and window._breadcrumb.isVisible(),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    return window


def _row_y(window: QWidget, widget: QWidget) -> int:
    """控件在窗口坐标里的中间一行。"""
    return widget.mapTo(window, QPoint(0, widget.height() // 2)).y()


def _strip_color(image: QImage, y: int, *, x_from: int = 0, x_to: int | None = None) -> QColor:
    """取某一横条里铺得最多的颜色。

    取「最多」而不是「某一点」：路径条上那几个按钮如果偷偷画了自己的底色，
    单点取样未必碰得到，占多数的那种颜色才会露馅。
    """
    right = image.width() if x_to is None else x_to
    counts: Counter[int] = Counter()
    for x in range(x_from, min(right, image.width())):
        counts[image.pixelColor(x, y).rgb()] += 1
    return QColor(counts.most_common(1)[0][0])


def _layer_colors(image: QImage, window: MainWindow) -> dict[str, QColor]:
    """截一张窗口图，逐层取色。"""
    x = window.width() - SAMPLE_FROM_RIGHT
    return {
        "标题框": _strip_color(image, _row_y(window, window.menuBar())),
        "路径条": _strip_color(image, _row_y(window, window._breadcrumb)),
        # 只取右半边：左边是相册格子，格子里有照片，颜色会盖过底色。
        "网格": _strip_color(image, _row_y(window, window._grid), x_from=window.width() // 2),
        "照片区": _strip_color(image, _row_y(window, window._detail), x_from=x),
    }


def _open_detail(qt_app: QApplication, window: MainWindow) -> None:
    """切到大图页并放一张图，让最里层真的有内容。"""
    window._stack.setCurrentWidget(window._detail_page)
    window._detail.set_image(QImage(64, 48, QImage.Format.Format_RGB32), refit=True)
    qt_app.processEvents()


def _without_banner(qt_app: QApplication, window: MainWindow) -> None:
    """量底色之前先把横幅收干净。

    横幅是**浮在内容上面**的一层，它出现时照片区顶边那一圈是橙色的 —— 那既不是边框，也不是
    「几层各铺各的底色」。所以先等后台结束（否则进度消息会在截图之前又冒出来），再清横幅。
    """
    wait_for_idle(qt_app, window)
    window._clear_banner()
    qt_app.processEvents()


def _edge_colors(image: QImage, window: QWidget, widget: QWidget, *, outward: int = 2) -> set[int]:
    """控件四周那一圈（内外各若干像素）出现过的颜色。

    ``outward`` 是往外看几像素。框是画在控件**自己**的矩形上的，``QListView`` 那种
    ``StyledPanel`` 边框落在视口外面 1 像素，所以默认要往外看；大图区的视口正贴着菜单栏，
    外侧那几行是菜单栏自己的分隔线，那边传 ``outward=0``。
    """
    top_left = widget.mapTo(window, widget.rect().topLeft())
    x0, y0 = top_left.x(), top_left.y()
    x1, y1 = x0 + widget.width(), y0 + widget.height()
    width, height = image.width(), image.height()
    # 截图最外圈 3 像素是窗口自己的圆角与阴影（底行是 #c3c5c7 一类桌面色），不是界面画的
    # 框 —— 跳过，否则会把窗口边缘当成边框。
    margin = 3
    found: set[int] = set()

    def take(x: int, y: int) -> None:
        if margin <= x < width - margin and margin <= y < height - margin:
            found.add(image.pixelColor(x, y).rgb())

    for offset in range(-outward, 3):
        for x in range(x0, x1):
            take(x, y0 + offset)
            take(x, y1 + offset)
        for y in range(y0, y1):
            take(x0 + offset, y)
            take(x1 + offset, y)
    return found


def test_innermost_layer_has_no_border(qt_app: QApplication, window: MainWindow) -> None:
    """最里层（照片区 / 网格）四周不能有任何描边或轮廓。

    回归的是那条系统灰边框：``QListView`` 默认带 ``StyledPanel``，会在视口四周画一圈
    ``#b6b6b6`` 之类的描边，整个照片区看上去像浮在一块面板上。大图区本来就是 ``NoFrame``，
    网格要跟它对齐。横幅浮在内容上面，压在照片区顶边时那一圈是橙色的 —— 量的是边框，
    不能把那一层浮着的东西算进来。
    """
    try:
        expected = theme.current_theme().background.rgb()
        _without_banner(qt_app, window)
        assert window._grid.frameShape() == QFrame.Shape.NoFrame, (
            "网格又长出了默认的 StyledPanel 边框"
        )
        assert window._detail.frameShape() == QFrame.Shape.NoFrame

        def offenders(found: set[int]) -> list[str]:
            return [QColor(rgb).name() for rgb in sorted(found - {expected})]

        # 网格页：视口四边都得是统一的底色
        edges = _edge_colors(window.grab().toImage(), window, window._grid.viewport())
        assert edges == {expected}, f"网格四周出现了底色以外的颜色：{offenders(edges)}"

        # 大图页：同理
        _open_detail(qt_app, window)
        _without_banner(qt_app, window)
        detail_edges = _edge_colors(
            window.grab().toImage(), window, window._detail.viewport(), outward=0
        )
        assert detail_edges == {expected}, (
            f"大图区四周出现了底色以外的颜色：{offenders(detail_edges)}"
        )
    finally:
        window.close()


def test_the_three_layers_paint_one_background(qt_app: QApplication, window: MainWindow) -> None:
    """标题栏那一段、路径条、照片区必须是同一块底色。"""
    try:
        expected = theme.current_theme().background
        for name, color in _layer_colors(window.grab().toImage(), window).items():
            assert color == expected, (
                f"{name}那一层是 {color.name()}，窗口底色是 {expected.name()} —— "
                "几层各铺各的底色，正是要修掉的那个毛病"
            )
    finally:
        window.close()


def test_detail_view_keeps_the_same_background(qt_app: QApplication, window: MainWindow) -> None:
    """从网格切到大图，底色不能变 —— 否则点开照片会突然黑一块。"""
    try:
        expected = theme.current_theme().background
        _open_detail(qt_app, window)
        assert window._detail.backgroundBrush().color() == expected
        assert (
            _strip_color(
                window.grab().toImage(),
                _row_y(window, window._detail),
                x_from=window.width() - SAMPLE_FROM_RIGHT,
            )
            == expected
        )
    finally:
        window.close()


def test_background_follows_the_system_both_ways(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None]
) -> None:
    """系统在浅色 / 深色之间来回切，各层每次都要跟着走。

    回归的是真实缺陷：切换外观时只有 macOS 自己画的标题栏变了色，其余控件停在原处 ——
    因为 ``QApplication.setPalette`` 把应用调色板固定了，平台再写新颜色进来也进不到我们眼里。
    """
    try:
        for tag, window_colour, base_colour in (
            ("浅色", LIGHT_WINDOW, LIGHT_BASE),
            ("深色", DARK_WINDOW, DARK_BASE),
            ("再切回浅色", LIGHT_WINDOW, LIGHT_BASE),
        ):
            appearance(window=window_colour, base=base_colour)
            expected = theme.current_theme().background
            assert expected == base_colour, (
                f"{tag}：底色该取内容区色 {base_colour.name()}，却取到了 {expected.name()}"
            )
            for name, color in _layer_colors(window.grab().toImage(), window).items():
                assert color == expected, f"{tag}：{name}那一层是 {color.name()}，没跟上系统"
            _open_detail(qt_app, window)
            assert window._detail.backgroundBrush().color() == expected
            # 大图页会把路径条收起来，下一轮要采样的仍是网格页。
            window.show_grid()
            qt_app.processEvents()
    finally:
        window.close()


def test_calendar_page_follows_the_system_theme(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None]
) -> None:
    """日历那一页也要跟着系统走 —— 它是唯一自己派一份配色的地方。

    回归的是真实缺陷：``CalendarPage`` 起手拿 ``findChild(ThemeWatcher)`` 找监听器，而那个
    监听器挂在 ``QApplication`` 上 —— ``findChild`` 只翻控件自己的子孙，于是返回的永远是
    ``None``，``changed`` 从来接不上。量的是**渲染出来的像素**：日历的配色是一份派生物，
    「对象换过了」不等于「屏幕上换过了」。
    """
    try:
        window.set_browse_mode(BROWSE_MODE_CALENDAR)
        qt_app.processEvents()
        page = window._calendar_page
        for tag, window_colour, base_colour in (
            ("浅色", LIGHT_WINDOW, LIGHT_BASE),
            ("深色", DARK_WINDOW, DARK_BASE),
            ("再切回浅色", LIGHT_WINDOW, LIGHT_BASE),
        ):
            appearance(window=window_colour, base=base_colour)
            expected = theme.current_theme().background
            color = _strip_color(page.grab().toImage(), page.height() // 2)
            assert color == expected, (
                f"{tag}：日历那一页是 {color.name()}，没跟上系统（该是 {expected.name()}）"
            )
    finally:
        window.close()


def test_dark_background_is_the_deeper_content_colour(appearance: Callable[..., None]) -> None:
    """深色下要的是内容区那种更深的黑，不是系统窗口那层灰：实测 macOS 深色 ``Window`` 是
    ``#323232``、``Base`` 是 ``#171717``，取 ``Window`` 会让整块界面浮着一层灰。
    """
    appearance(window=DARK_WINDOW, base=DARK_BASE)
    background = theme.current_theme().background
    assert background == DARK_BASE
    assert background.lightnessF() < DARK_WINDOW.lightnessF(), (
        f"深色底色取成了 {background.name()}，比系统窗口那层 {DARK_WINDOW.name()} 还浅"
    )
    assert background.lightnessF() < 0.2, f"{background.name()} 对深色主题来说不够深"


def test_theme_leaves_the_application_palette_to_the_platform(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None]
) -> None:
    """应用调色板是平台的地盘，我们只在控件上下发。

    一旦回头去写应用调色板，就等于把它固定：系统换外观时 Qt 不再更新它，界面从此跟不上系统。
    """
    try:
        appearance(window=DARK_WINDOW, base=DARK_BASE)
        assert qt_app.palette().color(QPalette.ColorRole.Window) == DARK_WINDOW
        assert qt_app.palette().color(QPalette.ColorRole.Base) == DARK_BASE
        # 而窗口里已经统一成内容区那一个颜色了
        assert window._grid.viewport().palette().color(QPalette.ColorRole.Base) == DARK_BASE
    finally:
        window.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="标题栏是 macOS 的事")
def test_title_bar_is_painted_by_the_window(window: MainWindow) -> None:
    """标题栏那一段要由窗口自己画：macOS 的原生标题栏是一层半透明材质，
    和内容区差一档且随壁纸变；调色板匹配不上它，只能让它别画自己的底。
    """
    flags = window.windowFlags()
    assert flags & Qt.WindowType.ExpandedClientAreaHint, "客户区没有铺到标题栏底下"
    assert flags & Qt.WindowType.NoTitleBarBackgroundHint, "标题栏还会盖一层系统材质"


def test_drag_region_is_everything_above_the_content(window: MainWindow) -> None:
    """拖动区就是内容区之上那一条；内容区里的点击照常给网格与面包屑。"""
    try:
        top = window.centralWidget().geometry().top()
        assert not window._is_title_bar(QPointF(200, top + 5)), "内容区里的点击被当成拖窗口了"
        if top > 0:
            assert window._is_title_bar(QPointF(200, top - 1))
        else:
            # 离屏平台没有标题栏，内容顶到窗口最上沿：这时不存在可拖的那一条
            assert not window._is_title_bar(QPointF(200, top))
    finally:
        window.close()


def test_the_pushed_palette_pins_the_popup_roles(qt_app: QApplication) -> None:
    """下发给控件的那一份必须把 :data:`theme.RESOLVED_ROLES` **固定**。

    不写上的话 Qt 会按控件类型各解析一档 —— 实测 macOS 上同一份调色板，``QMenu`` 的
    ``Highlight`` 是 ``#548fff``、``QTreeWidget`` 是 ``#084ad9``，于是右键菜单与候选列表
    高亮成两种蓝，而两者的样式表是**同一份**生成的（ADR 0035 决策 2）。

    **量的是「钉没固定」而不是「是什么颜色」**：测试跑在 ``offscreen``（``fusion``）上，
    那里每一类控件解析出来的都一样，bug 在测试里根本看不见。
    """
    pinned = theme.pushed_palette(qt_app)
    unresolved = QPalette(qt_app.palette())  # 这几档没写过，交给 Qt 自己解析
    original = QPalette(qt_app.palette())
    sentinel = QColor(1, 2, 3)
    try:
        shifted = QPalette(original)
        for role in theme.RESOLVED_ROLES:
            shifted.setColor(role, sentinel)
        qt_app.setPalette(shifted)
        qt_app.processEvents()

        loose = QWidget()
        loose.setPalette(unresolved)
        assert loose.palette().color(theme.RESOLVED_ROLES[0]) == sentinel, (
            "测试前提不成立：Qt 不再按应用调色板解析这几个角色了，于是「钉没钉死」这一句量不出来"
        )

        probe = QWidget()
        probe.setPalette(pinned)
        for role in theme.RESOLVED_ROLES:
            assert probe.palette().color(role) == pinned.color(role), (
                f"{role.name} 没有钉死：控件会按自己的类型重新解析它，菜单与列表于是给出两个值"
            )
    finally:
        qt_app.setPalette(original)
        qt_app.processEvents()


def test_the_probe_is_not_handed_an_already_answered_palette(
    qt_app: QApplication,
) -> None:
    """问 Qt 要那几档时给它的那份，**那几档一个都不许带着**。

    应用调色板里那几档**有时带着「已解析」标记** —— macOS 换过一次外观之后写进来的那份
    就带着（刚启动时那份没有，所以第一轮看起来一切正常）。带着标记时 ``setPalette``
    变成「原样继承」，探针问出来的等于应用调色板自己那一档：浅色下就成了那个发白的
    ``#a5cdff``，**切一趟深浅色就再也回不来**（ADR 0035 决策 2）。

    **量的是「有没有带着答案」而不是颜色**：``fusion`` 上只有一种解析结果，颜色断言在这里
    量不出差别 —— 解析标记是能量的（``resolveMask``）。
    """
    mask = theme._probe_palette(theme.current_theme(qt_app).background).resolveMask()
    for role in theme.RESOLVED_ROLES:
        assert not (mask >> role.value) & 1, (
            f"{role.name} 被写进了问 Qt 要答案的那份调色板 —— Qt 会原样继承，"
            "问出来的就是应用调色板自己那一档，而不是「按控件类型解析」的那一档"
        )


def test_a_theme_change_is_applied_after_the_events_settle(
    qt_app: QApplication,
) -> None:
    """换外观的那一下**不就地下发**：信号触发的那一刻应用调色板是半路的。

    实测 macOS 一轮换外观里那个回调会被叫好几次：头一次拿到的还是**旧的**调色板，后几次
    拿到的是新的 —— 而那一份里那几档**带着解析标记**。就地下发等于把半路的调色板钉进整棵
    控件树，而那几档一旦钉错就再也回不来。所以真正那一次下发排到**这一批事件走完之后**
    （0ms 定时器），一轮里发几个信号都只发一次。
    """
    theme.ensure_installed(qt_app)
    widget = QWidget()
    original = QPalette(qt_app.palette())
    try:
        theme.apply_theme(qt_app)
        before = widget.palette().color(QPalette.ColorRole.Base)

        dark = QPalette(original)
        dark.setColor(QPalette.ColorRole.Base, QColor(0x33, 0x33, 0x33))
        qt_app.setPalette(dark)
        assert widget.palette().color(QPalette.ColorRole.Base) == before, (
            "换完外观的那一刻就已经下发过了 —— 那就又是在半路上钉的"
        )

        qt_app.processEvents()
        assert (
            widget.palette().color(QPalette.ColorRole.Base)
            == theme.current_theme(qt_app).background
        ), "转完一轮事件循环之后还没下发，这一轮换外观就丢了"
    finally:
        qt_app.setPalette(original)
        theme.apply_theme(qt_app)


def test_coming_back_to_light_gives_the_first_light_again(
    qt_app: QApplication,
) -> None:
    """浅 → 深 → 浅之后，浮层那几个角色**与第一轮逐项相同**。

    任何一轮里被半路调色板带偏、又被固定的东西，都会在这一条上露出来。
    """
    roles = tuple(theme.RESOLVED_ROLES) + (QPalette.ColorRole.Window,)
    popup = QWidget()
    rounds: list[tuple[QColor, ...]] = []
    original = QPalette(qt_app.palette())
    try:
        for base in (QColor(0xFF, 0xFF, 0xFF), QColor(0x17, 0x17, 0x17), QColor(0xFF, 0xFF, 0xFF)):
            shifted = QPalette(original)
            shifted.setColor(QPalette.ColorRole.Base, base)
            qt_app.setPalette(shifted)
            qt_app.processEvents()
            theme.apply_theme(qt_app)
            rounds.append(tuple(popup.palette().color(role) for role in roles))
        assert rounds[1] != rounds[0], "测试前提不成立：深浅两轮给的是同一个值"
        assert rounds[2] == rounds[0], (
            "切回浅色之后与第一轮不一样：",
            [color.name() for color in rounds[2]],
        )
    finally:
        qt_app.setPalette(original)
        qt_app.processEvents()
        theme.apply_theme(qt_app)


def _surface_fill(window: MainWindow, widget: QWidget) -> QColor:
    """控件内部（去掉边框那一圈）铺得最多的颜色。"""
    image = window.grab().toImage()
    top_left = widget.mapTo(window, QPoint(0, 0))
    counts: Counter[int] = Counter()
    for x in range(top_left.x() + 2, top_left.x() + widget.width() - 2):
        for y in range(top_left.y() + 2, top_left.y() + widget.height() - 2):
            counts[image.pixelColor(x, y).rgb()] += 1
    return QColor(counts.most_common(1)[0][0])


@pytest.mark.parametrize("which", ["search_field", "external_button"])
def test_the_input_surfaces_are_the_window_background(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None], which: str
) -> None:
    """两块输入面（搜索胶囊与「用外部应用打开」那个药丸）**与外面同一个底色**。

    原来它们各派生一档（深色下提亮、浅色下压暗），而浅色下那一档灰恰好就是 macOS 系统
    窗口的颜色，用户第一眼以为那是 Qt 原生的（ADR 0035 决策 1）。现在靠那一圈边框显出边界。
    """
    widget = window._search_field if which == "search_field" else window._external_button
    try:
        for tag, window_colour, base_colour in (
            ("浅色", LIGHT_WINDOW, LIGHT_BASE),
            ("深色", DARK_WINDOW, DARK_BASE),
        ):
            appearance(window=window_colour, base=base_colour)
            qt_app.processEvents()
            measured = _surface_fill(window, widget)
            background = theme.current_theme(qt_app).background
            assert measured == background, (
                f"{tag}：{which} 那块不是窗口底色 {background.name()}，量到 {measured.name()}"
            )
    finally:
        window.close()


def test_the_rename_field_shares_that_background(qt_app: QApplication) -> None:
    """改名输入框与那两块是**同一个**函数派生的（各派生一次早晚会差一档）。"""
    current = theme.current_theme(qt_app)
    assert rename_edit.field_background(current) == current.background


def _chip_ink(window: MainWindow) -> tuple[float, float]:
    """条件胶囊那一块里**最暗与最亮**的两个像素（字色就落在其中一端）。

    在**窗口**截图上量，不在控件上量：胶囊那一格是 ``background: transparent``，
    单独 grab 一个控件只会得到一块没画过底的图（实测全黑），量出来的「对比」是假的。
    """
    chips = window._breadcrumb.findChildren(ChipButton)
    assert chips, "条件胶囊没摆出来 —— 这条量的是它的字色"
    chip = chips[-1]
    image = window.grab().toImage()
    top_left = chip.mapTo(window, QPoint(0, 0))
    shades = [
        image.pixelColor(x, y).lightnessF()
        for x in range(top_left.x() + 3, top_left.x() + chip.width() - 3)
        for y in range(top_left.y() + 3, top_left.y() + chip.height() - 3)
    ]
    return min(shades), max(shades)


def test_condition_chip_ink_follows_the_theme_at_once(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None]
) -> None:
    """切浅色 / 深色之后，条件胶囊的字色**当场**就跟着走，不要等点一下。

    原因在 ``ui.theme`` 模块文档第 3 条：样式表里的 ``palette(...)`` 只在样式表装上去的
    那一刻解析一次，改调色板不会让它重来。点一下管用是因为那一下**重建了胶囊**，于是症状
    长成「要点一下」。**量的是渲染出来的像素**：胶囊的调色板一直是新的，只有画出来的那
    一层停在原处。
    """
    try:
        window._conditions = [
            SearchCondition(field=DEFAULT_FIELD, text="海边", comparator="contains", value="海边")
        ]
        window._update_breadcrumb()
        qt_app.processEvents()
        for tag, window_colour, base_colour in (
            ("浅色", LIGHT_WINDOW, LIGHT_BASE),
            ("深色", DARK_WINDOW, DARK_BASE),
            ("再切回浅色", LIGHT_WINDOW, LIGHT_BASE),
        ):
            appearance(window=window_colour, base=base_colour)
            dark_end, light_end = _chip_ink(window)
            if base_colour == LIGHT_BASE:
                readable = dark_end < 0.4
                what = f"{tag}：胶囊上最暗的那一端是 {dark_end:.2f}"
            else:
                readable = light_end > 0.6
                what = f"{tag}：胶囊上最亮的那一端是 {light_end:.2f}"
            assert readable, f"{what} —— 字压在底上，看不见。要「立刻」变，不要靠点一下重建。"
            # 胶囊是**换过主题之前**就在那儿的那一枚，不许在两轮之间重建过
            assert window._breadcrumb.findChildren(ChipButton)
    finally:
        window.close()


def _contrast(a: QColor, b: QColor) -> float:
    """WCAG 的对比度（相对亮度之比，落在 1:1 ~ 21:1）。"""

    def luminance(color: QColor) -> float:
        channels = []
        for value in (color.redF(), color.greenF(), color.blueF()):
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_separator_is_visible_on_both_appearances(
    qt_app: QApplication, window: MainWindow, appearance: Callable[..., None]
) -> None:
    """面包屑里那个 ``▸`` 在浅色与深色下都要看得见。

    原因：**拿 ``palette(mid)`` 当字色**。深色下那一档是 ``#585858``，压在 ``#171717``
    的底上只有 2.52:1 —— 而 ``▸`` 是 11 点、笔画只有两三个像素宽的字，抗锯齿一展开就
    彻底没了。**量的是渲染出来的像素，不是控件的调色板**：颜色是带透明度写进样式表的，
    Qt 不会把带透明度的 ``color`` 落到控件调色板的 ``Text`` 那一栏上，读那一栏永远读到
    的是正文色。判的是 **WCAG 对非文字内容那条 3:1**，量的是**画得最实的那个像素**。
    """
    try:
        window._conditions = [
            SearchCondition(field=DEFAULT_FIELD, text="海边", comparator="contains", value="海边")
        ]
        window._update_breadcrumb()
        qt_app.processEvents()
        for tag, window_colour, base_colour in (
            ("浅色", LIGHT_WINDOW, LIGHT_BASE),
            ("深色", DARK_WINDOW, DARK_BASE),
        ):
            appearance(window=window_colour, base=base_colour)
            separators = [
                widget
                for widget in window._breadcrumb.findChildren(QLabel, "separator")
                if widget.isVisible() and widget.width() < 100
            ]
            assert separators, "分隔符没摆出来（或者摆出来还没落位）—— 这条量的是它的字色"
            image = window.grab().toImage()
            separator = separators[0]
            top_left = separator.mapTo(window, QPoint(0, 0))
            background = image.pixelColor(top_left.x() + 1, top_left.y() + 1)
            inked = [
                image.pixelColor(x, y)
                for x in range(top_left.x(), top_left.x() + separator.width())
                for y in range(top_left.y(), top_left.y() + separator.height())
                if image.pixelColor(x, y) != background
            ]
            assert inked, f"{tag}：分隔符一个像素都没画出来"
            strongest = max(inked, key=lambda color: _contrast(color, background))
            measured = _contrast(strongest, background)
            assert measured >= 3.0, (
                f"{tag}：分隔符画得最实的一笔是 {strongest.name()}，底色 "
                f"{background.name()}，对比只有 {measured:.2f}:1 —— 低于非文字内容那条 3:1"
            )
    finally:
        window.close()
