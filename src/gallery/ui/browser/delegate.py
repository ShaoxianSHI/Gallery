"""缩略图卡片的绘制：居中照片、底部省略号文件名、相册与分组角标。

它只画，不碰几何 —— 位置与尺寸由视图按 :class:`~gallery.ui.browser.transition.GridTransition`
逐帧给（切档过渡时是插值出来的那一帧）。绘制与测试共用这里的取景算术。
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from gallery.ui.browser.model import ALBUM_ROLE, GROUP_COUNT_ROLE, GROUP_ROLE
from gallery.ui.browser.transition import GridTransition
from gallery.ui.thumb_sizes import DEFAULT_EDGE

#: 格子相对缩略图多出来的留白：横向是卡片内边距，纵向还要放下文件名。
GRID_PADDING_X = 28
GRID_PADDING_Y = 56


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
        :meth:`draw_edge` 拨回旧尺寸再往新尺寸走，所以这里顺手对齐不会丢掉动画的起点。
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
                # 照片落的那一块算一次（角标仍对齐取景框角，见 :meth:`_draw_badge`）。
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

        对齐取景框而不是照片：照片按比例居中，横竖封面的左边缘落在不同的横坐标上，跟着照片走
        一屏卡片的角标就参差不齐。代价是封面不是正方形时角标会落在照片之外的留白上。取景框
        本身跟着过渡插值，所以角标照样跟着卡片一起挪、一起长（见 :meth:`PhotoDelegate.cell_rect`）。
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


__all__ = ["GRID_PADDING_X", "GRID_PADDING_Y", "PhotoDelegate"]
