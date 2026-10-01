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
    QObject,
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
    MIN_EDGE,
    PINCH_THRESHOLD,
    WHEEL_THRESHOLD,
    StepAccumulator,
    fit_columns,
)

#: 滚动后延迟多久重算可见区。避免每滚一个像素就重排一次缩略图请求。
VIEWPORT_DEBOUNCE_MS = 60

#: 预取时向视口四周外扩的比例（1.0 表示各外扩一个视口）。
PREFETCH_MARGIN = 1.0

#: 格子相对缩略图多出来的留白：横向是卡片内边距，纵向还要放下文件名。
GRID_PADDING_X = 28
GRID_PADDING_Y = 56

#: 自适应出来的长边与上一次「通知界面重新生成缩略图」的差值小于它时就不折腾：
#: 差几个像素看不出来，却会让整屏缩略图作废重取。
EDGE_REFRESH_THRESHOLD = 6

#: 视口停止变化多久之后才真正通知界面重取缩略图（拖动窗口时的防抖）。
EDGE_SETTLE_MS = 120

#: 排完版多久之后核对实际列数（等布局彻底落定，避免读到半成品）。
COLUMN_CHECK_MS = 50

#: Qt 少摆一列时，按实测列数重算所留的安全边距。
LAYOUT_SAFETY = 40

#: 「视口宽度 → 修正方案」的记忆条目上限。
MAX_LAYOUT_MEMO = 32


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
    #: 自适应后的实际缩略图长边变了，界面需要按新尺寸重新取一遍缩略图。
    icon_edge_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._target_edge = icon_edge
        self._applied_edge = icon_edge
        self._emitted_edge = icon_edge
        self._laying_out = False
        self._laid_out_width = 0
        self._laid_out_columns = 0
        self._layout_fix: dict[int, tuple[int, int]] = {}
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

        self._examiner = QTimer(self)
        self._examiner.setSingleShot(True)
        self._examiner.setInterval(COLUMN_CHECK_MS)
        self._examiner.timeout.connect(self._examine_columns)

        self._edge_timer = QTimer(self)
        self._edge_timer.setSingleShot(True)
        self._edge_timer.setInterval(EDGE_SETTLE_MS)
        self._edge_timer.timeout.connect(self._emit_edge_change)

        # 视口宽度变化（窗口缩放、滚动条出现或消失）都要重算格子宽度。
        self.viewport().installEventFilter(self)

    # ---- 尺寸档位与自适应排版 ----------------------------------------------

    @property
    def icon_edge(self) -> int:
        """实际生效的缩略图长边（随视口宽度自适应，不一定等于档位目标值）。"""
        return self._applied_edge

    @property
    def target_edge(self) -> int:
        """当前档位要求的目标长边。"""
        return self._target_edge

    def set_target_edge(self, edge: int) -> None:
        """换一档目标尺寸。真正的格子宽度由视口宽度决定，见 :meth:`_recompute_layout`。"""
        self._target_edge = edge
        self._layout_fix.clear()
        self._recompute_layout()

    def _columns_for(self, available: int) -> int:
        """这个宽度下打算摆几列。"""
        fix = self._layout_fix.get(available)
        if fix is not None:
            return fix[0]
        return fit_columns(available, self._target_edge + GRID_PADDING_X)

    def _cell_size_for(self, available: int) -> QSize:
        """按列数把可用宽度均分给各列，铺满一行。"""
        columns = self._columns_for(available)
        fix = self._layout_fix.get(available)
        slack = fix[1] if fix is not None else 0
        return self._cell_size_for_width(max(1, (available - slack) // columns))

    def _cell_size_for_width(self, cell_width: int) -> QSize:
        edge = max(MIN_EDGE, cell_width - GRID_PADDING_X)
        return QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y)

    def _recompute_layout(self) -> None:
        """按当前视口宽度重排，让第一行铺满、右侧不留空白。"""
        if self._laying_out:
            return
        available = self.viewport().width()
        if available <= 0:
            return
        columns = self._columns_for(available)
        # 视口宽度变了就必须重排：格子宽度可能恰好没变，但列数是按视口宽度算的，
        # 不重排就会沿用旧列数，右边留下一条空白。
        if available == self._laid_out_width and columns == self._laid_out_columns:
            return
        self._apply_cell(self._cell_size_for(available))
        self._laid_out_width = available
        self._laid_out_columns = columns
        self._examiner.start()

    def _examine_columns(self) -> None:
        """排完版之后核对一次实际列数。

        Qt 的换行宽度与视口宽度并不严格相等（实测在宽窗口下会少摆一列），
        猜公式不如实测：真少了就按实测列数重算，并留出安全边距保证这次摆得下。
        """
        available = self.viewport().width()
        intended = self._laid_out_columns
        if available != self._laid_out_width or intended <= 0:
            return
        measured = self._first_row_columns()
        if measured <= 0 or measured >= intended:
            return
        if len(self._layout_fix) > MAX_LAYOUT_MEMO:
            self._layout_fix.clear()
        self._layout_fix[available] = (measured, LAYOUT_SAFETY)
        self._apply_cell(self._cell_size_for(available))
        self._laid_out_columns = measured

    def _first_row_columns(self) -> int:
        """实际排在第一行的格子数（只采样一行，代价与库大小无关）。"""
        matrix = self.model()
        if matrix is None or matrix.rowCount() == 0:
            return 0
        top = self.visualRect(matrix.index(0, 0)).top()
        count = 0
        for row in range(matrix.rowCount()):
            if self.visualRect(matrix.index(row, 0)).top() != top:
                break
            count += 1
        return count

    def _apply_cell(self, cell: QSize) -> None:
        if cell == self.gridSize():
            return
        self._laying_out = True
        try:
            self._applied_edge = cell.width() - GRID_PADDING_X
            self._delegate.set_icon_edge(self._applied_edge)
            self.setIconSize(QSize(self._applied_edge, self._applied_edge))
            self.setGridSize(cell)
            self.doItemsLayout()
            index = self.currentIndex()
            if index.isValid():
                self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        finally:
            self._laying_out = False
        self._schedule_edge_update()

    def _schedule_edge_update(self) -> None:
        if abs(self._applied_edge - self._emitted_edge) < EDGE_REFRESH_THRESHOLD:
            return
        self._edge_timer.start()

    def _emit_edge_change(self) -> None:
        if self._applied_edge == self._emitted_edge:
            return
        self._emitted_edge = self._applied_edge
        self.icon_edge_changed.emit(self._applied_edge)

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
        self._recompute_layout()
        self._debounce.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            # 延到事件循环下一轮：视口尺寸变化本身就发生在布局过程中，
            # 立刻重排会把布局再推进一次。
            QTimer.singleShot(0, self._recompute_layout)
        return super().eventFilter(watched, event)


__all__ = [
    "COLUMN_CHECK_MS",
    "EDGE_REFRESH_THRESHOLD",
    "EDGE_SETTLE_MS",
    "LAYOUT_SAFETY",
    "GRID_PADDING_X",
    "GRID_PADDING_Y",
    "PREFETCH_MARGIN",
    "VIEWPORT_DEBOUNCE_MS",
    "PhotoDelegate",
    "PhotoGrid",
]
