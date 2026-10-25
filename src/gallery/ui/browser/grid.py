"""缩略图网格。

视图只管摆格子与回答「哪些格子可见」，解码与排队在 :mod:`gallery.ui.thumbs`。
可见区用 ``indexAt`` 采样计算，不能遍历全部行 —— 几万张时每次滚动都成 O(n)。
"""

from __future__ import annotations

import time
from typing import Final, cast

from PySide6.QtCore import (
    QAbstractAnimation,
    QAbstractItemModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QContextMenuEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QListView,
    QWidget,
)

from gallery.core.library.photos import GridEntry
from gallery.ui import theme
from gallery.ui.browser.anchoring import AnchoringMixin
from gallery.ui.browser.delegate import (
    GRID_PADDING_X,
    GRID_PADDING_Y,
    SECTION_HEIGHT,
    PhotoDelegate,
)
from gallery.ui.browser.layout import GridLayout
from gallery.ui.browser.model import ALBUM_ROLE, ENTRY_ROLE, PhotoListModel
from gallery.ui.browser.transition import (
    EDGE_ANIMATION_CURVE,
    EDGE_ANIMATION_MS,
    EDGE_SETTLE_MS,
    GridTransition,
    TransitionMixin,
)
from gallery.ui.motion import pinch_interval_ms, scaled_ms
from gallery.ui.rename_edit import RenameEdit
from gallery.ui.scrollbar_style import apply_scrollbar_style
from gallery.ui.thumb_sizes import (
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

#: 滚动后延迟多久重算可见区。避免每滚一个像素就重排一次缩略图请求。
VIEWPORT_DEBOUNCE_MS = 60

#: 预取时向视口四周外扩的比例（1.0 表示各外扩一个视口）。
PREFETCH_MARGIN = 1.0


#: Qt 的非精确滚轮**一格**是这么多角度单位（120 = 一格），与 ``thumb_sizes`` 里同一个约定。
ANGLE_UNITS_PER_NOTCH: Final = 120.0

#: 鼠标滚轮**一格**滚多少像素（Qt 的非精确滚轮一格是 120 个角度单位）。
#: Qt 自己那一档（``wheelScrollLines()`` × 格子高）中档一跳三行；240 约等于过一行。
WHEEL_NOTCH_PIXELS: Final = 240

#: 触控板两指滚动的**像素增量**乘以它：Qt 把 ``pixelDelta`` 丢掉了、只按角度增量算，
#: 而 macOS 的换算是「1 像素 = 2 个角度单位」，照角度算会把手指距离放大一倍多。
WHEEL_PIXEL_FACTOR: Final = 0.5


#: 排完版多久之后核对实际列数（等布局彻底稳定下来，避免读到半成品）。
COLUMN_CHECK_MS = 50

#: **每一列**额外让出的像素，理由见 :meth:`PhotoGrid._cell_size_for`。
CELL_SLACK = 2

#: Qt 少摆一列时，按实测列数重算所留的安全边距。
LAYOUT_SAFETY = 40

#: 「视口宽度 → 修正方案」的记忆条目上限。
MAX_LAYOUT_MEMO = 32

#: 拖动距离的回退值。正常情况取平台自己的数字（见 :func:`_drag_threshold`）。
_FALLBACK_DRAG_DISTANCE = 10


def _drag_threshold() -> int:
    """鼠标按下后挪多远才算「拖动」。

    用平台自己的数字（macOS 上是 10 像素），不硬编码常量；问不到时给一个保守的下限。
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


class PhotoGrid(TransitionMixin, AnchoringMixin, QListView):
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
    #: 用户按住 ⌥ 点了一格：打开这一格所属影片事件的网站（ADR 0063）。
    movie_open_requested = Signal(QModelIndex)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        icon_edge: int = DEFAULT_EDGE,
        animation_speed: str = "normal",
    ) -> None:
        super().__init__(parent)
        self._animation_speed = animation_speed
        self._target_edge = icon_edge
        self._applied_edge = icon_edge
        self._emitted_edge = icon_edge
        #: 这套几何下的格子尺寸。**分区式里 ``gridSize()`` 是空的**（逐条尺寸），所以内部
        #: 一律读这一份，别去问 Qt。
        self._cell = QSize(icon_edge + GRID_PADDING_X, icon_edge + GRID_PADDING_Y)
        #: 分区标题那一行的尺寸（宽度 = 排版宽度，由 :meth:`_apply_geometry` 写入）。
        self._section_size = QSize(0, SECTION_HEIGHT)
        self._laid_out_width = 0
        self._laid_out_columns = 0
        #: 上一次排版用的是哪种形态 —— 模型换了形态就得按新形态重排一次（切换分组样式）。
        self._laid_out_sectioned = False
        #: 上一次算出来的位置表与它的判据。滚动与预取每次防抖都问一遍，重算一遍
        #: 是 O(行数) —— 而「主线程上不许做随库的大小线性增长的事」。
        self._layout_cache: tuple[tuple[object, ...], GridLayout] | None = None
        self._laying_out = False
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
        # 网格自己不铺底色：整块窗口只有一处铺底，照片之间的空隙露出那一块（ADR 0096）。
        theme.mark_see_through(self)
        theme.mark_see_through(self.viewport())
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
        # guard: allow-long-comment（样式与排版判据只从 scrollbar_style 一处来，改一处要连带改判据）
        # 样式只从 :mod:`gallery.ui.scrollbar_style` 一处来 —— 周视图与元数据面板共用同一份。
        # 它比原生那档细，于是 ``_scrollbar_reserve`` 跟着变小、排版宽度多出几像素（少掉的那几
        # 像素原本是照片的）；但格宽一变列数就可能变，切档过渡的判据因此得看列数而不看边缘差。
        apply_scrollbar_style(self.verticalScrollBar())
        self._delegate = PhotoDelegate(self, icon_edge=icon_edge)
        self.setItemDelegate(self._delegate)
        # 捏合与滚轮都是连续事件，必须累积到阈值才切一档；捏合另套一道最小间隔，否则手势快
        # 时每次切档都把上一次的动画掐断。⌘+滚轮不限速，连按由 MainWindow 排队。
        self._pinch_steps = RateLimitedAccumulator(
            PINCH_THRESHOLD,
            pinch_interval_ms(EDGE_ANIMATION_MS, animation_speed, minimum=MIN_PINCH_INTERVAL_MS),
        )
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

        # guard: allow-long-comment（一批插入只排一次版：逐条排是「批大小」次全量排版）
        # **一批插入只排一次版。** 见 :meth:`_on_rows_inserted`：监听器报一批新文件时模型会一条
        # 一条地插（每条一次 ``rowsInserted``），直接排全版就是「批大小」次全量排版。0 ms 单发、
        # ``start()`` 在已排着时原地续期，于是天然合并 —— 与 :attr:`_debounce` 同一个手法。
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(0)
        self._relayout.timeout.connect(self._relayout_after_insert)
        # guard: allow-long-comment（QWidget 默认强焦点，点一下时间轴就抢走 ← / →）
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
        self._transition_anim.setDuration(self._transition_duration_ms())
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

        漏掉那次列数核对就再也补不回来、右边一直空着 —— 决定格子宽度的是视口宽度而不是条目数，
        所以这里不重排版，只给核对补一次机会。换模型时**先摘掉上一个的连接**：``setModel``
        不止调一次，旧连接既让模型回不来，也会叫醒一个已经不显示它的控件。
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
        # 装上的模型可能已经是分区式（测试与「先建模型再挂视图」两条路）：下一轮按它重排。
        QTimer.singleShot(0, self, self._sync_layout_mode)

    def _on_rows_inserted(self, *_args: object) -> None:
        """模型里多了一批条目（扫描、监听、粘贴取回…）。

        给列数核对补一次机会；已经滚下去过的话，把这次插入引起的重排接管掉 —— Qt 为插入
        排的延迟重排每次从第 0 条重排起，正看到中间的人会被当场夹回顶部。**排成 0 ms 定时器
        而不在这里当场做**：这里被调「批大小」次而每次都是全量排版，绝大部分是重复劳动。
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
        # 换分组样式（卡片式 ↔ 分区式）也是走模型重置这条路的：两种形态的排版方式不同
        # （固定网格 vs 逐条尺寸），要等这一轮重置稳定下来之后按新形态重排一次。
        QTimer.singleShot(0, self, self._sync_layout_mode)

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

    def set_animation_speed(self, speed: str) -> None:
        """换一档动画速度：下一段过渡就用新时长，正在跑的那一段不打断。"""
        self._animation_speed = speed
        self._transition_anim.setDuration(self._transition_duration_ms())
        self._pinch_steps.set_min_interval(
            pinch_interval_ms(EDGE_ANIMATION_MS, speed, minimum=MIN_PINCH_INTERVAL_MS)
        )

    def _transition_duration_ms(self) -> int:
        return scaled_ms(EDGE_ANIMATION_MS, self._animation_speed)

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

        **排版宽度不是视口宽度**：Qt 先扣纵向滚动条，再扣样式的默认边距（macOS 下 16 + 1~6
        像素）。直接除会在边界上卡住 —— 1260 // 6 = 210 正好是 Qt 摆不下 6 个的那一处，只摆 5 个。
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

        切档恰好会把滚动条推过临界点，于是同一个窗口会按「露不露头」算出不同的列数与格宽，
        而那次重排又会按新几何掐掉正在跑的过渡（见 :meth:`_set_cell`）。按「在」算就不会发生。
        """
        width = self.viewport().width()
        if self.verticalScrollBar().isVisible():
            return width
        return width - self._scrollbar_reserve()

    def _cell_size_for_width(self, cell_width: int) -> QSize:
        edge = max(MIN_EDGE, cell_width - GRID_PADDING_X)
        return QSize(edge + GRID_PADDING_X, edge + GRID_PADDING_Y)

    def _recompute_layout(self, *, animate: bool = False, force: bool = False) -> None:
        """按当前视口宽度重排，让第一行铺满、右侧不留空白。

        ``animate`` 只有「用户切了一档」那条路径会传 ``True``（拖窗口是连续事件），这里**不**取消
        正在跑的过渡。**宽度变了就一定要走一趟 :meth:`_set_cell`，哪怕格子尺寸算出来一模一样**：
        Qt 的 ``ResizeMode.Adjust`` 每次宽度变化都会排一次延迟重排，把滚动条范围缩回一批。
        ``force`` 给「格子算出来一样、但形态变了」（卡片式 ↔ 分区式）用。
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
        if (
            not force
            and available == self._laid_out_width
            and columns == self._laid_out_columns
            and self._is_sectioned() == self._laid_out_sectioned
        ):
            return
        self._set_cell(self._cell_size_for(available), animate=animate)
        self._laid_out_width = available
        self._laid_out_columns = columns
        self._laid_out_sectioned = self._is_sectioned()
        self._examiner.start()

    def _sync_layout_mode(self) -> None:
        """模型换了形态（卡片式 ↔ 分区式）之后按新形态重排一次。

        两种形态的排版方式不同：卡片式用固定网格（``gridSize``），分区式用逐条尺寸
        （标题要整行宽）。判据只有模型的 :attr:`PhotoListModel.sectioned` 一处。
        """
        if self._is_sectioned() == self._laid_out_sectioned:
            return
        self._recompute_layout(force=True)

    def _is_sectioned(self) -> bool:
        """这一层是不是分区式。模型说了算（内容与模式一起给进来，见 ``reorder``）。"""
        model = self.model()
        return isinstance(model, PhotoListModel) and model.sectioned

    def _is_header(self, row: int) -> bool:
        """这一行是不是分区标题：不可选中、不能改名、右键按空白处理。"""
        model = self.model()
        return isinstance(model, PhotoListModel) and model.is_header(row)

    def _layout(self, *, columns: int = 0, cell: QSize | None = None) -> GridLayout:
        """当前这套几何的位置表（过渡、锚点与可视区共用）。

        ``columns`` 为 0 时用排过版的列数；量不出来时退回「这个宽度下打算摆几列」。
        **按判据缓存一份**：滚动与预取每次防抖都会问它。
        """
        model = self.model()
        row_count = model.rowCount() if model is not None else 0
        header_rows: tuple[int, ...] = ()
        if isinstance(model, PhotoListModel) and model.sectioned:
            header_rows = model.header_rows()
        resolved = max(
            1, columns or self._laid_out_columns or self._columns_for(self._layout_width())
        )
        target = cell or self._cell
        key = (
            row_count,
            header_rows,
            resolved,
            target.width(),
            target.height(),
            self._section_size.width(),
            self._section_size.height(),
        )
        if self._layout_cache is not None and self._layout_cache[0] == key:
            return self._layout_cache[1]
        layout = GridLayout.of(
            row_count=row_count,
            header_rows=header_rows,
            columns=resolved,
            cell=target,
            header=self._section_size,
        )
        self._layout_cache = (key, layout)
        return layout

    def _examine_columns(self) -> None:
        """排完版之后核对一次实际列数。

        Qt 的换行宽度与视口宽度并不严格相等（宽窗口下会少摆一列），猜公式不如实测：真少了就按实测列
        数重算并留出安全边距。**修正不当成一次换档**（列数并没变），但格子高度变了，正跑着的过渡要跟
        着改。改完**必须按动画自己的 ``t`` 重摆那一帧**（``set_icon_edge`` 已把绘制尺寸对齐）。
        """
        available = self._layout_width()
        intended = self._laid_out_columns
        if available != self._laid_out_width or intended <= 0:
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

    def _refresh_scroll_goal(self) -> None:
        """过渡正滑向某个位置时，按新几何把那个位置重算一遍。

        ``_scroll_from == _scroll_to`` 说明这一趟只是原地重画，没有终点可修正。
        """
        if self._transition_anim.state() == QAbstractAnimation.State.Stopped:
            return
        if self._centered_row < 0 or self._scroll_to == self._scroll_from:
            return
        # 用排过版的那份几何（``_laid_out_columns`` 刚被修正过），不用过渡里那一份：
        # 它可能是在量出错列数时建出来的，而滚动目标必须对着真正排出来的版面。
        self._scroll_to = self._centered_scroll_value(self._centered_row, self._layout())

    def _measured_columns(self, cell: QSize, *, viewport_width: int | None = None) -> int:
        """这套几何下 Qt 实际排出来的列数 —— **由视口宽度与格子宽度直接除出来**。

        IconMode 的换行宽度就是视口宽度，而每一格的宽度都相同。**别去数量第一行有几格**：
        分区式里一行可能被段尾截断，量出来的是「这一段有几张」，不是列数（按天分组、
        一天两张时每一行都只有两张）。
        """
        width = self.viewport().width() if viewport_width is None else viewport_width
        return max(1, width // max(1, cell.width()))

    def _first_row_columns(self) -> int:
        """量一段够长的分区第一行有几格（标题行不算）；量不出来时返回 0。

        **只给列数核对用**（``_examine_columns`` 拿它与打算摆的列数比）。过渡与锚点问
        :meth:`_measured_columns`：分区式里一行可能被段尾截断，逐格数会数成段长。
        """
        matrix = self.model()
        if matrix is None or matrix.rowCount() == 0:
            return 0
        intended = max(1, self._columns_for(self._layout_width()))
        rows = matrix.rowCount()
        headers: tuple[int, ...] = ()
        if isinstance(matrix, PhotoListModel) and matrix.sectioned:
            headers = matrix.header_rows()
        segments: list[tuple[int, int]] = []
        if not headers:
            segments.append((0, rows))
        else:
            if headers[0] > 0:
                segments.append((0, headers[0]))
            for index, header in enumerate(headers):
                end = headers[index + 1] if index + 1 < len(headers) else rows
                segments.append((header + 1, end))
        for first, end in segments:
            if end - first < intended:
                continue  # 太短的一段量不出真实列数（最后一行本来就没摆满）
            top = self.visualRect(matrix.index(first, 0)).top()
            count = 0
            for row in range(first, end):
                if self.visualRect(matrix.index(row, 0)).top() != top:
                    break
                count += 1
            return count
        return 0

    def _set_cell(self, cell: QSize, *, animate: bool = False) -> None:
        """换一次格子尺寸：改几何、全量重排，**用户看着的那一段留在原处**。

        滚动位置是内容坐标：格子高矮或列数一变，同一个像素值指的是另一批照片。换档按
        **相对位置**换算（否则末尾那一档会从中间跳到末尾），有真选区时再把当前项摆到正中；
        没选中时不居中 —— 居中会把靠边的位置推向两端。第 0 帧靠 ``scroll_shift`` 补回那笔差。
        """
        # 动画起点是「上一帧画出来」的那一档：上一次过渡可能还没跑完，从中间值接着走。
        drawn_edge = self._delegate.draw_edge
        # 必须在**改几何之前**读：那次延迟重排会把滚动位置夹到已排版那一批的末尾。
        start_scroll = self.verticalScrollBar().value()
        old_maximum = self.verticalScrollBar().maximum()
        old_cell = self._cell
        old_columns = self._measured_columns(old_cell, viewport_width=self.viewport().width())
        old_layout = self._layout(columns=old_columns, cell=old_cell)
        self._apply_geometry(cell, start_scroll)
        new_columns = self._measured_columns(cell)
        new_layout = self._layout(columns=new_columns, cell=cell)
        new_maximum = self.verticalScrollBar().maximum()
        # 滚动条停在原来的相对位置：换档时照搬像素值会落到另一段内容上；只拖窗口时
        # 照旧留在同一个值。起点那一夹也要补进 ``scroll_shift``，否则第 0 帧是另一屏。
        scroll_from = (
            round(start_scroll / old_maximum * new_maximum)
            if animate and old_maximum > 0
            else min(start_scroll, new_maximum)
        )
        scroll_shift = scroll_from - start_scroll
        self._centered_row = -1
        if old_columns > 0 and new_columns > 0:
            reflows = old_columns != new_columns
            row = self._focus_row() if reflows else -1
            if row >= 0:
                self._center_on_row(row, new_layout)
            elif reflows or animate:
                self._keep_scroll(scroll_from)
        self._transition = (
            GridTransition(old_layout, new_layout, scroll_shift)
            if old_columns > 0 and new_columns > 0
            else None
        )
        self._schedule_edge_update()
        self._start_transition(drawn_edge, scroll_from, animate=animate)

    def _apply_geometry(self, cell: QSize, scroll: int) -> None:
        """设定格子几何、**全量**排一遍版，再把滚动位置摆回 ``scroll``。

        ``scroll`` 由调用方在改几何**之前**读出来。**必须全量排版而不是 Batched**：它的滚动条
        范围只按已排过的那一批算，排完第一批那一刻小得离谱，Qt 会当场把当前位置夹回去。
        **分区式不用固定网格**：标题要整行宽，只能逐条给尺寸（``gridSize`` 会让所有条目一样大）。
        """
        self._laying_out = True
        # 布局期间 Qt 因范围变小而夹一下当前位置是**布局的副作用**，不是「用户滚了」。
        self._driving_scroll = True
        try:
            self._applied_edge = cell.width() - GRID_PADDING_X
            self._cell = cell
            self._section_size = QSize(max(1, self._layout_width()), SECTION_HEIGHT)
            self._delegate.set_icon_edge(self._applied_edge)
            self._delegate.set_section_width(self._section_size.width())
            self.setIconSize(QSize(self._applied_edge, self._applied_edge))
            if self._is_sectioned():
                self.setUniformItemSizes(False)
                self.setGridSize(QSize())
            else:
                self.setUniformItemSizes(True)
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

    def _layout_everything(self) -> None:
        """把**全部**条目排一遍版。每一次几何变化都用它（见 :meth:`_apply_geometry`）。

        **为什么不能继续用 Batched**：它的滚动条范围只覆盖已排版的那一批，刚排完第一批那一刻
        小得离谱（切到大档后 800 张时 maximum 是 4272，满范围是 62600），范围一变小当前位置就被
        Qt **当场夹**回去，整屏瞬间弹回顶部。SinglePass 同步算准且便宜（3000 张 7.9 ms）。
        """
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        try:
            self.doItemsLayout()
        finally:
            self.setLayoutMode(QListView.LayoutMode.Batched)

    # ---- 可见区计算 --------------------------------------------------------

    def visible_rows(self, *, margin_ratio: float = 0.0) -> list[int]:
        """返回当前可见的行号（``margin_ratio`` 让结果向上下外扩）。

        位置来自 :class:`~gallery.ui.browser.layout.GridLayout`：分区式里标题行夹在照片行中间，
        按格高步长采样 ``indexAt`` 会逐渐漂移、整行照片漏掉；二分那份位置表与库的大小无关。
        """
        rect = self.viewport().rect()
        if rect.isEmpty():
            return []
        layout = self._layout()
        if not layout.blocks:
            return []
        bar = self.verticalScrollBar()
        pad = int(rect.height() * margin_ratio)
        return layout.rows_near(
            bar.value() + rect.top() - pad,
            bar.value() + rect.bottom() + pad,
        )

    # ---- 选中与当前行 ------------------------------------------------------

    def current_row(self) -> int:
        index = self.currentIndex()
        return index.row() if index.isValid() else -1

    def select_row(self, row: int, *, ensure_visible: bool = True) -> None:
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        if self._is_header(row):
            return  # 标题当不了当前项（模型也没让它可选）
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

        删除按这个顺序取条目，但摘行要**从后往前**（见 ``PhotoListModel.remove_rows``）。
        **选区为空时会退回当前项**，这个回退对「删除」「拷贝」这类命令是对的，但它**不是**
        「谁被选中了」的答案 —— 判可见性与右键落点要问真选区，见 :meth:`genuinely_selected_rows`。
        """
        rows = self.genuinely_selected_rows()
        if rows:
            return rows
        row = self.current_row()
        return [row] if row >= 0 else []

    def genuinely_selected_rows(self) -> list[int]:
        """**真选区**，没有当前项回退（见 :meth:`selected_rows` 末尾那段）。"""
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

    def deselect(self) -> None:
        """用户主动取消选中（``Esc`` 退不动时）：选区、当前项与扩选锚点一起清掉。

        **扩选锚点也要清**：留着它，下一次 ``⇧`` 点会从刚取消掉的那一格铺开。
        """
        self.clear_current()
        self._range_anchor = -1
        self._armed_row = -1
        self._rename_timer.stop()

    def sync_current_to_selection(self) -> None:
        """**当前项必须落在真选区里**，否则当场把它清掉。

        界面上「选中」只有一种画法（``State_Selected``），而命令作用的对象读 :meth:`current_row`；
        两者对不上，用户就拿到一个**屏幕上没有高亮、↵ 却进得去、⌘R 却改得了名**的格子（⌘⌫ 更糟：
        它会删掉一个看不见的对象）。Qt 自己就会造出这种状态：**点空白处**与 **⌘ 点减掉最后一个**。
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

        **Qt 判定双击是应用级的事**（上一次按下的时间与位置记在应用里，不是控件上），于是「在
        日历上点一格 → 那一格摊成一层网格」之后紧接着的那一下会被判成网格页里的第二下；而那个
        位置多半是空白，于是「双击空白处退一层」当场把人带回日历。
        """
        self._swallow_until = time.monotonic() + QApplication.doubleClickInterval() / 1000

    def _swallowing(self) -> bool:
        """这一下要不要当成「上一次点击的尾巴」忽略掉（忽略掉之后只这一次）。"""
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

        ``QListView`` 默认**不会**选中光标下的项，于是「右键 A、命令却作用在原来选中的 B」是 Qt
        的默认行为。规则定死成三种（ADR 0014 决策 2）：未选中的一格只选它；已选中的一格**不动**；
        空白处清掉选区与当前项。「已经选中」问的是**真选区**（不能用会退回当前项的那个）。
        """
        self._armed_row = -1
        self._rename_timer.stop()
        if row < 0:
            self.clear_current()
            return -1
        if self._is_header(row):
            # 分区标题不是可操作的一格，但它有自己的组级菜单（ADR 0089）：**不动选区与当前项**
            # —— 那份菜单作用的是标题代表的那一组，与选区无关。
            return row
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
        if self._is_header(index.row()):
            return False  # 标题没有名字可改
        # 名字取自条目本身而不是 DisplayRole：隐藏扩展名时后者是主干，拿它去改名会把扩展名弄丢。
        entry = cast(GridEntry, model.data(index, ENTRY_ROLE))
        text = entry.name
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

        **不能靠「输入框现在还看得见吗」判断有没有东西要收尾**：按 Esc 走 ``RenameEdit._cancel``，
        它先 ``hide()``、**再**发 ``cancelled``，等这个槽跑起来时输入框早就藏好了，于是那条信号
        压根没发出去，菜单里 ⌘R / ⌘⌫ 永远留在禁用态。session 状态由这里自己记，与可见性解耦。
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

        图标视图里 Qt 在**按下瞬间**就进入框选状态，之后鼠标一动就画橡皮筋 —— 那 10 像素的拖动
        距离只管拖拽，不管它。带 `⇧` / `⌘` 点选时手总要抖上一两个像素，于是「从 A 到 B 的连续
        区间」被悄悄换成矩形框选，落在分行排的网格上就成了「每行只选中最边上那一两个格子」。
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

        上一道只是**不让**橡皮筋画起来，这里再兜一层：即便它画了，抬起时也按用户的心意重算
        一次。带修饰键点下去时意图毫无歧义 —— `⇧` 是锚点到落点的连续区间，`⌘` 是加一个 /
        减一个；含糊的代价是用户按 `⌘⌫` 删掉的不是他以为的那几张照片。落点取**按下时**那一格。
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

        第二次点击不立刻改名，而是按下双击阈值**等一等**：Qt 是在第二次「按下」时判定双击的，
        而人的两下快慢不一（按下快、抬起慢时两次抬起的间隔会超过阈值），于是「双击打开」会被
        误判成「再次点击改名」。所以这里等 Qt 先说话：它认成双击就去打开照片。
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
        # 按住 ⌥ 点一格：打开它所属影片的网站（ADR 0063）。**Alt 不在扩选那组里**，
        # 所以这一下本来只是一次普通点选 —— 这里把它接管过来，且不 arm 重命名。
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
            and not self._press_extends
            and self._press_point is not None
            and (event.position().toPoint() - self._press_point).manhattanLength()
            < _drag_threshold()
        ):
            index = self.indexAt(event.position().toPoint())
            self._press_point = None
            self._press_target = -1
            self._press_shift = False
            self._armed_row = -1
            self._rename_timer.stop()
            if index.isValid():
                self.movie_open_requested.emit(index)
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

        「双击空白处往回退」是访达里用惯了的手势，而逐层进入时它最常用。视图只回答「这一下落在
        格子还是空白」，要不要退由窗口判断。**落点必须在交给基类之前问**：``super()`` 会双击打开，
        而打开相册是**同步**的，那之后再问「这一点底下还有没有格子」，问到的已经是新的一层。
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
        """⌘ + 滚轮切换缩略图尺寸；普通滚动按像素自己走，不交给基类。

        **为什么不能交给基类**：Qt 在 ``ScrollPerPixel`` 这一支的算法是「角度增量 ÷ 120 ×
        ``wheelScrollLines()``（macOS 上 3）× **一个格子高**」，于是滚轮一格 = 3 × 格子高（中档 660
        像素，视口才 800 高），正中的照片多半被直接翻过去。改成按像素走（见那两个常量）。
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
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

        **没走满一个像素的零头要留着**（:attr:`_wheel_remainder`）：触控板慢推一下只给 1 像素的
        增量，乘 0.5 之后直接取整就把**每一次**都抹成 0，轻推纹丝不动而且永远累积不起来。向零
        截断（而不是四舍五入）让零头与增量**同号**，累积的永远是「同一方向还差的那一点」。
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
        # 输入框跟着格子滚是基类做的（基类滚的就是 ``viewport->scroll``），**这里一个字都不用加**
        # —— 自己再挪一遍就成了搬两次，改着名字一滚动输入框钉在原地不动。
        super().scrollContentsBy(dx, dy)
        if not self._driving_scroll:
            # 用户自己滚了：过渡立刻让位，否则动画会一边往终点走、一边被推着改终点。
            self._cancel_transition()
        # ``_debounce`` 是单次的，这里每次 ``start`` 恰好把过渡期间的重启合并成一次通知。
        self._debounce.start()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        # guard: allow-long-comment（这里刻意不取消过渡，另一类 resize 又必须放行）
        # **这里刻意不 ``_cancel_transition()``。** 真的拖窗口会让排版宽度变，
        # :meth:`_recompute_layout` 走到 :meth:`_set_cell` 时自己就落位了。
        # 而**不是**拖窗口的那种 resize 必须放行：横幅出现/消失、切档让滚动条开关都改不动
        # 版面（:meth:`_layout_width` 只认宽度），:meth:`_recompute_layout` 直接返回。
        self._recompute_layout()
        self._debounce.start()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        # 过渡这一帧由委托自己摆；Qt 只会画最终布局落在视口里的条目，见
        # :meth:`TransitionMixin._paint_transition_viewport`。
        if event.type() == QEvent.Type.Paint and self._transition is not None:
            self._paint_transition_viewport(cast(QPaintEvent, event))
            return True
        return super().viewportEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - 名字是 Qt 定的
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            # guard: allow-long-comment（定时器必须绑住本对象，否则会去调已析构的对象）
            # 延到事件循环下一轮：视口尺寸变化本身发生在布局过程中，立刻重排会把布局再推进。
            # 第三个参数（context）是**必须**的：没有它，这个 0 毫秒定时器与本对象毫无关系，
            # 「收到 resize → 同一轮里窗口被关掉」会让它去调一个已经析构的对象。
            QTimer.singleShot(0, self, self._recompute_layout)
        return super().eventFilter(watched, event)


__all__ = [
    "ANGLE_UNITS_PER_NOTCH",
    "COLUMN_CHECK_MS",
    "LAYOUT_SAFETY",
    "PREFETCH_MARGIN",
    "VIEWPORT_DEBOUNCE_MS",
    "WHEEL_NOTCH_PIXELS",
    "WHEEL_PIXEL_FACTOR",
    "PhotoGrid",
]
