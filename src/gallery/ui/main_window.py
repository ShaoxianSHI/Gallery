"""主窗口：界面与动作的装配在这里，各条流程住在 :mod:`gallery.ui.window` 下的 mixin 里；Qt 的覆写
必须留在 ``MainWindow`` 自己身上 —— Qt 按 MRO 找第一个实现，拆进 mixin 会有一处永远不生效。
会动用户文件的入口只在 :mod:`gallery.ui.write_ops`（红线 1）；状态集中在这个窗口对象上并显式传递。
"""

from __future__ import annotations

import datetime
import logging
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.external import launch
from gallery.core.library.config import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_CALENDAR,
    BROWSE_MODE_FLAT,
    DEFAULT_CALENDAR_LEVEL,
    DEFAULT_SORT_FACET,
    SEARCH_SCOPE_LEVEL,
    SEARCH_SCOPE_LIBRARY,
    LibraryUIState,
)
from gallery.core.library.facade import Library
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.search import SearchCondition
from gallery.core.library.session import SessionState, load_session
from gallery.core.library.watcher import LibraryChange
from gallery.core.metadata.exif import MetadataReport
from gallery.core.metadata.facets import PhotoFacets
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui import banners, desktop, theme
from gallery.ui.banner_overlay import BannerOverlay
from gallery.ui.browser.grid import PhotoGrid
from gallery.ui.browser.model import PhotoListModel
from gallery.ui.browser.thumb_sizes import (
    THUMBNAIL_SIZES,
    edge_for_index,
    label_for_index,
    nearest_index,
)
from gallery.ui.calendar import CalendarPage
from gallery.ui.external_button import (
    BUTTON_GAP,
    BUTTON_WIDTH,
    ExternalAppsButton,
    ExternalEntry,
    button_rect,
    calendar_icon,
    folder_icon,
)
from gallery.ui.navigation import (
    ENTRY_KIND_PATH,
    EntryKey,
    LevelKey,
    breadcrumb_parts,
    entry_key,
    level_key,
    path_key,
    row_of_entry_key,
)
from gallery.ui.panels.breadcrumb import BreadcrumbBar
from gallery.ui.panels.metadata import MetadataPanel
from gallery.ui.panels.welcome import WelcomeView
from gallery.ui.search_field import HEIGHT as SEARCH_FIELD_HEIGHT
from gallery.ui.search_field import MARGIN_RIGHT as SEARCH_FIELD_MARGIN_RIGHT
from gallery.ui.search_field import TEXT_ALIGN_DROP as SEARCH_FIELD_TEXT_ALIGN_DROP
from gallery.ui.search_field import TOGGLE_EDGE as SCOPE_TOGGLE_EDGE
from gallery.ui.search_field import TOGGLE_GAP as SCOPE_TOGGLE_GAP
from gallery.ui.search_field import WIDTH as SEARCH_FIELD_WIDTH
from gallery.ui.search_field import ScopeToggle, SearchField
from gallery.ui.side_buttons import SideButtonFilter
from gallery.ui.tasks import TaskRunner
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.title_bar import EXTRA_PADDING as TITLE_BAR_EXTRA
from gallery.ui.title_bar import HEIGHT as TITLE_BAR_HEIGHT
from gallery.ui.title_bar import TitleBar
from gallery.ui.viewer.detail import DetailView
from gallery.ui.watch import WatchBridge, WatchStarter, start_watch
from gallery.ui.window.banner_flow import BannerFlowMixin
from gallery.ui.window.calendar_flow import CalendarFlowMixin
from gallery.ui.window.context_menu_flow import ContextMenuFlowMixin
from gallery.ui.window.level_stack import (
    AlbumFrame,
    CalendarFrame,
    GroupFrame,
    LevelStack,
    PhotoFrame,
    RootKind,
    SearchFrame,
)
from gallery.ui.window.level_stack_mixin import LevelStackMixin
from gallery.ui.window.library_flow import LibraryFlowMixin
from gallery.ui.window.metadata_flow import MetadataFlowMixin
from gallery.ui.window.ordering import OrderingMixin
from gallery.ui.window.search_flow import SearchFlowMixin
from gallery.ui.window.thumbnail_flow import ThumbnailFlowMixin
from gallery.ui.window.viewer_flow import ViewerFlowMixin
from gallery.ui.window.watch_flow import WatchFlowMixin
from gallery.ui.write_ops import WriteOpsMixin, flattened_entries

logger = logging.getLogger(__name__)

#: 网格页**左边**留多少（点）。面包屑那一行也要知道它：条件胶囊要与搜索胶囊右对齐
#: （见 :meth:`MainWindow._layout_title_bar`）。
_GRID_PAGE_MARGIN_H = 10

#: 网格页**右边**留多少（点），比左边窄得多：这一页最右边就是那条滚动条，它自己还带着
#: :data:`~gallery.ui.scrollbar_style.HANDLE_GAP` 那 2 点，两段加起来才是滑块右缘到窗口右缘的
#: 距离。**别把它并回 :data:`_GRID_PAGE_MARGIN_H`** —— 左边那 10 点是给照片留的呼吸，
#: 右边这 2 点是给滚动条的，两件事。
_GRID_PAGE_MARGIN_RIGHT = 2


