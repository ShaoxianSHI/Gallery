"""把一个控件画在「它现在坐着的那一块底色」上。

控件自己不再铺底色（ADR 0096），于是单拉出来 ``grab()`` / ``render()`` 量到的是一片空白，
判不出它到底画了什么。要量，就得先替窗口把那一块铺上，并且**别让 Qt 顺手把调色板那层也画一遍**
（``render`` 默认带 ``DrawWindowBackground``，量到的会是调色板而不是界面）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QWidget


def render_over(widget: QWidget, background: QColor) -> QImage:
    """把 ``widget`` 与它的子控件画在 ``background`` 上，返回一张新图。"""
    image = QImage(widget.size(), QImage.Format.Format_RGBA8888)
    image.fill(background)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image
