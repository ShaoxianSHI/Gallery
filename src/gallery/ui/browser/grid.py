"""缩略图网格。

视图只管摆格子与回答「哪些格子可见」，解码与排队在 :mod:`gallery.ui.thumbs`。
可见区用 ``indexAt`` 采样计算，不能遍历全部行 —— 几万张时每次滚动都成 O(n)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import (
    QAbstractAnimation,
    QAbstractItemModel,
    QEasingCurve,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
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
    QPainterPath,
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
    MIN_PINCH_INTERVAL_MS,
    PINCH_THRESHOLD,
    WHEEL_STEP_PIXELS,
    RateLimitedAccumulator,
    StepAccumulator,
    fit_columns,
    wheel_delta,
)
from gallery.ui.rename_edit import RenameEdit
from gallery.ui.scrollbar_style import apply_scrollbar_style

#: 滚动后延迟多久重算可见区。避免每滚一个像素就重排一次缩略图请求。
VIEWPORT_DEBOUNCE_MS = 60

#: 预取时向视口四周外扩的比例（1.0 表示各外扩一个视口）。
PREFETCH_MARGIN = 1.0

#: 格子相对缩略图多出来的留白：横向是卡片内边距，纵向还要放下文件名。
GRID_PADDING_X = 28
GRID_PADDING_Y = 56

#: 长边与上次「通知界面重取缩略图」的差值小于它就不折腾，否则整屏缩略图作废重取。
EDGE_REFRESH_THRESHOLD = 6

#: 视口停止变化多久之后才真正通知界面重取缩略图（拖动窗口时的防抖）。
EDGE_SETTLE_MS = 120

#: Qt 的非精确滚轮**一格**是这么多角度单位（120 = 一格），与 ``thumb_sizes`` 里同一个约定。
ANGLE_UNITS_PER_NOTCH: Final = 120.0

#: 鼠标滚轮**一格**滚多少像素（Qt 的非精确滚轮一格是 120 个角度单位）。
#: Qt 自己那一档（``wheelScrollLines()`` × 格子高）中档一跳三行；240 约等于过一行。
WHEEL_NOTCH_PIXELS: Final = 240

#: 触控板两指滚动的**像素增量**乘以它：Qt 把 ``pixelDelta`` 丢掉了、只按角度增量算，
#: 而 macOS 的换算是「1 像素 = 2 个角度单位」，照角度算会把手指距离放大一倍多。
WHEEL_PIXEL_FACTOR: Final = 0.5

#: 切档时格子「长过去/缩回去」的时长（毫秒）。只驱动**画面**，不驱动几何与缩略图请求。
EDGE_ANIMATION_MS = 160

#: 切档过渡的缓动曲线：**两端都缓**的对称曲线（``OutCubic`` 第一帧就走完 27%，是一跳）。
EDGE_ANIMATION_CURVE = QEasingCurve.Type.InOutCubic

#: 边长差小于这个值就不动画：差几个像素看不出来，却白付一次全视口重绘。
EDGE_ANIMATION_MIN_DELTA = 6

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


@dataclass(frozen=True)
class GridTransition:
    """换一档时，每一格从旧布局飞到新布局的那段位移。

    **只描述画面，不描述逻辑状态**：``gridSize``、``visualRect``、``indexAt`` 在切换
    那一刻就已经是最终值了，Qt 全程不知道有过过渡。**不用逐帧重排**：显式
    ``doItemsLayout`` 会强制全量排版，几万条目时单次几十毫秒，60 帧装不下；而绘制时
    改一块矩形只是 O(可见格数) 的几次整数加法。

    两端的列数都是**量出来的**（见 :meth:`PhotoGrid._first_row_columns`）：
    :meth:`PhotoGrid._columns_for` 给的只是「打算摆几列」，用错每格会整体漂移。
    """

    old_columns: int
    old_cell: QSize
    new_columns: int
    new_cell: QSize
    #: 旧画面整体要跟着挪多少像素（内容坐标）：不补这一笔，第 0 帧整幅画会挪到屏幕外面去。
    scroll_shift: int = 0

    @property
    def reflows(self) -> bool:
        """列数变了 —— 有格子从上一行挪到下一行，光缩放救不回来。

        **只看列数**：格子大小差几个像素时肉眼根本看不出来，却要白付一整轮全视口
        重绘（那正是 :data:`EDGE_ANIMATION_MIN_DELTA` 管的事）。
        """
        return self.old_columns != self.new_columns

    @staticmethod
    def _position(columns: int, cell: QSize, row: int) -> QPoint:
        """第 ``row`` 格在「``columns`` 列、每格 ``cell``」这套布局里的左上角。"""
        return QPoint((row % columns) * cell.width(), (row // columns) * cell.height())

    def offset(self, row: int, t: float) -> QPoint:
        """第 ``row`` 格相对**最终位置**要往回挪多少，才能落在第 ``t`` 帧。

        ``t=0`` 给回旧位置，``t=1`` 给 0。坐标是**内容坐标**：视图的滚动平移由 Qt
        统一叠在上面。
        """
        old = self._position(self.old_columns, self.old_cell, row)
        new = self._position(self.new_columns, self.new_cell, row)
        k = 1.0 - t
        return QPoint(
            round((old.x() - new.x()) * k),
            round((old.y() - new.y()) * k) + round(self.scroll_shift * k),
        )

    def frame_rect(self, final: QRect, row: int, t: float) -> QRect:
        """第 ``row`` 格在第 ``t`` 帧该画成的那块矩形（``final`` 是 Qt 给的终态）。

        **位置与尺寸一起插**：只插位置的话，卡片（选中底、角标、文件名）在切换那一刻
        就已经是新尺寸、只有照片在长（见 :meth:`PhotoDelegate.cell_rect`）。尺寸长到
        ``final.size()`` 而不是 :attr:`new_cell`，因为那才是 Qt 真正排出来的那一格；
        两端于是各自与切换前/后的那一帧完全一致，中间没有混血帧。
        """
        size = QSize(
            round(self.old_cell.width() + (final.width() - self.old_cell.width()) * t),
            round(self.old_cell.height() + (final.height() - self.old_cell.height()) * t),
        )
        return QRect(final.topLeft() + self.offset(row, t), size)


@dataclass(frozen=True)
class _ScrollAnchor:
    """换几何**之前**，视口正中那一格：行号，以及它当时在屏幕上的纵向位置。

    **两个都要**：行号用来算它换完之后落在第几行（列数一变行号就变了），屏幕位置
    用来把它摆回原处 —— 只摆到视口正中会多挪最多半格，那也是一跳。
    """

    row: int
    screen_y: int


def _drag_threshold() -> int:
    """鼠标按下后挪多远才算「拖动」。

    用平台自己的数字（macOS 上是 10 像素），不写死常量；问不到时给一个保守的下限。
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

    底色一律透明，只有选中才铺一层淡色圆角底，好让「选中了哪一张」一眼可见；
    相册靠封面角标与文件夹图形区分，不靠底色。照片、相册与分组卡片三样都圆角。
    """

    PADDING = 8
    LABEL_HEIGHT = 20
    RADIUS = 10
    #: 缩略图自身的圆角半径。**只轻微柔化**，占位虚线框用同一个值，否则落地那格就换了形状。
    THUMB_RADIUS = 5

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._icon_edge = icon_edge
        #: **画出来**的边长：切档动画要把它取成小数并逐帧变化，几何尺寸则必须一直是整数。
        self._draw_edge = float(icon_edge)
        #: 切档过渡：这一帧相对最终位置往回挪多少。``None`` 表示不偏移（常态）。
        self._transition: GridTransition | None = None
        self._t = 1.0

    def set_icon_edge(self, edge: int) -> None:
        """几何尺寸变了 —— 绘制尺寸先跟它对齐。

        动画是在 :meth:`PhotoGrid._set_cell` 里、几何定下来**之后**另行发起的，它会把
        :meth:`draw_edge` 拨回旧尺寸再往新尺寸走，所以这里顺手对齐不会吃掉动画的起点。
        """
        self._icon_edge = edge
        self._draw_edge = float(edge)

    @property
    def draw_edge(self) -> float:
        """当前**画出来**的缩略图长边。动画期间是小数且逐帧变化。"""
        return self._draw_edge

    def set_draw_edge(self, edge: float) -> None:
        """只改绘制尺寸，不碰几何。见 :attr:`draw_edge`。"""
        self._draw_edge = max(1.0, edge)

    def set_transition(self, transition: GridTransition | None, t: float) -> None:
        """这一帧该画在哪、画多大（``None`` 表示不偏移、就是 Qt 给的那一格）。

        与 :meth:`set_draw_edge` 是同一个 ``t`` 的两半：分成两个动画就会是两个时钟，
        早晚差一帧 —— 那一帧里照片已经变了尺寸、格子却还没到位。
        """
        self._transition = transition
        self._t = t if transition is not None else 1.0

    def cell_offset(self, row: int) -> QPoint:
        """第 ``row`` 格相对 ``option.rect`` 的额外位移（没有过渡时是原点）。

        **位置只是这一帧的一半** —— 整格的矩形见 :meth:`cell_rect`。
        """
        if self._transition is None:
            return QPoint()
        return self._transition.offset(row, self._t)

    def cell_rect(self, rect: QRect, row: int) -> QRect:
        """Qt 给的那一格在这一帧该画成什么样（没有过渡时原样返回）。

        **位置与尺寸一起换**：只平移的话，卡片的框、选中底与文件名已经是新尺寸、
        只有照片在长；整格一起插值，卡片才是一个整体在长大。
        """
        if self._transition is None:
            return rect
        return self._transition.frame_rect(rect, row, self._t)

    def label_rect(self, rect: QRect) -> QRect:
        """文件名那一行的位置。

        绘制与行内重命名的输入框共用这一份，否则会看到「框在别处、字在这儿」。
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
            # 过渡中这一格画在「旧布局与最终布局之间」的那一帧，位置与**大小**一起插值。
            rect = self.cell_rect(option.rect, index.row()).adjusted(3, 3, -3, -3)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            is_album = bool(index.data(ALBUM_ROLE))
            is_group = bool(index.data(GROUP_ROLE))
            palette = option.palette

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            highlight = palette.color(QPalette.ColorRole.Highlight)
            # 未选中时不铺底色：密排时相邻格子的底色会连成一片，照片边界反而看不清。
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
                # 照片落的那一块算一次（角标仍钉取景框角，见 :meth:`_draw_badge`）。
                photo = self.target_rect(image_rect, pixmap)
                self._draw_thumbnail(painter, photo, pixmap)
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

    def target_rect(self, rect: QRect, pixmap: QPixmap) -> QRect:
        """缩略图落在取景框里的哪一块（按比例缩放后居中）。

        绘制与测试共用这一份，测试量角时不必再复刻一遍魔数。用的是 :attr:`draw_edge`
        而不是几何边长 —— 那正是切档动画能动的地方。**取整**是有意的：亚像素坐标肉眼
        看不出来，却会让 ``QSize`` / ``QRect`` 的整数契约在绘制路径上到处漏小数。
        """
        edge = round(self._draw_edge)
        target = pixmap.size().scaled(QSize(edge, edge), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            rect.left() + (rect.width() - target.width()) // 2,
            rect.top() + (rect.height() - target.height()) // 2,
            target.width(),
            target.height(),
        )

    def _draw_thumbnail(self, painter: QPainter, target: QRect, pixmap: QPixmap) -> None:
        """把缩略图画进 ``target``（照片自己那一块），四角按 :attr:`THUMB_RADIUS` 裁圆。

        尺寸刚切换时手里的图还是按旧边长缩放的，这里就地缩放到新框，于是每一格始终
        有图、大小也对得上。**刻意不走** :func:`gallery.ui.imaging.scale_pixmap`：那一处
        是把缩略图缩成新位图存起来，这里是绘制时按目标矩形画，不额外产出中间位图。
        圆角用 clip 裁而不是蒙版抠 —— 后者每格多产出一张等大位图，而且抠出来还是锯齿。
        """
        clip = QPainterPath()
        clip.addRoundedRect(target, self.THUMB_RADIUS, self.THUMB_RADIUS)
        painter.save()
        try:
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            if target.size() == pixmap.size():
                painter.drawPixmap(target.topLeft(), pixmap)
            else:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(target, pixmap)
        finally:
            painter.restore()

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

    def _draw_group_badge(self, painter: QPainter, frame: QRect, count: str) -> None:
        """分组卡片：左上角盖一个张数徽标（相册盖的是「相册」两个字）。

        用封面当分组卡片的正脸 —— 封面还没到时才退回文件夹图形。
        """
        self._draw_badge(painter, frame, count)

    def _draw_album_badge(self, painter: QPainter, frame: QRect) -> None:
        """有封面的相册：左上角盖一个「相册」小标签。"""
        self._draw_badge(painter, frame, "相册")

    def _draw_badge(self, painter: QPainter, frame: QRect, text: str) -> None:
        """在**取景框**左上角盖一个小标签。

        钉取景框而不是钉照片：照片按比例居中，横竖封面的左边缘落在不同的横坐标上，
        跟着照片走一屏卡片的角标就参差不齐。代价是封面不是正方形时角标会落在照片之外
        的留白上 —— 为了整齐认下的取舍。取景框本身跟着过渡插值，所以角标照样跟着卡片
        一起挪、一起长（见 :meth:`PhotoDelegate.cell_rect`）。
        """
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 4
        badge = QRect(frame.left() + 4, frame.top() + 4, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(badge, 6, 6)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)

    def _draw_placeholder(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        """还没有缩略图时的占位：一个淡色空框，而不是空白或转圈。

        圆角跟 :attr:`THUMB_RADIUS` 一致，否则缩略图一落地那一格就换了形状。
        """
        color = palette.color(QPalette.ColorRole.WindowText)
        pen = QPen(QColor(color.red(), color.green(), color.blue(), 60))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = self.THUMB_RADIUS
        painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), radius, radius)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        del option, index
        return QSize(self._icon_edge + GRID_PADDING_X, self._icon_edge + GRID_PADDING_Y)


