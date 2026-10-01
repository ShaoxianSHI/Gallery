"""切图时不得冒出网格卡片的悬停提示。

Qt 已经排好的悬停定时器**不会**被 ``QToolTip.hideText()`` 取消 —— 它只隐藏已经显示的
窗口。所以切图后定时器照样醒来并弹出提示，用户看到的就是照片上凭空闪出的小白窗，
有时还闪两次。办法是让网格在大图打开时不再提供提示内容：定时器醒来问不到东西，
就不会显示任何东西。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from gallery.core.config import prepare_library  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402


def _settle(qt_app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path, make_photo: object) -> MainWindow:
    root = tmp_path / "库"
    for index in range(6):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)  # type: ignore[operator]
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.resize(1200, 820)
    window.show()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and window._model.rowCount() < 6:
        _settle(qt_app, 0.05)
    yield window  # type: ignore[misc]
    window.close()


def _hover(window: MainWindow, qt_app: QApplication, seconds: float = 0.7) -> bool:
    """把鼠标放到第一张卡片上并等待；返回提示是否出现。"""
    viewport = window._grid.viewport()
    rect = window._grid.visualRect(window._model.index(0, 0))
    QTest.mouseMove(viewport, QPoint(4, 4))
    _settle(qt_app, 0.25)  # 先移开，否则 Qt 不会重启提示定时器
    QTest.mouseMove(viewport, rect.center())
    _settle(qt_app, seconds)
    visible = QToolTip.isVisible()
    if visible:
        QToolTip.hideText()
        _settle(qt_app, 0.2)
    return visible


def test_pending_tooltip_does_not_pop_up_after_switching(
    window: MainWindow, qt_app: QApplication
) -> None:
    """悬停还没到显示时间就切图，切换过程中不得弹出提示。"""
    window.show_grid()
    _settle(qt_app, 0.5)
    flashed = 0
    for _ in range(3):
        window.show_grid()
        _settle(qt_app, 0.4)
        _hover(window, qt_app, 0.7)  # 只等 0.7 秒：定时器还没触发
        window._open_detail(window._model.index(0, 0))
        for _ in range(120):
            qt_app.processEvents()
            time.sleep(0.01)
            if QToolTip.isVisible():
                flashed += 1
                QToolTip.hideText()
                break

    assert flashed == 0, f"切图时又闪出了 {flashed} 次悬停提示"


def test_grid_tooltip_still_works_after_returning(window: MainWindow, qt_app: QApplication) -> None:
    """回到网格后悬停提示必须照常可用 —— 不能为了不闪而把功能关掉。"""
    window._open_detail(window._model.index(0, 0))
    _settle(qt_app, 1.0)
    window.show_grid()
    _settle(qt_app, 0.6)

    if not _hover(window, qt_app, 1.8):
        pytest.skip("离屏环境没能弹出悬停提示")


def test_model_stops_offering_tooltips_while_in_detail(
    window: MainWindow, qt_app: QApplication
) -> None:
    """大图打开时模型不返回提示内容（定时器醒来问不到东西）。"""
    from PySide6.QtCore import Qt

    index = window._model.index(0, 0)

    window.show_grid()
    _settle(qt_app, 0.4)
    assert window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), "网格里应当有提示"

    window._open_detail(window._model.index(0, 0))
    _settle(qt_app, 0.4)
    assert not window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), (
        "大图打开时不该再提供提示内容"
    )

    window.show_grid()
    _settle(qt_app, 0.4)
    assert window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), "回到网格应当恢复"
