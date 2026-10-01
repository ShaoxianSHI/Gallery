"""元数据面板的可读性回归测试。

这里的两条测试都是补真实缺陷：

1. 面板曾经是**系统白底配浅色文字**，几乎看不清 —— 因为普通 ``QWidget`` 不设
   ``WA_StyledBackground`` 就不会画样式表里的背景。所以这里不检查控件树，
   而是**量渲染出来的像素**：底色要深、文字对比度要够。
2. 换一份内容时旧控件会被 ``deleteLater`` 排队删除，在真正销毁前仍画在原位置，
   于是上一份内容糊在新内容上面。所以这里检查旧文案确实不见了。

Qt 的离屏平台让这些断言不需要真实显示器。
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from gallery.core.metadata import build_report  # noqa: E402
from gallery.ui.metadata_panel import PANEL_BACKGROUND, MetadataPanel  # noqa: E402

#: 可读性的最低对比度（WCAG AA 对正文的要求）。
MIN_CONTRAST = 4.5

#: 底色亮度上限：低于它才算「深色底」。
MAX_BACKGROUND_LUMINANCE = 60


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


def luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def contrast_ratio(first: QColor, second: QColor) -> float:
    brighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (brighter + 5) / (darker + 5)


def sample_colors(widget: QWidget) -> tuple[QColor, QColor]:
    """返回 ``(出现最多的颜色, 最亮的颜色)``：前者是底色，后者是文字。"""
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    counts: Counter[int] = Counter()
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            counts[image.pixelColor(x, y).rgb()] += 1
    ranked = [QColor(rgb) for rgb, _ in counts.most_common(400)]
    return ranked[0], max(ranked, key=luminance)


@pytest.fixture
def report(tmp_path: Path) -> object:
    return build_report(
        {"EXIF:Make": "Canon", "EXIF:Model": "Canon EOS R6", "EXIF:ISO": "400"},
        path=tmp_path / "IMG_0001.JPG",
        size=1024,
        mtime_ns=0,
        source="exiftool",
    )


def test_panel_is_dark_with_readable_text(qt_app: QApplication, report: object) -> None:
    panel = MetadataPanel()
    panel.resize(300, 420)
    panel.set_report(report)  # type: ignore[arg-type]
    panel.show()
    qt_app.processEvents()

    background, text = sample_colors(panel)

    assert luminance(background) < MAX_BACKGROUND_LUMINANCE, (
        f"面板底色是 {background.name()}，太亮了：浅底配浅字根本看不清"
    )
    assert contrast_ratio(background, text) >= MIN_CONTRAST, (
        f"底色 {background.name()} 与文字 {text.name()} 的对比度不足"
    )
    assert luminance(PANEL_BACKGROUND) < MAX_BACKGROUND_LUMINANCE
    panel.deleteLater()


def test_switching_report_leaves_no_stale_labels(qt_app: QApplication, report: object) -> None:
    panel = MetadataPanel()
    panel.clear()
    qt_app.processEvents()
    assert any("尚未打开照片" in label.text() for label in panel.findChildren(QLabel))

    panel.set_report(report)  # type: ignore[arg-type]
    qt_app.processEvents()

    remaining = [label.text() for label in panel.findChildren(QLabel)]
    assert not any("尚未打开照片" in text for text in remaining)
    assert any("相机" in text for text in remaining)
    panel.deleteLater()