class PhotoGrid(QListView):
    """缩略图网格视图。"""

    activated_index = Signal(QModelIndex)
    #: 用户双击了**没有格子**的空白处：窗口据此退回上一层（见 MainWindow.go_up）。
    background_activated = Signal()
    #: 可见区域可能变了（滚动、缩放、插入新行），需要重算缩略图窗口。
    viewport_changed = Signal()
    #: 用户用捏合或 ⌘+滚轮要求换一档缩略图尺寸：正数更大，负数更小。
    size_step_requested = Signal(int)
    #: 一段切档过渡结束了 —— 跑完了，或者被用户打断（见 :attr:`is_transitioning`）；
    #: **每发出去一次过渡就正好发一次**，被 :meth:`_cancel_transition` 掐断时也发。
    transition_ended = Signal()
    #: 自适应后的实际缩略图长边变了，界面需要按新尺寸重新取一遍缩略图。
    icon_edge_changed = Signal(int)
    #: 用户再次点击了已经选中的格子：可以开始重命名了。
    rename_requested = Signal(QModelIndex)
    rename_committed = Signal(int, str)
    #: 行内重命名开始了（``True``）或收起来了（``False``）：改名的这段时间 Esc 归输入框。
    rename_active_changed = Signal(bool)
    #: 用户在网格上按了右键：**该作用于哪一行**，``-1`` 表示落在空白处（选区由视图摆正）。
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
        #: arm 是交互状态：得是用户自己指过的那一格，⌘R 进来又 Esc 出去的那格也要记。
        self._armed_row = -1
        self._double_click = False
        self._pending_rename_row = -1
        #: 「当前正处在一次行内重命名里」，与 ``_rename_edit.is_editing()`` 是两件事。
        self._rename_active = False
        self._press_point: QPoint | None = None
        self._press_extends = False
        self._press_shift = False
        self._press_selection: tuple[int, ...] = ()
        self._press_anchor = -1
        self._press_target = -1
        #: 扩选的锚点：最后一次普通点选的行（`⌘` / `⇧` 点都不挪它）。
        self._range_anchor = -1
        # 去掉 QListView 默认的 StyledPanel 边框，让照片区铺满窗口（大图区本来就是 NoFrame）。
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
        # 多选：⌘⌫ 一次把选中的若干项移进废纸篓（ADR 0008）；重命名仍是单条的（ADR 0004）。
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 原生滚动条常驻一条实心轨道，颜色由系统外观决定、与本应用统一下发的底色对不上。
        # 样式只从 :mod:`gallery.ui.scrollbar_style` 一处来 —— 周视图与元数据面板共用同一份。
        # **它比原生那档细，于是 ``_scrollbar_reserve`` 跟着变小、排版宽度多出几像素**：
        # 那是该得的（少掉的原本是照片的），但格宽一变列数就可能变、列数一变就该走切档过渡，
        # 所以钉着切档判据的用例得自己找一个「不换列」的增量，见 grid_helpers。
        apply_scrollbar_style(self.verticalScrollBar())
        self._delegate = PhotoDelegate(self, icon_edge=icon_edge)
        self.setItemDelegate(self._delegate)
        # 捏合与滚轮都是连续事件，必须攒够阈值才切一档；捏合另套一道最小间隔，否则手势快
        # 时每次切档都把上一次的动画掐断。⌘+滚轮不限速，连按由 MainWindow 排队。
        self._pinch_steps = RateLimitedAccumulator(PINCH_THRESHOLD, MIN_PINCH_INTERVAL_MS)
        self._wheel_steps = StepAccumulator(WHEEL_STEP_PIXELS)
        #: 滚动时**没走满一个像素**的那一点零头（见 :meth:`_scroll_pixels`）。
        self._wheel_remainder = 0.0

        # **只接 ``activated``**（双击与回车都发）；再接 ``doubleClicked`` 会把双击算两遍。
        self.activated.connect(self.activated_index.emit)

        self._rename_edit = RenameEdit(self.viewport())
        self._rename_edit.committed.connect(self._on_rename_committed)
        self._rename_edit.cancelled.connect(self.cancel_rename)

        # 「上一下的尾巴」：这一下属于**别处**刚做完的那次点击（见 :meth:`swallow_click`）。
        self._swallow_until = 0.0
        self._swallow_press = False

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

        # **一批插入只排一次版。** 见 :meth:`_on_rows_inserted`：监听器报一批新文件时模型会
        # 一条一条地插（每条一次 ``rowsInserted``），而这一格直接排全版的话，那一批就是
        # 「批大小」次全量排版。0 ms 单发、``start()`` 在已排着时原地续期，于是天然合并成
        # 一次 —— 与 :attr:`_debounce` 同一个手法。
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(0)
        self._relayout.timeout.connect(self._relayout_after_insert)
        #: 插入发生时用户正看着的纵向位置。**要在插入那一刻记，不能在定时器里读** ——
        #: Qt 紧接着就会从第 0 条重排一次，那时 ``bar.value()`` 已经是那个被夹小的值
        #: （400 张的库 7037 → 1642），拿它去重排就是拿错的基准去修。
        self._relayout_anchor = 0

        self._edge_timer = QTimer(self)
        self._edge_timer.setSingleShot(True)
        self._edge_timer.setInterval(EDGE_SETTLE_MS)
        self._edge_timer.timeout.connect(self._emit_edge_change)

        # 切档过渡：**一个动画、三件事**（格子、缩略图、纵向滚动），拆开就是三个时钟。
        self._transition_anim = QVariantAnimation(self)
        self._transition_anim.setStartValue(0.0)
        self._transition_anim.setEndValue(1.0)
        self._transition_anim.setEasingCurve(EDGE_ANIMATION_CURVE)
        self._transition_anim.setDuration(EDGE_ANIMATION_MS)
        self._transition_anim.valueChanged.connect(self._on_transition_step)
        self._transition_anim.finished.connect(self._settle_transition)
        self._transition: GridTransition | None = None
        #: 「真的跑起来了」才发 :attr:`transition_ended`；同步落位那一段不算。
        self._transition_running = False
        self._edge_from = 0.0
        self._edge_to = 0.0
        self._scroll_from = 0
        self._scroll_to = 0
        #: 这一趟过渡正把第几行摆到正中（``-1`` = 不是为了居中）；几何中途被改掉时靠它重算。
        self._centered_row = -1
        #: 「这一次滚动是那条过渡动画自己发起的」；判断这件事只信 :meth:`scrollContentsBy`。
        self._driving_scroll = False

        # 视口宽度变化（窗口缩放、滚动条出现或消失）都要重算格子宽度。
        self.viewport().installEventFilter(self)

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        """装上模型，并盯住「条目到齐」这个时刻。

        :meth:`_examine_columns` 量不出实际列数只可能是**条目还没到齐**（扫库是分批塞的），
        而漏掉的那次修正再也不会发生、右边就一直空着。决定格子宽度的是视口宽度而不是
        条目数，所以这里不用重排版，只需给核对补一次机会。
        换模型时**先摘掉上一个的连接**：``setModel`` 不是只调一次（切库、换浏览方式都会），
        旧模型留着连接既不能被回收，也会来叫醒一个已经不显示它的控件。
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
        """模型里多了一批条目（扫描、监听、粘贴取回…）。

        给「核对实际列数」补一次机会（见 :meth:`setModel`）；**已经滚下去过的话，
        顺手把这次插入引起的重排接管掉** —— Qt 为插入排的延迟重排每次都从第 0 条重新
        排起，滚动条范围在排完第一批那一刻只剩一小块，正看到中间的人当场被夹回顶部
        （400 张的库 6970 → 1620）。停在最顶上时（扫库的常态）不花这笔钱。

        **那一摞重排排成 0 ms 定时器，不在这里当场做。** 缺陷现场：监听器一次报一批新
        文件、模型一条一条地插，于是这里被调「批大小」次，而每一次都是一次**全量排版**
        —— 实测三万张的库里插 20 行是 477 ms（23.9 ms/行），访达丢 200 张就是几秒的
        主线程冻死，而其中绝大部分是重复劳动：那一批要排的版是同一份。定时器在已排着时
        原地续期，于是合并成一次（与 :attr:`_debounce` 同一个手法）。
        """
        # 插进来的新行没有「旧位置」可言，算不出它该从哪来 —— 过渡作废。
        self._cancel_transition()
        self._examiner.start()
        if self.verticalScrollBar().value() > 0:
            self._relayout_anchor = self.verticalScrollBar().value()
            self._relayout.start()

    def _relayout_after_insert(self) -> None:
        """一批插入排完版之后按**插入那一刻**的滚动位置重排一次（见 :attr:`_relayout_anchor`）。"""
        self._apply_geometry(self._cell_size_for(self._layout_width()), self._relayout_anchor)

    def _on_model_reset(self, *_args: object) -> None:
        """模型整体重置（换层、换排序）→ 旧的 arm 作废。

        ``_armed_row`` 记的是**行号**，而重置之后摆上来的是另一批条目；留着旧行号，
        用户在新一层里点那一格，第一下就直接改名 —— 而他这一层里什么都没指过。
        这一层随后多半会 :meth:`select_row` 把该选中的那一格摆出来，那时会重新 arm 上。
        """
        self._armed_row = -1
        self._cancel_transition()

    # ---- 尺寸档位与自适应排版 ----------------------------------------------

    @property
    def icon_edge(self) -> int:
        """实际生效的缩略图长边（随视口宽度自适应，不一定等于档位目标值）。"""
        return self._applied_edge

    @property
    def target_edge(self) -> int:
        """当前档位要求的目标长边。"""
        return self._target_edge

    @property
    def is_transitioning(self) -> bool:
        """正有一段切档过渡在跑（网格此刻画的不是逻辑几何）。

        问它的是「下一次切档能不能立刻动手」：不能的话那一下就该先排着，
        等 :attr:`transition_ended` 再来。
        """
        return self._transition_anim.state() != QAbstractAnimation.State.Stopped

    def set_target_edge(self, edge: int) -> None:
        """换一档目标尺寸。真正的格子宽度由视口宽度决定，见 :meth:`_recompute_layout`。"""
        self._target_edge = edge
        self._layout_fix.clear()
        self._recompute_layout(animate=True)

    def _columns_for(self, available: int) -> int:
        """这个宽度下打算摆几列。"""
        fix = self._layout_fix.get(available)
        if fix is not None:
            return fix[0]
        return fit_columns(available, self._target_edge + GRID_PADDING_X)

    def _cell_size_for(self, available: int) -> QSize:
        """按列数把可用宽度均分给各列，铺满一行。

        **排版宽度不是视口宽度**：Qt 换行时会先扣掉纵向滚动条，再扣掉样式的默认视图边距
        （macOS 下 16 + 1~6 像素）。直接拿视口宽去除会卡在边界上 —— 1260 // 6 = 210 正好
        是 Qt 摆不下 6 个的那一处，它只摆 5 个，右边空出整整一列。所以扣两笔：滚动条预留
        （见 :meth:`_scrollbar_reserve`）与每列 2 像素余量，剩下十几像素是本来就有的留白。
        """
        columns = self._columns_for(available)
        fix = self._layout_fix.get(available)
        slack = fix[1] if fix is not None else CELL_SLACK * columns
        usable = available - self._scrollbar_reserve() - slack
        return self._cell_size_for_width(max(1, usable // columns))

    def _scrollbar_reserve(self) -> int:
        """纵向滚动条要从排版宽度里先扣掉一条。

        **无条件扣**：Qt 排过一次版之后就把它的宽度算进去了，管它当刻显不显示
        （内容只有几行、滚动条根本没出现时，换行宽度照样窄了 18 像素）。跟着实际状态
        走等于猜它什么时候「见过世面」；代价只是没有滚动条时右边多留十几像素。
        """
        return self.verticalScrollBar().sizeHint().width()

    def _layout_width(self) -> int:
        """排版用的视口宽度：**按「滚动条在」的那一版算**，不管它此刻在不在。

        滚动条会随内容高矮自己开关，而切档恰好会把它推过临界点（缩小一档，内容就比
        视口矮了）。直接拿 ``viewport().width()`` 排版，同一个窗口会因为「滚动条恰好
        露不露头」算出不同的列数与格宽（1100 宽下小档 8 列 131 像素对 8 列 133）。
        那次重排是切档自己的后果，却会按新几何把正在跑的过渡重算一遍、当场掐掉
        （见 :meth:`_set_cell`）；按「滚动条在」算它根本不会发生。
        """
        width = self.viewport().width()
        if self.verticalScrollBar().isVisible():
            return width
        return width - self._scrollbar_reserve()

    def _cell_size_for_width(self, cell_width: int) -> QSize:
        edge = max(MIN_EDGE, cell_width - GRID_PADDING_X)
        return QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y)

    def _recompute_layout(self, *, animate: bool = False) -> None:
        """按当前视口宽度重排，让第一行铺满、右侧不留空白。

        ``animate`` 只有「用户切了一档」那条路径会传 ``True``：拖窗口是连续事件。
        这里**不**取消正在跑的过渡，连按两档要能正常接第二段（见 :meth:`_start_transition`）。

        **宽度变了就一定要走一趟 :meth:`_set_cell`，哪怕算出来的格子尺寸一模一样。**
        Qt 的 ``ResizeMode.Adjust`` 每次宽度变化都会排一次它自己的延迟重排，那次重排会
        把滚动条范围缩回一批、顺手夹掉当前位置（拖窗口时从 6970 掉到 1620 就是这么来的）。
        这一趟会把它取消掉，所以格子尺寸没变时它省下的是 Qt 那次重排，不是白跑。
        """
        if self._laying_out:
            return
        available = self._layout_width()
        if available <= 0:
            return
        if available != self._laid_out_width:
            # 修正方案是「在这个宽度下量出来的」，留着它会把别的宽度也带偏。
            self._layout_fix.clear()
        columns = self._columns_for(available)
        # 视口宽度变了就必须重排：列数是按视口宽度算的，不重排会沿用旧列数。
        if available == self._laid_out_width and columns == self._laid_out_columns:
            return
        self._set_cell(self._cell_size_for(available), animate=animate)
        self._laid_out_width = available
        self._laid_out_columns = columns
        self._examiner.start()

    def _examine_columns(self) -> None:
        """排完版之后核对一次实际列数。

        Qt 的换行宽度与视口宽度并不严格相等（宽窗口下会少摆一列），猜公式不如实测：
        真少了就按实测列数重算并留出安全边距。**修正不当成一次换档**（走
        :meth:`_apply_geometry`，列数并没变），但格子高度变了，正跑着的过渡要跟着改
        （见 :meth:`_rebuild_transition` 与 :meth:`_refresh_scroll_goal`），否则会按错的
        位置去画、那一趟滚动也会停在一个偏移了几千像素的地方。
        改完**必须把这一帧按动画自己的 ``t`` 重新摆一遍**：``set_icon_edge`` 顺手把绘制
        尺寸对齐到了新几何，对刚起头的过渡来说那是终点，不摆回去会先闪到终点再被拽回来。
        """
        available = self._layout_width()
        intended = self._laid_out_columns
        if available != self._laid_out_width or intended <= 0:
            return
        matrix = self.model()
        if matrix is None or matrix.rowCount() < intended:
            # 条目还不够摆满一行时「第一行的格子数」等于条目总数，根本量不出真实列数。
            return
        measured = self._first_row_columns()
        if measured <= 0 or measured >= intended:
            return
        if len(self._layout_fix) > MAX_LAYOUT_MEMO:
            self._layout_fix.clear()
        self._layout_fix[available] = (measured, LAYOUT_SAFETY)
        cell = self._cell_size_for(available)
        self._apply_geometry(cell, self.verticalScrollBar().value())
        self._laid_out_columns = measured
        self._rebuild_transition(cell)
        self._refresh_scroll_goal()
        if self._transition_anim.state() != QAbstractAnimation.State.Stopped:
            self._paint_transition_frame(float(self._transition_anim.currentValue()))

    def _rebuild_transition(self, cell: QSize) -> None:
        """几何变了之后，把过渡里记的那套「新布局」也换成实测的那一套。

        委托拿过渡算出来的矩形是**相对当前逻辑布局**的，逻辑布局已经按新几何排好了；
        过渡里还留着旧几何的话差得可不止一两个像素（格子高差 30 像素时第 100 格就差
        三千像素）。**旧端原样留着** —— 那是这一趟飞行的起点，与刚改完的几何无关。
        """
        if self._transition is None:
            return
        columns = self._first_row_columns()
        if columns <= 0:
            return
        self._transition = GridTransition(
            self._transition.old_columns,
            self._transition.old_cell,
            columns,
            cell,
            self._transition.scroll_shift,
        )

    def _refresh_scroll_goal(self) -> None:
        """过渡正滑向某个位置时，按新几何把那个位置重算一遍。

        ``_scroll_from == _scroll_to`` 说明这一趟只是原地重画，没有终点可修正。
        """
        if self._transition_anim.state() == QAbstractAnimation.State.Stopped:
            return
        if self._centered_row < 0 or self._scroll_to == self._scroll_from:
            return
        self._scroll_to = self._centered_scroll_value(self._centered_row, self._laid_out_columns)

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

    def _set_cell(self, cell: QSize, *, animate: bool = False) -> None:
        """换一次格子尺寸：改几何、全量重排、位置留在原处，**换了行才重新定位**。

        滚动位置在几何变化前后**一格都不动**：拖窗口、换档、核对列数都不该看见滚动条
        自己走。唯一会动的是**列数真的变了**（:attr:`GridTransition.reflows`）—— 那时
        「第 31 行」已经是另一批照片，于是把用户正看着的那一格摆到正中（见
        :meth:`_focus_row`）。换行时过渡的**起点**也要换算法（见
        :meth:`_anchored_scroll_value`），照搬旧的像素值会让第 0 帧闪到别的段落上去。
        """
        # 动画起点是「上一帧画出来」的那一档：上一次过渡可能还没跑完，从中间值接着走。
        drawn_edge = self._delegate.draw_edge
        # 三件都必须在**改几何之前**读：那次延迟重排会把滚动位置夹到已排版那一批的末尾。
        scroll_from = self.verticalScrollBar().value()
        old_columns = self._first_row_columns()
        old_cell = self.gridSize()
        anchor = self._anchor()
        self._apply_geometry(cell, scroll_from)
        new_columns = self._first_row_columns()
        scroll_shift = 0
        if old_columns > 0 and new_columns > 0 and old_columns != new_columns:
            # **只在真的换行时居中**：Qt 会顺手把当前项设成第 0 行，无条件居中会拽回顶部。
            anchored = self._anchored_scroll_value(anchor, new_columns)
            if anchored is not None:
                scroll_shift = anchored - scroll_from
                scroll_from = anchored
            self._center_on_row(self._focus_row(anchor.row), new_columns)
        self._transition = (
            GridTransition(old_columns, old_cell, new_columns, cell, scroll_shift)
            if old_columns > 0 and new_columns > 0
            else None
        )
        self._schedule_edge_update()
        self._start_transition(drawn_edge, scroll_from, animate=animate)

    def _apply_geometry(self, cell: QSize, scroll: int) -> None:
        """设定格子几何、**全量**排一遍版，再把滚动位置摆回 ``scroll``。

        ``scroll`` 由调用方在改几何**之前**读出来（见 :meth:`_set_cell`）。

        **必须全量排版（:meth:`_layout_everything`）而不是 Batched**：Batched 的滚动条
        范围只按已排过的那一批算，而每次重排都从第 0 条开始 —— 范围在排完第一批那一刻
        缩成一小块（400 张、6 列时是 1620），Qt 会在那一刻**当场把当前位置夹进去**。
        全量排版之后范围当场就是终值。代价是拖窗口时每一帧付一次全量排版（几万张也就
        几十毫秒），换来的是滚动条不再一帧一跳。
        """
        self._laying_out = True
        # 布局期间 Qt 因范围变小而夹一下当前位置是**布局的副作用**，不是「用户滚了」。
        self._driving_scroll = True
        try:
            self._applied_edge = cell.width() - GRID_PADDING_X
            self._delegate.set_icon_edge(self._applied_edge)
            self.setIconSize(QSize(self._applied_edge, self._applied_edge))
            self.setGridSize(cell)
            self._layout_everything()
        finally:
            self._laying_out = False
            self._driving_scroll = False
        self._restore_scroll(scroll)

    def _restore_scroll(self, value: int) -> None:
        """把滚动位置摆回 ``value``。

        布局每跑一次都会按新几何重算范围，而 Qt 在范围变小时当场把当前位置夹进去；
        范围在全量排版之后已经是对的，所以摆回去就是「同一处内容仍在同一处」。
        位置没动时直接返回。超出新范围时会被 ``setValue`` 夹住 —— 那是内容真的变短了。
        """
        bar = self.verticalScrollBar()
        if bar.value() == value:
            return
        self._driving_scroll = True
        try:
            bar.setValue(value)
        finally:
            self._driving_scroll = False

    def _anchor(self) -> _ScrollAnchor:
        """视口正中那一格：行号 + 它这一刻在屏幕上的纵向位置。

        **必须在改几何之前问**：改完之后「正中」已经指到另一格、格子高矮也变了。
        """
        middle = QPoint(self.viewport().width() // 2, self.viewport().height() // 2)
        index = self.indexAt(middle)
        if not index.isValid():
            return _ScrollAnchor(-1, middle.y())
        return _ScrollAnchor(index.row(), self.visualRect(index).center().y())

    def _anchored_scroll_value(self, anchor: _ScrollAnchor, columns: int) -> int | None:
        """把 ``anchor`` 那一格摆回它换几何之前所在的位置时，滚动条该在的值。

        这是过渡**第 0 帧**要落的地方。滚动位置是内容坐标，格子高矮与每行张数一变，
        同一个像素值指的就是**另一批照片** —— 照搬旧值，末尾放大一档时滚动条会一上来
        从 100% 跳到 46%（屏上正中的那一格从第 795 张变成第 366 张），然后才一路滑
        回来。缩小方向同样会闪。画面本身要与它配合（见
        :attr:`GridTransition.scroll_shift`），否则第 0 帧画出来的是「旧画面 + 新滚动
        位置」。量不出那一格（``row < 0``）时返回 ``None``，调用方退回像素值。
        """
        if anchor.row < 0 or columns <= 0:
            return None
        top = (anchor.row // columns) * self.gridSize().height()
        target = top + self.gridSize().height() // 2 - anchor.screen_y
        bar = self.verticalScrollBar()
        return max(bar.minimum(), min(bar.maximum(), target))

    def _focus_row(self, anchor_row: int) -> int:
        """换了行之后该把哪一格摆到正中。

        有真选区时是当前项，否则是换行之前视口正中那一格。**不能退回 ``currentIndex()``**：
        Qt 在模型重置与每一次重排之后都会把当前项顺手设成第 0 行，照它居中就等于
        「在相册中间放大一档 → 整屏弹回相册顶部」。
        """
        if self.genuinely_selected_rows():
            row = self.current_row()
            if row >= 0:
                return row
        if anchor_row >= 0:
            return anchor_row
        return self.current_row()

    def _layout_everything(self) -> None:
        """把**全部**条目排一遍版。每一次几何变化都用它（见 :meth:`_apply_geometry`）。

        **为什么不能继续用 Batched**：Batched 的滚动条范围只覆盖已排版的那一批，刚排完
        第一批那一刻小得离谱（切到大档后 800 张时 maximum 是 4272，正确的满范围是
        62600）。用户在相册底部时范围一变小，当前位置就被 Qt **当场夹**到 4272 ——
        整屏瞬间弹回顶部。SinglePass 同步算对且便宜：3000 张 7.9ms、30000 张 40.4ms。
        平时**滚动**仍然完全不重排，Batched 在那里才有意义，所以排完要把它设回去。
        """
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        try:
            self.doItemsLayout()
        finally:
            self.setLayoutMode(QListView.LayoutMode.Batched)

    def _centered_scroll_value(self, row: int, columns: int) -> int:
        """把第 ``row`` 行摆到正中时滚动条该在的值（夹进范围）。

        格子的位置由「``columns`` 列、每格 ``cell``」直接算出（:class:`GridTransition`
        用的是同一条算术），**与那一行有没有被排版过无关**。夹范围只为内容比视口短的
        情况；``columns`` 量不出来时退回 :attr:`_laid_out_columns`。
        """
        if columns <= 0:
            columns = max(1, self._laid_out_columns)
        cell_height = self.gridSize().height()
        top = (row // columns) * cell_height
        bar = self.verticalScrollBar()
        target = top + cell_height // 2 - self.viewport().height() // 2
        return max(bar.minimum(), min(bar.maximum(), target))

    def _center_on_row(self, row: int, columns: int) -> None:
        """把第 ``row`` 行摆到视口正中间（``columns`` 是当前这套布局实际的列数）。

        **不用 ``scrollTo(PositionAtCenter)``**：它要问 ``visualRect``，而 Batched 布局下
        超出已排版那一批的行返回的是空矩形，于是它静默什么都不做。记下 ``row``
        （:attr:`_centered_row`）供几何变动后重算终点（见 :meth:`_examine_columns`）。
        """
        self._centered_row = row
        target = self._centered_scroll_value(row, columns)
        # :meth:`_start_transition` 紧接着就拿滚动条的当前位置当终点，所以这里**必须**摆过去。
        self._scroll_to = target
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(target)
        finally:
            self._driving_scroll = False

    def _start_transition(self, drawn_edge: float, scroll_from: int, *, animate: bool) -> None:
        """发一次切档过渡（``animate`` 为假就直接落位）。

        **过渡只碰「画成什么样」，别的都一步到位**：:attr:`_applied_edge` 与 ``gridSize``
        一动，Batched 布局就得整个重排，格子飞过去靠的是委托另画一块矩形；而
        ``icon_edge_changed`` 是**重新取缩略图**的信号，跟着动画走会按几十个中间尺寸
        各要一遍图，磁盘上「一张照片只有几份缓存」的不变量当场作废；``visualRect``
        全程是终值，代价是那 160ms 里画面与逻辑不一致，由「任何交互都立刻落位」来兜。

        滚动起点要**夹进新范围**，否则插值中途会被 ``maximum()`` 削平、先平一段再动。
        """
        # 上一段还没跑完又换了一档就**不接第二段**（读状态必须在 ``stop()`` 之前）。
        still_running = self._transition_anim.state() != QAbstractAnimation.State.Stopped
        self._transition_anim.stop()
        self._edge_from = drawn_edge
        self._edge_to = float(self._applied_edge)
        bar = self.verticalScrollBar()
        self._scroll_from = min(scroll_from, bar.maximum())
        self._scroll_to = bar.value()

        reflows = self._transition is not None and self._transition.reflows
        grows = abs(self._edge_to - self._edge_from) >= EDGE_ANIMATION_MIN_DELTA
        if not animate or still_running or not (reflows or grows):
            self._settle_transition()
            return

        # 先把第 0 帧摆出来再启动：``set_icon_edge`` 已把绘制尺寸对齐到了新几何。
        self._paint_transition_frame(0.0)
        self._transition_running = True
        self._transition_anim.start()

    def _on_transition_step(self, t: float) -> None:
        """动画每一帧回调：把这一帧摆出来。"""
        self._paint_transition_frame(t)

    def _paint_transition_frame(self, t: float) -> None:
        """把第 ``t`` 帧摆出来：格子画在哪、画多大，缩略图画多大，纵向滚到哪。

        **与动画时钟分开是有意的**：几何在过渡途中被改掉时（见 :meth:`_examine_columns`），
        改几何那一步会把绘制尺寸对齐到新几何，那时要能按动画自己的 ``t`` 重摆这一帧。
        """
        self._delegate.set_draw_edge(self._edge_from + (self._edge_to - self._edge_from) * t)
        self._delegate.set_transition(self._transition, t)
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(
                round(self._scroll_from + (self._scroll_to - self._scroll_from) * t)
            )
        finally:
            self._driving_scroll = False
        self.viewport().update()

    def _settle_transition(self, *, restore_scroll: bool = True) -> None:
        """过渡收尾：画面回到「就是逻辑几何」，绘制尺寸与几何对齐。

        ``restore_scroll=False`` 是给「被用户打断」那条路用的：用户自己滚到了某处，
        就该留在那里。**要恢复滚动目标**，因为动画可能一步都没走完就被判了落位，
        那时滚动条还停在起点上。**取消也走这里** —— 格子用的还是旧布局算出来的位置，
        停在半空等于画面在说一件假话。:attr:`transition_ended` 也在这里发（**只发真正
        跑起来过的那一段**），于是「跑完」与「被掐断」是同一个出口：掐断时动画的
        ``finished`` 不会来，由 :meth:`_cancel_transition` 走到这里来补。
        """
        if restore_scroll:
            self._driving_scroll = True
            try:
                self.verticalScrollBar().setValue(self._scroll_to)
            finally:
                self._driving_scroll = False
        self._transition = None
        self._delegate.set_transition(None, 1.0)
        self._delegate.set_draw_edge(float(self._applied_edge))
        self.viewport().update()
        # **放在最后发**：被它叫醒的排队切档必须看到已经彻底落位的网格。
        if self._transition_running:
            self._transition_running = False
            self.transition_ended.emit()

    def _cancel_transition(self) -> None:
        """用户动了，或者局面已经变了 —— 过渡立刻落位。

        **按下就取消**而不是「让它跑完」：那 160ms 里画面位置与 ``visualRect`` /
        ``indexAt`` 不一致，一次点击会落在**画得好看的那一格**、却选中**逻辑上的另一格**。
        与其在命中测试上做一套反向映射，不如让那一刻的画面先归位。滚动位置**不动**。
        """
        if self._transition_anim.state() == QAbstractAnimation.State.Stopped:
            return
        # 这一趟是用户自己接手结束的：它要居中的那一行不再是目标了。
        self._centered_row = -1
        self._transition_anim.stop()
        self._settle_transition(restore_scroll=False)

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
        # 代码里指定选中同样算「一次普通点选」：接下来的 `⇧` 点应该从这一格铺开。
        self._range_anchor = row
        # 同理「再点一下就是改名」也得认它：大图页退回来、往上退一层、撤销取回的那一格。
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

        删除按这个顺序取条目，但摘行要**从后往前**（见 ``PhotoListModel.remove_rows``）
        —— 前面的行被摘掉之后，后面的行号就变了。

        **选区为空时会退回当前项**（Qt 刚打开时可能顺手把第 0 行设成当前项），这个兜底
        对「删除」「拷贝」这类命令是对的，但它**不是**「谁被选中了」的答案 —— 判可见性
        或判右键落点要问真选区，见 :meth:`genuinely_selected_rows`。
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

        **当前项要一起清**：``⌘R`` / ``⌘⌫`` 的可用性判据读的是 ``current_row``，
        留着它就会出现「菜单亮着、作用的对象在屏幕上不存在」。扩选锚点**不在这里动**
        （右键空白处还要用它，见 :meth:`reset_for_new_level`）。
        """
        self.clearSelection()
        self.setCurrentIndex(QModelIndex())

    def sync_current_to_selection(self) -> None:
        """**当前项必须落在真选区里**，否则当场把它清掉。

        界面上「选中」只有一种画法（``State_Selected``），而命令作用的对象读的是
        :meth:`current_row`；两者一旦对不上，用户就拿到一个**屏幕上没有高亮、↵ 却进得去、
        ⌘R 却改得了名**的格子（更糟的是 ⌘⌫：它会删掉一个看不见的对象）。

        Qt 自己就会造出这种状态：**左键点空白处**（清掉选区，却把当前项原封不动留着）
        与 **⌘ 点减掉最后一个选中项**。两条都在鼠标**抬起**那一步才落地，所以收口放在
        ``super().mouseReleaseEvent`` 之后；橡皮筋那条路
        （:meth:`_reassert_extended_selection`）由它自己再收一次口。右键那条路不需要 ——
        :meth:`select_for_context` 已经把选区与当前项一起摆好了。
        """
        row = self.current_row()
        model = self.model()
        selection = self.selectionModel()
        if row < 0 or model is None or selection is None:
            return
        if selection.isSelected(model.index(row, 0)):
            return
        self.setCurrentIndex(QModelIndex())

    def swallow_click(self) -> None:
        """接下来**一个**按下（双击阈值之内）不算数：它是别处那次点击的尾巴。

        **Qt 判定双击是应用级的事**（上一次按下的时间与位置记在应用里，不是控件上），
        于是「在日历上点一格 → 那一格摊成一层网格」之后紧接着的那一下会被判成网格页里
        的第二下；而那个位置多半是空白，于是「双击空白处退一层」当场把人带回日历。
        逻辑上第一下属于日历，第二下不该算作新的一下。
        """
        self._swallow_until = time.monotonic() + QApplication.doubleClickInterval() / 1000

    def _swallowing(self) -> bool:
        """这一下要不要当成「上一次点击的尾巴」吃掉（吃掉之后只这一次）。"""
        if time.monotonic() < self._swallow_until:
            self._swallow_until = 0.0
            return True
        return False

    def reset_for_new_level(self) -> None:
        """换层：这一层从零开始，网格上不留上一层的一切痕迹。

        除了 :meth:`clear_current`，还要作废**扩选锚点** —— 它记的是上一层的行号，
        留着它下一次 `⇧` 点会从一层之隔的那一格铺开，选出一段用户并没有要过的区间。
        """
        self.clear_current()
        self._range_anchor = -1

    def select_for_context(self, row: int) -> int:
        """右键之前的选区修正。返回菜单该作用于哪一行（``-1`` = 空白处）。

        ``QListView`` 默认**不会**选中光标下的项，于是「右键 A、命令却作用在原来选中的
        B 上」是 Qt 的默认行为。规则定死成三种（ADR 0014 决策 2）：未选中的那一格只选
        它；已经选中的一格**不动**（菜单作用于整份选择）；空白处清掉选区与当前项
        （与访达一致），菜单里只会有视图级命令。刻意**不挪扩选锚点** —— 那是「上一次
        完成的普通左键点选」，挪了它「右键 A → ⇧ 点 D」就会从 A 铺开。

        「已经选中」问的是**真选区**，不能用会退回当前项的 :meth:`selected_rows`：后者会把
        「Qt 顺手把第 0 行设成当前项、但谁也没选中」误判成「已经选中了」而直接返回。
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
        **顺带把这一格 arm 上**（见 :attr:`_armed_row`），这样「⌘R 进去 → 出来 → 再点它
        一下」与「连点两下」是同一件事；不 arm 的话用户就得点第二下。
        """
        self._rename_timer.stop()
        self._pending_rename_row = -1
        # 过渡进行中画面位置与 ``visualRect`` 不一致，先落位比再加一段偏移换算简单得多。
        self._cancel_transition()
        model = self.model()
        if model is None or not index.isValid():
            return False
        text = str(model.data(index, int(Qt.ItemDataRole.DisplayRole)) or "")
        if not text:
            return False
        cell = self.visualRect(index).adjusted(3, 3, -3, -3)
        rect = self._delegate.label_rect(cell)
        # 比标签高一圈。**只往上长那 3 点**：``QRect.setTop`` 自己会把高度补上（底边不动），
        # **不能再补一句 setHeight**，那句补的正是往下那半圈，输入框会挂在选中框外面。
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

        **不能靠「输入框现在还看得见吗」判断有没有东西要收尾**：按 Esc 走的是
        ``RenameEdit._cancel``，它先 ``hide()``、**再**发 ``cancelled``，等这个槽跑起来时
        输入框早就藏好了，于是「输入框收起来了」那条信号压根没发出去，菜单里 ⌘R / ⌘⌫ /
        ⇧⌘N / ⌘⏎ 永远留在禁用态。所以 session 状态由这里自己记，与输入框可见性解耦；
        守卫仍留着 —— 外部强制收起一个没在改的名字时不该广播。
        """
        if not self._rename_active:
            return
        self._rename_active = False
        self.rename_active_changed.emit(False)

    # ---- 事件 --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """记下这一下的来龙去脉（供 mouseMoveEvent / mouseReleaseEvent 判断）。"""
        # 过渡进行中画面位置与 ``indexAt`` 不一致，先落位，命中才对得上画出来的格子。
        self._cancel_transition()
        # 上一次点击的尾巴：选区照常走（用户看见格子被选中），但**不 arm 重命名**。
        self._swallow_press = self._swallowing()
        point = event.position().toPoint()
        self._press_point = point
        modifiers = event.modifiers()
        self._press_extends = bool(modifiers & _EXTEND_MODIFIERS)
        self._press_shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self._press_selection = tuple(self.selected_rows())
        index = self.indexAt(point)
        self._press_target = index.row() if index.isValid() else -1
        # 锚点是「上一次**完成**的普通点选」那一格：`⌘`/`⇧` 点都从它铺开、都不挪它。
        self._press_anchor = self._range_anchor
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """手抖不算拖动，扩选更不算。

        图标视图里 Qt 在**按下瞬间**就进入框选状态，之后鼠标一动就画橡皮筋 —— 那 10
        像素的拖动距离只管拖拽，不管它。带 `⇧` / `⌘` 点选时手总要抖上一两个像素，于是
        「从 A 到 B 的连续区间」被悄悄换成矩形框选，落在分行排的网格上就成了「每行只选
        中最边上那一两个格子」。所以带修饰键的一律当扩选（想框选就空着手拖），空着手
        也要挪够平台自己的拖动距离，才把事件交给基类。
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

        上一道只是**不让**橡皮筋画起来，这里再兜一层：即便它画了，抬起时也按用户的心意
        重算一次。带修饰键点下去时意图毫无歧义 —— `⇧` 是锚点到落点的连续区间，`⌘` 是
        加一个 / 减一个；含糊的代价是用户按 `⌘⌫` 删掉的不是他以为的那几张照片。
        落点取**按下时**那一格：缩略图还在源源不断到货，等鼠标抬起来，底下的东西已经不是
        用户刚才瞄准的那个了。
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
        # 选区是这里重算出来的（不是 Qt 那一遍），于是「当前项落在选区里」也得在这里重做一次。
        self.sync_current_to_selection()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """第一次点击只是选中；对**已经选中**的格子再点一次才开始重命名。

        第二次点击不立刻改名，而是按下双击阈值**等一等**：Qt 是在第二次「按下」时判定
        双击的，而人的两下快慢不一（按下快、抬起慢时两次抬起的间隔会超过阈值），于是
        「双击打开」会被误判成「再次点击改名」，照片没打开倒先弹了个输入框。
        所以这里等 Qt 先说话：它认成双击就去打开照片，一直没等到才是重命名。
        """
        super().mouseReleaseEvent(event)
        # 点空白处、⌘ 点减掉最后一项都会留下一个「屏幕上没高亮、命令却仍作用其上」的当前项。
        self.sync_current_to_selection()
        if self._swallow_press:
            # 尾巴：这一下只用来把格子选中，不开始「再点一下就是改名」那套。
            self._swallow_press = False
            self._armed_row = -1
            self._rename_timer.stop()
            return
        # 修饰键按下与抬起时**都**看一眼：「先按下鼠标、再补上 ⇧」也是常见手势。
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

        「双击空白处往回退」是访达里用惯了的手势，而逐层进入时它是最常用的动作之一
        （照片没铺满一屏时右下角总有一大片空白）。视图只回答「这一下落在格子还是空白」，
        要不要退、能不能退由窗口判断。

        **落点必须在交给基类之前问**：``super()`` 会双击打开，而打开相册是**同步**的 ——
        那之后再问「这一点底下还有没有格子」，问到的已经是新的一层。
        """
        if self._swallowing():
            # 双击的第二下属于**上一个页面**那次点击（见 `swallow_click`），两件都不做。
            self._swallow_press = False
            self._rename_timer.stop()
            self._pending_rename_row = -1
            self._armed_row = -1
            return
        self._rename_timer.stop()
        self._pending_rename_row = -1
        self._armed_row = -1
        self._double_click = True
        on_blank = not self.indexAt(event.position().toPoint()).isValid()
        super().mouseDoubleClickEvent(event)
        if on_blank:
            # 这一层连一格都没有时到处都是空白，正是最需要「双击退出去」的情形。
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
        「重命名为」是什么，也不该知道分组为什么删不掉。行内改名进行中**不弹菜单**：
        那时键盘与右键都归输入框（见 ADR 0014）。
        """
        if self._rename_edit.is_editing():
            event.accept()
            return
        index = self.indexAt(event.pos())
        row = self.select_for_context(index.row() if index.isValid() else -1)
        self.context_requested.emit(row)
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """⌘/Ctrl + 滚轮切换缩略图尺寸；普通滚动按像素自己走，不交给基类。

        **为什么不能交给基类**：网格的竖直滚动是 ``ScrollPerPixel``，Qt 在这一支的算法
        是「角度增量 ÷ 120 × ``wheelScrollLines()``（macOS 上 3）× **一个格子高**」，于是
        滚轮一格 = 3 × 格子高（中档 660 像素，视口才 800 高），屏上正中的照片多半被直接
        翻过去；格子再高一点那一格就够翻一整页，Qt 会索性当成翻页键。这里改成按像素走：
        滚轮一格 :data:`WHEEL_NOTCH_PIXELS`，触控板按像素增量 × :data:`WHEEL_PIXEL_FACTOR`
        （用 Qt 丢掉的 ``pixelDelta``，理由见那两个常量）。

        **仍然走 ``setValue`` 而不是直接 ``scrollContentsBy``**：这样滚动条那一跳照样
        经过 :meth:`scrollContentsBy`，行内改名输入框跟着滚、切档过渡让位、可见区去抖
        这三件都还在。
        """
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            steps = self._wheel_steps.feed(
                wheel_delta(float(event.pixelDelta().y()), float(event.angleDelta().y()))
            )
            if steps:
                self.size_step_requested.emit(1 if steps > 0 else -1)
            event.accept()
            return
        # **只取纵向**：横向滚动条是关着的，而两指斜着划时两个方向同时非零。
        pixels = self._scroll_pixels(float(event.pixelDelta().y()), float(event.angleDelta().y()))
        bar = self.verticalScrollBar()
        # 正的增量是「往上滚」，内容下移，所以值要**减**（Qt 自己那一支的符号也是这样）。
        bar.setValue(bar.value() - pixels)
        event.accept()

    def _scroll_pixels(self, pixel: float, angle: float) -> int:
        """一次普通滚动该走多少**纵向像素**（两种设备的换算见上面那两个常量）。

        **没走满一个像素的零头要留着**（:attr:`_wheel_remainder`）：触控板慢推一下只给
        1 像素的增量，乘 0.5 之后直接取整就把**每一次**都抹成 0，轻推纹丝不动而且永远
        攒不起来。向零截断（而不是四舍五入）让零头与增量**同号**，攒的永远是「同一方向
        还差的那一点」。
        """
        exact = (
            pixel * WHEEL_PIXEL_FACTOR + self._wheel_remainder
            if pixel
            else angle * WHEEL_NOTCH_PIXELS / ANGLE_UNITS_PER_NOTCH
        )
        whole = int(exact)  # 向零截断
        self._wheel_remainder = exact - whole
        return whole

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
        # **带修饰符的回车不是「打开」**（见 ADR 0014）：不可用时按下去会走到这里，宁可不做。
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            index = self.currentIndex()
            if index.isValid():
                self.activated_index.emit(index)
            return
        before = self.currentIndex()
        super().keyPressEvent(event)
        # 方向键挪动当前项时那一格在界面上就是**选中**的，所以「再点一下就是改名」也得认它；
        # 只在**真的挪动了**时才 arm，否则「刚打开时 Qt 顺手设的当前项」也会被 arm 上。
        index = self.currentIndex()
        if index.isValid() and index != before:
            self._armed_row = index.row()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - 名字是 Qt 定的
        # **输入框跟着格子滚是基类做的，这里一个字都不用加。** 曾经这里有一句「手动挪同样
        # 的距离」，理由是错的：基类滚的就是 ``viewport->scroll``，子控件跟着一起挪；那一句
        # 于是成了搬两次（先 ``-dy`` 再 ``+dy``），症状是改着名字一滚动输入框钉在原地不动。
        super().scrollContentsBy(dx, dy)
        if not self._driving_scroll:
            # 用户自己滚了：过渡立刻让位，否则动画会一边往终点走、一边被推着改终点。
            self._cancel_transition()
        # ``_debounce`` 是单次的，这里每次 ``start`` 恰好把过渡期间的重启合并成一次通知。
        self._debounce.start()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        # **这里刻意不 ``_cancel_transition()``。** 真的拖窗口会让排版宽度变，
        # :meth:`_recompute_layout` 走到 :meth:`_set_cell` 时自己就落位了。
        # 而**不是**拖窗口的那种 resize 必须放行：横幅出现/消失、切档让滚动条开关都改不动
        # 版面（:meth:`_layout_width` 只认宽度），:meth:`_recompute_layout` 直接返回。
        self._recompute_layout()
        self._debounce.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            # 延到事件循环下一轮：视口尺寸变化本身发生在布局过程中，立刻重排会把布局再推进。
            # 第三个参数（context）是**必须**的：没有它，这个 0 毫秒定时器与本对象毫无关系，
            # 「收到 resize → 同一轮里窗口被关掉」会让它去调一个已经析构的对象。
            QTimer.singleShot(0, self, self._recompute_layout)
        return super().eventFilter(watched, event)


__all__ = [
    "ANGLE_UNITS_PER_NOTCH",
    "COLUMN_CHECK_MS",
    "EDGE_ANIMATION_CURVE",
    "EDGE_ANIMATION_MIN_DELTA",
    "EDGE_ANIMATION_MS",
    "EDGE_REFRESH_THRESHOLD",
    "EDGE_SETTLE_MS",
    "GRID_PADDING_X",
    "GRID_PADDING_Y",
    "LAYOUT_SAFETY",
    "PREFETCH_MARGIN",
    "VIEWPORT_DEBOUNCE_MS",
    "WHEEL_NOTCH_PIXELS",
    "WHEEL_PIXEL_FACTOR",
    "PhotoDelegate",
    "PhotoGrid",
]
