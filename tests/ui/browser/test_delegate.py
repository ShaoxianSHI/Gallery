"""网格卡片的底色回归测试。

这里不检查控件树，而是**量渲染出来的像素**：把一格画到全透明的画布上，看它有没有
真的铺底色。

- 未选中的照片与相册都不该铺底色。密排时相邻格子的底色会连成一片，照片本身的
  边界反而被衬得看不清（见 b2f05c5 与本次改动）。
- 选中必须仍然铺一层高亮底色，否则「选中了哪一张」的反馈就没了。

取样点取在「圆角底之内、缩略图与文件名之外」的那一小条：那里除了底色不该有别的
东西，采样不会撞到缩略图、角标或文字。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
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

from gallery.core.library.photos import FolderRef, PhotoRef  # noqa: E402
from gallery.ui.browser.grid import PhotoDelegate  # noqa: E402
from gallery.ui.browser.model import PhotoListModel  # noqa: E402

#: 取样用的缩略图边长。小一点就跑得快，格子结构完全一样。
ICON_EDGE = 96


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


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


def _render(deleg: PhotoDelegate, index: QModelIndex, *, selected: bool) -> tuple[QImage, QColor]:
    """把一格画到全透明画布上，返回 ``(图像, 取样点颜色)``。"""
    option = _option(deleg, index, selected=selected)
    image = QImage(option.rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))

    # 取样点：圆角底之内，缩略图与文件名之外的一条窄带。复刻委托自己的排版算式，
    # 免得两边各写一套魔数。
    rect = option.rect.adjusted(3, 3, -3, -3)
    image_rect = rect.adjusted(
        PhotoDelegate.PADDING,
        PhotoDelegate.PADDING,
        -PhotoDelegate.PADDING,
        -(PhotoDelegate.PADDING + PhotoDelegate.LABEL_HEIGHT),
    )
    sample = QRect(image_rect.right() + 4, (image_rect.top() + image_rect.bottom()) // 2, 1, 1)

    painter = QPainter(image)
    try:
        deleg.paint(painter, option, index)
    finally:
        painter.end()
    return image, image.pixelColor(sample.topLeft())


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
