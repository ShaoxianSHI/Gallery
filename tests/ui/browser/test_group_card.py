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


#: 窄条封面（高瘦）：取景框里左右会留出一大片背景，角标钉在哪一角一看就分得出来。
NARROW_COVER = QSize(20, 60)

#: 封面颜色取**亮**色：暗色会被 :func:`_dark_pixels` 一起数进去，量角标就量不准了。
LIGHT_COVER = QColor(240, 232, 220)

#: 「这个像素算角标」的亮度上限。角标底是 ``QColor(0, 0, 0, 150)`` 压在浅色封面上，
#: 亮度落在 40 上下；浅色封面自己接近 230。
BADGE_LIGHTNESS = 120


def badge_columns(pixmap: QPixmap, *, top: int, bottom: int) -> list[int]:
    """``top``~``bottom`` 那几行里所有「算角标」的像素的横坐标。

    必须限定行范围：卡片底下还有一行文件名，那串字也是深色的，一起数进来就量不出角标
    在哪一角了。
    """
    image = pixmap.toImage()
    return [
        x
        for y in range(max(0, top), min(image.height(), bottom))
        for x in range(image.width())
        if image.pixelColor(x, y).lightness() < BADGE_LIGHTNESS
    ]


def test_the_badge_sits_on_the_photo_not_on_the_frame(tmp_path: Path, qt_app: QApplication) -> None:
    """角标钉在**照片**的左上角，不是取景框（那圈留白）的左上角。

    钉取景框的话，切档那一刻角标就已经站在终态上、只有照片还在长 —— 「格子的位置一下
    就跳到位」那份观感里最显眼的一处就是它（角标只在相册/分组卡片上有）。钉照片上，
    它才跟着照片一起挪、一起长。

    用窄封面来量：照片居中之后左边留着一大片背景，两种钉法的横坐标差着几十像素。
    """
    del qt_app  # QPixmap 要有一个 QGuiApplication 才活得下来
    photo = PhotoRef(path=tmp_path / "封面.jpg", size=10, mtime_ns=1)
    model = _group_model(tmp_path, (photo,))
    cover = QImage(NARROW_COVER, QImage.Format.Format_RGB32)
    cover.fill(LIGHT_COVER)
    model.set_thumbnail(photo.path, cover)

    painted = _paint(model, 0)
    assert painted is not None and not painted.isNull()

    pixmap = model.thumbnail_pixmap(photo.path)
    assert pixmap is not None
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, painted.width(), painted.height())
    delegate = PhotoDelegate(icon_edge=ICON_EDGE)
    image_rect = option.rect.adjusted(3, 3, -3, -3).adjusted(
        PhotoDelegate.PADDING,
        PhotoDelegate.PADDING,
        -PhotoDelegate.PADDING,
        -(PhotoDelegate.PADDING + PhotoDelegate.LABEL_HEIGHT),
    )
    photo_rect = delegate.target_rect(image_rect, pixmap)
    assert photo_rect.width() < image_rect.width() // 2, (
        f"前提没摆出来：封面该是窄的，照片 {photo_rect} 对取景框 {image_rect}"
    )

    # 只看照片顶部那一条：角标就钉在那里，而文件名在卡片底部（也是一片深色）。
    columns = badge_columns(painted, top=photo_rect.top(), bottom=photo_rect.top() + 34)
    assert columns, "没找到角标，测试没搭对"
    left = min(columns)
    assert left >= photo_rect.left(), (
        f"角标从 x={left} 起，而照片从 x={photo_rect.left()} 起 —— 它钉在取景框角上了"
    )
    assert left <= photo_rect.left() + 12, (
        f"角标从 x={left} 起，照片左边在 x={photo_rect.left()} —— 它没钉在照片角上"
    )
