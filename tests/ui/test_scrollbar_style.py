"""滚动条：**没有轨道，只剩滑块**。

三处（网格最右缘、日历周视图的时间轴、大图页的元数据面板）分属三个包，用户在它们
之间来回走，所以「长什么样」只从 :mod:`gallery.ui.scrollbar_style` 一处解析 ——
与 :mod:`gallery.ui.menu_style` 当初要解决的是同一件事。

**这里量的是像素，不是样式表字符串**：样式表写着 ``::add-page { background: transparent }``
只说明它写对了，不代表屏幕上真的没画。实测过两轮：原生滚动条那一份在离屏下轨道是
``#e6e6e6`` 的实心竖条（正是用户说的「常驻的一条灰底」）；而只写 ``::handle`` 而不中和
``::add-page`` 的话，轨道原样留着 —— 所以下面每条断言都对着像素。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QScrollArea,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)
from waiting import DEFAULT_TIMEOUT_SECONDS  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.ui import scrollbar_style, theme  # noqa: E402
from gallery.ui.browser.grid import PhotoGrid  # noqa: E402
from gallery.ui.calendar.thumbs import ThumbStore  # noqa: E402
from gallery.ui.calendar.time_view import WeekView  # noqa: E402
from gallery.ui.main_window import _GRID_PAGE_MARGIN_RIGHT, MainWindow  # noqa: E402
from gallery.ui.panels.metadata import MetadataPanel  # noqa: E402
from gallery.ui.scrollbar_style import (  # noqa: E402
    HANDLE_ALPHA,
    HANDLE_GAP,
    HANDLE_MIN,
    HANDLE_RADIUS,
    HANDLE_WIDTH,
    SCROLLBAR_WIDTH,
)

#: 每行多高、总共几行（内容共 8000 像素，视口 400）。按比例算出来的滑块只剩 20 像素
#: 上下 —— 正好把 :data:`HANDLE_MIN` 那个下限顶起来。
ROW_HEIGHT = 20
ROW_COUNT = 400

VIEWPORT = (300, 400)


def _blended(top: QColor, bottom: QColor) -> QColor:
    """把半透明的前景色压到底色上 —— 样式表里那个 ``rgba(...)`` 落到屏幕上就是这么合成的。"""
    alpha = top.alphaF()
    return QColor(
        round(top.red() * alpha + bottom.red() * (1 - alpha)),
        round(top.green() * alpha + bottom.green() * (1 - alpha)),
        round(top.blue() * alpha + bottom.blue() * (1 - alpha)),
    )


def _within(actual: QColor, expected: QColor, tolerance: int = 1) -> bool:
    """两个颜色逐通道差不超过 ``tolerance``。

    **留这一格余量是因为 Qt 的合成取整与 Python 的 ``round`` 不同**：同一个
    ``rgba(0, 0, 0, 0.32)`` 压在白底上，Qt 落成 174、``round(0.68 * 255)`` 落成 173。
    钉死到 0 就是在测 Qt 的取整实现，而不是测「它是不是正文色压出来的」。
    """
    return all(
        abs(getattr(actual, channel)() - getattr(expected, channel)()) <= tolerance
        for channel in ("red", "green", "blue")
    )


def _scroll_area(*, styled: bool, sheet: str | None = None) -> tuple[QScrollArea, QScrollBar]:
    """造一条够长的滚动区（可选地给它上样式），并把当前底色推到它身上。

    ``sheet`` 用来下发一份**手工改过的**样式表（给「方角对照组」用）；给了它就不再走
    :func:`scrollbar_style.apply_scrollbar_style`。
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    body = QWidget()
    layout = QVBoxLayout(body)
    for _ in range(ROW_COUNT):
        row = QWidget()
        row.setFixedHeight(ROW_HEIGHT)
        layout.addWidget(row)
    area.setWidget(body)
    area.resize(*VIEWPORT)
    if sheet is not None:
        area.verticalScrollBar().setStyleSheet(sheet)
    elif styled:
        scrollbar_style.apply_scrollbar_style(area.verticalScrollBar())
    area.show()
    theme.push_palette(area)
    return area, area.verticalScrollBar()


def _handle_box(image: QImage, background: QColor) -> tuple[int, int, int, int]:
    """滑块的外接矩形 ``(left, top, right, bottom)``；没画出来时 ``left`` 是 ``-1``。"""
    top, bottom = _handle_span(image, background)
    if top < 0:
        return -1, -1, -1, -1
    middle = (top + bottom) // 2
    columns = [x for x in range(image.width()) if image.pixelColor(x, middle) != background]
    return columns[0], top, columns[-1], bottom


