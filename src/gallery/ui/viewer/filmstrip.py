"""大图页底部那一条缩略图导航。

它只是一块画布：条目（行号 + 路径）、当前行与取图函数都由窗口给。当前那张尽量居中，
到这一层的头尾时自然贴边。布局是纯算术（:func:`strip_rect` / :func:`centered_offset`），
不必造窗口就能测。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget

from gallery.ui import theme
from gallery.ui.outline import draw_outline

#: 一格缩略图与格间的缝（点）。
CELL = 64
GAP = 6

#: 面板内边距、面板圆角，以及缩略图自己的圆角。
PADDING = 8
PANEL_RADIUS = 10
THUMB_RADIUS = 5

#: 面板离内容区左右与底边各留多少。**不紧贴底边**：贴边会与窗口圆角、Dock 那一带挤在一起。
SIDE_MARGIN = 32
BOTTOM_MARGIN = 28

#: 面板最宽多少。再宽就成了一条横贯整屏的窄带，当前那张周围反而空出一大片。
MAX_WIDTH = 1180

#: 面板底色的不透明度：它压在照片上，得够实才看得清缩略图的边。
PANEL_ALPHA = 0.82


def strip_height() -> int:
    """面板高多少（一格缩略图加上下内边距）。"""
    return CELL + 2 * PADDING


def strip_rect(area: QRect) -> QRect:
    """面板摆在 ``area`` 的哪儿：水平居中，离底边留 :data:`BOTTOM_MARGIN` 点。"""
    width = min(max(area.width() - 2 * SIDE_MARGIN, 1), MAX_WIDTH)
    height = strip_height()
    return QRect(
        area.x() + (area.width() - width) // 2,
        area.bottom() - BOTTOM_MARGIN - height + 1,
        width,
        height,
    )


def content_width(count: int) -> int:
    """``count`` 格排成一行有多宽（含内边距与格间的缝）。"""
    if count <= 0:
        return 0
    return 2 * PADDING + count * CELL + (count - 1) * GAP


def centered_offset(viewport_width: int, count: int, current: int) -> int:
    """要让第 ``current`` 格居中，整排内容要向左移多少点。

    内容比视口短时返回**负数** —— 整排居中摆，于是当前那张也落在中间。当前格贴近两端时
    返回被夹住的那个值：头尾自然贴边，中间那些严格居中。
    """
    if count <= 0:
        return 0
    content = content_width(count)
    if content <= viewport_width:
        return -((viewport_width - content) // 2)
    center = PADDING + current * (CELL + GAP) + CELL / 2
    return max(0, min(round(center - viewport_width / 2), content - viewport_width))


def cell_rect(index: int, offset: int) -> QRect:
    """第 ``index`` 格在面板里的位置；``offset`` 是滚动后内容左移的点数。"""
    return QRect(PADDING + index * (CELL + GAP) - offset, PADDING, CELL, CELL)


def visible_indices(viewport_width: int, count: int, offset: int) -> range:
    """当前视口里看得到的那几格。**只按这一条自己的宽度裁剪**。"""
    if count <= 0 or viewport_width <= 0:
        return range(0)
    step = CELL + GAP
    first = max(0, (offset - PADDING) // step)
    last = min(count - 1, (offset + viewport_width - PADDING) // step)
    if last < first:
        return range(0)
    return range(first, last + 1)


class Filmstrip(QWidget):
    """底部那一排缩略图：当前那张选中并尽量居中，点一下换到那一条。"""

    #: 用户点了一格：带的是**这一层的行号**，由窗口决定怎么换页。
    photo_activated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[int, ...] = ()
        self._paths: tuple[Path, ...] = ()
        self._current = -1
        self._offset = 0
        self._hover = -1
        self._pressed = -1
        self._enabled = True
        self._provider: Callable[[Path], QPixmap | None] | None = None
        # 面板自己铺一层半透明底色压在照片上；别让 Qt 再铺一块不透明底。
        theme.mark_see_through(self)
        # 点缩略图不该把键盘从大图页抢走，否则之后 ← / → 就不翻图了。
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self._watcher = theme.ensure_installed()
        self._watcher.changed.connect(self.update)
        self.hide()

    # ---- 内容 --------------------------------------------------------------

    def set_thumbnail_provider(self, provider: Callable[[Path], QPixmap | None]) -> None:
        """取一张原始缩略图；``None`` 表示还没有（画占位块）。"""
        self._provider = provider

    def set_enabled(self, enabled: bool) -> None:
        """视图菜单那个开关：收起 / 放出这条面板，**已经摆好的那一批不动**。"""
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._apply_visibility()

    def set_items(self, items: Sequence[tuple[int, Path]], current_row: int) -> None:
        """换一批条目并把 ``current_row`` 选中。**顺序就是这一层的照片顺序**。"""
        rows = tuple(row for row, _path in items)
        paths = tuple(path for _row, path in items)
        if (rows, paths) == (self._rows, self._paths) and current_row == self.current_row:
            return
        self._rows = rows
        self._paths = paths
        self._current = rows.index(current_row) if current_row in rows else -1
        self._hover = -1
        self._pressed = -1
        self._apply_visibility()
        self._recenter()
        self.update()

    def _apply_visibility(self) -> None:
        # 一张照片时这条面板没有可导航的东西，白白压住画面；随手关掉时也一样收起来。
        self.setVisible(self._enabled and self._current >= 0 and len(self._rows) > 1)

    def rows(self) -> tuple[int, ...]:
        """这一批条目在这一层里的行号，按显示顺序。"""
        return self._rows

    def visible_rows(self) -> tuple[int, ...]:
        """视口里看得到的那几行。面板还没摆出来时给**全部** —— 宁可多取几张图。"""
        if not self.isVisible() or self.width() <= 0:
            return self._rows
        return tuple(
            self._rows[index]
            for index in visible_indices(self.width(), len(self._rows), self._offset)
        )

    @property
    def current_row(self) -> int:
        """当前选中那一格的行号（列表里没有就是 ``-1``）。"""
        if 0 <= self._current < len(self._rows):
            return self._rows[self._current]
        return -1

    def refresh(self) -> None:
        """缩略图到货之后重画。**取图函数每次现问**，所以不必自己记账新旧。"""
        self.update()

    # ---- 布局 --------------------------------------------------------------

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._recenter()

    def _recenter(self) -> None:
        self._offset = centered_offset(self.width(), len(self._rows), self._current)

    # ---- 绘制 --------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if not self._rows:
            return
        current = theme.current_theme()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            fill = QColor(current.background)
            fill.setAlphaF(PANEL_ALPHA)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(panel, PANEL_RADIUS, PANEL_RADIUS)
            edge = QColor(current.text)
            edge.setAlphaF(0.14)
            draw_outline(painter, panel, PANEL_RADIUS, edge)
            for index in visible_indices(self.width(), len(self._rows), self._offset):
                self._draw_cell(painter, index, current)
        finally:
            painter.end()

    def _draw_cell(self, painter: QPainter, index: int, current: theme.AppTheme) -> None:
        cell = QRectF(cell_rect(index, self._offset))
        thumb = self._pixmap(index)
        if thumb is None:
            placeholder = QColor(current.text)
            placeholder.setAlphaF(0.08)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(placeholder)
            painter.drawRoundedRect(cell, THUMB_RADIUS, THUMB_RADIUS)
        else:
            self._draw_thumb(painter, cell, thumb)
        if index == self._current:
            draw_outline(painter, cell.adjusted(-1, -1, 1, 1), THUMB_RADIUS + 1, current.link, 2.0)
        elif index == self._hover:
            hover = QColor(current.text)
            hover.setAlphaF(0.45)
            draw_outline(painter, cell, THUMB_RADIUS, hover)

    def _draw_thumb(self, painter: QPainter, cell: QRectF, thumb: QPixmap) -> None:
        """按比例缩到格子里居中。**与网格委托同一做法**：就地画，不先产出一张缩小的位图。"""
        size = thumb.size().scaled(QSize(CELL, CELL), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(
            cell.x() + (cell.width() - size.width()) / 2,
            cell.y() + (cell.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )
        clip = QPainterPath()
        clip.addRoundedRect(target, THUMB_RADIUS, THUMB_RADIUS)
        painter.save()
        try:
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(target, thumb, QRectF(thumb.rect()))
        finally:
            painter.restore()

    def _pixmap(self, index: int) -> QPixmap | None:
        provider = self._provider
        if provider is None or not 0 <= index < len(self._paths):
            return None
        source = provider(self._paths[index])
        if source is None or source.isNull():
            return None
        return source

    # ---- 鼠标 --------------------------------------------------------------

    def _index_at(self, point: QPoint) -> int:
        for index in visible_indices(self.width(), len(self._rows), self._offset):
            if cell_rect(index, self._offset).contains(point):
                return index
        return -1

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._index_at(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        index = self._index_at(event.position().toPoint())
        pressed = self._pressed
        self._pressed = -1
        if pressed >= 0 and index == pressed:
            if index != self._current:
                self.photo_activated.emit(self._rows[index])
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        index = self._index_at(event.position().toPoint())
        if index != self._hover:
            self._hover = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        if self._hover >= 0:
            self._hover = -1
            self.update()
        super().leaveEvent(event)


__all__ = [
    "BOTTOM_MARGIN",
    "CELL",
    "GAP",
    "MAX_WIDTH",
    "PADDING",
    "Filmstrip",
    "cell_rect",
    "centered_offset",
    "content_width",
    "strip_height",
    "strip_rect",
    "visible_indices",
]
