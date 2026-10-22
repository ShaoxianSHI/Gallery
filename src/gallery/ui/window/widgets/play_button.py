"""标题条上的放映按钮：一颗经典的三角形，摆在「用外部应用打开」左边。

外观与外部按钮那个药丸同一套（底色、边框、圆角都从 :mod:`gallery.ui.theme` 与
:data:`~gallery.ui.window.widgets.external_button.BUTTON_RADIUS` 来），**图标来自 Qt**
（``QStyle.SP_MediaPlay`` / ``SP_MediaPause``），只按主题的正文色重新上色 —— 标准图标在
深色底上是一颗近黑的三角，直接用会看不见。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QToolButton, QWidget

from gallery.ui import theme
from gallery.ui.rename_edit import field_background
from gallery.ui.window.widgets.external_button import BUTTON_RADIUS

#: 按钮宽度（点）。比外部按钮窄：里面只有一颗图标，没有下拉箭头。
PLAY_BUTTON_WIDTH = 30

#: 图标的边长（点）。与外部按钮里那颗图标同一档。
PLAY_ICON_EDGE = 12

#: 提示气泡上的文案。
TOOLTIP = "放映当前层级的所有照片"

#: 放映中的提示：按它暂停。
TOOLTIP_PAUSE = "暂停放映"

#: 已暂停的提示：按它继续。
TOOLTIP_RESUME = "继续放映"


def _tinted_icon(source: QIcon, color: QColor, size: int) -> QIcon:
    """把 Qt 的标准图标按 ``color`` 重新上色，保留它自己的透明轮廓。"""
    raw = source.pixmap(QSize(size * 2, size * 2)).toImage()
    raw = raw.scaled(
        size * 2,
        size * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QPixmap(raw.size())
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        painter.drawImage(0, 0, raw)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(canvas.rect(), color)
    finally:
        painter.end()
    canvas.setDevicePixelRatio(2)
    return QIcon(canvas)


class PlayButton(QToolButton):
    """放映 / 暂停那一颗按钮。**点击信号由外面接**：这里只管画成什么样。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._paused = False
        self._icon = QIcon()
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(TOOLTIP)
        self.setAccessibleName("放映")
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self._apply_theme)
        self._apply_theme()

    def sizeHint(self) -> QSize:  # noqa: N802 - 名字是 Qt 定的
        return QSize(PLAY_BUTTON_WIDTH, 22)

    def set_running(self, running: bool, *, paused: bool = False) -> None:
        """放映中与已暂停两种状态各自换图标与提示。"""
        if self._running == running and self._paused == paused:
            return
        self._running = running
        self._paused = paused
        self.setToolTip(
            TOOLTIP_PAUSE if running and not paused else TOOLTIP_RESUME if paused else TOOLTIP
        )
        self._apply_icon()
        self.update()

    def _apply_theme(self) -> None:
        self._apply_icon()
        self.update()

    def _apply_icon(self) -> None:
        pixmap = (
            QStyle.StandardPixmap.SP_MediaPause
            if self._running and not self._paused
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        source = self.style().standardIcon(pixmap)
        color = theme.current_theme().text_at(0.78)
        self._icon = _tinted_icon(source, color, PLAY_ICON_EDGE) if not source.isNull() else QIcon()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """药丸底色 + 边框 + 居中的那颗图标。"""
        app_theme = theme.current_theme()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            radius = min(BUTTON_RADIUS, self.height() / 2)
            body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(field_background(app_theme))
            painter.drawRoundedRect(body, radius, radius)
            if self.underMouse():
                hover = QColor(app_theme.background)
                painter.setBrush(hover.lighter(112) if app_theme.is_dark else hover.darker(104))
                painter.drawRoundedRect(body, radius, radius)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(app_theme.text_at(0.28), 1))
            painter.drawRoundedRect(body, radius, radius)
            if not self._icon.isNull():
                edge = min(PLAY_ICON_EDGE, max(0, self.height() - 4))
                area = QRect(
                    (self.width() - edge) // 2,
                    (self.height() - edge) // 2,
                    edge,
                    edge,
                )
                self._icon.paint(painter, area, Qt.AlignmentFlag.AlignCenter)
        finally:
            painter.end()

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self.update()
        super().leaveEvent(event)


__all__ = [
    "PLAY_BUTTON_WIDTH",
    "PLAY_ICON_EDGE",
    "TOOLTIP",
    "TOOLTIP_PAUSE",
    "TOOLTIP_RESUME",
    "PlayButton",
]
