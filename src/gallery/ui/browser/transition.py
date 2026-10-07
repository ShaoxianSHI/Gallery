"""切档过渡：换一档缩略图尺寸时，每一格从旧布局飞到新布局的那一段。

它**只描述画面，不描述逻辑状态**：``gridSize`` / ``visualRect`` / ``indexAt`` 在切换那一刻
就已经是最终值，Qt 全程不知道有过过渡。单独成模块是因为这套算术（:class:`GridTransition`）
与驱动它的那几条方法是一条独立的时序链，混在网格的排版代码里很难看出「画面与逻辑何时对齐」。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QRect, QSize

#: 视口停止变化多久之后才真正通知界面重取缩略图（拖动窗口时的防抖）。
EDGE_SETTLE_MS = 120

#: 长边与上次「通知界面重取缩略图」的差值小于它就不折腾，否则整屏缩略图作废重取。
EDGE_REFRESH_THRESHOLD = 6

#: 切档时格子「长过去/缩回去」的时长（毫秒）。只驱动**画面**，不驱动几何与缩略图请求。
EDGE_ANIMATION_MS = 160

#: 切档过渡的缓动曲线：**两端都缓**的对称曲线（``OutCubic`` 第一帧就走完 27%，是一跳）。
EDGE_ANIMATION_CURVE = QEasingCurve.Type.InOutCubic

#: 边长差小于这个值就不动画：差几个像素看不出来，却白付一次全视口重绘。
EDGE_ANIMATION_MIN_DELTA = 6


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


class TransitionMixin:
    """切档过渡那一段时序：发起、逐帧摆、落位、被用户打断时立刻归位。"""

    def _rebuild_transition(self, cell: QSize) -> None:
        """几何变了之后，把过渡里记的那套「新布局」也换成刚排出来的那一套。

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


__all__ = [
    "EDGE_ANIMATION_CURVE",
    "EDGE_ANIMATION_MIN_DELTA",
    "EDGE_ANIMATION_MS",
    "EDGE_REFRESH_THRESHOLD",
    "EDGE_SETTLE_MS",
    "GridTransition",
    "TransitionMixin",
]
