"""``gallery.ui.detail_view`` 的显示尺寸回归测试。

守的是一条用户直接看得见的约定：**占位缩略图与真正的全图，占的地方必须一样大**。

缓存未命中时，窗口先用网格里的缩略图顶着显示，全分辨率图随后替换。曾经两者显示的
大小差一大截（实测小 34%），用户看到的就是「照片先小一大圈、再跳着变大」。

原因不在缩略图本身，而在于「适应窗口」算出来的比例被手动缩放的上限（8 倍）卡住：
一张 168 像素的缩略图要在大窗口里铺满，需要 12 倍以上的缩放。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gallery.ui.detail_view import DetailView  # noqa: E402
from gallery.ui.zoom import MAX_SCALE  # noqa: E402

#: 视口尺寸：故意开到很大，否则小图铺满只需要几倍，卡不出上限那条路径。
VIEWPORT = (2400, 1400)

#: 一张 2400×1600 的照片，以及它的 168×112 占位缩略图（同一比例）。
FULL_SIZE = (2400, 1600)
THUMBNAIL_SIZE = (168, 112)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _displayed_size(view: DetailView) -> tuple[float, float]:
    width, height = view.image_size
    scale = view.current_scale
    return (round(width * scale, 1), round(height * scale, 1))


def _image(size: tuple[int, int]) -> QImage:
    return QImage(size[0], size[1], QImage.Format.Format_RGB32)


def test_placeholder_thumbnail_fills_the_view_like_the_full_image(
    qt_app: QApplication,
) -> None:
    view = DetailView()
    view.resize(*VIEWPORT)
    view.show()
    qt_app.processEvents()
    try:
        view.set_image(_image(FULL_SIZE))
        full = _displayed_size(view)

        view.set_image(_image(THUMBNAIL_SIZE), refit=True)
        placeholder = _displayed_size(view)

        assert placeholder[0] == pytest.approx(full[0], rel=0.02), (
            f"占位缩略图宽 {placeholder[0]}，全图宽 {full[0]} —— 大小不一致"
        )
        assert placeholder[1] == pytest.approx(full[1], rel=0.02), (
            f"占位缩略图高 {placeholder[1]}，全图高 {full[1]} —— 大小不一致"
        )
    finally:
        view.close()


def test_small_photo_still_fills_the_window(qt_app: QApplication) -> None:
    """一张本来就很小的照片，打开时也应该铺满窗口，而不是缩在中间。

    这是同一个上限的另一面：以前 8 倍的上限会让小图缩在角落里。
    """
    view = DetailView()
    view.resize(*VIEWPORT)
    view.show()
    qt_app.processEvents()
    try:
        view.set_image(_image((300, 200)))
        qt_app.processEvents()
        width, height = _displayed_size(view)
        viewport = view.viewport().size()
        # 高度方向应当顶到视口（留出适配边距）
        assert height == pytest.approx(viewport.height() * 0.98, rel=0.02)
        assert width > viewport.width() * 0.3
    finally:
        view.close()


def test_manual_zoom_is_still_capped(qt_app: QApplication) -> None:
    """上限只放开「适应窗口」，手动放大仍然受 MAX_SCALE 约束。"""
    view = DetailView()
    view.resize(800, 600)
    view.show()
    qt_app.processEvents()
    try:
        view.set_image(_image((FULL_SIZE[0], FULL_SIZE[1])))
        for _ in range(60):
            view.zoom_in()
        assert view.current_scale == pytest.approx(MAX_SCALE)
    finally:
        view.close()
