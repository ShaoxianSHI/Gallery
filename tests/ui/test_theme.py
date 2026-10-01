"""窗口底色只有一层的回归测试。

窗口里套着三层：标题框（菜单栏）、相册路径那一层、最里头的照片区。它们以前各铺各的
底色（``Window`` / ``Base`` / 写死的 ``#1C1C1E``），于是浅色主题下点开照片会突然变一块
黑，深色主题下则是三四种不同的黑。

这里不检查控件树，而是**量渲染出来的像素**：在同一张窗口截图上逐层取样，颜色必须相同，
且等于主题给出的那一个底色（``ui.theme``）。另有一条守「跟随系统主题」：系统换成深色时，
各层仍然同色，并且一起变过去。
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

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QWidget  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.core.library.session import (  # noqa: E402
    BROWSE_MODE_ALBUMS,
    SessionState,
    save_session,
)
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描与首屏布局稳定的时限（只是上限，条件一成立就返回）。
READY_TIMEOUT_SECONDS = 8.0

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
    save_session(
        SessionState(recent_roots=(root,), browse_mode=BROWSE_MODE_ALBUMS),
        base=session_base,
    )

    window = MainWindow(session_base=session_base)
    window.resize(1200, 800)
    window.show()

    wait_for(
        lambda: window._model.rowCount() >= 2 and window._breadcrumb.isVisible(),
        timeout=READY_TIMEOUT_SECONDS,
    )
    return window


# ---- 取样 ---------------------------------------------------------------


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


def _edge_colors(image: QImage, window: QWidget, widget: QWidget, *, outward: int = 2) -> set[int]:
    """控件四周那一圈（内外各若干像素）出现过的颜色。

    ``outward`` 是往外看几像素。框是画在控件**自己**的矩形上的，``QListView`` 那种
    ``StyledPanel`` 边框就落在视口外面 1 像素，所以默认要往外看；大图区的视口正贴着
    菜单栏，外侧那几行是菜单栏自己的分隔线（实测 ``#e5e5e5``），那边传 ``outward=0``。

    ``QListView`` 默认那圈灰描边实测是 ``#8e8e8e`` / ``#b6b6b6``，在一整块底色上很显眼。
    """
    top_left = widget.mapTo(window, widget.rect().topLeft())
    x0, y0 = top_left.x(), top_left.y()
    x1, y1 = x0 + widget.width(), y0 + widget.height()
    width, height = image.width(), image.height()
    # 截图最外圈 3 像素是窗口自己的圆角与阴影（实测底行是 #c3c5c7 一类桌面色），
    # 不是界面画的框 —— 跳过，否则会把窗口边缘当成边框。
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


# ---- 测试 ---------------------------------------------------------------


def test_innermost_layer_has_no_border(qt_app: QApplication, window: MainWindow) -> None:
    """最里层（照片区 / 网格）四周不能有任何描边或轮廓。

    回归的是那条系统灰边框：``QListView`` 默认带 ``StyledPanel``，会在视口四周画一圈
    ``#b6b6b6`` 之类的描边，整个照片区看上去像浮在一块面板上，而不是铺满窗口。大图区
    本来就是 ``NoFrame``，网格要跟它对齐。
    """
    try:
        expected = theme.current_theme().background.rgb()
        qt_app.processEvents()
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
    """从网格切到大图，底色不能变 —— 以前点开照片会突然黑一块。"""
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
    因为 ``QApplication.setPalette`` 把应用调色板钉死了，平台再写新颜色进来也进不到我们眼里。
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


def test_dark_background_is_the_deeper_content_colour(appearance: Callable[..., None]) -> None:
    """深色下要的是内容区那种更深的黑，不是系统窗口那层灰。

    实测 macOS 深色：``Window`` 是 ``#323232``、``Base`` 是 ``#171717``。取 ``Window``
    会让整块界面浮着一层灰，照片压不住。
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

    一旦回头去写应用调色板，就等于把它钉死：系统换外观时 Qt 不再更新它，
    界面从此跟不上系统（这正是「浅色下只有标题栏变浅」的成因）。
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
    """标题栏那一段要由窗口自己画。

    macOS 的原生标题栏是一层半透明材质，实测深色下 ``#3b3e3f``、浅色下 ``#f2f4f7``，
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
