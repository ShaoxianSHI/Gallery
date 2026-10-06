"""网格卡片的绘制回归测试。

这里不检查控件树，而是**量渲染出来的像素**：把一格画到全透明的画布上，看它有没有
真的铺底色、缩略图有没有真的裁圆。

- 未选中的照片与相册都不该铺底色。密排时相邻格子的底色会连成一片，照片本身的
  边界反而被衬得看不清（见 b2f05c5 与本次改动）。
- 选中必须仍然铺一层高亮底色，否则「选中了哪一张」的反馈就没了。
- 照片 / 相册 / 分组卡片的缩略图都必须裁圆角（``THUMB_RADIUS``）。

取样点取在「圆角底之内、缩略图与文件名之外」的那一小条：那里除了底色不该有别的
东西，采样不会撞到缩略图、角标或文字。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from gallery.core.library.photos import FolderRef, GroupRef, PhotoRef  # noqa: E402
from gallery.ui.browser.grid import PhotoDelegate  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402

#: 取样用的缩略图边长。小一点就跑得快，格子结构完全一样。
ICON_EDGE = 96

#: 「这个像素算被裁掉了」的 alpha 上限。**不断言恰好 0**：圆角是抗锯齿裁出来的，
#: 半径一小，弧线离角上那个像素只有一像素出头，它就会沾上一点过渡；而直角时那里是
#: 满的 255 —— 取中间这一档，两种形状分得很开，以后调半径也不会被抗锯齿的零头绊倒。
CLEAR_ALPHA = 32


@pytest.fixture
def rows(qt_app: QApplication, tmp_path: Path) -> tuple[PhotoListModel, int, int]:
    """一个装着「一个相册 + 一张照片」的模型，返回 ``(模型, 相册行, 照片行)``。"""
    del qt_app
    model = PhotoListModel(icon_edge=ICON_EDGE)
    model.append(
        [
            FolderRef(path=tmp_path / "2023-京都", mtime_ns=0),
            PhotoRef(path=tmp_path / "IMG_0001.jpg", size=1024, mtime_ns=0),
        ]
    )
    return model, 0, 1


def _option(
    deleg: QStyledItemDelegate, index: QModelIndex, *, selected: bool
) -> QStyleOptionViewItem:
    option = QStyleOptionViewItem()
    option.rect = QRect(QPoint(0, 0), deleg.sizeHint(option, index))
    option.palette = QApplication.palette()
    option.state = QStyle.StateFlag.State_Selected if selected else QStyle.StateFlag.State_None
    return option


def _image_rect(option: QStyleOptionViewItem) -> QRect:
    """取景框在整格里的位置。复刻委托自己的排版算式，免得两边各写一套魔数。"""
    rect = option.rect.adjusted(3, 3, -3, -3)
    return rect.adjusted(
        PhotoDelegate.PADDING,
        PhotoDelegate.PADDING,
        -PhotoDelegate.PADDING,
        -(PhotoDelegate.PADDING + PhotoDelegate.LABEL_HEIGHT),
    )


def _render(deleg: PhotoDelegate, index: QModelIndex, *, selected: bool) -> tuple[QImage, QColor]:
    """把一格画到全透明画布上，返回 ``(图像, 取样点颜色)``。"""
    option = _option(deleg, index, selected=selected)
    image = QImage(option.rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))

    # 取样点：圆角底之内，缩略图与文件名之外的一条窄带。
    image_rect = _image_rect(option)
    sample = QRect(image_rect.right() + 4, (image_rect.top() + image_rect.bottom()) // 2, 1, 1)

    painter = QPainter(image)
    try:
        deleg.paint(painter, option, index)
    finally:
        painter.end()
    return image, image.pixelColor(sample.topLeft())


def _cover_model(tmp_path: Path, kind: str) -> tuple[PhotoListModel, Path]:
    """装着**一张有封面的格子**的模型，返回 ``(模型, 封面的键)``。

    照片、相册、分组卡片走的是同一条绘制路径（``PhotoDelegate.paint`` 里三者只差一个角标），
    封面也都是各自的那一张代表照片 —— 所以圆角三样都得验，少验一样就等于「相册没圆」
    这类回退可以悄悄溜过去。
    """
    model = PhotoListModel(icon_edge=ICON_EDGE)
    if kind == "photo":
        photo = PhotoRef(path=tmp_path / "IMG_0001.jpg", size=1024, mtime_ns=0)
        model.append([photo])
        return model, photo.path
    if kind == "album":
        folder = FolderRef(path=tmp_path / "2023-京都", mtime_ns=0)
        model.append([folder])
        return model, folder.path
    photo = PhotoRef(path=tmp_path / "封面.jpg", size=1024, mtime_ns=0)
    model.reorder(
        [GroupRef(facet="captured_month", key="2024-05", label="2024 年 5 月", photos=(photo,))]
    )
    return model, photo.path


def _solid_cover() -> QImage:
    """纯色封面：得先有颜色，才量得出「角上有没有东西」。"""
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 40, 40))
    return image


def test_unselected_photo_cell_is_transparent(
    qt_app: QApplication, rows: tuple[PhotoListModel, int, int]
) -> None:
    del qt_app
    model, _, photo_row = rows
    deleg = PhotoDelegate(icon_edge=ICON_EDGE)

    _, sample = _render(deleg, model.index(photo_row), selected=False)

    assert sample.alpha() == 0, f"未选中的照片格子铺了底色 {sample.name(QColor.NameFormat.HexArgb)}"


def test_unselected_album_cell_is_transparent(
    qt_app: QApplication, rows: tuple[PhotoListModel, int, int]
) -> None:
    del qt_app
    model, album_row, _ = rows
    deleg = PhotoDelegate(icon_edge=ICON_EDGE)

    _, sample = _render(deleg, model.index(album_row), selected=False)

    assert sample.alpha() == 0, f"未选中的相册格子铺了底色 {sample.name(QColor.NameFormat.HexArgb)}"


def test_selected_album_cell_keeps_highlight_background(
    qt_app: QApplication, rows: tuple[PhotoListModel, int, int]
) -> None:
    del qt_app
    model, album_row, _ = rows
    deleg = PhotoDelegate(icon_edge=ICON_EDGE)
    highlight = QApplication.palette().color(QPalette.ColorRole.Highlight)

    _, sample = _render(deleg, model.index(album_row), selected=True)

    assert sample.alpha() > 0, "选中的格子必须铺底色，否则选中反馈没了"
    # 半透明色存进 premultiplied 画布再读回来会掉一两个色阶，所以留 1 的容差。
    for channel in ("red", "green", "blue"):
        actual = getattr(sample, channel)()
        expected = getattr(highlight, channel)()
        assert abs(actual - expected) <= 1, (
            f"{channel} 通道是 {actual}，期望接近高亮色的 {expected}"
        )


@pytest.mark.parametrize("kind", ["photo", "album", "group"])
def test_thumbnail_corners_are_rounded(qt_app: QApplication, tmp_path: Path, kind: str) -> None:
    """缩略图的四个角要裁圆：角上基本透明，正中间不透明。

    圆角是**图片自己的形状**，不是选中框 —— 所以这里取未选中的那一格，
    免得把那层淡色选中底（半径是 ``RADIUS``）当成圆角验了。
    """
    del qt_app
    model, cover = _cover_model(tmp_path, kind)
    model.set_thumbnail(cover, _solid_cover())
    deleg = PhotoDelegate(icon_edge=ICON_EDGE)
    index = model.index(0, 0)
    option = _option(deleg, index, selected=False)

    image, _ = _render(deleg, index, selected=False)

    pixmap = model.thumbnail_pixmap(cover)
    assert pixmap is not None, "封面刚回填完，模型里就该有这张缩略图"
    # 取景框通常比缩略图大一圈（长宽各留一点余量），量角必须量在**照片自己那块**
    # 上，量取景框的角只会量到本来就空着的地方。
    frame = deleg.target_rect(_image_rect(option), pixmap)
    assert image.pixelColor(frame.center()).alpha() == 255, (
        "缩略图正中间应当是照片本身，别把整张图都裁没了"
    )
    for corner in (frame.topLeft(), frame.topRight(), frame.bottomLeft(), frame.bottomRight()):
        assert image.pixelColor(corner).alpha() <= CLEAR_ALPHA, (
            f"{kind} 的缩略图在 {corner} 上没有裁圆角"
            f"（那里读到 alpha {image.pixelColor(corner).alpha()}，"
            f"裁掉的话应该在 {CLEAR_ALPHA} 以下）"
        )
