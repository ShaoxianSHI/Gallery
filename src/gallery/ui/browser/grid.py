"""缩略图网格。

视图负责两件事：把格子摆好看，以及回答「现在哪些格子是可见的」。
解码与排队在别处（``ui.thumbnails``），所以滚动时视图只做布局，不会卡。

可见区用 ``indexAt`` 采样计算，而不是遍历全部行 —— 库里有几万张照片时，
后者会让每次滚动都变成 O(n)。
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import (
    QAbstractItemModel,
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
    QContextMenuEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
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
    QApplication,
    QFrame,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from gallery.ui.browser.model import ALBUM_ROLE, GROUP_COUNT_ROLE, GROUP_ROLE
from gallery.ui.browser.thumb_sizes import (
    DEFAULT_EDGE,
    MIN_EDGE,
    PINCH_THRESHOLD,
    WHEEL_THRESHOLD,
    StepAccumulator,
    fit_columns,
)
from gallery.ui.rename_edit import RenameEdit

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

#: **每一列**额外让出的像素，理由见 :meth:`PhotoGrid._cell_size_for`。
CELL_SLACK = 2

#: Qt 少摆一列时，按实测列数重算所留的安全边距。
LAYOUT_SAFETY = 40

#: 「视口宽度 → 修正方案」的记忆条目上限。
MAX_LAYOUT_MEMO = 32

#: 拖动距离的兜底值。正常情况取平台自己的数字（见 :func:`_drag_threshold`）。
_FALLBACK_DRAG_DISTANCE = 10


def _drag_threshold() -> int:
    """鼠标按下后挪多远才算「拖动」。

    用平台自己的数字（macOS 上是 10 像素），不写死常量：手抖的幅度因设备、指针
    与人而异，平台比我们清楚。实在问不到时给一个保守的下限。
    """
    hints = QGuiApplication.styleHints()
    distance = hints.startDragDistance() if hints is not None else 0
    return max(4, distance or _FALLBACK_DRAG_DISTANCE)


#: 扩选用的修饰键。Qt 在 macOS 上把 Command 报成 ControlModifier。
_EXTEND_MODIFIERS: Final = (
    Qt.KeyboardModifier.ShiftModifier
    | Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.MetaModifier
)


class PhotoDelegate(QStyledItemDelegate):
    """把一格画成卡片：居中缩略图、底部省略号文件名。

    底色一律透明，缩略图与文件名直接落在网格背景上；只有选中才铺一层淡色圆角底，
    好让「选中了哪一张」一眼可见。相册与照片都不再靠底色区分：相册有自己的封面角标，
    封面还没到时画的是文件夹图形。
    """

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

    def label_rect(self, rect: QRect) -> QRect:
        """文件名那一行的位置。

        绘制与行内重命名的输入框共用这一份算法：输入框必须正好盖住名字，
        否则用户会看到「框在别处、字在这儿」。
        """
        return QRect(
            rect.left() + 8,
            rect.bottom() - self.LABEL_HEIGHT,
            rect.width() - 16,
            self.LABEL_HEIGHT,
        )

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        try:
            rect = option.rect.adjusted(3, 3, -3, -3)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            is_album = bool(index.data(ALBUM_ROLE))
            is_group = bool(index.data(GROUP_ROLE))
            palette = option.palette

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            highlight = palette.color(QPalette.ColorRole.Highlight)
            # 未选中时不铺底色，照片与相册一视同仁 —— 否则密排时相邻格子的底色
            # 连成一片，照片本身的边界反而被衬得看不清了。相册靠封面角标和文件夹
            # 图形区分，不靠底色。
            background = QColor(highlight)
            background.setAlpha(90 if selected else 0)
            painter.setPen(Qt.PenStyle.NoPen)
            if background.alpha() > 0:
                painter.setBrush(background)
                painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

            label_rect = self.label_rect(rect)
            image_rect = rect.adjusted(
                self.PADDING,
                self.PADDING,
                -self.PADDING,
                -(self.PADDING + self.LABEL_HEIGHT),
            )

            pixmap = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                self._draw_thumbnail(painter, image_rect, pixmap)
                if is_album:
                    self._draw_album_badge(painter, image_rect)
                elif is_group:
                    self._draw_group_badge(painter, image_rect, str(index.data(GROUP_COUNT_ROLE)))
            elif is_album or is_group:
                self._draw_folder_glyph(painter, image_rect, highlight)
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

    def _draw_thumbnail(self, painter: QPainter, rect: QRect, pixmap: QPixmap) -> None:
        """把缩略图放进取景框。

        尺寸刚切换时手里的图还是按旧边长缩放的，这里就地缩放到新框 —— 于是每一格
        始终有图、大小也始终对得上，用户看不到「先空框、再一张张弹回来」的过程。
        尺寸一致时走 1:1 直接绘制，不额外付平滑缩放的代价。

        **这里刻意不走 :func:`gallery.ui.imaging.scale_pixmap`。** 那两处（后台线程
        出图、模型换档位）是把缩略图**缩成一张新的位图存起来**，缩放是一次性的
        预处理；这里是在**绘制时**按目标矩形画 —— 用的是 ``QPainter`` 的
        ``SmoothPixmapTransform``，不额外产出中间位图（那才是每次重绘一张的代价）。
        两件事看起来像，实则不是同一件事，硬凑成一份只会把「缓存」和「画」搅在一起。
        """
        target = pixmap.size().scaled(
            QSize(self._icon_edge, self._icon_edge), Qt.AspectRatioMode.KeepAspectRatio
        )
        x = rect.left() + (rect.width() - target.width()) // 2
        y = rect.top() + (rect.height() - target.height()) // 2
        if target == pixmap.size():
            painter.drawPixmap(x, y, pixmap)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(QRect(x, y, target.width(), target.height()), pixmap)

    def _draw_folder_glyph(self, painter: QPainter, rect: QRect, color: QColor) -> None:
        """空相册（或封面还没到时）画一个文件夹，而不是「加载中」的虚线框。"""
        box = rect.adjusted(
            rect.width() // 5, rect.height() // 3, -rect.width() // 5, -rect.height() // 3
        )
        if box.width() < 24 or box.height() < 18:
            box = rect.adjusted(8, 8, -8, -8)
        tab_width = max(14, box.width() // 3)
        tab = QRect(
            box.left(), box.top() - max(4, box.height() // 6), tab_width, max(6, box.height() // 4)
        )
        fill = QColor(color)
        fill.setAlpha(200)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(tab, 3, 3)
        painter.drawRoundedRect(box, 4, 4)

    def _draw_group_badge(self, painter: QPainter, rect: QRect, count: str) -> None:
        """分组卡片：封面左上角盖一个张数徽标（相册盖的是「相册」两个字）。

        用封面而不是文件夹图形当分组卡片的正脸 —— 组里照片长什么样比「这里有一堆」
        更值得看一眼；封面还没到时才退回文件夹图形。
        """
        self._draw_badge(painter, rect, count)

    def _draw_album_badge(self, painter: QPainter, rect: QRect) -> None:
        """有封面的相册：左上角盖一个「相册」小标签。"""
        self._draw_badge(painter, rect, "相册")

    def _draw_badge(self, painter: QPainter, rect: QRect, text: str) -> None:
        """在取景框左上角盖一个小标签。"""
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 4
        badge = QRect(rect.left() + 4, rect.top() + 4, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(badge, 6, 6)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)

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
    #: 用户双击了**没有格子**的空白处：窗口据此退回上一层（见 MainWindow.go_up）。
    background_activated = Signal()
    #: 可见区域可能变了（滚动、缩放、插入新行），需要重算缩略图窗口。
    viewport_changed = Signal()
    #: 用户用捏合或 ⌘+滚轮要求换一档缩略图尺寸：正数更大，负数更小。
    size_step_requested = Signal(int)
    #: 自适应后的实际缩略图长边变了，界面需要按新尺寸重新取一遍缩略图。
    icon_edge_changed = Signal(int)
    #: 用户再次点击了已经选中的格子：可以开始重命名了。
    rename_requested = Signal(QModelIndex)
    #: 行内编辑确认了新名字：``(行号, 新名字)``。
    rename_committed = Signal(int, str)
    #: 行内重命名开始了（``True``）或收起来了（``False``）。
    #: 改名的这段时间 Esc 归输入框（取消），窗口据此把「上一级」让开。
    rename_active_changed = Signal(bool)
    #: 用户在网格上按了右键：参数是**该作用于哪一行**，``-1`` 表示落在空白处。
    #: 视图负责把选择摆正（见 :meth:`select_for_context`），菜单由窗口拼。
    context_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._target_edge = icon_edge
        self._applied_edge = icon_edge
        self._emitted_edge = icon_edge
        self._laying_out = False
        self._laid_out_width = 0
        self._laid_out_columns = 0
        self._layout_fix: dict[int, tuple[int, int]] = {}
        #: 「这一格已经处在『再点一下就是改名』的状态」——**不等于「它被选中了」**。
        #:
        #: 「选中」是界面状态，「arm」是**交互状态**：得是用户自己指过的那一格，
        #: 否则刚打开应用时随手点一下高亮着的照片就会直接弹输入框。所以它不能由
        #: 「当前项是不是这一格」推出来，得有人明确记下：普通左键点选的抬起
        #: （:meth:`mouseReleaseEvent`）与开始改名（:meth:`begin_rename`）。
        #:
        #: 两条路径缺一不可 —— 只认左键，⌘R 进来又 Esc 出去的那一格就永远没 arm 过，
        #: 用户再点它会毫无反应（它看上去明明一直选着）。
        self._armed_row = -1
        self._double_click = False
        self._pending_rename_row = -1
        #: 「当前正处在一次行内重命名里」——与 ``_rename_edit.is_editing()`` 是两件事，
        #: 别拿后者顶替：见 :meth:`_end_rename_session` 末尾那段。
        self._rename_active = False
        # 最近一次左键按下的位置、修饰键，以及按下那一刻的选中与当前行：
        # 用来把「手抖」和「真的在拖」分开，并在抬起时按用户的心意重算选择。
        self._press_point: QPoint | None = None
        self._press_extends = False
        self._press_shift = False
        self._press_selection: tuple[int, ...] = ()
        self._press_anchor = -1
        self._press_target = -1
        #: 扩选的锚点：最后一次普通点选的行（`⌘` / `⇧` 点都不挪它）。
        self._range_anchor = -1
        # 去掉 QListView 默认的 StyledPanel 边框：那是一圈系统灰的描边，画在视口四周，
        # 于是整个照片区（最里层）看上去像浮在一块面板上，而不是铺满窗口。大图区那边
        # 本来就是 NoFrame，这里跟它对齐 —— 底色只有一块，边框不该再分出第二层。
        self.setFrameShape(QFrame.Shape.NoFrame)
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
        # 多选：⌘⌫ 一次把选中的若干项移进废纸篓（见 ADR 0008）。重命名仍然是单条的 ——
        # 批量改名一次改错就是几十张照片找不到，理由见 ADR 0004。
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._delegate = PhotoDelegate(self, icon_edge=icon_edge)
        self.setItemDelegate(self._delegate)
        # 捏合与滚轮都是连续事件，必须攒够一个阈值才切一档，否则会一路跳到底。
        self._pinch_steps = StepAccumulator(PINCH_THRESHOLD)
        self._wheel_steps = StepAccumulator(WHEEL_THRESHOLD)

        # **只接 ``activated``**。``QAbstractItemView.activated`` 在鼠标双击**和**按
        # 回车时都会发，所以它一条就够；再接上 ``doubleClicked`` 等于把双击那次算两遍。
        # 而「算两遍」在这里不是多一次刷新那么简单：进入相册是**同步**的（模型清空、
        # 重新排版），于是第二次激活落到了**刚进入的那一层**上，可能顺手打开一张
        # 与刚才点的完全无关的照片。
        self.activated.connect(self.activated_index.emit)

        # 行内重命名的输入框：挂在视口上，跟着格子一起滚。
        self._rename_edit = RenameEdit(self.viewport())
        self._rename_edit.committed.connect(self._on_rename_committed)
        self._rename_edit.cancelled.connect(self.cancel_rename)

        # 「再点一次」的等待窗口：等过这么久还没等到第二下，才认定是想改名。
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.setInterval(QApplication.doubleClickInterval())
        self._rename_timer.timeout.connect(self._emit_pending_rename)

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

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        """装上模型，并盯住「条目到齐」这个时刻。

        「核对实际列数」（:meth:`_examine_columns`）量不出来的时候只可能是因为**条目还没到齐**：
        扫库是分批往里塞的，网格刚摆出来时模型里一行都没有，等排版跑完再看当然量不准，
        而过去的办法是「量不准就算了」—— 那次修正再也不会发生，于是右边就一直空着。

        真正决定格子宽度的是视口宽度而不是条目数（见 :meth:`_cell_size_for`），所以这里
        不用重排版，只需要给核对补一次机会。

        换模型时**先摘掉上一个的连接**：``setModel`` 不是只调一次（切库、换浏览方式
        都会），旧模型若还留着一条指向本控件的连接，它就既不能被回收，也会在
        旧模型自己发信号时叫醒一个已经不显示它的控件。
        """
        previous = self.model()
        if previous is not None:
            previous.rowsInserted.disconnect(self._on_rows_inserted)
            previous.modelReset.disconnect(self._on_model_reset)
        super().setModel(model)
        if model is not None:
            # 扫描每来一批就重启一次 50ms 的定时器，一屏之内最多多核对一次，代价可以忽略。
            model.rowsInserted.connect(self._on_rows_inserted)
            model.modelReset.connect(self._on_model_reset)

    def _on_rows_inserted(self, *_args: object) -> None:
        """模型里多了一批条目 → 给「核对实际列数」补一次机会（定时器 50ms 后触发）。"""
        self._examiner.start()

    def _on_model_reset(self, *_args: object) -> None:
        """模型整体重置（换层、换排序）→ 旧的 arm 作废。

        ``_armed_row`` 记的是**行号**，而重置之后摆上来的是**另一批条目**：换层
        （``MainWindow`` 的 ``_model.clear()``）与换排序（``PhotoListModel.reorder``）
        都会走到这里。留着旧行号，用户在新一层里点那一格，第一下就直接改名 ——
        而他这一层里什么都没指过。

        这一层随后多半会 :meth:`select_row` 把该选中的那一格摆出来，那时会重新 arm 上
        （见 :attr:`_armed_row`），所以「退上去之后停在刚离开的那一格」仍然是可改名的。
        """
        self._armed_row = -1

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
        """按列数把可用宽度均分给各列，铺满一行。

        **排版宽度不是视口宽度**：Qt 换行时会先扣掉纵向滚动条，再扣掉样式的默认视图边距
        （实测 macOS 样式下是 16 + 1~6 像素，offscreen 下是 18 + 1~6 像素）。我们过去
        直接拿视口宽去除，于是 ``1260 // 6 = 210`` 正好卡在 Qt 摆不下 6 个的边界上，
        它只摆 5 个 —— **右边空出整整一列**，在 1260 宽的窗口里就是 210 像素的空白。
        「放大再缩小」会重新排一次版、恰好绕开了这个边界，于是又「好了」，但那是碰巧。

        所以这里扣两笔：滚动条预留（见 :meth:`_scrollbar_reserve`）与每列 2 像素的
        余量（吃掉样式的默认边距，按列数摊开，列越多余量越大，怎么都不会再掉列）。
        剩下的十几像素就是网格页本来就有的左右留白，看不出来。
        """
        columns = self._columns_for(available)
        fix = self._layout_fix.get(available)
        slack = fix[1] if fix is not None else CELL_SLACK * columns
        usable = available - self._scrollbar_reserve() - slack
        return self._cell_size_for_width(max(1, usable // columns))

    def _scrollbar_reserve(self) -> int:
        """纵向滚动条要从排版宽度里先扣掉一条。

        **无条件扣**：Qt 是等滚动条排过一次版之后就把它的宽度算进去的，管它当刻显不显示
        （实测内容只有几行、滚动条根本没出现时，换行宽度照样窄了 18 像素）。跟着它的实际
        状态走就等于猜它什么时候「见过世面」，不如一律按样式给出的宽度扣 —— 代价只是
        没有滚动条时右边多留十几像素，而少留就会掉一整列。
        """
        return self.verticalScrollBar().sizeHint().width()

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
        if available != self._laid_out_width:
            # 修正方案是「在这个宽度下量出来的」，换宽度就不再适用。留着它会把别的
            # 宽度也带偏：旧宽度上量到的「只摆得下 5 列」会一直套用到新的视口上。
            self._layout_fix.clear()
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
        matrix = self.model()
        if matrix is None or matrix.rowCount() < intended:
            # 条目还不够摆满一行时，「第一行的格子数」其实等于条目总数，
            # 拿它当列数会把两个相册撑成整屏。这种时候根本量不出真实列数。
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
        # 代码里指定选中（撤销取回、回到网格…）同样算「一次普通点选」：
        # 接下来用户的第一次 `⇧` 点应该从这一格铺开。
        self._range_anchor = row
        # 同理，「再点一下就是改名」也得认它：大图页退回来（``show_grid``）、往上退一层
        # （``_restore_level_focus``）、撤销把照片取回来的那一格，界面上都是**选中**的，
        # ⌘R 也亮着 —— 用户点它一下就该改名，不该因为「不是用左键选的」而要点第二下。
        self._armed_row = row
        selection = self.selectionModel()
        if selection is not None:
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        if ensure_visible:
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def selected_rows(self) -> list[int]:
        """当前选中的行号（升序）。

        删除要按这个顺序取条目，但摘行要**从后往前**（见
        ``PhotoListModel.remove_rows``）—— 前面的行被摘掉之后，后面的行号就变了。

        **选区为空时会退回当前项**（网格刚打开时 Qt 可能顺手把第 0 行设成当前项）。
        这个兜底对「删除」「拷贝」这类命令是对的：它们总得作用于点什么。但它
        **不是**「谁被选中了」的答案 —— 判可见性（⌘V 粘到哪里）或判右键落点时
        要问的是真选区，见 :meth:`genuinely_selected_rows`。
        """
        rows = self.genuinely_selected_rows()
        if rows:
            return rows
        row = self.current_row()
        return [row] if row >= 0 else []

    def genuinely_selected_rows(self) -> list[int]:
        """**真选区**，没有当前项兜底（见 :meth:`selected_rows` 末尾那段）。"""
        selection = self.selectionModel()
        if selection is None:
            return []
        return sorted({index.row() for index in selection.selectedIndexes()})

    def clear_current(self) -> None:
        """把「谁也没选中」做成一个明确的状态：选区与当前项一起清掉。

        **当前项要一起清**，不能只清选区：``⌘R`` / ``⌘⌫`` 的可用性判据读的是
        ``current_row``，留着它就会出现「菜单亮着、作用的对象在屏幕上不存在」
        （同一个道理见 :meth:`select_for_context` 末尾那段）。

        扩选锚点（``_range_anchor``）**不在这里动**：右键空白处也要用它，见
        :meth:`reset_for_new_level`。
        """
        self.clearSelection()
        self.setCurrentIndex(QModelIndex())

    def reset_for_new_level(self) -> None:
        """换层：这一层从零开始，网格上不留上一层的一切痕迹。

        除了 :meth:`clear_current`，还要作废**扩选锚点**：它记的是**上一层**的行号，
        换层之后那一行指着的是别的东西 —— 留着它，下一次 `⇧` 点会从一层之隔的那一格
        铺开，选出一段用户并没有要过的区间。
        """
        self.clear_current()
        self._range_anchor = -1

    def select_for_context(self, row: int) -> int:
        """右键之前的选区修正。返回菜单该作用于哪一行（``-1`` = 空白处）。

        ``QListView`` 默认的 ``contextMenuEvent`` **不会**选中光标下的项，于是原生菜单里
        「右键 A、命令却作用在原来选中的 B 上」是 Qt 的默认行为。规则定死成三种
        （ADR 0014 决策 2）：

        | 落点 | 选区 |
        |------|------|
        | 未选中的那一格 | 清掉旧的，只选它 |
        | 已经选中的一格 | **不动** —— 多选保持原样，菜单作用于整个选择 |
        | 空白处 | 清掉选区（与访达一致），菜单里只会有视图级命令 |

        刻意**不挪扩选锚点**（``_range_anchor``）：那是「上一次完成的普通左键点选」，
        挪了它，「右键 A → ⇧ 点 D」就会从 A 铺开，而访达是从上一次左键那格铺开的。
        改选区与改锚点必须是两件事（对照 :meth:`select_row`，那里两件事是一起的）。

        空白处**连当前项一起清掉**：只清选区的话，「重命名」的可用性判据里那个
        ``current_row`` 仍然是有效的，用户会拿到一个作用在看不见的东西上的按钮。
        扩选锚点**留着** —— 右键不是一次点选，理由见上面那段。

        「已经选中」问的是**真选区**（:meth:`genuinely_selected_rows`）而不是
        :meth:`selected_rows`。后者在选区为空时会退回当前项，于是「Qt 顺手把第 0 行
        设成了当前项、但谁也没选中」这个状态会被误判成「它已经选中了」而直接返回 ——
        结果一个格子都没选中，菜单却摆出来了，而菜单里每一条命令读的都是真选区：
        用户看到的是「右键了京都」，实际作用的是「什么都没选」。
        """
        self._armed_row = -1
        self._rename_timer.stop()
        if row < 0:
            self.clear_current()
            return -1
        model = self.model()
        selection = self.selectionModel()
        if model is None or selection is None or not 0 <= row < model.rowCount():
            return -1
        if row in self.genuinely_selected_rows():
            return row  # 已经在选中里：整份选择就是作用对象，一个格子都不动
        index = model.index(row, 0)
        self.setCurrentIndex(index)
        selection.select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        return row

    # ---- 行内重命名 --------------------------------------------------------

    def begin_rename(self, index: QModelIndex) -> bool:
        """在 ``index`` 的文件名位置开始行内重命名。返回是否真的开始了。

        照片只选中主干、不选扩展名：直接打字就是在改名字，而 ``.jpg`` 不会被手滑敲掉。

        **顺带把这一格 arm 上**，这样「⌘R 进去 → Esc / 回车出来 → 再点它一下」与
        「连点两下」是同一件事。理由见 :attr:`_armed_row` 那一行：用户此刻正在处置的
        就是这一格，它也正是当前项；不 arm 的话，这次点击会被当成「第一次：只选中」，
        用户就得点第二下 —— 可屏幕上明明一直高亮着那一格。
        """
        self._rename_timer.stop()
        self._pending_rename_row = -1
        model = self.model()
        if model is None or not index.isValid():
            return False
        text = str(model.data(index, int(Qt.ItemDataRole.DisplayRole)) or "")
        if not text:
            return False
        cell = self.visualRect(index).adjusted(3, 3, -3, -3)
        rect = self._delegate.label_rect(cell)
        # 比标签高一圈：输入框贴着 20px 的标签会显得挤，也不好点。
        #
        # **只往上长那 3 点。** ``cell`` 就是选中时铺的那层淡色底的边界（``PhotoDelegate.paint``
        # 用的是同一个矩形），名字那一行的下沿正好贴着它。``QRect.setTop`` 自己就会把高度补上
        # （底边不动），所以**不能再补一句 setHeight** —— 那句补的正是往下那半圈，于是输入框的
        # 下半截挂在选中框外面，看着像错位（有了圆角边框之后尤其明显）。
        rect.setTop(rect.top() - 3)
        self._rename_edit.start(rect, text, keep_suffix=not index.data(ALBUM_ROLE))
        self._rename_active = True
        self._armed_row = index.row()
        self.rename_active_changed.emit(True)
        return True

    def cancel_rename(self) -> None:
        """收起输入框，不提交。"""
        self._rename_timer.stop()
        self._pending_rename_row = -1
        self._rename_edit.stop()
        self._end_rename_session()

    def is_renaming(self) -> bool:
        return self._rename_edit.is_editing()

    def _on_rename_committed(self, text: str) -> None:
        self._end_rename_session()
        row = self.current_row()
        if row >= 0:
            self.rename_committed.emit(row, text)

    def _end_rename_session(self) -> None:
        """一次行内重命名结束了（提交、Esc 取消，或外部强制收起）。

        **这里不能靠「输入框现在还看得见吗」来判断有没有东西要收尾。**
        用户按 Esc 时走的是 ``RenameEdit._cancel``：它先 ``hide()``、**再**发
        ``cancelled``。等这个槽跑起来时输入框早就藏好了，于是一个「之前确实正在改名、
        现在收起来了」的事实被读成了「什么都没在改」——「输入框收起来了」那条信号
        压根没发出去，菜单里 ⌘R / ⌘⌫ / ⇧⌘N / ⌘⏎ 于是**永远留在禁用态**。
        症状很怪：照片明明还选着，别的命令都亮着，唯独「重命名…」再按 ⌘R 没反应。

        所以 session 状态由这里自己记：``begin_rename`` 开，``_end_rename_session`` 关，
        与输入框的可见性解耦。守卫仍留着 —— 外部强制收起一个没在改的名字时不该广播。
        """
        if not self._rename_active:
            return
        self._rename_active = False
        self.rename_active_changed.emit(False)

    # ---- 事件 --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """记下这一下的来龙去脉（供 mouseMoveEvent / mouseReleaseEvent 判断）。"""
        point = event.position().toPoint()
        self._press_point = point
        modifiers = event.modifiers()
        self._press_extends = bool(modifiers & _EXTEND_MODIFIERS)
        self._press_shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self._press_selection = tuple(self.selected_rows())
        index = self.indexAt(point)
        self._press_target = index.row() if index.isValid() else -1
        # 锚点是「上一次**完成**的普通点选」那一格。`⌘` 点只是加一个 / 减一个，
        # `⇧` 点是从锚点铺开，两者都不挪锚点 —— 否则「点 0 → ⌘点 5 → ⇧点 21」
        # 会从 5 铺开，把 1~4 漏在区间外（访达是从 0 铺开的）。
        # 之所以看「完成」而不是「按下」：先按下鼠标、想起要扩选再补上 `⇧`，
        # 那一格不该被当成新的锚点。
        self._press_anchor = self._range_anchor
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """手抖不算拖动，扩选更不算。

        图标视图里 Qt 在**按下瞬间**就进入「框选」状态（``DragSelectingState``），
        之后只要鼠标挪动就开始画橡皮筋 —— 那 10 像素的拖动距离只管拖拽，不管它。
        后果很具体，而且极其容易撞上：带 `⇧` / `⌘` 点选时手总要抖上一两个像素，
        于是「从 A 扩选到 B 的连续区间」被悄悄换成一个**矩形框选**。网格是分行排的，
        矩形落到屏幕上就变成「每行只选中最边上那一两个格子」—— 隔一个跳一个，
        正是「多选看起来有点奇怪」的那种奇怪。

        所以挡掉两种误判：带修饰键的一律当扩选（想框选就空着手拖），
        空着手也要挪够平台自己的拖动距离，才把事件交给基类去框选。
        """
        point = self._press_point
        if point is not None and event.buttons() & Qt.MouseButton.LeftButton:
            moved = (event.position().toPoint() - point).manhattanLength()
            if self._press_extends or moved < _drag_threshold():
                event.accept()
                return
        super().mouseMoveEvent(event)

    def _reassert_extended_selection(self, target: int, *, shift: bool) -> None:
        """抬起前把「这一下到底想要什么选择」自己算一遍，不指望基类没越权。

        上一道只是**不让**橡皮筋画起来；这里再兜一层：即便它画了（Qt 的内部状态
        机不是我们能完全预测的），抬起时也按用户的心意重算一次。带着修饰键点下去
        的时候，意图其实是毫无歧义的 —— `⇧` 是「锚点到落点的连续区间」，
        `⌘` 是「落点加一个 / 减一个」。

        之所以要自己算，是因为这一下**只有两种可能的心意**：按区间来，或者按框来。
        含糊的代价是选中一个「每行几个」的集合，用户按 `⌘⌫` 删掉的却不是自己
        以为的那几张照片 —— 这种错必须由我们钉死，不能寄望于基类恰好老实。

        落点取**按下时**那一格，而不是抬起时鼠标底下那一格：缩略图还在源源不断
        到货，网格随时可能重排，等鼠标抬起来，底下的东西已经不是用户刚才瞄准的那个
        了。按下时选中的正是那一格，重算也该跟它一致。
        """
        model = self.model()
        selection = self.selectionModel()
        if model is None or selection is None or not 0 <= target < model.rowCount():
            return
        rows = set(self._press_selection)
        anchor = self._press_anchor if self._press_anchor >= 0 else target
        if shift:
            low, high = sorted((anchor, target))
            rows.update(range(low, high + 1))
        else:
            rows.symmetric_difference_update({target})  # ⌘ 点：加一个或减一个
        selection.clearSelection()
        for row in sorted(rows):
            selection.select(
                model.index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """第一次点击只是选中；对**已经选中**的格子再点一次才开始重命名。

        第二次点击不立刻改名，而是按下双击阈值**等一等**：Qt 是在第二次「按下」时
        判定双击的，而人的两下快慢不一（按下快、抬起慢时，两次抬起的间隔会超过阈值，
        于是「双击打开」会被误判成「再次点击改名」，照片没打开，倒先弹了个输入框）。

        所以这里改成：等 Qt 先说话。它认成双击就去打开照片（见
        :meth:`mouseDoubleClickEvent`），一直没等到双击，才是重命名。
        """
        super().mouseReleaseEvent(event)
        # 这一次点击结束了，「从哪儿按下、带着什么修饰键」也就过期了：
        # 之后的手动悬停不该再被拦。修饰键按下与抬起时**都**看一眼：
        # 「先按下鼠标、再补上 ⇧」也是常见手势，只认按下那一刻会漏掉它。
        modifiers = event.modifiers()
        press_point, self._press_point = self._press_point, None
        press_target, self._press_target = self._press_target, -1
        press_extends, self._press_extends = self._press_extends, False
        press_shift, self._press_shift = self._press_shift, False
        extending = press_extends or bool(modifiers & _EXTEND_MODIFIERS)
        if extending and event.button() == Qt.MouseButton.LeftButton and press_point is not None:
            shift = press_shift or bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            self._reassert_extended_selection(press_target, shift=shift)
        elif not extending and event.button() == Qt.MouseButton.LeftButton:
            # 一次完整走完的普通点选：它才是下一次扩选的起点。
            self._range_anchor = press_target
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._double_click:
            # 双击的第二下：接下来是抬起，不该被当成「再次点击」。
            self._double_click = False
            self._armed_row = -1
            return
        index = self.indexAt(event.position().toPoint())
        if not index.isValid() or index != self.currentIndex():
            # 点到别处：改主意了，把刚才那点「再点一次」作废。
            self._armed_row = -1
            self._rename_timer.stop()
            return
        if self._armed_row != index.row():
            self._armed_row = index.row()  # 第一次点击：只选中
            return
        self._armed_row = -1
        self._pending_rename_row = index.row()
        self._rename_timer.start()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """双击一定要打开照片，而不是拉出一个输入框；落在空白处就退回上一层。

        「双击空白处往回退」是访达里用惯了的手势，而在逐层进入的浏览方式里
        「上一层」是最高频的动作之一（照片没铺满一屏时，右下角总有一大片空白）。
        视图只负责回答「这一下落在格子还是空白」，要不要退、能不能退由窗口判断
        （分组要退，库根退不了）。

        **落点必须在交给基类之前问**：``super()`` 会双击打开，而打开相册 / 进入分组
        是**同步**的 —— 模型当场清空、版面重排。那之后再问「这一点底下还有没有格子」，
        问到的已经是新的一层：刚点开的相册被自己当成空白，又立刻退了回去，
        屏幕上只剩一闪。
        """
        self._rename_timer.stop()
        self._pending_rename_row = -1
        self._armed_row = -1
        self._double_click = True
        on_blank = not self.indexAt(event.position().toPoint()).isValid()
        super().mouseDoubleClickEvent(event)
        if on_blank:
            # 这一层连一格都没有时，界面上到处都是空白，双击自然会走到这里 ——
            # 正是最需要「双击空白处退出去」的情形：空相册、空分组。
            self.background_activated.emit()

    def _emit_pending_rename(self) -> None:
        """等过了双击阈值仍然没等到第二下 —— 那就当用户确实想改名。"""
        row = self._pending_rename_row
        self._pending_rename_row = -1
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        index = model.index(row, 0)
        if index == self.currentIndex() and not self._rename_edit.is_editing():
            self.rename_requested.emit(index)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """右键：先把选区摆正，再把「这一下落在哪」告诉窗口。

        视图只回答落点，菜单由 ``MainWindow`` 拼（``ui.menus``）—— 视图不该知道
        「重命名为」是什么，也不该知道分组为什么删不掉。

        行内改名进行中**不弹菜单**：那时键盘归输入框，右键也归它（与 ``trash_selected``
        里的同一道守卫同源，见 ADR 0014）。
        """
        if self._rename_edit.is_editing():
            event.accept()
            return
        index = self.indexAt(event.pos())
        row = self.select_for_context(index.row() if index.isValid() else -1)
        self.context_requested.emit(row)
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        # **带修饰符的回车不是「打开」**：⌘⏎ 归「在访达中显示」、⇧⏎ 归「在默认应用中打开」
        # （两条都是窗口级 QAction，见 ADR 0014 决策 6 / 7）。它们按下时 Qt 先取走，按键根本
        # 走不到这里；但它们**不可用**时（选中的是分组卡片、正在改名字）按下去是走得到的 ——
        # 那时要是照旧当成「打开」，用户以为打开去了默认应用，其实打开的仍是 Gallery。
        # 宁可什么都不做。
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            index = self.currentIndex()
            if index.isValid():
                self.activated_index.emit(index)
            return
        before = self.currentIndex()
        super().keyPressEvent(event)
        # 方向键挪动当前项时，Qt 会把选区一起挪过去 —— 挪到的那一格在界面上就是**选中**的
        # （画着选中框、⌘R 亮着），所以「再点一下就是改名」也得认它，与 ``select_row``
        # 同一个道理。用户用键盘走过来之后不该反过来要点两下才改得了名。
        #
        # 只在**真的挪动了**当前项时才 arm：按一个按不动的方向键（在第 0 行还按 ↑）
        # 什么也没发生，那一格并不是用户指过的，无条件 arm 会把「刚打开时 Qt 顺手设的
        # 那个当前项」也 arm 上 —— 于是打开应用随手点一下照片就直接弹输入框。
        index = self.currentIndex()
        if index.isValid() and index != before:
            self._armed_row = index.row()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - 名字是 Qt 定的
        # 输入框是视口的子控件，不会自己跟着格子滚 —— 手动挪同样的距离，
        # 它就始终盖在那个名字上（滚出视口也会被视口裁掉，不会浮到别处）。
        if self._rename_edit.is_editing():
            self._rename_edit.move(self._rename_edit.pos() - QPoint(dx, dy))
        super().scrollContentsBy(dx, dy)
        self._debounce.start()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._recompute_layout()
        self._debounce.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            # 延到事件循环下一轮：视口尺寸变化本身就发生在布局过程中，
            # 立刻重排会把布局再推进一次。
            #
            # 第三个参数（context）是**必须**的：没有它，这个 0 毫秒的定时器与本对象
            # 毫无关系，于是「收到 resize 事件 → 同一轮里窗口被关掉/换库」这种再平常
            # 不过的时序，会让它照常触发并去调一个已经析构的 ``_recompute_layout``，
            # 换来一个 ``Internal C++ object already deleted``。给了 context，Qt 会在
            # 接收方析构时自动取消它。
            QTimer.singleShot(0, self, self._recompute_layout)
        return super().eventFilter(watched, event)


__all__ = [
    "COLUMN_CHECK_MS",
    "EDGE_REFRESH_THRESHOLD",
    "EDGE_SETTLE_MS",
    "GRID_PADDING_X",
    "GRID_PADDING_Y",
    "LAYOUT_SAFETY",
    "PREFETCH_MARGIN",
    "VIEWPORT_DEBOUNCE_MS",
    "PhotoDelegate",
    "PhotoGrid",
]