def _background_pixels_inside(image: QImage, background: QColor) -> int:
    """滑块外接矩形里有多少像素是**底色**。

    方角时是 0（那个矩形就是滑块自己），圆角时四角一定露着底色。
    """
    left, top, right, bottom = _handle_box(image, background)
    assert left >= 0, "滑块没画出来"
    return sum(
        1
        for y in range(top, bottom + 1)
        for x in range(left, right + 1)
        if image.pixelColor(x, y) == background
    )


def _handle_span(image: QImage, background: QColor) -> tuple[int, int]:
    """量出滑块在**中间那一列**上的上下两端；没画出来时是 ``(-1, -1)``。

    取中间那一列而不是四角：圆角只作用在左右两侧，中间那列的上下是干净的横边，
    量出来的两端就是滑块真正的边界（角上那些过渡色会把它算长一截）。
    """
    column = image.width() // 2
    rows = [y for y in range(image.height()) if image.pixelColor(column, y) != background]
    return (rows[0], rows[-1]) if rows else (-1, -1)


def _handle_colour(sheet: str) -> str:
    """从样式表里取出 ``::handle:vertical`` 那一档颜色（``rgba(...)`` 那个写法）。"""
    lines = [line.strip() for line in sheet.splitlines()]
    for index, line in enumerate(lines):
        if line.startswith("QScrollBar::handle:vertical"):
            return lines[index + 1].removeprefix("background:").rstrip(";").strip()
    raise AssertionError("样式表里没有 ::handle:vertical 那一档")


