"""分组卡片的绘制。

组卡片和相册卡片同形（ADR 0009），只是左上角的角标从「相册」两个字换成了张数。
所以这里测的是「真的画得出来，且画的是那一格」—— 用离屏平台把委托画进一张
QPixmap，再看结果，不是靠读代码确认。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem  # noqa: E402

from gallery.core.library.photos import GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.grid import GRID_PADDING_X, GRID_PADDING_Y, PhotoDelegate  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402

#: 缩略图边长，与网格默认档位一致。
ICON_EDGE = 176


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _paint(model: PhotoListModel, row: int, edge: int = ICON_EDGE) -> QPixmap:
    """把第 ``row`` 行按当前尺寸画到一张离屏图上，返回它。"""
    size = QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y)
    pixmap = QPixmap(size)
    pixmap.fill(QColor(255, 255, 255))
    painter = QPainter(pixmap)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, size.width(), size.height())
        delegate = PhotoDelegate(icon_edge=edge)
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()
    return pixmap


def _dark_pixels(pixmap: QPixmap) -> int:
    image = pixmap.toImage()
    dark = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).lightness() < 90:
                dark += 1
    return dark


def _group_model(tmp_path: Path, photos: tuple[PhotoRef, ...]) -> PhotoListModel:
    model = PhotoListModel(icon_edge=ICON_EDGE)
    model.reorder(
        [GroupRef(facet="captured_month", key="2024-05", label="2024 年 5 月", photos=photos)]
    )
    return model


def test_group_card_with_cover_paints_the_cover_and_the_badge(
    tmp_path: Path, qt_app: QApplication
) -> None:
    del qt_app  # QPixmap 要有一个 QGuiApplication 才活得下来
    """有封面时画封面，并在左上角盖一个「N 张」的徽标。"""
    photo = PhotoRef(path=tmp_path / "封面.jpg", size=10, mtime_ns=1)
    model = _group_model(tmp_path, (photo,))
    cover = QImage(64, 64, QImage.Format.Format_RGB32)
    cover.fill(QColor(200, 40, 40))
    model.set_thumbnail(photo.path, cover)

    painted = _paint(model, 0)

    assert not painted.isNull()
    # 封面是红色的一整块：取景框中央应当是它。
    centre = painted.toImage().pixelColor(painted.width() // 2, painted.height() // 2)
    assert centre.red() > centre.blue()
    assert _dark_pixels(painted) > 0  # 徽标那一小块深色


def test_group_card_without_cover_still_paints(tmp_path: Path, qt_app: QApplication) -> None:
    del qt_app
    """封面还没到时画文件夹图形，不能留空白。"""
    photo = PhotoRef(path=tmp_path / "封面.jpg", size=10, mtime_ns=1)
    model = _group_model(tmp_path, (photo,))

    painted = _paint(model, 0)

    assert not painted.isNull()
    assert _dark_pixels(painted) > 0


def test_badge_text_is_the_photo_count(tmp_path: Path, qt_app: QApplication) -> None:
    del qt_app
    photos = tuple(
        PhotoRef(path=tmp_path / f"{index}.jpg", size=10, mtime_ns=1) for index in range(3)
    )
    model = PhotoListModel(icon_edge=ICON_EDGE)
    model.reorder([GroupRef(facet="keyword", key="海边", label="海边", photos=photos)])

    assert model.data(model.index(0, 0), int(Qt.ItemDataRole.DisplayRole)) == "海边"