class MainWindow(
    LevelStackMixin,
    OrderingMixin,
    LibraryFlowMixin,
    SearchFlowMixin,
    WatchFlowMixin,
    ThumbnailFlowMixin,
    MetadataFlowMixin,
    ContextMenuFlowMixin,
    ViewerFlowMixin,
    BannerFlowMixin,
    CalendarFlowMixin,
    WriteOpsMixin,
    QMainWindow,
):
    # 这些 mixin 就是这个窗口的目录：每一条流程各住一间（各模块的文档说清自己管哪一块），
    # 这一份文件留装配、Qt 覆写与跨流程的那几条线。``WriteOpsMixin`` 带进来的是**会动用户
    # 文件的那些入口**（红线 1），它必须留在 ``ui/write_ops.py`` —— 守护测试按文件名找它。

    """应用主窗口。"""

    def __init__(
        self,
        *,
        session_base: Path | None = None,
        cache_base: Path | None = None,
        initial_library: Path | None = None,
        watch_starter: WatchStarter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_base = session_base
        #: 系统级缩略图缓存根目录（``None`` = 平台默认位置）；**测试必须注入它**：
        #: 否则会在真实缓存目录里留下几百个没人认领的库摘要目录。
        self._cache_base = cache_base
        self._session: SessionState = load_session(base=session_base)
        self._runner = TaskRunner(self)
        #: 外部文件变化 → 网格。回调在 watchdog 的线程上到达，必须用排队连接接。
        self._watch = WatchBridge(self, starter=watch_starter or start_watch)
        self._watch.changed.connect(self._on_watch_changed, Qt.ConnectionType.QueuedConnection)
        self._watch.failed.connect(self._on_watch_failed, Qt.ConnectionType.QueuedConnection)
        #: 自家刚写过的路径与它们的过期时刻（见 ``WATCH_ECHO_TTL``）。
        self._watch_echoes: dict[Path, float] = {}
        #: 用户触发的那条横幅要让后台消息让位到什么时候（见 BANNER_STICKY_SECONDS）。
        self._banner_sticky_until = 0.0
        #: 攒着没落地的变化（扫描在途 / 条目还在暂存区 / 人正在大图页），条件合适时补做。
        self._watch_pending: tuple[LibraryChange, ...] | None = None
        #: 分组视图里来了新照片：重新分组之后要回到哪个组（ADR 0013）。
        self._pending_group: str | None = None
        self._pending_focus: EntryKey | None = None

        self._library: Library | None = None
        self._pipeline: ThumbnailPipeline | None = None
        self._generation = 0
        self._scan_job: int | None = None
        #: 选区变化后**攒一轮**再全量重算动作可用性（0ms 单次，理由见连接处）。
        self._action_states_timer = QTimer(self)
        #: 拖窗口那一闸（见 :meth:`mousePressEvent`）：单次，一个双击周期之后还没等到第二下就真拖。
        self._title_bar_drag_timer = QTimer(self)
        self._title_bar_drag_timer.setSingleShot(True)
        self._title_bar_drag_timer.setInterval(QApplication.doubleClickInterval())
        self._title_bar_drag_timer.timeout.connect(self._start_title_bar_drag)
        self._title_bar_drag_armed = False
        self._detail_job: int | None = None
        self._current_row = -1
        self._metadata_job: int | None = None
        #: 「复制元数据」现读元数据用的那次任务（面板的读取消了就没人要那份结果）。
        self._metadata_copy_job: int | None = None
        #: **会往某个目录里落文件**的后台任务（⌘V / 复制到相册 / 移动到相册，ADR 0018、0019）：
        #: 三者同时只允许一个（会争同一个目标名字），切库时必须取消。
        self._write_job: int | None = None
        #: **在大图页上删掉的那一张**（``None`` = 没删过 / 已取回）：⌘Z 之后要跳回它，
        #: **只对紧接着的那一次撤销有效**。
        self._detail_trashed: Path | None = None
        self._place_resolver: LazyPlaceResolver | None = None
        self._place_job: int | None = None
        #: 本次运行已经起过那一遍的库（是集合不是单个：来回切库不该把同一个库再起一遍空跑）。
        self._place_pass_roots: set[Path] = set()
        self._place_notice_text: str | None = None
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]] = OrderedDict()
        self._thumbnail_failures = 0
        #: 「清空缩略图缓存」在途的那一次（O(n) 的 unlink 循环跑在工作线程上，否则冻住窗口）。
        self._cache_job: int | None = None
        # 下一次 ⌘Z 会撤掉什么；只在写操作之后刷新，否则每次翻图都要重放一遍日志。
        self._undo_label: str | None = None

        # 缩略图档位、浏览方式、分组 / 排序都是**库级配置**（ADR 0038 决策 4）：这里先给界面
        # 默认值，打开库时由 ``_apply_ui_state`` 覆盖。
        self._size_index = nearest_index(None)
        #: 「我在哪一层」—— **一条栈**（ADR 0041）。浏览方式、当前目录、分组、日历那一格及
        #: 其标志位都是从这条栈算出来的属性，见 ``LevelStackMixin``。
        self._level_stack = LevelStack(root=RootKind.FLAT)
        #: 检索条件的作用域（ADR 0040）：整库检索还是筛选当前这一层。**库级**偏好，
        #: 打开库时由 ``_apply_ui_state`` 从那个库的 ``config.toml`` 覆盖。
        self._search_scope: str = SEARCH_SCOPE_LIBRARY
        # 分组 / 排序（ADR 0009）：字段 id 存在库的 config.toml 里，下次打开这个库沿用；空值走默认。
        self._group_facet: str | None = None
        self._sort_facet: str | None = DEFAULT_SORT_FACET
        self._sort_descending: bool = False
        #: 大图页右侧的元数据面板是否展开。**同样是库级的**（ADR 0038 决策 4）。
        self._metadata_visible: bool = False
        #: 日历停在哪一层。也是库级的。
        self._calendar_level: str = DEFAULT_CALENDAR_LEVEL
        #: 旧 ``state.json`` 里的库级偏好，整个进程只读一次（``None`` = 还没读）。
        self._legacy_ui: LibraryUIState | None = None
        # 已经进入的分组（`_group_view`）不再是字段 —— 它是栈上最近一个 ``GroupFrame``。
        #: 模型里此刻摆着的是哪一层：用来分清「换了一层」（选区清空）与「还是这一层，重排一遍」。
        self._level_key: LevelKey | None = None
        #: 这一层第一次排好时该选中的一格（``None`` = 谁也不选，见 :meth:`_arm_focus`）。
        self._focus: EntryKey | None = None
        #: 上面那一格摆出来了吗。一层只摆一次，免得跟用户后来的点选抢当前项。
        self._focus_restored = False
        self._facet_values: dict[Path, PhotoFacets] = {}
        #: **上一次重排的输入** —— 用来在排序**之前**判「这次会不会变」（见
        #: :meth:`ordering.OrderingMixin._reorder_level`），少了它监听器每报一次自己写的回声
        #: 就要白付一次整层排序。``_reorder_values`` 存的是**对象本身**（不是 ``id()``），
        #: 这样它不会被回收、地址不会被复用，判 ``is`` 才靠得住。
        self._reorder_fingerprint: tuple[object, ...] | None = None
        self._reorder_values: dict[Path, PhotoFacets] | None = None
        self._reorder_photos: tuple[PhotoRef, ...] = ()
        self._reorder_albums: tuple[FolderRef, ...] = ()
        self._facet_job: int | None = None
        #: 日历页读整库拍摄时间的任务；与 :attr:`_facet_job` 分开（取消时机不同，会互相作废）。
        self._calendar_job: int | None = None
        # 从日历点开的那一层 —— `_drill_*` 三个字段是栈上 ``CalendarFrame`` 的投影（ADR 0041）。
        #: 这一层还没摆上屏的条目（顺序定下来再一次给出去，ADR 0009 决策 6）；``None`` = 已经排好。
        self._staged: list[GridEntry] | None = None
        self._level_notice = False
        self._progress_notice = False
        #: 进度那条自己写上去的话（收尾时**只收自己那一句**，见 ``_clear_progress_notice``）。
        self._progress_notice_text: str | None = None
        #: 扫描本身出过问题（跳过的目录 / 读不了的文件）。它比「这里是空的」更该说。
        self._scan_issue = False
        # ---- 搜索（ADR 0026）-------------------------------------------------
        #: 已生效的筛选条件，按录入顺序。**空列表 = 不在搜索**（普通的一层）。
        self._conditions: list[SearchCondition] = []
        #: 正在搜索框里改的是第几条条件（``None`` = 没在改）。改完回车**就地替换**它。
        self._editing_condition: int | None = None
        #: 上一次检索命中了多少张（面包屑上那个「搜索结果（128）」；``None`` = 还没查过）。
        self._search_total: int | None = None
        self._search_job: int | None = None
        #: 「按条件收窄当前这一层」那一次筛选的任务（ADR 0040）。**与检索那个是分开的两份**：
        #: 它们互不同时在途（条件非空时只走其中一条），但取消时机不同 —— 换层时两个都要作废。
        self._filter_job: int | None = None
        #: 「把整库的照片写进索引」那一遍的任务（见 ``_maybe_start_photo_index``）。
        self._index_job: int | None = None
        #: 已经起过那一遍的库。**是集合不是单个**：来回切库不该把同一个库再起一遍空跑。
        self._index_roots: set[Path] = set()
        self._index_notice_text: str | None = None
        #: 顶边那一条归不归这个窗口（``theme.unify_title_bar``）；不归就用 ``setWindowTitle``。
        self._owns_title_bar = False
        #: 不在大图页时窗口顶边那一行显示的名字（ADR 0025 决策 4）。
        self._library_title = "Gallery"
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        #: 「等上一段切档动画跑完再执行」的档数（0 = 没有排着的）；方向相反的连按会互相抵掉。
        self._pending_size_step = 0
        self._build_ui()
        self._build_actions()
        self._restore_session()
        self._sync_order_actions()
        self._metadata_action.setChecked(self._metadata_visible)
        self._panel.setVisible(self._metadata_visible)
        self._sync_size_actions()
        self._update_action_states()

        if initial_library is not None:
            self.open_library(initial_library)
        elif self._session.recent_roots and self._session.recent_roots[0].is_dir():
            # 下次启动回到上次打开的库；**开不起来就静默留在欢迎页** —— 那个库可能只是被移走了。
            self.open_library(self._session.recent_roots[0], silent=True)

    # ---- 构建界面 ----------------------------------------------------------

    def _build_ui(self) -> None:
        self.resize(1280, 860)
        self.setMinimumSize(720, 480)

        theme.ensure_installed()
        # 标题栏那一段交给窗口自己画（否则 macOS 会铺一层半透明材质，与底色差一档），**标题文字**
        # 也得自己画，否则系统那份会与 ``TitleBar`` 叠在一起（ADR 0025）。
        self._owns_title_bar = theme.unify_title_bar(self)

        self._title_bar = TitleBar(self)
        self._title_bar.rename_committed.connect(self._on_title_rename_committed)
        self._title_bar.rename_cancelled.connect(self._title_bar.stop_edit)
        # 改到一半 Esc 归输入框、会动文件的命令一起让开（与网格那个输入框同一件事，ADR 0025）。
        self._title_bar.edit_active_changed.connect(self._on_rename_active_changed)
        if self._owns_title_bar:
            # 标题文字改由 ``TitleBar`` 画，系统那份必须空着（两份叠在同一处，ADR 0025 决策 1）。
            self.setWindowTitle("")
        # 位置与「现在在哪一页」都还没定（归 ``_layout_title_bar``）；别让它在窗口建好前抢着显示。
        self._title_bar.hide()

        self._welcome = WelcomeView()
        self._welcome.open_requested.connect(self.choose_folder)
        self._welcome.recent_requested.connect(self._on_recent_requested)

        self._grid = PhotoGrid(icon_edge=edge_for_index(self._size_index))
        self._grid.setModel(self._model)
        self._grid.activated_index.connect(self._on_entry_activated)
        self._grid.background_activated.connect(self._on_background_activated)
        self._grid.viewport_changed.connect(self._refresh_thumbnail_window)
        self._grid.size_step_requested.connect(self.step_thumbnail_size)
        self._grid.transition_ended.connect(self._run_pending_size_step)
        self._grid.icon_edge_changed.connect(self._on_grid_edge_changed)
        self._grid.rename_requested.connect(self._on_rename_requested)
        self._grid.rename_committed.connect(self._on_rename_committed)
        self._grid.rename_active_changed.connect(self._on_rename_active_changed)
        self._grid.context_requested.connect(self._show_context_menu)
        # 「重命名」「移到废纸篓」能不能用，取决于当前有没有选中东西。**只接 `selectionChanged`，
        # 并且攒一轮再算**：一次算要全量重算十八枚动作，其中 ⌘V 还要同步读一次系统剪贴板。
        selection_model = self._grid.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._schedule_action_states)
        self._action_states_timer.setSingleShot(True)
        self._action_states_timer.setInterval(0)
        self._action_states_timer.timeout.connect(self._update_action_states)

        self._breadcrumb = BreadcrumbBar()
        self._breadcrumb.path_requested.connect(self._on_breadcrumb_requested)
        self._breadcrumb.condition_removed.connect(self._on_condition_removed)
        self._breadcrumb.condition_edited.connect(self._on_condition_edited)
        # 右上角要让出多宽**不在这里给死**：它随页面变，由 ``_layout_title_bar`` 算（ADR 0034）。

        # **横幅不进这个布局**（ADR 0029 决策 1）：在竖排里占一行，每来一条消息就把网格往下挤。
        self._grid_page = QWidget()
        grid_layout = QVBoxLayout(self._grid_page)
        grid_layout.setContentsMargins(_GRID_PAGE_MARGIN_H, 10, _GRID_PAGE_MARGIN_RIGHT, 0)
        grid_layout.setSpacing(8)
        grid_layout.addWidget(self._breadcrumb)
        grid_layout.addWidget(self._grid, 1)

        self._detail = DetailView()
        self._detail.request_close.connect(self.show_grid)
        self._detail.request_step.connect(self.step_detail)
        # 大图页上的右键：视图只报位置，菜单在这一侧拼（与网格同一个流程，见 ADR 0021）。
        self._detail.context_requested.connect(self._show_detail_context_menu)

        # 日历那一页**自己带着工具条**（占掉的就是网格那页面包屑那一行，ADR 0033 决策 5）。
        self._calendar_page = CalendarPage()
        self._calendar_page.cell_requested.connect(self._on_calendar_cell_requested)
        self._calendar_page.level_changed.connect(self._on_calendar_level_changed)
        # 工具条上那几枚胶囊与面包屑右端那几枚是**同一批条件**（ADR 0040 决策 9），接到**同一个**
        # 处理函数上：两处各接一次，早晚会有一处忘了改。
        self._calendar_page.condition_edited.connect(self._on_condition_edited)
        self._calendar_page.condition_removed.connect(self._on_condition_removed)
        # 换层 / 翻页 / 滚动都会改变「现在看得见哪些」，据此重新要缩略图。
        self._calendar_page.window_changed.connect(self._on_calendar_window_changed)
        self._calendar_page.set_today(datetime.date.today())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._grid_page)
        self._stack.addWidget(self._calendar_page)
        self._panel = MetadataPanel()
        self._panel.hide()
        # 「编辑元数据」那个按钮只发信号（面板不碰文件，红线 1）；写盘在 ``write_ops``。
        self._panel.edit_metadata_requested.connect(self.edit_metadata_of_current)
        # 焦点停在面板上时，← / → 仍然换照片（大图页走它自己那条路，网格上挪当前项）。
        self._panel.photo_step_requested.connect(self.step_photo_from_panel)
        self._detail_page = QWidget()
        detail_layout = QHBoxLayout(self._detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_layout.addWidget(self._detail, 1)
        detail_layout.addWidget(self._panel)
        self._stack.addWidget(self._detail_page)
        self.setCentralWidget(self._stack)
        # 内容区往下让出 :data:`TITLE_BAR_EXTRA`（初始值，每次重算是 ``_layout_title_bar`` 的事）。
        # **不是把那一段画在内容上，而是真的腾出地方**：三页的内容都从 ``安全区 + EXTRA`` 开始。
        if self._owns_title_bar:
            self._stack.setContentsMargins(0, TITLE_BAR_EXTRA, 0, 0)

        # 「用外部应用打开」按钮摆在搜索胶囊**左边**（ADR 0034），与胶囊同一手法：不进布局、摆位由
        # ``_layout_title_bar`` 算、要抬一次（Qt 按创建顺序叠兄弟控件）；**建在搜索框之前**。
        self._external_button = ExternalAppsButton(self)
        self._external_button.set_entries(self._external_entries())
        self._external_button.entry_activated.connect(self._on_external_entry)
        self._external_button.hide()

        # 搜索框**最后建**（它要压在中央控件之上）；摆位归 ``_layout_title_bar``。
        self._search_field = SearchField(self)
        self._search_field.submitted.connect(self._on_search_submitted)
        self._search_field.dismissed.connect(self._on_search_dismissed)
        self._search_field.editing_changed.connect(self._on_search_active_changed)
        self._search_field.hide()

        # 切换按钮摆在搜索胶囊**右边**（ADR 0040 决策 5）：同一个胶囊里的同一套条件语法，
        # 只改这些条件管到哪儿。不进布局 —— 与胶囊一样由 ``_layout_title_bar`` 摆，并且
        # **建在胶囊之后**才叠得上去。
        self._scope_toggle = ScopeToggle(self)
        self._scope_toggle.switched.connect(self._on_scope_switched)
        self._scope_toggle.hide()

        # 横幅**也建在最后**（Qt 按创建顺序叠兄弟控件）；差别是它**不进任何布局**。
        self._banner = BannerOverlay(self)
        self._stack.currentChanged.connect(self._on_page_changed)

        self._refresh_title()
        # 刻意不建状态栏：扫描进度与缩放百分比换不来照片区少 20 来像素；出错信息有横幅（ADR 0029）。

    def _build_actions(self) -> None:
        self._open_action = QAction("打开文件夹…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self.choose_folder)

        self._fit_action = QAction("适应窗口", self)
        self._fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self._fit_action.triggered.connect(self._detail.fit_to_window)

        self._actual_action = QAction("实际大小", self)
        self._actual_action.setShortcut(QKeySequence("Ctrl+1"))
        self._actual_action.triggered.connect(self._detail.actual_size)

        # ⌃⌘F：与 macOS 同一个键（缩放 / 铺满，ADR 0037），也是窗口被铺满之后唯一还按得到的。
        # **⌃ 在 Qt 里写作 ``Meta``**（⌘ 才写作 ``Ctrl``）；文案刷新只有 ``_sync_fill_action``。
        self._fill_action = QAction("铺满窗口", self)
        self._fill_action.setShortcut(QKeySequence("Ctrl+Meta+F"))
        self._fill_action.triggered.connect(self._toggle_filled)

        self._zoom_in_action = QAction("放大", self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(self._detail.zoom_in)

        self._zoom_out_action = QAction("缩小", self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(self._detail.zoom_out)

        # ⇧⌘↑ / ⇧⌘↓：缩略图换档；不挂 ⌘↑ / ⌘↓（被「上一级」占着），也不抢 ⌘+ / ⌘-（归大图缩放）。
        self._thumb_zoom_in_action = QAction("放大缩略图", self)
        self._thumb_zoom_in_action.setShortcut(QKeySequence("Ctrl+Shift+Up"))
        # ``triggered`` 递过来的是 checked（一个 bool），不是档数，所以要包一层。
        self._thumb_zoom_in_action.triggered.connect(
            lambda _checked=False: self.step_thumbnail_size(1)
        )

        self._thumb_zoom_out_action = QAction("缩小缩略图", self)
        self._thumb_zoom_out_action.setShortcut(QKeySequence("Ctrl+Shift+Down"))
        self._thumb_zoom_out_action.triggered.connect(
            lambda _checked=False: self.step_thumbnail_size(-1)
        )
        # **显式挂到窗口上**：QAction 只有被加进某个控件才注册进快捷键表，归属不该取决于菜单在不在。
        self.addAction(self._thumb_zoom_in_action)
        self.addAction(self._thumb_zoom_out_action)

        # 回上一层用 Esc（⌘↑ 被 macOS 占着）：**大图页与网格页共用这一条**——大图页也是栈上
        # 一帧，退掉它就是回网格（ADR 0041 决策 1），所以「返回网格」不再另立一个动作。
        # **Esc 只管位置，不管条件**（ADR 0040 决策 11）：判据里条件排在最前时，只要有条件就永远
        # 退不出那一层。撤条件挪到 ⇧Esc，两者彻底分开，绝不互相抢。
        self._up_action = QAction("上一级", self)
        self._up_action.setShortcut(QKeySequence("Esc"))
        self._up_action.triggered.connect(self.go_up)

        # 鼠标后侧键也归这一条（ADR 0043）：触发的是**同一个动作**，于是启用状态、菜单里那一条与
        # 真正干活的 go_up 只有一份。
        self._side_buttons = SideButtonFilter(self)
        self._side_buttons.back_pressed.connect(self._on_back_button)
        #: 后侧键这一下刚把改名的输入框收掉了（由 ``_on_rename_active_changed`` 记）。
        self._input_closed_by_back_button = False

        self._pop_condition_action = QAction("撤掉最后一条筛选条件", self)
        self._pop_condition_action.setShortcut(QKeySequence("Shift+Esc"))
        self._pop_condition_action.triggered.connect(self.pop_last_condition)

        self._albums_action = QAction("相册", self)
        self._albums_action.setCheckable(True)
        self._albums_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_ALBUMS))

        self._flat_action = QAction("全部照片", self)
        self._flat_action.setCheckable(True)
        self._flat_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_FLAT))

        self._calendar_action = QAction("日历", self)
        self._calendar_action.setCheckable(True)
        self._calendar_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_CALENDAR))

        self._metadata_action = QAction("显示元数据信息", self)
        self._metadata_action.setCheckable(True)
        self._metadata_action.setShortcut(QKeySequence("Ctrl+I"))
        self._metadata_action.toggled.connect(self.toggle_metadata)

        self._clear_cache_action = QAction("清空缩略图缓存", self)
        self._clear_cache_action.triggered.connect(self.clear_thumbnail_cache)

        # ⇧⌘N：与访达一致（ADR 0022）。建完立刻进行内重命名，因此**不进后台线程** —— 一次 mkdir。
        self._new_album_action = QAction("新建相册", self)
        self._new_album_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_album_action.triggered.connect(self.create_album)
        # **必须挂到窗口上**（理由见 ``_reveal_action``）；挂窗口不会让它冒到菜单栏。
        self.addAction(self._new_album_action)

        self._rename_action = QAction("重命名…", self)
        self._rename_action.setShortcut(QKeySequence("Ctrl+R"))
        self._rename_action.triggered.connect(self.rename_current_entry)

        # ⌘⌫：与访达一致，不弹确认框（ADR 0008）。``StandardKey.Delete`` 指的是前向删除键（⌘⌦）。
        self._trash_action = QAction("移到废纸篓", self)
        self._trash_action.setShortcut(QKeySequence("Ctrl+Backspace"))
        self._trash_action.triggered.connect(self.trash_selected)

        # ⌘Z 撤的是这一摞的栈顶（``core.metadata.undo``），文案跟着栈顶变；它住在「编辑」菜单里。
        self._undo_action = QAction("撤销", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.triggered.connect(self.undo_last_write)

        # ---- 菜单栏与快捷键（见 ADR 0014）--------------------------------
        # 「打开」刻意不给**真**快捷键：回车归 ``PhotoGrid``；菜单里那枚「回车」只画不生效。
        self._open_entry_action = QAction("打开", self)
        self._open_entry_action.triggered.connect(self.open_current_entry)

        # 「退出」只活在**大图页的右键菜单**里（ADR 0021），也不给真快捷键。
        self._close_entry_action = QAction("退出", self)
        self._close_entry_action.triggered.connect(self.show_grid)

        # ⇧⏎ 归「在默认应用中打开」；它只活在右键菜单里（菜单栏上放没有选区可依的命令没有意义）。
        self._open_default_action = QAction("在默认应用中打开", self)
        self._open_default_action.setShortcut(QKeySequence("Shift+Return"))
        self._open_default_action.triggered.connect(self.open_current_in_default_app)
        # 同样**必须挂到窗口上**才按得出来（理由见 ``_reveal_action`` 那段）。
        self.addAction(self._open_default_action)

        # ⌘⏎：⏎ 归「打开」自己，加 ⌘ 正好空出来一条；这条**只作用在网格里**。
        self._reveal_action = QAction("在访达中显示", self)
        self._reveal_action.setShortcut(QKeySequence("Ctrl+Return"))
        self._reveal_action.triggered.connect(self.reveal_selection)
        # **必须挂到窗口上**（``QWidget.addAction``）才按得出来：快捷键只有被加进某个控件才注册。
        self.addAction(self._reveal_action)

        # ⌘C 与访达一致：剪贴板里放的是**文件本身**（ADR 0017）；是路径引用，不读像素、不写文件。
        self._copy_action = QAction("拷贝", self)
        self._copy_action.setShortcut(QKeySequence("Ctrl+C"))
        self._copy_action.triggered.connect(self.copy_selected_files)

        # ⇧⌘C 与访达一致。注意 Qt 在 macOS 上把 Command 写作 Ctrl，不要手动换键。
        self._copy_path_action = QAction("拷贝路径", self)
        self._copy_path_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_path_action.triggered.connect(self.copy_selected_paths)

        # ⌘V 与访达一致：把剪贴板里的文件**复制**进当前这一层（ADR 0018），是这几个写操作里唯一
        # 不进撤销栈的（撤销粘贴就是删文件，红线 1）；**相册菜单里那一条也是它**。
        self._paste_action = QAction("粘贴", self)
        self._paste_action.setShortcut(QKeySequence("Ctrl+V"))
        self._paste_action.triggered.connect(self.paste_into_current_folder)
        # 剪贴板一变就重算 ⌘V（在访达里 ⌘C 之后切回来要能直接粘）；连接随窗口析构自动摘掉。
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

        self._select_all_action = QAction("全选", self)
        self._select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        self._select_all_action.triggered.connect(self.select_all_entries)

        # 只活在右键菜单里的两条：它们离不开「当前这一张」，放进菜单栏反而没处可用。
        self._copy_metadata_action = QAction("复制元数据", self)
        self._copy_metadata_action.triggered.connect(self.copy_current_metadata)

        # 「编辑元数据…」⇧⌘I（ADR 0030 / 0032）：本项目里第一个改动**照片内容**的命令，只对单张
        # 照片成立、只活在右键菜单里，但它要一个真快捷键 —— 访达的照片「简介」也是 ⌘I。
        self._edit_metadata_action = QAction("编辑元数据…", self)
        self._edit_metadata_action.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self._edit_metadata_action.triggered.connect(self.edit_metadata_of_current)
        self.addAction(self._edit_metadata_action)

        self._copy_group_action = QAction("复制分组条件", self)
        self._copy_group_action.triggered.connect(self.copy_current_group)

        # 「设为相册封面」/「取消相册封面」（ADR 0028）：只活在右键菜单里，**刻意不给真快捷键**。
        self._set_cover_action = QAction("设为相册封面", self)
        self._set_cover_action.triggered.connect(self.set_selected_as_album_cover)

        self._clear_cover_action = QAction("取消相册封面", self)
        self._clear_cover_action.triggered.connect(self.clear_album_cover)

        # ⌘W 与 ⌘Q 是**两件事**（ADR 0037）：⌘W 关掉这一个窗口（应用还活着），⌘Q 才是退出。
        # **刻意不写 ``StandardKey.Close``**：Qt 在 macOS 上把它解析成 ⌘F4，而用户按的是 ⌘W。
        self._close_action = QAction("关闭窗口", self)
        self._close_action.setShortcut(QKeySequence("Ctrl+W"))
        self._close_action.triggered.connect(self.close)

        # ⌘Q：退出。**刻意不写 ``StandardKey.Quit``** —— Qt 把它解析成空序列（``count() == 0``）。
        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self._quit_action.triggered.connect(self.quit_app)

        # ⌘F 与访达一致：不是弹对话框，是**把光标放进标题栏那个胶囊**（检索那一套全在窗口顶上）。
        self._search_action = QAction("搜索…", self)
        self._search_action.setShortcut(QKeySequence("Ctrl+F"))
        self._search_action.triggered.connect(self.focus_search)
        self.addAction(self._search_action)

        self._clear_search_action = QAction("清除搜索条件", self)
        self._clear_search_action.triggered.connect(self.clear_search_conditions)

        # 「筛选当前视图」：切换按钮在菜单里的那条路（ADR 0040 决策 5）。**与按钮走同一个入口**，
        # 所以两处永远说同一句话。按钮自己不再摆状态（ADR 0042 决策 1），**这一项的勾是菜单上
        # 唯一的「此刻是哪一头」** —— 与胶囊里的占位文字一起，那两处必须一直是准的。
        self._scope_action = QAction("筛选当前视图", self)
        self._scope_action.setCheckable(True)
        # **``toggled`` 递过来的是 ``checked``（一个 bool），不是作用域那个字符串** —— 直接接
        # ``set_search_scope`` 会让一个非法的值走到它开头那道校验里，于是按了没反应。
        self._scope_action.toggled.connect(
            lambda checked=False: self.set_search_scope(
                SEARCH_SCOPE_LEVEL if checked else SEARCH_SCOPE_LIBRARY
            )
        )

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        file_menu.addAction(self._search_action)
        file_menu.addAction(self._scope_action)
        # 没有条件时**整条不出现**（``QAction.setVisible`` 就是「不进菜单」，ADR 0014 决策 4）。
        self._clear_search_action.setVisible(False)
        file_menu.addAction(self._clear_search_action)
        file_menu.addSeparator()
        file_menu.addAction(self._new_album_action)
        file_menu.addAction(self._rename_action)
        file_menu.addAction(self._trash_action)
        file_menu.addSeparator()
        file_menu.addAction(self._clear_cache_action)
        file_menu.addSeparator()
        # 「关闭窗口」与「退出」挨着摆（macOS 上同一族）：一个收窗口，一个把应用带走（ADR 0037）。
        file_menu.addAction(self._close_action)
        file_menu.addAction(self._quit_action)

        # 「编辑」：全选、拷贝、拷贝路径、粘贴、撤销（ADR 0014 决策 5）；⌘Z 从「文件」搬过来。
        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self._select_all_action)
        edit_menu.addAction(self._copy_action)
        edit_menu.addAction(self._copy_path_action)
        edit_menu.addAction(self._paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self._undo_action)

        view_menu = self.menuBar().addMenu("视图")
        # **「上一级」一条管两处**（大图页 / 网格页），所以视图菜单里没有第二条同键的命令。
        view_menu.addAction(self._up_action)
        # 挨着「上一级」：两者是同一族的「往回退」，但一个退位置、一个退条件（ADR 0040 决策 11）。
        view_menu.addAction(self._pop_condition_action)
        view_menu.addSeparator()
        browse_menu = view_menu.addMenu("浏览方式")
        browse_menu.addAction(self._flat_action)
        browse_menu.addAction(self._albums_action)
        browse_menu.addAction(self._calendar_action)
        self._build_order_menus(view_menu)
        view_menu.addSeparator()
        view_menu.addAction(self._fit_action)
        view_menu.addAction(self._actual_action)
        view_menu.addAction(self._fill_action)
        view_menu.addSeparator()
        view_menu.addAction(self._zoom_in_action)
        view_menu.addAction(self._zoom_out_action)
        view_menu.addSeparator()
        view_menu.addAction(self._metadata_action)
        view_menu.addSeparator()
        view_menu.addAction(self._thumb_zoom_in_action)
        view_menu.addAction(self._thumb_zoom_out_action)
        self._size_actions: list[QAction] = []
        size_menu = view_menu.addMenu("缩略图大小")
        for index in range(len(THUMBNAIL_SIZES)):
            action = QAction(f"{label_for_index(index)}（约 {edge_for_index(index)} 像素）", self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, target=index: self.set_thumbnail_size(target)
            )
            size_menu.addAction(action)
            self._size_actions.append(action)

        # 不加工具栏：这些命令在 macOS 顶部菜单里都有，再重复一遍只会挤掉照片。
        self._rebuild_recent_menu()
        # 补发一次底色：这些 QMenu 是**此刻**才建出来的，而主题只在那一刻已存在的顶层控件上下发。
        theme.ensure_installed()

    # ---- 每一条命令此刻能不能按 ----------------------------------------------

    def _update_action_states(self) -> None:
        self._update_navigation_actions()
        in_detail = self._in_detail_page()
        in_grid = self._stack.currentWidget() is self._grid_page
        # 改名的过程中别启用会动文件的命令 —— 那时键盘归输入框，⌘⌫ 不该把正在编辑的那张删掉。
        # **三个输入框都算**：网格那个、标题栏那个改名框（ADR 0025）与搜索框。
        idle = (
            not self._grid.is_renaming()
            and not self._title_bar.is_editing()
            and not self._search_field.is_editing()
        )
        for action in (
            self._fit_action,
            self._actual_action,
            self._zoom_in_action,
            self._zoom_out_action,
        ):
            action.setEnabled(in_detail)
        # 缩略图那两条只在网格页亮着；改到一半也不亮 —— 换档会重排网格，把正在编辑的那一格挪走。
        for action in (self._thumb_zoom_in_action, self._thumb_zoom_out_action):
            action.setEnabled(in_grid and idle)
        self._clear_cache_action.setEnabled(self._library is not None and self._cache_job is None)
        self._sync_fill_action()
        # 「清除搜索条件」**整条出现 / 消失**，不是置灰（ADR 0014 决策 4）：没有条件时它没有对象。
        # 文案跟着作用域走（ADR 0040 决策 5）：清掉的是「筛选条件」还是「搜索条件」。
        filtering_now = self._search_scope == SEARCH_SCOPE_LEVEL
        self._clear_search_action.setVisible(bool(self._conditions))
        self._clear_search_action.setText("清除筛选条件" if filtering_now else "清除搜索条件")
        self._search_action.setText("筛选…" if filtering_now else "搜索…")
        # 搜索框摆在标题栏那一行上（ADR 0026 决策 8），而**那一行只在 macOS 上存在**：别的平台置灰。
        self._search_action.setEnabled(self._owns_title_bar)
        # 切换按钮同理（它就摆在那一行上），并且要一直跟得上库级偏好。
        self._scope_toggle.setVisible(self._owns_title_bar)
        self._scope_toggle.setEnabled(self._library is not None)
        self._scope_action.setEnabled(self._library is not None)
        # 「改名」落在哪儿：大图页改的是标题栏那一行（ADR 0025），网格里是行内编辑。
        renaming_here = in_grid or (in_detail and self._title_bar.isVisible())
        # 移到废纸篓两边都成立（大图页删的是正在看的那一张）。**日历那一页两者都不是** —— 它没有
        # 行号，而网格的当前项还停着上一次点的那一格，拿它算可用性就会亮着一条指向别处的路。
        in_calendar = self._stack.currentWidget() is self._calendar_page
        # 「用外部应用打开」**只在有东西可开的时候出现**（ADR 0034，判据 ``is_available()``）。
        self._external_button.refresh()
        self._external_button.setVisible(
            self._owns_title_bar and self._external_button.is_available()
        )
        self._layout_title_bar()
        # **网格选区整轮只问一次** —— `selected_rows()` 要为每一个选中项新建一个 `QModelIndex`
        # 再收成列表（三万张全选时一次 15.4 ms），问两遍是纯重复劳动。
        grid_rows = self._grid.selected_rows() if in_grid else []
        entries = [] if in_calendar else self._selected_entries(grid_rows if in_grid else None)
        focus_row = -1 if in_calendar else self._focus_row()
        # 「改名」只对磁盘上的**某一个**东西成立（组卡片改不了名，一次只改一条，ADR 0009 / 0004）。
        considered: tuple[int, ...] = (
            (*grid_rows, self._grid.current_row()) if in_grid else (self._current_row,)
        )
        file_only = not any(
            isinstance(self._model.entry_at(row), GroupRef) for row in considered if row >= 0
        )
        # 「移到废纸篓」与「拷贝」一族作用于**摊平之后**的选区（一张分组卡片 = 它代表的组员）。
        flat = flattened_entries(entries)
        self._rename_action.setEnabled(renaming_here and focus_row >= 0 and idle and file_only)
        self._trash_action.setEnabled(bool(flat) and idle)
        # 可用性一律按「此刻作用于谁」算：菜单栏与右键菜单里的同一份动作因此永远说同一句话。
        paths = [entry.path for entry in flat]
        current = self._model.entry_at(focus_row) if focus_row >= 0 else None
        self._select_all_action.setEnabled(in_grid and self._model.rowCount() > 0)
        self._copy_path_action.setEnabled(bool(paths))
        self._copy_action.setEnabled(bool(paths))
        # ⌘V 与选区无关；**大图页不粘** —— 那一页没有「当前这一层」（ADR 0021）。
        # **筛选生效时两条写操作都不亮**（ADR 0040 决策 7）：这一层显示的不是全部内容，粘进去 /
        # 新建出来的照片可能一条都不出现，而它们恰恰是**会动用户文件**的那一类（红线 1），
        # 不该在用户看不清全貌时发生 —— 真要粘，先撤掉筛选。
        writes_hidden = self._is_filtering()
        self._paste_action.setEnabled(in_grid and not writes_hidden and self._can_paste())
        # ⇧⌘N 与选区无关（只看这一层有没有这个目录，ADR 0022）；分组里不行 —— 摆的全是照片。
        self._new_album_action.setEnabled(
            in_grid and idle and not writes_hidden and self._can_create_album()
        )
        # ⌘⏎ 与改名输入框抢键：改到一半键盘归输入框，否则「敲完名字顺手 ⌘⏎」会把访达叫起来。
        self._reveal_action.setEnabled(bool(paths) and idle)
        self._open_entry_action.setEnabled(current is not None)
        # ⇧⏎ 比 ⌘⏎ 更窄（这一格得是**照片**、没在改名）；**大图页上也可用**（ADR 0021）。
        self._open_default_action.setEnabled(idle and isinstance(current, PhotoRef))
        self._copy_metadata_action.setEnabled(isinstance(current, PhotoRef))
        # 「编辑元数据」在右键菜单里是「不出现」、在菜单栏是「置灰」（那一处永远摆着，否则 ⇧⌘I
        # 没地方显示提示），两条判据同源：``metadata_edit_candidate``。
        self._edit_metadata_action.setEnabled(self.metadata_edit_candidate() is not None and idle)
        self._copy_group_action.setEnabled(in_grid and isinstance(current, GroupRef))
        self._close_entry_action.setEnabled(in_detail)
        self._update_undo_action()

    def _schedule_action_states(self, *_args: object) -> None:
        """选区变了：把全量重算**攒到这一轮事件循环结束时**做一次。"""
        self._action_states_timer.start()

    def _update_undo_action(self) -> None:
        """「撤销」的文案跟着这摞的栈顶变，栈空时禁用。"""
        self._undo_action.setText(
            "撤销" if self._undo_label is None else f"撤销 {self._undo_label}"
        )
        self._undo_action.setEnabled(self._undo_label is not None)

    def _refresh_undo_label(self) -> None:
        """重新问一次 core：下一次 ⌘Z 会撤掉什么。"""
        library = self._library
        self._undo_label = library.next_undo_label() if library is not None else None
        self._update_undo_action()

    def enter_folder(self, directory: Path, *, focus: EntryKey | None = None) -> None:
        """进入一个相册（子文件夹）。库以外的路径一律拒绝。

        :param focus: 到了这一层该选中哪一格；``Esc`` 退上去时传的是刚离开的那个相册。

        **进目录就是退出整库检索**：两者都是「一层」（ADR 0026）—— 条件不清掉的话，下一次
        ``_start_scan`` 里检索会抢在目录前面，点开一个相册看到的却是全库的检索结果。
        **筛选不在此列**（ADR 0040 决策 1）：它收窄的正是「当前这一层」，所以进相册就是进到
        「那个相册里符合条件的那部分」，条件要跟着走 —— 栈模型下它是**跟着这一帧走**，
        弹回来时自动恢复（ADR 0041 决策 2）。

        **存进去的是解析过的路径**：库路径里带软链接时，监听器报回来的是真路径而这里存的是
        原写法，「是不是在当前这一层」就永远说不成 —— 访达里丢进来一张照片，网格当没看见。
        """
        library = self._library
        if library is None:
            return
        target = Path(directory)
        if not paths.is_within(library.root, target):
            return
        resolved = target.resolve()
        # 进相册 = 退出整库检索（ADR 0041 决策 3）。**必须先弹搜索帧再压相册链**：搜索帧压在
        # 相册链之上，而 ``_drop_search_frames`` 只从栈顶往上弹，反过来弹掉的是刚压上去的那一层。
        leaving_search = self._search_frame is not None
        if leaving_search:
            self._leave_search_frame(self._search_frame, reload=False)
            self._drop_search_frames()
        self._push_album_chain(resolved)
        # **只有整库检索要清条件**（理由见上面那段）：筛选要跟着走进这个相册。
        self._reload_level(focus=focus)

    def _on_back_button(self) -> None:
        """鼠标后侧键 = **「上一级」那一条命令**（ADR 0043），与 ``Esc`` 是同一个动作。

        **输入框被这一下收掉时这一格让开**：Qt 是先把焦点给鼠标底下的控件、再把事件交给过滤器
        的（实测顺序：``focusOut`` → 改名提交 → 过滤器），所以等这一层看见侧键时输入框已经收起、
        那条动作也已经重新亮起来 —— 直接触发就是「既把名字提交了又退一层」。而这一按的意思
        早就变成了「点别处」（与左键完全一样）。
        """
        if self._input_closed_by_back_button:
            self._input_closed_by_back_button = False
            return
        # **不自己判能不能退**：那条动作的启用状态就是那一句（``Esc`` 此刻按不到时侧键也按不到）。
        self._up_action.trigger()

    def go_up(self) -> None:
        """**弹掉栈顶一帧** —— 只管位置，与条件无关（ADR 0041 决策 1）。

        「能退什么」不再由一串手写的 ``if`` 决定，而是**看弹掉的那一帧是什么**，四种情况在
        :meth:`_leave_frame` 里分派。加一种新层级只需在那一处加一支 —— 而原来「忘了在
        ``go_up`` 与 ``_can_go_up`` 里各加一支」正是那个 bug：搜索结果既不是文件夹也不是分组，
        于是 ``_can_go_up()`` 恒为 ``False``，「搜索后直接按 Esc 能退出」是**巧合**（那时焦点
        还在搜索框里，走的是搜索框自己的 Esc），进一趟分组、焦点回到网格就没反应了。

        退上去时**把刚离开的那一级留在选中状态**（访达里退上一层就是这样）；只此一处例外 ——
        再点进去是另一次浏览，那一层不留上一次的选中（见 :meth:`_arm_focus`）。
        """
        if self._library is None:
            return
        popped = self._pop_frame()
        if popped is None:
            return  # 只有根帧：退不动就安静地不动
        self._leave_frame(popped.top)

    def _leave_frame(self, frame: object) -> None:
        """**刚弹掉的是哪一帧，就按那一帧做收尾**（重新列出这一层、必要时回日历 / 选中原卡片）。

        **这是 ``go_up`` 唯一需要「认得帧」的地方**：弹栈是统一的，而不同帧的收尾差别很大
        —— 日历格那一帧要回日历页、分组那一帧要把刚离开的卡片重新选中、相册与搜索帧只是重列
        那一层。

        **注意 :meth:`go_up` 是「先弹再收尾」** —— 所以这一支**不能**走 ``enter_folder``
        （那会再压一帧，栈越退越深，实测退不出相册）。要退到哪一层是弹之前就决定的：
        弹掉相册帧之后底下那一帧就是去处。
        """
        if isinstance(frame, AlbumFrame):
            # 弹掉相册帧之后**底下那一帧**就是要去的地方（可能是根帧 = 库根）。
            target = self._level_stack.top
            focus = path_key(frame.path)
            if isinstance(target, GroupFrame):
                self._reload_level(focus=entry_key(target.group))
                return
            if isinstance(target, CalendarFrame):
                self._leave_calendar_cell()
                return
            self._reload_level(focus=focus)
            return
        if isinstance(frame, GroupFrame):
            self._reload_level(focus=entry_key(frame.group))
            return
        if isinstance(frame, CalendarFrame):
            self._leave_calendar_cell()
            return
        if isinstance(frame, PhotoFrame):
            # 照片帧底下是日历格就回日历，否则回网格 —— **大图页的「上一级」就是这里**。
            if self._calendar_frame is not None:
                self._back_to_calendar()
                return
            self.leave_detail_page()
            return
        if isinstance(frame, SearchFrame):
            # 条件是那一帧的内容，所以帧没了条件也没了 —— 底下那一层恢复成「没搜过」的样子。
            # **必须把刚弹掉的那一帧传进去**：弹完之后重查栈只会得到「压根没有搜索帧」。
            self._leave_search_frame(frame)
            return
        self._reload_level()

    def pop_last_condition(self) -> None:
        """撤掉最后一条筛选条件（``⇧Esc``，或菜单里的那一行）。

        **与 :meth:`go_up` 分开是用户选定的**：Esc 纯粹退位置，退不掉的时候什么也不做，也不
        顺手撤掉一条条件 —— 「按 Esc 退一层，结果条件少了一条」这件事没法从屏幕上分辨出
        「我退了」还是「我撤了一条」。
        """
        if not self._conditions:
            return
        self._conditions.pop()
        self._editing_condition = None
        self._sync_search_frame()  # 撤光了就不再是整库检索 → 弹掉搜索帧（ADR 0041 决策 3）
        self._apply_conditions()

    def _can_go_up(self) -> bool:
        """现在退不退得回去。**就是「栈深 > 1」**（ADR 0041 决策 1）。

        判据与 :meth:`go_up` 用**同一句话**（栈深），不各手写一遍那串 ``if`` ——「退错了一层」
        不是任何一句能自检的东西。
        """
        if self._library is None or self._grid.is_renaming():
            return False
        return self._level_stack.can_pop()

    def _on_background_activated(self) -> None:
        """双击了网格里没有格子的地方：退回上一层，与 ``Esc`` 是同一件事。

        这一层空着（空相册、空分组）时尤其需要它 —— 屏幕上根本没有可双击的东西。退不动时
        安静地什么都不做：到了头本来就是一个合法状态。
        """
        if self._grid.is_renaming():
            # 改到一半：这一下先归输入框。既不把人带走，也不打扰他。
            return
        self._cancel_condition_edit()  # 改到一半：这一下算放弃（ADR 0040 决策 12）
        if self._can_go_up():
            self.go_up()

    def _on_breadcrumb_requested(self, directory: object) -> None:
        if isinstance(directory, Path):
            self.enter_folder(directory)

    def _reload_level(self, *, focus: EntryKey | None = None) -> None:
        """重新列出当前层（切模式、进入或返回相册时都走这里）。

        :param focus: 到了这一层要选中哪一格；**只有往上一层退才会给**，其余由
            :meth:`_arm_focus` 自己判断。
        """
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        self._cancel_level_filter()
        self._arm_focus(focus)
        # 换代：让已经在路上的旧结果作废。
        self._generation += 1
        self._grid.cancel_rename()
        self._cancel_facet_job()
        self._facet_values = {}
        self._model.clear()
        # 网格先清干净：当前项留着的话，⌘R / ⌘⌫ 的可用性判据里它仍然算数（菜单亮着，对象却不存在）。
        self._grid.reset_for_new_level()
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._staged = []  # 换层要重扫，攒着，别先摆一屏旧顺序
        self._level_notice = False
        self._progress_notice = False
        # 换层只把横幅收掉，**不打断**全库解析：它是整个库的事，与当前在哪一层无关。
        self._place_notice_text = None
        self._scan_issue = False
        self._current_row = -1
        self._detail.clear_image()
        self._panel.clear()
        self._banner.hide()
        # 整库检索不是日历这一层的一部分：条件撤光之后 Esc 不该退到日历的那一格。
        self._drop_calendar_cell_frame()
        # **摆哪一页问的是状态**（浏览方式 + 有没有条件）：一律摆成网格页，勾与眼前那一页对不上。
        self._update_breadcrumb()
        if self._shows_calendar_page():
            # 日历那一页自己收尾（标题、命令可用性、缩略图窗口），位置不动。
            self._show_calendar()
        else:
            self._stack.setCurrentWidget(self._grid_page)
            self._update_action_states()
        self._start_scan()

    # ---- 到了这一层该选中哪一格 ----------------------------------------------
    # **往上一层退**时仍停在刚离开的那个相册 / 那个组；**还是这一层重排一遍**时跟着停着的那一格走；
    # **换了层**时谁也不选 —— 留着上一次的选择，用户看到的是上一趟的高亮。三者都靠**标识**而不是
    # 行号，比一比**层标识**就知道属于哪一种。

    def _arm_focus(self, focus: EntryKey | None = None) -> None:
        """记下到了新的一层该选中哪一格。

        :param focus: 显式指定（往上一层退时给的那个相册 / 那个组）；不给就由下面的规则判断。

        判断靠比层标识（``navigation.level_key``）：两个 ``enter_*`` 都是先改状态再重载，所以
        问「现在在哪一层」得到的是**新的**那一层，正好拿来和 ``self._level_key`` 比。

        **顺序要紧**：先把「还是同一层吗」问完，再换 ``self._level_key``；反过来两边永远相等，
        「换了层就什么都不记」这条永远轮不到执行。
        """
        remembered = self._shown_entry_key()
        self._level_key = self._current_level_key()
        self._focus = focus if focus is not None else remembered
        self._focus_restored = False

    def _current_level_key(self) -> LevelKey:
        """当前这一层的标识（见 ``navigation.level_key``）。"""
        group = None if self._group_view is None else (self._group_view.facet, self._group_view.key)
        conditions = [condition.identity for condition in self._conditions]
        return level_key(
            self._browse_mode,
            self._current_dir,
            group=group,
            conditions=conditions,
            scope=self._search_scope,
        )

    def _shown_entry_key(self) -> EntryKey | None:
        """网格上此刻停着的那一格是谁；不是同一层（或者压根没有）就是 ``None``。"""
        if self._level_key is None or self._level_key != self._current_level_key():
            return None
        entry = self._model.entry_at(self._grid.current_row())
        return None if entry is None else entry_key(entry)

    def _restore_focus(self) -> None:
        """这一层排好之后，把该选中的一格选中。

        **每一层只摆一次**：摆完之后就是用户自己的选择了，后台再重排（来了新照片、换排序）
        不该把它拽回去 —— 那种重排由 ``_reorder_level`` 的锚点负责。找不到就什么都不选。
        """
        if self._focus_restored:
            return
        self._focus_restored = True
        key, self._focus = self._focus, None
        if key is None:
            return
        row = self._row_of_focus(key)
        if row >= 0:
            self._grid.select_row(row)  # 一并滚过去：这一层可能长到一屏之外

    def _row_of_focus(self, key: EntryKey) -> int:
        """该选中的那一格现在在第几行（``-1`` = 这一层里已经没有了）。

        照片与相册走模型现成的路径索引；组卡片没有索引，扫一遍这一层 —— 两条路不能合成一条，
        因为这一层里的照片可能有几万张。
        """
        if key[0] == ENTRY_KIND_PATH:
            return self._model.row_of_path(Path(key[1]))
        return row_of_entry_key(self._model.entries(), key)

    def _group_view_label(self) -> str | None:
        """当前所在分组的显示名（没在分组里就是 ``None``）。"""
        return None if self._group_view is None else self._group_view.label

    def _update_breadcrumb(self) -> None:
        """面包屑那一行写什么（**顺带把日历工具条上那几枚胶囊也摆好**）。

        **两种作用域写的是两件不同的事**（ADR 0040 决策 4）：整库检索时报**只有库名** ——
        范围真的变成整库了，留着「旅行 ▸ 2024」读起来就是「在这个文件夹里搜到 4 张」，而那 4 张
        大半在别处（ADR 0026 决策 1b）；筛选时报**完整路径 + 那一组**，因为范围没变，藏掉路径
        同样是在说一件不存在的事。
        """
        library = self._library
        # 平铺模式下平时不显示面包屑，但**分组里与有条件时一律显示**（ADR 0026 决策 9）。
        in_albums = self._browse_mode == BROWSE_MODE_ALBUMS
        conditions = [condition.chip_text for condition in self._conditions]
        editing = self._editing_condition if conditions else None
        # **日历工具条那几枚先摆，且在每一个早退之前**（ADR 0040 决策 9）。摆在末尾就漏了
        # 「撤光条件」那一步：早退在 ``conditions`` 为空时直接返回，那一枚撤掉的胶囊就一直挂着，
        # 要切一次视图再切回来才走。
        self._sync_calendar_conditions(conditions, editing)
        if self._drill_photos is not None:
            # 日历点出来的那一格不是文件夹，面包屑没有一段能指；标题里已经写着它在哪一段日子。
            self._hide_breadcrumb()
            return
        if library is None or (not in_albums and self._group_view is None and not conditions):
            self._hide_breadcrumb()
            return
        if self._is_library_search():
            # **检索时只报库名：既不报当前文件夹，也不报当前那一组**（检索横跨整个库，ADR 0026
            # 决策 1）。范围要收窄加一条「#相册 …」；``_group_view`` 与 ``_current_dir`` 都留着。
            parts = [(library.display_name, library.root)]
            group = None
        else:
            parts = (
                breadcrumb_parts(library.root, self._current_dir or library.root)
                if in_albums
                else [(library.display_name, library.root)]
            )
            # 筛选时这一组**说的是真的**：它就是这一层的范围。
            group = self._group_view_label()
        self._breadcrumb.set_path(
            parts,
            group=group,
            conditions=conditions,
            result_count=self._search_total if conditions else None,
            editing=editing,
            scope=self._search_scope,
        )
        self._breadcrumb.show()

    def _hide_breadcrumb(self) -> None:
        """藏起来，**并且清空内容**。

        ``hide()`` 只让控件不画，上一轮摆的那些还留在布局里（``set_path`` 开头就是 ``_clear()``）；
        「不摆」与「摆成空的」不是同一件事。
        """
        self._breadcrumb.clear_content()
        self._breadcrumb.hide()

    def _sync_calendar_conditions(self, conditions: Sequence[str], editing: int | None) -> None:
        """把同一批条件也摆到日历工具条上（ADR 0040 决策 9）。

        **两处摆的是同一批条件**：这一行占掉的就是网格页那条面包屑（ADR 0033 决策 5），而筛选时
        用户看不到网格页 —— 只摆面包屑上，条件就等于「加了但不知道加了什么」。

        **无论当前在哪一页都要更新**：条件可能是在网格页上改的（那边改完切到日历页要立刻看见），
        所以判据是「条件现在是什么样」，不是「日历页此刻在不在线」。
        """
        self._calendar_page.set_conditions(conditions, editing=editing)

    def _update_mode_actions(self) -> None:
        self._albums_action.setChecked(self._browse_mode == BROWSE_MODE_ALBUMS)
        self._flat_action.setChecked(self._browse_mode == BROWSE_MODE_FLAT)
        self._calendar_action.setChecked(self._browse_mode == BROWSE_MODE_CALENDAR)

    def _update_navigation_actions(self) -> None:
        # 「上一级」是**弹栈顶一帧**，大图页那一帧也压在上面，所以那里同样成立（ADR 0041 决策 1）。
        # 改到一半不启用：那一刻 Esc 归输入框（「取消改名」）。
        idle = not self._grid.is_renaming() and not self._title_bar.is_editing()
        self._up_action.setEnabled(idle and self._can_go_up())
        # **「撤掉最后一条」不限于网格页**：条件是跨页的，在日历那一页也还在，于是那条 ⇧Esc 在
        # 任何一页都要能用（ADR 0040 决策 11）。没条件时关掉，而不是按下去什么也不发生。
        self._pop_condition_action.setEnabled(bool(self._conditions))

    # ---- 窗口 --------------------------------------------------------------

    def _is_title_bar(self, point: QPointF) -> bool:
        """这一点是不是落在标题栏那一段（窗口顶边与内容区之间）。

        **真全屏时一律不是**（那一整条已经收起来了，见 :meth:`_layout_title_bar`），**铺满时
        照旧是** —— 那只是窗口变大，于是双击铺满之后还能双击还原。
        """
        if not self._owns_title_bar or self.isFullScreen():
            return False
        central = self.centralWidget()
        return central is not None and point.y() < central.geometry().top()

    def _press_belongs_to_the_title_bar(self, position: QPointF) -> bool:
        """这一次按下是不是**归标题栏**（「按住拖窗口」与「双击铺满」该轮到这里）。

        **改到一半不算**：那一段上正摆着输入框，拖走它等于把正在改的名字甩到屏幕外。两件事
        用同一句判据，于是不会出现「拖不动却能双击」这种半截状态。
        """
        return self._is_title_bar(position) and not self._title_bar.is_editing()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """按住标题栏那一段拖动窗口。

        内容铺到标题栏底下之后那一条已经是本窗口的客户区，系统不再代劳拖动，得自己发起；按下
        **先排一个闸**（见 :meth:`_start_title_bar_drag`），确认不是双击之后才真的发起 ——
        不等这个双击周期，双击标题栏一次都不会生效（macOS 的原生标题栏也等）。

        **不用先问 ``TitleBar`` 在不在那儿**：不在编辑时它对鼠标是透明的，按下照旧落到窗口
        自己身上。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._press_belongs_to_the_title_bar(
            event.position()
        ):
            self._title_bar_drag_armed = True
            self._title_bar_drag_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """松手就作废那一闸：拖动得**按住不放**，点一下标题栏什么也不该发生。"""
        if self._title_bar_drag_armed:
            self._title_bar_drag_armed = False
            self._title_bar_drag_timer.stop()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """双击标题栏：把窗口**铺满**，再双击一次还原（macOS 的标准动作）。

        那一段已经归窗口了（ADR 0025 决策 1），系统设置里「双击标题栏」那一档也就不生效，
        这件事得自己做。**做的是「铺满」而不是真全屏**（见 :meth:`_toggle_filled`）：占满菜单栏
        与 Dock 之间的那块地方，两者都还在，也不另开 Space。

        **这一下必须先于拖动到达**：拖动一发就是系统的模态拖动会话，会把这一串鼠标事件吃掉
        —— 所以拖动那一路才要等过一个双击周期（见 :meth:`mousePressEvent`）。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._press_belongs_to_the_title_bar(
            event.position()
        ):
            self._title_bar_drag_armed = False
            self._title_bar_drag_timer.stop()
            self._toggle_filled()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _start_title_bar_drag(self) -> None:
        """真的发起系统拖动 —— 已经等过一个双击周期，确认这一下不是双击。

        **为什么非要等**：Qt 在 macOS 上把 ``startSystemMove`` 实现成
        ``[NSWindow performWindowDragWithEvent:]``，那是一个**模态的事件跟踪循环**，拖完之前
        不返回 —— 不设闸的话「双击标题栏铺满」一次都不会发生，而界面上看不出任何理由。

        ``startSystemMove`` 返回假（平台不支持、或系统自己会拖）就什么都不做：这一次按下没有
        别的含义，不该往下传成一次点空白处的点击。
        """
        if not self._title_bar_drag_armed:
            return
        self._title_bar_drag_armed = False
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def _toggle_filled(self) -> None:
        """铺满 / 还原 —— 也就是 macOS 的**缩放**（AppKit 的 ``zoom:``，⌃⌘F 那个键）。

        **占满的是菜单栏与 Dock 之间的那块地方，两样都还在**，也不另开 Space：
        ``showMaximized`` 在 macOS 上正是 ``[NSWindow zoom:nil]``，落到 ``visibleFrame``。
        **这不是 macOS 的「填满」**（那个会盖住菜单栏与 Dock），系统设置里那一档**不读**。

        **还原回进铺满之前那一份几何**（``showNormal`` 恢复的正是它），而不是让系统按当前
        屏幕随便给一个 —— 拖到一半再双击回来，窗口该回到刚才那个大小与位置。
        """
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ---- 标题栏（见 ADR 0025）-----------------------------------------------

    def _in_detail_page(self) -> bool:
        """现在是不是大图页。「此刻作用于谁」的判据都问它（ADR 0021 决策 3）。"""
        return self._stack.currentWidget() is self._detail_page

    def _refresh_title(self) -> None:
        """窗口顶边那一行显示什么：**大图页是正在看的那张照片的文件名，其余是相册名**。

        判据只有这一段（「现在在哪一页 + 那一格是谁」），于是翻图、改名（含 ⌘Z 撤回）、换库走
        的都是同一段算术，不会有一处忘了更新而标题还写着旧名。
        """
        photo = self._model.photo_at(self._current_row) if self._in_detail_page() else None
        if photo is not None:
            name = photo.name
        elif self._drill_photos is not None:
            # 从日历某一格摊开的那一层：标题要说得清在看哪一格，否则与「全部照片」长得一模一样。
            name = f"{self._drill_label} · {len(self._drill_photos)} 张"
        else:
            name = self._library_title
        if self._owns_title_bar:
            self._title_bar.set_text(name)
        else:
            # 标题栏那一段不归窗口（例如 Linux），照旧交给系统画（ADR 0025 决策 2）。
            self.setWindowTitle(name)

    def _layout_title_bar(self) -> None:
        """把 ``TitleBar`` 与搜索胶囊摆在窗口顶边那一条上。

        那一条的高度是**安全区**顶出来的那 28 点，不写死。**真全屏**时系统自己也不画标题栏，
        这一条跟着收起来（否则会盖在照片上，ADR 0025 决策 2）；**铺满窗口不在此列**。

        **两个控件是摆在一起的，不是一套布局**：``TitleBar`` 那一行由标题文字与改名字段自己
        居中，胶囊贴右边，抢同一份布局反而互相挪（ADR 0026 决策 8）。**内容区让出多少也归
        这里**（``_build_ui`` 只留初始值）：真全屏时那一段收起来，让出去的 12 点就该还回去 ——
        不还回去的话，照片区顶上会压着一条什么都没有的空档。
        """
        if not self._owns_title_bar:
            return
        # 判据是**真全屏，不是铺满**：铺满只是窗口变大，那一条照旧在（见 :meth:`_toggle_filled`）。
        full_screen = self.isFullScreen()
        self._stack.setContentsMargins(0, 0 if full_screen else TITLE_BAR_EXTRA, 0, 0)
        if full_screen:
            # 高度给 0，于是它与胶囊都不会盖在照片上（ADR 0025 决策 2）。
            self._title_bar.setGeometry(0, 0, self.width(), 0)
            self._title_bar.hide()
            self._search_field.hide()
            self._external_button.hide()
            self._scope_toggle.hide()
            return
        central = self.centralWidget()
        top = central.geometry().top() if central is not None else 0
        # 安全区之外**再加** :data:`TITLE_BAR_EXTRA`：三页的内容都从 ``安全区 + EXTRA`` 开始。
        height = max(top + TITLE_BAR_EXTRA, TITLE_BAR_HEIGHT)
        self._title_bar.setGeometry(0, 0, self.width(), height)
        self._title_bar.setVisible(True)
        # 胶囊摆在**这一条**里（内容区从 ``top + EXTRA`` 开始），再往下挪
        # :data:`SEARCH_FIELD_TEXT_ALIGN_DROP`。
        field_height = min(SEARCH_FIELD_HEIGHT, height - 4)
        # **右上角那一块有两样东西**（ADR 0040 决策 5）：切换按钮贴着窗口右边，胶囊被推到它左边。
        # 两者的间距与同高都由 :data:`SCOPE_TOGGLE_GAP` 与 ``field_height`` 一处给出 —— 各算各的
        # 会「歪了一两个点」（见 ``external_button.button_rect``）。
        block_right = self.width() - SEARCH_FIELD_MARGIN_RIGHT
        toggle_width = min(SCOPE_TOGGLE_EDGE, max(0, field_height))
        toggle_x = block_right - toggle_width
        search_x = toggle_x - SCOPE_TOGGLE_GAP - SEARCH_FIELD_WIDTH
        self._search_field.setGeometry(
            search_x,
            (height - field_height) // 2 + SEARCH_FIELD_TEXT_ALIGN_DROP,
            SEARCH_FIELD_WIDTH,
            field_height,
        )
        self._search_field.show()
        # **要抬一次**：布局跑完之后，中央控件与菜单栏会被抬到「最后创建的子控件」之上把胶囊盖住。
        self._search_field.raise_()
        # 切换按钮**建在胶囊之后**（`_build_ui` 里），于是这一次抬就是为它准备的。
        self._scope_toggle.setGeometry(toggle_x, self._search_field.y(), toggle_width, field_height)
        self._scope_toggle.show()
        self._scope_toggle.raise_()
        #: **窗口右缘到胶囊右缘**有多远 —— 面包屑那几枚条件胶囊对齐的就是这条线（ADR 0026 决策
        #: 8），这一个数是两者之间唯一的换算依据。
        inset_to_capsule = SEARCH_FIELD_MARGIN_RIGHT + toggle_width + SCOPE_TOGGLE_GAP

        # 「用外部应用打开」**从胶囊那一块往左长出来**（见 ``button_rect``），于是两者必然同高、同一
        # 基线。**显隐的判据只有一处**（``is_available``）；真全屏时它整个不见。
        if self._external_button.isVisible():
            self._external_button.setGeometry(button_rect(self._search_field.geometry()))
            self._external_button.raise_()
        # 标题文字与 ⌘R 的输入框都要给右上角这一整块让出来（胶囊**加上左边的按钮**）。
        self._title_bar.reserve_right(
            inset_to_capsule
            + SEARCH_FIELD_WIDTH
            + SEARCH_FIELD_MARGIN_RIGHT
            + (BUTTON_WIDTH + BUTTON_GAP if self._external_button.isVisible() else 0)
        )

        # 面包屑右端那几枚条件胶囊与这个胶囊**右缘对齐**；换算只在这一处做 —— 面包屑摆在一份有
        # 边距的布局里，右缘比窗口右缘靠内 :data:`_GRID_PAGE_MARGIN_RIGHT` 点。
        # **对齐的是胶囊而不是那一整块**：切换按钮在胶囊右边，让条件胶囊对齐到按钮的右缘会在
        # 中间空出一截，看着像它们不属于任何一个框。
        self._breadcrumb.set_right_inset(inset_to_capsule - _GRID_PAGE_MARGIN_RIGHT)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().showEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """窗口状态一变（铺满、还原、真全屏）就把窗口顶边那一行重摆一次。

        **铺满时其实什么都不用动**，但**真全屏要动**（见 :meth:`_layout_title_bar`），而判据
        不能只挑那个来 —— 一并跟着 ``WindowStateChange`` 走，少一处判断也就少一处走错的机会。

        **外部按钮的显隐也要补一次**：真全屏那一支把它藏起来了，而退出真全屏不经过
        ``_update_action_states``，不补这一句它就没了。别的 ``changeEvent``（例如激活状态）不重摆。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # **先把外部按钮叫回来，再摆**：真全屏把它藏起来了，而退出真全屏
            # 不经过 ``_update_action_states``。
            self._external_button.setVisible(self._external_button.is_available())
            self._layout_title_bar()
            self._layout_banner()
            self._sync_fill_action()

    def _sync_fill_action(self) -> None:
        """「铺满窗口 / 还原窗口」那一句跟着此刻的状态走。

        **两处都要调**：菜单那一条平时跟着 :meth:`_update_action_states` 走，而双击标题栏与按
        ⌃⌘F 都不经过那条路。**判据是「铺满」而不是「真全屏」** —— 菜单上这一条说的就是铺满，
        与绿按钮悬停菜单里的「进入全屏幕」不是同一件事。
        """
        self._fill_action.setText("还原窗口" if self.isMaximized() else "铺满窗口")

    def _on_scope_switched(self) -> None:
        """切换按钮被按了：把作用域**掉个头**（按钮自己不记状态，ADR 0042 决策 1）。

        走 :meth:`set_search_scope` —— **与菜单里那一条是同一个入口**，所以两处永远说同一句话
        （各写一份回调，早晚会有一处忘了改其中一样）。
        """
        self.set_search_scope(
            SEARCH_SCOPE_LIBRARY if self._search_scope == SEARCH_SCOPE_LEVEL else SEARCH_SCOPE_LEVEL
        )

    def _on_title_rename_committed(self, text: str) -> None:
        """标题栏里确认了一个新名字。

        **与网格那个输入框走同一个写操作入口**（``write_ops._on_rename_committed``），因此记账、
        横幅、撤销、监听器回声一样都不少（红线 1、ADR 0025 决策 3）。
        """
        self._on_rename_committed(self._focus_row(), text)

        # ---- 用外部应用打开（ADR 0034）------------------------------------

    def _external_entries(self) -> tuple[ExternalEntry, ...]:
        """下拉框里那几项。**每一项自己判自己此刻该不该出现**（``ExternalEntry.enabled``），
        于是列表不用随页面变化重建，翻页时 ``refresh`` 问一遍就够。
        """
        return (
            ExternalEntry(
                id="google_calendar",
                name="Google 日历",
                icon=calendar_icon(),
                enabled=self._on_calendar_page,
            ),
            ExternalEntry(
                id="finder",
                name="访达",
                icon=folder_icon(),
                # **与日历那一项互补**：日历页给 Google 日历，其余页给访达（每一页只有一样可开的）。
                enabled=lambda: not self._on_calendar_page() and self._current_folder() is not None,
            ),
        )

    def _on_external_entry(self, entry: object) -> None:
        """下拉框里点了哪一项。**按 id 分派**，不在按钮那一侧写死（理由见 ``ExternalEntry``）。"""
        if not isinstance(entry, ExternalEntry):
            return
        if entry.id == "google_calendar":
            self._open_google_calendar()
        elif entry.id == "finder":
            self._open_finder()

    def _open_google_calendar(self) -> None:
        """把 Google 日历开到与当前这一页一致的那一层、那一天。

        **日期取「选中的一天，没有选中就取锚点」**；周视图把那一天递过去即可（两边的周都从
        周日起）。**整个过程进后台线程** —— 装没装 PWA 要扫一遍应用包，实测冷启动一两秒。
        """
        page = self._calendar_page
        level = page.level()
        day = page.selected_day() or page.anchor()
        self._runner.submit(
            lambda _context: launch.open_google_calendar(level, day),
            on_result=self._on_external_opened,
            on_error=self._on_external_failed,
        )

    def _on_external_opened(self, outcome: object) -> None:
        if not isinstance(outcome, launch.OpenOutcome):
            return
        # **说过就走**：开都开完了，用户看的应该是那个窗口而不是这条消息（ADR 0029）。
        self._show_banner(banners.external_app_opened(outcome, "Google 日历"), auto_hide=True)

    def _on_calendar_page(self) -> bool:
        """此刻是不是日历那一页。**下拉框那两页互斥的判据都在这一句里。**"""
        return self._stack.currentWidget() is self._calendar_page

    def _current_folder(self) -> Path | None:
        """此刻所在的那个文件夹，**没有库就是 ``None``**。

        口径就是 ``_current_dir or library.root``：全部照片视图下锚点是库根，相册视图下是当前
        那一层。**分组时用的也是它** —— 组卡片代表的那批照片就摆在这个目录里（ADR 0020）。
        """
        library = self._library
        if library is None:
            return None
        return self._current_dir or library.root

    def _open_finder(self) -> None:
        """在访达里**打开**当前这个文件夹 —— 落到它**里面**去，看的是里面摆着什么。

        走 ``desktop.open_folder_in_finder``，与右键菜单对**相册**走的是同一件事（同一个
        入口，同一句话的措辞）。**与「在访达中显示」不是同一件事**：那条是照片那一路，
        ``open -R`` 把东西在它所在的目录里选出来（ADR 0014 决策 3）；对着文件夹用它，
        用户看到的是文件夹**外面**那一层。
        """
        folder = self._current_folder()
        if folder is None:
            self._show_banner("还没有打开相册库，没有可以打开的文件夹。")
            return
        self._show_banner(desktop.open_folder_in_finder([folder]), auto_hide=True)

    def _on_external_failed(self, message: str) -> None:
        # 走到这里的是「起进程这一步整个炸了」；``core.external.launch`` 自己的降级路径不抛异常。
        self._show_banner(f"没能打开 Google 日历：{message}")

    # ---- 关闭 --------------------------------------------------------------

    def quit_app(self) -> None:
        """⌘Q：收完尾之后退出**整个应用**（与 :meth:`close` 那一半是两件事，ADR 0037）。

        **必须先 ``close()``**：会话落盘、监听器停掉、后台任务收工全在 :meth:`closeEvent` 里，
        而 ``QApplication.quit()`` 压根不发它 —— 直接调它的话那三样一步都不走。后半句是为
        ``app.keep_running_without_windows`` 准备的（关掉最后一个窗口不再自动退出应用）。
        """
        self.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self._save_session()
        self._teardown_library()
        # ``abandon`` 而不是「取消 + 等」：排队里还没开始的任务必须丢掉，否则每跑掉一个就再等一个
        # （几百张照片就是好几分钟）。已经跑起来的那几个靠令牌收工，连「云盘上没下完」也能立刻放手。
        if not self._runner.abandon():
            logger.warning("仍有后台任务未结束，已放弃等待")
        # 剪贴板那个连接**不显式断开**：PySide 会在接收者析构时自动摘掉，显式 disconnect 反而报错。
        super().closeEvent(event)


__all__ = ["MainWindow"]
