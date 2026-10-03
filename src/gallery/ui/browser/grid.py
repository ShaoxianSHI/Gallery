"""缩略图网格。

视图负责两件事：把格子摆好看，以及回答「现在哪些格子是可见的」。
解码与排队在别处（``ui.thumbnails``），所以滚动时视图只做布局，不会卡。

可见区用 ``indexAt`` 采样计算，而不是遍历全部行 —— 库里有几万张照片时，
后者会让每次滚动都变成 O(n)。
"""

from __future__ import annotations

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

#: 切档时缩略图「长过去/缩回去」的时长（毫秒）。
#:
#: 只驱动**绘制**尺寸，不驱动几何也不驱动缩略图请求 —— 理由见
#: :meth:`PhotoGrid._animate_draw_edge`。够短就不会让人觉得界面在等它，
#: 够长才看得出是动画而不是跳变。
EDGE_ANIMATION_MS = 160

#: 边长差小于这个值就不动画：差几个像素肉眼看不出来，却白付一次全视口重绘。
#:
#: 与 :data:`EDGE_REFRESH_THRESHOLD` 是两回事 —— 那个管的是「要不要重新取缩略图」
#: （代价在磁盘与解码），这个管的是「要不要重绘一帧」（代价在屏幕）。
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

    **只描述画面，不描述逻辑状态**：逻辑几何（``gridSize``、列数、``visualRect``、
    ``indexAt``）在切换那一刻就已经是最终值了，Qt 全程不知道有过过渡。过渡只决定
    「这一格画在哪儿」。

    **为什么不用逐帧重排**：Batched 布局一批只排 64 条，但显式的
    ``doItemsLayout`` 会强制**全量**排版，几万条目时单次就是几十毫秒 —— 60 帧根本
    装不下。而这套网格的全部设计（批量布局 + 按可见区取缩略图）就是为了扛大库，
    逐帧全量重排等于把它要消灭的东西又请回来。绘制时平移则是 O(可见格数)，
    一次额外的 ``QPoint`` 加法。

    **两端的列数都是量出来的，不是算出来的**（见 :meth:`PhotoGrid._first_row_columns`）。
    Qt 实际摆几列受样式边距影响，:meth:`PhotoGrid._columns_for` 给的是「打算摆几列」，
    两者隔着一次 :meth:`PhotoGrid._examine_columns` 的修正。用错的那一个，
    每格会整体漂移一点 —— 用错多少就漂多少，很难看出是哪来的。
    """

    old_columns: int
    old_cell: QSize
    new_columns: int
    new_cell: QSize

    @property
    def reflows(self) -> bool:
        """列数变了 —— 有格子从上一行挪到下一行，光缩放救不回来。

        **只看列数，不看格子大小**：格子大小差几个像素时那一列一列都在原地挪，
        肉眼根本看不出来，却要白付一整轮全视口重绘（那正是
        :data:`EDGE_ANIMATION_MIN_DELTA` 管的事，在 :meth:`PhotoGrid._start_transition`
        里与它合取）。
        """
        return self.old_columns != self.new_columns

    @staticmethod
    def _position(columns: int, cell: QSize, row: int) -> QPoint:
        """第 ``row`` 格在「``columns`` 列、每格 ``cell``」这套布局里的左上角。"""
        return QPoint((row % columns) * cell.width(), (row // columns) * cell.height())

    def offset(self, row: int, t: float) -> QPoint:
        """第 ``row`` 格相对**最终位置**要往回挪多少，才能落在第 ``t`` 帧。

        ``t=0`` 给回旧位置，``t=1`` 给 0。坐标是**内容坐标**：视图的滚动平移由 Qt
        统一叠在上面，所以这一段位移与滚动过渡天然可交换，不用互相知道对方。
        """
        old = self._position(self.old_columns, self.old_cell, row)
        new = self._position(self.new_columns, self.new_cell, row)
        k = 1.0 - t
        return QPoint(round((old.x() - new.x()) * k), round((old.y() - new.y()) * k))


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

    **照片本身有圆角**（:attr:`THUMB_RADIUS`），相册与分组卡片跟照片同形，于是三样都圆。
    """

    #: 缩略图与卡片边缘的间距。
    PADDING = 8
    #: 文件名区域高度。
    LABEL_HEIGHT = 20
    #: 选中时那层淡色底 / 描边的圆角半径。
    RADIUS = 10
    #: 缩略图自身的圆角半径。**只轻微柔化**：密排时一屏全是照片，圆角的作用是别让它们
    #: 看着像一堆直角矩形，而不是把角啃掉一块。
    #:
    #: 比 macOS 照片那个 3 pt 略大一点（``docs/调研报告-功能与外观.md`` 里的 token 表
    #: 记的是 3），仍在这个量级：再大就压过照片本身的形状了。
    #: 刻意不跟 :attr:`RADIUS` 取成同一个值 —— 10 落在照片上是另一个量级的观感，
    #: 而且那个值说的是「选中框」，与「照片」不是一回事。
    #: 占位虚线框（:meth:`_draw_placeholder`）用的是同一个值：那是同一张卡片的框在
    #: 「图还没到」时的样子，两处不圆角一致，缩略图一落地那一格就换了形状。
    THUMB_RADIUS = 5

    def __init__(self, parent: QWidget | None = None, *, icon_edge: int = DEFAULT_EDGE) -> None:
        super().__init__(parent)
        self._icon_edge = icon_edge
        #: **画出来**的边长。与 :attr:`_icon_edge` 分开存，是因为切档动画要把它取成
        #: 小数并逐帧变化，而几何尺寸必须一直是整数（见 :meth:`PhotoGrid._animate_draw_edge`）。
        #: 两者相等是常态，只有动画跑的那 160ms 里不等。
        self._draw_edge = float(icon_edge)
        #: 切档过渡：这一帧相对最终位置往回挪多少。``None`` 表示不偏移（常态）。
        self._transition: GridTransition | None = None
        self._t = 1.0

    def set_icon_edge(self, edge: int) -> None:
        """几何尺寸变了 —— 绘制尺寸先跟它对齐。

        动画是在 :meth:`PhotoGrid._set_cell` 里、在几何定下来**之后**另行发起的：
        它会把 :meth:`draw_edge` 立刻拨回旧尺寸再往新尺寸走（那里拨这一下是刻意的，
        免得中间漏出一帧新尺寸）。所以这里「顺手对齐」不会把动画的起点吃掉。
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
        """这一帧该画在哪儿（传 ``None`` 表示不偏移）。

        与 :meth:`set_draw_edge` 是同一个 ``t`` 的两半：一半决定格子画在哪儿，
        一半决定缩略图画多大。分成两个动画就会是两个时钟，早晚差一帧 ——
        那一帧里缩略图已经变了尺寸、格子却还没到位。
        """
        self._transition = transition
        self._t = t if transition is not None else 1.0

    def cell_offset(self, row: int) -> QPoint:
        """第 ``row`` 格相对 :meth:`option.rect` 的额外位移。

        没有过渡时是原点，调用方不必分情况。
        """
        if self._transition is None:
            return QPoint()
        return self._transition.offset(row, self._t)

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
            # 切档过渡：这一格暂时不画在 Qt 给的最终位置上，而是画在「旧位置与最终
            # 位置之间」的那一帧。整张卡片（底色、缩略图、角标、名字）一起挪 ——
            # 只挪缩略图的话，边框与名字会在原地等着，画面是散的。
            rect.translate(self.cell_offset(index.row()))
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

    def target_rect(self, rect: QRect, pixmap: QPixmap) -> QRect:
        """缩略图落在取景框里的哪一块（按比例缩放后居中）。

        绘制与测试共用这一份：几何只有一处算法，测试量角时不必再复刻一遍魔数
        （与 :meth:`label_rect` 同一个道理）。

        用的是 :attr:`draw_edge` 而不是几何边长 —— 这正是切档动画能动的那一处。
        **取整**是有意的：动画一帧跨好几个像素，亚像素坐标肉眼看不出来，却会让
        ``QSize`` / ``QRect`` 的整数契约在整条绘制路径上到处漏小数。
        """
        edge = round(self._draw_edge)
        target = pixmap.size().scaled(QSize(edge, edge), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            rect.left() + (rect.width() - target.width()) // 2,
            rect.top() + (rect.height() - target.height()) // 2,
            target.width(),
            target.height(),
        )

    def _draw_thumbnail(self, painter: QPainter, rect: QRect, pixmap: QPixmap) -> None:
        """把缩略图放进取景框，四个角按 :attr:`THUMB_RADIUS` 裁圆。

        尺寸刚切换时手里的图还是按旧边长缩放的，这里就地缩放到新框 —— 于是每一格
        始终有图、大小也始终对得上，用户看不到「先空框、再一张张弹回来」的过程。
        尺寸一致时走 1:1 直接绘制，不额外付平滑缩放的代价。

        **这里刻意不走 :func:`gallery.ui.imaging.scale_pixmap`。** 那两处（后台线程
        出图、模型换档位）是把缩略图**缩成一张新的位图存起来**，缩放是一次性的
        预处理；这里是在**绘制时**按目标矩形画 —— 用的是 ``QPainter`` 的
        ``SmoothPixmapTransform``，不额外产出中间位图（那才是每次重绘一张的代价）。
        两件事看起来像，实则不是同一件事，硬凑成一份只会把「缓存」和「画」搅在一起。

        **圆角用 clip 裁，不拿蒙版去抠。** 后者（先画一遍，再 ``QImage.setAlphaChannel``）
        每画一格都要额外产出一张同样大小的位图，实测 0.124 ms 对 0.029 ms；更糟的是在
        Qt 上它**还是锯齿的**（实测抠出来的四个角只有 0 与 255 两档 alpha）。``paint``
        里已经开了 ``Antialiasing``，clip 出来的边于是是渐变的，肉眼看不出锯齿。
        """
        target = self.target_rect(rect, pixmap)
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
        """还没有缩略图时的占位：一个淡色空框，而不是空白或转圈。

        圆角跟 :attr:`THUMB_RADIUS` 一致：这就是同一张卡片在「图还没到」时的样子，
        两处对不上，缩略图一落地那一格就换了形状。
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

        # 切档过渡。**一个动画、三件事**，因为它们必须是同一个 ``t``：格子画在哪儿
        # （委托侧，见 :class:`GridTransition`）、缩略图画多大、纵向滚到哪儿。
        # 拆成三个动画就是三个时钟，早晚差一帧 —— 那一帧里缩略图已经变了尺寸、
        # 格子却还在路上。
        #
        # 几何与缩略图请求仍然一步到位，理由见 :meth:`_start_transition`。
        self._transition_anim = QVariantAnimation(self)
        self._transition_anim.setStartValue(0.0)
        self._transition_anim.setEndValue(1.0)
        self._transition_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._transition_anim.setDuration(EDGE_ANIMATION_MS)
        self._transition_anim.valueChanged.connect(self._on_transition_step)
        self._transition_anim.finished.connect(self._settle_transition)
        #: 过渡进行中：旧布局的两端尺寸（内容坐标），以及要插值到的那一头。
        self._transition: GridTransition | None = None
        self._edge_from = 0.0
        self._edge_to = 0.0
        self._scroll_from = 0
        self._scroll_to = 0
        #: 这一趟过渡正把第几行摆到正中（``-1`` = 这一趟不是为了居中）。
        #: 几何在过渡途中被改掉时（见 :meth:`_examine_columns`），要靠它把终点重算一遍。
        self._centered_row = -1
        #: 「这一次滚动是那条过渡动画自己发起的」。**不是**它发起的滚动都要停掉过渡，
        #: 而判断这件事只有一个可靠的地方 —— :meth:`scrollContentsBy`，所有滚动
        #: （滚轮、方向键、拖滚动条、``EnsureVisible``）都从那里过。
        self._driving_scroll = False

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
        """模型里多了一批条目（扫描、监听、粘贴取回…）。

        两件事：

        - 给「核对实际列数」补一次机会（条目不够一行时那次核对量不出真实列数，
          见 :meth:`setModel`）。
        - **已经滚下去过的话，顺手把这次插入引起的重排接管掉。** Qt 会为插入排一次
          延迟重排，而它每次都从第 0 条重新排起 —— 滚动条范围在排完第一批的那一刻
          只剩一小块，正看到中间的人当场被夹回顶部（实测 400 张的库 6970 → 1620）。
          走一遍正常的重排路径（:meth:`_apply_geometry`）就把那次延迟重排取消掉了，
          范围一次算全、位置原样留住。停在最顶上时（扫库的常态）不花这笔钱：
          范围小一点也夹不掉谁，反正本来就该是 0。
        """
        # 插进来的新行没有「旧位置」可言：:class:`GridTransition` 算得出老行要去哪，
        # 算不出一个从来没在这套旧布局里待过的行该从哪来。所以过渡作废。
        self._cancel_transition()
        self._examiner.start()
        bar = self.verticalScrollBar()
        if bar.value() > 0:
            self._apply_geometry(self._cell_size_for(self.viewport().width()), bar.value())

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

    def _recompute_layout(self, *, animate: bool = False) -> None:
        """按当前视口宽度重排，让第一行铺满、右侧不留空白。

        ``animate`` 只有「用户切了一档」那条路径会传 ``True``：拖窗口是连续事件，
        每一次宽度变化都放一次动画等于动画永远在跑，界面反而变成一直在重绘。

        这里**不**取消正在跑的过渡：连按两档要能正常地接第二段（见
        :meth:`_start_transition`）。视口变了但格子没变的场合，
        旧布局其实一点没动，过渡照旧是对的；真的变了，:meth:`_set_cell` 自己会收尾。

        **宽度变了就一定要走一趟 :meth:`_set_cell`，哪怕算出来的格子尺寸一模一样。**
        Qt 的 ``ResizeMode.Adjust`` 每次宽度变化都会排一次它自己的延迟重排，而那次
        重排会把滚动条范围缩回一批、顺手夹掉当前位置 —— 它与我们的 ``_first_row_columns``
        之类的调用谁先谁后全看时序，夹完了没人摆回来，位置就永久地丢了（实测拖窗口时
        从 6970 掉到 1620 就是这么来的）。这一趟会把它取消掉（``doItemsLayout`` 会
        ``interruptDelayedItemsLayout``），所以格子尺寸没变时它省下的是 Qt 那次重排，
        不是白跑。
        """
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
        self._set_cell(self._cell_size_for(available), animate=animate)
        self._laid_out_width = available
        self._laid_out_columns = columns
        self._examiner.start()

    def _examine_columns(self) -> None:
        """排完版之后核对一次实际列数。

        Qt 的换行宽度与视口宽度并不严格相等（实测在宽窗口下会少摆一列），
        猜公式不如实测：真少了就按实测列数重算，并留出安全边距保证这次摆得下。

        **修正不当成一次换档**（走 :meth:`_apply_geometry` 而不是 :meth:`_set_cell`）：
        列数并没有变（Qt 本来就只摆了这么多，我们只是把一行的宽度重新均分一下），
        没有什么要重新定位的。但格子高度确实变了，所以正跑着的过渡要跟着改：
        它的「新布局」与滚动终点都是按旧格高算出来的，不重算就会按错的位置去画
        （越远的行偏得越多，屏上那几格会被画到视口外面去），那一趟滚动也会停在
        一个偏移了几千像素的地方（见 :meth:`_rebuild_transition` 与
        :meth:`_refresh_scroll_goal`）。
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
        cell = self._cell_size_for(available)
        self._apply_geometry(cell, self.verticalScrollBar().value())
        self._laid_out_columns = measured
        self._rebuild_transition(cell)
        self._refresh_scroll_goal()

    def _rebuild_transition(self, cell: QSize) -> None:
        """几何变了之后，把过渡里记的那套「新布局」也换成实测的那一套。

        过渡只描述「这一格从旧位飞到新位画在哪儿」（见 :class:`GridTransition`），
        委托拿它算出来的位移是**相对当前逻辑位置**的。逻辑位置已经按新几何排好了，
        过渡里还留着旧几何的话，差得可不止一两个像素：格子高度差 30 像素时，第 100
        行的位移就差三千像素 —— 屏上那几格会被画到视口外面去。
        """
        if self._transition is None:
            return
        columns = self._first_row_columns()
        if columns <= 0:
            return
        self._transition = GridTransition(
            self._transition.old_columns, self._transition.old_cell, columns, cell
        )

    def _refresh_scroll_goal(self) -> None:
        """过渡正滑向某个位置时，按新几何把那个位置重算一遍。

        只在「动画还在跑、而且确实在滑向某处、而且那处是一次居中」时才有事可做：
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

        滚动位置在几何变化前后**一格都不动**，这是整件事的底线：拖窗口、换档、核对
        列数都不该看见滚动条自己走。唯一会动的场合是**列数真的变了**（:attr:`GridTransition.reflows`）
        —— 那时「第 31 行」已经是另一批照片了，停在原来的像素上等于换了一张照片，
        所以把用户正看着的那一格摆到正中（见 :meth:`_focus_row`）。
        """
        # 动画起点是「上一帧画出来」的那一档，不是几何尺寸：上一次过渡可能还没跑完，
        # 那时两者不等。从中间值接着走，用户连点两档才不会看见它先倒退回去。
        drawn_edge = self._delegate.draw_edge
        # 下面三件事都必须在**改几何之前**读，而且顺序有讲究：
        # ``_first_row_columns``（走 ``visualRect``）会把 Qt 排着的那趟延迟重排当场
        # 排掉，而那次重排会把滚动位置夹到「已排版的那一批」的末尾 —— 位置要先读，
        # 读到的才是用户真正在的地方。
        scroll_from = self.verticalScrollBar().value()
        old_columns = self._first_row_columns()
        old_cell = self.gridSize()
        anchor = self._anchor_row()
        self._apply_geometry(cell, scroll_from)
        new_columns = self._first_row_columns()
        if old_columns > 0 and new_columns > 0 and old_columns != new_columns:
            # **只在真的换行时居中。** 这一条是踩过坑的：Qt 会顺手把当前项设成第 0 行，
            # 无条件按它居中就等于「每 resize 一次（**包括后台横幅出现/消失带来的自动
            # resize**）都把整屏拽回顶部」。
            self._center_on_row(self._focus_row(anchor), new_columns)
        self._transition = (
            GridTransition(old_columns, old_cell, new_columns, cell)
            if old_columns > 0 and new_columns > 0
            else None
        )
        self._schedule_edge_update()
        self._start_transition(drawn_edge, scroll_from, animate=animate)

    def _apply_geometry(self, cell: QSize, scroll: int) -> None:
        """设定格子几何、**全量**排一遍版，再把滚动位置摆回 ``scroll``。

        ``scroll`` 由调用方在改几何**之前**读出来（见 :meth:`_set_cell` 里的顺序说明）。

        **为什么必须全量排版（:meth:`_layout_everything`）而不是 Batched。**
        Batched 的滚动条范围只按「已经排过的那一批」算（``contentsSize`` 就是最后
        一条排过的条目的右下角），而每次重排都从第 0 条重新开始 —— 于是范围在排完
        第一批的那一刻缩成一小块（400 张、6 列时是 1620），而 Qt 会在范围变小的瞬间
        **当场把当前位置夹进去**。全量排版之后范围当场就是终值，夹取无从发生；这也
        正是拖窗口时滚动条不再一帧一跳的原因。

        代价要说清楚：拖窗口时每一帧都是一次全量排版（量级见
        :meth:`_layout_everything` 的实测表：几万张也就几十毫秒）。以前只在「发现
        位置被夹了」时才付这笔钱，但它付得并不少 —— 被夹几乎是每一帧的事（只要视图
        不是停在最顶上），还白搭一次 Batched 排版；换来的是滚动条不再乱跳，而不跳
        比拖得略卡要紧得多。
        """
        self._laying_out = True
        try:
            self._applied_edge = cell.width() - GRID_PADDING_X
            self._delegate.set_icon_edge(self._applied_edge)
            self.setIconSize(QSize(self._applied_edge, self._applied_edge))
            self.setGridSize(cell)
            self._layout_everything()
        finally:
            self._laying_out = False
        self._restore_scroll(scroll)

    def _restore_scroll(self, value: int) -> None:
        """把滚动位置摆回 ``value``。

        布局每跑一次都会按新几何重算范围，而 Qt 在范围变小时当场把当前位置夹进去。
        范围在全量排版之后已经是对的，所以这里摆回去就是「同一处内容仍在同一处」；
        位置没动时直接返回（绝大多数帧都是这样，一次赋值而已）。

        超出新范围时会被 ``setValue`` 夹住 —— 那是内容真的变短了（末行摆不到窗口
        中间去），该夹。
        """
        bar = self.verticalScrollBar()
        if bar.value() == value:
            return
        # 自己发起的滚动不算「用户滚了」：``scrollContentsBy`` 会拿这个标志去判断
        # 要不要叫停正在跑的过渡（见 :meth:`_on_transition_step`）。
        self._driving_scroll = True
        try:
            bar.setValue(value)
        finally:
            self._driving_scroll = False

    def _anchor_row(self) -> int:
        """视口正中那一格的行号 —— 用户这一刻在看的东西；量不出来时给 ``-1``。

        **必须在改几何之前问**：改完之后「正中」已经指到另一格了。用 ``indexAt``
        采样一个点，代价与库的大小无关。
        """
        middle = QPoint(self.viewport().width() // 2, self.viewport().height() // 2)
        index = self.indexAt(middle)
        return index.row() if index.isValid() else -1

    def _focus_row(self, anchor_row: int) -> int:
        """换了行之后该把哪一格摆到正中。

        有真选区时是当前项（选区里那个带焦点的格子，方向键与 ⇧ 扩选都照它走）；
        没有真选区时是换行之前视口正中那一格。**不能退回 ``currentIndex()``**：
        Qt 在模型重置与每一次重排之后都会把当前项顺手设成第 0 行，照着它居中，就等于
        「在相册中间放大一档 → 整屏弹回相册顶部」。``anchor_row`` 才是用户那一刻
        真正在看的东西。
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

        **为什么不能继续用 Batched。** Batched 布局的滚动条范围只覆盖已排版的那一批，
        于是刚排完第一批那一刻范围小得离谱。实测切到大档（4 列、格高 317）之后：

        ===============  ================  ====================
        条目             刚切完的 maximum   正确的满范围
        ===============  ================  ====================
        200              4272              15050
        800              4272              62600
        3000             4272              236950
        ===============  ================  ====================

        后果有两个，都很难看：

        - 用户在相册底部（实测 800 张时 value=28680），范围一变小，当前位置被 Qt
          **当场夹**到 4272 —— 整屏瞬间弹回相册顶部。用户看到的「先向上滚了很多」
          就是它。
        - 想把选中项摆到正中就得先把范围撑开，于是只好「重试几轮等它涨」。而那几轮
          0 毫秒定时器在头一两毫秒内就跑完了，而范围要 12ms（200 张）、37ms（800 张）、
          131ms（3000 张）才涨到位 —— 800 张以上一次都没等到。

        SinglePass 同步就算对，且很便宜：3000 张 7.9ms、10000 张 18.5ms、30000 张
        40.4ms，线性，约每张 1.3 微秒。它还比 Qt 自己那次**延迟**的全量排版便宜得多
        （3000 张：6.6ms 对 131ms，20 倍 —— 那个延迟路径要反复重排）。

        **为什么每一帧都付得起**：范围不准的代价不是「少滚一点」，而是「位置被当场
        夹走」—— 而夹取几乎每一帧都会发生（只要视图不是停在最顶上），所以以前那套
        「先 Batched 排一批、发现被夹了再全量补一遍」并没有省下什么，反而多付了一趟
        Batched。平时**滚动**仍然完全不重排，Batched 在那里才有意义 ——
        这也是为什么排完还要把它设回去。
        """
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        try:
            self.doItemsLayout()
        finally:
            self.setLayoutMode(QListView.LayoutMode.Batched)

    def _centered_scroll_value(self, row: int, columns: int) -> int:
        """把第 ``row`` 行摆到正中时滚动条该在的值（夹进范围）。

        格子在「``columns`` 列、每格 ``cell``」里的位置本来就是确定的
        （:class:`GridTransition` 用的是同一条算术），算出来夹进滚动条范围即可，
        **与那一行有没有被排版过无关**。

        夹范围这一步只为**内容比视口短**的情况（第一行摆到正中会算出负数、末行
        摆到正中会算出超出下界的数）。范围够不够得着不由这里操心 ——
        切档时已经 :meth:`_layout_everything` 算全了（理由见那里）。

        ``columns`` 量不出来时（条目不满一行，见 :meth:`_first_row_columns`）退回
        :attr:`_laid_out_columns` —— 那时最多只有一行，居中与否其实看不出差别。
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

        **不用 ``scrollTo(PositionAtCenter)``。** 那个方法要问 Qt 问 ``visualRect``，
        而 Batched 布局下超出已排版那一批的行 ``visualRect`` 返回
        ``QRect(-1, -1, 0, 0)``，于是 ``scrollTo`` 静默什么都不做。

        记下 ``row``（:attr:`_centered_row`）：几何要是被改掉（见
        :meth:`_examine_columns`），这一趟的终点得按新几何重算，否则就会停在
        一个按旧格高算出来的位置上。
        """
        self._centered_row = row
        target = self._centered_scroll_value(row, columns)
        # 过渡每帧都重读 ``_scroll_to``，而 :meth:`_start_transition` 紧接着就拿
        # 滚动条的当前位置当终点 —— 所以这里**必须**真的把滚动条摆过去，哪怕上一段
        # 过渡还在跑（连按两档）。下面 :meth:`_start_transition` 会判「不接第二段」
        # 而落位，落到的正是这里摆好的位置。
        self._scroll_to = target
        # 自己发起的滚动不算「用户滚了」：``scrollContentsBy`` 会拿这个标志去判断
        # 要不要叫停正在跑的过渡（见 :meth:`_on_transition_step`）。
        self._driving_scroll = True
        try:
            self.verticalScrollBar().setValue(target)
        finally:
            self._driving_scroll = False

    def _start_transition(self, drawn_edge: float, scroll_from: int, *, animate: bool) -> None:
        """发一次切档过渡（``animate`` 为假就直接落位）。

        **过渡只碰「画在哪儿」，三样东西一律一步到位，而且这是刻意的**：

        - :attr:`_applied_edge` 与 ``gridSize`` —— 它们一动，Batched 布局就得整个
          重排一次。格子从旧位飞到新位是靠委托侧平移画出来的（见 :class:`GridTransition`），
          不是靠真的重排。
        - ``icon_edge_changed`` —— 那是**重新取缩略图**的信号。若跟着动画走，
          一次切档会按几十个中间尺寸各要一遍缩略图，
          :mod:`gallery.ui.browser.thumb_sizes` 里那条「离散档位 ⇒ 一张照片在磁盘上
          只有几份缓存」的不变量当场作废。所以重取仍由 ``_schedule_edge_update``
          按新边长发一次，与过渡并行而不受它影响。
        - ``visualRect`` / ``indexAt`` —— 它们是逻辑几何，全程都是终值。代价是过渡
          那 160ms 里**画面与逻辑不完全一致**，由「任何交互都立刻落位」来兜
          （见 :meth:`_cancel_transition`）。

        滚动起点要**夹进新范围**：列数变多时内容变矮，旧位置可能已经超出新范围，
        不夹的话插值中途会被 ``maximum()`` 削平 —— 于是先平一段再动，比不动还难看。
        """
        # 上一段过渡还没跑完又换了一档（连按两下的手势）就**不接第二段**：上一段的
        # 旧布局已经不是这一段的旧布局了，拿它当起点，画面会先蹿去用户已经放弃的
        # 那一档。直接落位反而更贴近他要的结果。
        # （读状态必须在 ``stop()`` 之前 —— 停掉之后它当然永远是 ``Stopped``。）
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

        # 先把第 0 帧摆出来再启动：:meth:`PhotoDelegate.set_icon_edge` 已经把绘制
        # 尺寸对齐到了新几何，中间那一帧不该露出来（动画启动时也会发一次 t=0）。
        self._on_transition_step(0.0)
        self._transition_anim.start()

    def _on_transition_step(self, t: float) -> None:
        """一帧的 ``t``：格子画在哪儿、缩略图画多大、纵向滚到哪儿，三件事一起。"""
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
        就该留在那里，别把他拽回 ``_scroll_to``。

        **要恢复滚动目标**，因为动画可能一步都没走完就被判了落位（时长为 0、
        或者连按两档）—— 那时滚动条还停在**起点**上。不恢复的话，「关掉动画再跑
        一遍」这个 A/B 手法会把视图留在一个莫名其妙的位置（实测切档后停在 0）。

        **取消也走这里。** 中途被叫停时不该停在半空 —— 那些格子用的还是旧布局算
        出来的位置，而逻辑几何早就是终值了；停在半空等于画面在说一件假话。
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

    def _cancel_transition(self) -> None:
        """用户动了，或者局面已经变了 —— 过渡立刻落位。

        调用点是 :meth:`scrollContentsBy` 与各处鼠标/键盘入口。**按下就取消**
        而不是「让它跑完、期间点哪儿算哪儿」是有意的：那 160ms 里画面位置与
        ``visualRect`` / ``indexAt`` 不一致，一次点击会落在**画得好看的那一格**、
        却选中**逻辑上的另一格**。与其在命中测试上做一套反向映射（又是一处每帧
        都要对得上的算术），不如让那一刻的画面先归位 —— 点击落在哪就是哪，
        而 160ms 的余下部分少一帧动画没人会数。

        滚动位置**不动**：用户滚到哪儿就留在哪儿。
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
        # 下面要用 ``visualRect`` 给输入框定位，而过渡进行中画面位置与它不一致。
        # 让过渡先落位，输入框于是照着一张「它算得出来」的图摆 —— 比起再加一段
        # 偏移换算（又是一处每帧都要对得上的算术），先归位简单得多。
        self._cancel_transition()
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
        # 过渡进行中画面位置与 ``indexAt`` 不一致（理由见 :meth:`_cancel_transition`），
        # 于是这一按先让过渡落位，下面的命中才对得上画出来的那一格。
        self._cancel_transition()
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
        if not self._driving_scroll:
            # 用户自己滚了（滚轮、方向键、拖滚动条、``EnsureVisible``…）：过渡立刻让位。
            # 不这么做的话，动画会一边往终点走、一边被这些滚动推着改终点 —— 最后落到
            # 一个谁也没指定的位置上。
            self._cancel_transition()
        # ``_debounce`` 是单次的，这里每次都 ``start`` 恰好起到了合并的作用：切档过渡
        # 期间它被反复重启，于是只在整段动画结束约一帧之后才发一次 ``viewport_changed``。
        # 可见区在那 160ms 里并没有真的变多少，算一次就够。
        self._debounce.start()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        # 过渡进行中拖窗口：它的两端是按旧视口量出来的，拖到一半那两个「旧」已经不
        # 算了。落位即可 —— 接下来 :meth:`_recompute_layout` 会按新宽度重排。
        self._cancel_transition()
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
    "EDGE_ANIMATION_MIN_DELTA",
    "EDGE_ANIMATION_MS",
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
