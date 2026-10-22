"""标题条上的放映按钮：一颗经典的「圆圈 + 三角」，摆在「用外部应用打开」左边。

**图标自己画**：Qt 的 ``SP_MediaPlay`` 在 22 点的按钮里只占很小一块，而且带一圈内边距，
看上去既小又偏心。这里按按钮的几何中心直接铺一颗三角（暂停时换成两根竖条），尺寸、位置
都不再受标准图标的留白影响。底色、边框、圆角仍与外部按钮同一套主题色。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QToolButton, QWidget

from gallery.ui import theme
from gallery.ui.rename_edit import field_background

#: 按钮宽度（点）。与高度相同，画出来是一颗圆。
PLAY_BUTTON_WIDTH = 22

#: 按钮高度（点）。与搜索胶囊同高。
PLAY_BUTTON_HEIGHT = 22

#: 三角占圆圈直径的比例。留出一圈空白，不把按钮塞满。
TRIANGLE_SCALE = 0.38

#: 提示气泡上的文案。
TOOLTIP = "放映当前层级的所有照片"

#: 放映中的提示：按它暂停。
TOOLTIP_PAUSE = "暂停放映"

#: 已暂停的提示：按它继续。
TOOLTIP_RESUME = "继续放映"


class PlayButton(QToolButton):
    """放映 / 暂停那一颗按钮。**点击信号由外面接**：这里只管画成什么样。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._paused = False
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(TOOLTIP)
        self.setAccessibleName("放映")
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)

    def sizeHint(self) -> QSize:  # noqa: N802 - 名字是 Qt 定的
        return QSize(PLAY_BUTTON_WIDTH, PLAY_BUTTON_HEIGHT)

    def set_running(self, running: bool, *, paused: bool = False) -> None:
        """放映中与已暂停两种状态各自换图形与提示。"""
        if self._running == running and self._paused == paused:
            return
        self._running = running
        self._paused = paused
        self.setToolTip(
            TOOLTIP_PAUSE if running and not paused else TOOLTIP_RESUME if paused else TOOLTIP
        )
        self.update()

    def _apply_theme(self) -> None:
        self.update()

    def _body_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _play_polygon(self) -> QPolygonF:
        """居中的三角。**光心略往右偏**：等边三角的重心偏左，摆在几何中心反而看着偏左。"""
        body = self._body_rect()
        size = min(body.width(), body.height()) * TRIANGLE_SCALE
        center = body.center()
        left = center.x() - size * 0.38
        return QPolygonF(
            [
                QPointF(left, center.y() - size / 2),
                QPointF(left, center.y() + size / 2),
                QPointF(left + size * 0.88, center.y()),
            ]
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """圆底 + 边框 + 居中的三角（放映中换成两根竖条）。"""
        app_theme = theme.current_theme()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            body = self._body_rect()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(field_background(app_theme))
            painter.drawEllipse(body)
            if self.underMouse():
                hover = QColor(app_theme.background)
                painter.setBrush(hover.lighter(112) if app_theme.is_dark else hover.darker(104))
                painter.drawEllipse(body)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(app_theme.text_at(0.30), 1))
            painter.drawEllipse(body)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(app_theme.text_at(0.78))
            if self._running and not self._paused:
                self._paint_pause(painter)
            else:
                painter.drawPolygon(self._play_polygon())
        finally:
            painter.end()

    def _paint_pause(self, painter: QPainter) -> None:
        """暂停：两根圆角竖条，宽度与三角同一档。"""
        body = self._body_rect()
        height = min(body.width(), body.height()) * TRIANGLE_SCALE
        width = height * 0.32
        gap = width * 0.9
        top = body.center().y() - height / 2
        left = body.center().x() - gap / 2 - width
        for x in (left, left + width + gap):
            painter.drawRoundedRect(QRectF(x, top, width, height), width / 2, width / 2)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.update()
        super().leaveEvent(event)


__all__ = [
    "PLAY_BUTTON_HEIGHT",
    "PLAY_BUTTON_WIDTH",
    "TOOLTIP",
    "TOOLTIP_PAUSE",
    "TOOLTIP_RESUME",
    "TRIANGLE_SCALE",
    "PlayButton",
]
