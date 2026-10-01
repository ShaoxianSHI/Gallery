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
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gallery.core.config import prepare_library  # noqa: E402
from gallery.core.session import BROWSE_MODE_ALBUMS, SessionState, save_session  # noqa: E402
from gallery.ui import theme  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 等待扫描与首屏布局稳定的时限。
READY_TIMEOUT_SECONDS = 8.0

#: 取样点离右边多远。相册只有两个，右边整片是空的，取到的就是底色本身。
SAMPLE_FROM_RIGHT = 24


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path, make_photo: Callable[..., Path]) -> MainWindow:
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

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if window._model.rowCount() >= 2 and window._breadcrumb.isVisible():
            break
        time.sleep(0.01)
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


# ---- 测试 ---------------------------------------------------------------


def test_the_three_layers_paint_one_background(qt_app: QApplication, window: MainWindow) -> None:
    """标题框、路径条、照片区必须是同一块底色。"""
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


def test_every_layer_follows_the_system_theme(qt_app: QApplication, window: MainWindow) -> None:
    """系统换成深色时，各层一起换，且仍然同色。"""
    original = QPalette(qt_app.palette())
    dark = QColor(28, 28, 30)
    try:
        palette = QPalette(original)
        for role in theme.BACKGROUND_ROLES:
            palette.setColor(role, dark)
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.Link,
        ):
            palette.setColor(role, QColor(236, 236, 238))
        qt_app.setPalette(palette)
        qt_app.processEvents()

        assert theme.current_theme().background == dark
        for name, color in _layer_colors(window.grab().toImage(), window).items():
            assert color == dark, f"{name}那一层还是 {color.name()}，没跟上系统主题"
        _open_detail(qt_app, window)
        assert window._detail.backgroundBrush().color() == dark
    finally:
        qt_app.setPalette(original)
        window.close()
