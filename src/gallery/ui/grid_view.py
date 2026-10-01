"""缩略图网格。

视图负责两件事：把格子摆好看，以及回答「现在哪些格子是可见的」。
解码与排队在别处（``ui.thumbnails``），所以滚动时视图只做布局，不会卡。

可见区用 ``indexAt`` 采样计算，而不是遍历全部行 —— 库里有几万张照片时，
后者会让每次滚动都变成 O(n)。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QNativeGestureEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from gallery.ui.thumb_sizes import (
    DEFAULT_EDGE,
    PINCH_THRESHOLD,
    WHEEL_THRESHOLD,
    StepAccumulator,
)

#: 滚动后延迟多久重算可见区。避免每滚一个像素就重排一次缩略图请求。
VIEWPORT_DEBOUNCE_MS = 60

#: 预取时向视口四周外扩的比例（1.0 表示各外扩一个视口）。
PREFETCH_MARGIN = 1.0

#: 格子相对缩略图多出来的留白：横向是卡片内边距，纵向还要放下文件名。
GRID_PADDING_X = 28
GRID_PADDING_Y = 56


class PhotoDelegate(QStyledItemDelegate):
    """把一格画成卡片：圆角底、居中缩略图、底部省略号文件名。"""

    #: 缩略图与卡片边缘的间距。
    PADDING = 8
    #: 文件名区域高度。
    LABEL_HEIGHT = 20
    #: 圆角半径。
    RADIUS = 10

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._icon_edge = icon_edge

    def set_icon_edge(self, edge: int) -> None:
        self._icon_edge = edge

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        try:
            rect = option.rect.adjusted(3, 3, -3, -3)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            palette = option.palette

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            highlight = palette.color(QPalette.ColorRole.Highlight)
            if selected:
                background = QColor(highlight)
                background.setAlpha(90)
            else:
                # 浅色方案下白色几乎不可见，深色方案下黑色几乎不可见；
                # 用文字色的低透明度副本，两种方案下都只是「一层淡淡的底」。
                text_color = palette.color(QPalette.ColorRole.WindowText)
                background = QColor(text_color)
                background.setAlpha(18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

            label_rect = QRect(
                rect.left() + 8,
                rect.bottom() - self.LABEL_HEIGHT,
                rect.width() - 16,
                self.LABEL_HEIGHT,
            )
            image_rect = rect.adjusted(
                self.PADDING,
                self.PADDING,
                -self.PADDING,
                -(self.PADDING + self.LABEL_HEIGHT),
            )

            pixmap = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                size = pixmap.size()
                painter.drawPixmap(
                    image_rect.left() + (image_rect.width() - size.width()) // 2,
                    image_rect.top() + (image_rect.height() - size.height()) // 2,
                    pixmap,
                )
            else:
                self._draw_placeholder(painter, image_rect, palette)

            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.setPen(palette.color(QPalette.ColorRole.WindowText))
            metrics = painter.fontMetrics()
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, label_rect.width()),
            )

            if selected:
                pen = QPen(highlight)
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        finally:
            painter.restore()

    def _draw_placeholder(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        """还没有缩略图时的占位：一个淡色空框，而不是空白或转圈。"""
        color = palette.color(QPalette.ColorRole.WindowText)
        pen = QPen(QColor(color.red(), color.green(), color.blue(), 60))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), 6, 6)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        del option, index
        return QSize(self._icon_edge + GRID_PADDING_X, self._icon_edge + GRID_PADDING_Y)


class PhotoGrid(QListView):
    """缩略图网格视图。"""

    #: 用户双击或按回车选中了一张照片。
    activated_index = Signal(QModelIndex)
    #: 可见区域可能变了（滚动、缩放、插入新行），需要重算缩略图窗口。
    viewport_changed = Signal()
    #: 用户用捏合或 ⌘+滚轮要求换一档缩略图尺寸：正数更大，负数更小。
    size_step_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._icon_edge = icon_edge
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(64)
        self.setIconSize(QSize(icon_edge, icon_edge))
        self.setGridSize(QSize(icon_edge + GRID_PADDING_X, icon_edge + GRID_PADDING_Y))
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._delegate = PhotoDelegate(self, icon_edge=icon_edge)
        self.setItemDelegate(self._delegate)
        # 捏合与滚轮都是连续事件，必须攒够一个阈值才切一档，否则会一路跳到底。
        self._pinch_steps = StepAccumulator(PINCH_THRESHOLD)
        self._wheel_steps = StepAccumulator(WHEEL_THRESHOLD)

        self.doubleClicked.connect(self.activated_index.emit)
        self.activated.connect(self.activated_index.emit)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(VIEWPORT_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.viewport_changed.emit)

    # ---- 尺寸档位 ----------------------------------------------------------

    @property
    def icon_edge(self) -> int:
        return self._icon_edge

    def set_icon_edge(self, edge: int) -> None:
        """切换格子尺寸并重新排版，尽量让当前选中的照片留在视野中央。"""
        if edge == self._icon_edge:
            return
        self._icon_edge = edge
        self.setIconSize(QSize(edge, edge))
        self.setGridSize(QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y))
        self._delegate.set_icon_edge(edge)
        self.doItemsLayout()
        index = self.currentIndex()
        if index.isValid():
            self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._debounce.start()

    # ---- 可见区计算 --------------------------------------------------------

    def visible_rows(self, *, margin_ratio: float = 0.0) -> list[int]:
        """返回当前可见的行号（``margin_ratio`` 让结果向四周外扩）。

        用采样而不是遍历：采样点数只与可见格子数成正比，与库的大小无关。
        """
        rect = self.viewport().rect()
        if rect.isEmpty():
            return []
        grid = self.gridSize()
        step_x = max(1, grid.width())
        step_y = max(1, grid.height())
        pad_x = int(rect.width() * margin_ratio)
        pad_y = int(rect.height() * margin_ratio)
        rows: list[int] = []
        seen: set[int] = set()
        y = rect.top() - pad_y
        bottom = rect.bottom() + pad_y
        while y <= bottom:
            x = rect.left() - pad_x
            right = rect.right() + pad_x
            while x <= right:
                index = self.indexAt(QPoint(x, y))
                if index.isValid() and index.row() not in seen:
                    seen.add(index.row())
                    rows.append(index.row())
                x += step_x
            y += step_y
        return rows

    # ---- 选中与当前行 ------------------------------------------------------

    def current_row(self) -> int:
        index = self.currentIndex()
        return index.row() if index.isValid() else -1

    def select_row(self, row: int, *, ensure_visible: bool = True) -> None:
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        index = model.index(row, 0)
        self.setCurrentIndex(index)
        selection = self.selectionModel()
        if selection is not None:
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        if ensure_visible:
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    # ---- 事件 --------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        """⌘/Ctrl + 滚轮切换缩略图尺寸；普通滚动交给基类翻页。"""
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            steps = self._wheel_steps.feed(float(event.angleDelta().y()))
            if steps:
                self.size_step_requested.emit(1 if steps > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event: QEvent) -> bool:
        # macOS 触控板捏合：原生手势事件不会走 wheelEvent。
        if (
            event.type() == QEvent.Type.NativeGesture
            and isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            steps = self._pinch_steps.feed(float(event.value()))
            if steps:
                self.size_step_requested.emit(1 if steps > 0 else -1)
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                self.activated_index.emit(index)
            return
        super().keyPressEvent(event)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._debounce.start()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._debounce.start()


__all__ = [
    "GRID_PADDING_X",
    "GRID_PADDING_Y",
    "PREFETCH_MARGIN",
    "VIEWPORT_DEBOUNCE_MS",
    "PhotoDelegate",
    "PhotoGrid",
]
