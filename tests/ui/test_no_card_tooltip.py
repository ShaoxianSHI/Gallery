"""网格卡片**不再**提供悬停提示。

这个提示曾经有过：名字、格式、大小、时间、所在文件夹。按「可有可无」删掉了 —— 格子底下
的文件名已经说清了是哪一张，其余信息在大图右侧的元数据面板里（⌘I）随时能看。

**别把它当成一次修 bug**：当年「打开照片时照片的位置上闪出白色小窗」的根因是元数据面板
换内容时把旧控件摘成了顶层窗口（见 ``ui.panels.metadata.MetadataPanel._fill``），与悬停
提示无关。删这个提示是产品决定，不是修复的一部分。

这一条是把「删掉了」钉住：将来谁觉得它方便而加回来，这里会红。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.core.library.config import prepare_library  # noqa: E402
from gallery.ui.main_window import MainWindow  # noqa: E402

#: 网格里摆几张，够问「提示还在不在」。
PHOTO_COUNT = 3

#: 等窗口里的照片全部扫出来的时限。
READY_TIMEOUT_SECONDS = 8.0


@pytest.fixture
def window(
    qt_app: QApplication,
    wait_for: Callable[..., bool],
    tmp_path: Path,
    make_photo: object,
) -> Iterator[MainWindow]:
    root = tmp_path / "库"
    for index in range(PHOTO_COUNT):
        make_photo(f"IMG_{index:04d}.jpg", directory=root)  # type: ignore[operator]
    prepare_library(root)
    window = MainWindow(session_base=tmp_path / "session", initial_library=root)
    window.show()
    assert wait_for(lambda: window._model.rowCount() >= PHOTO_COUNT, timeout=READY_TIMEOUT_SECONDS)
    yield window  # type: ignore[misc]
    window.close()


def test_grid_cards_offer_no_tooltip(window: MainWindow) -> None:
    """网格在屏幕上、也切到大图之后，卡片都不给悬停提示内容。"""
    for current in (window._grid_page, window._detail_page):
        window._stack.setCurrentWidget(current)
        for row in range(window._model.rowCount()):
            index = window._model.index(row, 0)
            assert not window._model.data(index, int(Qt.ItemDataRole.ToolTipRole)), (
                f"第 {row} 格又给出了悬停提示内容 —— 那个顶层小窗会回到照片的位置上"
            )