def test_the_track_is_gone_and_only_the_handle_is_left(qt_app: QApplication) -> None:
    """滑块以外的地方必须与底色一模一样 —— 那条常驻的实心轨道就是用户看见的那一层。

    **同一段代码里顺带量一次原生的**：它证明这条断言不是空转的。若哪天原生那一份也
    恰好透明了，这个对照组会先变红，而不是让守护悄悄失去意义。
    """
    theme.ensure_installed(qt_app)
    background = theme.current_theme(qt_app).background

    area, bar = _scroll_area(styled=False)
    native = bar.grab().toImage()
    native_colors = {
        native.pixelColor(native.width() // 2, y).name() for y in range(native.height())
    }
    assert background.name() not in native_colors, (
        "前置条件失效：原生滚动条在离屏下也画成了透明 —— 这条用例量不到它要治的那个毛病"
    )
    area.close()

    area, bar = _scroll_area(styled=True)
    image = bar.grab().toImage()
    top, bottom = _handle_span(image, background)
    assert top >= 0, "整条滚动条上一个非底色像素都没有，滑块根本没画出来"
    # **先钉住「确实量得到轨道那一段」**：滑块一铺满整条，下面那个 ``beyond`` 就是空列表，
    # 而 ``all([])`` 恒为真 —— 守护于是变成空转，轨道回来了它照样绿（实测过）。
    beyond = [y for y in range(image.height()) if not top <= y <= bottom]
    assert beyond, "滑块铺满了整条，量不到轨道那一段"

    column = image.width() // 2
    offenders = [y for y in beyond if image.pixelColor(column, y) != background]
    assert not offenders, f"滑块跨度以外第 {offenders} 行的像素不是底色 —— 轨道没去掉"

    # 滑块本身是「正文色按那一档透明度压在底色上」，不是随手挑的灰。
    expected = _blended(theme.current_theme(qt_app).text_at(HANDLE_ALPHA), background)
    actual = image.pixelColor(column, (top + bottom) // 2)
    assert _within(actual, expected), (
        f"滑块颜色 {actual.name()} 不是正文色 {HANDLE_ALPHA} 压在底色上该有的 {expected.name()}"
    )
    area.close()


def test_the_handle_is_never_shorter_than_the_minimum(qt_app: QApplication) -> None:
    """内容远长于视口时，滑块不许被压成一条看不见的线。

    那正是用户最需要知道「还剩很多没看完」的时候。
    """
    theme.ensure_installed(qt_app)
    background = theme.current_theme(qt_app).background
    area, bar = _scroll_area(styled=True)
    top, bottom = _handle_span(bar.grab().toImage(), background)
    assert bottom - top + 1 >= HANDLE_MIN, "滑块短到认不出来了"
    area.close()


def test_the_handle_colour_is_derived_from_the_theme(qt_app: QApplication) -> None:
    """滑块是「正文色压到底色上」压出来的那一档 —— 浅底上更深、深底上更亮。

    **判据是方向而不是绝对明度**：浅底上那一条深灰（``#aeaeae``）明度就有 0.68，
    按「明度低于一半」去判会把它误判成没压住。真正要盯的是它有没有往正文色那边走、
    以及与底色分不分得开。
    """
    theme.ensure_installed(qt_app)
    current = theme.current_theme(qt_app)
    for background, expect_lighter in ((QColor("#ffffff"), False), (QColor("#171717"), True)):
        text = QColor("#ffffff" if expect_lighter else "#000000")
        app_theme = theme.AppTheme(
            background=background,
            text=text,
            link=current.link,
            notice=current.notice,
            error=current.error,
        )
        handle = _blended(app_theme.text_at(HANDLE_ALPHA), background)
        assert handle != background, f"底色 {background.name()} 上滑块与底色分不开"
        assert (handle.lightnessF() > background.lightnessF()) is expect_lighter, (
            f"底色 {background.name()} 上的滑块没有往正文色那边走"
        )


def test_a_theme_change_re_pushes_the_sheet(qt_app: QApplication) -> None:
    """换外观之后滑块要真的换色。

    颜色是**拼进样式表的字面量**，所以只重设调色板对它一点影响都没有 —— 必须重新下发
    一次样式表才作数。这条盯的就是那个「重新下发」。
    """
    watcher = theme.ensure_installed(qt_app)
    _, bar = _scroll_area(styled=True)
    before = bar.styleSheet()
    original = qt_app.palette()

    dark = theme.AppTheme(
        background=QColor("#171717"),
        text=QColor("#ffffff"),
        link=QColor("#548fff"),
        notice=QColor("#ffcf70"),
        error=QColor("#ff9b8a"),
    )
    try:
        palette = qt_app.palette()
        palette.setColor(theme.BACKGROUND_ROLE, dark.background)
        # **正文色也要一起换**：:func:`theme.current_theme` 的 ``text`` 取自 ``WindowText``，
        # 只换底色的话滑块压出来的还是那一档黑 —— 那是测试把前置条件设残了，
        # 真机换外观时这一档本来就跟着变。
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
            palette.setColor(role, dark.text)
        qt_app.setPalette(palette)
        watcher._apply_now()  # 真机上换外观那一轮走的就是这条
        qt_app.processEvents()

        after = bar.styleSheet()
        assert after != before, "换了外观，滑块的颜色没跟着换"
        assert _handle_colour(after) == theme.rgba(dark.text_at(HANDLE_ALPHA))
    finally:
        qt_app.setPalette(original)  # 别把这一轮的深色留给同模块后面的用例
        theme.apply_theme(qt_app)
    bar.close()


def test_the_three_scrollbars_get_one_sheet(qt_app: QApplication) -> None:
    """三处共用同一份样式表 —— 用户在它们之间来回走，各画各的早晚差一档。"""
    theme.ensure_installed(qt_app)
    grid, week, panel = PhotoGrid(), WeekView(ThumbStore()), MetadataPanel()
    sheets = {
        grid.verticalScrollBar().styleSheet(),
        week._area.verticalScrollBar().styleSheet(),
        panel._scroll.verticalScrollBar().styleSheet(),
    }
    assert len(sheets) == 1, "三处滚动条不是同一份样式表 —— 统一这件事没做到"
    assert sheets.pop() == scrollbar_style.scrollbar_style(theme.current_theme(qt_app))


def test_the_metadata_panel_no_longer_cascades_a_bare_declaration(qt_app: QApplication) -> None:
    """元数据面板那条**不许**再用不带选择器的裸声明。

    ``setStyleSheet("background: transparent; border: none;")`` 会级联到所有子控件，
    那条竖滚动条也在内 —— 挂了样式表之后它不再走原生样式，轨道成了一条常驻的实心竖条。
    现在改成点名视口：滚动区照样透明，滚动条那份样式表是干净的。
    """
    theme.ensure_installed(qt_app)
    panel = MetadataPanel()
    assert panel._scroll.styleSheet() == "", "滚动区上又有裸声明了，它会级联到滚动条上"
    viewport = panel._scroll.viewport()
    assert viewport.palette().color(theme.BACKGROUND_ROLE).alpha() == 0, (
        "视口不透明了，会盖住面板自己画的那层底色"
    )


def test_the_bar_is_narrower_than_the_native_one(qt_app: QApplication) -> None:
    """滚动条比原生那档细，而网格把省下的像素算进排版里。

    :meth:`PhotoGrid._scrollbar_reserve` 问的正是 ``sizeHint().width()``，所以这条
    样式会**真的**把每一格挪宽几像素 —— 那是该得的（少掉的原本是照片的），但也意味着
    格宽一变列数就可能变，而列数一变就该走切档过渡（见 ``grid_helpers`` 里那个
    「找一个不换列的增量」）。钉住「预留的宽度确实变窄了」，免得哪天它悄悄回到 18
    而省下的那几像素白扔了。
    """
    theme.ensure_installed(qt_app)
    plain = QScrollBar()
    grid = PhotoGrid()
    assert grid.verticalScrollBar().sizeHint().width() < plain.sizeHint().width()
    assert grid._scrollbar_reserve() == grid.verticalScrollBar().sizeHint().width()


def test_the_handle_is_a_pill_set_back_from_the_right_edge(qt_app: QApplication) -> None:
    """胶囊形，且右缘留 :data:`HANDLE_GAP` 像素的缝 —— 贴着边看着像内容被切掉一截。

    量的是**滑块横向占住哪几列**：左边起于第 0 列（靠着内容那一侧不留边），右边停在
    倒数第 ``HANDLE_GAP`` 列，最后那两列必须是底色。贴死的那一版用户原话：
    「你的截图上没有边缘，很奇怪」。
    """
    theme.ensure_installed(qt_app)
    background = theme.current_theme(qt_app).background
    area, bar = _scroll_area(styled=True)
    image = bar.grab().toImage()
    left, _top, right, _bottom = _handle_box(image, background)
    assert left >= 0, "滑块没画出来"

    assert left == 0, f"滑块左边没起于第 0 列（第 {left} 列起）"
    assert right == HANDLE_WIDTH - 1, (
        f"滑块横向占了第 {left}..{right} 列（宽 {right - left + 1}），"
        f"要的是 {HANDLE_WIDTH} 列 —— 右边那 {HANDLE_GAP} 列应该是底色"
    )
    assert bar.width() == SCROLLBAR_WIDTH == HANDLE_WIDTH + HANDLE_GAP
    area.close()


def test_the_handle_corners_are_rounded_not_square(qt_app: QApplication) -> None:
    """滑块四角是圆的 —— 半径一旦被 Qt 清零，滑块就是一个方头。

    **这条守的是 ``983e00d`` 那个回退过的事故**：那一版是 9 点宽 + ``HANDLE_WIDTH / 2``
    = 4.5，真机上圆角整个消失，而当时所有证据都来自离屏渲染。机制在
    :mod:`gallery.ui.scrollbar_style` 的模块文档里（``qRound`` 一次 + ``qNormalizeRadii``
    判 ``2 × 圆角 > 宽度`` 就清零）；现在这一档是 10 点宽 + 5 点圆角，正好压线而保住。

    **量的是「有 / 没有」，不是「多圆」**：离屏渲染的圆弧比真机淡得多（实测这一档
    真机上顶端内缩 9 设备像素，离屏下只有 2），量半径大小等于在量渲染器。所以
    **同一个用例里再造一条半径 0 的做对照组**：有圆角的那条必须在外接矩形里露出底色，
    方的那条一个也不许露 —— 「露了」与「一个都没露」才是这条要断的事。

    **它专治「半径越过一半被清零」**（半径改成 6 时它会红）：单纯把 ``//`` 写成 ``/``
    在偶数额度上只是 5.0 vs 5，渲染一样，那种违规由上一条用例按类型拦下。
    """
    theme.ensure_installed(qt_app)
    background = theme.current_theme(qt_app).background

    rounded_area, rounded_bar = _scroll_area(styled=True)
    rounded = _background_pixels_inside(rounded_bar.grab().toImage(), background)
    rounded_area.close()

    square_sheet = scrollbar_style.scrollbar_style(theme.current_theme(qt_app)).replace(
        f"border-radius: {HANDLE_RADIUS}px;", "border-radius: 0px;"
    )
    square_area, square_bar = _scroll_area(styled=False, sheet=square_sheet)
    square = _background_pixels_inside(square_bar.grab().toImage(), background)
    square_area.close()

    assert square == 0, f"对照组（半径 0）都露出 {square} 个底色像素 —— 这条用例量不到它要治的毛病"
    assert rounded > square, (
        f"有圆角的那条在外接矩形里一个底色像素都没露（对照组 {square} 个）—— 圆角被 Qt 清掉了"
    )


def test_the_corner_radius_is_exactly_half_the_handle_width(qt_app: QApplication) -> None:
    """圆角**正好**是滑块宽度的一半（10 → 5），而宽度因此必须是偶数。

    Qt 先把 ``border-radius`` 四舍五入成整数（``qcssparser.cpp`` 的 ``qRound``），
    再在 ``qNormalizeRadii``（``qcssutil.cpp``）里判 ``2 × 圆角 > 宽度`` 就把半径清零 ——
    真机上不是「圆角变小」而是**方头**。9 点宽那一档正好踩上：``4.5`` → ``qRound`` 成 5
    → ``10 > 9`` → 圆角整个消失（``983e00d``，真机截屏里一点圆角都没有）。

    **让步的是宽度，不是圆角**：一半要落在整数上，所以 :data:`HANDLE_WIDTH` 取偶数；
    而 ``2 × 5 = 10`` 正好压在那条判据上 —— 它是 ``>`` 不是 ``≥``，所以保住。
    这条用例同时钉住三件事：一半、整数、偶数宽度。
    """
    theme.ensure_installed(qt_app)
    assert HANDLE_WIDTH % 2 == 0, "滑块宽度必须是偶数，圆角才可能正好是一半且落在整数上"
    assert isinstance(HANDLE_RADIUS, int), "圆角必须是整数：小数会被 Qt 四舍五入到线那边去"
    assert HANDLE_RADIUS == HANDLE_WIDTH // 2 == HANDLE_WIDTH / 2, "圆角不是严格的一半"
    assert 2 * HANDLE_RADIUS <= HANDLE_WIDTH, "2 × 圆角超过了滑块宽度，Qt 会把圆角整个丢掉"
    sheet = scrollbar_style.scrollbar_style(theme.current_theme(qt_app))
    assert f"border-radius: {HANDLE_RADIUS}px;" in sheet
    # 只有尾端留缝（竖着在右、横着在下），缝宽就是 HANDLE_GAP。
    assert f"margin: 0px {HANDLE_GAP}px 0px 0px;" in sheet


def test_the_grid_scrollbar_keeps_only_a_slit_to_the_window_edge(
    qt_app: QApplication,
    tmp_path: Path,
    make_photo: Callable[..., Path],
    wait_for: Callable[..., bool],
) -> None:
    """网格页那条滚动条的右缘离**窗口**右缘只有 :data:`_GRID_PAGE_MARGIN_RIGHT` 点。

    **这条守的正是用户看见的那块「右侧空白」**：滑块的缝只有 2 点，可滚动区外面还包着
    一圈页面边距（原来是 10 点，比滚动条本身还宽）—— 于是屏幕上那一段空白是这个之和，
    看着像内容被切掉一截。用户原话：「右边的 10 点改为 2」。

    **量的是窗口而不是滚动区**：上一轮只量到「滚动条右缘 = 滚动区右缘」，那一层是对的，
    漏掉的恰恰是滚动区到窗口之间这一段。两个右缘相减，就是要守的那个数。

    **「2」在这条用例里是写死的**：只跟 :data:`_GRID_PAGE_MARGIN_RIGHT` 比是空转 ——
    常量一改，布局与断言一起变，永远绿（实测过：把右边距改回 10 它照样过）。
    """
    # 用户点名要的那一档就是 2 点（原话：「右边的 10 点改为 2」），在这儿独立写一遍才是守护。
    assert _GRID_PAGE_MARGIN_RIGHT == 2, (
        f"网格页右边距改成了 {_GRID_PAGE_MARGIN_RIGHT} 点，用户要的是 2 点"
    )

    root = tmp_path / "库"
    for index in range(40):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)
    prepare_library(root)

    window = MainWindow(session_base=tmp_path / "会话", initial_library=root)
    window.resize(1000, 700)
    window.show()
    try:
        bar = window._grid.verticalScrollBar()
        # 等两件事一起成立：照片扫出来了，**并且**布局已经跑过 —— 滚动条不再是那个还没被
        # 摆过的默认 100 点宽，样式表定下的宽度已经生效。
        assert wait_for(
            lambda: window._model.rowCount() >= 40 and bar.width() == SCROLLBAR_WIDTH,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ), "照片没能在时限内扫出来、或者网格的布局还没跑过，这条量的就不是网格页"

        bar_right = bar.mapToGlobal(QPoint(bar.width(), 0)).x()
        window_right = window.mapToGlobal(QPoint(window.width(), 0)).x()
        assert window_right - bar_right == 2, (
            f"滚动条右缘离窗口右缘还有 {window_right - bar_right} 点，要的是 2 点"
        )
    finally:
        window.close()
