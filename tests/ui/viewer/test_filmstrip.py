"""底部画廊条：位置、居中与点击。

几何部分不造窗口也能测；点击那几条走真实控件，确认命中测试与当前那张的判据。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.viewer.filmstrip import (  # noqa: E402
    BOTTOM_MARGIN,
    CELL,
    MAX_WIDTH,
    PADDING,
    Filmstrip,
    cell_rect,
    centered_offset,
    content_width,
    strip_height,
    strip_rect,
    visible_indices,
)

AREA = QRect(0, 0, 1000, 800)


# ---- 位置 --------------------------------------------------------------------


def test_the_strip_sits_near_the_bottom_without_touching_it() -> None:
    rect = strip_rect(AREA)

    assert rect.bottom() == AREA.bottom() - BOTTOM_MARGIN
    assert rect.width() <= MAX_WIDTH
    assert rect.center().x() == AREA.center().x()


def test_a_very_wide_window_does_not_stretch_the_strip() -> None:
    rect = strip_rect(QRect(0, 0, 5000, 900))

    assert rect.width() == MAX_WIDTH
    assert rect.center().x() == pytest.approx(2500, abs=1)


def test_a_narrow_window_still_gets_a_usable_strip() -> None:
    rect = strip_rect(QRect(0, 0, 200, 200))

    assert rect.width() > 0
    assert rect.left() >= 0
    assert rect.right() <= 200


# ---- 居中 --------------------------------------------------------------------


def test_the_current_cell_lands_in_the_middle() -> None:
    count = 33
    width = 700
    offset = centered_offset(width, count, 16)
    current = cell_rect(16, offset)

    assert current.center().x() == pytest.approx(width / 2, abs=1)


def test_the_first_cell_sticks_to_the_left() -> None:
    assert centered_offset(700, 33, 0) == 0
    assert cell_rect(0, 0).left() == PADDING


def test_the_last_cell_sticks_to_the_right() -> None:
    count = 33
    width = 700
    offset = centered_offset(width, count, count - 1)
    last = cell_rect(count - 1, offset)

    assert offset == content_width(count) - width
    assert last.right() == width - PADDING - 1


def test_a_row_shorter_than_the_panel_is_centered() -> None:
    count = 3
    width = 700
    offset = centered_offset(width, count, 1)

    assert content_width(count) < width
    assert offset < 0
    assert cell_rect(0, offset).left() == (width - content_width(count)) // 2 + PADDING
    assert cell_rect(1, offset).center().x() == pytest.approx(width / 2, abs=1)


def test_visible_indices_are_cut_by_the_viewport() -> None:
    count = 33
    offset = centered_offset(700, count, 16)
    seen = visible_indices(700, count, offset)

    assert 0 < len(seen) < count
    assert cell_rect(seen[0], offset).right() >= 0
    assert cell_rect(seen[-1], offset).left() < 700
    assert cell_rect(seen[0] - 1, offset).right() < 0


# ---- 控件 --------------------------------------------------------------------


def _pictures() -> QPixmap:
    image = QImage(CELL, CELL, QImage.Format.Format_RGB32)
    image.fill(QColor("#4080c0"))
    return QPixmap.fromImage(image)


def _items(count: int) -> list[tuple[int, Path]]:
    return [(row, Path(f"/照片/{row}.jpg")) for row in range(count)]


def _strip(qt_app: QApplication, rows: int = 6, current: int = 2) -> Filmstrip:
    strip = Filmstrip()
    strip.resize(600, strip_height())
    strip.set_thumbnail_provider(lambda _path: _pictures())
    strip.show()
    # 先摆出来再给条目：``set_items`` 自己决定这一批要不要显示（只有一张时不显示）。
    strip.set_items(_items(rows), current)
    qt_app.processEvents()
    return strip


def test_the_current_row_is_selected(qt_app: QApplication) -> None:
    strip = _strip(qt_app)
    try:
        assert strip.rows() == (0, 1, 2, 3, 4, 5)
        assert strip.current_row == 2
        assert strip.isVisible() is True
    finally:
        strip.close()


def test_a_single_photo_hides_the_strip(qt_app: QApplication) -> None:
    strip = _strip(qt_app, rows=1, current=0)
    try:
        assert strip.isVisible() is False
    finally:
        strip.close()


def test_clicking_a_thumbnail_reports_its_row(qt_app: QApplication) -> None:
    strip = _strip(qt_app)
    seen: list[int] = []
    strip.photo_activated.connect(seen.append)
    offset = centered_offset(strip.width(), len(strip.rows()), 2)
    try:
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=cell_rect(4, offset).center())

        assert seen == [4]
    finally:
        strip.close()


def test_clicking_the_current_thumbnail_says_nothing(qt_app: QApplication) -> None:
    strip = _strip(qt_app)
    seen: list[int] = []
    strip.photo_activated.connect(seen.append)
    offset = centered_offset(strip.width(), len(strip.rows()), 2)
    try:
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=cell_rect(2, offset).center())

        assert seen == []
    finally:
        strip.close()


def test_it_paints_the_panel_and_the_thumbnails(qt_app: QApplication) -> None:
    strip = _strip(qt_app)
    try:
        image = strip.grab().toImage()

        assert image.height() == strip.height()
        # 面板底色是半透明的，抓到的画面不会是一片全透明。
        assert image.pixelColor(strip.width() // 2, 4).alpha() > 0
        # 当前那一格有一圈强调色，与面板底色不同。
        offset = centered_offset(strip.width(), len(strip.rows()), 2)
        current = cell_rect(2, offset)
        assert image.pixelColor(current.center()) != image.pixelColor(strip.width() // 2, 4)
    finally:
        strip.close()
