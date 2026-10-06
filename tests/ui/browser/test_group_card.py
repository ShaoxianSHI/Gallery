"""分组卡片的绘制。

组卡片和相册卡片同形（ADR 0009），只是左上角的角标从「相册」两个字换成了张数。
所以这里测的是「真的画得出来，且画的是那一格」—— 用离屏平台把委托画进一张
QPixmap，再看结果，不是靠读代码确认。

**数像素必须数在指定的那一块上。** 卡片底下那一行文件名也是深色的，整张图一起数的话
「角标画出来了」这种断言**永远为真**：把 :meth:`PhotoDelegate._draw_group_badge` 与
:meth:`PhotoDelegate._draw_folder_glyph` 整个打成空实现，量出来的深色像素一像素都不会少，
全是那行字。
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
from gallery.ui.browser.model import GROUP_COUNT_ROLE, PhotoListModel  # noqa: E402

#: 缩略图边长，与网格默认档位一致。
ICON_EDGE = 176

#: 画布底色。没画到的地方就是这个色，所以「非底色」＝「真的画了东西」。
CANVAS = QColor(255, 255, 255)


def _paint(model: PhotoListModel, row: int, edge: int = ICON_EDGE) -> QPixmap:
    """把第 ``row`` 行按当前尺寸画到一张离屏图上，返回它。"""
    size = QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y)
    pixmap = QPixmap(size)
    pixmap.fill(CANVAS)
    painter = QPainter(pixmap)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, size.width(), size.height())
        delegate = PhotoDelegate(icon_edge=edge)
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()
    return pixmap


def _card(painted: QPixmap) -> QRect:
    """卡片本身（``paint`` 里那个 ``adjusted(3, 3, -3, -3)``）。"""
    return QRect(0, 0, painted.width(), painted.height()).adjusted(3, 3, -3, -3)


def _frame(painted: QPixmap) -> QRect:
    """卡片里的**取景框**：封面、角标、文件夹图形都画在这里，文件名那条不在。

    照 :meth:`PhotoDelegate.paint` 的算法算，而不是抄一份数：``PADDING`` 与
    ``LABEL_HEIGHT`` 哪天变了，这里跟着变。
    """
    return _card(painted).adjusted(
        PhotoDelegate.PADDING,
        PhotoDelegate.PADDING,
        -PhotoDelegate.PADDING,
        -(PhotoDelegate.PADDING + PhotoDelegate.LABEL_HEIGHT),
    )


def _badge_block(painted: QPixmap) -> QRect:
    """角标待着的那一小块：取景框左上角往里 4 像素（见 :meth:`_draw_badge`）。"""
    frame = _frame(painted)
    return QRect(frame.left() + 4, frame.top() + 4, 44, 44)


def _count(pixmap: QPixmap, rect: QRect, *, darker_than: int | None = None) -> int:
    """数 ``rect`` 里符合条件的像素：给了 ``darker_than`` 数深色，否则数非底色。"""
    image = pixmap.toImage()
    return sum(
        1
        for y in range(max(0, rect.top()), min(image.height(), rect.bottom()))
        for x in range(max(0, rect.left()), min(image.width(), rect.right()))
        if (
            image.pixelColor(x, y).lightness() < darker_than
            if darker_than is not None
            else image.pixelColor(x, y) != CANVAS
        )
    )


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
    """有封面时画封面，并在取景框左上角盖一个「N 张」的徽标。"""
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

    # **只数角标那一小块。** 这个红封面的亮度正好落在 120（``(200+40)/2``），
    # 不算「深色」，所以数出来的就是徽标自己的墨 —— 把徽标整个关掉是 0。
    assert _count(painted, _badge_block(painted), darker_than=120) > 0, (
        "取景框左上角那一小块没有徽标 —— 角标压根没画，或者没钉在框角上"
    )


def test_group_card_without_cover_still_paints(tmp_path: Path, qt_app: QApplication) -> None:
    del qt_app
    """封面还没到时画文件夹图形，不能留空白。"""
    photo = PhotoRef(path=tmp_path / "封面.jpg", size=10, mtime_ns=1)
    model = _group_model(tmp_path, (photo,))

    painted = _paint(model, 0)

    assert not painted.isNull()
    # 数的是**取景框里非底色的像素**：文件名那一行不在框里，所以这一次量到的
    # 就是图形本身。把 :meth:`PhotoDelegate._draw_folder_glyph` 打成空实现是 0。
    assert _count(painted, _frame(painted)) > 0, "封面没到时取景框是空白的 —— 文件夹图形没画"


def test_badge_text_is_the_photo_count(tmp_path: Path, qt_app: QApplication) -> None:
    del qt_app
    """角标上写的是**张数**，卡片底下那一行才是组标签 —— 两者别搞混。"""
    photos = tuple(
        PhotoRef(path=tmp_path / f"{index}.jpg", size=10, mtime_ns=1) for index in range(3)
    )
    model = PhotoListModel(icon_edge=ICON_EDGE)
    model.reorder([GroupRef(facet="keyword", key="海边", label="海边", photos=photos)])
    index = model.index(0, 0)

    assert model.data(index, GROUP_COUNT_ROLE) == "3 张", "角标写的不是张数"
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "海边", "卡片底下那一行不是组标签"


#: 两种封面形状：一竖一横。取景框角是同一个点，照片角则跟着封面形状跑。
COVER_SHAPES = ((20, 60), (60, 20))

#: 封面颜色取**亮**色：暗色会被一起数进「角标」里，量角标就量不准了。
LIGHT_COVER = QColor(240, 232, 220)

#: 「这个像素算角标」的亮度上限。角标底是 ``QColor(0, 0, 0, 150)`` 压在浅色封面上，
#: 亮度落在 40 上下；浅色封面自己接近 230。
BADGE_LIGHTNESS = 120


def badge_corner(pixmap: QPixmap, frame: QRect) -> tuple[int, int]:
    """角标左上角在图里的 ``(x, y)``。

    只在**取景框顶部那一条**里找：卡片底下还有一行文件名，那串字也是深色的，一起数
    进来就量不出角标在哪一角了。横封面的照片本来就在框的中间偏下，竖封面的照片会顶
    到框外，所以那一条里除了角标没有别的深色东西。
    """
    image = pixmap.toImage()
    bottom = min(image.height(), frame.top() + 40)
    hits = [
        (x, y)
        for y in range(max(0, frame.top() - 2), bottom)
        for x in range(image.width())
        if image.pixelColor(x, y).lightness() < BADGE_LIGHTNESS
    ]
    assert hits, f"取景框左上角那一条（y={frame.top()}~{bottom}）里没有角标 —— 它不在框角上"
    return min(x for x, _ in hits), min(y for _, y in hits)


def _group_card_with_cover(
    tmp_path: Path, shape: tuple[int, int], name: str
) -> tuple[QPixmap, QRect]:
    """画一张有封面的分组卡片，返回 ``(画面, 取景框)``。"""
    photo = PhotoRef(path=tmp_path / name, size=10, mtime_ns=1)
    model = _group_model(tmp_path, (photo,))
    cover = QImage(QSize(*shape), QImage.Format.Format_RGB32)
    cover.fill(LIGHT_COVER)
    model.set_thumbnail(photo.path, cover)
    painted = _paint(model, 0)
    return painted, _frame(painted)


def test_the_badge_keeps_the_same_corner_whatever_the_cover_shape(
    tmp_path: Path, qt_app: QApplication
) -> None:
    """角标钉在**取景框**左上角：封面是横是竖，它都落在同一处。

    照片在取景框里按比例居中，横封面的左边缘与竖封面的左边缘差着几十像素 —— 角标
    跟着照片走的话，一屏卡片的角标就参差不齐了（这是当初钉框角的理由）。所以这条
    同时盯两件事：两种封面量出来的角标**必须重合**，而且就在框角 ``+4`` 那里。

    钉回照片角这条必红：两种形状量出来的横坐标会差开。
    """
    del qt_app  # QPixmap 要有一个 QGuiApplication 才活得下来
    corners = []
    for index, shape in enumerate(COVER_SHAPES):
        painted, frame = _group_card_with_cover(tmp_path, shape, f"封面{index}.jpg")
        corners.append((badge_corner(painted, frame), frame))

    (first, first_frame), (second, second_frame) = corners
    assert first == second, (
        f"封面 {COVER_SHAPES[0]} 的角标在 {first}，{COVER_SHAPES[1]} 的在 {second} —— "
        "它跟着封面的形状跑了（那是钉照片角）"
    )
    assert first == (first_frame.left() + 4, first_frame.top() + 4), (
        f"角标在 {first}，取景框角在 {first_frame.topLeft()} —— 它没钉在框角上"
    )
    assert second_frame.topLeft() == first_frame.topLeft(), "两格的取景框本来就该在同一处"
