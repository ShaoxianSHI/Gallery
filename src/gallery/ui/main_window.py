"""主窗口：把库、后台任务与各个视图装配起来。

界面层的状态全部集中在这里，并且**显式传递**：没有全局单例，没有模块级可变状态，
所以打开两个库、或者在测试里构造两个窗口，都不会互相干扰。

三个「不许」在这里具体落地：

- 不自己读原图：一切像素都来自 ``gallery.core``（经 ``Library`` 门面）。
- 不偷偷写盘：**每一次写操作都来自用户在菜单或快捷键里亲手触发的那一下** ——
  改名（⌘R）、移到废纸篓（⌘⌫）、粘贴（⌘V）、复制 / 移动到相册、新建相册
  （⇧⌘N）、设为相册封面、撤销（⌘Z），另加开场对话框里同意后的 ``prepare_library``。
  每一条都有对应的 ADR，没有一个是后台整理、脚本改名或「顺便改改」（红线 1）。
- 不阻塞主线程：**大件**都在 ``TaskRunner`` 的线程池里 —— 扫描、分组用的元数据批量
  读取、缩略图与全分辨率解码、粘贴与复制 / 移动（一次几百 MB）。
  留在主线程上的是**小件**，刻意如此：``mkdir`` 建一个空文件夹（ADR 0022 决策 6）、
  会话记忆与窗口几何的落盘。**移到废纸篓、撤销一次跨卷移动、清空缩略图缓存也在
  主线程上** —— 它们平时是毫秒级的（同一卷内只是一次改名），但用户对着一个几 GB 的
  相册按 ⌘⌫ 时会卡住，那一档的取舍是「不打断界面反馈」而不是「不卡」。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QPointF, QRect, Qt, QTimer
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
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gallery.core import paths
from gallery.core.errors import GalleryError
from gallery.core.library.config import prepare_library
from gallery.core.library.facade import Library, PlacePass
from gallery.core.library.groups import SortOrder, build_groups, sort_photos
from gallery.core.library.photos import FolderRef, GridEntry, GroupRef, PhotoRef
from gallery.core.library.scanner import ScanReport, describe_entry
from gallery.core.library.search import (
    RESULT_LIMIT,
    ParsedInput,
    SearchCondition,
    make_condition,
    parse_input,
    unusable_reason,
)
from gallery.core.library.session import (
    BROWSE_MODE_ALBUMS,
    BROWSE_MODE_FLAT,
    SessionState,
    WindowGeometry,
    load_session,
    save_session,
)
from gallery.core.library.watcher import ChangeKind, LibraryChange, summarize
from gallery.core.metadata.exif import ExifToolReader, MetadataReport, read_metadata
from gallery.core.metadata.facets import (
    GROUP_FACETS,
    SORT_FACETS,
    Facet,
    PhotoFacets,
    group_facet,
    needs_metadata,
    sort_facet,
)
from gallery.core.places.countries import place_label
from gallery.core.places.dataset import is_real_coordinate
from gallery.core.places.resolver import LazyPlaceResolver
from gallery.ui import desktop, menu_style, theme
from gallery.ui.banner_overlay import AUTO_HIDE_SECONDS as BANNER_AUTO_HIDE_SECONDS
from gallery.ui.banner_overlay import BannerOverlay
from gallery.ui.browser.grid import PREFETCH_MARGIN, PhotoGrid
from gallery.ui.browser.model import PhotoListModel
from gallery.ui.browser.thumb_sizes import (
    THUMBNAIL_SIZES,
    edge_for_index,
    label_for_index,
    nearest_index,
    shifted_index,
)
from gallery.ui.imaging import to_qimage
from gallery.ui.menus import MenuActions, TargetKind, build_menu
from gallery.ui.navigation import (
    ENTRY_KIND_PATH,
    EntryKey,
    LevelKey,
    breadcrumb_parts,
    entry_key,
    level_key,
    parent_of,
    path_key,
    row_of_entry_key,
)
from gallery.ui.panels.breadcrumb import BreadcrumbBar
from gallery.ui.panels.metadata import MetadataPanel
from gallery.ui.panels.welcome import WelcomeView
from gallery.ui.search_field import HEIGHT as SEARCH_FIELD_HEIGHT
from gallery.ui.search_field import MARGIN_RIGHT as SEARCH_FIELD_MARGIN_RIGHT
from gallery.ui.search_field import TEXT_ALIGN_DROP as SEARCH_FIELD_TEXT_ALIGN_DROP
from gallery.ui.search_field import WIDTH as SEARCH_FIELD_WIDTH
from gallery.ui.search_field import SearchField
from gallery.ui.tasks import TaskContext, TaskRunner
from gallery.ui.thumbs.pipeline import ThumbnailPipeline
from gallery.ui.title_bar import EXTRA_PADDING as TITLE_BAR_EXTRA
from gallery.ui.title_bar import HEIGHT as TITLE_BAR_HEIGHT
from gallery.ui.title_bar import TitleBar
from gallery.ui.viewer.detail import DetailView
from gallery.ui.watch import WatchBridge, WatchStarter, start_watch
from gallery.ui.write_ops import NOTHING_SELECTED, WriteOpsMixin, flattened_entries

logger = logging.getLogger(__name__)

#: 扫描时每积累多少张就送回主线程一次（流式出图，不等整库扫完）。
SCAN_BATCH_SIZE = 64

#: 大图预览在内存里保留几张（当前张前后各一张，切图才不会白屏）。
PREVIEW_CACHE_SIZE = 3

#: 元数据缓存条数。翻图时命中缓存就直接显示，没有中间态。
METADATA_CACHE_SIZE = 128

#: 预读前后各几张的元数据。一次读取约 50ms，预读了就等于切图时零等待。
METADATA_PREFETCH_RADIUS = 2

#: 预览图相对视口长边的倍率：留出余量，缩放一点也不会糊。
PREVIEW_EDGE_FACTOR = 1.5

#: 预览图长边的上下限（像素）。
MIN_PREVIEW_EDGE = 512
MAX_PREVIEW_EDGE = 4096

#: 自家写操作（改名 / 移到废纸篓 / 撤销）之后，监听器报回来的同一件事在这段时间内
#: 不再重复应用。给的是**够用**的时间而不是「等下一个事件」：漏掉一次回声会让同一行
#: 被改两遍，过期之后用户真的在访达里新建一个同名文件又该正常显示（ADR 0013）。
WATCH_ECHO_TTL = 2.0

#: 用户触发了一条横幅之后，后台消息让位这么久（秒）。
#:
#: 横幅只有一个槽，而用户刚做的事与后台的收尾常常同时发生：把照片移到废纸篓之后，
#: 网格会立刻去给留下来的那些行要缩略图，于是「刚被自家动过、这一刻读不到」的那一张
#: 解码失败会**迟一步**报上来，把「1 张没移走」整句盖掉。改名与撤销同理。
#:
#: 代价是用户刚操作完的两秒里，真正的解码失败不冒头；两秒后照常报，而那正是用户
#: 手上正有别的动静（滚动、切库）的时候，横幅会再被刷一次。
BANNER_STICKY_SECONDS = 2.0


def _context_kind(entry: GridEntry) -> TargetKind:
    """这一格是照片、相册还是分组 —— 右键菜单据此拼不同的一份（ADR 0014）。"""
    if isinstance(entry, GroupRef):
        return TargetKind.GROUP
    if isinstance(entry, FolderRef):
        return TargetKind.ALBUM
    return TargetKind.PHOTO


#: 离最近的城市超过这么多公里，就在地点后面补一句距离。
#:
#: 标签本身已经写了「（推断）」，内容里不再重复；只有偏得远时才补 —— 那是在提醒
#: 「这只是最近的城市」，不是在报误差。
PLACE_DISTANCE_NOTICE_KM = 50.0


def _place_text(facets: PhotoFacets) -> str:
    """索引里那一行的地点 → 面板上显示的一句话。

    拼装用 ``places.place_label``，与反查结果自己的 ``Place.label()`` 是同一个函数 ——
    面板和分组显示的必须是同一个说法（ADR 0010）。
    """
    label = place_label(facets.city, facets.region, facets.country)
    if not label:
        return "坐标附近没有城市记录"
    distance = facets.place_distance_km
    if distance is None or distance < PLACE_DISTANCE_NOTICE_KM:
        return label
    return f"{label}（最近城市约 {distance:.0f} 公里外）"


class MainWindow(WriteOpsMixin, QMainWindow):
    # ``WriteOpsMixin`` 带进来的是**会动用户文件的那些入口**（粘贴 / 新建相册 /
    # 复制与移动到相册 / 改名 / 移到废纸篓 / 撤销），连同它们共用的那个前置问题
    # 「现在动的是哪些东西」。红线 1 说「绝不删除、移动用户照片」—— 把它们收在一个
    # 文件里，那句话就从「靠人记」变成「可以 grep」（详见 ui/write_ops.py 的文档）。

    """应用主窗口。"""

    def __init__(
        self,
        *,
        session_base: Path | None = None,
        initial_library: Path | None = None,
        watch_starter: WatchStarter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_base = session_base
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
        #: 攒着没落地的变化（当时扫描在途、条目还在暂存区，或人正在大图页）。
        #: 条件合适时补做，见 :meth:`_flush_pending_watch`。
        self._watch_pending: tuple[LibraryChange, ...] | None = None
        #: 分组视图里来了新照片：重新分组之后要回到哪个组（ADR 0013）。
        self._pending_group: str | None = None
        #: 回到那个组时该选中哪一格（那一趟是软件自己绕出去的，见 :meth:`_reenter_group_later`）。
        self._pending_focus: EntryKey | None = None

        self._library: Library | None = None
        self._pipeline: ThumbnailPipeline | None = None
        self._generation = 0
        self._scan_job: int | None = None
        #: 选区变化后**攒一轮**再全量重算动作可用性（0ms 单次；理由见连接处）。
        #: 同步清掉是为了关窗时不留一个还排着队的定时器。
        self._action_states_timer = QTimer(self)
        self._detail_job: int | None = None
        self._current_row = -1
        self._metadata_job: int | None = None
        #: 「复制元数据」现读元数据用的那次任务（面板的读取消了就没人要那份结果）。
        self._metadata_copy_job: int | None = None
        #: 会往某个目录里**落文件**的后台任务（⌘V 粘贴、复制到相册、移动到相册，
        #: 见 ADR 0018 / 0019）。**三者同时只允许一个**：它们会争同一个目标名字，
        #: 后到的会把先到的盖掉 —— 那是这个项目最不能出现的一种事。
        #: 切库时必须取消（见 ``_teardown_library``），否则它会拿旧库的记录去问新库
        #: 「下一次 ⌘Z 撤什么」，于是记录明明记了、界面上却撤不掉。
        self._write_job: int | None = None
        #: 分组 / 排序与元数据面板共用的地点反查器（按坐标），首次用时才加载城市数据。
        self._place_resolver: LazyPlaceResolver | None = None
        #: 打开库时那一遍「全库解析地点」的任务（ADR 0010）。
        self._place_job: int | None = None
        #: 本次运行已经起过那一遍的库。**是集合不是单个**：来回切库不该把同一个库
        #: 再起一遍空跑（后来才加进来的照片由面板那条兜底路补，见 ``_maybe_lookup_place``）。
        self._place_pass_roots: set[Path] = set()
        #: 横幅上属于本任务的那句话（``None`` = 不占着横幅）。
        self._place_notice_text: str | None = None
        self._preview_cache: OrderedDict[Path, QImage] = OrderedDict()
        self._metadata_cache: OrderedDict[Path, tuple[int, int, MetadataReport]] = OrderedDict()
        self._thumbnail_failures = 0
        # 下一次 ⌘Z 会撤掉什么。日志只有写操作才会变，所以只在这几处刷新它 ——
        # 每次选中变化都重放一遍日志的话，用方向键翻图时白白读文件。
        self._undo_label: str | None = None

        self._size_index = nearest_index(self._session.thumbnail_edge)
        self._browse_mode: str = self._session.browse_mode or BROWSE_MODE_FLAT
        self._current_dir: Path | None = None
        # 分组 / 排序（见 ADR 0009）：字段 id 存进会话，下次打开沿用。
        # 从没选过排序字段的人（会话里那一项是空的）按默认排序字段走，见 session 模块。
        self._group_facet: str | None = self._session.group_facet
        self._sort_facet: str | None = self._session.resolved_sort_facet()
        self._sort_descending: bool = bool(self._session.sort_descending)
        #: 已经进入的分组（它不是磁盘上的目录，只是这一层的一个筛选项）。
        self._group_view: GroupRef | None = None
        #: 模型里此刻摆着的是哪一层。换层时用它分清「换了一层」（重新进入，选区清空）与
        #: 「还是这一层，重排一遍」（跟着停着的那一格走）—— ``enter_folder`` /
        #: ``enter_group`` 都是先改状态再重载，所以只能事后比一比，见 :meth:`_arm_focus`。
        self._level_key: LevelKey | None = None
        #: 这一层第一次排好时该选中的一格（``None`` = 谁也不选，见 :meth:`_arm_focus`）。
        self._focus: EntryKey | None = None
        #: 上面那一格摆出来了吗。一层只摆一次，免得跟用户后来的点选抢当前项。
        self._focus_restored = False
        #: 本层照片的分组 / 排序字段快照（按路径索引）。
        self._facet_values: dict[Path, PhotoFacets] = {}
        self._facet_job: int | None = None
        #: 这一层还没摆上屏的条目。扫出来的一律先攒在这里，顺序定下来再一次给出去
        #: （见 ADR 0009 决策 6）；``None`` 表示「模型就是当前这一层的全部」，
        #: 也就是这一层已经排好、正在被人看。
        self._staged: list[GridEntry] | None = None
        #: 横幅上正占着「这一层什么都没有」这句话；有了内容要把它收回去。
        self._level_notice = False
        #: 横幅上正占着「正在读取元数据…」这条进度。
        self._progress_notice = False
        #: 扫描本身出过问题（跳过的目录 / 读不了的文件）。它比「这里是空的」更该说。
        self._scan_issue = False
        self._exiftool_available: bool | None = None  # 探测一次就够
        # ---- 搜索（ADR 0026）-------------------------------------------------
        #: 已生效的筛选条件，按录入顺序。**空列表 = 不在搜索**（普通的一层）。
        self._conditions: list[SearchCondition] = []
        #: 正在搜索框里改的是第几条条件（``None`` = 没在改）。改完回车**就地替换**它。
        self._editing_condition: int | None = None
        #: 上一次检索命中了多少张（面包屑上那个「搜索结果（128）」；``None`` = 还没查过）。
        self._search_total: int | None = None
        self._search_job: int | None = None
        #: 「把整库的照片写进索引」那一遍的任务（见 ``_maybe_start_photo_index``）。
        self._index_job: int | None = None
        #: 已经起过那一遍的库。**是集合不是单个**：来回切库不该把同一个库再起一遍空跑。
        self._index_roots: set[Path] = set()
        #: 横幅上正占着「正在建立检索索引…」这条进度。
        self._index_notice_text: str | None = None
        #: 窗口顶边那一条是不是已经归我们了（见 ``theme.unify_title_bar``）。
        #: 没归我们（例如 Linux）就照旧用 ``setWindowTitle``，不摆 ``TitleBar``。
        self._owns_title_bar = False
        #: 不在大图页时窗口顶边那一行显示的名字（ADR 0025 决策 4）。
        self._library_title = "Gallery"
        self._model = PhotoListModel(self, icon_edge=edge_for_index(self._size_index))
        #: 「等上一段切档动画跑完再执行」的档数，见 :meth:`step_thumbnail_size`。
        #: 0 = 没有排着的。方向相反的连按会互相抵掉（连按 ⇧⌘↑ 再 ⇧⌘↓ 等于没按）。
        self._pending_size_step = 0
        self._build_ui()
        self._build_actions()
        self._restore_session()
        self._sync_order_actions()
        self._metadata_action.setChecked(bool(self._session.metadata_visible))
        self._panel.setVisible(bool(self._session.metadata_visible))
        self._sync_size_actions()
        self._update_action_states()

        if initial_library is not None:
            self.open_library(initial_library)
        elif self._session.recent_roots and self._session.recent_roots[0].is_dir():
            # README 承诺过：下次启动回到上次打开的库。静默打开，失败就留在欢迎页。
            self.open_library(self._session.recent_roots[0], silent=True)

    # ---- 构建界面 ----------------------------------------------------------

    def _build_ui(self) -> None:
        self.resize(1280, 860)
        self.setMinimumSize(720, 480)

        # 先把底色统一好，下面每一个控件都从这一份取色：标题栏那一段、面包屑、网格视口、
        # 大图区与元数据面板从此是同一块底色（见 ui.theme）。
        theme.ensure_installed()
        # 标题栏那一段交给窗口自己画，否则 macOS 会铺一层半透明材质，和下面的底色差一档。
        # 它同时回答「那一段归不归我们」：归了就得连**标题文字**也自己画，否则系统那份
        # 会与 TitleBar 叠在同一处（ADR 0025 决策 1）。
        self._owns_title_bar = theme.unify_title_bar(self)

        self._title_bar = TitleBar(self)
        self._title_bar.rename_committed.connect(self._on_title_rename_committed)
        self._title_bar.rename_cancelled.connect(self._title_bar.stop_edit)
        # 改到一半 Esc 归输入框、会动文件的命令一起让开 —— 与网格那个输入框同一件事
        # （网格那边是 ``rename_active_changed``，这里是对应的另一半，ADR 0025 决策 6）。
        self._title_bar.edit_active_changed.connect(self._on_rename_active_changed)
        if self._owns_title_bar:
            # 标题文字改由 ``TitleBar`` 画，系统那份必须空着 ——
            # 两份标题叠在同一处是最难看的失败方式（ADR 0025 决策 1）。
            self.setWindowTitle("")
        # 位置与「现在在哪一页」都还没定下来（``_layout_title_bar`` / ``showEvent``），
        # 这里只是别让它在窗口还没建好时就抢着显示：``_refresh_title`` 问的是页面栈。
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
        # 「重命名」「移到废纸篓」能不能用，取决于当前有没有选中东西。
        #
        # **只接 `selectionChanged`，并且攒一轮再算。** Qt 一次用户动作会先发
        # `currentChanged` 再发 `selectionChanged`（点一格、Shift 连选一段都是），
        # 两条都接就是同一个动作算两遍。而一次算就是十八枚动作全量重算，
        # 其中 ⌘V 那一条还要**同步读一次系统剪贴板** —— 框里拖选三百行，
        # 就是几百次全量重算加几百次剪贴板往返，全在主线程上。
        #
        # 那个 0 毫秒的定时器把一串连发攒成一次：它不是「等一会儿再算」，而是
        # 「这一轮事件循环转完就算」，所以用户感觉不到延迟。
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
        # 标题文字与 ⌘R 的输入框都要给右上角那块让出来，否则长相册名会从它底下穿过去。
        self._title_bar.reserve_right(SEARCH_FIELD_WIDTH + 2 * SEARCH_FIELD_MARGIN_RIGHT)

        # **横幅不进这个布局**（ADR 0029 决策 1）：它在竖排里占一行，于是每一条消息
        # 都把网格往下挤一段。控件本体与摆位在 ``_build_ui`` 的最后一步建。
        self._grid_page = QWidget()
        grid_layout = QVBoxLayout(self._grid_page)
        grid_layout.setContentsMargins(10, 10, 10, 0)
        grid_layout.setSpacing(8)
        grid_layout.addWidget(self._breadcrumb)
        grid_layout.addWidget(self._grid, 1)

        self._detail = DetailView()
        self._detail.request_close.connect(self.show_grid)
        self._detail.request_step.connect(self.step_detail)
        # 大图页上的右键：视图只报位置，菜单在这一侧拼（与网格同一个流程，见 ADR 0021）。
        self._detail.context_requested.connect(self._show_detail_context_menu)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._grid_page)
        self._panel = MetadataPanel()
        self._panel.hide()
        self._detail_page = QWidget()
        detail_layout = QHBoxLayout(self._detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_layout.addWidget(self._detail, 1)
        detail_layout.addWidget(self._panel)
        self._stack.addWidget(self._detail_page)
        self.setCentralWidget(self._stack)
        # 内容区往下让出 :data:`TITLE_BAR_EXTRA`（标题栏那一条加高的那一段）。
        # **不是把那一段画在内容上，而是真的给它腾出地方**：加边距之后三页的内容
        # 都从 ``安全区 + EXTRA`` 开始，胶囊与标题文字因此可以摆在多出来的那一段里，
        # 不会像之前那样压住照片区的顶边（ADR 0026 决策 8）。
        if self._owns_title_bar:
            self._stack.setContentsMargins(0, TITLE_BAR_EXTRA, 0, 0)

        # 搜索框**最后建**：它要压在中央控件之上（候选列表画在窗口内容那一段，
        # 而 Qt 按创建顺序叠兄弟控件 —— 先建的那个被后建的盖住）。
        # 摆位同样由 ``_layout_title_bar`` 负责，那里先算高度再决定要不要摆它。
        self._search_field = SearchField(self)
        self._search_field.submitted.connect(self._on_search_submitted)
        self._search_field.dismissed.connect(self._on_search_dismissed)
        self._search_field.editing_changed.connect(self._on_search_active_changed)
        self._search_field.hide()

        # 横幅**也建在最后**：它是压在中央控件之上的一层，与搜索胶囊同一手法（Qt 按创建
        # 顺序叠兄弟控件，先建的被后建的盖住）。差别是它**不进任何布局** —— 出现与消失
        # 都不动底下任何一样东西（ADR 0029 决策 1），摆位由 ``_layout_banner`` 负责。
        self._banner = BannerOverlay(self)
        # 换页时重新摆：大图页上照片区右边让给元数据面板，居中的位置也就跟着变了。
        self._stack.currentChanged.connect(self._on_page_changed)

        # 标题栏那一行第一次有话可说：还没打开库，所以是「Gallery」（ADR 0025 决策 4）。
        self._refresh_title()
        # 刻意不建状态栏：底部那一条只是为了放扫描进度、照片尺寸、缩放百分比，
        # 换来的是照片区少 20 来像素。出错信息有横幅，提示性信息一律不留 —— 横幅浮在
        # 内容上面，压根不占照片区的那一行（ADR 0029 决策 1）。

    def _build_actions(self) -> None:
        self._open_action = QAction("打开文件夹…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self.choose_folder)

        self._back_action = QAction("返回网格", self)
        self._back_action.setShortcut(QKeySequence("Esc"))
        self._back_action.triggered.connect(self.show_grid)

        self._fit_action = QAction("适应窗口", self)
        self._fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self._fit_action.triggered.connect(self._detail.fit_to_window)

        self._actual_action = QAction("实际大小", self)
        self._actual_action.setShortcut(QKeySequence("Ctrl+1"))
        self._actual_action.triggered.connect(self._detail.actual_size)

        self._zoom_in_action = QAction("放大", self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(self._detail.zoom_in)

        self._zoom_out_action = QAction("缩小", self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(self._detail.zoom_out)

        # ⇧⌘↑ / ⇧⌘↓：缩略图放大 / 缩小**一档**。捏合与 ⌘+滚轮已经能做同一件事，
        # 但那两条要手正好搭在触控板上；这两条给的是键盘那一路，握着鼠标也能一档一档挪。
        #
        # 为什么不挂 ⌘↑ / ⌘↓：上面那条「上一级」的注释已经说明 ⌘↑ 在 macOS 上被占着。
        # 而 ⌘+ / ⌘- 归大图的缩放（StandardKey.ZoomIn/ZoomOut），网格里换档不能去抢它 ——
        # 两者动的是**不同的东西**（照片的缩放倍率 vs 缩略图的档位）。
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
        # **显式挂到窗口上**（与 ``_new_album_action`` / ``_reveal_action`` 同一个做法）：
        # QAction 只有被加进某个控件才会注册进 Qt 的快捷键表。它们同时进了「视图」菜单
        # （见下面那两行 ``view_menu.addAction``），本来也能注册 —— 但快捷键的归属不该
        # 取决于「那一份菜单还在不在」，挂在窗口上才是它自己的保证。
        self.addAction(self._thumb_zoom_in_action)
        self.addAction(self._thumb_zoom_out_action)

        # 回上一层用 Esc：⌘↑ 在 macOS 上已经被占着，我们抢不到。
        # Esc 在大图页是「返回网格」，在网格页才是「上一级」——同一时刻只有一个主人
        # （见 _update_navigation_actions），两处不会互相抢键。
        self._up_action = QAction("上一级", self)
        self._up_action.setShortcut(QKeySequence("Esc"))
        self._up_action.triggered.connect(self.go_up)

        self._albums_action = QAction("相册（按文件夹）", self)
        self._albums_action.setCheckable(True)
        self._albums_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_ALBUMS))

        self._flat_action = QAction("全部照片", self)
        self._flat_action.setCheckable(True)
        self._flat_action.triggered.connect(lambda: self.set_browse_mode(BROWSE_MODE_FLAT))

        self._metadata_action = QAction("显示元数据信息", self)
        self._metadata_action.setCheckable(True)
        self._metadata_action.setShortcut(QKeySequence("Ctrl+I"))
        self._metadata_action.toggled.connect(self.toggle_metadata)

        self._clear_cache_action = QAction("清空缩略图缓存", self)
        self._clear_cache_action.triggered.connect(self.clear_thumbnail_cache)

        # ⇧⌘N：与访达一致，在当前这一层里建一个相册（ADR 0022）。建完立刻进行内重命名，
        # 因此**不进后台线程** —— 那是一次 mkdir，不是搬几百兆的复制。
        self._new_album_action = QAction("新建相册", self)
        self._new_album_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_album_action.triggered.connect(self.create_album)
        # **必须挂到窗口上**（理由见 ``_reveal_action`` 那段）：QAction 的快捷键只有在
        # 它被加进某个控件之后才注册进 Qt 的快捷键表，而右键菜单是用完即弃的。
        # 挂窗口不会让它冒到菜单栏里 —— 菜单栏只画被塞进某个 ``QMenu`` 的动作。
        self.addAction(self._new_album_action)

        self._rename_action = QAction("重命名…", self)
        self._rename_action.setShortcut(QKeySequence("Ctrl+R"))
        self._rename_action.triggered.connect(self.rename_current_entry)

        # ⌘⌫：与访达一致，直接把选中的若干项移进废纸篓，不弹确认框（见 ADR 0008）。
        # Qt 在 macOS 上把 Command 写作 Ctrl；StandardKey.Delete 指的是前向删除键
        # （⌘⌦），不是访达那个「移到废纸篓」的 ⌘⌫，所以这里显式写出来。
        self._trash_action = QAction("移到废纸篓", self)
        self._trash_action.setShortcut(QKeySequence("Ctrl+Backspace"))
        self._trash_action.triggered.connect(self.trash_selected)

        # ⌘Z 撤的是这一摞里的栈顶，改名与移到废纸篓共用（core.metadata.undo）。
        # 文案跟着栈顶变，用户按之前就知道会撤掉什么。它住在「编辑」菜单里 ——
        # 那是 macOS 放撤销的标准位置，见 ADR 0014 决策 5。
        self._undo_action = QAction("撤销", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.triggered.connect(self.undo_last_write)

        # ---- 菜单栏与快捷键（见 ADR 0014）--------------------------------
        # 「打开」刻意不给**真**快捷键：⌘O 已经是「打开文件夹…」，而回车网格自己已经处理
        # 了（见 PhotoGrid.keyPressEvent），抢过来只会和它打架。
        #
        # 菜单里那枚「回车」是 ui.menus 挂在**这一次弹出的菜单**上的提示，只画不生效
        # （ShortcutColumnMenu.show_key），这个共用的 QAction 一个字节都没动。
        self._open_entry_action = QAction("打开", self)
        self._open_entry_action.triggered.connect(self.open_current_entry)

        # 「退出」只活在**大图页的右键菜单**里（ADR 0021），是那张菜单的第一行。
        # 与「打开」一样**不给真快捷键**：大图页上的 Esc 归 DetailView.keyPressEvent，
        # 菜单里那枚 ⎋ 是 ui.menus 挂在这一次菜单上的提示，只画不生效。
        self._close_entry_action = QAction("退出", self)
        self._close_entry_action.triggered.connect(self.show_grid)

        # ⇧⏎：与 ⌘⏎ 同一个思路再加一层 —— ⏎ 归「打开」、⌘⏎ 归「在访达中显示」，
        # ⇧⏎ 就归「在默认应用中打开」。三个「打开」因此挨着摆，菜单里画的也是同一列。
        #
        # 与「打开」一样**只活在右键菜单里**：它离不开「当前这一张」，而菜单栏上放一个
        # 没有选区可依的命令没有意义（见下面 ``_copy_metadata_action`` 那段）。
        self._open_default_action = QAction("在默认应用中打开", self)
        self._open_default_action.setShortcut(QKeySequence("Shift+Return"))
        self._open_default_action.triggered.connect(self.open_current_in_default_app)
        # 同样**必须挂到窗口上**才按得出来（理由见 ``_reveal_action`` 那段）。
        self.addAction(self._open_default_action)

        # ⌘⏎：⏎ 归「打开」自己（网格里已经处理了），加上 ⌘ 正好空出来一条，与访达把
        # ⌘C / ⇧⌘C 让给拷贝是一个思路。这条**只作用在网格里**（置灰判据见
        # ``_update_action_states``），所以 ⌘⏎ 在大图页按下去仍然只是回车。
        self._reveal_action = QAction("在访达中显示", self)
        self._reveal_action.setShortcut(QKeySequence("Ctrl+Return"))
        self._reveal_action.triggered.connect(self.reveal_selection)
        # **必须挂到窗口上**（``QWidget.addAction``）这个快捷键才真的按得出来：QAction 的
        # 快捷键只有在它被加进某个控件之后才注册进 Qt 的快捷键表，光给它一个 QAction 父对象
        # 不算数（实测：那样按 ⌘⏎ 什么都不发生）。而右键菜单是用完即弃的，靠它注册等于没注册。
        #
        # 挂窗口**不会**让它冒到菜单栏里：菜单栏只画被塞进某个 ``QMenu`` 的动作。
        self.addAction(self._reveal_action)

        # ⌘C 与访达一致：往剪贴板里放的是**文件本身**，访达里 ⌘V 得到一份拷贝
        # （ADR 0017）。注意放的是路径引用，不读像素、不写文件。
        self._copy_action = QAction("拷贝", self)
        self._copy_action.setShortcut(QKeySequence("Ctrl+C"))
        self._copy_action.triggered.connect(self.copy_selected_files)

        # ⇧⌘C 与访达一致。注意 Qt 在 macOS 上把 Command 写作 Ctrl，不要手动换键。
        self._copy_path_action = QAction("拷贝路径", self)
        self._copy_path_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_path_action.triggered.connect(self.copy_selected_paths)

        # ⌘V 与访达一致：把剪贴板里的文件**复制**进当前这一层的目录（ADR 0018）。
        # 它是这三个写操作里唯一不进撤销栈的 —— 撤销粘贴就是删文件（红线 1）。
        #
        # **相册菜单里那一条也是它**（ADR 0018 决策 7）：那一行因此照常写着 ⌘V，而且
        # **按 ⌘V 与点它走的是同一个动作、同一个槽** —— 界面上写着什么，按下去就是什么，
        # 不会有两处说法。粘到哪里由 :meth:`_paste_target` 说了算（选中一个相册就粘进那个
        # 相册，否则粘进当前这一层），右键那一格本来就会把它选中。
        self._paste_action = QAction("粘贴", self)
        self._paste_action.setShortcut(QKeySequence("Ctrl+V"))
        self._paste_action.triggered.connect(self.paste_into_current_folder)
        # 剪贴板一变就重算 ⌘V：在访达里 ⌘C 之后切回 Gallery 直接按 ⌘V，得有反应
        # （不然它还停在「剪贴板是空的」那一次算出来的置灰上）。连接随窗口析构自动
        # 摘掉，见 ``closeEvent`` 那段注释。
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

        self._select_all_action = QAction("全选", self)
        self._select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        self._select_all_action.triggered.connect(self.select_all_entries)

        # 只活在右键菜单里的两条：它们离不开「当前这一张」，放进菜单栏反而没处可用。
        self._copy_metadata_action = QAction("复制元数据", self)
        self._copy_metadata_action.triggered.connect(self.copy_current_metadata)

        self._copy_group_action = QAction("复制分组条件", self)
        self._copy_group_action.triggered.connect(self.copy_current_group)

        # 「设为相册封面」/「取消相册封面」（ADR 0028）：同样只活在右键菜单里，理由与
        # 上面那两条一模一样 —— 都要「当前这一格」才有对象（前者是一张照片，后者是一个
        # 相册）。**刻意不给真快捷键**：⇧⌘C 已经是「拷贝路径」，而这两条一年也用不到
        # 几次，为它抢一个常用键位不值当。
        self._set_cover_action = QAction("设为相册封面", self)
        self._set_cover_action.triggered.connect(self.set_selected_as_album_cover)

        self._clear_cover_action = QAction("取消相册封面", self)
        self._clear_cover_action.triggered.connect(self.clear_album_cover)

        self._quit_action = QAction("退出 Gallery", self)
        self._quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._quit_action.triggered.connect(self.close)

        # ⌘F 与访达一致：不是弹一个对话框，是**把光标放进标题栏那个胶囊**。
        # 检索的那一套全在窗口顶上（ADR 0026），弹窗等于把界面挪到别处去。
        self._search_action = QAction("搜索…", self)
        self._search_action.setShortcut(QKeySequence("Ctrl+F"))
        self._search_action.triggered.connect(self.focus_search)
        # **必须挂到窗口上**（理由见 ``_reveal_action`` 那段）才按得出来。
        self.addAction(self._search_action)

        # 条件是临时的，但「我刚才搜的那一串」不该靠记忆留着 —— 一条命令清光它。
        # 没有条件时整条**不出现**（不是置灰）：菜单里那个位置平时就该是空的。
        self._clear_search_action = QAction("清除搜索条件", self)
        self._clear_search_action.triggered.connect(self.clear_search_conditions)

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self._open_action)
        self._recent_menu = file_menu.addMenu("最近打开")
        file_menu.addSeparator()
        # 「搜索…」单独成一段：它是窗口顶上那一个胶囊的入口，与「往库里放东西」
        # （新建相册、改名、移到废纸篓）不是一族。
        file_menu.addAction(self._search_action)
        # 「清除搜索条件」没有条件时**整条不出现**（``QAction.setVisible`` 就是
        # 「不进菜单」），与 ADR 0014 决策 4 那条取向一致：不该出现的一条摆着置灰，
        # 只会让人以为它此刻还能点什么。
        self._clear_search_action.setVisible(False)
        file_menu.addAction(self._clear_search_action)
        file_menu.addSeparator()
        file_menu.addAction(self._new_album_action)
        file_menu.addAction(self._rename_action)
        file_menu.addAction(self._trash_action)
        file_menu.addSeparator()
        file_menu.addAction(self._clear_cache_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        # 「编辑」：全选、拷贝、拷贝路径、粘贴、撤销 —— 与 macOS 的摆放一致
        # （ADR 0014 决策 5）。⌘Z 从「文件」搬过来了：撤销是编辑动作，放在文件菜单里
        # 会让 ⌘Z 与「打开 / 移到废纸篓」这些会动文件的命令混在一起。
        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self._select_all_action)
        edit_menu.addAction(self._copy_action)
        edit_menu.addAction(self._copy_path_action)
        edit_menu.addAction(self._paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self._undo_action)

        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self._back_action)
        view_menu.addAction(self._up_action)
        view_menu.addSeparator()
        browse_menu = view_menu.addMenu("浏览方式")
        browse_menu.addAction(self._albums_action)
        browse_menu.addAction(self._flat_action)
        self._build_order_menus(view_menu)
        view_menu.addSeparator()
        view_menu.addAction(self._fit_action)
        view_menu.addAction(self._actual_action)
        view_menu.addSeparator()
        view_menu.addAction(self._zoom_in_action)
        view_menu.addAction(self._zoom_out_action)
        view_menu.addSeparator()
        view_menu.addAction(self._metadata_action)
        view_menu.addSeparator()
        # 「放大 / 缩小缩略图」与「缩略图大小」是同一件事的两种说法：前两条挪一档，
        # 子菜单直接挑一档。挨着摆，用户按 ⇧⌘↑ 时不用先想「这条在哪一栏」。
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

        # 不加工具栏：这些命令在 macOS 顶部菜单里都有，快捷键也照常生效，
        # 界面上再重复一遍只会挤掉照片。
        self._rebuild_recent_menu()
        # 补发一次底色。菜单栏里这些 QMenu 是**此刻**才建出来的，而统一底色是在
        # ``_build_ui`` 里下发的（见 ``ui.theme``）—— 那时它们还不存在，于是各自
        # 用着系统菜单色（浅色 ``#ececec`` / 深色 ``#2b2b2b``），与窗口的纯色底差
        # 一档。按下菜单的那一瞬，割裂就是这么来的。
        #
        # 它们是菜单栏的子控件，重新下发就会走到它们；而这一发也让之后每一次
        # 系统换浅色 / 深色时，它们的底色跟着窗口一起变。
        theme.ensure_installed()

    # ---- 分组与排序（见 ADR 0009）--------------------------------------------

    def _build_order_menus(self, view_menu: QMenu) -> None:
        """「视图 → 分组 / 排序」两个子菜单。

        放菜单而不是窗口上的控件：这个项目一直刻意不加工具栏，照片区那点高度不该
        拿来放两个下拉。
        """
        group_menu = view_menu.addMenu("分组")
        group_menu.setToolTipsVisible(True)
        self._group_actions: dict[str | None, QAction] = {
            None: self._order_choice(group_menu, "不分组", None, self.set_group_facet)
        }
        for facet in GROUP_FACETS:
            self._group_actions[facet.id] = self._order_choice(
                group_menu,
                facet.label,
                facet.id,
                self.set_group_facet,
                unavailable=self._unavailable_hint(facet),
            )

        sort_menu = view_menu.addMenu("排序")
        sort_menu.setToolTipsVisible(True)
        # 菜单里**没有「不排序」**：扫描顺序是内部实现（相册在前、按名称），把它摆成一个
        # 选项等于承认「不按任何字段排」也是用户的正当选择 —— 那条路只会让人在两种
        # 看不懂的顺序之间来回试。真的要按名字看，「文件名」那一项摆在那儿。
        self._sort_actions: dict[str, QAction] = {}
        for facet in SORT_FACETS:
            self._sort_actions[facet.id] = self._order_choice(
                sort_menu,
                facet.label,
                facet.id,
                self.set_sort_facet,
                unavailable=self._unavailable_hint(facet),
            )
        sort_menu.addSeparator()
        self._descending_action = QAction("逆序", self)
        self._descending_action.setCheckable(True)
        self._descending_action.toggled.connect(self.set_sort_descending)
        sort_menu.addAction(self._descending_action)

    def _order_choice(
        self,
        menu: QMenu,
        label: str,
        facet_id: str | None,
        handler: Callable[[str | None], None],
        *,
        unavailable: str | None = None,
    ) -> QAction:
        """菜单里的一个单选项。``unavailable`` 非空时置灰并说明原因。"""
        action = QAction(label, self)
        action.setCheckable(True)
        if unavailable is not None:
            action.setEnabled(False)
            action.setToolTip(unavailable)
        else:
            action.triggered.connect(lambda _checked=False, target=facet_id: handler(target))
        menu.addAction(action)
        return action

    def _unavailable_hint(self, facet: Facet) -> str | None:
        """没装 exiftool 时，只有 exiftool 读得到的那些字段要**明说**为什么用不了。

        降级可以，假装没降级不行（见 ``core.metadata.exif``）：让用户分出一个
        「全都没有」的组，比一开始就告诉他「这个字段要装 exiftool」糟糕得多。
        """
        if not facet.needs_exiftool or self._has_exiftool:
            return None
        return "需要 exiftool 才能读取这个字段。安装后即可使用：brew install exiftool"

    @property
    def _has_exiftool(self) -> bool:
        """exiftool 现在能不能用。启动时探一次，之后不再反复起进程去问。"""
        if self._exiftool_available is None:
            self._exiftool_available = ExifToolReader().available()
        return self._exiftool_available

    def set_group_facet(self, facet_id: str | None) -> None:
        """切换分组字段（菜单用）。"""
        if facet_id is not None and group_facet(facet_id) is None:
            return  # 非法 id：会话文件里的旧值也可能走到这里，静默忽略
        if facet_id == self._group_facet:
            self._sync_order_actions()
            return
        self._group_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        # 换分组字段等于换了一组组卡片，原来进入的那个组已经不存在了。
        self._group_view = None
        self._reload_level()

    def set_sort_facet(self, facet_id: str | None) -> None:
        """切换排序字段（菜单用）。组内排序也跟着换 —— 这正是它的用处。"""
        if facet_id is None or sort_facet(facet_id) is None:
            return  # 没有「不排序」这个选择；非法 id 只可能来自旧会话，静默忽略
        if facet_id == self._sort_facet:
            self._sync_order_actions()
            return
        self._sort_facet = facet_id
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def set_sort_descending(self, descending: bool) -> None:
        """切换正序 / 逆序。只重排当前这一层，不必重新读元数据。"""
        if descending == self._sort_descending:
            return
        self._sort_descending = descending
        self._remember_ordering()
        self._sync_order_actions()
        self._reload_level()

    def _remember_ordering(self) -> None:
        self._session = self._session.with_ordering(
            self._group_facet, self._sort_facet, self._sort_descending
        )
        self._save_session()

    def _sync_order_actions(self) -> None:
        """让菜单的勾选状态跟着实际选择走（含从会话恢复出来的选择）。"""
        for facet_id, action in self._group_actions.items():
            action.setChecked(facet_id == self._group_facet)
        for facet_id, action in self._sort_actions.items():
            action.setChecked(facet_id == self._sort_facet)
        self._descending_action.setChecked(self._sort_descending)

    def _current_order(self) -> SortOrder:
        return SortOrder(facet=sort_facet(self._sort_facet), descending=self._sort_descending)

    def _current_group_facet(self) -> Facet | None:
        return group_facet(self._group_facet)

    def _needs_facet_read(self) -> bool:
        """当前的选择是否必须真去读 EXIF 才能算出顺序。"""
        group = None if self._group_view is not None else self._current_group_facet()
        return needs_metadata((group, sort_facet(self._sort_facet)))

    # ---- 进入与退出分组 ------------------------------------------------------

    def enter_group(self, group: GroupRef, *, focus: EntryKey | None = None) -> None:
        """进入一个分组（双击组卡片）。

        分组不是磁盘上的目录，因此不走 ``enter_folder``：它记不住路径，退出时
        直接回到进来之前的那一层。

        :param focus: 到了这一层选中哪一格。只有软件自己为了重新分组绕出去再回来时
            才给（见 :meth:`_reenter_group_later`）；用户点进来的一律谁也不选。
        """
        self._group_view = group
        self._reload_level(focus=focus)

    def _leave_group(self) -> None:
        group = self._group_view
        if group is None:
            return
        self._group_view = None
        # 退回来的那一层选中这张组卡片：分组不是磁盘上的目录，退出时回不到「上一层目录」，
        # 只能由摆着它的这一层来收场（这一层摆的是组卡片，所以组卡片的标识正好对得上）。
        self._reload_level(focus=entry_key(group))

    # ---- 读取本层元数据并重排 ------------------------------------------------

    def _start_facet_read(self) -> None:
        """按当前选择给这一层补齐元数据，然后重排。

        只有真的要读 EXIF 的字段才会走到这里（按文件名排序之类是纯算的）。
        读过一次的结果落进索引（``core.library.index``），所以第二次换分组字段
        几乎立刻完成。
        """
        library = self._library
        if library is None or not self._needs_facet_read():
            self._apply_ordering()
            return
        # 暂存优先：刚扫完的这一层还在暂存区里，模型上一条也没有。
        entries = self._staged if self._staged is not None else self._model.entries()
        refs = [entry for entry in entries if isinstance(entry, PhotoRef)]
        if not refs:
            self._apply_ordering()
            return
        generation = self._generation

        # **索引里全都有就直接排**：这正是「第二次打开这个库」的样子，纯粹是一次查表加
        # 一次排序。起后台线程、报「正在读取 N 张照片的元数据」都是在说一件没发生的事 ——
        # 用户没法分辨是又在干活还是软件出了毛病（同 ``_maybe_start_place_pass`` 的理由）。
        cached = library.cached_facets(refs)
        if len(cached) == len(refs):
            self._facet_values = cached
            self._apply_ordering()
            return

        self._progress_notice = True
        self._show_banner(
            f"正在读取 {len(refs)} 张照片的元数据，用于{'分组' if self._group_facet else '排序'}…"
        )

        resolver = self._place_lookup()

        def work(context: TaskContext) -> object:
            return library.photo_facets(
                refs,
                should_cancel=context.is_cancelled,
                on_progress=lambda done, total: context.progress((done, total)),
                status=lambda text: context.progress(text),
                resolver=resolver,
            )

        self._facet_job = self._runner.submit(
            work,
            on_progress=partial(self._on_facet_progress, generation),
            on_result=partial(self._on_facets_ready, generation),
            on_error=partial(self._on_facets_failed, generation),
        )

    def _clear_progress_notice(self) -> None:
        """收掉「正在读取元数据…」那条 —— **只收自己那一条**：扫描出问题时那条横幅
        比进度重要得多，不能顺手一起藏了。"""
        if not self._progress_notice:
            return
        self._progress_notice = False
        self._banner.hide()

    def _update_level_notice(self) -> None:
        """这一层什么都没有时说一声；有了内容就把这句收回去。

        **判据是「网格上有没有条目」，不是「有没有相册 / 照片」**：分组会把照片换成
        一张张组卡片，只数相册和照片会把一屏组卡片（甚至一屏相册）误判成空 ——
        那正是这条提示曾经出现的地方。
        """
        if self._library is None:
            return
        if self._scan_issue:
            # 扫描出过问题时横幅归它：那句（哪些内容没读到）比「这里是空的」更该说，
            # 而这里也不许把它收掉 —— 收了等于把用户真正需要知道的事藏起来。
            return
        if self._model.rowCount():
            if self._level_notice:
                self._level_notice = False
                self._banner.hide()
            return
        self._level_notice = True
        if self._conditions:
            # 检索没命中时**不要说「没有找到可显示的照片，支持 JPEG…」**：
            # 那个格式清单与此刻发生的事毫无关系，用户会以为自己的文件格式不对。
            self._show_banner("这些筛选条件下没有照片。")
        elif self._group_view is not None:
            self._show_banner("这个分组里没有照片了。")
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._show_banner("这个文件夹里既没有子文件夹，也没有可显示的照片。")
        else:
            self._show_banner("没有找到可显示的照片。支持 JPEG、PNG、HEIC、TIFF、GIF、WebP、BMP。")

    def _cancel_facet_job(self) -> None:
        """作废在途的元数据读取（换库、换层、换分组字段时）。"""
        if self._facet_job is not None:
            self._runner.cancel(self._facet_job)
            self._facet_job = None

    def _place_lookup(self) -> LazyPlaceResolver:
        """共用的地点反查器：分组、元数据面板、打开库时那一遍都用这一个。

        城市库（解压后约 1.2 MB）只该读一次，所以三条路径共用同一个实例 ——
        反查器自己缓存着加载好的索引与查过的坐标。
        """
        if self._place_resolver is None:
            self._place_resolver = LazyPlaceResolver()
        return self._place_resolver

    def _on_facet_progress(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        if isinstance(payload, str):
            self._show_banner(payload)  # 「正在加载城市库…」这类说明
            return
        if not isinstance(payload, tuple):
            return
        done, total = payload if len(payload) == 2 else (0, 0)
        if isinstance(done, int) and isinstance(total, int) and total:
            self._show_banner(f"正在读取元数据…{done} / {total}")

    def _on_facets_ready(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._facet_values = payload if isinstance(payload, dict) else {}
        self._clear_progress_notice()
        self._apply_ordering()
        # 这一层读完了，才轮到整库那两遍（见 _maybe_start_place_pass 里的理由）。
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _on_facets_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._facet_job = None
        self._show_banner(f"读取元数据失败，按默认顺序显示：{message}")
        self._apply_ordering()
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    def _apply_ordering(self) -> None:
        """把当前这一层按分组 / 排序重排一次，然后一次性摆到网格上。"""
        if self._stack.currentWidget() is not self._grid_page:
            return
        library = self._library
        if library is None:
            return
        try:
            self._reorder_level(library)
        finally:
            # 顺序定下来了，这才有资格回答「这一层是不是空的」。
            self._update_level_notice()
            # 也才找得到「退上去时该停住的那一格」：组卡片本身就是重排出来的。
            self._restore_focus()
            # 分组视图里来了新照片时，这里是重新分组之后回到原处的地方（ADR 0013）。
            self._maybe_reenter_group()
            # 暂存区到这里才清空：攒着的变化从这一刻起才能安全地落到网格上。
            self._flush_pending_watch()

    def _reorder_level(self, library: Library) -> None:
        # 暂存的优先：那一层还没上屏，顺序就是这里算出来的。
        staged = self._staged
        entries = staged if staged is not None else list(self._model.entries())
        self._staged = None
        albums = [entry for entry in entries if isinstance(entry, FolderRef)]
        photos = [entry for entry in entries if isinstance(entry, PhotoRef)]
        # 相册卡片永远排在前面：先看文件夹、再看按元数据归拢出来的组，是既有心智。
        order = self._current_order()
        # 已经在分组里面了：那一层全是照片，再分一次组毫无意义（进去就出不来）。
        group = None if self._group_view is not None else self._current_group_facet()
        values = self._facet_values
        if group is not None:
            ordered: list[GridEntry] = [
                *albums,
                *build_groups(photos, facet=group, order=order, root=library.root, values=values),
            ]
        else:
            ordered = [*albums, *sort_photos(photos, order=order, root=library.root, values=values)]
        if staged is None and ordered == list(self._model.entries()):
            return  # 什么都没攒、顺序也没变：让整屏重画一次毫无意义
        selected = self._grid.selected_rows()
        anchor = self._model.photo_at(self._grid.current_row())
        self._model.reorder(ordered)
        if anchor is not None:
            row = self._model.row_of_path(anchor.path)
            if row >= 0:
                self._grid.select_row(row, ensure_visible=False)
        elif selected:
            self._grid.select_row(min(selected), ensure_visible=False)
        self._refresh_thumbnail_window()

    def _restore_session(self) -> None:
        self._welcome.set_recent(self._session.recent_roots)
        window = self._session.window
        if window is None:
            return
        self.setGeometry(window.x, window.y, window.width, window.height)
        if window.maximized:
            self.showMaximized()

    # ---- 打开库 ------------------------------------------------------------

    def choose_folder(self) -> None:
        """弹出系统文件夹选择框。"""
        start = str(self._library.root) if self._library is not None else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择照片文件夹", start)
        if chosen:
            self.open_library(Path(chosen))

    def open_library(self, root: Path, *, silent: bool = False) -> bool:
        """打开一个库。返回是否成功。

        :param silent: 启动时自动恢复上次的库时为 ``True``：不弹任何对话框，
            缓存一律放到系统目录，避免用户刚打开软件就被问问题。
        """
        root = Path(root)
        try:
            cache_in_library = self._resolve_cache_location(root, silent=silent)
        except GalleryError as exc:
            self._report_error(exc.message)
            return False
        if cache_in_library is None:
            return False
        try:
            library = Library.open(root, cache_in_library=cache_in_library)
        except GalleryError as exc:
            if not silent:
                self._report_error(exc.message)
            else:
                logger.info("自动打开上次的库失败：%s", exc.message)
            return False

        self._activate_library(library)
        return True

    def _resolve_cache_location(self, root: Path, *, silent: bool) -> bool | None:
        """决定缩略图缓存放在库内还是系统目录。

        返回 ``True`` 表示库内、``False`` 表示系统目录、``None`` 表示用户取消。
        """
        if paths.has_config_dir(root):
            return True
        if silent:
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("缓存放在哪里？")
        box.setText(f"「{root.name}」还没有 Gallery 的缓存目录。")
        box.setInformativeText(
            "缩略图缓存能让浏览快很多。\n\n"
            f"· 在库中缓存：在照片文件夹里创建 {paths.config_dir(root).name} 目录"
            "（随时可删，删除后会自动重建）\n"
            "· 缓存到系统目录：照片文件夹保持一个字节都不写入\n\n"
            "无论选哪个，Gallery 都不会移动或修改你的照片；"
            "只有你自己触发的重命名（⌘R）和移到废纸篓（⌘⌫）会动文件，"
            "两者都能用 ⌘Z 撤销。"
        )
        in_library = box.addButton("在库中缓存（推荐）", QMessageBox.ButtonRole.AcceptRole)
        system_cache = box.addButton("缓存到系统目录", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(in_library)
        box.exec()

        clicked = box.clickedButton()
        if clicked is in_library:
            # 这是全应用唯一会写入照片目录的动作，必须由用户刚刚的点击触发。
            prepare_library(root)
            return True
        if clicked is system_cache:
            return False
        return None

    def _activate_library(self, library: Library) -> None:
        self._teardown_library()
        self._library = library
        self._generation += 1
        self._thumbnail_failures = 0
        # 换一个库就是换一摞撤销栈，菜单里的「撤销」要立刻跟着换。
        self._refresh_undo_label()
        self._current_row = -1
        self._preview_cache.clear()
        self._metadata_cache.clear()
        self._facet_values = {}
        self._model.clear()

        self._pipeline = ThumbnailPipeline(library, self._runner, self)
        self._pipeline.ready.connect(self._on_thumbnail_ready)
        self._pipeline.failed.connect(self._on_thumbnail_failed)

        self._banner.hide()
        # 切库时真正清空大图区，别让上一个库的全分辨率副本一直占着内存。
        self._detail.clear_image()
        self._panel.clear()
        self._current_dir = library.root
        self._arm_focus()  # 打开库走的是自己那一套，不经过 _reload_level
        # 每一层都要重排（排序字段总是有的），所以扫出来的一律先攒着，等顺序定下来。
        self._staged = []
        self._level_notice = False
        self._progress_notice = False
        self._place_notice_text = None
        self._scan_issue = False
        self._update_breadcrumb()
        self._update_mode_actions()
        self._library_title = library.display_name
        self._stack.setCurrentWidget(self._grid_page)
        self._refresh_title()  # 换库之后标题栏回到相册名（ADR 0025 决策 4）
        self._update_action_states()
        self._remember(library.root)
        self._watch.start(library)
        self._start_scan()

    def _teardown_library(self) -> None:
        # 监听器第一刀：它还在跑的时候，下面就把库和索引都拆了。
        self._watch.stop()
        self._watch_echoes.clear()
        self._watch_pending = None
        self._pending_group = None
        self._pending_focus = None
        # 「到了这一层该选中哪一格」是**上一个库**留下的：换库就得跟着一起走，
        # 不然新库的那一层会按上一个库的路径去选中一格（那一格多半在新库里根本不存在，
        # 于是只是白找一趟；更糟的是它要是在新库里也碰巧存在，选中的就是另一张照片）。
        # 「现在摆的是哪一层」同样属于上一个库：不作废的话，新库的第一层会被当成
        # 「还是刚才那一层」，于是把上一库的选中原样搬过来。
        self._focus = None
        self._level_key = None
        self._cancel_facet_job()
        self._cancel_place_pass()
        # **写操作闸门也要收。** 搬几百 MB 的那一个 ⌘V / 「移动到相册」是在旧库的
        # ``Library`` 上跑的；不取消它就让它接着跑，而 ``self._library`` 此时已经指向
        # 新库了 —— 收尾时 ``_on_transfer_done`` 会去问**新库**「下一次 ⌘Z 撤什么」，
        # 于是那条记录明明记在旧库里、界面上却一个都撤不掉。用户在横幅里看到的
        # 恰恰是「⌘Z 可以撤销」，按下去什么也没发生。
        if self._write_job is not None:
            self._runner.cancel(self._write_job)
            self._write_job = None
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        if self._detail_job is not None:
            self._runner.cancel(self._detail_job)
            self._detail_job = None
        self._cancel_search()
        self._cancel_photo_index()
        if self._pipeline is not None:
            self._pipeline.shutdown()
            self._pipeline.deleteLater()
            self._pipeline = None
        if self._library is not None:
            self._library.close_index()
        self._library = None
        self._group_view = None
        self._facet_values = {}
        # 条件是**临时的**（ADR 0026 决策 9）：换库之后还留着上一座库的那几枚胶囊，
        # 用户看到的检索结果属于一个已经关掉的文件夹。
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        """按当前浏览方式列出内容：平铺模式递归整库，相册模式只列当前一层。

        已经进入某个分组时不再扫盘 —— 这一组是上一次分组算出来的，直接铺开即可。

        **有筛选条件时走检索那条路**（ADR 0026 决策 7）：结果是横跨整个库的，
        与当前在哪一层无关。
        """
        library = self._library
        if library is None:
            return
        if self._conditions:
            # 搜索优先于分组：分组那一族是「这一层里怎么归拢」，而检索结果是**一层**。
            self._start_search(library)
        elif self._group_view is not None:
            self._start_group_listing(library)
        elif self._browse_mode == BROWSE_MODE_ALBUMS:
            self._start_level_scan(library)
        else:
            self._start_flat_scan(library)

    def _start_group_listing(self, library: Library) -> None:
        """分组视图：铺开这个组里的照片，并按当前排序排好。"""
        generation = self._generation
        group = self._group_view
        assert group is not None  # 只在 _group_view 非空时调用
        photos = list(group.photos)
        if not photos:
            return
        ordered = sort_photos(
            photos,
            order=self._current_order(),
            root=library.root,
            values=self._facet_values,
        )

        def work(context: TaskContext) -> object:
            return tuple(ordered)

        self._scan_job = self._runner.submit(
            work,
            on_result=partial(self._on_group_listed, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _on_group_listed(self, generation: int, payload: object) -> None:
        """分组视图的照片进网格，然后按「扫描完成」那一套收尾。"""
        if generation != self._generation:
            return
        entries = payload if isinstance(payload, tuple) else ()
        if self._staged is None:
            self._model.append(entries)
        else:
            self._staged.extend(entries)
        self._on_scan_done(generation, ScanReport())

    def _start_level_scan(self, library: Library) -> None:
        """相册模式：列出当前层（相册在前，照片在后）。"""
        generation = self._generation
        report = ScanReport()
        directory = self._current_dir or library.root

        def work(context: TaskContext) -> object:
            batch: list[GridEntry] = []
            for entry in library.iter_entries(
                directory, should_cancel=context.is_cancelled, report=report
            ):
                batch.append(entry)
                if len(batch) >= SCAN_BATCH_SIZE:
                    context.progress(tuple(batch))
                    batch = []
            if batch:
                context.progress(tuple(batch))
            return report

        self._scan_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _start_flat_scan(self, library: Library) -> None:
        generation = self._generation
        report = ScanReport()

        def work(context: TaskContext) -> object:
            batch: list[PhotoRef] = []
            for ref in library.iter_photos(should_cancel=context.is_cancelled, report=report):
                batch.append(ref)
                if len(batch) >= SCAN_BATCH_SIZE:
                    context.progress(tuple(batch))
                    batch = []
            if batch:
                context.progress(tuple(batch))
            return report

        self._scan_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_scan_done, generation),
            on_error=partial(self._on_scan_failed, generation),
        )

    def _on_scan_batch(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        batch = payload if isinstance(payload, tuple) else ()
        if self._staged is not None:
            # 扫描期间一律先攒着：**边扫边出图在这里等于闪一下** —— 用户先看到一屏按
            # 扫描顺序排的照片，下一秒它们全变成排好序的（甚至全变成组卡片），看起来
            # 像界面自己乱了。攒到顺序定下来再一次给出去，中间那段宁可留白
            # （横幅会说明在读什么）。
            self._staged.extend(batch)
            return
        self._model.append(batch)
        self._refresh_thumbnail_window()

    def _on_scan_done(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        report = payload if isinstance(payload, ScanReport) else ScanReport()
        # 「扫了多少张」原本在状态条上，条子没了就不报了；出问题才值得打断用户。
        if report.has_issues:
            self._scan_issue = True
            self._show_banner(f"部分内容没能读取：{report.summary}")
        # 「这一层空不空」**不在这里判断**：要重排时条目还攒着没上屏（见 _staged），
        # 那时模型里一条也没有，照着它判断就会把一屏相册 / 组卡片误报成「空」。
        # 真正该由最终内容决定，所以交给 _apply_ordering → _update_level_notice。
        if self._watch_pending:
            # 扫描在途时收到过外部变化：流式扫描没法在半路上改（会插到旧位置、会插重）。
            # 干脆重来这一层，几十毫秒的事。
            self._watch_pending = None
            self._reload_level()
            return
        self._refresh_thumbnail_window()
        self._start_facet_read()
        self._maybe_start_place_pass()
        self._maybe_start_photo_index()

    # ---- 检索（ADR 0026）------------------------------------------------------

    def _start_search(self, library: Library) -> None:
        """按已生效的条件检索整个库，把命中的照片攒进这一层。

        **索引没建全时先把它建起来**（ADR 0026 决策 5）：否则用户看到的是「一条都没有」，
        而那不是答案，是索引还没建。那一遍跑完会自己重跑这次检索（见
        :meth:`_on_photo_index_done`），所以这一轮什么都不做就算数。
        """
        if library.indexed_photos() == 0:
            self._maybe_start_photo_index()
            return
        generation = self._generation
        conditions = tuple(self._conditions)

        def work(context: TaskContext) -> object:
            outcome = library.search_photos(conditions, limit=RESULT_LIMIT)
            refs = outcome.refs
            # 与扫描一样**分批送回**（``_on_scan_batch`` 直接吃这个形状）：结果可能有几千条，
            # 攒齐再一次给出去，中间那段宁可留白（横幅会说明在读什么）。
            for start in range(0, len(refs), SCAN_BATCH_SIZE):
                context.progress(tuple(refs[start : start + SCAN_BATCH_SIZE]))
            return outcome.total

        self._search_job = self._runner.submit(
            work,
            on_progress=partial(self._on_scan_batch, generation),
            on_result=partial(self._on_search_done, generation),
            on_error=partial(self._on_search_failed, generation),
        )

    def _on_search_done(self, generation: int, payload: object) -> None:
        """检索完了，**接着走与扫描完全相同的那段收尾**。

        facet 补齐、分组排序、缩略图预取、焦点恢复、空层提示、监听增量，一样都不少
        （ADR 0026 决策 7）—— 另写一条平行路径才是 bug 的来源。
        """
        if generation != self._generation:
            return
        self._search_job = None
        total = payload if isinstance(payload, int) else 0
        # 命中数变了要刷新面包屑：它就写在那枚「搜索结果（128）」里。
        if total != self._search_total or self._search_total is None:
            self._search_total = total
            self._update_breadcrumb()
        self._on_scan_done(generation, ScanReport())

    def _on_search_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._search_job = None
        self._search_total = 0
        self._update_breadcrumb()
        self._show_banner(f"检索没能完成：{message}")
        self._apply_ordering()  # 把这一层按「没有结果」收尾，界面不至于卡在空白上

    def _cancel_search(self) -> None:
        """作废在途的检索（换层、换库、关窗时）。"""
        if self._search_job is not None:
            self._runner.cancel(self._search_job)
            self._search_job = None

    def _on_search_submitted(self, raw: str) -> None:
        """搜索框里回车了：把这一串变成一条条件，然后重新列出这一层。

        三种「提交了但没东西可提交」，各自安静地退开：

        - 空串（回车了两下）；
        - 只敲了字段名（``#相机``）—— 那是补全，不是条件；
        - 值写坏了（``#ISO abc``）—— **这一种要说一句话**，用户以为条件生效了。
        """
        editing = self._editing_condition
        parsed = parse_input(raw)
        condition = None if parsed is None else make_condition(parsed)
        if condition is None:
            # **正在改的那一条被清空后回车 = 撤掉它**。这时输入框是空的，按回车没有别的
            # 意思，而「把这条删了」正是用户清空它想要的结果 —— 于是撤掉这一条，
            # 免得「点一下胶囊就没了」那种意外（改与撤必须是两个动作）。
            if parsed is None and editing is not None:
                self._on_condition_removed(editing)
                return
            self._warn_unusable_input(parsed)
            return
        if self._library is None:
            # 没有库的时候收下这条条件是**悄悄的**：面包屑那一行压根不显示，
            # 用户看不见自己敲了什么，换库时它又被清掉 —— 与其这样，
            # 不如说一句「还没有库可搜」。
            self._show_banner("先打开一个照片文件夹，再搜索。")
            return
        if editing is not None and 0 <= editing < len(self._conditions):
            # **就地替换**：改一条条件的结果仍然是那一条，位置也不变 —— 换成「删掉旧的、
            # 追加一条新的」的话，面包屑上的顺序会跳，用户看着像是多了一条。
            self._conditions[editing] = condition
        else:
            self._conditions.append(condition)
        self._editing_condition = None
        self._warn_metadata_without_exiftool(condition)
        self._apply_conditions()

    def _warn_unusable_input(self, parsed: object) -> None:
        """没提交成的一条输入，值不值得说一句。

        只说「值写坏了」的那一种（``#ISO abc``）：还在挑字段（``#相机``）不是毛病，
        空串也不是 —— 每一句多余的提示都在教用户忽略横幅。

        **具体理由由 :func:`gallery.core.library.search.unusable_reason` 说**：横幅是
        用户唯一能看见的反馈，而「要一个值」对 ``2024～2023`` 与 ``2024-13`` 是同一句话 ——
        两种毛病要改的地方不一样。
        """
        if not isinstance(parsed, ParsedInput) or parsed.selecting or parsed.field is None:
            return
        self._show_banner(
            f"「{parsed.field.label}」{unusable_reason(parsed.field, parsed.value)}"
            "不支持的说法不会变成一条筛选条件。"
        )

    def _warn_metadata_without_exiftool(self, condition: SearchCondition) -> None:
        """提交了一条要读 EXIF 才读得到的条件，而 exiftool 不在。

        与 :meth:`_unavailable_hint` 同一个取向：**降级可以，假装没降级不行**。
        搜「相机」一条都没有，用户只会以为自己的照片没写过相机。
        """
        if not self._has_exiftool or not condition.field.needs_metadata:
            return
        self._show_banner(
            f"没装 exiftool，「{condition.field.label}」这个字段读不出来"
            "（文件名、相册、格式、大小不受影响）。安装后即可使用：brew install exiftool"
        )

    def _on_search_dismissed(self) -> None:
        """搜索框里按 Esc 且里面是空的。

        正在改某一条的时候，``Esc`` 是**放弃这一次改动**（那一条原样留着）——
        否则「改到一半想溜」与「撤掉一条」是同一下，用户没法中途反悔。
        """
        if self._editing_condition is not None:
            self._cancel_condition_edit()
            return
        self.go_up()

    def _on_search_active_changed(self, _active: bool) -> None:
        """正在输入时别的命令让开 —— 与两个改名输入框同一件事（键盘归输入框）。"""
        self._update_action_states()

    def _on_condition_edited(self, position: int) -> None:
        """点面包屑上第 ``position`` 枚胶囊：把它放回输入框接着改。

        **不是就地改**：胶囊上改不了字（要打字），所以把原文放回搜索框，由用户敲完回车
        （:meth:`_on_search_submitted` 里就地替换那一条）。按钮上会标出「正在改这一条」。
        """
        if not 0 <= position < len(self._conditions):
            return
        self._editing_condition = position
        self._search_field.load(self._conditions[position].as_input)
        self._update_breadcrumb()

    def _cancel_condition_edit(self) -> None:
        """放弃这一次改动：胶囊原样留着，输入框清空。"""
        self._editing_condition = None
        self._search_field.stop_edit()
        self._update_breadcrumb()

    def _on_condition_removed(self, position: int) -> None:
        """撤掉第 ``position`` 枚条件胶囊（点它右端的 ✕，或改到一半清空后回车）。"""
        if not 0 <= position < len(self._conditions):
            return
        del self._conditions[position]
        # 序号会往前挪一位 —— 不跟着挪，正在改的那一条就变成了**另一条**。
        editing = self._editing_condition
        if editing == position:
            self._editing_condition = None
        elif editing is not None and editing > position:
            self._editing_condition = editing - 1
        self._apply_conditions()

    def clear_search_conditions(self) -> None:
        """「清除搜索条件」：一条不剩，回到普通的一层。"""
        if not self._conditions:
            return
        self._conditions.clear()
        self._editing_condition = None
        self._apply_conditions()

    def focus_search(self) -> None:
        """⌘F：把光标放进标题栏那个胶囊。"""
        if not self._owns_title_bar:
            return  # 标题栏不归我们（见 theme.unify_title_bar），那一侧没有搜索框
        self._search_field.focus_search()

    def _apply_conditions(self) -> None:
        """条件变了（加一条、撤一条、清空）之后重新列出这一层。"""
        self._search_total = None
        self._reload_level()

    # ---- 建立检索索引（ADR 0026 决策 5）---------------------------------------

    def _maybe_start_photo_index(self) -> None:
        """把整个库的照片写进索引的文件表，**只有真的有活干才起**。

        与 :meth:`_maybe_start_place_pass` 同一个套路（ADR 0010）：打开库时顺手填好，
        之后每次检索就只是一次查表。第一遍跑完之前检索会「什么都搜不到」——
        所以这一遍自己会在结束时把在等的检索重跑一次（见 :meth:`_on_photo_index_done`）。

        与地点那一遍**串行**：两边各自要遍历整棵子树，同时跑等于把磁盘读两遍。
        地点那遍先跑（它要起 exiftool 子进程，更贵）；这一遍等它结束再起。
        """
        library = self._library
        if (
            library is None
            or self._index_job is not None
            or library.root in self._index_roots
            or self._facet_job is not None
            or self._place_job is not None
            or self._scan_issue
        ):
            return
        self._index_roots.add(library.root)

        def work(context: TaskContext) -> object:
            return library.index_photos(
                should_cancel=context.is_cancelled,
                on_work=lambda count: context.progress(("work", count)),
                on_progress=lambda count: context.progress(count),
            )

        # 横幅**不在这儿亮**：已经建过索引的库这一遍是空跑，先亮等于在说一件没发生的事
        # （与 ``_maybe_start_place_pass`` 同一句理由）。
        self._index_job = self._runner.submit(
            work,
            on_progress=self._on_photo_index_progress,
            on_result=self._on_photo_index_done,
            on_error=self._on_photo_index_failed,
        )

    def _on_photo_index_progress(self, payload: object) -> None:
        if self._index_notice_text is None or self._scan_issue:
            return
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "work":
            self._show_index_notice(f"正在建立检索索引…{payload[1]} 张")
            return
        if isinstance(payload, int) and payload > 0:
            self._show_index_notice(f"正在建立检索索引…已处理 {payload} 张")

    def _on_photo_index_done(self, payload: object) -> None:
        self._index_job = None
        self._clear_index_notice()
        written = payload if isinstance(payload, int) else 0
        logger.info("检索索引：写入 %d 张", written)
        # **这里不按代次判断**：这一遍要回答的是「此刻有没有人在等检索」，
        # 而条件列表才是那一刻的真相。拿提交时的代次来比会漏掉最常见的情形 ——
        # 用户在索引维护**进行当中**提交了条件，那一下 ``_reload_level`` 已经把代次 +1，
        # 于是这次检索永远不会被补跑，用户看到的是一个空结果（实测并行下会随机变红）。
        #
        # 切库时那一遍会被取消、条件也一并清掉（``_teardown_library``），
        # 所以「此刻的条件」不可能属于一个已经关掉的文件夹。
        if self._conditions:
            self._apply_conditions()

    def _on_photo_index_failed(self, message: str) -> None:
        self._index_job = None
        self._clear_index_notice()
        # 建不了索引不该让浏览整个不可用，只是检索做不了。**把库名从「起过」里拿掉**，
        # 下次换回这个库还能再试一次 —— 否则一次失败就把这个库的检索永久关掉了。
        self._forget_indexed_root()
        logger.info("建立检索索引失败：%s", message)
        if self._conditions:
            self._show_banner("这个库没法建立检索索引，搜索暂时用不了。")

    def _cancel_photo_index(self) -> None:
        """作废在途的索引维护（切库、关窗时）。"""
        if self._index_job is not None:
            self._runner.cancel(self._index_job)
            self._index_job = None
            # 半途取消等于这一遍**没做完**：记在 ``_index_roots`` 里它就再也起不来了，
            # 而下一次切回这个库的用户看到的是「搜不到任何东西」。
            self._forget_indexed_root()
        self._clear_index_notice()

    def _forget_indexed_root(self) -> None:
        """把这个库从「索引维护起过」那份名单里拿掉（取消或失败之后）。"""
        if self._library is not None:
            self._index_roots.discard(self._library.root)

    def _show_index_notice(self, text: str) -> None:
        """进度消息不许抢横幅 —— 理由与 :meth:`_show_place_notice` 一模一样。"""
        current = self._banner.text() if self._banner.isVisible() else ""
        if current and current != self._index_notice_text:
            return
        if self._show_banner(text, background=True):
            self._index_notice_text = text

    def _clear_index_notice(self) -> None:
        if self._index_notice_text is None:
            return
        if self._banner.isVisible() and self._banner.text() == self._index_notice_text:
            self._banner.hide()
        self._index_notice_text = None

    # ---- 打开库时把地点解析一遍（ADR 0010）------------------------------------

    def _maybe_start_place_pass(self) -> None:
        """第一次打开一个库时，在后台把整库的地点算出来存进索引。

        **为什么是打开时而不是等用户去分组**：分组与元数据面板要的是同一份反查结论，
        两处各算一次就必然漂（面板写着杭州、分组写着 Bordeaux）。打开时算一遍存进
        索引，之后两处都只读它 —— 详见 ADR 0010。

        一个库里只跑一次：``_place_pass_roots`` 记着起过哪些库，换库才重来，
        来回切回同一个库也不再起。
        扫描本身出问题时不抢横幅 —— 那句「哪些内容没读到」比进度重要。

        **本层正在读元数据时先让着**：那一遍读的就是同一批照片的 EXIF，
        两边同时跑等于把同一件事做两遍。等它结束再开始（见 ``_on_facets_ready``）。
        """
        library = self._library
        if (
            library is None
            or library.root in self._place_pass_roots
            or self._scan_issue
            or self._facet_job is not None
        ):
            return
        self._place_pass_roots.add(library.root)
        resolver = self._place_lookup()

        def work(context: TaskContext) -> object:
            return library.resolve_places(
                should_cancel=context.is_cancelled,
                on_work=lambda count: context.progress(("work", count)),
                on_progress=lambda done, total: context.progress(done),
                status=lambda text: context.progress(text),
                resolver=resolver,
            )

        # **横幅不在这儿亮**：这一遍多半是空跑（库里都算过了），先亮等于在说一件
        # 没有发生的事。等 worker 真的找到活干，由 ``_on_place_pass_work`` 再说。
        self._place_job = self._runner.submit(
            work,
            on_progress=self._on_place_pass_progress,
            on_result=self._on_place_pass_done,
            on_error=self._on_place_pass_failed,
        )

    def _on_place_pass_progress(self, payload: object) -> None:
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "work":
            self._on_place_pass_work(payload[1])
            return
        if self._place_notice_text is None or self._scan_issue:
            return
        if isinstance(payload, str):
            self._show_place_notice(payload)  # 「正在加载城市库…」这类说明
            return
        if isinstance(payload, int) and payload > 0:
            self._show_place_notice(f"正在解析照片的地点…已处理 {payload} 张")

    def _on_place_pass_work(self, count: object) -> None:
        """真的找到活干了才亮横幅：空跑的库不该让用户看见「正在解析…」。"""
        if self._scan_issue or not isinstance(count, int):
            return
        self._show_place_notice(f"正在解析 {count} 张照片的地点…")

    def _on_place_pass_done(self, payload: object) -> None:
        self._place_job = None
        self._clear_place_notice()
        result = payload if isinstance(payload, PlacePass) else PlacePass()
        logger.info("地点解析完成：扫过 %d 张，反查 %d 张", result.scanned, result.resolved)
        # 地点那一遍腾出了后台，检索索引那一遍接着起（见 _maybe_start_photo_index）。
        self._maybe_start_photo_index()

    def _on_place_pass_failed(self, message: str) -> None:
        self._place_job = None
        self._clear_place_notice()
        # 解析失败不影响浏览：分组与面板都会在自己要用的时候再试一次。
        logger.info("解析地点失败：%s", message)
        self._maybe_start_photo_index()

    def _show_place_notice(self, text: str) -> None:
        """进度消息**不许抢**横幅：上面正有别人的话就等它说完。

        抢了会怎样：用户刚丢掉三张照片，横幅上「已把 3 项移到废纸篓」被后台进度顶掉，
        那条确认就此消失。这条横幅是全局共享的，谁都能写 —— 所以后台的进度是
        **最没有资格**占着它的那一个。

        两道判据：上面已经有别人的话（比文字），或者刚刚有用户动作（比时间，
        见 :data:`BANNER_STICKY_SECONDS`）。后者能挡住「用户动作的那句话说完之后、
        进度才姗姗来迟」这种情形。
        """
        current = self._banner.text() if self._banner.isVisible() else ""
        if current and current != self._place_notice_text:
            return  # 上面有更要紧的话（「哪些内容没读到」「已把 3 项移到废纸篓」…）
        if self._show_banner(text, background=True):
            self._place_notice_text = text

    def _clear_place_notice(self) -> None:
        """收掉自己那一条 —— **而且只在自己还占着的时候收**。"""
        if self._place_notice_text is None:
            return
        if self._banner.isVisible() and self._banner.text() == self._place_notice_text:
            self._banner.hide()
        self._place_notice_text = None

    def _cancel_place_pass(self) -> None:
        """作废在途的全库解析（切库、关窗时）。"""
        if self._place_job is not None:
            self._runner.cancel(self._place_job)
            self._place_job = None

    def _on_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._scan_job = None
        self._scan_issue = True
        self._show_banner(f"扫描失败：{message}")
        # 已经攒到的条目照常摆出来（按当前能算出来的顺序）：扫到一半失败时给用户
        # 一屏能看的东西，比给一屏空白加一句「扫描失败」有用。
        self._apply_ordering()

    # ---- 实时监听（ADR 0013）------------------------------------------------

    def _on_watch_changed(self, payload: object) -> None:
        """把一批外部变化落到当前这一层上（**主线程**：排队连接搬过来的）。"""
        library = self._library
        if library is None or not isinstance(payload, tuple):
            return
        changes = tuple(
            change
            for change in payload
            if isinstance(change, LibraryChange) and not self._is_echo(change)
        )
        if not changes:
            return
        logger.info("磁盘上有变化：%s", summarize(changes))
        if self._defer_watch_until_idle():
            waiting = self._watch_pending or ()
            self._watch_pending = (*waiting, *changes)
            return
        self._apply_watch_changes(library, changes)

    def _defer_watch_until_idle(self) -> bool:
        """现在还不能就地改网格吗？回答「是」，这批变化就该先攒着。

        三种时候不能就地改：扫描还在流式上屏、条目还攒在暂存区等着定顺序、
        人正看着大图（前两种会插到旧位置、会插重，第三种会重排不动）。
        """
        return (
            self._scan_job is not None
            or self._staged is not None
            or self._stack.currentWidget() is not self._grid_page
        )

    def _flush_pending_watch(self) -> None:
        """把之前攒下的变化补上：重排一结束、或者人回到网格，都是合适的时候。"""
        changes, self._watch_pending = self._watch_pending, None
        if changes is None:
            return
        library = self._library
        if library is None:
            return
        if self._defer_watch_until_idle():
            self._watch_pending = changes  # 还是不行，继续等
            return
        self._apply_watch_changes(library, changes)

    def _on_watch_failed(self, message: str) -> None:
        """监听本身出问题不该挡住浏览，但界面从此不是最新的，得说出来。"""
        logger.info("文件监听出错：%s", message)
        self._show_banner(f"{message}（界面暂时不会自动更新，重新打开这个库即可恢复）")

    def _apply_watch_changes(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """按当前这一层的形状，把一批变化落到网格、内存缓存与索引上。"""
        if self._conditions:
            # 检索结果是横跨整个库的一层，「当前这一层」的增量逻辑接不上（见
            # :meth:`_apply_search_changes`）。重跑一次几十毫秒，比在一条平行路径上
            # 把插 / 删 / 改三条分支都改对便宜得多。
            self._apply_search_changes(library, changes)
            return
        added: list[GridEntry] = []
        gone: list[Path] = []
        #: 内存里缓存作废的路径（改名、内容变了、照片没了）。
        stale: set[Path] = set()
        #: 内部变了、封面可能要重找的相册。
        covers: set[Path] = set()

        for change in changes:
            if change.kind is ChangeKind.MOVED and change.dest_path is not None:
                self._apply_watch_move(change, added, gone, stale)
                continue
            if change.kind is ChangeKind.REMOVED:
                if self._is_on_current_level(change.path):
                    gone.append(change.path)
                    stale.add(change.path)
                else:
                    covers.update(self._album_covering(change.path))
                continue
            entry = describe_entry(library.root, change.path)
            if entry is None:
                continue  # 已经不在了，或者压根不是照片：这一条什么也做不了
            if change.kind is ChangeKind.MODIFIED:
                stale.add(change.path)
                if self._is_on_current_level(change.path):
                    row = self._model.row_of_path(change.path)
                    if row >= 0:
                        # 换一份新的引用，让这一行不再挂着旧的大小与修改时间。
                        self._model.replace_entry(row, entry)
                        continue
                covers.update(self._album_covering(change.path))
                continue
            if self._is_on_current_level(change.path):
                added.append(entry)
                # 「这一格已经在了」也要当成文件变了：云盘同步中的照片会先以半截的样子
                # 出现（解码失败，流水线把它记成「读不出来」），等下完了再报一次「创建」。
                # 那一格如果不换新引用就一直挂着旧的大小与修改时间，而那份「读不出来」的
                # 记忆也不会清 —— 于是文件明明已经能读了，缩略图却永远回不来。
                # 这正是「照片放进文件夹之后一直是黑的，重开也没用」的一半原因。
                if self._model.row_of_path(change.path) >= 0:
                    stale.add(change.path)
            else:
                covers.update(self._album_covering(change.path))

        rows = sorted(row for path in gone if (row := self._model.row_of_path(path)) >= 0)
        if rows:
            self._model.remove_paths(gone)
            library.forget_paths(gone)  # 索引里的孤儿行：照片不在了，那一行也不该留着
            self._select_after_removal(rows)
        if added:
            whole_level = self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT
            for entry in added:
                self._model.insert_entry(entry, whole_level=whole_level)
        if stale:
            self._forget_caches(stale)
        if covers:
            self._refresh_album_covers(covers)

        if added or gone:
            if self._group_view is not None and any(not isinstance(e, FolderRef) for e in added):
                # 组里的成员是上一次分组算出来的快照，新照片该不该进这个组只能重算。
                self._reenter_group_later(self._group_view.key)
                return
            # 新照片要读一次 EXIF 才排得对；其余照片命中索引，不会重读。
            self._start_facet_read()
        # 「这一层空不空」由它自己判断是加提示还是收提示（扫描出问题时它会让位）。
        self._update_level_notice()
        self._refresh_thumbnail_window()

    def _apply_search_changes(self, library: Library, changes: Sequence[LibraryChange]) -> None:
        """检索这一层的变化：先把索引跟上，再重跑一次检索。

        **索引先跟上，界面后跟上**：重跑时读的就是它，不先更新的话新照片搜不到、
        删掉的照片还在结果里（那正是 ``search_photos`` 要逐条确认「还在不在」的原因 ——
        它兜得住残留行，但那是兜底，不是做法）。

        变化落在两层之下时（例如另一个相册里新增一张），**照样重新检索**：条件能不能
        命中它取决于条件本身，与它在屏幕上可见不可见无关。
        """
        gone: list[Path] = []
        fresh: list[PhotoRef] = []
        stale: set[Path] = set()
        for change in changes:
            for path in (change.path, change.dest_path):
                if path is None:
                    continue
                entry = describe_entry(library.root, path)
                if isinstance(entry, PhotoRef):
                    fresh.append(entry)
                else:
                    gone.append(path)
            stale.update(change.touches)
        if gone:
            library.forget_paths(gone)
        if fresh:
            library.remember_photos(fresh)
        if stale:
            self._forget_caches(stale)
        self._apply_conditions()

    def _apply_watch_move(
        self,
        change: LibraryChange,
        added: list[GridEntry],
        gone: list[Path],
        stale: set[Path],
    ) -> None:
        """外部改名 / 移动：两端都在当前层就原地换那一格，否则一端加、一端删。"""
        library = self._library
        destination = change.dest_path
        if library is None or destination is None:
            return
        entry = describe_entry(library.root, destination)
        source_here = self._is_on_current_level(change.path)
        destination_here = self._is_on_current_level(destination)
        if source_here and destination_here:
            stale.update(change.touches)
            row = self._model.row_of_path(change.path)
            if row >= 0:
                if entry is None:
                    gone.append(change.path)  # 改着改着没了
                else:
                    self._model.replace_entry(row, entry)
            elif entry is not None:
                # 屏幕上没有它（旧名字不在这一层里）：新位置在这一层，就当新来的。
                added.append(entry)
            return
        if destination_here and entry is not None:
            added.append(entry)
        if source_here:
            gone.append(change.path)

    def _is_on_current_level(self, path: Path) -> bool:
        """这个路径是不是当前这一层直接摆出来的一条。"""
        library = self._library
        if library is None:
            return False
        if self._conditions:
            return True  # 检索横跨整个库（ADR 0026 决策 1）
        if self._group_view is not None or self._browse_mode == BROWSE_MODE_FLAT:
            return True  # 平铺与分组：整棵子树都摆在屏幕上
        return path.parent == (self._current_dir or library.root)

    def _album_covering(self, path: Path) -> tuple[Path, ...]:
        """变化发生在某个相册**内部**时，找出当前这一层里代表它的那个相册。

        相册的封面是「里面第一张照片」，所以往相册里丢一张新照片可能会换封面 ——
        这种情况下要把那一格已经画好的封面作废，让缩略图流水线重新去找一次。
        """
        library = self._library
        if library is None or self._browse_mode != BROWSE_MODE_ALBUMS:
            return ()
        current = self._current_dir or library.root
        try:
            relative = path.relative_to(current)
        except ValueError:
            return ()
        if len(relative.parts) < 2:
            return ()  # 就是这一层上的东西，不走这条路
        album = current / relative.parts[0]
        return (album,) if self._model.row_of_path(album) >= 0 else ()

    def _refresh_album_covers(self, albums: Iterable[Path]) -> None:
        """相册内容变了：让封面重新找一次。"""
        stale = {album for album in albums if self._model.row_of_path(album) >= 0}
        if stale:
            self._forget_caches(stale)

    def _forget_caches(self, paths: Iterable[Path]) -> None:
        """丢掉按路径索引的内存缓存（路径上已经没有文件，或者文件已经不是那个文件了）。

        磁盘上的缩略图缓存**不用动**：它的键含路径与 ``(大小, 修改时间)``，文件一改
        键就自然对不上，下次会重新解码（见 ``media.thumbs``）。
        """
        wanted = {Path(path) for path in paths}
        for path in wanted:
            self._preview_cache.pop(path, None)
            self._metadata_cache.pop(path, None)
        self._model.forget_thumbnails(wanted)
        if self._pipeline is not None:
            self._pipeline.forget(wanted)
        self._reload_open_photo(wanted)

    def _reload_open_photo(self, stale: set[Path]) -> None:
        """正看着的那张被改了，就重新载入它 —— 否则大图区与面板显示的还是旧值。"""
        if self._stack.currentWidget() is not self._detail_page or self._current_row < 0:
            return
        row = min(self._current_row, self._model.rowCount() - 1)
        photo = self._model.photo_at(row)
        if photo is not None and photo.path in stale:
            self._current_row = row
            self._load_detail(row)

    def _reenter_group_later(self, key: str) -> None:
        """分组视图里来了新照片：整个库重新分组一次，之后再回到同一个组。

        否则组卡片上的张数会一直停在快照那一刻，用户看不出这个组已经变了。

        **这一趟不是用户自己退出去的**，所以停着的那一格要记下来，等回到同一个组时
        重新选中 —— 不然库里只是多了一张照片，人正在看的那张就被甩掉了（与「换排序
        跟着照片走」同一取向，见 :meth:`_arm_focus`）。
        """
        self._pending_group = key
        self._pending_focus = self._shown_entry_key()
        self._group_view = None
        self._reload_level()

    def _maybe_reenter_group(self) -> None:
        """重排之后回到刚才那个组（它不在了就留在这一层，由横幅说明为什么）。"""
        key, self._pending_group = self._pending_group, None
        focus, self._pending_focus = self._pending_focus, None
        if key is None:
            return
        for entry in self._model.entries():
            if isinstance(entry, GroupRef) and entry.key == key:
                self.enter_group(entry, focus=focus)
                return

    def _note_self_write(self, *paths: Path) -> None:
        """记下自家刚动过的路径：监听器随后报上来的同一件事不重复应用（ADR 0013）。"""
        deadline = time.monotonic() + WATCH_ECHO_TTL
        for path in paths:
            self._watch_echoes[Path(path)] = deadline

    def _is_echo(self, change: LibraryChange) -> bool:
        """这次变化是不是自家写操作的回声。"""
        now = time.monotonic()
        for path, deadline in list(self._watch_echoes.items()):
            if deadline <= now:
                del self._watch_echoes[path]
        return any(path in self._watch_echoes for path in change.touches)

    def _was_self_written(self, path: Path) -> bool:
        """这个路径是不是自家刚刚写过的（改名 / 移到废纸篓 / 撤销）。"""
        now = time.monotonic()
        for known, deadline in list(self._watch_echoes.items()):
            if deadline <= now:
                del self._watch_echoes[known]
        return path in self._watch_echoes

    # ---- 缩略图 ------------------------------------------------------------

    def _refresh_thumbnail_window(self) -> None:
        if self._pipeline is None or self._stack.currentWidget() is not self._grid_page:
            return
        visible_rows = self._grid.visible_rows()
        prefetch_rows = self._grid.visible_rows(margin_ratio=PREFETCH_MARGIN)
        visible_set = set(visible_rows)
        visible = self._entries_missing_thumbnails(visible_rows)
        prefetch = self._entries_missing_thumbnails(
            [row for row in prefetch_rows if row not in visible_set]
        )
        # 把当前档位一起交给流水线：缩放在工作线程里做，界面线程只留一次
        # QPixmap.fromImage（ADR 0015）。
        self._pipeline.set_window(visible, prefetch, target_edge=self._model.icon_size.width())

    def _entries_missing_thumbnails(self, rows: Sequence[int]) -> list[GridEntry]:
        """只挑出需要取缩略图的条目（相册与分组卡片算它们的封面）。

        两种情况：手里根本没有，或者手里那张是按旧尺寸缩放的。滚动会让可见窗口
        反复变化，如果每次都把可见项重新要一遍，就等于每滚一下都把磁盘缓存重读
        重缩放一次，内存里的 LRU 也形同虚设。
        """
        entries: list[GridEntry] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is None:
                continue
            cover = entry.cover if isinstance(entry, GroupRef) else None
            path = cover.path if cover is not None else entry.path
            if self._model.needs_thumbnail(path):
                entries.append(entry)
        return entries

    def _on_thumbnail_ready(self, path: object, image: object, edge: int = 0) -> None:
        if isinstance(path, Path) and isinstance(image, QImage):
            self._model.set_thumbnail(path, image, edge)

    # ---- 缩略图尺寸 --------------------------------------------------------

    def set_thumbnail_size(self, index: int) -> None:
        """直接切到某一档（菜单用，也是排队那一步执行时用的）。

        **明确选了一档，排着的那些就都作废。** 否则「动画期间按了 ⇧⌘↑（正好在顶上，
        本来什么也不该发生），接着又从菜单点了某一档」会让那一下在动画结束后才生效 ——
        用户看到的是自己刚点完，尺寸又自己动了一下。
        """
        self._pending_size_step = 0
        if index == self._size_index:
            return
        self._size_index = index
        self._apply_thumbnail_size()
        self._remember_thumbnail_size()

    def step_thumbnail_size(self, step: int) -> None:
        """按档位加减一档（捏合、⌘+滚轮、⇧⌘↑ / ⇧⌘↓ 用）。

        **上一段切档动画还在跑就排着，不丢。** 连按两下时第二下若立刻执行，网格那边
        判「上一段没跑完」就不接第二段、直接一步落位（见
        ``grid.PhotoGrid._start_transition``），画面上是一次跳变 —— 快起来一帧动画都
        看不见。排着等它跑完，两下就都是看得见的动画。

        **捏合为什么不走这条路**：捏合已经由 ``thumb_sizes.RateLimitedAccumulator``
        限速过了（间隔不足直接丢掉）。捏合的意图本来就是连续的，多出来的那几下是同
        一个意图的重复表达；而快捷键与 ⌘+滚轮是**每一下都独立的意图**，丢掉一下就是
        丢了一次明确的操作 —— 所以这两条排队，那一条丢弃。

        菜单里直接点某一档（:meth:`set_thumbnail_size`）不排队：那是明确选了终值，
        等一等反而不对劲，它维持原来「直接落到用户选的那一档」的行为，并且会把
        排着的那些作废。

        **排不排队的判据是「算上已经排着的那些之后，这一下还会不会换档」**，而不是
        「当前在不在头尾」：顶部那一档上按 ⇧⌘↑ 要是正好抵销掉排着的那一档缩小，
        少了这一句，「先 ⇧⌘↓ 再 ⇧⌘↑」会变成真的一次缩小；而抵销掉之后它也不该
        凭空排一档上去。
        """
        if self._grid.is_transitioning:
            effective = shifted_index(self._size_index, self._pending_size_step)
            if shifted_index(effective, step) != effective:
                self._pending_size_step += step
            return
        target = shifted_index(self._size_index, step)
        if target == self._size_index:
            return  # 已经在头尾：以前会提示「已经是最大尺寸」，随状态条一起去掉
        self.set_thumbnail_size(target)

    def _run_pending_size_step(self) -> None:
        """上一段切档动画结束了：把排着的那一下执行掉（网格发来 :attr:`transition_ended`）。

        排队的档数在 :meth:`step_thumbnail_size` 里是**累加**出来的，所以这里一次
        ``shifted_index`` 就跨到头：连按三下 ⇧⌘↓ 要能一下到最小，不是慢慢挪。

        **先清空再执行**：执行途中会再起一段新的过渡，它结束时会再叫回来一次这个槽。
        不先清空就会一次调用套一次调用地往下钻。队列被自己抵成 0（先放大又缩小）
        或者越界夹住时，这里什么也不做 —— 网格也就不会为此起一段动画。
        """
        step, self._pending_size_step = self._pending_size_step, 0
        if step:
            self.step_thumbnail_size(step)

    def _apply_thumbnail_size(self) -> None:
        """把档位交给网格；实际格子宽度由视口宽度自适应。

        网格算完会通过 ``icon_edge_changed`` 回来，届时再作废并重取缩略图。
        """
        self._grid.set_target_edge(edge_for_index(self._size_index))
        self._sync_size_actions()

    def _on_grid_edge_changed(self, edge: int) -> None:
        """网格自适应出新的缩略图尺寸：作废旧图并重取可见的那一批。"""
        self._model.set_icon_edge(edge)
        self._refresh_thumbnail_window()

    def _sync_size_actions(self) -> None:
        for index, action in enumerate(self._size_actions):
            action.setChecked(index == self._size_index)

    def _remember_thumbnail_size(self) -> None:
        self._session = self._session.with_thumbnail_edge(edge_for_index(self._size_index))
        self._save_session()

    def _on_thumbnail_failed(self, path: object, message: str) -> None:
        # 两类失败不是「照片坏了」，是用户自己（或自家代码）刚刚造成的，丢掉：
        #
        # 1. **这一格已经不在当前这一层** —— 用户刚改名 / 移走 / 删掉 / 换了库。
        #    成功的回填早就在模型里按这个判据丢掉了（``PhotoListModel.set_thumbnail``），
        #    失败这一侧原先却没有对称的判据。
        # 2. **这个路径是自家刚写过的** —— 在途的那次解码读的就是它，而文件被改名 /
        #    移走的那一瞬间它确实读不到，失败是必然的。这类失败是**并发**的产物：
        #    解码开始时文件还在、失败报上来时它又回来了（撤销改名就是这样），
        #    照片其实好得很。
        #
        # 不丢的话，用户看到的是：刚改完名（或刚撤销）成功，横幅立刻变成
        # 「文件可能已损坏」—— 把一次成功的操作说成了数据损坏，最坏的一种假警报。
        if isinstance(path, Path) and (
            not self._model.shows_path(path) or self._was_self_written(path)
        ):
            logger.info("缩略图失败，但这一格已经不在（或刚被自家改过），丢弃：%s", message)
            if self._pipeline is not None:
                # 别把它记成「这文件读不出来」：照片是好的，下次窗口刷新该重取就重取。
                self._pipeline.forget([path])
            return
        self._thumbnail_failures += 1
        logger.info("缩略图失败：%s", message)
        self._show_banner(
            f"有 {self._thumbnail_failures} 张照片无法显示缩略图：{message}",
            background=True,
            auto_hide=True,
        )

    # ---- 元数据面板、地点与进入

    def toggle_metadata(self, visible: bool) -> None:
        """⌘I：展开 / 收起右侧的元数据面板，并把选择记进会话。"""
        self._panel.setVisible(visible)
        self._session = self._session.with_metadata_visible(visible)
        self._save_session()
        # 面板一收一放，照片区右边那条边界就变了，横幅的居中位置跟着重摆。
        self._layout_banner()
        if visible:
            self._load_metadata()
        else:
            self._cancel_metadata_job()

    def _cancel_metadata_job(self) -> None:
        if self._metadata_job is not None:
            self._runner.cancel(self._metadata_job)
            self._metadata_job = None
        if self._metadata_copy_job is not None:
            self._runner.cancel(self._metadata_copy_job)
            self._metadata_copy_job = None

    def _load_metadata(self) -> None:
        """读当前这张照片的元数据；面板没展开就什么都不做。

        命中缓存就同步显示（真正的「直接显示」）；没命中就老实说「正在读取」，
        **不要把上一张的元数据留在屏幕上** —— 那是错的照片的元数据，用户会当成
        当前这张的信息看，比多等 50ms 糟糕得多。
        """
        self._cancel_metadata_job()
        if not self._panel.isVisible():
            return
        row = self._current_row
        ref = self._model.photo_at(row)
        if ref is None:
            self._panel.clear()
            return
        generation = self._generation
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._panel.set_report(cached)
            self._prefetch_metadata(row)
            # 缓存里的报告可能还没推断过地点（预读只读元数据）：命中缓存不等于
            # 「地点已经查过」，漏了这一步，带坐标的照片会永远不显示地点。
            self._maybe_lookup_place(ref, cached, generation, announce=False)
            return
        # 未命中：先说在读，不要让上一张的元数据冒充当前这张。
        self._panel.show_loading(ref.name)

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._metadata_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_ready, row, generation),
            on_error=partial(self._on_metadata_failed, row, generation),
        )

    def _on_metadata_ready(self, row: int, generation: int, payload: object) -> None:
        if not isinstance(payload, MetadataReport):
            return
        ref = self._model.photo_at(row)
        if ref is not None:
            self._cache_metadata(ref, payload)
        if generation != self._generation or row != self._current_row:
            return  # 用户已经翻到别处了，这份结果只进缓存，不上屏
        self._panel.set_report(payload)
        self._prefetch_metadata(row)
        self._maybe_lookup_place(ref, payload, generation, announce=True)

    def _on_metadata_failed(self, row: int, generation: int, message: str) -> None:
        if generation != self._generation or row != self._current_row:
            return
        self._panel.show_error(message)

    def _maybe_lookup_place(
        self,
        ref: PhotoRef,
        report: MetadataReport,
        generation: int,
        *,
        announce: bool,
    ) -> None:
        """按坐标给出地点：**先读索引**，索引里没有才现场反查一次。

        打开库时那一遍已经把结论存进索引了（ADR 0010），所以绝大多数照片在这里是
        一次同步的读缓存就显示到位，不必起后台任务。
        现场反查只兜两种情况：打开时那一遍还没轮到它，或者后来才加进库的照片。
        那条路走 ``Library.resolve_photo_place``：用面板手里已经读到的坐标去查，
        **查完并回索引** —— 面板临时算出来的东西不会只留在内存里。

        ``announce`` 为真时才在面板上显示中间状态（「正在查询…」「正在加载城市库…」）。
        预读与缓存命中都传 ``False``：那时候面板上显示的是当前这张照片，
        拿别的照片的中间状态去替换它，用户只会看到内容闪一下。
        """
        if report.latitude is None or report.longitude is None:
            return
        if not is_real_coordinate(report.latitude, report.longitude):
            return  # 坐标是 (0,0)：那是「没定到 GPS」的占位写法，没有地点可言
        if report.place_from_file or report.place_resolved:
            return  # 文件里写了地点，或已经推断过一次（含「没有记录」这种结论）
        if not self._panel.isVisible():
            return
        library = self._library
        if library is None:
            return
        # 索引里已经有结论就直接显示：这是绝大多数照片走的那条路。
        stored = library.cached_facets([ref]).get(ref.path)
        if stored is not None and stored.place_checked:
            self._show_place(ref, report, generation, _place_text(stored))
            return
        if announce:
            self._panel.set_report(report.with_place("正在查询最近的城市…"))
        resolver = self._place_lookup()
        latitude, longitude = report.latitude, report.longitude

        def work(context: TaskContext) -> object:
            return library.resolve_photo_place(
                ref,
                latitude,
                longitude,
                resolver=resolver,
                status=lambda text: context.progress(text),
            )

        # 不取消进行中的地点查询：它只往索引里写，切走了也仍然有用，
        # 切回来时反而能一次显示到位。
        self._runner.submit(
            work,
            on_progress=partial(self._on_place_progress, ref, report, generation),
            on_result=partial(self._on_place_ready, ref, report, generation),
            on_error=partial(self._on_place_failed, ref, report, generation),
        )

    def _is_current_photo(self, ref: PhotoRef, generation: int) -> bool:
        """这张照片是不是当前显示的那张（换代过就不算）。"""
        if generation != self._generation:
            return False
        current = self._model.photo_at(self._current_row)
        return current is not None and current.path == ref.path

    def _on_place_progress(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        if isinstance(payload, str) and self._is_current_photo(ref, generation):
            self._panel.set_report(report.with_place(payload))

    def _on_place_ready(
        self, ref: PhotoRef, report: MetadataReport, generation: int, payload: object
    ) -> None:
        """``Library.resolve_photo_place`` 的结论：直接拿来显示。"""
        if not isinstance(payload, PhotoFacets):
            self._show_place(ref, report, generation, "未能推断地点：没有读到坐标")
            return
        self._show_place(ref, report, generation, _place_text(payload))

    def _show_place(
        self, ref: PhotoRef, report: MetadataReport, generation: int, text: str
    ) -> None:
        """把地点插进面板、入缓存，并且只在这张正是当前照片时才上屏。"""
        enriched = report.with_place(text)
        self._cache_metadata(ref, enriched)
        if self._is_current_photo(ref, generation):
            self._panel.set_report(enriched)

    def _on_place_failed(
        self, ref: PhotoRef, report: MetadataReport, generation: int, message: str
    ) -> None:
        self._show_place(ref, report, generation, f"未能推断地点：{message}")

    def _on_entry_activated(self, index: QModelIndex) -> None:
        """双击一个格子：相册或分组就进去，照片就打开大图。"""
        entry = self._model.entry_at(index.row())
        if isinstance(entry, FolderRef):
            self.enter_folder(entry.path)
        elif isinstance(entry, GroupRef):
            self.enter_group(entry)
        else:
            self._open_detail(index)

    def _open_detail(self, index: QModelIndex) -> None:
        row = index.row()
        if self._model.photo_at(row) is None:
            return
        self._current_row = row
        self._stack.setCurrentWidget(self._detail_page)
        self._detail.setFocus()
        self._update_action_states()
        self._load_detail(row)

    # ---- 右键菜单（见 ADR 0014）----------------------------------------------

    def _show_context_menu(self, row: int) -> None:
        """网格上的一次右键：拼菜单、弹出来、用完即弃。

        粘到哪里不看菜单本身，看**选区**（:meth:`_paste_target`）—— 右键那一格会先把它
        选中（``context_menu_for`` 之前网格已经 ``select_for_context`` 过了），所以点那一行
        与按 ⌘V 落在同一个目录上，不需要谁替菜单记着「这一下按的是哪个相册」。
        """
        menu = self.context_menu_for(row)
        if menu is None:
            return
        menu.exec(self._grid.cursor().pos())
        menu.deleteLater()

    def context_menu_for(self, row: int) -> QMenu | None:
        """这一下该弹什么菜单。**只拼不弹**，测试因此可以直接调它看内容。

        拼之前先刷新一次动作的可用性：「上一级」退不退得回去、「重命名」会不会被分组
        挡掉，判据都依赖**此刻**的选区与层级（见 :meth:`_update_action_states`）。
        拿几十毫秒前的状态来拼菜单，用户会看到一个与界面不一致的灰度。

        「粘贴」拼在哪、粘到哪，按**落点**定（见 :meth:`_context_paste`）：空白处粘进
        当前这一层，按在某个相册上则粘进那个相册**内部**，按在照片上则压根不出现。

        ``None`` 表示这次右键不该有菜单（不在网格页、行号已经过期）。
        """
        if self._stack.currentWidget() is not self._grid_page:
            return None
        entry = self._model.entry_at(row) if row >= 0 else None
        if row >= 0 and entry is None:
            # 扫描还在流式上屏时，模型可能已经往前挪过：宁可不弹，也不弹一份错的。
            return None
        self._update_action_states()
        kind = TargetKind.BLANK if entry is None else _context_kind(entry)
        multiple = len(self._grid.selected_rows()) > 1
        menu = build_menu(
            self._menu_actions(self._context_paste(kind, entry, multiple=multiple)),
            kind,
            multiple=multiple,
        )
        return self._dressed_menu(menu)

    def _show_detail_context_menu(self, position: QPoint) -> None:
        """大图页上的一次右键：拼菜单、弹出来、用完即弃（与网格同一套流程）。

        位置是 ``DetailView`` 报的窗口坐标 —— 视图只回答「按在了哪」，菜单归这一侧拼
        （ADR 0021）。菜单用完即弃：它里面那份 ``QAction`` 是共用的，但这份菜单本身不是。
        """
        menu = self.detail_context_menu()
        if menu is None:
            return
        menu.exec(position)
        menu.deleteLater()

    def detail_context_menu(self) -> QMenu | None:
        """大图页该弹什么菜单。**只拼不弹**，与 :meth:`context_menu_for` 一个规矩。

        判据只有一条：现在在大图页，且**确实在看一张照片**。加载失败、画面还空着的时候
        右键一下也不该有菜单 —— 里面每一条命令的对象都还不存在。
        """
        if self._stack.currentWidget() is not self._detail_page:
            return None
        if self._model.photo_at(self._current_row) is None:
            return None
        # 与网格那一侧同样先刷新一次可用性：菜单里那些行共享同一份 QAction，灰度得是
        # 「此刻」的灰度，而不是进大图页时算的那一份。
        self._update_action_states()
        # 大图页没有「当前这一层」可粘，因此这一份动作包里压根不带「粘贴」
        # （ADR 0018 决策 7），与网格里单张照片那份一致。
        return self._dressed_menu(build_menu(self._menu_actions(None), TargetKind.DETAIL))

    def _dressed_menu(self, menu: QMenu) -> QMenu:
        """刚拼好的菜单在下发之前要做的那几件事：底色、圆角行距、快捷键那一列。

        网格与大图页共用同一段 —— 两边拼出来的菜单长得不一样，用起来却必须是同一个软件。
        """
        # 菜单的底色取自调色板的 ``Window`` 角色，而它默认来自系统（浅色 ``#ececec``），
        # 于是按一次右键就掉出整块底色之外。菜单是**这一下**才建的，此后不会再有一次
        # 主题下发，所以直接照抄窗口那份统一调色板（``ui.theme`` 逐控件下发的那份）。
        # 系统随后换浅色 / 深色也不影响它：下一次右键建的就是一个新的。
        menu.setPalette(self.palette())
        # 圆角与行距：macOS 样式给的那圈约 5 像素、每行 18 像素高，太挤，只能用样式表改。
        # 而样式表一上，面板就不再由系统画、底色会掉回系统菜单色，所以底色必须显式写进
        # 样式表（见 ``ui.menus`` 模块文档）。
        menu_style.apply_style(menu)
        # 快捷键那一列我们自己画（按字母 / 修饰符分列），得先跟 Qt 约好别重复画。
        menu_style.prepare(menu)
        return menu

    def _context_paste(
        self, kind: TargetKind, entry: GridEntry | None, *, multiple: bool
    ) -> QAction | None:
        """这一下右键该递进去的那一条「粘贴」——**粘到哪里由落点决定**（ADR 0018 决策 7）。

        | 落点 | 菜单里那一条 |
        |------|--------------|
        | **空白处** | 粘进**当前这一层** |
        | **单个相册** | 粘进**这个相册内部**，不是它所在的这一层 |
        | 照片 / 多选照片 / 分组卡片 / 多个相册 | **不出现** |
        | 大图页 | 不出现（那一页连「当前这一层」都没有，见 :meth:`detail_context_menu`） |

        前两处递进去的**是同一枚动作**（:attr:`_paste_action`，菜单栏那个 ⌘V）—— 因此空白处
        与相册那两份菜单里那一行都照常写着 ⌘V，而且**点它与按 ⌘V 是同一件事**，不可能出现
        「写着 ⌘V、按下去却是另一个地方」的分裂。粘到哪里一律由 :meth:`_paste_target` 算：
        这一格被右键选中着，它就是那个相册（访达的「粘贴项目」：按在哪个文件夹上就粘进哪个
        文件夹）；空白处那一次选区是空的，于是粘进当前这一层。

        其余落点一律不出现，按的是用户给的取向：按在一张照片上时「粘到哪」说不清（这一格
        未必属于当前这一层），多选相册时「粘进哪一个」说不清。⌘V 一直都在 —— **菜单摆不摆
        它，与快捷键怎么用是两回事**（⌘V 粘到哪里照样按选区算，见 :meth:`_paste_target`）。
        """
        if kind is TargetKind.ALBUM and not multiple and isinstance(entry, FolderRef):
            # 剪贴板里没有文件则整条不出现（ADR 0018 决策 6）。
            if not desktop.read_files_from_clipboard():
                return None
            return self._paste_action
        if kind is TargetKind.BLANK and self._can_paste():
            return self._paste_action
        return None

    def _menu_actions(self, paste: QAction | None) -> MenuActions:
        """把菜单要用的动作收成一包（``ui.menus`` 只认这个，不认窗口）。

        ``paste`` 由 :meth:`_context_paste` 按**这一下的落点**算好递进来（照片那一处压根不递），
        摆在第几段由每张菜单自己决定（``ui.menus``）。
        """
        # 两个相册清单**共用同一次遍历**：``_album_targets`` 递归走一遍整棵目录树，
        # 拼两个子菜单却走两遍就是白翻一倍。库里一个相册都没有时两份都不出现（空子菜单
        # 是个空框，与 ADR 0014 决策 4 同一个取向）。
        albums = self._album_targets()
        copy_to_album = self._album_picker("复制到相册", albums)
        move_to_album = self._album_picker("移动到相册", albums)
        if copy_to_album is not None:
            copy_to_album.triggered.connect(self._on_album_chosen_for_copy)
        if move_to_album is not None:
            move_to_album.triggered.connect(self._on_album_chosen_for_move)
        return MenuActions(
            open_entry=self._open_entry_action,
            close_entry=self._close_entry_action,
            open_default=self._open_default_action,
            reveal=self._reveal_action,
            copy=self._copy_action,
            copy_path=self._copy_path_action,
            # 「新建相册」只在空白处那张菜单里（ADR 0022 决策 1），而建不了时不出现 ——
            # 与「粘贴」同一个取向。此时它的快捷键 ⇧⌘N 由 ``_update_action_states`` 置灰。
            new_album=self._new_album_action if self._can_create_album() else None,
            # 两个相册清单：库里一个相册都没有时压根不出现（空子菜单是个空框，
            # 与 ADR 0014 决策 4 同一个取向）。每次弹出现建，所以连的是这一份的信号。
            copy_to_album=copy_to_album,
            move_to_album=move_to_album,
            # 「粘贴」由 :meth:`_context_paste` 按落点递进来：此刻粘不了（剪贴板里没有
            # 文件）就传 ``None``，菜单里干脆不出现，而不是摆一条永远点不动的。
            paste=paste,
            rename=self._rename_action,
            trash=self._trash_action,
            metadata=self._metadata_action,
            copy_metadata=self._copy_metadata_action,
            copy_group=self._copy_group_action,
            select_all=self._select_all_action,
            up=self._up_action,
            group_choices=tuple(self._group_actions.values()),
            sort_choices=tuple(self._sort_actions.values()),
            descending=self._descending_action,
            size_choices=tuple(self._size_actions),
            # 封面那两条（ADR 0028）按**这一下的落点与选区**算，与「新建相册」同一个做法：
            # 此刻没有对象就是 ``None``，菜单里不出现这一行，而不是摆一条点不动的。
            # 对象是谁由 ``WriteOpsMixin`` 回答 —— 它与「粘到哪去」「动的是哪些东西」
            # 是同一个前置问题，答案自然也在同一个地方。
            set_cover=self._set_cover_action if self.cover_candidate() is not None else None,
            clear_cover=self._clear_cover_action if self.covered_album() is not None else None,
        )

    def open_current_entry(self) -> None:
        """「打开」：照片开大图，相册与分组进去 —— 与双击是同一件事。"""
        index = self._grid.currentIndex()
        if index.isValid():
            self._on_entry_activated(index)

    def open_current_in_default_app(self) -> None:
        """「在默认应用中打开」⇧⏎：交给系统为这类文件设定的默认应用（见 ``ui.desktop``）。

        作用于**当前这一张**，多选时说不清打开哪一张，因此菜单里那一行压根不出现。
        用 ``photo_at`` 而不是「取当前项再判断类型」：分组卡片与相册在这条命令上没有
        意义（相册是文件夹，让系统在访达里打开它是「打开」自己的活），这一层直接挡住，
        免得拼错一次就在这里抛 ``AttributeError``。

        行的来源是 :meth:`_focus_row` 而不是网格的当前项：大图页上看的可能已经是翻过去的
        那一张（ADR 0021），照着网格的当前项会把上一张交给默认应用打开。
        """
        ref = self._model.photo_at(self._focus_row())
        if ref is None:
            return
        # 成功时没有话可说（那个应用自己会弹到前台），只有失败才上横幅。
        message = desktop.open_with_default_app(ref.path)
        if message is not None:
            self._show_banner(message)

    def reveal_selection(self) -> None:
        """「在访达中显示」：在访达里把选中的东西选出来（见 ``ui.desktop``）。

        选中的**分组卡片**摊平成它的组员（ADR 0020）：卡片自己没有路径，而它代表的
        那批照片在访达里实实在在地摆着 —— 这正是用户右键一张「Canon · 128 张」时
        想看到的东西。
        """
        paths = self._selected_paths()
        if not paths:
            self._show_banner(NOTHING_SELECTED)
            return
        self._show_banner(desktop.reveal_in_finder(paths), auto_hide=True)

    def copy_selected_paths(self) -> None:
        """「拷贝路径」⇧⌘C：与访达一样，一行一个。"""
        paths = self._selected_paths()
        if not paths:
            return
        desktop.copy_to_clipboard(desktop.format_paths(paths))
        head = "已拷贝 1 个路径。" if len(paths) == 1 else f"已拷贝 {len(paths)} 个路径。"
        self._show_banner(head, auto_hide=True)

    def copy_selected_files(self) -> None:
        """「拷贝」⌘C：把选中的**文件**放进剪贴板，访达里 ⌘V 得到一份拷贝（ADR 0017）。

        与「拷贝路径」一样作用于整个选择，相册（文件夹）也算。**一个字节都不碰原文件**：
        放进剪贴板的是路径引用，粘出来的那一份由访达去写，不在 Gallery 的地盘上。
        """
        paths = self._selected_paths()
        if not paths:
            return
        self._show_banner(desktop.copy_files_to_clipboard(paths), auto_hide=True)

    # ---- 右键菜单里的只读命令（见 ADR 0014）

    def _on_clipboard_changed(self) -> None:
        """剪贴板变了就重算一次 ⌘V 的可用性（只算这一条，不动别的）。"""
        in_grid = self._stack.currentWidget() is self._grid_page
        self._paste_action.setEnabled(in_grid and self._can_paste())

    def select_all_entries(self) -> None:
        """⌘A：全选当前这一层的所有格子（相册、照片、分组卡片都算）。"""
        if self._stack.currentWidget() is not self._grid_page:
            return
        self._grid.selectAll()
        self._update_action_states()

    def copy_current_metadata(self) -> None:
        """「复制元数据」：把当前这张的元数据拷成文本。

        缓存里没有就**现读一次** —— 这条命令在面板没展开时也可用，而那时多半没有缓存
        可用。读一次约 50ms，走后台线程，完成时用横幅说一声。

        大图页上拷的是**正在看的那一张**（ADR 0021），所以行的来源是 :meth:`_focus_row`。
        """
        ref = self._model.photo_at(self._focus_row())
        if ref is None:
            return
        cached = self._cached_metadata(ref)
        if cached is not None:
            self._put_metadata_on_clipboard(cached, ref)
            return
        generation = self._generation

        def work(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return read_metadata(ref.path)

        self._metadata_copy_job = self._runner.submit(
            work,
            on_result=partial(self._on_metadata_copied, ref, generation),
            on_error=self._on_metadata_copy_failed,
        )

    def _on_metadata_copied(self, ref: PhotoRef, generation: int, payload: object) -> None:
        self._metadata_copy_job = None
        if not isinstance(payload, MetadataReport) or generation != self._generation:
            return  # 已经换库了：这份元数据不该再跑到界面上
        self._cache_metadata(ref, payload)
        self._put_metadata_on_clipboard(payload, ref)

    def _on_metadata_copy_failed(self, message: str) -> None:
        self._metadata_copy_job = None
        logger.info("复制用的元数据读取失败：%s", message)
        self._show_banner(f"没能读出元数据：{message.rstrip('。')}")

    def _put_metadata_on_clipboard(self, report: MetadataReport, ref: PhotoRef) -> None:
        desktop.copy_to_clipboard(report.as_text())
        self._show_banner(f"已复制「{ref.name}」的元数据。", auto_hide=True)

    def copy_current_group(self) -> None:
        """「复制分组条件」：把这个组是怎么分出来的抄走。

        组卡片本身只是磁盘上那批照片的一个切面，粘到别处也得说得出它是哪一组。
        """
        group = self._model.group_at(self._grid.current_row())
        if group is None:
            return
        facet = group_facet(group.facet)
        field = facet.label if facet is not None else group.facet
        desktop.copy_to_clipboard(f"按「{field}」分组：{group.label} · {group.count} 张")
        self._show_banner(f"已复制分组条件：按「{field}」分组。", auto_hide=True)

    # ---- 大图（见 ADR 0021）

    def show_grid(self) -> None:
        """回到网格，并让当前照片保持选中。"""
        self._title_bar.stop_edit()  # 切页时把改到一半的名字收起来（ADR 0025）
        if self._stack.currentWidget() is self._grid_page:
            return
        self._stack.setCurrentWidget(self._grid_page)
        if self._current_row >= 0:
            self._grid.select_row(self._current_row)
        # 在大图页时攒下的外部变化：这里才是网格能改的地方（ADR 0013）。
        self._flush_pending_watch()
        self._refresh_title()
        self._update_action_states()
        self._refresh_thumbnail_window()

    def step_detail(self, delta: int) -> None:
        """上一张 / 下一张。相册模式下会跳过相册格子。"""
        row = self._neighbour_photo_row(self._current_row, delta)
        if row is None:
            return
        self._current_row = row
        self._load_detail(row)

    def _neighbour_photo_row(self, start: int, delta: int) -> int | None:
        row = start + delta
        while 0 <= row < self._model.rowCount():
            if self._model.photo_at(row) is not None:
                return row
            row += delta
        return None

    def _load_detail(self, row: int) -> None:
        library = self._library
        ref = self._model.photo_at(row)
        if library is None or ref is None:
            return
        # 标题栏那一行从此是这张照片的文件名（ADR 0025 决策 4）。放在这里而不是各调用处：
        # 翻图、打开、删掉一张之后接着看下一张，走的都是这一段。
        self._refresh_title()
        generation = self._generation

        if self._detail_job is not None:
            # 上一张的解码已经没有意义了，早点让它停下来。
            self._runner.cancel(self._detail_job)
            self._detail_job = None
        if self._panel.isVisible():
            self._load_metadata()

        cached = self._preview_cache.get(ref.path)
        if cached is not None:
            self._preview_cache.move_to_end(ref.path)
            self._detail.set_image(cached, refit=True)
            self._request_full(ref, row, generation)
            # 命中也必须继续预读前后：只预读未命中的那一张的话，连续翻图会变成
            # 「命中 → 不预读 → 下次未命中 → 预读 → 命中」地交替，每隔一张就
            # 先闪一下缩略图。命中与未命中应当对缓存的处理完全一致。
            self._prefetch_neighbours(row)
            return

        self._detail.prepare_switch()
        # 画面上此刻还留着**上一张**照片。留着它，用户会以为点错了文件 ——
        # 看到的是错的照片，比短暂的黑屏更糟。先用这张照片自己的缩略图顶上：
        # 尺寸小一点，但至少是对的。
        thumbnail = self._model.thumbnail_pixmap(ref.path)
        if thumbnail is not None:
            self._detail.set_image(thumbnail.toImage(), refit=True)
        else:
            self._detail.clear_image()
        max_edge = self._preview_edge()

        def work_preview(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            # 取消令牌要传进解码：云盘上没下完的照片会让 ``open()`` 永远不返回，
            # 关窗时那一个任务就会把退出拖住（见 ``core.media.decode._within_deadline``）。
            return to_qimage(
                library.preview(ref, max_edge=max_edge, should_cancel=context.is_cancelled)
            )

        self._detail_job = self._runner.submit(
            work_preview,
            on_result=partial(self._on_preview_ready, ref, row, generation),
            on_error=partial(self._on_detail_failed, ref, row, generation),
        )
        self._prefetch_neighbours(row)

    def _on_preview_ready(self, ref: PhotoRef, row: int, generation: int, payload: object) -> None:
        if generation != self._generation or row != self._current_row:
            return  # 用户已经翻到别处了，丢弃这张迟到的图
        if not isinstance(payload, QImage):
            return
        self._cache_preview(ref.path, payload)
        self._detail.set_image(payload, refit=True)
        self._request_full(ref, row, generation)

    def _request_full(self, ref: PhotoRef, row: int, generation: int) -> None:
        """后台解码全分辨率图像，用于 100% 查看。"""
        library = self._library
        if library is None:
            return

        def work_full(context: TaskContext) -> object:
            if context.is_cancelled():
                return None
            return to_qimage(library.full(ref, should_cancel=context.is_cancelled))

        self._detail_job = self._runner.submit(
            work_full,
            on_result=partial(self._on_full_ready, ref, row, generation),
            on_error=partial(self._on_detail_failed, ref, row, generation),
        )

    def _on_full_ready(self, ref: PhotoRef, row: int, generation: int, payload: object) -> None:
        if generation != self._generation or row != self._current_row:
            return
        if not isinstance(payload, QImage):
            return
        # refit=False：替换成更清晰的图，但保住用户当前的缩放与位置，画面不跳。
        self._detail.set_image(payload, refit=False)

    def _on_detail_failed(self, ref: PhotoRef, row: int, generation: int, message: str) -> None:
        if generation != self._generation or row != self._current_row:
            return
        # 原因直接画在大图区里（show_error），不需要状态条再报一遍。
        self._detail.show_error(message)

    def _cached_metadata(self, ref: PhotoRef) -> MetadataReport | None:
        """缓存里有没有这张照片的元数据；文件改过（大小 / 修改时间）就当没有。"""
        entry = self._metadata_cache.get(ref.path)
        if entry is None or entry[0] != ref.size or entry[1] != ref.mtime_ns:
            return None
        self._metadata_cache.move_to_end(ref.path)
        return entry[2]

    def _cache_metadata(self, ref: PhotoRef, report: MetadataReport) -> None:
        self._metadata_cache[ref.path] = (ref.size, ref.mtime_ns, report)
        self._metadata_cache.move_to_end(ref.path)
        while len(self._metadata_cache) > METADATA_CACHE_SIZE:
            self._metadata_cache.popitem(last=False)

    def _prefetch_metadata(self, row: int) -> None:
        """预读附近几张的元数据，让翻照片时直接显示、没有中间态。

        一次读取约 50ms：预读了，切过去就是 0ms；不预读则每次切图都要等，
        只能靠「保留旧内容」顶着 —— 那会把上一张的地点显示给当前照片，是错数据。
        """
        if not self._panel.isVisible():
            return
        for offset in range(-METADATA_PREFETCH_RADIUS, METADATA_PREFETCH_RADIUS + 1):
            if offset == 0:
                continue
            ref = self._model.photo_at(row + offset)
            if ref is None or self._cached_metadata(ref) is not None:
                continue

            def work(context: TaskContext, target: PhotoRef = ref) -> object:
                if context.is_cancelled():
                    return None
                return read_metadata(target.path)

            self._runner.submit(
                work,
                on_result=partial(self._on_metadata_prefetched, ref),
                on_error=partial(self._on_metadata_prefetch_failed, ref),
            )

    def _on_metadata_prefetched(self, ref: PhotoRef, payload: object) -> None:
        """预读结果入缓存，顺带把地点也推断好。

        地点必须在这里查完：否则切到这张照片时缓存里只有坐标，面板要先显示一遍
        没有地点的版本、再被反查结果替换 —— 那正是「切换照片时地点闪一下」的来源。
        入缓存的应当是可以一次显示到位的完整报告。
        """
        if isinstance(payload, MetadataReport):
            self._cache_metadata(ref, payload)
            self._maybe_lookup_place(ref, payload, self._generation, announce=False)

    def _on_metadata_prefetch_failed(self, ref: PhotoRef, message: str) -> None:
        logger.info("预读元数据失败：%s（%s）", ref.name, message)

    def _prefetch_neighbours(self, row: int) -> None:
        """预取前后各一张的预览，让连续翻图不白屏。"""
        library = self._library
        if library is None:
            return
        generation = self._generation
        max_edge = self._preview_edge()
        for neighbour in (row - 1, row + 1):
            ref = self._model.photo_at(neighbour)
            if ref is None or ref.path in self._preview_cache:
                continue

            def work(context: TaskContext, target: PhotoRef = ref) -> object:
                if context.is_cancelled():
                    return None
                return to_qimage(
                    library.preview(target, max_edge=max_edge, should_cancel=context.is_cancelled)
                )

            self._runner.submit(
                work,
                on_result=partial(self._on_neighbour_ready, ref, generation),
            )

    def _on_neighbour_ready(self, ref: PhotoRef, generation: int, payload: object) -> None:
        if generation == self._generation and isinstance(payload, QImage):
            self._cache_preview(ref.path, payload)

    def _cache_preview(self, path: Path, image: QImage) -> None:
        self._preview_cache[path] = image
        self._preview_cache.move_to_end(path)
        while len(self._preview_cache) > PREVIEW_CACHE_SIZE:
            self._preview_cache.popitem(last=False)

    def _preview_edge(self) -> int:
        """预览图长边：略大于视口的物理像素，保证 1:1 时也不糊。"""
        viewport = self._detail.viewport().size()
        ratio = self._detail.devicePixelRatioF()
        edge = int(max(viewport.width(), viewport.height()) * ratio * PREVIEW_EDGE_FACTOR)
        return max(MIN_PREVIEW_EDGE, min(MAX_PREVIEW_EDGE, edge))

    # ---- 提示 --------------------------------------------------------------

    def _show_banner(self, text: str, *, background: bool = False, auto_hide: bool = False) -> bool:
        """显示横幅，返回这条消息是否真的上了屏。

        ``background=True`` 是**后台**消息（缩略图解码失败、地点解析进度）：它们不许盖掉
        用户刚刚触发的那条说明，理由与时长见 :data:`BANNER_STICKY_SECONDS`。用户自己做的事
        要能说清楚，这是第一位的；而「某张照片读不出来」「正在解析地点」这种话，等一会儿
        再说并不会让人误判。

        ``auto_hide=True`` 是**说过就走**：过 :data:`BANNER_AUTO_HIDE_SECONDS` 秒自己收起来
        （ADR 0029 决策 3）。判据是「用户看完要不要动手」：

        - **用户做完一件事的确认** —— 「已重命名为…」「已把 3 项移到废纸篓。⌘Z 可以撤销。」
          「已复制 12 个路径。」—— 说完就没有新内容了，继续压在照片中间只是碍事；
        - **要用户动手处理的那几句一律留下**：出错、没能做成、「这一层是空的」、
          后台进度。收掉它们等于在用户还没看完时把话收走。
        """
        now = time.monotonic()
        if background and now < self._banner_sticky_until:
            logger.debug("后台消息先让着用户动作，过一会儿再报：%s", text)
            return False
        if not background:
            self._banner_sticky_until = now + BANNER_STICKY_SECONDS
        self._banner.show_message(
            text,
            auto_hide_seconds=BANNER_AUTO_HIDE_SECONDS if auto_hide else None,
        )
        return True

    def _clear_banner(self) -> None:
        """把横幅收干净（目前只有测试用：它是全局共享的一个槽）。"""
        self._banner_sticky_until = 0.0
        self._place_notice_text = None
        self._banner.hide()

    # ---- 横幅的摆位（ADR 0029）------------------------------------------------

    def _on_page_changed(self, _index: int) -> None:
        """换页：横幅要重新摆 —— 大图页上照片区右边让给了元数据面板，居中的位置变了。"""
        self._layout_banner()

    def _banner_area(self) -> QRect:
        """横幅可以摆的那一片：**内容区减去元数据面板**（面板摆着的那几页才减）。

        中央控件自己已经让开了标题栏那一条（见 ``_build_ui``），所以这里拿它的
        ``geometry`` 就是照片区那一带，不必再算一遍边距。
        """
        central = self.centralWidget()
        if central is None:
            return QRect()
        area = QRect(central.geometry())
        panel = self._panel
        if panel is not None and panel.isVisible():
            # 面板是盖在右边那一段的，不该被横幅压住：居中到照片区里去。
            area.setRight(panel.geometry().left() - 1)
        return area

    def _layout_banner(self) -> None:
        """把横幅摆在内容区底边居中（它在窗口上，不属于任何布局）。

        窗口改大小、换页、展开收起元数据面板之后都要重摆一次 —— 与 ``_layout_title_bar``
        同一套时机。
        """
        self._banner.reposition(self._banner_area())
        if self._banner.isVisible():
            self._banner.raise_()  # 窗口布局跑完之后中央控件会被抬上来

    def _report_error(self, message: str) -> None:
        QMessageBox.critical(self, "Gallery", message)

    def clear_thumbnail_cache(self) -> None:
        """清空当前库的缩略图缓存（只删缓存目录里的文件）。"""
        if self._library is None:
            return
        answer = QMessageBox.question(
            self,
            "清空缩略图缓存",
            f"将删除「{self._library.display_name}」的缩略图缓存。\n"
            "照片本身不受影响，缓存会在下次浏览时自动重建。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self._library.clear_cache()
        self._model.clear_thumbnails()
        self._refresh_thumbnail_window()
        self._show_banner(f"已清除 {removed} 个缩略图缓存文件。", auto_hide=True)

    def _update_action_states(self) -> None:
        self._update_navigation_actions()
        in_detail = self._in_detail_page()
        in_grid = self._stack.currentWidget() is self._grid_page
        # 改名的过程中别启用别的会动文件的命令 —— 那时键盘归输入框，⌘⌫ 不该把正在编辑的
        # 那张删掉。**两个输入框都算**：网格那个与标题栏那个（ADR 0025 决策 6），
        # 标题栏右上角的搜索框也是（ADR 0026）—— 那时键盘归它，⌘⌫ 要删的是搜索框
        # 里刚敲的字。
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
        # 缩略图那两条正好反过来，只在网格页亮着：大图页里 ⌘+ / ⌘- 才是缩放，
        # 而 ⇧⌘↑ ⇧⌘↓ 在那一页没有对应的意思（照片那边只有倍率，没有「档」）。
        # 改到一半也不亮：换档会重排网格，把正在编辑的那一格从输入框底下挪走。
        for action in (self._thumb_zoom_in_action, self._thumb_zoom_out_action):
            action.setEnabled(in_grid and idle)
        # 「退出」也是 Esc，所以改到一半要把它关掉：那一刻 Esc 是输入框的「取消改名」，
        # 与「上一级」在网格页上的让位是同一条理由（见 ``_update_navigation_actions``）。
        self._back_action.setEnabled(in_detail and idle)
        self._clear_cache_action.setEnabled(self._library is not None)
        # 「清除搜索条件」**整条出现 / 消失**，不是置灰（ADR 0014 决策 4）：
        # 没有条件时它压根没有对象，摆一个灰着的「清除」只会让人去猜在清什么。
        self._clear_search_action.setVisible(bool(self._conditions))
        # 搜索框摆在标题栏那一行上（ADR 0026 决策 8），而**那一行只在 macOS 上存在**
        # （ADR 0025 决策 2）。别的平台上这一条老实置灰，而不是按了没反应。
        self._search_action.setEnabled(self._owns_title_bar)
        # 「改名」落在哪儿：大图页改的是窗口标题栏那一行（ADR 0025），网格里是行内编辑。
        # 没有标题栏那一段的平台上（见 ``theme.unify_title_bar``）大图页没有改名字段，
        # 那一侧仍旧是网格的事。
        renaming_here = in_grid or (in_detail and self._title_bar.isVisible())
        # 移到废纸篓两边都成立：大图页删的是正在看的那一张（``_selected_entries`` 认这一页）。
        entries = self._selected_entries()
        focus_row = self._focus_row()
        # 「改名」只对磁盘上的**某一个**东西成立：分组卡片不是磁盘上的东西，改不了名，
        # 一次也只改一条（见 ADR 0009 与 ADR 0004）。网格页要连**当前项**一起算
        # —— 它可能不在选区里；大图页指向的必然是正在看的那张照片，看它就够了。
        considered: tuple[int, ...] = (
            (*self._grid.selected_rows(), self._grid.current_row())
            if in_grid
            else (self._current_row,)
        )
        file_only = not any(
            isinstance(self._model.entry_at(row), GroupRef) for row in considered if row >= 0
        )
        # 「移到废纸篓」与「拷贝」一族作用于**摊平之后**的选区：选中一张分组卡片就是
        # 选中它代表的那批组员（ADR 0020），与「进去全选再删」是同一件事。大图页上是
        # 正在看的那一张，摊平对它没有影响。
        flat = flattened_entries(entries)
        self._rename_action.setEnabled(renaming_here and focus_row >= 0 and idle and file_only)
        self._trash_action.setEnabled(bool(flat) and idle)
        # 右键新增的那几条（ADR 0014）：可用性一律按「此刻作用于谁」算，
        # 菜单栏上的 ⌘A / ⇧⌘C 与右键菜单里的同一份动作因此永远说同一句话。
        paths = [entry.path for entry in flat]
        current = self._model.entry_at(focus_row) if focus_row >= 0 else None
        self._select_all_action.setEnabled(in_grid and self._model.rowCount() > 0)
        self._copy_path_action.setEnabled(bool(paths))
        self._copy_action.setEnabled(bool(paths))
        # ⌘V 与选区无关：只看「当前这一层有没有可粘的目录」与「剪贴板里有没有文件」。
        # 粘不了时它同时也不会出现在右键菜单里（见 ``_menu_actions``）。**大图页不粘**：
        # 那一页根本没有「当前这一层」，粘到哪要另说（ADR 0021）。
        self._paste_action.setEnabled(in_grid and self._can_paste())
        # ⇧⌘N 与选区无关：只看「当前这一层有没有这个目录」（ADR 0022 决策 1）。
        # 分组里没有 —— 那一层摆的全是照片，插一张相册卡片进去是错位的；
        # 改到一半也不行，与改名 / 删除同一个 ``idle``：那时键盘归输入框。
        self._new_album_action.setEnabled(in_grid and idle and self._can_create_album())
        # ⌘⏎ 与改名输入框抢键：改到一半键盘归输入框（与 rename / trash 同一个 ``idle``），
        # 不然「敲完名字顺手 ⌘⏎」会变成把访达叫起来。
        self._reveal_action.setEnabled(bool(paths) and idle)
        self._open_entry_action.setEnabled(current is not None)
        # ⇧⏎ 与 ⌘⏎ 同一套判据，只是更窄一层：当前这一格得是**照片**、且没在改名字。
        # 相册与分组卡片这一条不出现（见 ``open_current_in_default_app``）。**大图页上也可用**
        # （ADR 0021）：菜单里那一条亮着，按下去就该是那一行说的事 —— 顺带说一句，这会改掉
        # 「大图下按 ⇧⏎ 是回车」这个原先的巧合：现在 ⇧⏎ 交给系统，``Esc`` / ``⏎`` 才是退出。
        self._open_default_action.setEnabled(idle and isinstance(current, PhotoRef))
        self._copy_metadata_action.setEnabled(isinstance(current, PhotoRef))
        self._copy_group_action.setEnabled(in_grid and isinstance(current, GroupRef))
        # 「退出」只出现在大图页的右键菜单里（ADR 0021），所以它也只在那儿可用。
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

    # ---- 浏览方式与相册导航 ------------------------------------------------

    def set_browse_mode(self, mode: str) -> None:
        """切换「相册（逐层进入）」与「全部照片（整库平铺）」。"""
        if mode == self._browse_mode:
            self._update_mode_actions()
            return
        self._browse_mode = mode
        self._group_view = None
        # 检索是横跨整个库的一层（ADR 0026 决策 1），换浏览方式时留着它等于把两个
        # 互不相干的概念叠在一起：用户看到的照片既不是「整库平铺」也不是「这一层」。
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None
        self._session = self._session.with_browse_mode(mode)
        self._save_session()
        self._update_mode_actions()
        if self._library is not None:
            self._reload_level()

    def enter_folder(self, directory: Path, *, focus: EntryKey | None = None) -> None:
        """进入一个相册（子文件夹）。库以外的路径一律拒绝。

        :param focus: 到了这一层该选中哪一格。``Esc`` 往上一层退时传的是「刚离开的那个
            相册」，于是退回去时网格上仍然停在它身上（见 :meth:`go_up`）。

        **进目录就是退出检索**：两者都是「一层」，面包屑上那一段只有一个是当前所在
        （ADR 0026）。条件不清掉的话，检索会在下一次 ``_start_scan`` 里抢在目录前面 ——
        用户点开一个相册，看到的却是全库的检索结果。

        **存进去的是解析过的路径**（与 ``Library.root``、扫描出来的条目、监听器报回来的
        变化同一套写法）。少了这一步，路径里带软链接的库（``/var`` → ``/private/var``、
        用户自己链过去的目录）会安静地出毛病：监听器报回来的是真路径，而当前这一层
        存的是原写法，于是「是不是在当前这一层」永远说不成 —— 访达里丢进去一张照片，
        网格当没看见（ADR 0018 的粘贴同样栽在这里，网格里长不出新照片）。
        """
        library = self._library
        if library is None:
            return
        target = Path(directory)
        if not paths.is_within(library.root, target):
            return
        self._current_dir = target.resolve()
        self._group_view = None
        self._conditions.clear()
        self._editing_condition = None
        self._search_total = None
        self._reload_level(focus=focus)

    def go_up(self) -> None:
        """回到上一层：有筛选条件就撤掉最后一条，否则按分组 / 相册往上退。

        分组比文件夹深一级（它是文件夹之上的一个视图），所以先退它；
        **筛选条件又深一级**（它跨着整个库，见 ADR 0026 决策 9）—— 与访达
        「一层一层退」是同一套心智，所以走的是同一个 ``Esc``。

        退上去时**把刚离开的那一级留在选中状态**：从相册退回去选中的是那个相册卡片，
        从分组退回去选中的是那张组卡片（访达里退上一层就是这个样子）。

        只此一处例外。再**点进去**是另一次浏览：那一层不记着自己上一次停在哪一格，
        网格上不会留着上一次的选中（见 :meth:`_arm_focus`）。
        """
        library = self._library
        if library is None:
            return
        if self._conditions:
            self._conditions.pop()
            self._editing_condition = None
            self._apply_conditions()
            return
        if self._group_view is not None:
            self._leave_group()
            return
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return
        current = self._current_dir or library.root
        parent = parent_of(library.root, current)
        if parent is not None:
            self.enter_folder(parent, focus=path_key(current))

    def _can_go_up(self) -> bool:
        """现在退不退得回去。菜单里的「上一级」与双击空白处都问它。

        判据与 :meth:`go_up` 一致，否则菜单灰着、双击却退了回去（或者反过来）。
        """
        library = self._library
        if library is None or self._grid.is_renaming():
            return False
        if self._conditions:
            return True  # 有得撤：撤一条筛选条件就是「上一层」（与 go_up 同一套判据）
        if self._group_view is not None:
            return True
        if self._browse_mode != BROWSE_MODE_ALBUMS:
            return False
        return parent_of(library.root, self._current_dir or library.root) is not None

    def _on_background_activated(self) -> None:
        """双击了网格里没有格子的地方：退回上一层，与 `Esc` 是同一件事。

        这一层空着（空相册、空分组）时尤其需要它 —— 屏幕上根本没有可双击的东西，
        而这正是最想立刻退出去的时候。已经退不动时就安静地什么都不做：到了头本来就是
        一个合法状态，不该为它弹一句话提醒。
        """
        if self._grid.is_renaming():
            # 改到一半：这一下先归输入框。既不把人带走，也不打扰他。
            return
        if self._can_go_up():
            self.go_up()

    def _on_breadcrumb_requested(self, directory: object) -> None:
        if isinstance(directory, Path):
            self.enter_folder(directory)

    def _reload_level(self, *, focus: EntryKey | None = None) -> None:
        """重新列出当前层（切模式、进入或返回相册时都走这里）。

        :param focus: 到了这一层要选中哪一格。**只有往上一层退才会给**（那个相册 / 那个组），
            其余由 :meth:`_arm_focus` 自己判断：还是这一层就跟着停着的那一格走，换了层
            就谁也不选。
        """
        if self._scan_job is not None:
            self._runner.cancel(self._scan_job)
            self._scan_job = None
        self._arm_focus(focus)
        # 换代：让已经在路上的旧结果作废。
        self._generation += 1
        self._grid.cancel_rename()
        self._cancel_facet_job()
        self._facet_values = {}
        self._model.clear()
        # 网格先清干净，不靠模型 reset 的副作用：这一层可能一个格子都没有（空相册），
        # 而「谁也没选中」正是要摆出来给人看的一个状态。当前项留着的话，⌘R / ⌘⌫ 的可用性
        # 判据里它仍然算数 —— 菜单亮着，作用的对象却不存在。该恢复的那一格由
        # :meth:`_restore_focus` 在这一层排好之后重新选中。
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
        self._stack.setCurrentWidget(self._grid_page)
        self._update_breadcrumb()
        self._update_action_states()
        self._start_scan()

    # ---- 到了这一层该选中哪一格 ----------------------------------------------
    #
    # 三种情形，三种答案：
    #
    # - **往上一层退**（``go_up`` / ``_leave_group`` 显式给出）：网格上仍停着刚离开的
    #   那个相册 / 那个组。不然每退一级都要重新找一遍自己刚才在哪儿。
    # - **还是这一层，重排一遍**（换排序 / 换分组 / 正序逆序 / 扫描在途时决定重来）：
    #   停着的那一格还在，跟着它走 —— 是同一批照片换了个位置，不该把人甩掉。
    # - **换了层**（进入相册 / 进入分组 / 切浏览方式，含退上去之后再点进去）：
    #   **谁也不选**。文件浏览里「重新进入一层」是一次新的浏览：把上一次的选择留着，
    #   用户看到的是上一趟留下的高亮，而随手一个 ⌘⌫ 删掉的是他这一趟并没有指过的
    #   东西 —— 选中框画在那儿，看着却像「我已经选好了」。
    #
    # 三者都靠**标识**而不是行号（重扫、重排、换排序之后行号就变了，标识没变），标识由
    # ``ui.navigation`` 的纯函数算（见 ``tests/ui/test_navigation.py``）。哪一条属于哪一
    # 种情形不靠调用方记得传参，而是比一比**层标识**—— 见 :meth:`_arm_focus`。

    def _arm_focus(self, focus: EntryKey | None = None) -> None:
        """记下到了新的一层该选中哪一格。

        :param focus: 显式指定（往上一层退时给的那个相册 / 那个组）。不给就是让这里自己
            判断：**还是刚才那一层**就把停着的那一格记下来跟着它走，**换了层**就谁也不选。

        判断靠比层标识（见 ``navigation.level_key``）：``enter_folder`` / ``enter_group``
        都是先改状态再重载，所以问「现在在哪一层」得到的是**新的**那一层 —— 正好拿来
        和 ``self._level_key``（模型里此刻摆着的那一层）比。

        **顺序要紧**：先把「还是同一层吗」问完，再把 ``self._level_key`` 换成新的那一层。
        反过来的话，两边永远相等，于是「换了层就什么都不记」这条永远轮不到执行 ——
        剩下的全靠「记下来的那一格在新的一层里恰好不存在」蒙对。
        """
        remembered = self._shown_entry_key()
        self._level_key = self._current_level_key()
        self._focus = focus if focus is not None else remembered
        self._focus_restored = False

    def _current_level_key(self) -> LevelKey:
        """当前这一层的标识（见 ``navigation.level_key``）。"""
        group = None if self._group_view is None else (self._group_view.facet, self._group_view.key)
        conditions = [condition.identity for condition in self._conditions]
        return level_key(self._browse_mode, self._current_dir, group=group, conditions=conditions)

    def _shown_entry_key(self) -> EntryKey | None:
        """网格上此刻停着的那一格是谁；不是同一层（或者压根没有）就是 ``None``。"""
        if self._level_key is None or self._level_key != self._current_level_key():
            return None
        entry = self._model.entry_at(self._grid.current_row())
        return None if entry is None else entry_key(entry)

    def _restore_focus(self) -> None:
        """这一层排好之后，把该选中的一格选中。

        **每一层只摆一次**：摆完之后就是用户自己的选择了，后台再重排一次（监听器来了
        新照片、换排序）不该把它拽回去 —— 那种重排由 ``_reorder_level`` 的锚点负责。

        找不到就什么都不选：那一格多半已经被改名、移走或删掉了，界面此时也说不清该选谁，
        与其挑一个错的，不如让这一层从零开始。
        """
        if self._focus_restored:
            return
        self._focus_restored = True
        key, self._focus = self._focus, None
        if key is None:
            return
        row = self._row_of_focus(key)
        if row >= 0:
            self._grid.select_row(row)  # 顺带滚过去：这一层可能长到一屏之外

    def _row_of_focus(self, key: EntryKey) -> int:
        """该选中的那一格现在在第几行（``-1`` = 这一层里已经没有了）。

        照片与相册走模型现成的路径索引；组卡片没有索引（它的键不是路径），扫一遍这一层
        —— 组卡片本身就不多，而这一层里的照片可能有几万张，所以两条路不能合成一条。
        """
        if key[0] == ENTRY_KIND_PATH:
            return self._model.row_of_path(Path(key[1]))
        return row_of_entry_key(self._model.entries(), key)

    def _group_view_label(self) -> str | None:
        """当前所在分组的显示名（没在分组里就是 ``None``）。"""
        return None if self._group_view is None else self._group_view.label

    def _update_breadcrumb(self) -> None:
        library = self._library
        # 平铺模式下平时不显示面包屑，但**分组里必须显示**：那里没有文件夹可指，
        # 只有一条「库名 ▸ 2024 年 5 月」才说得出自己在哪。
        #
        # **有筛选条件时一律显示**（ADR 0026 决策 9）：条件总得有个地方摆，
        # 而平铺模式下没有目录可指 —— 那正是「不显示」的理由，于是这里只剩它了。
        in_albums = self._browse_mode == BROWSE_MODE_ALBUMS
        conditions = [condition.chip_text for condition in self._conditions]
        if library is None or (not in_albums and self._group_view is None and not conditions):
            self._breadcrumb.hide()
            return
        if conditions:
            # **检索时只报库名：既不报当前文件夹，也不报当前那一组**。检索横跨整个库
            # （ADR 0026 决策 1），于是那一行若是「照片 ▸ 旅行 ▸ 2024 ▸ 搜索结果（4）」，
            # 读起来就是「在这个文件夹里搜到 4 张」；带着组名更糟 —— 「照片 ▸ 2024 年 5 月
            # ▸ 搜索结果（3）」读起来是「这一组里搜到 3 张」，而那 3 张大半在别的组、别的
            # 相册（实测：一个**空组**里搜出 3 张全库照片，面包屑还写着那个组）。
            #
            # 那一行在说一件不存在的事，比什么都不说更坏。范围要收窄自己加一条
            # 「#相册 …」，那一条就明明白白写在右端。
            #
            # ``_group_view`` 与 ``_current_dir`` **都留着**：撤光条件之后要回到原来
            # 那一组、那一层去（见 ``_start_scan``：条件为空时才轮到分组那条路）。
            parts = [(library.display_name, library.root)]
        else:
            parts = (
                breadcrumb_parts(library.root, self._current_dir or library.root)
                if in_albums
                else [(library.display_name, library.root)]
            )
        # 分组那一段同理：条件还在的时候它说的不是检索范围，而是「我进来过哪一组」。
        group = None if conditions else self._group_view_label()
        editing = self._editing_condition if conditions else None
        self._breadcrumb.set_path(
            parts,
            group=group,
            conditions=conditions,
            result_count=self._search_total if conditions else None,
            editing=editing,
        )
        self._breadcrumb.show()

    def _update_mode_actions(self) -> None:
        albums = self._browse_mode == BROWSE_MODE_ALBUMS
        self._albums_action.setChecked(albums)
        self._flat_action.setChecked(not albums)

    def _update_navigation_actions(self) -> None:
        # 「上一级」只在网格页启用：大图页的 Esc 是「返回网格」，两个动作共用一个 Esc，
        # 必须有一个是关着的，否则同一按键落到谁身上说不准。改名的过程中也不启用 ——
        # 那时 Esc 是输入框的「取消改名」（输入框还会先抢一次按键，见 RenameEdit.event）。
        # **两个输入框都算**（ADR 0025 决策 6）。
        in_grid = self._stack.currentWidget() is self._grid_page
        idle = not self._grid.is_renaming() and not self._title_bar.is_editing()
        self._up_action.setEnabled(in_grid and idle and self._can_go_up())

    # ---- 会话 --------------------------------------------------------------

    def _remember(self, root: Path) -> None:
        self._session = self._session.with_recent_root(root)
        self._welcome.set_recent(self._session.recent_roots)
        self._rebuild_recent_menu()
        self._save_session()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        roots = [root for root in self._session.recent_roots if root.is_dir()]
        if not roots:
            empty = QAction("（还没有）", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for root in roots:
            action = QAction(str(root), self)
            action.triggered.connect(lambda _checked=False, path=root: self.open_library(path))
            self._recent_menu.addAction(action)

    def _on_recent_requested(self, root: object) -> None:
        if isinstance(root, Path):
            self.open_library(root)

    def _save_session(self) -> None:
        geometry = self.geometry()
        self._session = self._session.with_window(
            WindowGeometry(
                x=geometry.x(),
                y=geometry.y(),
                width=geometry.width(),
                height=geometry.height(),
                maximized=self.isMaximized(),
            )
        )
        try:
            save_session(self._session, base=self._session_base)
        except OSError as exc:
            # 记不住窗口位置不值得打断用户。
            logger.debug("会话状态写入失败：%s", exc)

    # ---- 窗口 --------------------------------------------------------------

    def _is_title_bar(self, point: QPointF) -> bool:
        """这一点是不是落在标题栏那一段（窗口顶边与内容区之间）。"""
        central = self.centralWidget()
        return central is not None and point.y() < central.geometry().top()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        """按住标题栏那一段拖动窗口。

        内容铺到标题栏底下之后（见 ``ui.theme.unify_title_bar``），那一条已经是我们的
        客户区，系统不再代劳拖动，得自己发起。落在标题栏里的按下就交给 ``startSystemMove``；
        它返回假（平台不支持、或系统其实自己会拖）就照常往下传，不影响别处的点击。

        **不用先问 ``TitleBar`` 在不在那儿**：不在编辑时它对鼠标是透明的（编辑时要摘掉，
        否则输入框收不到点击），按下照旧落到窗口自己身上。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._is_title_bar(event.position()):
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    # ---- 标题栏（见 ADR 0025）-----------------------------------------------

    def _in_detail_page(self) -> bool:
        """现在是不是大图页。「此刻作用于谁」的判据都问它（ADR 0021 决策 3）。"""
        return self._stack.currentWidget() is self._detail_page

    def _refresh_title(self) -> None:
        """窗口顶边那一行显示什么：**大图页是正在看的那张照片的文件名，其余是相册名**。

        判据只有这一段 —— 「现在在哪一页 + 那一格是谁」—— 于是翻图、退回网格、改名
        （含 ``⌘Z`` 撤回）、换库走的都是同一段算术，不会有一处忘了更新而标题还写着旧名
        （那恰恰是用户正盯着改的名字）。
        """
        photo = self._model.photo_at(self._current_row) if self._in_detail_page() else None
        name = photo.name if photo is not None else self._library_title
        if self._owns_title_bar:
            self._title_bar.set_text(name)
        else:
            # 标题栏那一段不归我们（例如 Linux），照旧交给系统画（ADR 0025 决策 2）。
            self.setWindowTitle(name)

    def _layout_title_bar(self) -> None:
        """把 ``TitleBar`` 与搜索胶囊摆在窗口顶边那一条上。

        那一条的高度是**安全区**顶出来的那 28 点（见 ``theme.unify_title_bar``），不写死。
        全屏时系统自己也不画标题栏，安全区让位，于是这一条跟着收起来 —— 否则它会盖在
        照片上面（ADR 0025 决策 2）。

        **两个控件是摆在一起的，不是一套布局**：``TitleBar`` 那一行由标题文字与改名字段
        自己居中，胶囊要贴右边，两者抢同一份布局反而互相挪（ADR 0026 决策 8）。
        """
        if not self._owns_title_bar:
            return
        if self.isFullScreen():
            # 全屏时系统自己也不画标题栏，这一整条跟着收起来（ADR 0025 决策 2）：
            # 高度给 0，于是它与胶囊都不会盖在照片上。
            self._title_bar.setGeometry(0, 0, self.width(), 0)
            self._title_bar.hide()
            self._search_field.hide()
            return
        central = self.centralWidget()
        top = central.geometry().top() if central is not None else 0
        # 安全区之外**再加** :data:`TITLE_BAR_EXTRA`：中央控件已经用同样的边距把三页
        # 往下推了（见 ``_build_ui``），于是多出来的那一段是真的空出来的，不是盖在
        # 内容上的 —— 胶囊是画的，压上去就等于在照片区顶边摆了一块底色。
        height = max(top + TITLE_BAR_EXTRA, TITLE_BAR_HEIGHT)
        self._title_bar.setGeometry(0, 0, self.width(), height)
        self._title_bar.setVisible(True)
        # 胶囊摆在**这一条**里：内容区从 ``top + EXTRA`` 开始，两者首尾相接。
        #
        # 再往下挪 :data:`SEARCH_FIELD_TEXT_ALIGN_DROP` 那一格：中线是**格子**的中线，
        # 而眼睛看的是标题那行字的墨迹 —— 截图量过两者的中线，差多少、往哪边差，
        # 都写在 ``search_field.TEXT_ALIGN_DROP`` 那里了。
        field_height = min(SEARCH_FIELD_HEIGHT, height - 4)
        self._search_field.setGeometry(
            self.width() - SEARCH_FIELD_WIDTH - SEARCH_FIELD_MARGIN_RIGHT,
            (height - field_height) // 2 + SEARCH_FIELD_TEXT_ALIGN_DROP,
            SEARCH_FIELD_WIDTH,
            field_height,
        )
        self._search_field.show()
        # **要抬一次**：窗口布局跑完之后，中央控件与菜单栏会被抬到「最后创建的子控件」
        # 之上（实测它们就是这么盖住了胶囊），而胶囊是要画出来的 —— 被盖住就等于窗口上
        # 根本没有那个东西。``show()`` 对已经可见的控件不再发 ``showEvent``，所以抬的动作
        # 得写在这里，缩放窗口时也一并生效。
        self._search_field.raise_()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().showEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        super().resizeEvent(event)
        self._layout_title_bar()
        self._layout_banner()

    def _on_title_rename_committed(self, text: str) -> None:
        """标题栏里确认了一个新名字。

        **与网格那个输入框走同一个写操作入口**（``write_ops._on_rename_committed``），
        因此记账、横幅、撤销、监听器回声一样都不少 —— 换的只是「在哪一行上改」
        （红线 1、ADR 0025 决策 3）。
        """
        self._on_rename_committed(self._focus_row(), text)

    # ---- 关闭 --------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - 名字是 Qt 定的
        self._save_session()
        self._teardown_library()
        # ``abandon`` 而不是「取消 + 等」：排队里还没开始的任务必须丢掉，否则每跑掉
        # 一个就再等一个（丢进来几百张照片时那是好几分钟）。已经跑起来的那几个靠令牌
        # 收工 —— 解码中途会问令牌（``core.media.decode``），所以连「云盘上还没下完、
        # open() 一直不返回」那种也能立刻放手，⌘Q 不会被拖住。
        if not self._runner.abandon():
            logger.warning("仍有后台任务未结束，已放弃等待")
        # 剪贴板那个连接**不显式断开**：PySide 会在接收者析构时自动摘掉（实测过，
        # 窗口销毁后再动剪贴板不会回调）。显式 disconnect 反而每次关窗都报一次
        # 「Failed to disconnect」—— 那个 bound method 已经不是当初连上去的那一个了。
        # 关窗到析构之间剪贴板若真的变了，回调只会动那条 QAction，窗口此时已不可见。
        super().closeEvent(event)


__all__ = ["MainWindow"]
